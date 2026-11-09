"""Worker entrypoint: processes the Postgres-backed job queue and watches
the inbox directory for dropped files. Job transitions live in
services/jobs.py (see lifecycle.JobStatus).
"""

import logging
import sys
import time
from pathlib import Path

from .config import get_settings
from .db import SessionLocal
from .lifecycle import JobKind
from .models import Item, Job

log = logging.getLogger("kist.worker")

HEARTBEAT = Path("/tmp/kist-worker-heartbeat")
REAP_EVERY_SECONDS = 60


def beat() -> None:
    HEARTBEAT.touch()


def healthcheck() -> int:
    try:
        age = time.time() - HEARTBEAT.stat().st_mtime
    except FileNotFoundError:
        return 1
    return 0 if age < 60 else 1


def process_job(session, job: Job) -> None:
    from .services import ingest, previews

    if job.kind == JobKind.INGEST:
        ingest.run_ingest_job(session, job.payload)
    elif job.kind == JobKind.REGEN_PREVIEW:
        item = session.get(Item, (job.payload or {}).get("item_id"))
        if item is None:
            raise LookupError("the item no longer exists")
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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # libvips reports every resize step at INFO
    logging.getLogger("pyvips").setLevel(logging.WARNING)
    settings = get_settings()
    for d in (settings.items_dir, settings.inbox_dir, settings.staging_dir,
              *settings.import_dirs):
        d.mkdir(parents=True, exist_ok=True)
    log.info("worker started (library=%s)", settings.library_dir)

    last_inbox_scan = 0.0
    last_reap = 0.0
    while True:
        beat()
        worked = False
        try:
            if time.monotonic() - last_reap > REAP_EVERY_SECONDS:
                reap()
                last_reap = time.monotonic()
            worked = run_one()
            if time.monotonic() - last_inbox_scan > settings.inbox_poll_seconds:
                scan_inbox()
                last_inbox_scan = time.monotonic()
        except Exception:
            log.exception("worker loop error")
            time.sleep(5)
        if not worked:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    if "--healthcheck" in sys.argv:
        sys.exit(healthcheck())
    main()
