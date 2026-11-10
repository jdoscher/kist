"""Timeline, On this day and the map (Milestone 5).

All three use the same filters as GET /api/items (search.build_filters, so a
folder-limited token only ever counts its own items), and the date an item
was taken (taken_sort: taken_at, else created_at). Dates are grouped in UTC;
EXIF times are stored as the camera's wall-clock time, so a photo taken at
23:30 local time stays on its own day.
"""

import math
from datetime import UTC, date, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, aggregate_order_by
from sqlalchemy.orm import Session

from ..models import Item
from .validation import ValidationError

GROUPS = {"day": "YYYY-MM-DD", "month": "YYYY-MM", "year": "YYYY"}
MAX_CLUSTERS = 5000


def _utc(col):
    return sa.func.timezone("UTC", col)


def buckets(session: Session, group: str, filters: list, limit: int = 5000) -> list[dict]:
    """[{key, count, first_item_id}] newest first; first_item_id is the
    newest item in the bucket (what the date scrubber jumps to)."""
    if group not in GROUPS:
        raise ValidationError("group must be day, month or year")
    key = sa.func.to_char(_utc(Item.taken_sort), GROUPS[group]).label("key")
    first = sa.type_coerce(
        sa.func.array_agg(aggregate_order_by(Item.id, Item.taken_sort.desc(), Item.id.desc())),
        ARRAY(sa.Text),
    )[1]
    rows = session.execute(
        sa.select(key, sa.func.count().label("count"), first.label("first"))
        .where(*filters)
        .group_by(key)
        .order_by(key.desc())
        .limit(limit)
    ).all()
    return [{"key": r.key, "count": r.count, "first_item_id": r.first} for r in rows]


def on_this_day(session: Session, filters: list, day: date | None = None,
                per_year: int = 12) -> dict:
    """Items taken on this month/day in earlier years, grouped by year."""
    day = day or datetime.now(UTC).date()
    local = _utc(Item.taken_sort)
    rows = session.execute(
        sa.select(Item)
        .where(
            *filters,
            sa.extract("month", local) == day.month,
            sa.extract("day", local) == day.day,
            sa.extract("year", local) < day.year,
        )
        .order_by(Item.taken_sort.desc(), Item.id.desc())
        .limit(per_year * 50)
    ).scalars().all()
    years: dict[int, list[Item]] = {}
    for item in rows:
        y = item.taken_sort.astimezone(UTC).year
        years.setdefault(y, [])
        if len(years[y]) < per_year:
            years[y].append(item)
    return {"date": day.isoformat(), "years": [
        {"year": y, "years_ago": day.year - y, "items": items}
        for y, items in sorted(years.items(), reverse=True)
    ]}


def parse_bbox(raw: str | None) -> tuple[float, float, float, float] | None:
    if not raw:
        return None
    try:
        w, s, e, n = (float(x) for x in raw.split(","))
    except ValueError:
        raise ValidationError("bbox must be west,south,east,north in degrees") from None
    if not (-180 <= w <= 180 and -180 <= e <= 180 and -90 <= s <= n <= 90):
        raise ValidationError("bbox is out of range (west,south,east,north)")
    return w, s, e, n


def cell_degrees(zoom: int) -> float:
    """Grid cell size: a quarter of a 256 px tile at this zoom (64 px)."""
    return 360.0 / (2 ** max(0, min(zoom, 22))) / 4


def clusters(session: Session, filters: list, bbox: tuple | None, zoom: int) -> dict:
    """Server-side grid clustering of items with GPS."""
    if not 0 <= zoom <= 22:
        raise ValidationError("zoom must be between 0 and 22")
    cell = cell_degrees(zoom)
    where = [*filters, Item.has_gps.is_(True), Item.lat.isnot(None), Item.lon.isnot(None)]
    if bbox:
        w, s, e, n = bbox
        where.append(Item.lat.between(s, n))
        if w <= e:
            where.append(Item.lon.between(w, e))
        else:  # crosses the antimeridian
            where.append(sa.or_(Item.lon >= w, Item.lon <= e))
    gx = sa.func.floor(Item.lon / cell).label("gx")
    gy = sa.func.floor(Item.lat / cell).label("gy")
    sample = sa.type_coerce(
        sa.func.array_agg(aggregate_order_by(Item.id, Item.taken_sort.desc(), Item.id.desc())),
        ARRAY(sa.Text),
    )[1]
    rows = session.execute(
        sa.select(
            gx, gy, sa.func.count().label("count"),
            sa.func.avg(Item.lat).label("lat"), sa.func.avg(Item.lon).label("lon"),
            sa.func.min(Item.lat).label("s"), sa.func.max(Item.lat).label("n"),
            sa.func.min(Item.lon).label("w"), sa.func.max(Item.lon).label("e"),
            sample.label("item_id"),
            # the place name most items in the cell share, and how many
            # different ones there are (the map list names groups by place)
            sa.func.mode().within_group(Item.place).label("place"),
            sa.func.count(sa.distinct(Item.place)).label("places"),
        )
        .where(*where)
        .group_by(gx, gy)
        .order_by(sa.func.count().desc())
        .limit(MAX_CLUSTERS)
    ).all()
    out = []
    total = 0
    for r in rows:
        total += r.count
        out.append({
            "lat": round(float(r.lat), 6), "lon": round(float(r.lon), 6), "count": r.count,
            "item_id": r.item_id,
            "place": r.place, "places": r.places,
            "bbox": [round(float(r.w), 6), round(float(r.s), 6),
                     round(float(r.e), 6), round(float(r.n), 6)],
        })
    return {"zoom": zoom, "cell_deg": cell, "total": total, "clusters": out,
            "truncated": len(rows) == MAX_CLUSTERS}


def zoom_for(bbox: tuple | None) -> int:
    if not bbox:
        return 2
    w, _s, e, _n = bbox
    span = (e - w) % 360 or 360
    return max(0, min(18, int(math.log2(360 / span)) + 2))
