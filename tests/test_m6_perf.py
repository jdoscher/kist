"""Speed at 250k items (2.0 M6 perf, branch m6p): every optimised read path
returns what it returned before.

Each optimisation keeps the SQL it replaced here (LEGACY, verbatim from
f4cc6d5) as the reference, and test_results_match_the_old_queries runs
every case both ways on a seeded synthetic library
(scripts/bench/make_library.py plus vocabulary rules, stacks with trashed,
missing or foreign covers, and a folder-limited reader), comparing every
page and the total, so a faster plan can't quietly change a result.
"""

import contextlib

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import aliased


def test_kist_connections_run_without_jit(database):
    """LLVM JIT cost 250-800 ms per listing on dev0's N100 (EXPLAIN ANALYZE
    "JIT: Total 776 ms" on a 1.2 s tag page); the app's engine turns it off."""
    from kist.db import SessionLocal, engine

    with engine.connect() as conn:
        assert conn.execute(sa.text("SHOW jit")).scalar_one() == "off"
    with SessionLocal() as s:
        assert s.execute(sa.text("SHOW jit")).scalar_one() == "off"


# ---------- the queries as they were (f4cc6d5) ----------


def legacy_tag_condition(names, session=None):
    from kist.models import Item, ItemTag, Tag
    from kist.services.vocabulary import is_wildcard, matching_names

    conds = []
    for name in names:
        ns = is_wildcard(name)
        if ns is not None:
            escaped = ns.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conds.append(Tag.name.ilike(f"{escaped}:%", escape="\\"))
        else:
            conds.append(Tag.name.in_(matching_names(name)))
    return sa.exists().where(ItemTag.item_id == Item.id, ItemTag.tag_id == Tag.id, sa.or_(*conds))


def legacy_representative_filter():
    from kist.models import Item, Stack
    from kist.services.stacks import _visible_cond

    cover = aliased(Item, name="stack_cover")
    member = aliased(Item, name="stack_member")
    cover_id = (
        sa.select(Stack.cover_item_id)
        .join(cover, cover.id == Stack.cover_item_id)
        .where(Stack.id == Item.stack_id, *_visible_cond(cover))
        .scalar_subquery()
    )
    first_id = (
        sa.select(sa.func.min(member.id))
        .where(member.stack_id == Item.stack_id, *_visible_cond(member))
        .scalar_subquery()
    )
    return sa.or_(Item.stack_id.is_(None), Item.id == sa.func.coalesce(cover_id, first_id))


def legacy_keyset_page(session, filters, order, limit, cursor):
    from kist.models import Item
    from kist.services.ranking import RANKED_CURSOR
    from kist.services.search import (
        ORDERS,
        SEMANTIC_CURSOR,
        BadCursor,
        decode_cursor,
        encode_cursor,
    )

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
            stmt = stmt.where(sa.or_(col < value, sa.and_(col == value, Item.id < last_id)))
        else:
            stmt = stmt.where(sa.or_(col > value, sa.and_(col == value, Item.id > last_id)))
    rows = list(session.execute(stmt.limit(limit + 1)).scalars())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor(getattr(last, col.key), last.id)
    return rows, next_cursor


def legacy_list_with_counts(session, namespace=None):
    from kist.models import Item, ItemTag, Tag
    from kist.services.tags import tag_dict

    rows = session.execute(
        sa.select(Tag.id, Tag.name, sa.func.count(Item.id).label("count"))
        .outerjoin(ItemTag, ItemTag.tag_id == Tag.id)
        .outerjoin(Item, sa.and_(Item.id == ItemTag.item_id, Item.deleted_at.is_(None)))
        .group_by(Tag.id, Tag.name)
        .order_by(sa.text("count DESC"), Tag.name)
    ).all()
    out = [tag_dict(r.id, r.name, r.count) for r in rows]
    if namespace is not None:
        want = None if namespace.lower() in ("", "none") else namespace.lower()
        out = [t for t in out if t["namespace"] == want]
    return out


