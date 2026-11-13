"""F4 REST routes: credits and rights, the clearance check, auto-fill and
the print check (services/credits.py).

    GET   /api/credits/settings              {clearance_warning}
    PATCH /api/credits/settings              {clearance_warning}
    GET   /api/credits/summary               {needs_credit, uncleared, ai_generated}
    POST  /api/credits/clearance             {target, target_id?, item_ids?}
    POST  /api/credits/bulk                  {item_ids, provenance}
    GET   /api/items/{id}/print              the print check
    GET   /api/items/{id}/credits            credit line, Present caption, auto-fill suggestions
    POST  /api/items/{id}/credits/autofill   fill empty fields from the stored files
"""

from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import access
from ..db import get_session
from ..models import Item
from ..schemas import ItemOut
from ..services import credits, events, search, validation
from ..services import items as items_svc

router = APIRouter(tags=["credits"])

# what a bulk credit edit may set (null removes the field)
BULK_FIELDS = ("creator", "credit", "license", "rights_url", "rights_status", "credit_exempt")
BULK_MAX = 2000


class SettingsPatch(BaseModel):
    clearance_warning: bool | None = None


@router.get("/credits/settings")
def get_settings_() -> dict:
    return credits.read_settings()


@router.patch("/credits/settings")
def patch_settings(body: SettingsPatch) -> dict:
    access.require(access.Action.EDIT)
    return credits.write_settings(body.model_dump(exclude_none=True))


@router.get("/credits/summary")
def summary(session: Annotated[Session, Depends(get_session)]) -> dict:
    """Counts for the sidebar: live items needing a credit, items not
    licensed/cleared and AI-made items. Informational: never \"needs
    attention\"."""

    def count(**kw) -> int:
        conds = search.build_filters(state="active", session=session, **kw)
        return session.execute(sa.select(sa.func.count()).select_from(Item).where(*conds)
                               ).scalar_one()

    return {
        "needs_credit": count(credits={"needs_credit": True}),
        "uncleared": count(credits={"rights_status": "uncleared"}),
        "ai_generated": count(ai_generated="true"),
    }


class ClearanceIn(BaseModel):
    target: str = "items"  # folder | smart | board | items | all
    target_id: str | None = None
    item_ids: list[str] | None = Field(default=None, max_length=100_000)


@router.post("/credits/clearance")
def clearance(body: ClearanceIn, session: Annotated[Session, Depends(get_session)]) -> dict:
    """Which of these items aren't licensed or cleared: the non-blocking
    warning before a share or an export. `enabled` false means the user
    turned the warning off (the list is still there for agents)."""
    return credits.clearance_for_target(session, body.target, body.target_id, body.item_ids)


class BulkIn(BaseModel):
    item_ids: list[str] = Field(max_length=BULK_MAX)
    provenance: dict[str, Any]


@router.post("/credits/bulk")
def bulk(body: BulkIn, session: Annotated[Session, Depends(get_session)]) -> dict:
    """Set credit/creator/licence/rights status/no-credit-needed on many
    items: one batch in the history, so one Undo reverts it."""
    access.require(access.Action.EDIT)
    patch = {k: v for k, v in body.provenance.items()}
    bad = sorted(set(patch) - set(BULK_FIELDS))
    if bad:
        raise HTTPException(422, f"bulk credits take only {', '.join(BULK_FIELDS)}; "
                                 f"not {', '.join(bad)}")
    if not patch:
        raise HTTPException(422, "provenance is empty")
    from ..services import provenance as provenance_svc

    provenance_svc.clean(patch, partial=True)  # 422 before any item changes
    ok: list[str] = []
    failed: list[dict] = []
    for item_id in dict.fromkeys(body.item_ids):
        try:
            items_svc.update_item(session, item_id, fields={"provenance": patch})
            ok.append(item_id)
        except items_svc.NotFound:
            session.rollback()
            failed.append({"id": item_id, "error": "not found"})
        except validation.ValidationError as exc:
            session.rollback()
            failed.append({"id": item_id, "error": str(exc)})
    return {"updated": len(ok), "failed": failed, "batch_id": events.context().batch_id}


def _item(session: Session, item_id: str) -> Item:
    try:
        return items_svc.get_item(session, item_id)
    except items_svc.NotFound:
        raise HTTPException(404, f"item {item_id} not found") from None


@router.get("/items/{item_id}/print")
def print_check(item_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    """Print size at 300 and 150 dpi, the papers it fills, the file's own
    dpi and its colour profile (read from the original's header)."""
    return credits.print_info(_item(session, item_id))


@router.get("/items/{item_id}/credits")
def item_credits(item_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    """The credit as outputs print it, Present's caption, and what auto-fill
    would add from the item's stored files (nothing is changed)."""
    item = _item(session, item_id)
    return {**credits.item_credit(item), "text": credits.output_text(item),
            "caption": credits.present_caption(item),
            "suggestions": credits.suggestions(item)}


@router.post("/items/{item_id}/credits/autofill")
def autofill(item_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    """Fill the item's empty credit fields from its embedded metadata and
    stored page snapshot (never the network). An edit: in the history and
    undoable."""
    access.require(access.Action.EDIT)
    _item(session, item_id)
    filled = credits.autofill(session, item_id, keep_modified=False)
    item = items_svc.get_item(session, item_id)
    return {"filled": filled, "item": ItemOut.from_item(item).model_dump(mode="json")}
