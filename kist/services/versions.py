"""Versions (Milestone 3): an item's chain of version_of links, oldest
first, and uploading a new version.

A version_of link points from the newer item to the one it replaces
(new -> old, services/links.py). The chain is every item reachable through
version_of links in either direction; it's ordered by depth (hops to the
oldest), then created_at. The newest is "current". A new upload is linked
to the current head, so the chain stays a line, and inherits the head's
folders and tags so it shows where the old one did. The chain is stacked
with the new version as the cover (on_new_version), so the grid shows one
picture, the current one, rather than every version loose.
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
    """Stack the chain with the new version as its cover, so a collapsed
    grid shows the current version: it joins the stack the version it
    replaces (or any other version) is in, else the chain becomes a new
    stack. Stacking is an item event in the upload's batch, so it undoes
    (and Unstack takes it apart)."""
    from . import stacks

    old = session.get(Item, old_id)
    if old is None:
        return
    members = chain(session, new_id)
    stacked = [i.stack_id for i in [old, *reversed(members)] if i.stack_id]
    if stacked:
        stacks.update(session, stacked[0], add=[new_id], cover=new_id)
        return
    ids = [i.id for i in members if not i.deleted_at]
    if new_id in ids and len(ids) >= 2:
        stacks.create(session, ids, cover=new_id)


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