def legacy_list_folders(session):
    from kist import access
    from kist.models import Folder, Item, ItemFolder
    from kist.services.folders import folder_dict, subtree_counts

    folders = session.execute(
        sa.select(Folder).order_by(Folder.position, Folder.name)
    ).scalars().all()
    memberships = session.execute(
        sa.select(ItemFolder.item_id, ItemFolder.folder_id)
        .join(Item, Item.id == ItemFolder.item_id)
        .where(Item.deleted_at.is_(None))
    ).all()
    counts = subtree_counts(list(folders), [tuple(m) for m in memberships])
    reader = access.restricted()
    if reader is not None:
        return [
            {**folder_dict(f, counts.get(f.id, 0)),
             "parent_id": f.parent_id if f.parent_id in reader.visible_folders else None}
            for f in folders if f.id in reader.visible_folders
        ]
    return [folder_dict(f, counts.get(f.id, 0)) for f in folders]


def legacy_keyword_condition(q):
    from kist.models import Item
    from kist.services.ranking import annotation_match, text_match, tsquery

    pattern = f"%{q}%"
    conds = [
        Item.filename.ilike(pattern),
        sa.func.similarity(Item.filename, q) > 0.25,
        Item.notes.ilike(pattern),
        Item.place.ilike(pattern),
        annotation_match(pattern),
    ]
    tsq = tsquery(q)
    if tsq:
        conds.append(text_match(tsq))
    return sa.or_(*conds)


def _legacy_patches():
    from kist.services import folders, ranking, search, stacks, tags

    return [
        (search, "tag_condition", legacy_tag_condition),
        (stacks, "representative_filter", legacy_representative_filter),
        (search, "_keyset_page", legacy_keyset_page),
        (tags, "list_with_counts", legacy_list_with_counts),
        (folders, "list_folders", legacy_list_folders),
        (ranking, "keyword_condition", legacy_keyword_condition),
        (ranking, "ranked_page", legacy_ranked_page),
    ]


@contextlib.contextmanager
def legacy(monkeypatch):
    with monkeypatch.context() as m:
        for module, name, fn in _legacy_patches():
            m.setattr(module, name, fn)
        yield


# ---------- ranked search as it was (f4cc6d5) ----------

from typing import Any  # noqa: E402

from sqlalchemy.orm import Session  # noqa: E402

from kist.models import HalfVec, Item, ItemColor, ItemEmbedding, ItemText  # noqa: E402
from kist.services import colour  # noqa: E402
from kist.services.ranking import (  # noqa: E402
    KIND_COLOR,
    KIND_HYBRID,
    KIND_KEYWORD,
    KIND_MEANING,
    KIND_SIMILAR,
    KIND_TEXT,
    RANKED_CURSOR,
    READ_SOURCES,
    RRF_K,
    ColourQuery,
    Signals,
    _keyword_score,
    _tsq,
    _tsv,
    decode_offset,
    snippets,
    text_match,
    tsquery,
)


def legacy_colour_hits(cq: ColourQuery):
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


def legacy_ranked(sub_id, score, name: str):
    """(id, score, rk) for one signal."""
    return sa.select(
        sub_id.label("id"),
        score.label("score"),
        sa.func.row_number().over(order_by=(score.desc(), sub_id.asc())).label("rk"),
    ).subquery(name)


def legacy_vector_signal(base, model: str, vec: list[float], name: str):
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
    return legacy_ranked(inner.c.id, inner.c.score, name)


def legacy_keyword_signal(base, q: str):
    inner = (
        sa.select(Item.id.label("id"), _keyword_score(q, tsquery(q)).label("score"))
        .join(base, base.c.id == Item.id)
        .where(legacy_keyword_condition(q))
        .subquery("keyword_raw")
    )
    return legacy_ranked(inner.c.id, inner.c.score, "keyword")


