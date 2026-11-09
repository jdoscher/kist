from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_session
from ..schemas import JobOut
from ..services import jobs as jobs_svc

# jobs_svc.NotFound -> 404, TransitionError -> 409, ValueError (bad status
# filter) -> 422: handlers in kist/app.py
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
def list_jobs(
    session: Annotated[Session, Depends(get_session)],
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    """Background jobs, newest first: inbox imports and preview retries."""
    return [jobs_svc.job_out(j) for j in jobs_svc.list_jobs(session, status, limit)]


@router.post("/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: int, session: Annotated[Session, Depends(get_session)]):
    return jobs_svc.job_out(jobs_svc.retry(session, job_id))


@router.delete("/{job_id}")
def dismiss_job(job_id: int, session: Annotated[Session, Depends(get_session)]):
    left = jobs_svc.dismiss(session, job_id)
    return {"status": "dismissed", "file_left_at": left}
