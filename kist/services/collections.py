"""Suggested collections: groups of look-alike items ("You seem to collect…").

Clustering is numpy only: spherical k-means (k-means++ seeding, cosine) on
the active items' image embeddings, with k chosen by the mean silhouette
over a sample from a handful of candidate values. Clusters smaller than
MIN_SIZE, or loose ones (mean cosine to their centre below MIN_COHESION),
are dropped, and so are clusters that are already a folder (most of their
items filed in one folder).

Each cluster gets a label: the tag most of its items share, else the tag
whose text vector best matches the cluster's centre (zero-shot, the same
tag vectors and thresholds as tag suggestions) when at least half of the
members match it too. Bookkeeping tags (credit:, license: ...) are never
labels.

Results are cached in library/.kist/collections.json; the worker recomputes
them every KIST_COLLECTIONS_INTERVAL_HOURS, and ?refresh=1 does it now.
Accepting one makes a folder (or a tag plus a smart folder) holding its
items, in one batch.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Folder, Item, ItemEmbedding, ItemFolder, ItemTag, Tag
from . import events, validation
from . import meta as meta_svc

log = logging.getLogger("kist.collections")

FILE = "collections.json"
MIN_SIZE = 8
MIN_COHESION = 0.72
ALREADY_FILED = 0.8
TAG_SHARE = 0.4
LABEL_AGREEMENT = 0.5
LABEL_RANK = 3
SAMPLE = 2000
MAX_ITEMS = 50_000
SAMPLE_IDS = 6
SEED = 20261109
MAX_K = 80
N_INIT = 4
SELECT_SAMPLE = 5000


class NotFound(Exception):
    def __init__(self, cid: str):
        super().__init__(f"suggested collection {cid} not found (they are recomputed daily)")


# ---------- clustering (numpy) ----------


def _unit(x):
    import numpy as np

    return x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-12)


def kmeans(x, k: int, *, iters: int = 30, rng=None):
    """Spherical k-means with k-means++ seeding. x: unit rows. Returns
    (labels, centres)."""
    import numpy as np

    rng = rng or np.random.default_rng(SEED)
    n = len(x)
    centres = np.empty((k, x.shape[1]), dtype=x.dtype)
    centres[0] = x[rng.integers(n)]
    d2 = np.full(n, np.inf)
    for c in range(1, k):
        d2 = np.minimum(d2, np.maximum(0.0, 2.0 - 2.0 * (x @ centres[c - 1])))
        total = d2.sum()
        idx = rng.integers(n) if total <= 0 else rng.choice(n, p=d2 / total)
        centres[c] = x[idx]
    labels = np.zeros(n, dtype=np.int64)
    for it in range(iters):
        new = np.argmax(x @ centres.T, axis=1)
        if it and np.array_equal(new, labels):
            break
        labels = new
        for c in range(k):
            members = x[labels == c]
            if len(members):
                centres[c] = members.sum(0)
            else:  # an empty cluster takes the point worst served
                worst = np.argmin(np.max(x @ centres.T, axis=1))
                centres[c] = x[worst]
        centres = _unit(centres)
    return labels, centres


def silhouette(x, labels, sample: int = SAMPLE, rng=None) -> float:
    """Mean silhouette (cosine distance) over a sample of points."""
    import numpy as np

    rng = rng or np.random.default_rng(SEED)
    n = len(x)
    idx = rng.choice(n, size=min(sample, n), replace=False)
    xs, ls = x[idx], labels[idx]
    dist = 1.0 - xs @ xs.T
    ks = np.unique(ls)
    if len(ks) < 2:
        return -1.0
    # mean distance from each point to each cluster (within the sample)
    means = np.stack([dist[:, ls == k].mean(1) for k in ks], axis=1)
    counts = np.array([(ls == k).sum() for k in ks])
    own = np.searchsorted(ks, ls)
    a = means[np.arange(len(ls)), own]
    # a point's own cluster mean includes its zero distance to itself
    sizes = counts[own]
    a = np.where(sizes > 1, a * sizes / np.maximum(sizes - 1, 1), 0.0)
    other = means.copy()
    other[np.arange(len(ls)), own] = np.inf
    b = other.min(1)
    s = np.where(sizes > 1, (b - a) / np.maximum(np.maximum(a, b), 1e-12), 0.0)
    return float(s.mean())


def candidate_ks(n: int) -> list[int]:
    """Every k up to 12, then steps of about 25% up to MAX_K (and n/4)."""
    hi = max(2, min(MAX_K, n // 4))
    ks = list(range(2, min(hi, 12) + 1))
    k = 12
    while k < hi:
        k = min(hi, max(k + 1, round(k * 1.25)))
        ks.append(k)
    return ks


def best_of(x, k: int, n_init: int = N_INIT, seed: int = SEED):
    """k-means from several seedings; the one whose points sit closest to
    their centres wins."""
    import numpy as np

    best = None
    for i in range(n_init):
        labels, centres = kmeans(x, k, rng=np.random.default_rng(seed + 1000 * i + k))
        fit = float(np.take_along_axis(x @ centres.T, labels[:, None], 1).sum())
        if best is None or fit > best[2]:
            best = (labels, centres, fit)
    return best[0], best[1]


def cluster(x) -> tuple:
    """(labels, centres, k, silhouette). k is picked by silhouette on a
    sample (SELECT_SAMPLE points), then the whole set is clustered with it."""
    import numpy as np

    rng = np.random.default_rng(SEED)
    sample = x if len(x) <= SELECT_SAMPLE else x[rng.choice(len(x), SELECT_SAMPLE, replace=False)]
    best_k, best_s = 2, -2.0
    for k in candidate_ks(len(sample)):
        labels, _ = best_of(sample, k)
        s = silhouette(sample, labels, rng=np.random.default_rng(SEED))
        if s > best_s:
            best_k, best_s = k, s
    labels, centres = best_of(x, best_k) if len(sample) < len(x) else best_of(sample, best_k)
    return labels, centres, best_k, best_s


# ---------- suggestions ----------


def _cid(ids: list[str]) -> str:
    return hashlib.sha1(",".join(sorted(ids)).encode()).hexdigest()[:16]


def compute(session: Session) -> dict:
    """Cluster the library and write collections.json. Returns its content."""
    import numpy as np

    from . import ai
    from . import suggestions as sugg

    model = ai.active_model()
    rows = session.execute(
        sa.select(ItemEmbedding.item_id, ItemEmbedding.vector)
        .join(Item, Item.id == ItemEmbedding.item_id)
        .where(ItemEmbedding.model == model, Item.deleted_at.is_(None))
        .order_by(ItemEmbedding.item_id).limit(MAX_ITEMS)
    ).all()
    out = {"version": 1, "computed_at": datetime.now(UTC).isoformat(), "model": model,
           "items": len(rows), "k": 0, "silhouette": None, "clusters": []}
    if len(rows) >= 2 * MIN_SIZE:
        ids = [r[0] for r in rows]
        x = _unit(np.asarray([r[1] for r in rows], dtype=np.float32))
        labels, centres, k, sil = cluster(x)
        out["k"], out["silhouette"] = int(k), round(sil, 4)
        tag_rows = session.execute(
            sa.select(ItemTag.item_id, Tag.name).join(Tag, Tag.id == ItemTag.tag_id)
        ).all()
        tags_of: dict[str, list[str]] = {}
        uses: dict[str, int] = {}
        for item_id, name in tag_rows:
            tags_of.setdefault(item_id, []).append(name)
            uses[name] = uses.get(name, 0) + 1
        folders_of: dict[str, list[str]] = {}
        for item_id, fid in session.execute(sa.select(ItemFolder.item_id, ItemFolder.folder_id)):
            folders_of.setdefault(item_id, []).append(fid)
        folder_names = dict(session.execute(sa.select(Folder.id, Folder.name)).all())
        try:
            sugg.refresh_tag_embeddings(session, limit=None)
        except ai.AiUnavailable as exc:
            session.rollback()
            log.info("collections: labels without new tag vectors (%s)", exc)
        tag_names, tag_m = sugg.tag_matrix(session, model)
        reference = sugg.reference_matrix(model) if tag_names else None
        for c in range(k):
            idx = np.nonzero(labels == c)[0]
            if len(idx) < MIN_SIZE:
                continue
            sims = x[idx] @ centres[c]
            cohesion = float(sims.mean())
            if cohesion < MIN_COHESION:
                continue
            members = [ids[i] for i in idx[np.argsort(-sims)]]
            fcount: dict[str, int] = {}
            for m in members:
                for f in folders_of.get(m, []):
                    fcount[f] = fcount.get(f, 0) + 1
            top_folder = max(fcount.items(), key=lambda kv: kv[1]) if fcount else None
            if top_folder and top_folder[1] / len(members) >= ALREADY_FILED:
                continue
            tcount: dict[str, int] = {}
            for m in members:
                for t in tags_of.get(m, []):
                    tcount[t] = tcount.get(t, 0) + 1
            common = sorted(tcount.items(), key=lambda kv: (-kv[1], kv[0].lower()))[:5]
            zs = (sugg.zero_shot(tag_names, tag_m, centres[c], top=3, reference=reference)
                  if tag_names else [])
            # a label names what the items show: never a bookkeeping tag
            # (credit:, license: ...) however many items carry it
            shared = [(t, n) for t, n in common if sugg.candidate_tag(t)]
            if shared and shared[0][1] / len(members) >= TAG_SHARE:
                label, source = shared[0][0], "tags"
            elif zs and _members_agree(zs[0][0], x[idx], tag_names, tag_m):
                label, source = _usual_spelling(zs[0][0], tcount, uses), "zero_shot"
            else:
                label, source = None, None
            out["clusters"].append({
                "id": _cid(members),
                "size": len(members),
                "item_ids": members,
                "sample_item_ids": members[:SAMPLE_IDS],
                "label_suggestion": label,
                "label_source": source,
                "common_tags": [{"tag": t, "count": n} for t, n in common],
                "zero_shot_tags": [{"tag": t, "score": round(p, 4)} for t, p in zs],
                "cohesion": round(cohesion, 4),
                "top_folder": ({"id": top_folder[0], "name": folder_names.get(top_folder[0]),
                                "count": top_folder[1]} if top_folder else None),
            })
        out["clusters"].sort(key=lambda c: (-c["cohesion"] * c["size"] ** 0.5, c["id"]))
    write_cache(out)
    return out


def _usual_spelling(label: str, member_tags: dict[str, int], uses: dict[str, int]) -> str:
    """A zero-shot label that is a variant spelling of a tag the members
    carry ("archtecture" next to "architecture", "dogs" next to "dog") gives
    way to the one more items in the library use."""
    from . import suggestions as sugg
    from .tags import _stem, levenshtein

    low = label.lower()
    best = label
    for t in member_tags:
        tl = t.lower()
        if tl == low or not sugg.candidate_tag(t) or uses.get(t, 0) <= uses.get(best, 0):
            continue
        if _stem(tl) == _stem(low) or (min(len(tl), len(low)) >= 5 and levenshtein(tl, low) <= 2):
            best = t
    return best


def _members_agree(tag: str, vecs, tag_names: list[str], tag_m) -> bool:
    """A zero-shot label must also be what the members themselves look
    like: it ranks in the top LABEL_RANK candidate tags for at least
    LABEL_AGREEMENT of them (the centre of a loose cluster can match a tag
    that few members do)."""
    import numpy as np

    from . import suggestions as sugg

    keep = [i for i, n in enumerate(tag_names) if sugg.candidate_tag(n)]
    names = [tag_names[i] for i in keep]
    if tag not in names:
        return False
    cos = _unit(np.asarray(vecs, dtype=np.float32)) @ tag_m[keep].T  # members x tags
    mine = cos[:, names.index(tag)]
    rank = (cos > mine[:, None]).sum(axis=1)  # 0 = the member's best tag
    # in a small vocabulary "top 3" means little: there it must be the best
    within = min(LABEL_RANK, max(1, len(names) // 4))
    return float((rank < within).mean()) >= LABEL_AGREEMENT


def path():
    return get_settings().state_dir / FILE


def write_cache(data: dict) -> None:
    meta_svc.atomic_write_text(path(), json.dumps(data, indent=2, ensure_ascii=False))


def read_cache() -> dict | None:
    try:
        data = meta_svc.read_json(path())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def due(interval_hours: float) -> bool:
    if interval_hours <= 0:
        return False
    data = read_cache()
    try:
        at = datetime.fromisoformat((data or {}).get("computed_at"))
    except (TypeError, ValueError):
        return True
    return (datetime.now(UTC) - at).total_seconds() >= interval_hours * 3600


def suggested(session: Session, refresh: bool = False) -> dict:
    """The cached suggestions (computed now when there are none, or on
    refresh), with items that were trashed or filed since left out."""
    data = None if refresh else read_cache()
    if data is None:
        data = compute(session)
    live = set(session.execute(
        sa.select(Item.id).where(Item.deleted_at.is_(None))
    ).scalars())
    clusters = []
    for c in data.get("clusters", []):
        ids = [i for i in c.get("item_ids", []) if i in live]
        if len(ids) < MIN_SIZE or c.get("accepted"):
            continue
        clusters.append({**c, "size": len(ids), "item_ids": ids,
                         "sample_item_ids": ids[:SAMPLE_IDS]})
    return {**data, "clusters": clusters}


def accept(session: Session, cid: str, name: str, kind: str = "folder",
           parent_id: str | None = None) -> dict:
    """Make the cluster real. kind=folder: a new folder with its items in it.
    kind=smart_folder: the name becomes a tag on its items and a smart
    folder shows that tag. The item changes share the caller's batch."""
    from ..schemas import SmartFolderQuery
    from . import folders as folders_svc
    from . import items as items_svc
    from . import smart_folders as smart_svc

    data = read_cache() or {}
    cluster_ = next((c for c in data.get("clusters", []) if c.get("id") == cid), None)
    if cluster_ is None:
        raise NotFound(cid)
    ids = [i for i in cluster_["item_ids"]
           if (it := session.get(Item, i)) is not None and it.deleted_at is None]
    if not ids:
        raise NotFound(cid)
    if kind == "folder":
        folder = folders_svc.create_folder(session, name, parent_id, "")
        for item_id in ids:
            items_svc.set_folders(session, item_id, add=[folder.id])
        made = {"folder_id": folder.id}
    elif kind == "smart_folder":
        tag = validation.tag_name(name)
        for item_id in ids:
            items_svc.set_tags(session, item_id, add=[tag])
        smart = smart_svc.create(session, name, SmartFolderQuery(tags=[tag]))
        made = {"smart_folder_id": smart.id, "tag": tag}
    else:
        raise validation.ValidationError("kind must be folder or smart_folder")
    cluster_["accepted"] = {**made, "at": datetime.now(UTC).isoformat()}
    write_cache(data)
    return {"id": cid, "items": len(ids), **made, "batch_id": events.context().batch_id}
