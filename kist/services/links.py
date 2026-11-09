"""Typed links between items (derived_from, alternate, related,
not_duplicate, version_of).

A link has a direction: src -> dst ("this crop is derived_from that
screenshot"). Both items' meta.json list it ("out" on src, "in" on dst), so
rebuild-index can restore it from either side. The event is recorded on the
item the link was made from.
"""

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..models import Item, ItemLink
from . import events, validation
from . import items as items_svc
from . import meta as meta_svc

KINDS = ("derived_from", "alternate", "related", "not_duplicate", "version_of")


def check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise validation.ValidationError(
            f"unknown link kind {kind!r}; expected one of {', '.join(KINDS)}"
        )
    return kind


def _sync(session: Session, *item_ids: str) -> None:
    for item_id in item_ids:
        meta_svc.sync_item(session, item_id)


def link(session: Session, src_id: str, dst_id: str, kind: str) -> bool:
    """Link src -> dst. Returns False when the link already existed."""
    check_kind(kind)
    if src_id == dst_id:
        raise validation.ValidationError("an item can't be linked to itself")
    items_svc.get_item(session, src_id)
    items_svc.get_item(session, dst_id)
    exists = session.get(ItemLink, (src_id, dst_id, kind))
    if exists is not None:
        return False
    session.add(ItemLink(src_id=src_id, dst_id=dst_id, kind=kind))
    events.record(session, src_id, events.Kind.LINK, None,
                  {"link": {"item_id": dst_id, "kind": kind, "direction": "out"}})
    session.commit()
    _sync(session, src_id, dst_id)
    return True


def unlink(session: Session, src_id: str, dst_id: str, kind: str | None = None) -> int:
    """Remove src -> dst links (of one kind, or all). Returns how many."""
    if kind is not None:
        check_kind(kind)
    items_svc.get_item(session, src_id)
    stmt = sa.select(ItemLink).where(ItemLink.src_id == src_id, ItemLink.dst_id == dst_id)
    if kind is not None:
        stmt = stmt.where(ItemLink.kind == kind)
    rows = session.execute(stmt).scalars().all()
    for row in rows:
        events.record(session, src_id, events.Kind.UNLINK,
                      {"link": {"item_id": dst_id, "kind": row.kind, "direction": "out"}}, None)
        session.delete(row)
    if rows:
        session.commit()
        _sync(session, src_id, dst_id)
    return len(rows)


def links_of(session: Session, item_id: str) -> list[tuple[Item, str, str]]:
    """(other item, kind, direction) for every link touching the item,
    skipping items the caller can't see."""
    rows = session.execute(
        sa.select(ItemLink).where(sa.or_(ItemLink.src_id == item_id, ItemLink.dst_id == item_id))
        .order_by(ItemLink.created_at, ItemLink.kind)
    ).scalars().all()
    out = []
    for row in rows:
        direction = "out" if row.src_id == item_id else "in"
        other = session.get(Item, row.dst_id if direction == "out" else row.src_id)
        if other is not None and items_svc.visible(other):
            out.append((other, row.kind, direction))
    return out


def meta_links(item: Item) -> list[dict]:
    """The links block of an item's meta.json."""
    from sqlalchemy.orm import object_session

    session = object_session(item)
    if session is None:
        return []
    rows = session.execute(
        sa.select(ItemLink.src_id, ItemLink.dst_id, ItemLink.kind).where(
            sa.or_(ItemLink.src_id == item.id, ItemLink.dst_id == item.id)
        )
    ).all()
    return sorted(
        (
            {"item_id": d if s == item.id else s, "kind": k,
             "direction": "out" if s == item.id else "in"}
            for s, d, k in rows
        ),
        key=lambda x: (x["direction"], x["kind"], x["item_id"]),
    )


def restore_from_meta(session: Session, item_id: str, links: list) -> int:
    """rebuild-index: re-create links listed in meta.json whose other item
    exists. Returns how many were added."""
    added = 0
    for entry in links or []:
        try:
            other, kind, direction = entry["item_id"], entry["kind"], entry["direction"]
        except (KeyError, TypeError):
            continue
        if kind not in KINDS or other == item_id or session.get(Item, other) is None:
            continue
        src, dst = (item_id, other) if direction == "out" else (other, item_id)
        if session.get(ItemLink, (src, dst, kind)) is None:
            session.add(ItemLink(src_id=src, dst_id=dst, kind=kind))
            session.flush()
            added += 1
    return added
