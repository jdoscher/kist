"""Cursor-paginated item queries (keyset pagination, stable under inserts)."""

import base64
import json
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .. import access
from ..filetypes import KINDS, OCTET_STREAM, VIDEO_EXTS
from ..lifecycle import ItemState, PreviewStatus
from ..models import Folder, Item, ItemFolder, ItemTag, Tag
from .validation import ValidationError


class BadCursor(ValueError):
    """A cursor this server didn't make (HTTP 400)."""

# Named views. "unfiled" is the inbox: active items in no folder and with no
# tags; filing or tagging an item takes it out. The web UI and MCP both use
# this one definition.
VIEWS = ("unfiled",)

# order key -> (column, direction) ; id is always the tiebreaker
ORDERS: dict[str, tuple[Any, bool]] = {
    "-created": (Item.created_at, True),
    "created": (Item.created_at, False),
    "filename": (Item.filename, False),
    "-size": (Item.size_bytes, True),
    "-rating": (Item.rating, True),
    "-modified": (Item.modified_at, True),
}

# filetypes.kind_for in SQL: the type filter, the stats and ItemOut.kind
# all agree (tests/test_unit.py checks the two against each other)
_PDF = Item.mime == "application/pdf"
_VIDEO = sa.or_(
    Item.mime.like("video/%"),
    sa.and_(Item.mime == OCTET_STREAM, sa.func.lower(Item.ext).in_(sorted(VIDEO_EXTS))),
)
_IMAGE = Item.mime.like("image/%")
KIND_FILTERS = {
    "pdf": _PDF,
    "video": _VIDEO,
    "image": _IMAGE,
    "other": sa.and_(sa.not_(_PDF), sa.not_(_VIDEO), sa.not_(_IMAGE)),
}


def encode_cursor(value: Any, item_id: str) -> str:
    if isinstance(value, datetime):
        value = {"$dt": value.isoformat()}
    raw = json.dumps([value, item_id]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[Any, str]:
    try:
        pad = "=" * (-len(cursor) % 4)
        value, item_id = json.loads(base64.urlsafe_b64decode(cursor + pad))
        if isinstance(value, dict) and "$dt" in value:
            value = datetime.fromisoformat(value["$dt"])
        if not isinstance(item_id, str) or isinstance(value, (dict, list)):
            raise TypeError("not a cursor")
    except (ValueError, TypeError) as exc:
        raise BadCursor("invalid cursor; start again without one") from exc
    return value, item_id


def folder_subtree(folder_id: str):
    """Recursive CTE of a folder's id plus every descendant folder id."""
    tree = (
        sa.select(Folder.id)
        .where(Folder.id == folder_id)
        .cte(name="folder_subtree", recursive=True)
    )
    return tree.union(sa.select(Folder.id).where(Folder.parent_id == tree.c.id))


STACK_MODES = ("collapse",)


def tag_condition(names: list[str]):
    """An item carries one of these tags, where a tag also matches through
    the vocabulary (services/vocabulary.py): an alias finds its tag, and a
    tag finds every item tagged with a tag that implies it, at any depth
    (#shoes finds boots). "ns:*" matches any tag in that namespace. The
    expansion happens here, at query time; nothing is written to items."""
    from .vocabulary import is_wildcard, matching_names

    conds = []
    for name in names:
        ns = is_wildcard(name)
        if ns is not None:
            escaped = ns.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conds.append(Tag.name.ilike(f"{escaped}:%", escape="\\"))
        else:
            conds.append(Tag.name.in_(matching_names(name)))
    return sa.exists().where(ItemTag.item_id == Item.id, ItemTag.tag_id == Tag.id, sa.or_(*conds))


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
    state: str | None = None,
    view: str | None = None,
    preview_status: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    fixity: str | None = None,
    stacks: str | None = None,
    person: str | None = None,
) -> list[Any]:
    filters: list[Any] = []
    if fixity is not None:
        if fixity != "failed":
            raise ValidationError(f"unknown fixity {fixity!r}; expected failed")
        filters.append(Item.fixity_failed_at.isnot(None))
    if view is not None and view not in VIEWS:
        raise ValidationError(f"unknown view {view!r}; expected one of {', '.join(VIEWS)}")
    if state is not None and state not in tuple(ItemState):
        raise ValidationError(f"unknown state {state!r}; expected active or trashed")
    trashed = (state == ItemState.TRASHED) if state is not None else deleted
    if preview_status is not None:
        if preview_status not in tuple(PreviewStatus):
            raise ValidationError(
                f"unknown preview_status {preview_status!r}; "
                "expected pending, ready, failed or none"
            )
        filters.append(Item.preview_status == preview_status)
    if view == "unfiled":
        trashed = False
        untagged = True
        folder = "none"
    filters.append(Item.deleted_at.isnot(None) if trashed else Item.deleted_at.is_(None))
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
            filters.append(tag_condition([name]))
    if tags_any:  # OR semantics: item carries at least one of these
        filters.append(tag_condition(tags_any))
    if untagged:
        filters.append(sa.not_(sa.exists().where(ItemTag.item_id == Item.id)))
    if folder == "none":
        filters.append(
            sa.not_(sa.exists().where(ItemFolder.item_id == Item.id))
        )
    elif folder:
        # a folder includes everything filed in its subfolders, at any depth
        filters.append(
            sa.exists().where(
                ItemFolder.item_id == Item.id,
                ItemFolder.folder_id.in_(sa.select(folder_subtree(folder).c.id)),
            )
        )
    if kind:
        if kind not in KIND_FILTERS:
            raise ValidationError(f"unknown type {kind!r}; expected one of {', '.join(KINDS)}")
        filters.append(KIND_FILTERS[kind])
    if rating_min:
        filters.append(Item.rating >= rating_min)
    if created_after:
        filters.append(Item.created_at >= created_after)
    if created_before:
        filters.append(Item.created_at <= created_before)
    if person:
        from ..models import Face

        filters.append(sa.exists().where(Face.item_id == Item.id, Face.person_id == person))
    if stacks is not None:
        if stacks not in STACK_MODES:
            raise ValidationError(f"unknown stacks {stacks!r}; expected collapse")
        if not trashed:  # the trash lists every trashed item on its own
            from .stacks import representative_filter

            filters.append(representative_filter())
    reader = access.restricted()
    if reader is not None:
        if folder and folder != "none" and folder not in reader.visible_folders:
            filters.append(sa.false())  # not even which items are filed there
        # a folder-limited token sees only live items filed in its folders
        filters.append(Item.deleted_at.is_(None))
        filters.append(
            sa.exists().where(
                ItemFolder.item_id == Item.id,
                ItemFolder.folder_id.in_(sorted(reader.visible_folders) or [""]),
            )
        )
    return filters


