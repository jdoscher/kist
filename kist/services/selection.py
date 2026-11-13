"""Which items a folder / smart folder / board / item list / the whole
library covers: SQL conditions shared by share links and exports. Only
live items are ever included."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..models import Folder, Item, ItemFolder, SmartFolder
from . import validation

KINDS = ("folder", "smart", "board", "items", "all")


def boards_service():
    """M3's boards service, if this build has it."""
    try:
        from . import boards  # type: ignore[attr-defined]
    except ImportError:
        return None
    return boards if hasattr(boards, "share_item_ids") else None


def _dt(value):
    if not value:
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def check(session: Session, kind: str, target_id: str | None, item_ids: list[str] | None,
          *, max_items: int = 100_000, allow_all: bool = True):
    """Validate a target. Returns (target_id, item_ids) cleaned."""
    if kind not in KINDS or (kind == "all" and not allow_all):
        allowed = [k for k in KINDS if allow_all or k != "all"]
        raise validation.ValidationError(f"target must be one of {', '.join(allowed)}")
    if kind == "all":
        return None, None
    if kind == "items":
        ids = list(dict.fromkeys(str(i) for i in item_ids or []))
        if not ids:
            raise validation.ValidationError("an items target needs item_ids")
        if len(ids) > max_items:
            raise validation.ValidationError(f"at most {max_items} items")
        found = set(session.execute(
            sa.select(Item.id).where(Item.id.in_(ids), Item.deleted_at.is_(None))
        ).scalars())
        missing = [i for i in ids if i not in found]
        if missing:
            raise validation.ValidationError(f"unknown or trashed items: {', '.join(missing[:5])}")
        return None, ids
    if not target_id:
        raise validation.ValidationError(f"a {kind} target needs target_id")
    if kind == "folder" and session.get(Folder, target_id) is None:
        raise validation.UnknownFolder(target_id)
    if kind == "smart" and session.get(SmartFolder, target_id) is None:
        raise validation.ValidationError(f"Unknown smart folder: {target_id}")
    if kind == "board":
        boards = boards_service()
        if boards is None:
            raise validation.ValidationError("boards aren't available in this build")
        try:
            boards.share_item_ids(session, target_id)
        except boards.NotFound:
            raise validation.ValidationError(f"Unknown board: {target_id}") from None
    return target_id, None


def filters(session: Session, kind: str, target_id: str | None,
            item_ids: list[str] | None) -> list:
    from . import search

    live = Item.deleted_at.is_(None)
    if kind == "all":
        return [live]
    if kind == "items":
        return [live, Item.id.in_(list(item_ids or []) or [""])]
    if kind == "folder":
        if session.get(Folder, target_id) is None:
            return [sa.false()]
        return [live, sa.exists().where(
            ItemFolder.item_id == Item.id,
            ItemFolder.folder_id.in_(sa.select(search.folder_subtree(target_id).c.id)),
        )]
    if kind == "smart":
        smart = session.get(SmartFolder, target_id)
        if smart is None:
            return [sa.false()]
        return smart_filters(dict(smart.query or {}))
    if kind == "board":
        return [live, Item.id.in_(board_ids(session, target_id) or [""])]
    return [sa.false()]


MEDIA_KEYS = ("taken_after", "taken_before", "has_gps", "camera", "lens", "duration_min",
              "duration_max", "vertical", "has_audio", "place", "date_after", "date_before",
              "bbox")


def smart_filters(q: dict) -> list:
    """A saved smart-folder query as SQL conditions, through the same
    search.build_filters every listing uses (so a share or export of a smart
    folder holds what the smart folder shows). Ordering signals (semantic,
    color) don't narrow a share; text= does (items whose OCR/PDF text
    matches)."""
    from . import ranking, search

    out = search.build_filters(
        q=q.get("q") or None, tags=q.get("tags") or None,
        tags_any=q.get("tags_any") or None, untagged=bool(q.get("untagged")),
        folder=q.get("folder") or None, kind=q.get("type") or None,
        rating_min=q.get("rating") or None, view=q.get("view") or None,
        preview_status=q.get("preview") or q.get("preview_status") or None,
        created_after=_dt(q.get("created_after")),
        created_before=_dt(q.get("created_before")),
        state="active",  # never the trash, whatever the smart folder says
        stacks=q.get("stacks") or None, person=q.get("person") or None,
        flag=q.get("flag") or None, label=q.get("label") or None,
        ai_generated=q.get("ai_generated"), license=q.get("license") or None,
        source_status=q.get("source_status") or None,
        auto_kind=q.get("auto_kind") or None,  # F1
        media={k: (_dt(q[k]) if k.startswith(("taken_", "date_")) else q[k])
               for k in MEDIA_KEYS if q.get(k) is not None},
        credits=_credits_spec(q),  # F4
        # F6: a project and custom field filters
        project=q.get("project") or None,
        custom_fields=q.get("custom_fields") or None,
    )
    if q.get("has_text") is not None:
        out.append(ranking.has_text_filter(bool(q["has_text"])))
    tsq = ranking.tsquery(q["text"]) if q.get("text") else None
    if tsq:
        out.append(ranking.text_match(tsq, ranking.READ_SOURCES))
    return out


def _credits_spec(q: dict) -> dict | None:
    from .credits import from_params

    return from_params(q)


def board_ids(session: Session, board_id: str | None) -> list[str]:
    boards = boards_service()
    if boards is None or not board_id:
        return []
    try:
        return list(boards.share_item_ids(session, board_id))
    except Exception:
        return []


def items(session: Session, kind: str, target_id: str | None, item_ids: list[str] | None,
          *, limit: int | None = None, offset: int = 0) -> list[Item]:
    """The items, in the target's own order (a hand-picked list or a board
    keeps its order; anything else is newest first)."""
    stmt = sa.select(Item).where(*filters(session, kind, target_id, item_ids))
    if kind in ("items", "board"):
        order = list(item_ids or []) if kind == "items" else board_ids(session, target_id)
        rows = {i.id: i for i in session.execute(stmt).scalars()}
        ordered = [rows[i] for i in order if i in rows]
        return ordered[offset:offset + limit if limit else None]
    stmt = stmt.order_by(Item.created_at.desc(), Item.id.desc()).offset(offset)
    if limit:
        stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars())


def ids(session: Session, kind: str, target_id: str | None, item_ids: list[str] | None
        ) -> list[str]:
    if kind in ("items", "board"):
        return [i.id for i in items(session, kind, target_id, item_ids)]
    return list(session.execute(
        sa.select(Item.id).where(*filters(session, kind, target_id, item_ids))
        .order_by(Item.created_at.desc(), Item.id.desc())
    ).scalars())


def count(session: Session, kind: str, target_id: str | None, item_ids: list[str] | None) -> int:
    return session.execute(
        sa.select(sa.func.count()).select_from(Item)
        .where(*filters(session, kind, target_id, item_ids))
    ).scalar_one()
