"""Ranked search: meaning, more like this, colour, text in images, hybrid.

GET /api/items (and MCP search_items) rank results when any of these is
given; every existing filter still applies first (services/search.py
build_filters, including what a folder-limited token may see):

    semantic=<words>   by meaning: cosine similarity of the item's image
                       embedding to the words' text embedding
    similar=<item id>  more like this: similarity to that item's embedding
    (image vector)     search by image (POST /api/search/image), same thing
                       for a file that isn't in the library
    color=<hex>        only items with a palette colour within the tolerance
                       (CIEDE2000), ranked by how much of the palette matches
    text=<words>       only items whose OCR / PDF text has every word (word
                       prefixes match), ranked by ts_rank
    q=<words> together with a meaning signal: keyword hits are a signal of
                       their own instead of a filter ("hybrid")

One signal orders by its own score. Several are merged with reciprocal rank
fusion: score = sum(1 / (60 + rank in each signal)). Every item carries
match = {kind, score, snippet?}; kind is the signal it matched, or "hybrid"
when it matched more than one. Items a meaning signal can't score (no
embedding yet) come last.

Paging is an offset cursor (opaque to clients); `total` is counted on the
first page only, like every other search.
"""

import re
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..models import HalfVec, Item, ItemColor, ItemEmbedding, ItemText
from . import colour
from .validation import ValidationError

RRF_K = 60
RANKED_CURSOR = "~ranked"
SNIPPET_OPTIONS = (
    "StartSel=<<, StopSel=>>, MaxWords=18, MinWords=6, MaxFragments=2, "
    'FragmentDelimiter=" … "'
)
SNIPPET_MAX_CHARS = 200_000
MAX_WORDS = 16
WORD = re.compile(r"\w+", re.UNICODE)
READ_SOURCES = ("ocr", "pdf")  # text found in the item (services/ai.READ_SOURCES)

KIND_KEYWORD = "keyword"
KIND_MEANING = "meaning"
KIND_TEXT = "text"
KIND_COLOR = "color"
KIND_SIMILAR = "similar"
KIND_HYBRID = "hybrid"


# ---------- text matching ----------


def tsquery(words: str | None) -> str | None:
    """'Orvis recei' -> 'orvis:* & recei:*' (every word, as a prefix), or
    None when there's nothing to search for. Only word characters reach
    to_tsquery, so its syntax can't be injected."""
    found = WORD.findall((words or "").lower())[:MAX_WORDS]
    return " & ".join(f"{w}:*" for w in found) or None


def _tsv(col):
    # the same expression as the ix_item_text_tsv GIN index
    return sa.func.to_tsvector(sa.literal_column("'simple'::regconfig"), col)


def _tsq(q: str):
    return sa.func.to_tsquery(sa.literal_column("'simple'::regconfig"), q)


def text_match(tsq: str, sources: tuple[str, ...] | None = None):
    """EXISTS: the item has text (OCR, PDF, caption) matching the query."""
    conds = [ItemText.item_id == Item.id, _tsv(ItemText.text).op("@@")(_tsq(tsq))]
    if sources:
        conds.append(ItemText.source.in_(sources))
    return sa.exists().where(*conds)


def has_text_filter(value: bool):
    found = sa.exists().where(
        ItemText.item_id == Item.id, ItemText.source.in_(READ_SOURCES), ItemText.text != ""
    )
    return found if value else sa.not_(found)


# ---------- colour ----------


@dataclass(frozen=True)
class ColourQuery:
    hex: str
    lab: tuple[float, float, float]
    max_de: float

    @classmethod
    def parse(cls, value: str, tolerance: float | None) -> "ColourQuery":
        try:
            h = colour.parse_hex(value)
        except ValueError as exc:
            raise ValidationError(str(exc)) from None
        tol = colour.DEFAULT_TOLERANCE if tolerance is None else tolerance
        if not 0 <= tol <= 100:
            raise ValidationError("color_tol must be between 0 and 100")
        return cls(h, colour.hex_to_lab(h), colour.max_delta(tol))

    def near(self):
        """item_colors rows within the tolerance: the Lab box (index) first,
        then CIEDE2000."""
        (l0, l1), (a0, a1), (b0, b1) = colour.lab_box(self.lab, self.max_de)
        return [
            ItemColor.a.between(a0, a1),
            ItemColor.b.between(b0, b1),
            ItemColor.l.between(l0, l1),
            self.delta() <= self.max_de,
        ]

    def delta(self):
        light, a, b = self.lab
        return sa.func.kist_ciede2000(ItemColor.l, ItemColor.a, ItemColor.b, light, a, b,
                                      type_=sa.Float)


