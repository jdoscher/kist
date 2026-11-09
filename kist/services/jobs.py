"""The job queue's transitions (lifecycle.JobStatus).

pending -> running      claim_next: a worker takes it (attempts += 1)
running -> done         finish(ok)
running -> pending      finish(error) with attempts left, after a backoff
running -> failed       finish(error) on the last attempt; an inbox file is
                        moved to library/failed/ under its own name
running -> pending      reap_stale: the worker died (lease expired)
failed  -> pending      retry (attempts reset; the file goes back to staging)
failed|done -> (gone)   dismiss
"""

import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..config import get_settings
from ..ids import new_ulid
from ..lifecycle import (
    MAX_ATTEMPTS,
    JobKind,
    JobStatus,
    PreviewStatus,
    TransitionError,
    retry_delay_seconds,
)
from ..models import Item, Job

log = logging.getLogger("kist.jobs")


class NotFound(Exception):
    pass


def failed_dir() -> Path:
    return get_settings().library_dir / "failed"


def job_filename(job: Job) -> str | None:
    p = job.payload or {}
    if p.get("filename"):
        return str(p["filename"])
    if p.get("path"):
        return Path(p["path"]).name
    if p.get("url"):
        return str(p["url"])
    return None


def reap_stale(session: Session, lease_seconds: int) -> int:
    """running jobs whose worker died (claimed longer ago than the lease) go
    back to pending. Their attempt still counts, so a job that kills the
    worker every time ends up failed instead of looping."""
    cutoff = datetime.now(UTC) - timedelta(seconds=lease_seconds)
    stale = session.execute(
        sa.select(Job).where(
            Job.status == JobStatus.RUNNING,
            sa.or_(Job.started_at.is_(None), Job.started_at < cutoff),
        ).with_for_update(skip_locked=True)
    ).scalars().all()
    for job in stale:
        job.last_error = "the worker stopped while running this job"
        if job.attempts >= MAX_ATTEMPTS:
            _fail(session, job)
        else:
            job.status = JobStatus.PENDING
            job.run_after = datetime.now(UTC)
        log.warning("job %s was left running; now %s", job.id, job.status)
    session.commit()
    return len(stale)


def claim_next(session: Session) -> Job | None:
    job = session.execute(
        sa.select(Job)
        .where(Job.status == JobStatus.PENDING, Job.run_after <= sa.func.now())
        .order_by(Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if job is None:
        return None
    job.status = JobStatus.RUNNING
    job.attempts += 1
    job.started_at = datetime.now(UTC)
    job.finished_at = None
    session.commit()
    return job


def finish(session: Session, job_id: int, error: str | None) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise NotFound(job_id)
    if error is None:
        job.status = JobStatus.DONE
        job.last_error = None
        job.finished_at = datetime.now(UTC)
    else:
        job.last_error = error[:4000]
        if job.attempts >= MAX_ATTEMPTS:
            _fail(session, job)
        else:
            job.status = JobStatus.PENDING
            job.run_after = datetime.now(UTC) + timedelta(
                seconds=retry_delay_seconds(job.attempts)
            )
    session.commit()
    return job


def _fail(session: Session, job: Job) -> None:
    job.status = JobStatus.FAILED
    job.finished_at = datetime.now(UTC)
    payload = dict(job.payload or {})
    staged = Path(payload["path"]) if payload.get("path") else None
    if job.kind == JobKind.INGEST and staged is not None and staged.is_file():
        # park it where a person can find it, under the name they gave it
        dest_dir = failed_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = payload.get("filename") or staged.name
        dest = dest_dir / name
        if dest.exists():
            dest = dest_dir / f"{Path(name).stem}-{job.id}{Path(name).suffix}"
        try:
            shutil.move(str(staged), dest)
            payload["failed_path"] = str(dest)
        except OSError:
            log.exception("could not move %s to %s", staged, dest)
    if job.kind == JobKind.REGEN_PREVIEW and payload.get("item_id"):
        item = session.get(Item, payload["item_id"])
        if item is not None and item.preview_status == PreviewStatus.PENDING:
            item.preview_status = PreviewStatus.FAILED
            item.preview_error = (job.last_error or "preview retry failed")[-500:]
    job.payload = payload


def list_jobs(session: Session, status: str | None = None, limit: int = 100) -> list[Job]:
    stmt = sa.select(Job).order_by(Job.id.desc()).limit(limit)
    if status is not None:
        if status not in tuple(JobStatus):
            raise ValueError(f"unknown status {status!r}")
        stmt = stmt.where(Job.status == status)
    return list(session.execute(stmt).scalars())


def get_job(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise NotFound(job_id)
    return job


def retry(session: Session, job_id: int) -> Job:
    """failed -> pending, with a fresh set of attempts."""
    job = get_job(session, job_id)
    if job.status != JobStatus.FAILED:
        raise TransitionError(f"job {job_id} is {job.status}; only failed jobs can be retried")
    payload = dict(job.payload or {})
    parked = Path(payload["failed_path"]) if payload.get("failed_path") else None
    if parked is not None:
        if not parked.is_file():
            raise TransitionError(
                f"the file for job {job_id} is no longer in the failed folder"
            )
        staging = get_settings().staging_dir
        staging.mkdir(parents=True, exist_ok=True)
        staged = staging / f"retry-{new_ulid()}-{parked.name}"
        shutil.move(str(parked), staged)
        payload["path"] = str(staged)
        payload.pop("failed_path", None)
    job.payload = payload
    job.status = JobStatus.PENDING
    job.attempts = 0
    job.run_after = datetime.now(UTC)
    job.started_at = job.finished_at = None
    if job.kind == JobKind.REGEN_PREVIEW and payload.get("item_id"):
        item = session.get(Item, payload["item_id"])
        if item is not None:
            item.preview_status = PreviewStatus.PENDING
    session.commit()
    return job


def dismiss(session: Session, job_id: int) -> str | None:
    """Forget a failed or done job. Returns where its file was left, if any."""
    job = get_job(session, job_id)
    if job.status not in (JobStatus.FAILED, JobStatus.DONE):
        raise TransitionError(f"job {job_id} is {job.status}; it can't be dismissed yet")
    left = (job.payload or {}).get("failed_path")
    session.delete(job)
    session.commit()
    return left


def enqueue_preview(session: Session, item: Item) -> Job:
    """Queue a preview retry for an item (once: an open retry is reused)."""
    if item.preview_status == PreviewStatus.NONE:
        raise TransitionError(f"item {item.id} is a file type Kist can't preview")
    open_job = session.execute(
        sa.select(Job).where(
            Job.kind == JobKind.REGEN_PREVIEW,
            Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
            Job.payload["item_id"].astext == item.id,
        )
    ).scalars().first()
    if open_job is not None:
        return open_job
    job = Job(kind=JobKind.REGEN_PREVIEW, payload={"item_id": item.id, "filename": item.filename})
    session.add(job)
    item.preview_status = PreviewStatus.PENDING
    session.commit()
    return job
