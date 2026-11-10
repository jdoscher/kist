"""Duplicate groups and resolving them (keep one, merge the rest into it).

Kinds:
- exact: the same bytes (sha256). Ingest already refuses a second copy
  (sha256 is unique), so this only finds anything in a library whose
  index was assembled some other way; it is kept for completeness.
- same_image: perceptual hashes within SAME_IMAGE_DISTANCE bits and aspect
  ratios within ASPECT_TOLERANCE (a resize or re-encode of one picture).
- similar: image embeddings with cosine >= SIMILAR_COSINE (a near-identical
  shot, a crop, a re-edit).

Pairs linked not_duplicate, and items in the same stack, are never put
together. Groups are the connected components of the remaining pairs, so a
group can hold more than two items. Each group carries a suggested keeper:
the largest picture, then the oldest, then one with a source URL.

Resolving runs in the caller's batch (one request, one MCP call), so
POST /api/batches/{id}/undo puts everything back.
"""

import hashlib

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import BIT
from sqlalchemy.orm import Session, aliased

from ..models import Item, ItemEmbedding, ItemLink
from . import events, validation

KINDS = ("exact", "same_image", "similar")
SAME_IMAGE_DISTANCE = 4
ASPECT_TOLERANCE = 0.03
SIMILAR_COSINE = 0.92
MAX_GROUPS = 500
BLOCK = 2048


def _not_duplicate_pairs(session: Session) -> set[frozenset]:
    return {
        frozenset((a, b)) for a, b in session.execute(
            sa.select(ItemLink.src_id, ItemLink.dst_id).where(ItemLink.kind == "not_duplicate")
        ).all()
    }


def _exact_pairs(session: Session) -> list[tuple[str, str, float]]:
    rows = session.execute(
        sa.select(Item.sha256, sa.func.array_agg(Item.id))
        .group_by(Item.sha256).having(sa.func.count() > 1)
    ).all()
    out = []
    for _, ids in rows:
        ids = sorted(ids)
        out += [(ids[0], other, 0.0) for other in ids[1:]]
    return out


def _same_image_pairs(session: Session) -> list[tuple[str, str, float]]:
    a, b = aliased(Item, name="a"), aliased(Item, name="b")
    distance = sa.func.bit_count(sa.cast(a.phash.op("#")(b.phash), BIT(64)))
    aspect_a = sa.cast(a.width, sa.Float) / sa.func.nullif(a.height, 0)
    aspect_b = sa.cast(b.width, sa.Float) / sa.func.nullif(b.height, 0)
    rows = session.execute(
        sa.select(a.id, b.id, distance)
        .where(
            a.id < b.id,
            a.phash.isnot(None), b.phash.isnot(None),
            a.deleted_at.is_(None), b.deleted_at.is_(None),
            distance <= SAME_IMAGE_DISTANCE,
            sa.or_(
                a.width.is_(None), b.width.is_(None), a.height.is_(None), b.height.is_(None),
                sa.func.abs(aspect_a - aspect_b) <= ASPECT_TOLERANCE * aspect_a,
            ),
        )
    ).all()
    return [(x, y, float(d)) for x, y, d in rows]


def _similar_pairs(session: Session, threshold: float = SIMILAR_COSINE) -> list[tuple]:
    """Every pair of active items whose embeddings are this close, by
    blockwise matrix products (numpy; a 20k-item library is a few seconds)."""
    import numpy as np

    from . import ai

    model = ai.active_model()
    rows = session.execute(
        sa.select(ItemEmbedding.item_id, ItemEmbedding.vector)
        .join(Item, Item.id == ItemEmbedding.item_id)
        .where(ItemEmbedding.model == model, Item.deleted_at.is_(None))
        .order_by(ItemEmbedding.item_id)
    ).all()
    if len(rows) < 2:
        return []
    ids = [r[0] for r in rows]
    x = np.asarray([r[1] for r in rows], dtype=np.float32)
    x /= np.linalg.norm(x, axis=1, keepdims=True) + 1e-12
    out = []
    for start in range(0, len(ids), BLOCK):
        sims = x[start:start + BLOCK] @ x.T
        ii, jj = np.nonzero(sims >= threshold)
        for i, j in zip(ii.tolist(), jj.tolist(), strict=True):
            gi = start + i
            if gi < j:
                out.append((ids[gi], ids[j], round(float(sims[i, j]), 4)))
    return out


def _keeper_order(item: Item):
    pixels = (item.width or 0) * (item.height or 0)
    return (-pixels, item.created_at, 0 if item.source_url else 1, item.id)


