"""Tag suggestions for one item (never applied by themselves).

Two sources, each with its own `why`:
- "similar items": the item's nearest neighbours by image embedding vote
  with their tags, each vote weighted by how close the neighbour is
  (score = the weighted share of neighbours carrying the tag).
- "matches the image": zero-shot CLIP. Every tag name is embedded as text
  once per model ("a photo of <tag>", cached in tag_embeddings and kept up
  to date as tags come and go), and the image is compared with all of them.
  Raw CLIP cosines sit in a narrow band (0.15-0.35), so they are turned
  into a softmax over the vocabulary at CLIP's own temperature (logit scale
  100); the score is that probability.

Feedback (POST .../tag-suggestions/feedback) is kept per item: a rejected
tag is never suggested again for that item, an accepted one isn't either
(it is applied by then). Items' own tags, and tags they already imply, are
left out.
"""

import json
import logging
import math
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .. import __version__
from ..models import HalfVec, Item, ItemEmbedding, ItemTag, Tag, TagEmbedding, TagFeedback
from . import validation

log = logging.getLogger("kist.suggestions")

NEIGHBOURS = 24
MIN_NEIGHBOUR_SIM = 0.6
KNN_MIN_SCORE = 0.15
ZERO_SHOT_TEMPERATURE = 100.0
ZERO_SHOT_MIN_SCORE = 0.08
# below this raw cosine a tag name doesn't describe the image at all, however
# it ranks among the others (CLIP ViT-B-32: matching captions score about
# 0.25-0.33, unrelated ones 0.10-0.20)
ZERO_SHOT_MIN_COSINE = 0.2
EMBED_PER_REQUEST = 64
PROMPT = "a photo of {}"
FEEDBACK_FILE = "tag-feedback.json"
SIMILAR, MATCHES = "similar items", "matches the image"


def halfvec(vec):
    """A vector as a halfvec SQL value (sent as text, cast by Postgres)."""
    return sa.cast(sa.bindparam(None, list(vec), type_=HalfVec()), HalfVec())


def prompt_for(tag: str) -> str:
    from .vocabulary import split_namespace

    ns, value = split_namespace(tag)
    return PROMPT.format(value if ns is None else f"{value} ({ns})")


def _item_vector(session: Session, item_id: str, model: str) -> list[float] | None:
    return session.execute(
        sa.select(ItemEmbedding.vector).where(
            ItemEmbedding.item_id == item_id, ItemEmbedding.model == model)
    ).scalar_one_or_none()


def refresh_tag_embeddings(session: Session, limit: int | None = EMBED_PER_REQUEST) -> int:
    """Embed tag names that have no vector for the active model yet (at most
    `limit` per call; the worker's daily run does the rest), and drop
    vectors of tags that are gone. Returns how many were embedded."""
    from . import ai

    model = ai.active_model()
    session.execute(sa.delete(TagEmbedding).where(
        TagEmbedding.model == model, TagEmbedding.tag.not_in(sa.select(Tag.name))))
    missing = list(session.execute(
        sa.select(Tag.name).where(~sa.exists().where(
            TagEmbedding.model == model, TagEmbedding.tag == Tag.name))
        .order_by(Tag.name).limit(limit)
    ).scalars())
    done = 0
    for name in missing:
        vec = halfvec(ai.ml_embed_text(prompt_for(name)))
        session.execute(
            pg_insert(TagEmbedding).values(model=model, tag=name, vector=vec)
            .on_conflict_do_update(index_elements=["model", "tag"], set_={"vector": vec})
        )
        done += 1
    session.commit()
    return done