def legacy_colour_signal(base, hits):
    inner = (
        sa.select(hits.c.id, hits.c.score).join(base, base.c.id == hits.c.id)
        .subquery("color_raw")
    )
    return legacy_ranked(inner.c.id, inner.c.score, "color")


def legacy_text_signal(base, tsq: str):
    inner = (
        sa.select(ItemText.item_id.label("id"),
                  sa.func.max(sa.func.ts_rank_cd(_tsv(ItemText.text), _tsq(tsq))).label("score"))
        .join(base, base.c.id == ItemText.item_id)
        .where(ItemText.source.in_(READ_SOURCES), _tsv(ItemText.text).op("@@")(_tsq(tsq)))
        .group_by(ItemText.item_id)
        .subquery("text_raw")
    )
    return legacy_ranked(inner.c.id, inner.c.score, "text")


def legacy_ranked_page(
    session: Session, filters: list, sig: Signals, limit: int, cursor: str | None,
    *, collapse_stacks: bool = False,
) -> tuple[list[Item], str | None, dict[str, dict], int | None]:
    """One page of a ranked search: (items, next_cursor, {item_id: match},
    total). total is counted in the same query, on the first page only.

    collapse_stacks (stacks=collapse) collapses after ranking: each stack
    keeps only its best-ranked member the caller can see, at that member's
    place (the member that matched, so its match and snippet are true; its
    stack_id and stack_count say there is more). The plain search shows
    the cover instead, because there nothing ranks."""
    from kist.services.search import encode_cursor

    offset = decode_offset(cursor)
    filters = list(filters)
    hits = None
    if sig.colour is not None:
        hits = legacy_colour_hits(sig.colour)
        filters.append(Item.id.in_(sa.select(hits.c.id)))
    if sig.text:
        filters.append(text_match(sig.text, READ_SOURCES))
    base = sa.select(Item.id.label("id")).where(*filters).cte("base")
    signals: list[tuple[str, Any]] = []
    if sig.meaning is not None:
        signals.append((KIND_MEANING,
                        legacy_vector_signal(base, sig.model, sig.meaning, "meaning")))
    if sig.similar is not None:
        signals.append((KIND_SIMILAR,
                        legacy_vector_signal(base, sig.model, sig.similar, "similar")))
    if sig.keyword:
        signals.append((KIND_KEYWORD, legacy_keyword_signal(base, sig.keyword)))
    if hits is not None:
        signals.append((KIND_COLOR, legacy_colour_signal(base, hits)))
    if sig.text:
        signals.append((KIND_TEXT, legacy_text_signal(base, sig.text)))

    inner_cols: list[Any] = [Item.id.label("ranked_id")]
    for kind, sub in signals:
        inner_cols += [sub.c.score.label(f"{kind}_score"), sub.c.rk.label(f"{kind}_rk")]
    if len(signals) == 1:
        sub = signals[0][1]
        order_value = sub.c.score
    else:
        order_value = sa.literal(0.0)
        for _, sub in signals:
            order_value = order_value + sa.func.coalesce(1.0 / (RRF_K + sub.c.rk), 0.0)
    inner_cols.append(order_value.label("fused"))
    if collapse_stacks:
        inner_cols.append(sa.func.row_number().over(
            partition_by=sa.func.coalesce(Item.stack_id, Item.id),
            order_by=(order_value.desc().nulls_last(), Item.id.asc()),
        ).label("stack_pos"))
    inner = sa.select(*inner_cols).join(base, base.c.id == Item.id)
    for _, sub in signals:
        inner = inner.outerjoin(sub, sub.c.id == Item.id)
    ranked = inner.subquery("ranked")
    cols: list[Any] = [Item, *(c for c in ranked.c if c.key not in ("ranked_id", "stack_pos"))]
    if offset == 0:
        cols.append(sa.func.count().over().label("total"))
    stmt = sa.select(*cols).join(ranked, ranked.c.ranked_id == Item.id)
    if collapse_stacks:
        stmt = stmt.where(ranked.c.stack_pos == 1)
    stmt = (
        stmt.order_by(ranked.c.fused.desc().nulls_last(), Item.id.asc())
        .offset(offset)
        .limit(limit + 1)
    )
    rows = session.execute(stmt).all()
    total = None
    if offset == 0:
        total = rows[0].total if rows else 0
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
    return items, next_cursor, matches, total


