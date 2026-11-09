"""The job queue's transitions (lifecycle.JobStatus).

pending -> running      claim_next: a worker takes it (attempts += 1)
running -> done         finish(ok)
running -> pending      finish(error) with attempts left, after a backoff
running -> failed       finish(error) on the last attempt; an inbox file is
                        moved to library/failed/ under its own name
running -> pending      reap_stale: the worker died (lease expired)
failed  -> pending      retry (attempts reset; the file goes back to staging)
failed|done -> (gone)   dismiss

Files move only after the commit that records where they go, and a move
that fails is written back, so a job never points at a place its file
isn't (and a crash in between leaves the file in staging, where the
worker's sweep finds it).
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
from . import meta as meta_svc
from .validation import ValidationError

log = logging.getLogger("kist.jobs")


class NotFound(Exception):
    def __init__(self, job_id: int):
        super().__init__(f"job {job_id} not found")
        self.job_id = job_id


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
    moves: list[tuple[int, Path, Path]] = []
    items: list[str] = []
    for job in stale:
        job.last_error = "the worker stopped while running this job"
        if job.attempts >= MAX_ATTEMPTS:
            move, item_id = _fail(session, job)
            moves += [move] if move else []
            items += [item_id] if item_id else []
        else:
            job.status = JobStatus.PENDING
            job.run_after = datetime.now(UTC)
        log.warning("job %s was left running; now %s", job.id, job.status)
    session.commit()
    _after_commit(session, moves, items)
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
    move = item_id = None
    if error is None:
        job.status = JobStatus.DONE
        job.last_error = None
        job.finished_at = datetime.now(UTC)
    else:
        job.last_error = error[:4000]
        if job.attempts >= MAX_ATTEMPTS:
            move, item_id = _fail(session, job)
        else:
            job.status = JobStatus.PENDING
            job.run_after = datetime.now(UTC) + timedelta(
                seconds=retry_delay_seconds(job.attempts)
            )
    session.commit()
    _after_commit(session, [move] if move else [], [item_id] if item_id else [])
    return job


def _fail(session: Session, job: Job) -> tuple[tuple[int, Path, Path] | None, str | None]:
    """Mark a job failed. Returns the file move to make once that's committed
    (an inbox file parked in library/failed/) and the item whose meta.json
    needs rewriting (a preview retry that gave up)."""
    job.status = JobStatus.FAILED
    job.finished_at = datetime.now(UTC)
    payload = dict(job.payload or {})
    move = item_id = None
    staged = Path(payload["path"]) if payload.get("path") else None
    if job.kind == JobKind.INGEST and staged is not None and staged.is_file():
        # park it where a person can find it, under the name they gave it
        dest_dir = failed_dir()
        name = Path(payload.get("filename") or staged.name).name
        dest = dest_dir / name
        if dest.exists():
            dest = dest_dir / f"{Path(name).stem}-{job.id}{Path(name).suffix}"
        payload["failed_path"] = str(dest)
        move = (job.id, staged, dest)
    if job.kind == JobKind.REGEN_PREVIEW and payload.get("item_id"):
        item = session.get(Item, payload["item_id"])
        if item is not None and item.preview_status == PreviewStatus.PENDING:
            item.preview_status = PreviewStatus.FAILED
            item.preview_error = (job.last_error or "preview retry failed")[-500:]
            item_id = item.id
    job.payload = payload
    return move, item_id


def _after_commit(
    session: Session, moves: list[tuple[int, Path, Path]], item_ids: list[str]
) -> None:
    for job_id, src, dest in moves:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), dest)
        except OSError:
            log.exception("could not move %s to %s; it stays in staging", src, dest)
            job = session.get(Job, job_id)
            if job is not None:
                payload = dict(job.payload or {})
                payload.pop("failed_path", None)
                job.payload = payload
                session.commit()
    for item_id in item_ids:
        meta_svc.sync_item(session, item_id)


def job_out(job: Job):
    """The job as REST and MCP return it."""
    from ..schemas import JobOut

    return JobOut(
        id=job.id,
        kind=job.kind,
        status=job.status,
        attempts=job.attempts,
        filename=job_filename(job),
        item_id=(job.payload or {}).get("item_id"),
        last_error=job.last_error,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def list_jobs(session: Session, status: str | None = None, limit: int = 100) -> list[Job]:
    stmt = sa.select(Job).order_by(Job.id.desc()).limit(limit)
    if status is not None:
        if status not in tuple(JobStatus):
            raise ValidationError(f"unknown status {status!r}")
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
    before = dict(job.payload or {})
    payload = dict(before)
    parked = Path(payload["failed_path"]) if payload.get("failed_path") else None
    staged = None
    if parked is not None:
        if not parked.is_file():
            raise TransitionError(
                f"the file for job {job_id} is no longer in the failed folder"
            )
        staged = get_settings().staging_dir / f"retry-{new_ulid()}-{parked.name}"
        payload["path"] = str(staged)
        payload.pop("failed_path", None)
    job.payload = payload
    job.status = JobStatus.PENDING
    job.attempts = 0
    # not before the file is back in staging (moved after this commit)
    job.run_after = datetime.now(UTC) + timedelta(seconds=60 if staged else 0)
    job.started_at = job.finished_at = None
    item_id = None
    if job.kind == JobKind.REGEN_PREVIEW and payload.get("item_id"):
        item = session.get(Item, payload["item_id"])
        if item is not None:
            item.preview_status = PreviewStatus.PENDING
            item_id = item.id
    session.commit()
    if staged is not None:
        try:
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(parked), staged)
        except OSError as exc:
            job.status = JobStatus.FAILED
            job.payload = before
            job.finished_at = datetime.now(UTC)
            session.commit()
            raise TransitionError(f"could not move the file back for job {job_id}: {exc}") from None
        job.run_after = datetime.now(UTC)
        session.commit()
    if item_id:
        meta_svc.sync_item(session, item_id)
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
    meta_svc.sync_item(session, item.id)
    return job


def retry_preview(session: Session, item_id: str) -> Item:
    """Queue a fresh attempt at an item's previews; returns the item."""
    from .items import get_item

    item = get_item(session, item_id)
    enqueue_preview(session, item)
    session.refresh(item)
    return item


def recover_staging(min_age_seconds: float = 60) -> int:
    """Queue inbox files stranded in library/.staging with no job pointing at
    them (the process stopped between staging a file and queueing it).
    Run by the worker at start and every few minutes. Returns how many."""
    import time

    from ..db import SessionLocal

    staging = get_settings().staging_dir
    if not staging.is_dir():
        return 0
    now = time.time()
    queued = 0
    for path in sorted([*staging.glob("inbox-*"), *staging.glob("retry-*")]):
        try:
            if not path.is_file() or now - path.stat().st_mtime < min_age_seconds:
                continue
        except FileNotFoundError:
            continue
        with SessionLocal() as session:
            known = session.execute(
                sa.select(Job.id).where(Job.payload["path"].astext == str(path)).limit(1)
            ).first()
            if known is not None:
                continue
            # inbox-<ulid>-<original name> / retry-<ulid>-<original name>
            parts = path.name.split("-", 2)
            name = parts[2] if len(parts) == 3 and parts[2] else path.name
            session.add(
                Job(
                    kind=JobKind.INGEST,
                    payload={"path": str(path), "filename": name, "recovered": True},
                )
            )
            session.commit()
        queued += 1
        log.warning("re-queued %s, left in staging without a job", path.name)
    return queued
