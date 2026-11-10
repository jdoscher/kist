"""GET /api/items/buckets: month counts + jump cursors for the date
scrubber (Milestone 6; services/buckets.py).

Included before the items router (api/__init__.py) so /items/buckets isn't
taken for an item id by GET /items/{item_id}.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_session
from ..schemas import BucketsOut
from ..services import buckets as buckets_svc
from ..services import search
from .items import search_filters

router = APIRouter(prefix="/items", tags=["items"])


@router.get("/buckets", response_model=BucketsOut)
def item_buckets(session: Annotated[Session, Depends(get_session)],
                 params: Annotated[dict, Depends(search_filters)],
                 sort: str | None = None):
    """Items per month (UTC) for the same filters as GET /api/items, in the
    list's order: sort = created | modified | taken, order = asc | desc (or
    the list's own order=-taken). Each bucket's cursor, passed to GET
    /api/items with order=<item_order>, starts at that month's first item."""
    order = params.get("order")
    filters = search.filters_from_params(params, session)
    return buckets_svc.buckets(session, filters, sort=sort, order=order)