# ---------- a seeded library ----------


@pytest.fixture
def seeded(clean):
    """make_library's synthetic library (1,500 items) plus the corner cases
    the optimised filters must keep: tag aliases and implications (a chain),
    a namespace, stacks whose cover is trashed, missing, outside the stack
    or outside a folder-limited reader's folders, and fully trashed stacks."""
    import importlib.util
    import os
    from pathlib import Path

    from kist.db import SessionLocal

    path = Path(__file__).resolve().parent.parent / "scripts" / "bench" / "make_library.py"
    spec = importlib.util.spec_from_file_location("bench_make_library_perf", path)
    make = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make)
    assert make.main(["--items", "1500", "--tags", "60", "--folders", "12", "--no-files",
                      "--database-url", os.environ["KIST_DATABASE_URL"],
                      "--library", str(clean)]) == 0

    with SessionLocal() as s:
        x = s.execute
        tags = [r[0] for r in x(sa.text(
            "SELECT t.name FROM tags t JOIN item_tags it ON it.tag_id = t.id "
            "GROUP BY t.name ORDER BY count(*) DESC, t.name"))]
        # vocabulary: an alias, and a chain of implications (c -> b -> a)
        a, b, c = tags[5], tags[20], tags[40]
        x(sa.text("INSERT INTO tag_aliases (alias, tag) VALUES ('perf-alias', :t)"), {"t": a})
        x(sa.text("INSERT INTO tag_implies (tag, implied) VALUES (:b, :a), (:c, :b)"),
          {"a": a, "b": b, "c": c})
        # make more stacks than make_library's 1.5 %: pairs of untagged items
        free = [r[0] for r in x(sa.text(
            "SELECT id FROM items WHERE stack_id IS NULL ORDER BY id LIMIT 120"))]
        for n in range(0, len(free), 3):
            sid = f"PERFSTACK{n:017d}"
            x(sa.text("INSERT INTO stacks (id, cover_item_id) VALUES (:s, :c)"),
              {"s": sid, "c": free[n + 1]})
            x(sa.text("UPDATE items SET stack_id = :s WHERE id = ANY(:ids)"),
              {"s": sid, "ids": free[n:n + 3]})
        stacks = [r[0] for r in x(sa.text("SELECT id FROM stacks ORDER BY id"))]
        assert len(stacks) >= 30
        # a trashed cover: the first live member stands in
        x(sa.text("UPDATE items SET deleted_at = now() "
                  "WHERE id = (SELECT cover_item_id FROM stacks WHERE id = :s)"),
          {"s": stacks[0]})
        # no cover at all
        x(sa.text("UPDATE stacks SET cover_item_id = NULL WHERE id = :s"), {"s": stacks[1]})
        # a cover that isn't a member (inconsistent, but the filter must agree)
        x(sa.text("UPDATE stacks SET cover_item_id = (SELECT id FROM items WHERE stack_id "
                  "IS NULL AND deleted_at IS NULL ORDER BY id LIMIT 1) WHERE id = :s"),
          {"s": stacks[2]})
        # every member trashed
        x(sa.text("UPDATE items SET deleted_at = now() WHERE stack_id = :s"), {"s": stacks[3]})
        # covers outside the reader's folders, members inside
        folders = [r[0] for r in x(sa.text(
            "SELECT f.id FROM folders f WHERE parent_id IS NULL ORDER BY "
            "(SELECT count(*) FROM folders c WHERE c.parent_id = f.id) DESC, f.id"))]
        reader_root = folders[0]
        for sid in stacks[4:12]:
            members = [r[0] for r in x(sa.text(
                "SELECT id FROM items WHERE stack_id = :s ORDER BY id"), {"s": sid})]
            x(sa.text("DELETE FROM item_folders WHERE item_id = ANY(:ids)"), {"ids": members})
            for m in members[:-1] if sid != stacks[4] else members:
                x(sa.text("INSERT INTO item_folders (item_id, folder_id) VALUES (:i, :f) "
                          "ON CONFLICT DO NOTHING"), {"i": m, "f": reader_root})
        # keyword search fields: notes, annotations (place comes seeded)
        some = [r[0] for r in x(sa.text(
            "SELECT id FROM items WHERE deleted_at IS NULL ORDER BY id DESC LIMIT 40"))]
        for n, item_id in enumerate(some[:20]):
            x(sa.text("UPDATE items SET notes = :n WHERE id = :i"),
              {"n": ["invoice from the hotel", "50% off", "Hotel Paris receipt",
                     "a_b test"][n % 4], "i": item_id})
        for n, item_id in enumerate(some[20:]):
            x(sa.text("INSERT INTO annotations (id, item_id, kind, text) VALUES "
                      "(:id, :i, 'point', :t)"),
              {"id": f"PERFANN{n:019d}", "i": item_id,
               "t": ["invoice total", "see the logo", "zebra"][n % 3]})
        # filenames by trigram similarity to "hotels": 0.294 and 0.278 (only
        # found when `%` uses a threshold <= 0.25), and exactly 0.25 (not > 0.25)
        for item_id, name in zip(some[:3], ("hotel_bill.jpg", "hotel_lobby.jpg",
                                            "hotel_receipt.jpg"), strict=True):
            x(sa.text("UPDATE items SET filename = :f WHERE id = :i"), {"f": name, "i": item_id})
        x(sa.text("ANALYZE"))
        s.commit()
        yield {"tags": tags, "alias": "perf-alias", "implied": a, "chain": (a, b, c),
               "stacks": stacks, "folders": folders, "reader_root": reader_root}


