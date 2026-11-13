"""F3 guest proofing, the owner's side: per-share settings and rounds,
the Feedback inbox, counts for tiles and the sidebar, Accept and delete.
Write scope (like /api/shares): a read token, folder-limited or not,
never sees guest feedback. The guest side is kist/proofing_pages.py."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import require_scope
from ..db import get_session
from ..services import proofing
from ..services import shares as shares_svc

router = APIRouter(tags=["proofing"], dependencies=[Depends(require_scope("write"))])


class ProofingPatch(BaseModel):
    enabled: bool | None = None  # "Let guests pick and comment"
    show_all: bool | None = None  # guests see everyone's feedback
    round_open: bool | None = None  # false closes the current round


class AcceptBody(BaseModel):
    mark_ids: list[str] = Field(default_factory=list, max_length=proofing.LIST_MAX)
    comment_ids: list[str] = Field(default_factory=list, max_length=proofing.LIST_MAX)
    # pick | reject | label:<colour>; default: what the guest chose (a heart: pick)
    as_: str | None = Field(default=None, alias="as", max_length=20)


class SeenBody(BaseModel):
    share_id: str | None = None


def _out(session: Session, share_id: str) -> dict[str, Any]:
    share = shares_svc.get(session, share_id)
    return proofing.settings_out(session, share, proofing.get_settings_row(session, share_id))


@router.get("/shares/{share_id}/proofing")
def get_proofing(share_id: str, session: Annotated[Session, Depends(get_session)]):
    return _out(session, share_id)


@router.patch("/shares/{share_id}/proofing")
def patch_proofing(share_id: str, body: ProofingPatch,
                   session: Annotated[Session, Depends(get_session)]):
    shares_svc.get(session, share_id)
    proofing.update_settings(session, share_id, body.model_dump(exclude_unset=True))
    return _out(session, share_id)


@router.post("/shares/{share_id}/proofing/rounds", status_code=201)
def new_round(share_id: str, session: Annotated[Session, Depends(get_session)]):
    shares_svc.get(session, share_id)
    proofing.start_round(session, share_id)
    return _out(session, share_id)


@router.get("/feedback")
def list_feedback(
    session: Annotated[Session, Depends(get_session)],
    share_id: str | None = None,
    round: Annotated[int | None, Query(ge=1)] = None,
    item_id: str | None = None,
    unseen: bool = False,
    limit: Annotated[int, Query(ge=1, le=proofing.LIST_MAX)] = 200,
):
    return proofing.feedback_rows(session, share_id=share_id, round=round, item_id=item_id,
                                  unseen=unseen, limit=limit)


@router.get("/feedback/summary")
def feedback_summary(session: Annotated[Session, Depends(get_session)]):
    return proofing.summary(session)


@router.get("/feedback/counts")
def feedback_counts(session: Annotated[Session, Depends(get_session)]):
    """{item_id: {picks, rejects, hearts, comments}} in current rounds."""
    return proofing.item_counts(session)


@router.post("/feedback/seen")
def feedback_seen(body: SeenBody, session: Annotated[Session, Depends(get_session)]):
    return {"marked": proofing.mark_seen(session, body.share_id)}


@router.post("/feedback/accept")
def feedback_accept(body: AcceptBody, session: Annotated[Session, Depends(get_session)]):
    return proofing.accept(session, mark_ids=body.mark_ids, comment_ids=body.comment_ids,
                           as_=body.as_)


@router.delete("/feedback/comments/{comment_id}")
def feedback_delete_comment(comment_id: str, session: Annotated[Session, Depends(get_session)]):
    proofing.delete_comment(session, comment_id)
    return {"status": "deleted"}
