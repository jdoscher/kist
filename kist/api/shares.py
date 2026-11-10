"""Share links: /api/shares (admin and write tokens only). The public
pages are kist/share_pages.py."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import access
from ..auth import require_scope
from ..db import get_session
from ..services import shares as shares_svc

router = APIRouter(prefix="/shares", tags=["shares"],
                   dependencies=[Depends(require_scope("write"))])


class ShareCreate(BaseModel):
    target_kind: str  # folder | smart | board | items
    target_id: str | None = None
    item_ids: list[str] | None = None
    title: str = Field(default="", max_length=shares_svc.TITLE_MAX)
    password: str | None = Field(default=None, max_length=shares_svc.PASSWORD_MAX)
    # expires_at wins; else expires_in_days (default 30, 0 = never)
    expires_at: datetime | None = None
    expires_in_days: float | None = Field(default=None, ge=0, le=3650)
    allow_download: bool = False
    allow_original: bool = False  # needs allow_download


class SharePatch(BaseModel):
    title: str | None = Field(default=None, max_length=shares_svc.TITLE_MAX)
    # "" or null removes the password
    password: str | None = Field(default=None, max_length=shares_svc.PASSWORD_MAX)
    expires_at: datetime | None = None
    allow_download: bool | None = None
    allow_original: bool | None = None
    item_ids: list[str] | None = None
    revoked: bool | None = None


def _who() -> str:
    p = access.current()
    return p.name if p is not None else "cli"


@router.get("")
def list_shares(session: Annotated[Session, Depends(get_session)]) -> list[dict[str, Any]]:
    return [shares_svc.share_out(s) for s in shares_svc.list_shares(session)]


@router.post("", status_code=201)
def create_share(body: ShareCreate, session: Annotated[Session, Depends(get_session)]):
    share = shares_svc.create(session, **body.model_dump(), created_by=_who())
    return shares_svc.share_out(share)


@router.get("/{share_id}")
def get_share(share_id: str, session: Annotated[Session, Depends(get_session)]):
    return shares_svc.share_out(shares_svc.get(session, share_id))


@router.patch("/{share_id}")
def patch_share(share_id: str, body: SharePatch,
                session: Annotated[Session, Depends(get_session)]):
    share = shares_svc.update(session, share_id, body.model_dump(exclude_unset=True))
    return shares_svc.share_out(share)


@router.post("/{share_id}/revoke")
def revoke_share(share_id: str, session: Annotated[Session, Depends(get_session)]):
    return shares_svc.share_out(shares_svc.revoke(session, share_id))


@router.delete("/{share_id}")
def delete_share(share_id: str, session: Annotated[Session, Depends(get_session)]):
    shares_svc.delete(session, share_id)
    return {"status": "deleted"}
