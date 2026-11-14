"""AI status and the helper job endpoints (contract section 8).

    GET  /api/ai/status                 admin
    POST /api/ai/jobs/claim             helper: {kinds, max, name?}
    GET  /api/ai/jobs/{id}/image        helper: the image to run a claimed job on
    POST /api/ai/jobs/{id}/result       helper: {vector, model} | {text, ...} | {error}

A helper token reaches nothing but these job endpoints (access.py), so it
fetches a claimed job's image here rather than from /api/items/{id}/thumb.

A helper names itself in the X-Kist-Helper header (or "name" in the claim
body); it may only report on jobs it claimed. Leases follow the job lease
rules: a claimed job that isn't reported within KIST_JOB_LEASE_SECONDS goes
back to pending.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import access
from ..auth import require_scope
from ..db import get_session
from ..schemas import JobOut
from ..services import ai
from ..services import jobs as jobs_svc
from . import items as items_api
from . import thumbfiles

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


def _helper_name(request: Request, header: str | None, body_name: str | None = None) -> str:
    """The helper's name: the claim body's, else X-Kist-Helper, else the
    name of the (non-owner) token it uses."""
    name = (body_name or header or "").strip()
    if not name:
        principal = request.scope.get("state", {}).get("principal")
        if principal is not None and principal.name != access.OWNER_NAME:
            name = principal.name
    return name[:100] or "helper"


@router.get("/status", dependencies=[Depends(require_scope("admin"))])
def status(session: Annotated[Session, Depends(get_session)]) -> dict:
    """Model, whether the ml service answers, how much is done and waiting,
    the backfill window and the helpers seen."""
    return ai.status(session)


@router.post("/jobs/claim", dependencies=[Depends(require_scope("helper"))])
def claim(
    body: ClaimIn,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    x_kist_helper: Annotated[str | None, Header()] = None,
) -> dict:
    name = _helper_name(request, x_kist_helper, body.name)
    jobs = ai.helper_claim(session, name, body.kinds, body.max, info=body.info)
    return {"model": ai.active_model(), "jobs": jobs}


@router.get("/jobs/{job_id}/image", dependencies=[Depends(require_scope("helper"))])
def job_image(
    job_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    x_kist_helper: Annotated[str | None, Header()] = None,
):
    """The thumbnail a claimed job runs on (512 px for embed, the 2048 px
    screen copy for OCR). Only the helper holding the job may fetch it."""
    item_id, size = ai.helper_job_image(session, job_id, _helper_name(request, x_kist_helper))
    items_api.items_svc.get_item(session, item_id)
    _, names = thumbfiles.thumb_candidates(size)
    return thumbfiles.file_response(thumbfiles.item_dir(item_id), names, None)


@router.post("/jobs/{job_id}/result", response_model=JobOut,
             dependencies=[Depends(require_scope("helper"))])
def result(
    job_id: int,
    body: ResultIn,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    x_kist_helper: Annotated[str | None, Header()] = None,
):
    try:
        job = ai.helper_result(
            session, job_id, _helper_name(request, x_kist_helper),
            body.model_dump(exclude_none=True)
        )
    except ai.ResultRejected as exc:
        session.rollback()
        raise HTTPException(422, str(exc)) from None
    return jobs_svc.job_out(job)
