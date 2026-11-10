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
                       their own instead of a filter ("hybrid"). Keyword
                       hits are filename, notes, annotation text, and OCR /
                       PDF / caption text

Every filter build_filters knows applies to every mode alike: tags (with
the vocabulary's aliases, implied tags and ns:* namespaces), folders,
flag / label, person, has_text, a folder-limited reader's scope.
stacks=collapse is applied after ranking (ranked_page): a stack shows once,
as its best-ranked member.

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
from sqlalchemy.orm import Session, aliased

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
# a filename matches q by trigram similarity above this (keyword_condition);
# db.DB_OPTIONS sets pg_trgm.similarity_threshold to it
TRGM_THRESHOLD = 0.25

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
    """EXISTS: the item has text (OCR, PDF, caption, page snapshot) matching
    the query; `sources` narrows it (text= passes READ_SOURCES)."""
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

    def delta(self):
        light, a, b = self.lab
        return sa.func.kist_ciede2000(ItemColor.l, ItemColor.a, ItemColor.b, light, a, b,
                                      type_=sa.Float)


def colour_hits(cq: ColourQuery):
    """CTE (id, score): items with a palette colour within the tolerance and
    how much of their palette is near it. CIEDE2000 runs once per candidate
    row (the OFFSET 0 keeps Postgres from evaluating it again for the score)."""
    (l0, l1), (a0, a1), (b0, b1) = colour.lab_box(cq.lab, cq.max_de)
    rows = (
        sa.select(ItemColor.item_id, ItemColor.pct, cq.delta().label("de"))
        .where(ItemColor.a.between(a0, a1), ItemColor.b.between(b0, b1),
               ItemColor.l.between(l0, l1))
        .offset(0)
        .subquery("colour_rows")
    )
    return (
        sa.select(rows.c.item_id.label("id"),
                  sa.func.sum(rows.c.pct * (1 - rows.c.de / cq.max_de)).label("score"))
        .where(rows.c.de <= cq.max_de)
        .group_by(rows.c.item_id)
        .cte("colour_hits")
    )


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


def annotation_match(pattern: str):
    """EXISTS: one of the item's annotations has this ILIKE pattern
    (the annotations trigram index, migration 0401)."""
    from ..models import Annotation

    return sa.exists().where(Annotation.item_id == Item.id, Annotation.text.ilike(pattern))


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
        + sa.case((Item.place.ilike(pattern), 0.3), else_=0.0)
        + sa.case((annotation_match(pattern), 0.3), else_=0.0)
        + text_rank
    )


def keyword_condition(q: str):
    """What q matches (the same as search.build_filters' q filter): the
    filename (as a substring or by trigram similarity over 0.25), notes,
    place, annotation text, or text in the item.

    Written as items.id IN (a UNION of index scans), not as an OR of
    per-row tests: the OR made Postgres compute similarity() for every
    item and run the annotation and text tests as subplans, so a keyword
    count read the whole library (923 ms on dev0 at 250k items). Every arm
    is now an index scan (trigram GIN on filename, notes, place and
    annotations; the tsvector GIN on item_text). The similarity arm keeps
    the exact `similarity() > 0.25` test and adds `filename % q`, which the
    trigram index answers; db.DB_OPTIONS sets pg_trgm.similarity_threshold
    to 0.25 for Kist's connections, so `%` holds for every row the exact
    test can pass (tests/test_m6_perf.py checks both)."""
    from ..models import Annotation

    pattern = f"%{q}%"
    named = aliased(Item, name="keyword_item")
    arms = [
        sa.select(named.id).where(sa.or_(
            named.filename.ilike(pattern),
            sa.and_(named.filename.op("%")(q),
                    sa.func.similarity(named.filename, q) > TRGM_THRESHOLD),
            named.notes.ilike(pattern),
            named.place.ilike(pattern),  # "City, Country" (Milestone 5)
        )),
        sa.select(Annotation.item_id).where(Annotation.text.ilike(pattern)),
    ]
    tsq = tsquery(q)
    if tsq:
        arms.append(sa.select(ItemText.item_id).where(_tsv(ItemText.text).op("@@")(_tsq(tsq))))
    # IS TRUE keeps Postgres from turning the IN into a semi-join: as a
    # hashed subplan it stays a filter, so a newest-first page still walks
    # the date index and stops after a page (dev0: 204 -> 62 ms)
    return Item.id.in_(sa.union(*arms)).is_(True)