def tag_matrix(session: Session, model: str):
    """(names, matrix of unit vectors) of every tag embedded for `model`."""
    import numpy as np

    rows = session.execute(
        sa.select(TagEmbedding.tag, TagEmbedding.vector)
        .join(Tag, Tag.name == TagEmbedding.tag)
        .where(TagEmbedding.model == model).order_by(TagEmbedding.tag)
    ).all()
    if not rows:
        return [], np.zeros((0, 1), dtype=np.float32)
    m = np.asarray([r[1] for r in rows], dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-12
    return [r[0] for r in rows], m


def zero_shot(names: list[str], matrix, vec, top: int = 10,
              min_cosine: float = ZERO_SHOT_MIN_COSINE) -> list[tuple[str, float]]:
    """(tag, probability) for an image vector, best first; tags whose raw
    cosine is below min_cosine are left out (a softmax over a small or
    unrelated vocabulary would otherwise crown a tag that doesn't fit)."""
    import numpy as np

    if not names:
        return []
    v = np.asarray(vec, dtype=np.float32)
    v /= np.linalg.norm(v) + 1e-12
    cos = matrix @ v
    logits = cos * ZERO_SHOT_TEMPERATURE
    p = np.exp(logits - logits.max())
    p /= p.sum()
    order = np.argsort(-p)[:top]
    return [(names[i], float(p[i])) for i in order if cos[i] >= min_cosine]


def _neighbours(session: Session, item_id: str, vec, model: str) -> list[tuple[str, float]]:
    """The nearest active items the caller can see: [(item_id, cosine)]."""
    from . import search

    dim = len(vec)
    distance = sa.cast(ItemEmbedding.vector, HalfVec(dim)).op("<=>", return_type=sa.Float)(
        sa.cast(sa.bindparam("q", vec, type_=HalfVec()), HalfVec(dim))
    )
    filters = search.build_filters()
    rows = session.execute(
        sa.select(Item.id, distance)
        .join(ItemEmbedding, sa.and_(ItemEmbedding.item_id == Item.id,
                                     ItemEmbedding.model == model))
        .where(*filters, Item.id != item_id)
        .order_by(distance).limit(NEIGHBOURS)
    ).all()
    return [(i, 1.0 - float(d)) for i, d in rows if 1.0 - float(d) >= MIN_NEIGHBOUR_SIM]


def suggest(session: Session, item_id: str, limit: int = 10) -> list[dict]:
    """[{tag, score, why}] for an item, best first (empty without an
    embedding). AiUnavailable only when new tags need embedding and the ml
    service is down; known tags still work then."""
    from . import ai
    from . import items as items_svc
    from .vocabulary import implied_closure

    item = items_svc.get_item(session, item_id)
    model = ai.active_model()
    vec = _item_vector(session, item.id, model)
    if vec is None:
        return []
    own = {t.name.lower() for t in item.tags}
    own |= {t.lower() for t in implied_closure(session, [t.name for t in item.tags])}
    feedback = dict(session.execute(
        sa.select(sa.func.lower(TagFeedback.tag), TagFeedback.accepted)
        .where(TagFeedback.item_id == item.id)
    ).all())
    skip = own | set(feedback)
    scores: dict[str, tuple[float, str, str]] = {}  # lower -> (score, why, name)

    neighbours = _neighbours(session, item.id, vec, model)
    if neighbours:
        weight = {i: s for i, s in neighbours}
        total = sum(weight.values())
        votes: dict[str, float] = {}
        names: dict[str, str] = {}
        for nid, tag in session.execute(
            sa.select(ItemTag.item_id, Tag.name).join(Tag, Tag.id == ItemTag.tag_id)
            .where(ItemTag.item_id.in_(list(weight)))
        ).all():
            votes[tag.lower()] = votes.get(tag.lower(), 0.0) + weight[nid]
            names[tag.lower()] = tag
        for key, v in votes.items():
            score = v / total
            if key not in skip and score >= KNN_MIN_SCORE:
                scores[key] = (score, SIMILAR, names[key])

    try:
        refresh_tag_embeddings(session)
    except ai.AiUnavailable as exc:
        session.rollback()
        log.info("tag suggestions without new tag vectors: %s", exc)
    tag_names, matrix = tag_matrix(session, model)
    for name, p in zero_shot(tag_names, matrix, vec, top=limit * 2):
        key = name.lower()
        if key in skip or p < ZERO_SHOT_MIN_SCORE:
            continue
        if key not in scores or p > scores[key][0]:
            scores[key] = (p, MATCHES, name)
    out = [{"tag": name, "score": round(score, 4), "why": why}
           for score, why, name in scores.values() if math.isfinite(score)]
    out.sort(key=lambda s: (-s["score"], s["tag"].lower()))
    return out[:limit]


# ---------- feedback ----------


def feedback(session: Session, item_id: str, tag: str, accepted: bool) -> dict:
    from . import items as items_svc

    item = items_svc.get_item(session, item_id)
    tag = validation.tag_name(tag)
    session.execute(
        pg_insert(TagFeedback).values(item_id=item.id, tag=tag, accepted=accepted,
                                      at=datetime.now(UTC))
        .on_conflict_do_update(index_elements=["item_id", "tag"],
                               set_={"accepted": accepted, "at": datetime.now(UTC)})
    )
    session.commit()
    write_feedback_sidecar(session, item.id)
    return {"item_id": item.id, "tag": tag, "accepted": accepted}


def _feedback_path(item_id: str):
    from .ai import ai_dir

    return ai_dir(item_id) / FEEDBACK_FILE


def write_feedback_sidecar(session: Session, item_id: str) -> None:
    from . import meta as meta_svc

    rows = session.execute(
        sa.select(TagFeedback).where(TagFeedback.item_id == item_id).order_by(TagFeedback.tag)
    ).scalars().all()
    try:
        meta_svc.atomic_write_text(_feedback_path(item_id), json.dumps({
            "feedback": [{"tag": r.tag, "accepted": r.accepted, "at": r.at.isoformat()}
                         for r in rows],
            "kist_version": __version__,
        }, indent=2, ensure_ascii=False))
    except OSError:
        log.exception("tag feedback for %s not written to disk", item_id)
        meta_svc.note_pending(f"tag-feedback:{item_id}")


def load_feedback_sidecar(session: Session, item_id: str) -> int:
    """rebuild-index: feedback rows from items/<id>/ai/tag-feedback.json."""
    from . import meta as meta_svc

    path = _feedback_path(item_id)
    if not path.is_file():
        return 0
    n = 0
    for row in (meta_svc.read_json(path).get("feedback") or []):
        try:
            at = datetime.fromisoformat(row["at"]) if row.get("at") else datetime.now(UTC)
            n += session.execute(
                pg_insert(TagFeedback).values(item_id=item_id, tag=str(row["tag"]),
                                              accepted=bool(row["accepted"]), at=at)
                .on_conflict_do_nothing()
            ).rowcount or 0
        except (KeyError, TypeError, ValueError):
            continue
    session.commit()
    return n


def check_feedback_files(session: Session) -> list[str]:
    """verify: every item with feedback rows has the sidecar listing them."""
    from . import meta as meta_svc

    counts = dict(session.execute(
        sa.select(TagFeedback.item_id, sa.func.count()).group_by(TagFeedback.item_id)
    ).all())
    problems = []
    for item_id, n in sorted(counts.items()):
        path = _feedback_path(item_id)
        try:
            have = len(meta_svc.read_json(path).get("feedback") or [])
        except (OSError, ValueError, AttributeError):
            have = -1
        if have != n:
            problems.append(f"{item_id}: ai/{FEEDBACK_FILE} doesn't match its {n} feedback rows")
    return problems