def colour_filter(cq: ColourQuery):
    return sa.exists().where(ItemColor.item_id == Item.id, *cq.near())


# ---------- the query ----------


@dataclass
class Signals:
    """What a ranked search ranks by (already resolved to vectors)."""

    model: str = ""
    meaning: list[float] | None = None  # semantic=<words>
    similar: list[float] | None = None  # similar=<id> or an uploaded image
    keyword: str | None = None  # q, only in a hybrid
    colour: ColourQuery | None = None
    text: str | None = None  # a tsquery
    snippet_q: str | None = None  # tsquery for snippets of keyword hits
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return any(v is not None for v in (self.meaning, self.similar, self.colour, self.text))


def _ranked(sub_id, score, name: str):
    """(id, score, rk) for one signal."""
    return sa.select(
        sub_id.label("id"),
        score.label("score"),
        sa.func.row_number().over(order_by=(score.desc(), sub_id.asc())).label("rk"),
    ).subquery(name)


def _vector_signal(base, model: str, vec: list[float], name: str):
    dim = len(vec)
    distance = sa.cast(ItemEmbedding.vector, HalfVec(dim)).op("<=>", return_type=sa.Float)(
        sa.cast(sa.bindparam(f"{name}_vec", vec, type_=HalfVec()), HalfVec(dim))
    )
    inner = (
        sa.select(base.c.id.label("id"), (1 - distance).label("score"))
        .join(ItemEmbedding, sa.and_(ItemEmbedding.item_id == base.c.id,
                                     ItemEmbedding.model == model))
    )
    inner = inner.subquery(f"{name}_raw")
    return _ranked(inner.c.id, inner.c.score, name)


def _keyword_score(q: str, tsq: str | None):
    pattern = f"%{q}%"
    text_rank = sa.literal(0.0)
    if tsq:
        text_rank = (
            sa.select(sa.func.coalesce(
                sa.func.max(sa.func.ts_rank_cd(_tsv(ItemText.text), _tsq(tsq))), 0.0))
            .where(ItemText.item_id == Item.id, _tsv(ItemText.text).op("@@")(_tsq(tsq)))
            .scalar_subquery()
        )
    return (
        sa.func.similarity(Item.filename, q)
        + sa.case((Item.filename.ilike(pattern), 0.5), else_=0.0)
        + sa.case((Item.notes.ilike(pattern), 0.3), else_=0.0)
        + text_rank
    )


def keyword_condition(q: str):
    """What q matches (the same as search.build_filters' q filter)."""
    pattern = f"%{q}%"
    conds = [
        Item.filename.ilike(pattern),
        sa.func.similarity(Item.filename, q) > 0.25,
        Item.notes.ilike(pattern),
    ]
    tsq = tsquery(q)
    if tsq:
        conds.append(text_match(tsq))
    return sa.or_(*conds)


def _keyword_signal(base, q: str):
    inner = (
        sa.select(Item.id.label("id"), _keyword_score(q, tsquery(q)).label("score"))
        .join(base, base.c.id == Item.id)
        .where(keyword_condition(q))
        .subquery("keyword_raw")
    )
    return _ranked(inner.c.id, inner.c.score, "keyword")


def _colour_signal(base, cq: ColourQuery):
    weight = ItemColor.pct * (1 - cq.delta() / cq.max_de)
    inner = (
        sa.select(ItemColor.item_id.label("id"), sa.func.sum(weight).label("score"))
        .join(base, base.c.id == ItemColor.item_id)
        .where(*cq.near())
        .group_by(ItemColor.item_id)
        .subquery("color_raw")
    )
    return _ranked(inner.c.id, inner.c.score, "color")


def _text_signal(base, tsq: str):
    inner = (
        sa.select(ItemText.item_id.label("id"),
                  sa.func.max(sa.func.ts_rank_cd(_tsv(ItemText.text), _tsq(tsq))).label("score"))
        .join(base, base.c.id == ItemText.item_id)
        .where(ItemText.source.in_(READ_SOURCES), _tsv(ItemText.text).op("@@")(_tsq(tsq)))
        .group_by(ItemText.item_id)
        .subquery("text_raw")
    )
    return _ranked(inner.c.id, inner.c.score, "text")


def decode_offset(cursor: str | None) -> int:
    from .search import SEMANTIC_CURSOR, BadCursor, decode_cursor

    if not cursor:
        return 0
    value, marker = decode_cursor(cursor)
    if marker not in (RANKED_CURSOR, SEMANTIC_CURSOR) or not isinstance(value, int) or value < 0:
        raise BadCursor("invalid cursor for a ranked search; start again without one")
    return value