def _vector_source(model: str, vec: list[float], name: str):
    """(embeddings alias, join condition, score): the item's embedding for
    `model` and its cosine similarity to `vec`."""
    emb = aliased(ItemEmbedding, name=f"{name}_emb")
    dim = len(vec)
    distance = sa.cast(emb.vector, HalfVec(dim)).op("<=>", return_type=sa.Float)(
        sa.cast(sa.bindparam(f"{name}_vec", vec, type_=HalfVec()), HalfVec(dim))
    )
    return emb, sa.and_(emb.item_id == Item.id, emb.model == model), 1 - distance


def _keyword_scores(q: str, filters: list):
    """(id, score) of the items q matches (a hybrid's keyword signal)."""
    return (
        sa.select(Item.id.label("id"), _keyword_score(q, tsquery(q)).label("score"))
        .where(keyword_condition(q), *filters)
        .subquery("keyword_raw")
    )


def _text_scores(tsq: str):
    """(id, score) of the items whose OCR / PDF text has every word."""
    return (
        sa.select(ItemText.item_id.label("id"),
                  sa.func.max(sa.func.ts_rank_cd(_tsv(ItemText.text), _tsq(tsq))).label("score"))
        .where(ItemText.source.in_(READ_SOURCES), _tsv(ItemText.text).op("@@")(_tsq(tsq)))
        .group_by(ItemText.item_id)
        .subquery("text_raw")
    )


def decode_offset(cursor: str | None) -> int:
    from .search import SEMANTIC_CURSOR, BadCursor, decode_cursor

    if not cursor:
        return 0
    value, marker = decode_cursor(cursor)
    if marker not in (RANKED_CURSOR, SEMANTIC_CURSOR) or not isinstance(value, int) or value < 0:
        raise BadCursor("invalid cursor for a ranked search; start again without one")
    return value


