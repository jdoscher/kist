"""AI status and the helper job endpoints (contract section 8).

    GET  /api/ai/status                 admin
    POST /api/ai/jobs/claim             helper: {kinds, max, name?}
    POST /api/ai/jobs/{id}/result       helper: {vector, model} | {text, ...} | {error}

A helper names itself in the X-Kist-Helper header (or "name" in the claim
body); it may only report on jobs it claimed. Leases follow the job lease
rules: a claimed job that isn't reported within KIST_JOB_LEASE_SECONDS goes
back to pending.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import require_scope
from ..db import get_session
from ..schemas import JobOut
from ..services import ai
from ..services import jobs as jobs_svc

router = APIRouter(prefix="/ai", tags=["ai"])


class ClaimIn(BaseModel):
    kinds: list[str] = Field(default_factory=lambda: ["embed", "ocr"])
    max: int = Field(default=8, ge=1, le=64)
    name: str | None = Field(default=None, max_length=100)
    info: dict[str, Any] | None = None


class ResultIn(BaseModel):
    vector: list[float] | None = None
    model: str | None = Field(default=None, max_length=200)
    text: str | None = None
    lang: str | None = Field(default=None, max_length=20)
    boxes: list[Any] | None = None
    error: str | None = Field(default=None, max_length=4000)


def _helper_name(header: str | None, body_name: str | None = None) -> str:
    name = (body_name or header or "").strip()
    return name[:100] or "helper"


@router.get("/status", dependencies=[Depends(require_scope("admin"))])
def status(session: Annotated[Session, Depends(get_session)]) -> dict:
    """Model, whether the ml service answers, how much is done and waiting,
    the backfill window and the helpers seen."""
    return ai.status(session)


@router.post("/jobs/claim", dependencies=[Depends(require_scope("helper"))])
def claim(
    body: ClaimIn,
    session: Annotated[Session, Depends(get_session)],
    x_kist_helper: Annotated[str | None, Header()] = None,
) -> dict:
    name = _helper_name(x_kist_helper, body.name)
    jobs = ai.helper_claim(session, name, body.kinds, body.max, info=body.info)
    return {"model": ai.active_model(), "jobs": jobs}


@router.post("/jobs/{job_id}/result", response_model=JobOut,
             dependencies=[Depends(require_scope("helper"))])
def result(
    job_id: int,
    body: ResultIn,
    session: Annotated[Session, Depends(get_session)],
    x_kist_helper: Annotated[str | None, Header()] = None,
):
    try:
        job = ai.helper_result(
            session, job_id, _helper_name(x_kist_helper), body.model_dump(exclude_none=True)
        )
    except ai.ResultRejected as exc:
        session.rollback()
        raise HTTPException(422, str(exc)) from None
    return jobs_svc.job_out(job)