def ranked_page(session: Session, filters: list, sig: Signals, limit: int,
                cursor: str | None) -> tuple[list[Item], str | None, dict[str, dict]]:
    """One page of a ranked search: (items, next_cursor, {item_id: match})."""
    from .search import encode_cursor

    offset = decode_offset(cursor)
    base = sa.select(Item.id.label("id")).where(*filters).cte("base")
    signals: list[tuple[str, Any]] = []
    if sig.meaning is not None:
        signals.append((KIND_MEANING, _vector_signal(base, sig.model, sig.meaning, "meaning")))
    if sig.similar is not None:
        signals.append((KIND_SIMILAR, _vector_signal(base, sig.model, sig.similar, "similar")))
    if sig.keyword:
        signals.append((KIND_KEYWORD, _keyword_signal(base, sig.keyword)))
    if sig.colour is not None:
        signals.append((KIND_COLOR, _colour_signal(base, sig.colour)))
    if sig.text:
        signals.append((KIND_TEXT, _text_signal(base, sig.text)))

    cols: list[Any] = [Item]
    for kind, sub in signals:
        cols += [sub.c.score.label(f"{kind}_score"), sub.c.rk.label(f"{kind}_rk")]
    if len(signals) == 1:
        sub = signals[0][1]
        order_value = sub.c.score
    else:
        order_value = sa.literal(0.0)
        for _, sub in signals:
            order_value = order_value + sa.func.coalesce(1.0 / (RRF_K + sub.c.rk), 0.0)
    stmt = sa.select(*cols, order_value.label("fused")).join(base, base.c.id == Item.id)
    for _, sub in signals:
        stmt = stmt.outerjoin(sub, sub.c.id == Item.id)
    stmt = (
        stmt.order_by(sa.literal_column("fused").desc().nulls_last(), Item.id.asc())
        .offset(offset)
        .limit(limit + 1)
    )
    rows = session.execute(stmt).all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(offset + limit, RANKED_CURSOR)

    items: list[Item] = []
    matches: dict[str, dict] = {}
    for row in rows:
        item = row[0]
        items.append(item)
        hit = [kind for kind, _ in signals if getattr(row, f"{kind}_rk") is not None]
        if not hit:
            continue
        if len(signals) == 1:
            kind, score = hit[0], getattr(row, f"{hit[0]}_score")
        else:
            kind = hit[0] if len(hit) == 1 else KIND_HYBRID
            score = row.fused
        matches[item.id] = {"kind": kind, "score": round(float(score or 0.0), 6)}
    snippet_q = sig.text or sig.snippet_q
    if snippet_q:
        sources = READ_SOURCES if sig.text else None
        for item_id, snip in snippets(session, [i.id for i in items], snippet_q,
                                      sources).items():
            if item_id in matches:
                matches[item_id]["snippet"] = snip
    return items, next_cursor, matches


def keyword_matches(session: Session, items: list[Item], q: str) -> dict[str, dict]:
    """match for a plain keyword search (ordered by `order`, not ranked):
    {kind: keyword, score, snippet when the words are in its text}."""
    if not items or not q:
        return {}
    ids = [i.id for i in items]
    scores = dict(session.execute(
        sa.select(Item.id, _keyword_score(q, tsquery(q))).where(Item.id.in_(ids))
    ).all())
    out = {i: {"kind": KIND_KEYWORD, "score": round(float(scores.get(i) or 0.0), 6)}
           for i in ids}
    tsq = tsquery(q)
    if tsq:
        for item_id, snip in snippets(session, ids, tsq, None).items():
            out[item_id]["snippet"] = snip
    return out


def snippets(session: Session, item_ids: list[str], tsq: str,
             sources: tuple[str, ...] | None) -> dict[str, str]:
    """The text around each item's hit, the words marked << >> (OCR and PDF
    text before a caption)."""
    if not item_ids:
        return {}
    conds = [ItemText.item_id.in_(item_ids), _tsv(ItemText.text).op("@@")(_tsq(tsq))]
    if sources:
        conds.append(ItemText.source.in_(sources))
    headline = sa.func.ts_headline(
        sa.literal_column("'simple'::regconfig"),
        sa.func.left(ItemText.text, SNIPPET_MAX_CHARS),
        _tsq(tsq),
        SNIPPET_OPTIONS,
    )
    rows = session.execute(
        sa.select(ItemText.item_id, ItemText.source, headline).where(*conds)
        .order_by(ItemText.item_id, sa.case((ItemText.source == "caption", 1), else_=0))
    ).all()
    out: dict[str, str] = {}
    for item_id, _source, snip in rows:
        if item_id not in out and snip:
            out[item_id] = " ".join(snip.split())
    return out