def ranked_page(
    session: Session, filters: list, sig: Signals, limit: int, cursor: str | None,
    *, collapse_stacks: bool = False,
) -> tuple[list[Item], str | None, dict[str, dict], int | None]:
    """One page of a ranked search: (items, next_cursor, {item_id: match},
    total). total is counted in the same query, on the first page only.

    collapse_stacks (stacks=collapse) collapses after ranking: each stack
    keeps only its best-ranked member the caller can see, at that member's
    place (the member that matched, so its match and snippet are true; its
    stack_id and stack_count say there is more). The plain search shows
    the cover instead, because there nothing ranks.

    How (M6 perf; tests/test_m6_perf.py compares it with the query it
    replaced): one narrow row per matching item (id, stack_id, each
    signal's score and whether it hit), the colour and text signals joined
    in as the filters they are; each signal's rank (only when several are
    fused) and the stack position as window functions over those rows,
    the stack window over stacked rows only; ordering, OFFSET/LIMIT and
    the count on the narrow rows; full items fetched for the page alone.
    It used to join every signal to a CTE of matching ids, rank each
    signal separately, partition every row by stack and fetch every
    matching item's full row before the LIMIT (similar= on 250k items:
    245k full rows, 3.4 s on dev0)."""
    from .search import encode_cursor

    offset = decode_offset(cursor)
    # (kind, what to join, on, score, hit, inner join?): colour and text are
    # filters as well as signals, so they join; the others outer-join
    sources: list[tuple[str, Any, Any, Any, Any, bool]] = []
    if sig.meaning is not None:
        emb, on, score = _vector_source(sig.model, sig.meaning, "meaning")
        sources.append((KIND_MEANING, emb, on, score, emb.item_id.isnot(None), False))
    if sig.similar is not None:
        emb, on, score = _vector_source(sig.model, sig.similar, "similar")
        sources.append((KIND_SIMILAR, emb, on, score, emb.item_id.isnot(None), False))
    if sig.keyword:
        kw = _keyword_scores(sig.keyword, filters)
        sources.append((KIND_KEYWORD, kw, kw.c.id == Item.id, kw.c.score, kw.c.id.isnot(None),
                        False))
    if sig.colour is not None:
        hits = colour_hits(sig.colour)
        sources.append((KIND_COLOR, hits, hits.c.id == Item.id, hits.c.score, sa.true(), True))
    if sig.text:
        txt = _text_scores(sig.text)
        sources.append((KIND_TEXT, txt, txt.c.id == Item.id, txt.c.score, sa.true(), True))
    kinds = [s[0] for s in sources]
    several = len(sources) > 1

    cols: list[Any] = [Item.id.label("id"), Item.stack_id.label("stack_id")]
    stmt = sa.select().select_from(Item)
    for kind, src, on, score, hit, inner in sources:
        stmt = stmt.join(src, on) if inner else stmt.outerjoin(src, on)
        cols += [score.label(f"{kind}_score"), hit.label(f"{kind}_hit")]
    rows = stmt.add_columns(*cols).where(*filters).subquery("signal_rows")

    ranked_cols: list[Any] = list(rows.c)
    if several:  # reciprocal rank fusion needs each signal's rank among the matches
        order_value = sa.literal(0.0)
        for kind in kinds:
            hit, score = rows.c[f"{kind}_hit"], rows.c[f"{kind}_score"]
            rk = sa.case((hit, sa.func.row_number().over(
                partition_by=hit, order_by=(score.desc(), rows.c.id.asc()))))
            ranked_cols.append(rk.label(f"{kind}_rk"))
        ranked = sa.select(*ranked_cols).subquery("ranked_signals")
        for kind in kinds:
            order_value = order_value + sa.func.coalesce(1.0 / (RRF_K + ranked.c[f"{kind}_rk"]),
                                                         0.0)
    else:
        ranked = rows
        order_value = rows.c[f"{kinds[0]}_score"]
    fused_rows = sa.select(*ranked.c, order_value.label("fused")).cte("fused_rows")

    if collapse_stacks:
        # a stack keeps its best-ranked member; items outside stacks all stay
        stack_pos = sa.func.row_number().over(
            partition_by=fused_rows.c.stack_id,
            order_by=(fused_rows.c.fused.desc().nulls_last(), fused_rows.c.id.asc()),
        )
        stacked = (sa.select(*fused_rows.c, stack_pos.label("stack_pos"))
                   .where(fused_rows.c.stack_id.isnot(None)).subquery("stacked"))
        listed = sa.union_all(
            sa.select(*fused_rows.c).where(fused_rows.c.stack_id.is_(None)),
            sa.select(*(stacked.c[c.key] for c in fused_rows.c)).where(stacked.c.stack_pos == 1),
        ).subquery("listed")
    else:
        listed = fused_rows
    page_cols: list[Any] = list(listed.c)
    if offset == 0:
        page_cols.append(sa.func.count().over().label("total"))
    page = (
        sa.select(*page_cols)
        .order_by(listed.c.fused.desc().nulls_last(), listed.c.id.asc())
        .offset(offset)
        .limit(limit + 1)
        .subquery("page")
    )
    out_cols = [Item, *(c for c in page.c if c.key not in ("id", "stack_id"))]
    rows_out = session.execute(
        sa.select(*out_cols).join(page, page.c.id == Item.id)
        .order_by(page.c.fused.desc().nulls_last(), Item.id.asc())
    ).all()
    total = None
    if offset == 0:
        total = rows_out[0].total if rows_out else 0
    next_cursor = None
    if len(rows_out) > limit:
        rows_out = rows_out[:limit]
        next_cursor = encode_cursor(offset + limit, RANKED_CURSOR)

    items: list[Item] = []
    matches: dict[str, dict] = {}
    for row in rows_out:
        item = row[0]
        items.append(item)
        hit = [kind for kind in kinds if getattr(row, f"{kind}_hit")]
        if not hit:
            continue
        if not several:
            kind, score = hit[0], getattr(row, f"{hit[0]}_score")
        else:
            kind = hit[0] if len(hit) == 1 else KIND_HYBRID
            score = row.fused
        matches[item.id] = {"kind": kind, "score": round(float(score or 0.0), 6)}
    snippet_q = sig.text or sig.snippet_q
    if snippet_q:
        sources_ = READ_SOURCES if sig.text else None
        for item_id, snip in snippets(session, [i.id for i in items], snippet_q,
                                      sources_).items():
            if item_id in matches:
                matches[item_id]["snippet"] = snip
    return items, next_cursor, matches, total


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
    text first, then a page snapshot's text, then a caption). An item that
    matched some words elsewhere (the filename, its notes) still gets the
    words its text has marked."""
    out = _snippets(session, item_ids, tsq, sources)
    rest = [i for i in item_ids if i not in out]
    if rest and " & " in tsq:
        out.update(_snippets(session, rest, tsq.replace(" & ", " | "), sources))
    return out


def _snippets(session: Session, item_ids: list[str], tsq: str,
              sources: tuple[str, ...] | None) -> dict[str, str]:
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
        .order_by(ItemText.item_id, sa.case((ItemText.source == "caption", 2),
                                            (ItemText.source == "page", 1), else_=0))
    ).all()
    out: dict[str, str] = {}
    for item_id, _source, snip in rows:
        if item_id not in out and snip:
            out[item_id] = " ".join(snip.split())
    return out