def groups(session: Session, kind: str, limit: int = 100) -> list[dict]:
    """Duplicate groups the caller can see: [{id, kind, items, pairs,
    suggested_keep}], biggest first."""
    from ..schemas import ItemOut
    from . import items as items_svc

    if kind not in KINDS:
        raise validation.ValidationError(f"unknown kind {kind!r}; expected {', '.join(KINDS)}")
    pairs = {"exact": _exact_pairs, "same_image": _same_image_pairs,
             "similar": _similar_pairs}[kind](session)
    apart = _not_duplicate_pairs(session)
    stack_of = dict(session.execute(
        sa.select(Item.id, Item.stack_id).where(Item.stack_id.isnot(None))
    ).all())
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    kept = []
    for a, b, score in pairs:
        if frozenset((a, b)) in apart:
            continue
        if stack_of.get(a) and stack_of.get(a) == stack_of.get(b):
            continue
        kept.append((a, b, score))
        parent[find(a)] = find(b)
    members: dict[str, list[str]] = {}
    for x in parent:
        members.setdefault(find(x), []).append(x)
    out = []
    for ids in members.values():
        items = [i for i in (session.get(Item, x) for x in ids) if i is not None
                 and items_svc.visible(i)]
        if len(items) < 2:
            continue
        seen = {i.id for i in items}
        items.sort(key=_keeper_order)
        gid = hashlib.sha1(",".join(sorted(seen)).encode()).hexdigest()[:16]
        out.append({
            "id": f"{kind}-{gid}",
            "kind": kind,
            "items": [ItemOut.from_item(i) for i in items],
            "pairs": [{"a": a, "b": b, "score": s} for a, b, s in kept
                      if a in seen and b in seen],
            "suggested_keep": items[0].id,
        })
    out.sort(key=lambda g: (-len(g["items"]), g["id"]))
    return out[: max(1, min(limit, MAX_GROUPS))]


MERGE_DEFAULTS = {"tags": True, "folders": True, "notes": True, "rating": "max",
                  "source_url": True}


def resolve(session: Session, keep: str, others: list[str], merge: dict | None = None) -> dict:
    """Merge the others' metadata into `keep`, then trash the others: tags
    and folders are joined, notes appended, the highest rating kept and a
    missing source URL filled in (each part can be turned off in `merge`).
    All of it lands in the caller's batch, so it undoes as one."""
    from ..lifecycle import TransitionError
    from . import items as items_svc

    opts = {**MERGE_DEFAULTS, **(merge or {})}
    if opts["rating"] not in ("max", "keep"):
        raise validation.ValidationError("merge.rating must be max or keep")
    others = [o for o in dict.fromkeys(others or []) if o != keep]
    if not others:
        raise validation.ValidationError("name at least one other item to merge into keep")
    keeper = items_svc.get_item(session, keep)
    if keeper.deleted_at is not None:
        raise TransitionError(f"item {keep} is in the trash; restore it before keeping it")
    rest = [items_svc.get_item(session, o) for o in others]
    for o in rest:  # check everything before changing anything
        if o.protected and o.deleted_at is None:
            raise items_svc.Protected(o.id)

    if opts["tags"]:
        add = sorted({t.name for o in rest for t in o.tags}
                     - {t.name for t in keeper.tags}, key=str.lower)
        if add:
            items_svc.set_tags(session, keep, add=add, resolve=False)
    if opts["folders"]:
        add = sorted({f.id for o in rest for f in o.folders} - {f.id for f in keeper.folders})
        if add:
            items_svc.set_folders(session, keep, add=add)
    fields: dict = {}
    if opts["notes"]:
        notes = [keeper.notes.strip()] if keeper.notes.strip() else []
        for o in rest:
            n = (o.notes or "").strip()
            if n and n not in notes:
                notes.append(n)
        joined = "\n\n".join(notes)
        if joined != (keeper.notes or ""):
            fields["notes"] = joined[: validation.NOTES_MAX]
    if opts["rating"] == "max":
        best = max([keeper.rating] + [o.rating for o in rest])
        if best != keeper.rating:
            fields["rating"] = best
    if opts["source_url"] and not keeper.source_url:
        url = next((o.source_url for o in rest if o.source_url), None)
        if url:
            fields["source_url"] = url
    if fields:
        items_svc.update_item(session, keep, fields=fields)
    trashed = []
    for o in rest:
        if o.deleted_at is None:
            items_svc.soft_delete(session, o.id)
            trashed.append(o.id)
    return {"keep": items_svc.get_item(session, keep), "trashed": trashed,
            "batch_id": events.context().batch_id}
