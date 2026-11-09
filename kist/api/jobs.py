from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_session
from ..lifecycle import TransitionError
from ..models import Job
from ..schemas import JobOut
from ..services import jobs as jobs_svc

router = APIRouter(prefix="/jobs", tags=["jobs"])


def job_out(job: Job) -> JobOut:
    return JobOut(
        id=job.id,
        kind=job.kind,
        status=job.status,
        attempts=job.attempts,
        filename=jobs_svc.job_filename(job),
        item_id=(job.payload or {}).get("item_id"),
        last_error=job.last_error,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.get("", response_model=list[JobOut])
def list_jobs(
    session: Annotated[Session, Depends(get_session)],
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    """Background jobs, newest first: inbox imports and preview retries."""
    try:
        return [job_out(j) for j in jobs_svc.list_jobs(session, status, limit)]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: int, session: Annotated[Session, Depends(get_session)]):
    try:
        return job_out(jobs_svc.retry(session, job_id))
    except jobs_svc.NotFound:
        raise HTTPException(404, f"job {job_id} not found") from None
    except TransitionError as exc:
        raise HTTPException(409, str(exc)) from None


@router.delete("/{job_id}")
def dismiss_job(job_id: int, session: Annotated[Session, Depends(get_session)]):
    try:
        left = jobs_svc.dismiss(session, job_id)
    except jobs_svc.NotFound:
        raise HTTPException(404, f"job {job_id} not found") from None
    except TransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"status": "dismissed", "file_left_at": left}
