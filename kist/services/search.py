"""Cursor-paginated item queries (keyset pagination, stable under inserts).

Ranked searches (meaning, more like this, colour, text in images, hybrid)
are built in services/ranking.py on top of the same filters; `search()`
picks one or the other.
"""

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .. import access
from ..filetypes import FONT_EXTS, FONT_MIMES, KINDS, OCTET_STREAM, VIDEO_EXTS
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
_FONT = sa.or_(
    Item.mime.in_(sorted(FONT_MIMES)),
    sa.and_(Item.mime == OCTET_STREAM, sa.func.lower(Item.ext).in_(sorted(FONT_EXTS))),
)
KIND_FILTERS = {
    "pdf": _PDF,
    "video": _VIDEO,
    "image": _IMAGE,
    "font": _FONT,
    "other": sa.and_(sa.not_(_PDF), sa.not_(_VIDEO), sa.not_(_IMAGE), sa.not_(_FONT)),
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
        # filename, notes, and text in the item (OCR, PDF text, caption)
        from .ranking import keyword_condition

        filters.append(keyword_condition(q))
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


SEMANTIC_CURSOR = "~semantic"  # M1's cursor marker (still accepted)


@dataclass
class SearchResult:
    items: list[Item]
    next_cursor: str | None
    total: int | None
    # item id -> {kind, score, snippet?} for ranked and keyword searches
    matches: dict[str, dict] = field(default_factory=dict)


def search(
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
    semantic: str | None = None,
    similar: str | None = None,
    image_vector: list[float] | None = None,
    color: str | None = None,
    color_tol: float | None = None,
    text: str | None = None,
    has_text: bool | None = None,
) -> SearchResult:
    """One page of items. total is counted on the first page only (no
    cursor); later pages return None.

    Without semantic / similar / image_vector / color / text this is the
    plain search: ordered by `order`, keyset cursor. With any of them it is
    ranked (services/ranking.py): `order` is ignored and the cursor is an
    offset (still opaque, and only valid for a ranked search).
    semantic=<words> needs the ml service once items have embeddings
    (ai.AiUnavailable otherwise); before then it is ignored."""
    from . import ranking

    sig = ranking.Signals()
    if semantic and semantic.strip() or similar or image_vector is not None:
        from . import ai

        sig.model = ai.active_model()
        if similar:
            sig.similar = ai.item_vector(session, similar, sig.model)
        elif image_vector is not None:
            sig.similar = image_vector
        if semantic and semantic.strip() and ai.has_embeddings(session):
            sig.meaning = ai.query_vector(semantic)  # AiUnavailable -> 503
    hybrid = bool(q and q.strip()) and (sig.meaning is not None or sig.similar is not None)
    if hybrid:
        sig.keyword = q.strip()  # type: ignore[union-attr]
        sig.snippet_q = ranking.tsquery(q)
    if color:
        sig.colour = ranking.ColourQuery.parse(color, color_tol)
    if text is not None:
        sig.text = ranking.tsquery(text)
        if sig.text is None:
            raise ValidationError("text needs at least one word to look for")

    if stacks is not None and stacks not in STACK_MODES:
        raise ValidationError(f"unknown stacks {stacks!r}; expected collapse")
    # a ranked search collapses stacks after ranking (ranking.ranked_page);
    # the plain one keeps each stack's cover as a filter
    collapse_ranked = stacks == "collapse" and sig.active
    filters = build_filters(
        q=None if hybrid else q, tags=tags, tags_any=tags_any, untagged=untagged,
        folder=folder, kind=kind,
        rating_min=rating_min, deleted=deleted, state=state, view=view,
        preview_status=preview_status,
        created_after=created_after, created_before=created_before, fixity=fixity,
        stacks=None if collapse_ranked else stacks, person=person,
    )
    if collapse_ranked:
        # the trash lists every trashed item on its own (as build_filters does)
        trashed = (state == ItemState.TRASHED) if state is not None else deleted
        collapse_ranked = view == "unfiled" or not trashed
    if has_text is not None:
        filters.append(ranking.has_text_filter(has_text))
    if similar:
        filters.append(Item.id != similar)  # more like it, not itself
    if order not in ORDERS:
        raise ValidationError(f"unknown order {order!r}; expected one of {', '.join(ORDERS)}")

    if sig.active:  # colour and text filter inside, and it counts the first page
        rows, next_cursor, matches, total = ranking.ranked_page(
            session, filters, sig, limit, cursor, collapse_stacks=collapse_ranked)
        return SearchResult(rows, next_cursor, total, matches)

    # counting re-scans every match: only the first page pays for it (the
    # client keeps that total and adjusts it as items change)
    total = None
    if not cursor:
        total = session.execute(
            sa.select(sa.func.count()).select_from(Item).where(*filters)
        ).scalar_one()

    rows, next_cursor = _keyset_page(session, filters, order, limit, cursor)
    matches = ranking.keyword_matches(session, rows, q.strip()) if q and q.strip() else {}
    return SearchResult(rows, next_cursor, total, matches)


def _keyset_page(session: Session, filters: list, order: str, limit: int,
                 cursor: str | None) -> tuple[list[Item], str | None]:
    from .ranking import RANKED_CURSOR

    col, desc = ORDERS[order]
    stmt = sa.select(Item).where(*filters)
    if desc:
        stmt = stmt.order_by(col.desc(), Item.id.desc())
    else:
        stmt = stmt.order_by(col.asc(), Item.id.asc())

    if cursor:
        value, last_id = decode_cursor(cursor)
        if last_id in (SEMANTIC_CURSOR, RANKED_CURSOR):
            raise BadCursor("that cursor belongs to a ranked search; start again without one")
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
    return rows, next_cursor


def search_items(session: Session, **kwargs) -> tuple[list[Item], str | None, int | None]:
    """search() as (items, next_cursor, total), for callers that don't show
    why an item matched."""
    result = search(session, **kwargs)
    return result.items, result.next_cursor, result.total