SEMANTIC_CURSOR = "~semantic"


def _semantic_page(session, filters, vec, model, limit, cursor):
    from ..models import HalfVec, ItemEmbedding

    offset = 0
    if cursor:
        value, marker = decode_cursor(cursor)
        if marker != SEMANTIC_CURSOR or not isinstance(value, int) or value < 0:
            raise BadCursor("invalid cursor for a semantic search; start again without one")
        offset = value
    dim = len(vec)
    # the same expression and predicate as the model's partial HNSW index
    distance = sa.cast(ItemEmbedding.vector, HalfVec(dim)).op("<=>", return_type=sa.Float)(
        sa.cast(sa.bindparam("semantic_q", vec, type_=HalfVec()), HalfVec(dim))
    )
    stmt = (
        sa.select(Item)
        .outerjoin(ItemEmbedding, sa.and_(ItemEmbedding.item_id == Item.id,
                                          ItemEmbedding.model == model))
        .where(*filters)
        .order_by(distance.asc().nulls_last(), Item.id.asc())
        .offset(offset)
        .limit(limit + 1)
    )
    rows = list(session.execute(stmt).scalars())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(offset + limit, SEMANTIC_CURSOR)
    return rows, next_cursor


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
    state: str | None = None,
    view: str | None = None,
    preview_status: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    fixity: str | None = None,
    stacks: str | None = None,
    person: str | None = None,
    order: str = "-created",
    limit: int = 100,
    cursor: str | None = None,
    semantic: list[float] | None = None,
    semantic_model: str | None = None,
) -> tuple[list[Item], str | None, int | None]:
    """total is counted on the first page only (no cursor); later pages
    return None.

    semantic: a query vector (services/ai.query_vector). Items are then
    ordered by cosine distance to it, nearest first, items without an
    embedding for `semantic_model` last; `order` is ignored and the cursor
    is an offset (still opaque to the client, and only valid for a semantic
    search). M2 builds the real hybrid search on top of this."""
    filters = build_filters(
        q=q, tags=tags, tags_any=tags_any, untagged=untagged,
        folder=folder, kind=kind,
        rating_min=rating_min, deleted=deleted, state=state, view=view,
        preview_status=preview_status,
        created_after=created_after, created_before=created_before, fixity=fixity,
        stacks=stacks, person=person,
    )
    if order not in ORDERS:
        raise ValidationError(f"unknown order {order!r}; expected one of {', '.join(ORDERS)}")
    col, desc = ORDERS[order]

    # counting re-scans every match: only the first page pays for it (the
    # client keeps that total and adjusts it as items change)
    total = None
    if not cursor:
        total = session.execute(
            sa.select(sa.func.count()).select_from(Item).where(*filters)
        ).scalar_one()

    if semantic is not None and semantic_model:
        return _semantic_page(session, filters, semantic, semantic_model, limit, cursor) + (
            total,
        )

    stmt = sa.select(Item).where(*filters)
    if desc:
        stmt = stmt.order_by(col.desc(), Item.id.desc())
    else:
        stmt = stmt.order_by(col.asc(), Item.id.asc())

    if cursor:
        value, last_id = decode_cursor(cursor)
        if last_id == SEMANTIC_CURSOR:
            raise BadCursor("that cursor belongs to a semantic search; start again without one")
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