def _reader(session, root):
    from kist import access
    from kist.services.search import folder_subtree

    visible = frozenset(session.execute(sa.select(folder_subtree(root).c.id)).scalars())
    return access.Principal("perf-reader", frozenset({"read"}), folder_ids=(root,),
                            visible_folders=visible)


def _pages(session, params, limit):
    """Every page of a search: [(ids, total)] following next_cursor."""
    from kist.services import search

    out, cursor = [], None
    for _ in range(10_000):
        r = search.search(session, **params, limit=limit, cursor=cursor)
        out.append(([i.id for i in r.items], r.total,
                     {k: v.get("kind") for k, v in r.matches.items()}))
        cursor = r.next_cursor
        if not cursor:
            return out
    raise AssertionError("too many pages")


def _cases(lib):
    tags, folders = lib["tags"], lib["folders"]
    ns = tags[0].split(":")[0] + ":*" if ":" in tags[0] else None
    cases = [
        {},
        {"tags": [tags[0]]},
        {"tags": [tags[30]]},
        {"tags": [tags[-1]]},
        {"tags": [lib["implied"]]},
        {"tags": [lib["alias"]]},
        {"tags": [lib["chain"][1]]},
        {"tags": [tags[0], tags[1]]},
        {"tags": ["no-such-tag"]},
        {"tags_any": tags[:3]},
        {"tags_any": [lib["alias"], tags[50], "no-such-tag"]},
        {"tags_any": ["no-such-tag"]},
        {"untagged": True},
        {"view": "unfiled"},
        {"folder": folders[0]},
        {"folder": folders[-1]},
        {"folder": "none"},
        {"kind": "image", "rating_min": 3},
        {"tags": [tags[2]], "folder": folders[0], "kind": "image"},
        {"deleted": True},
        {"state": "trashed"},
        {"q": "invoice"},
        {"q": "hotel"},
        {"q": "paris"},
        {"q": "IMG_40"},
        {"q": "img 4029"},
        {"q": "screenshot 2024"},
        {"q": "50%"},
        {"q": "a_b"},
        {"q": "ab"},
        {"q": "zebra"},
        {"q": "hotels"},
        {"q": "zzzzqqq"},
        {"q": "logo", "tags": [tags[1]]},
        {"q": "update", "folder": folders[0], "kind": "image"},
    ]
    if ns:
        cases.append({"tags": [ns]})
    return cases


