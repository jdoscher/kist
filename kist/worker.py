"""Worker entrypoint: processes the Postgres-backed job queue and watches
the inbox directory for dropped files. Job transitions live in
services/jobs.py (see lifecycle.JobStatus).

It also re-queues inbox files stranded in staging, runs `kist verify` once
a day (result in library/.kist/verify.json), empties trash older than
KIST_TRASH_RETENTION_DAYS, re-hashes a share of the originals every day
(fixity), runs `kist backup` every KIST_BACKUP_INTERVAL_HOURS, fills in
missing ThumbHash placeholders, and keeps a heartbeat file in library/.kist/
that the container healthcheck and GET /api/health/deep read.
"""

import json
import logging
import os
import sys
import threading
import time

import sqlalchemy as sa

from .config import get_settings
from .db import SessionLocal, engine
from .lifecycle import JobKind
from .models import Item, Job
from .observability import configure_logging
from .services.health import heartbeat_path

log = logging.getLogger("kist.worker")

REAP_EVERY_SECONDS = 60
RECOVER_EVERY_SECONDS = 300
BEAT_EVERY_SECONDS = 15
# the ml service didn't answer (down, or downloading its models on first
# start): its jobs go back to the queue and the AI lane waits this long
ML_DOWN_PAUSE_SECONDS = 60
# the container healthcheck's limit (a few missed beats)
HEALTHY_WITHIN_SECONDS = 60


class Heartbeat(threading.Thread):
    """Touches the heartbeat file every 15 s after a successful database
    round trip, while the main loop keeps making progress. A long preview or
    download doesn't make the worker look dead; a loop that hasn't moved in
    longer than the job lease (a real hang) or a database it can't reach
    does."""

    def __init__(self, stuck_after: float):
        super().__init__(name="heartbeat", daemon=True)
        self.stuck_after = stuck_after
        self.progress = time.monotonic()
        self.stopped = threading.Event()

    def progressed(self) -> None:
        self.progress = time.monotonic()

    def beat_once(self) -> bool:
        if time.monotonic() - self.progress > self.stuck_after:
            log.warning("worker loop hasn't moved for %.0f s", time.monotonic() - self.progress)
            return False
        try:
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
        except Exception as exc:
            log.warning("heartbeat: database unreachable (%s)", type(exc).__name__)
            return False
        path = heartbeat_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"pid": os.getpid(), "at": time.time()}))
        except OSError:
            log.exception("could not write the heartbeat")
            return False
        return True

    def run(self) -> None:
        while not self.stopped.is_set():
            self.beat_once()
            self.stopped.wait(BEAT_EVERY_SECONDS)


def healthcheck() -> int:
    try:
        age = time.time() - heartbeat_path().stat().st_mtime
    except OSError:
        return 1
    return 0 if age < HEALTHY_WITHIN_SECONDS else 1


def process_job(session, job: Job) -> None:
    from .services import events, ingest, previews

    if job.kind == JobKind.INGEST:
        # inbox drops: recorded in item history as the importer
        with events.acting("importer", "import"):
            ingest.run_ingest_job(session, job.payload)
    elif job.kind == JobKind.REGEN_PREVIEW:
        item = session.get(Item, (job.payload or {}).get("item_id"))
        if item is None:
            # purged since it was queued: nothing left to do
            log.info("job %s: item %s no longer exists", job.id, (job.payload or {}).get("item_id"))
            return
        previews.regenerate(session, item)
        from .services import ai

        ai.on_new_item(session, item)  # now it has a thumbnail to embed
    else:
        raise ValueError(f"unknown job kind: {job.kind}")


def run_one() -> bool:
    """Claim and run a single job. Returns True if a job was processed."""
    from .services import jobs

    with SessionLocal() as s:
        job = jobs.claim_next(s)
        if job is None:
            return False
        job_id, kind = job.id, job.kind
        try:
            process_job(s, job)
            error = None
        except Exception as exc:
            s.rollback()
            log.exception("job %s (%s) failed", job_id, kind)
            error = str(exc) or type(exc).__name__
        jobs.finish(s, job_id, error)
    return True


