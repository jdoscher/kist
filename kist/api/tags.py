from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_session
from ..schemas import TagOut
from ..services import tags as tags_svc

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagOut])
def list_tags(session: Annotated[Session, Depends(get_session)], namespace: str | None = None):
    """Every tag with its count, namespace ("brand" for brand:rolex) and
    value. namespace=brand lists only that namespace; namespace=none only
    tags outside every namespace."""
    return tags_svc.list_with_counts(session, namespace)


@router.delete("/unused")
def prune_unused_tags(session: Annotated[Session, Depends(get_session)]):
    """Delete every tag that no item (live or trashed) uses."""
    return {"deleted": tags_svc.prune_unused(session)}