# every list order; the small page sizes walk many cursors (ties in rating
# and size included)
ORDER_PAGES = {"-created": 97, "created": 400, "-modified": 400, "modified": 400,
               "-taken": 211, "taken": 400, "filename": 97, "-size": 400, "-rating": 97}


def test_results_match_the_old_queries(seeded, monkeypatch):
    """Every case, plain and stacks=collapse, for the owner and a
    folder-limited reader, in several orders: the same pages, cursors and
    totals as the f4cc6d5 queries."""
    from kist import access
    from kist.db import SessionLocal

    checked = 0
    with SessionLocal() as s:
        reader = _reader(s, seeded["reader_root"])
        for who in (None, reader):
            with access.acting_as(who):
                for case in _cases(seeded):
                    for stacks in (None, "collapse"):
                        for order, limit in ORDER_PAGES.items():
                            params = dict(case, stacks=stacks, order=order)
                            new = _pages(s, params, limit)
                            with legacy(monkeypatch):
                                old = _pages(s, params, limit)
                            assert new == old, (who and who.name, params)
                            checked += 1
                            if who is None and case == {} and stacks:
                                assert new[0][1] > 0
    assert checked == 2 * len(_cases(seeded)) * 2 * len(ORDER_PAGES)


def test_keyword_similarity_uses_the_index_threshold(seeded):
    """keyword_condition's `filename % q` arm needs pg_trgm's threshold at
    0.25 (db.DB_OPTIONS): similarity 0.294 and 0.278 match, exactly 0.25
    doesn't, as with the old `similarity() > 0.25` on every row."""
    from kist.db import SessionLocal
    from kist.services import search

    with SessionLocal() as s:
        s.execute(sa.text("SELECT similarity('a', 'b')"))  # loads pg_trgm
        assert s.execute(sa.text("SHOW pg_trgm.similarity_threshold")).scalar_one() == "0.25"
        names = {i.filename for i in search.search(s, q="hotels", limit=10_000).items}
    assert {"hotel_bill.jpg", "hotel_lobby.jpg"} <= names
    assert "hotel_receipt.jpg" not in names


