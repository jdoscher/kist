"""Month buckets for the grid's date scrubber (Milestone 6).

GET /api/items/buckets and the MCP tool item_buckets: how many items of a
filtered listing fall in each month, in the listing's own order, each with
a keyset cursor that makes GET /api/items?order=<item_order>&cursor=<cursor>
start at that month's first item.

One GROUP BY over the same filters as GET /api/items (search.build_filters
through filters_from_params, so smart folders, tags, colour labels, text=
and a folder-limited token's scope all apply). Months are UTC calendar
months, like the timeline (services/timeline.py: EXIF times are stored as
wall-clock time, so a photo stays in the month it was taken).

The cursor is search.encode_cursor(<month boundary>, ""): for a descending
order that is the start of the NEXT month (keyset "col < boundary" keeps
everything older), for an ascending one the start of the month itself
("col > start, or col = start and id > ''" keeps the whole month). An item
id is never the empty string, so the boundary is exclusive/inclusive
exactly as needed and no item is skipped or repeated.
"""

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..models import Item
from .search import ORDERS, encode_cursor
from .validation import ValidationError

# bucket sort name -> the column GET /api/items sorts by (search.ORDERS)
SORTS: dict[str, Any] = {
    "created": Item.created_at,
    "modified": Item.modified_at,
    "taken": Item.taken_sort,
}
DIRECTIONS = ("asc", "desc")


def resolve(sort: str | None, order: str | None) -> tuple[str, str, str]:
    """(sort, asc|desc, the GET /api/items order key) from either form:
    sort=taken&order=desc, or order=-taken as GET /api/items takes it (so a
    client can send the grid's own query string unchanged)."""
    direction = None
    if order in DIRECTIONS:
        direction = order
    elif order:
        if order not in ORDERS:
            raise ValidationError(
                f"unknown order {order!r}; expected asc, desc or one of {', '.join(ORDERS)}")
        direction = "desc" if order.startswith("-") else "asc"
        if sort is None:
            sort = order.lstrip("-")
    sort = sort or "created"
    direction = direction or "desc"
    if sort not in SORTS:
        raise ValidationError(
            f"date buckets need a date sort: {', '.join(SORTS)} (not {sort!r})")
    item_order = ("-" if direction == "desc" else "") + sort
    if item_order not in ORDERS:  # pragma: no cover - every combination is in ORDERS
        raise ValidationError(f"GET /api/items has no order {item_order!r}")
    return sort, direction, item_order


def _next_month(month: datetime) -> datetime:
    return month.replace(year=month.year + 1, month=1) if month.month == 12 \
        else month.replace(month=month.month + 1)


def buckets(session: Session, filters: list, sort: str | None = None,
            order: str | None = None) -> dict:
    """{sort, order, item_order, total, buckets: [{key, count, cursor}]}.
    key is "YYYY-MM"; items without a date (none today: every sort column
    falls back to the date added) would come last as key None, cursor None."""
    sort, direction, item_order = resolve(sort, order)
    col = SORTS[sort]
    month = sa.func.date_trunc("month", sa.func.timezone("UTC", col)).label("month")
    ordered = month.desc().nulls_last() if direction == "desc" else month.asc().nulls_last()
    rows = session.execute(
        sa.select(month, sa.func.count().label("count"))
        .select_from(Item)
        .where(*filters)
        .group_by(month)
        .order_by(ordered)
    ).all()
    out = []
    for row in rows:
        if row.month is None:
            out.append({"key": None, "count": row.count, "cursor": None})
            continue
        start = row.month.replace(tzinfo=UTC)
        boundary = _next_month(start) if direction == "desc" else start
        out.append({"key": start.strftime("%Y-%m"), "count": row.count,
                    "cursor": encode_cursor(boundary, "")})
    return {"sort": sort, "order": direction, "item_order": item_order,
            "total": sum(b["count"] for b in out), "buckets": out}