class AiLane:
    """AI jobs on threads of their own (KIST_AI_CONCURRENCY at once), so an
    import or a preview never waits behind an embedding or an OCR run.
    Claims only when no light job is waiting (services/ai.claim). A thread
    takes embed jobs in batches of KIST_AI_EMBED_BATCH (one ml request)."""

    def __init__(self, concurrency: int, batch: int = 1):
        from concurrent.futures import ThreadPoolExecutor

        self.size = max(1, concurrency)
        self.batch = max(1, min(batch, 32))
        self.pool = ThreadPoolExecutor(max_workers=self.size, thread_name_prefix="ai")
        self.running: set = set()
        # set by a job that found the ml service down: claim nothing until then
        self.paused_until = 0.0

    def kinds(self) -> tuple[str, ...]:
        from .services import ai

        return ai.worker_kinds()

    def fill(self) -> int:
        """Claim jobs for every free slot. Returns how many started."""
        from .services import ai

        for f in [f for f in self.running if f.done()]:
            if f.exception() is None and f.result() == "unavailable":
                self.paused_until = time.monotonic() + ML_DOWN_PAUSE_SECONDS
        self.running = {f for f in self.running if not f.done()}
        free = self.size - len(self.running)
        kinds = self.kinds() if free > 0 else ()
        if free <= 0 or not kinds or time.monotonic() < self.paused_until:
            return 0
        started = 0
        for _ in range(free):
            with SessionLocal() as s:
                jobs = ai.claim_batch(s, runner="worker", kinds=kinds, batch=self.batch)
                ids = [(j.id, j.kind) for j in jobs]
            if not ids:
                break
            if len(ids) == 1:
                self.running.add(self.pool.submit(run_ai_job, *ids[0]))
            else:
                self.running.add(self.pool.submit(run_ai_batch, [i for i, _ in ids]))
            started += len(ids)
        return started

    def wait(self, timeout: float) -> bool:
        """Instead of the idle sleep while AI jobs run: wake as soon as one
        ends, so a backfill isn't paced by the poll interval. False when
        nothing is running (the caller sleeps as usual)."""
        from concurrent.futures import FIRST_COMPLETED
        from concurrent.futures import wait as wait_futures

        self.running = {f for f in self.running if not f.done()}
        if not self.running:
            return False
        wait_futures(self.running, timeout=timeout, return_when=FIRST_COMPLETED)
        return True


def run_ai_job(job_id: int, kind: str) -> str:
    """Run one AI job; returns "done", "failed" or "unavailable" (the ml
    service didn't answer: the job goes back without losing an attempt)."""
    from .services import ai, jobs

    with SessionLocal() as s:
        try:
            job = s.get(Job, job_id)
            if job is None:
                return "done"
            ai.run_job(s, job)
            error = None
        except ai.AiUnavailable as exc:
            s.rollback()
            log.warning("job %s (%s) put back: %s", job_id, kind, exc)
            try:
                jobs.release(s, job_id, str(exc), ML_DOWN_PAUSE_SECONDS)
            except Exception:
                log.exception("could not put job %s back", job_id)
            return "unavailable"
        except Exception as exc:
            s.rollback()
            log.exception("job %s (%s) failed", job_id, kind)
            error = str(exc) or type(exc).__name__
        try:
            jobs.finish(s, job_id, error)
        except Exception:
            log.exception("could not record the end of job %s", job_id)
        return "done" if error is None else "failed"


def run_ai_batch(job_ids: list[int]) -> str:
    """Run a batch of embed jobs in one ml request; like run_ai_job, but
    each job ends with its own result."""
    from .services import ai, jobs

    with SessionLocal() as s:
        batch = [j for j in (s.get(Job, i) for i in job_ids) if j is not None]
        try:
            results = ai.run_embed_batch(s, batch)
        except ai.AiUnavailable as exc:
            s.rollback()
            log.warning("%d embed jobs put back: %s", len(job_ids), exc)
            for job_id in job_ids:
                try:
                    jobs.release(s, job_id, str(exc), ML_DOWN_PAUSE_SECONDS)
                except Exception:
                    log.exception("could not put job %s back", job_id)
            return "unavailable"
        except Exception as exc:
            s.rollback()
            log.exception("embed batch %s failed", job_ids)
            results = {i: str(exc) or type(exc).__name__ for i in job_ids}
        results = {job_id: results.get(job_id, "not run") for job_id in job_ids}
        try:
            jobs.finish_many(s, results)
        except Exception:
            log.exception("could not record the end of jobs %s", job_ids)
        return "failed" if any(e is not None for e in results.values()) else "done"


def reap() -> None:
    from .services import jobs

    with SessionLocal() as s:
        jobs.reap_stale(s, get_settings().job_lease_seconds)


def scan_inbox() -> None:
    from .services import ingest

    ingest.scan_inbox()


def recover_staging() -> None:
    from .services import jobs

    jobs.recover_staging()


def verify_due(interval_hours: float) -> bool:
    from datetime import UTC, datetime

    from .services import verify

    if interval_hours <= 0:
        return False
    last = (verify.last_result() or {}).get("last_run")
    if not last:
        return True
    try:
        age = datetime.now(UTC) - datetime.fromisoformat(last)
    except ValueError:
        return True
    return age.total_seconds() >= interval_hours * 3600


def run_verify() -> None:
    from .services import verify

    with SessionLocal() as s:
        report = verify.run(s)
    verify.save_result(report)
    level = logging.WARNING if report.problems else logging.INFO
    log.log(level, "verify: %d items checked, %d problems", report.items_checked,
            len(report.problems))


def purge_expired_trash() -> int:
    from .services import retention

    with SessionLocal() as s:
        return len(retention.purge_expired(s))


def check_fixity() -> tuple[int, int]:
    from .services import fixity

    with SessionLocal() as s:
        return fixity.run_some(s)


_backup_thread: threading.Thread | None = None


