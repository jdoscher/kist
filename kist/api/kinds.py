"""F1 utility collections ("Kinds") endpoints.

    GET    /api/kinds                     every kind: settings, counts, learning
    PATCH  /api/kinds                     {packs?, min_items?, low_res_px?, low_res_in?,
                                           kinds?: {id: {enabled?, strictness?}}}
    PATCH  /api/kinds/{id}                {enabled?, strictness?}
    POST   /api/kinds/{id}/correct        {item_ids, verdict: true | false | null}
    POST   /api/kinds/refresh             re-sort what is out of date (a job)
    GET    /api/items/{id}/kinds          an item's kinds, with score, why and source

Items in a kind: GET /api/items?auto_kind=<id> (and -<id>, <id>:unsure,
<id>:auto, <id>:confirmed, <id>:rejected), like every other filter.

Reads need read (a folder-limited reader sees counts and items of its own
folders only); changes need write, agent or admin.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_session
from ..services import kinds as kinds_svc

router = APIRouter(tags=["kinds"])


class KindPatch(BaseModel):
    enabled: bool | None = None
    strictness: float | None = Field(default=None, ge=0, le=1)


class KindsPatch(BaseModel):
    packs: dict[str, bool] | None = None
    min_items: int | None = Field(default=None, ge=0, le=100_000)
    low_res_px: int | None = Field(default=None, ge=16, le=100_000)
    # with F4's print check: Low-res = prints under this many inches at 300 dpi
    low_res_in: float | None = Field(default=None, ge=0.5, le=100)
    kinds: dict[str, dict] | None = None


class CorrectIn(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=kinds_svc.MAX_CORRECT)
    # true: belongs here; false: not this; null: back to automatic
    verdict: bool | None


@router.get("/kinds")
def list_kinds(session: Annotated[Session, Depends(get_session)]) -> dict:
    return kinds_svc.overview(session)


@router.patch("/kinds")
def patch_kinds(body: KindsPatch, session: Annotated[Session, Depends(get_session)]) -> dict:
    kinds_svc.update_settings(session, packs=body.packs, min_items=body.min_items,
                              low_res_px=body.low_res_px, kinds=body.kinds,
                              low_res_in=body.low_res_in)
    return kinds_svc.overview(session)


@router.patch("/kinds/{kind_id}")
def patch_kind(kind_id: str, body: KindPatch,
               session: Annotated[Session, Depends(get_session)]) -> dict:
    patch = body.model_dump(exclude_unset=True)
    kinds_svc.update_settings(session, kinds={kind_id: patch})
    return next(k for k in kinds_svc.overview(session)["kinds"] if k["id"] == kind_id)


@router.post("/kinds/{kind_id}/correct")
def correct(kind_id: str, body: CorrectIn,
            session: Annotated[Session, Depends(get_session)]) -> dict:
    return kinds_svc.correct(session, body.item_ids, kind_id, body.verdict)


@router.post("/kinds/refresh", status_code=202)
def refresh(session: Annotated[Session, Depends(get_session)]) -> dict:
    from .. import access

    access.require(access.Action.EDIT)
    kinds_svc.queue_all(session)
    return {"queued": True}


@router.get("/items/{item_id}/kinds")
def item_kinds(item_id: str, session: Annotated[Session, Depends(get_session)]) -> list[dict]:
    return kinds_svc.item_kinds(session, item_id)
