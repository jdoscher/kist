"""Versions (Milestone 3): an item's chain of version_of links, oldest
first, and uploading a new version.

A version_of link points from the newer item to the one it replaces
(new -> old, services/links.py). The chain is every item reachable through
version_of links in either direction; it's ordered by depth (hops to the
oldest), then created_at. The newest is "current". A new upload is linked
to the current head, so the chain stays a line, and inherits the head's
folders and tags so it shows where the old one did.
"""

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..models import Item, ItemLink
from . import items as items_svc
from . import links as links_svc

KIND = "version_of"
CHAIN_MAX = 200


def chain(session: Session, item_id: str) -> list[Item]:
    items_svc.get_item(session, item_id)
    seen = {item_id}
    frontier = [item_id]
    edges: list[tuple[str, str]] = []
    while frontier and len(seen) < CHAIN_MAX:
        rows = session.execute(
            sa.select(ItemLink.src_id, ItemLink.dst_id).where(
                ItemLink.kind == KIND,
                sa.or_(ItemLink.src_id.in_(frontier), ItemLink.dst_id.in_(frontier)),
            )
        ).all()
        frontier = []
        for src, dst in rows:
            edges.append((src, dst))
            for x in (src, dst):
                if x not in seen:
                    seen.add(x)
                    frontier.append(x)
    older = {}
    for src, dst in edges:
        older.setdefault(src, set()).add(dst)

    def depth(i: str, stack=()) -> int:
        if i in stack:
            return 0
        return max((1 + depth(o, (*stack, i)) for o in older.get(i, ())), default=0)

    items = session.execute(sa.select(Item).where(Item.id.in_(seen))).scalars().all()
    items = [i for i in items if items_svc.visible(i)]
    return sorted(items, key=lambda i: (depth(i.id), i.created_at, i.id))


def on_new_version(session: Session, old_id: str, new_id: str) -> None:
    """Hook: make the new version the stack cover once M2 stacks exist."""


def add_version(session: Session, item_id: str, staged, filename: str):
    """Ingest `staged` as a new version of the chain `item_id` is in."""
    from . import ingest

    current = chain(session, item_id)
    head = current[-1] if current else items_svc.get_item(session, item_id)
    result = ingest.ingest_file(
        session, staged, filename,
        folder_ids=[f.id for f in head.folders], tag_names=[t.name for t in head.tags],
    )
    new = result.item
    if new.id != head.id and new.id not in {i.id for i in current}:
        links_svc.link(session, new.id, head.id, KIND)
        on_new_version(session, head.id, new.id)
    return result