def start_backup_if_due() -> bool:
    """Run `kist backup` in a background thread when it is due, so a long
    upload never stalls the queue or the heartbeat."""
    global _backup_thread
    from .services import backup

    if _backup_thread is not None and _backup_thread.is_alive():
        return False
    if not backup.due():
        return False

    def run() -> None:
        try:
            result = backup.run()
            level = logging.INFO if result["ok"] else logging.WARNING
            log.log(level, "backup: %s", "done" if result["ok"] else result.get("error"))
        except Exception:
            log.exception("backup failed")

    _backup_thread = threading.Thread(target=run, name="backup", daemon=True)
    _backup_thread.start()
    return True


def backfill_thumbhashes() -> int:
    from .services import previews

    with SessionLocal() as s:
        return previews.backfill_thumbhashes(s)


ORGANISE_EVERY_SECONDS = 300


def run_organise() -> None:
    """Background upkeep for Milestone 2 (never fails the loop): new
    unnamed people from faces that match no one yet (KIST_FACES), and the
    suggested collections every KIST_COLLECTIONS_INTERVAL_HOURS (which also
    embeds new tag names for tag suggestions)."""
    from .services import ai
    from .services import collections as collections_svc
    from .services import faces as faces_svc

    settings = get_settings()
    if settings.faces:
        try:
            with SessionLocal() as s:
                made = faces_svc.cluster_unassigned(s)
            if made:
                log.info("faces: %d new people to name", made)
        except Exception:
            log.exception("face clustering failed")
    if settings.ml_url and collections_svc.due(settings.collections_interval_hours):
        try:
            with SessionLocal() as s:
                if ai.has_embeddings(s):
                    data = collections_svc.compute(s)
                    log.info("suggested collections: %d clusters from %d items",
                             len(data["clusters"]), data["items"])
        except ai.AiUnavailable as exc:
            log.warning("suggested collections postponed: %s", exc)
        except Exception:
            log.exception("suggested collections failed")


def main() -> None:
    configure_logging()
    settings = get_settings()
    for d in (settings.items_dir, settings.inbox_dir, settings.staging_dir,
              settings.state_dir, *settings.import_dirs):
        d.mkdir(parents=True, exist_ok=True)
    log.info("worker started (library=%s)", settings.library_dir)
    heartbeat = Heartbeat(stuck_after=settings.job_lease_seconds)
    heartbeat.start()
    ai_lane = AiLane(settings.ai_concurrency, settings.ai_embed_batch)
    from .services import captions

    if captions.warning():
        log.warning(captions.warning())
    if settings.ml_url:
        from .services import ai

        try:
            with SessionLocal() as s:
                ai.ensure_vector_index(s, ai.active_model())
        except Exception:
            log.exception("could not make the vector index for %s", settings.ml_model)
        log.info("AI jobs: ml service %s, model %s, %d at once, embed batches of %d, "
                 "window %s", settings.ml_url, settings.ml_model, ai_lane.size, ai_lane.batch,
                 settings.ai_window or "any time")

    last_inbox_scan = last_reap = last_recover = last_verify_check = 0.0
    last_backfill = last_retention = last_backup_check = 0.0
    last_fixity = last_organise = 0.0
    last_error = ""
    while True:
        heartbeat.progressed()
        worked = False
        try:
            now = time.monotonic()
            if now - last_recover > RECOVER_EVERY_SECONDS:
                recover_staging()
                last_recover = now
            if now - last_reap > REAP_EVERY_SECONDS:
                reap()
                last_reap = now
            worked = run_one()
            heartbeat.progressed()
            if not worked:
                worked = ai_lane.fill() > 0
            if time.monotonic() - last_inbox_scan > settings.inbox_poll_seconds:
                scan_inbox()
                last_inbox_scan = time.monotonic()
            if time.monotonic() - last_verify_check > 600:
                last_verify_check = time.monotonic()
                if verify_due(settings.verify_interval_hours):
                    run_verify()
            if time.monotonic() - last_retention > 3600:
                last_retention = time.monotonic()
                purge_expired_trash()
            if time.monotonic() - last_backup_check > 600:
                last_backup_check = time.monotonic()
                start_backup_if_due()
            # fixity: today's share of re-hashes, a chunk at a time while idle
            if not worked and time.monotonic() - last_fixity > 60:
                last_fixity = time.monotonic()
                checked, _failed = check_fixity()
                if checked:
                    last_fixity = 0.0  # more may be due
            if not worked and time.monotonic() - last_organise > ORGANISE_EVERY_SECONDS:
                last_organise = time.monotonic()
                run_organise()
            # older items get their ThumbHash placeholder a batch at a time,
            # only while the queue is idle
            if not worked and time.monotonic() - last_backfill > 60:
                last_backfill = time.monotonic()
                if backfill_thumbhashes():
                    last_backfill = 0.0  # more may be waiting
            last_error = ""
        except Exception as exc:
            # a database outage repeats the same error every few seconds:
            # one traceback, then a line per retry
            summary = f"{type(exc).__name__}: {exc}".splitlines()[0][:300]
            if summary != last_error:
                log.exception("worker loop error")
            else:
                log.warning("worker loop error again: %s", summary)
            last_error = summary
            time.sleep(5)
        if not worked and not ai_lane.wait(settings.worker_poll_seconds):
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    if "--healthcheck" in sys.argv:
        sys.exit(healthcheck())
    main()
