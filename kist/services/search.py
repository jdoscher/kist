"""Cursor-paginated item queries (keyset pagination, stable under inserts)."""

import base64
import json
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..models import Item, ItemFolder, ItemTag, Tag

# order key -> (column, direction) ; id is always the tiebreaker
ORDERS: dict[str, tuple[Any, bool]] = {
    "-created": (Item.created_at, True),
    "created": (Item.created_at, False),
    "filename": (Item.filename, False),
    "-size": (Item.size_bytes, True),
    "-rating": (Item.rating, True),
    "-modified": (Item.modified_at, True),
}

KIND_FILTERS = {
    "image": sa.and_(Item.mime.like("image/%")),
    "video": Item.mime.like("video/%"),
    "pdf": Item.mime == "application/pdf",
    "other": sa.and_(
        sa.not_(Item.mime.like("image/%")),
        sa.not_(Item.mime.like("video/%")),
        Item.mime != "application/pdf",
    ),
}


def encode_cursor(value: Any, item_id: str) -> str:
    if isinstance(value, datetime):
        value = {"$dt": value.isoformat()}
    raw = json.dumps([value, item_id]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[Any, str]:
    pad = "=" * (-len(cursor) % 4)
    value, item_id = json.loads(base64.urlsafe_b64decode(cursor + pad))
    if isinstance(value, dict) and "$dt" in value:
        value = datetime.fromisoformat(value["$dt"])
    return value, item_id


def build_filters(
    *,
    q: str | None = None,
    tags: list[str] | None = None,
    tags_any: list[str] | None = None,
    untagged: bool = False,
    folder: str | None = None,
    kind: str | None = None,
    rating_min: int | None = None,
    deleted: bool = False,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> list[Any]:
    filters: list[Any] = []
    filters.append(Item.deleted_at.isnot(None) if deleted else Item.deleted_at.is_(None))
    if q:
        pattern = f"%{q}%"
        filters.append(
            sa.or_(
                Item.filename.ilike(pattern),
                sa.func.similarity(Item.filename, q) > 0.25,
                Item.notes.ilike(pattern),
            )
        )
    if tags:
        for name in tags:  # AND semantics: item must carry every tag
            filters.append(
                sa.exists().where(
                    ItemTag.item_id == Item.id,
                    ItemTag.tag_id == Tag.id,
                    Tag.name == name,
                )
            )
    if tags_any:  # OR semantics: item carries at least one of these
        filters.append(
            sa.exists().where(
                ItemTag.item_id == Item.id,
                ItemTag.tag_id == Tag.id,
                Tag.name.in_(tags_any),
            )
        )
    if untagged:
        filters.append(sa.not_(sa.exists().where(ItemTag.item_id == Item.id)))
    if folder == "none":
        filters.append(
            sa.not_(sa.exists().where(ItemFolder.item_id == Item.id))
        )
    elif folder:
        filters.append(
            sa.exists().where(
                ItemFolder.item_id == Item.id, ItemFolder.folder_id == folder
            )
        )
    if kind and kind in KIND_FILTERS:
        filters.append(KIND_FILTERS[kind])
    if rating_min:
        filters.append(Item.rating >= rating_min)
    if created_after:
        filters.append(Item.created_at >= created_after)
    if created_before:
        filters.append(Item.created_at <= created_before)
    return filters


def search_items(
    session: Session,
    *,
    q: str | None = None,
    tags: list[str] | None = None,
    tags_any: list[str] | None = None,
    untagged: bool = False,
    folder: str | None = None,
    kind: str | None = None,
    rating_min: int | None = None,
    deleted: bool = False,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    order: str = "-created",
    limit: int = 100,
    cursor: str | None = None,
) -> tuple[list[Item], str | None, int]:
    filters = build_filters(
        q=q, tags=tags, tags_any=tags_any, untagged=untagged,
        folder=folder, kind=kind,
        rating_min=rating_min, deleted=deleted,
        created_after=created_after, created_before=created_before,
    )
    col, desc = ORDERS.get(order, ORDERS["-created"])

    total = session.execute(
        sa.select(sa.func.count()).select_from(Item).where(*filters)
    ).scalar_one()

    stmt = sa.select(Item).where(*filters)
    if desc:
        stmt = stmt.order_by(col.desc(), Item.id.desc())
    else:
        stmt = stmt.order_by(col.asc(), Item.id.asc())

    if cursor:
        value, last_id = decode_cursor(cursor)
        if desc:
            stmt = stmt.where(
                sa.or_(col < value, sa.and_(col == value, Item.id < last_id))
            )
        else:
            stmt = stmt.where(
                sa.or_(col > value, sa.and_(col == value, Item.id > last_id))
            )

    rows = list(session.execute(stmt.limit(limit + 1)).scalars())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor(getattr(last, col.key), last.id)
    return rows, next_cursor, total
