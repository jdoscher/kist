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


def _legacy_patches():
    from kist.services import search, stacks

    return [
        (search, "tag_condition", legacy_tag_condition),
        (stacks, "representative_filter", legacy_representative_filter),
    ]


@contextlib.contextmanager
def legacy(monkeypatch):
    with monkeypatch.context() as m:
        for module, name, fn in _legacy_patches():
            m.setattr(module, name, fn)
        yield


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
        cover = sa.text("SELECT cover_item_id FROM stacks WHERE id = :s")
        # a trashed cover: the first live member stands in
        x(sa.text("UPDATE items SET deleted_at = now() WHERE id = (%s)" % cover.text),
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
    ]
    if ns:
        cases.append({"tags": [ns]})
    return cases


ORDER_SAMPLE = ("-created", "taken", "-rating", "filename")


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
                        for n, order in enumerate(ORDER_SAMPLE):
                            params = dict(case, stacks=stacks, order=order)
                            limit = 10_000 if n else 97
                            new = _pages(s, params, limit)
                            with legacy(monkeypatch):
                                old = _pages(s, params, limit)
                            assert new == old, (who and who.name, params)
                            checked += 1
                            if who is None and case == {} and stacks:
                                assert new[0][1] > 0
    assert checked == 2 * len(_cases(seeded)) * 2 * len(ORDER_SAMPLE)


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
