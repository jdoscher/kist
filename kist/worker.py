"""Worker entrypoint: processes the Postgres-backed job queue and watches
the inbox directory for dropped files. Job transitions live in
services/jobs.py (see lifecycle.JobStatus).

It also re-queues inbox files stranded in staging, runs `kist verify` once
a day (result in library/.kist/verify.json), and keeps a heartbeat file in
library/.kist/ that the container healthcheck and GET /api/health/deep read.
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
    from .services import ingest, previews

    if job.kind == JobKind.INGEST:
        ingest.run_ingest_job(session, job.payload)
    elif job.kind == JobKind.REGEN_PREVIEW:
        item = session.get(Item, (job.payload or {}).get("item_id"))
        if item is None:
            # purged since it was queued: nothing left to do
            log.info("job %s: item %s no longer exists", job.id, (job.payload or {}).get("item_id"))
            return
        previews.regenerate(session, item)
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


def main() -> None:
    configure_logging()
    settings = get_settings()
    for d in (settings.items_dir, settings.inbox_dir, settings.staging_dir,
              settings.state_dir, *settings.import_dirs):
        d.mkdir(parents=True, exist_ok=True)
    log.info("worker started (library=%s)", settings.library_dir)
    heartbeat = Heartbeat(stuck_after=settings.job_lease_seconds)
    heartbeat.start()

    last_inbox_scan = last_reap = last_recover = last_verify_check = 0.0
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
            if time.monotonic() - last_inbox_scan > settings.inbox_poll_seconds:
                scan_inbox()
                last_inbox_scan = time.monotonic()
            if time.monotonic() - last_verify_check > 600:
                last_verify_check = time.monotonic()
                if verify_due(settings.verify_interval_hours):
                    run_verify()
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
        if not worked:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    if "--healthcheck" in sys.argv:
        sys.exit(healthcheck())
    main()
