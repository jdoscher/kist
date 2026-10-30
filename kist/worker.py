"""Worker entrypoint: processes the Postgres-backed job queue and watches
the inbox directory for dropped files.
"""

import logging
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import sqlalchemy as sa

from .config import get_settings
from .db import SessionLocal
from .models import Job

log = logging.getLogger("kist.worker")

HEARTBEAT = Path("/tmp/kist-worker-heartbeat")
MAX_ATTEMPTS = 3


def beat() -> None:
    HEARTBEAT.touch()


def healthcheck() -> int:
    try:
        age = time.time() - HEARTBEAT.stat().st_mtime
    except FileNotFoundError:
        return 1
    return 0 if age < 60 else 1


def process_job(session, job: Job) -> None:
    from .services import ingest

    if job.kind == "ingest":
        ingest.run_ingest_job(session, job.payload)
    else:
        raise ValueError(f"unknown job kind: {job.kind}")


def run_one() -> bool:
    """Claim and run a single job. Returns True if a job was processed."""
    with SessionLocal() as s:
        locked = s.execute(
            sa.select(Job)
            .where(Job.status == "pending", Job.run_after <= sa.func.now())
            .order_by(Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if locked is None:
            return False
        locked.status = "running"
        locked.attempts += 1
        s.commit()
        job_id, kind, attempts = locked.id, locked.kind, locked.attempts

        try:
            process_job(s, locked)
            locked.status = "done"
            s.commit()
        except Exception as exc:
            s.rollback()
            log.exception("job %s (%s) failed", job_id, kind)
            failed = s.get(Job, job_id)
            failed.last_error = str(exc)[:4000]
            if attempts >= MAX_ATTEMPTS:
                failed.status = "failed"
            else:
                failed.status = "pending"
                failed.run_after = datetime.now(UTC) + timedelta(
                    seconds=10 * attempts
                )
            s.commit()
    return True


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
    while True:
        beat()
        worked = False
        try:
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