def test_stack_reps_function_matches_migration():
    """services/stacks.py names kist_stack_reps; migration 0801 makes it."""
    import importlib.util
    from pathlib import Path

    path = (Path(__file__).resolve().parent.parent / "migrations" / "versions"
            / "0801_stack_reps_function.py")
    spec = importlib.util.spec_from_file_location("m0801", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "kist_stack_reps(visible_folders text[])" in mod.STACK_REPS
    assert mod.down_revision == "0701"


def test_collapse_corner_cases_are_seeded(seeded):
    """The seeded stacks really exercise the fallbacks the filter has."""
    from kist import access
    from kist.db import SessionLocal
    from kist.services import search

    with SessionLocal() as s:
        st = seeded["stacks"]
        shown = {i.id for i in search.search(s, stacks="collapse", limit=100_000).items}
        members = dict(s.execute(sa.text(
            "SELECT stack_id, array_agg(id ORDER BY id) FROM items WHERE deleted_at IS NULL "
            "AND stack_id IS NOT NULL GROUP BY stack_id")).all())
        assert members[st[0]][0] in shown  # trashed cover: the first live member
        assert members[st[1]][0] in shown  # no cover: the first member
        assert not shown & set(members[st[2]])  # a foreign cover hides the stack
        assert st[3] not in members  # all trashed
        with access.acting_as(_reader(s, seeded["reader_root"])):
            seen = {i.id for i in search.search(s, stacks="collapse", limit=100_000).items}
        assert seen & set(members[st[5]])


def _sidebar_urls(lib):
    tags = lib["tags"]
    ns = tags[0].split(":")[0] if ":" in tags[0] else "none"
    return [
        "/api/tags", "/api/tags?namespace=none", f"/api/tags?namespace={ns}",
        "/api/folders", "/api/stats",
        "/api/items/buckets?sort=taken&order=desc&stacks=collapse",
        f"/api/items/buckets?sort=created&order=asc&stacks=collapse&tags={tags[1]}",
        "/api/timeline?group=day", f"/api/timeline?group=month&tags={lib['implied']}",
        f"/api/timeline?group=day&tags_any={tags[3]},{lib['alias']}&stacks=collapse",
        "/api/map?zoom=2", f"/api/map?zoom=3&tags={tags[0]}",
    ]


def test_sidebar_and_listing_reads_match_the_old_queries(seeded, client, monkeypatch):
    """Tags list, sidebar folders (owner and a folder-limited reader),
    stats, month buckets, timeline and map: the same JSON as before."""
    from kist import access
    from kist.db import SessionLocal
    from kist.services import folders

    for url in _sidebar_urls(seeded):
        new = client.get(url)
        with legacy(monkeypatch):
            old = client.get(url)
        assert new.status_code == old.status_code == 200, url
        assert new.json() == old.json(), url
    with SessionLocal() as s, access.acting_as(_reader(s, seeded["reader_root"])):
        new = folders.list_folders(s)
        with legacy(monkeypatch):
            old = folders.list_folders(s)
        assert new == old and new
    counts = {f["id"]: f["item_count"] for f in client.get("/api/folders").json()}
    assert counts[seeded["reader_root"]] > 0


def _ranked_cases(s, lib):
    from kist.services import ai

    model = ai.active_model()
    embedded = [r[0] for r in s.execute(sa.text(
        "SELECT e.item_id FROM item_embeddings e JOIN items i ON i.id = e.item_id "
        "WHERE e.model = :m AND i.deleted_at IS NULL ORDER BY e.item_id LIMIT 3"), {"m": model})]
    assert len(embedded) == 3
    vec = ai.item_vector(s, embedded[2], model)
    tags, folders = lib["tags"], lib["folders"]
    return [
        {"color": "#3366cc"},
        {"color": "#808080", "color_tol": 100},
        {"color": "#ff0000", "color_tol": 10},
        {"color": "#3366cc", "tags": [tags[0]], "kind": "image"},
        {"text": "invoice"},
        {"text": "hotel logo"},
        {"text": "zzzqqq"},
        {"text": "screenshot", "folder": folders[0]},
        {"similar": embedded[0]},
        {"similar": embedded[1], "tags": [lib["implied"]]},
        {"image_vector": vec},
        {"similar": embedded[0], "q": "invoice"},
        {"image_vector": vec, "q": "hotel", "color": "#808080", "color_tol": 60},
        {"similar": embedded[1], "text": "update", "color": "#3366cc"},
        {"color": "#808080", "color_tol": 60, "text": "logo"},
    ]


def test_ranked_results_match_the_old_queries(seeded, monkeypatch):
    """Colour, text, more-like-this, search by image and their fusions
    (with q as a hybrid signal): the same pages, scores, match kinds,
    snippets and totals as the f4cc6d5 ranked query, plain and
    stacks=collapse, for the owner and a folder-limited reader."""
    from kist import access
    from kist.db import SessionLocal

    checked = 0
    with SessionLocal() as s:
        cases = _ranked_cases(s, seeded)
        vec = cases[10]["image_vector"]
        reader = _reader(s, seeded["reader_root"])
        for who in (None, reader):
            with access.acting_as(who):
                for case in cases:
                    if who is not None and "similar" in case:  # it may not see that item
                        case = {k: v for k, v in case.items() if k != "similar"} | {
                            "image_vector": vec}
                    for stacks in (None, "collapse"):
                        for limit in (37, 100_000):
                            params = dict(case, stacks=stacks)
                            new = _pages_full(s, params, limit)
                            with legacy(monkeypatch):
                                old = _pages_full(s, params, limit)
                            assert new == old, (who and who.name, params, limit)
                            checked += 1
                            if limit > 1000 and not who and not case.get("text") == "zzzqqq":
                                assert new[0][1], params  # something matched
    assert checked == 2 * len(cases) * 2 * 2


def _pages_full(session, params, limit):
    """Every page of a search with the full match dicts."""
    from kist.services import search

    out, cursor = [], None
    for _ in range(10_000):
        r = search.search(session, **params, limit=limit, cursor=cursor)
        out.append(([i.id for i in r.items], r.total, r.matches))
        cursor = r.next_cursor
        if not cursor:
            return out
    raise AssertionError("too many pages")


def test_colour_prefilter_keeps_every_match(seeded):
    """colour_hits' lightness bound drops only colours CIEDE2000 would
    reject: 60k random palette colours, queries from black to white and
    saturated blues (CIEDE2000's rotation term) at tolerances 0-100, the
    same hits and scores as the old candidate box alone."""
    from kist.db import SessionLocal
    from kist.services.ranking import ColourQuery, colour_hits

    with SessionLocal() as s:
        s.execute(sa.text(
            "INSERT INTO item_colors (item_id, idx, l, a, b, pct) "
            "SELECT i.id, 100 + g, random() * 100, random() * 256 - 128, random() * 256 - 128, "
            "random() FROM (SELECT id FROM items ORDER BY id LIMIT 1500) i, "
            "generate_series(1, 40) g"))
        for hex_ in ("#000000", "#ffffff", "#3366cc", "#1030e8", "#0047ab", "#ff0000",
                     "#808080", "#2e9e44", "#ffd60a", "#7b2cbf"):
            for tol in (0, 30, 70, 100):
                cq = ColourQuery.parse(hex_, tol)
                new = dict(s.execute(sa.select(colour_hits(cq))).all())
                old = dict(s.execute(sa.select(legacy_colour_hits(cq))).all())
                assert new == old, (hex_, tol)
                assert new or tol == 0, (hex_, tol)
        s.rollback()


def test_rating_order_pages_through_the_library_with_real_cursors(seeded, client):
    """The dev0 benchmark's "deep page order=-rating" came back empty: its
    crafted cursor used "~" as an id past every ULID, which holds in the C
    collation but not in en_US (punctuation sorts first). Real cursors page
    through every live item exactly once, and the bench's new sentinel works
    in either collation."""
    from kist.db import SessionLocal

    seen, cursor = [], None
    while True:
        page = client.get("/api/items", params={"order": "-rating", "limit": 113,
                                                **({"cursor": cursor} if cursor else {})}).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    with SessionLocal() as s:
        live = s.execute(sa.text("SELECT count(*) FROM items WHERE deleted_at IS NULL")).scalar()
        # what the benchmark's crafted cursor asks for, in a non-C collation too
        for coll in ('"C"', '"und-x-icu"'):
            n = s.execute(sa.text(
                f"SELECT count(*) FROM items WHERE deleted_at IS NULL AND (rating, id COLLATE "
                f"{coll}) < (0, 'ZZZZZZZZZZZZZZZZZZZZZZZZZZ' COLLATE {coll})")).scalar()
            zero = s.execute(sa.text(
                "SELECT count(*) FROM items WHERE deleted_at IS NULL AND rating = 0")).scalar()
            assert n == zero > 0, coll
    assert len(seen) == len(set(seen)) == live
