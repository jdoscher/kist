from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_session
from ..schemas import BatchOut, UndoOut
from ..services import events as events_svc

# events_svc.NotFound -> 404 (kist/app.py)
router = APIRouter(tags=["history"])


@router.get("/batches", response_model=list[BatchOut])
def recent_batches(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    actor: str | None = None,
):
    """Recent batches of changes, newest first: what one request, MCP tool
    call or CLI command did (or several requests sent with one X-Kist-Batch)."""
    return events_svc.recent_batches(session, limit, actor)


@router.post("/batches/{batch_id}/undo", response_model=UndoOut)
def undo_batch(batch_id: str, session: Annotated[Session, Depends(get_session)]):
    """Revert every change in the batch, newest first, as a new batch. A
    change is skipped when something later touched the same field."""
    return events_svc.undo_batch(session, batch_id)


@router.post("/events/{event_id}/revert", response_model=UndoOut)
def revert_event(event_id: int, session: Annotated[Session, Depends(get_session)]):
    return events_svc.revert_event(session, event_id)
