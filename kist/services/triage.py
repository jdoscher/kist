"""Triage queue (Milestone 3): an ordered cursor over a view, for the
keyboard and swipe triage modes.

GET /api/triage/next?view=unfiled&after=<id> answers the item after `after`
in the view's order (the same ordering and filters as GET /api/items), plus
the ids of the next few to prefetch and how many are left in the view.
`after` may be an item that has just left the view (filed, tagged,
trashed): the cursor is built from its sort value, so the queue carries on
where it was.
"""

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..models import Item
from . import items as items_svc
from . import search, validation

PREFETCH_MAX = 20


def next_item(session: Session, *, view: str | None = "unfiled", after: str | None = None,
              order: str = "-created", prefetch: int = 5, folder: str | None = None,
              flag: str | None = None, label: str | None = None,
              tags: list[str] | None = None, state: str | None = None) -> dict:
    if view in ("", "all"):
        view = None
    if order not in search.ORDERS:
        raise validation.ValidationError(f"unknown order {order!r}")
    prefetch = max(0, min(prefetch, PREFETCH_MAX))
    cursor = None
    if after:
        col, _ = search.ORDERS[order]
        anchor = items_svc.get_item(session, after)
        cursor = search.encode_cursor(getattr(anchor, col.key), anchor.id)
    params = dict(view=view, folder=folder, flag=flag, label=label, tags=tags or None,
                  state=state)
    rows, _, _ = search.search_items(session, order=order, limit=1 + prefetch,
                                     cursor=cursor, **params)
    filters = search.build_filters(**params)
    remaining = session.execute(
        sa.select(sa.func.count()).select_from(Item).where(*filters)
    ).scalar_one()
    return {
        "item": rows[0] if rows else None,
        "prefetch_ids": [r.id for r in rows[1:]],
        "remaining": remaining,
    }
