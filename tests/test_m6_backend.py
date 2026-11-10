"""Milestone 6 backend: date buckets (B1-B3), unfiled count (B6)."""

from datetime import UTC, datetime

import sqlalchemy as sa


def _dates(rows: dict[str, datetime], col: str = "created_at") -> None:
    from kist.db import SessionLocal
    from kist.models import Item

    with SessionLocal() as s:
        for item_id, when in rows.items():
            s.execute(sa.update(Item).where(Item.id == item_id).values({col: when}))
        s.commit()


def _dt(*a) -> datetime:
    return datetime(*a, tzinfo=UTC)


def _library(upload):
    """9 items over four months, with ties and items exactly on month starts."""
    ids = [upload(tags="m6" if i % 2 else "")["item"]["id"] for i in range(9)]
    when = {
        ids[0]: _dt(2024, 7, 1),  # exactly on July's start: July
        ids[1]: _dt(2024, 6, 30, 23, 59, 59),
        ids[2]: _dt(2024, 6, 15, 12),
        ids[3]: _dt(2024, 6, 15, 12),  # a tie with ids[2]
        ids[4]: _dt(2024, 6, 1),  # exactly on June's start: June
        ids[5]: _dt(2024, 5, 31, 23, 59, 59),
        ids[6]: _dt(2024, 5, 2),
        ids[7]: _dt(2023, 12, 31, 23),
        ids[8]: _dt(2023, 12, 1, 0, 0, 1),
    }
    _dates(when)
    return ids, when


def _all_pages(client, params: dict, limit: int) -> list[str]:
    out, cursor = [], params.pop("cursor", None)
    while True:
        q = {**params, "limit": limit, **({"cursor": cursor} if cursor else {})}
        page = client.get("/api/items", params=q).json()
        out += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            return out


def test_buckets_newest_first_with_cursors_that_start_each_month(client, upload):
    ids, when = _library(upload)
    res = client.get("/api/items/buckets", params={"sort": "created", "order": "desc"})
    assert res.status_code == 200, res.text
    data = res.json()
    assert (data["sort"], data["order"], data["item_order"]) == ("created", "desc", "-created")
    assert data["total"] == 9
    assert [(b["key"], b["count"]) for b in data["buckets"]] == [
        ("2024-07", 1), ("2024-06", 4), ("2024-05", 2), ("2023-12", 2)]

    full = _all_pages(client, {"order": "-created"}, 100)
    assert full == sorted(ids, key=lambda i: (when[i], i), reverse=True)
    for bucket in data["buckets"]:
        month = [i for i in full if when[i].strftime("%Y-%m") == bucket["key"]]
        # B2: GET /api/items takes the cursor unchanged; the month's items come
        # first, and paging (2 at a time, across the tie) runs on into the next
        paged = _all_pages(client, {"order": "-created", "cursor": bucket["cursor"]}, 2)
        assert paged[:len(month)] == month, bucket["key"]
        assert paged == full[full.index(month[0]):], bucket["key"]


def test_buckets_oldest_first(client, upload):
    ids, when = _library(upload)
    data = client.get("/api/items/buckets", params={"sort": "created", "order": "asc"}).json()
    assert [(b["key"], b["count"]) for b in data["buckets"]] == [
        ("2023-12", 2), ("2024-05", 2), ("2024-06", 4), ("2024-07", 1)]
    assert data["item_order"] == "created"
    full = _all_pages(client, {"order": "created"}, 100)
    june = data["buckets"][2]
    paged = _all_pages(client, {"order": "created", "cursor": june["cursor"]}, 3)
    assert paged == full[full.index(ids[4]):]  # June starts with the item on June 1 00:00
    assert paged[:4] == [i for i in full if when[i].strftime("%Y-%m") == "2024-06"]


def test_buckets_take_the_list_order_key_and_every_filter(client, upload):
    ids, when = _library(upload)
    # the taken sort: a date taken moves an item to its month
    _dates({ids[0]: _dt(2019, 3, 4)}, col="taken_at")
    data = client.get("/api/items/buckets", params={"order": "-taken"}).json()
    assert (data["sort"], data["order"], data["item_order"]) == ("taken", "desc", "-taken")
    assert [b["key"] for b in data["buckets"]] == ["2024-06", "2024-05", "2023-12", "2019-03"]
    paged = _all_pages(client, {"order": "-taken", "cursor": data["buckets"][-1]["cursor"]}, 5)
    assert paged == [ids[0]]
    # modified, both ways
    assert client.get("/api/items/buckets", params={"sort": "modified", "order": "asc"}
                      ).json()["item_order"] == "modified"
    assert client.get("/api/items", params={"order": "modified"}).status_code == 200
    from kist.schemas import OrderName
    from kist.services.search import ORDERS

    assert set(OrderName.__args__) == set(ORDERS)  # smart folders can save every order
    # filters: the same as GET /api/items
    tagged = client.get("/api/items/buckets", params={"tags": "m6"}).json()
    listed = client.get("/api/items", params={"tags": "m6"}).json()
    assert tagged["total"] == listed["total"] == 4
    unfiled = client.get("/api/items/buckets", params={"view": "unfiled"}).json()
    assert unfiled["total"] == 5
    assert client.get("/api/items/buckets", params={"type": "pdf"}).json() == {
        "sort": "created", "order": "desc", "item_order": "-created", "total": 0, "buckets": []}
    # not a date sort, or nonsense
    for params in ({"sort": "filename"}, {"order": "-size"}, {"order": "sideways"},
                   {"sort": "taken", "order": "up"}):
        res = client.get("/api/items/buckets", params=params)
        assert res.status_code == 422, (params, res.text)


def test_buckets_respect_a_folder_limited_token(client, upload):
    ids, _ = _library(upload)
    folder = client.post("/api/folders", json={"name": "Shared"}).json()
    client.patch(f"/api/items/{ids[2]}/folders", json={"add": [folder["id"]]})
    secret = client.post("/api/tokens", json={
        "name": "reader", "scopes": ["read"], "folder_ids": [folder["id"]]}).json()["token"]
    res = client.get("/api/items/buckets", headers={"Authorization": f"Bearer {secret}"})
    assert res.status_code == 200, res.text
    assert [(b["key"], b["count"]) for b in res.json()["buckets"]] == [("2024-06", 1)]


def test_mcp_item_buckets(client, upload):
    from test_mcp import call, init

    _library(upload)
    init(client)
    import json

    res = call(client, "item_buckets", {"sort": "created", "order": "asc", "tags": ["m6"]})
    assert not res.get("isError"), res
    data = json.loads(res["content"][0]["text"])
    assert data["item_order"] == "created" and data["total"] == 4
    rest = call(client, "item_buckets", {"sort": "filename"}, id_=11)
    assert rest["isError"] and "date sort" in rest["content"][0]["text"]
    first = data["buckets"][0]
    page = json.loads(call(client, "search_items", {
        "order": "created", "cursor": first["cursor"], "tags": ["m6"], "limit": 100,
    }, id_=12)["content"][0]["text"])
    assert len(page["items"]) == 4


def test_stats_counts_unfiled(client, upload):
    a = upload()["item"]
    upload(tags="x")
    folder = client.post("/api/folders", json={"name": "F"}).json()
    upload(folder_id=folder["id"])
    trashed = upload()["item"]
    assert client.get("/api/stats").json()["unfiled"] == 2
    client.delete(f"/api/items/{trashed['id']}")
    stats = client.get("/api/stats").json()
    assert stats["unfiled"] == 1
    assert stats["unfiled"] == client.get("/api/items", params={"view": "unfiled"}).json()["total"]
    client.patch(f"/api/items/{a['id']}/tags", json={"add": ["done"]})
    assert client.get("/api/stats").json()["unfiled"] == 0


# ---------- B4: media-backfill only does missing work ----------


def _media(item_id):
    from kist.db import SessionLocal
    from kist.models import Item

    with SessionLocal() as s:
        return s.get(Item, item_id).media


def test_media_backfill_remembers_items_with_nothing_to_read(client, upload, capsys):
    from unittest import mock

    from kist import cli, media_cli
    from kist.db import SessionLocal
    from kist.models import Item
    from kist.services import media, previews, thumbs

    png = upload("screenshot.png")["item"]
    txt = upload("notes.txt", b"plain words", "text/plain")["item"]
    # new items are marked when they're read at ingest; the API doesn't show it
    assert _media(png["id"]) == {"checked_rev": media.MEDIA_REV}
    assert _media(txt["id"]) == {"checked_rev": media.MEDIA_REV}
    assert png["media"] is None and txt["media"] is None

    with SessionLocal() as s:  # as Kist 2.0 M5 left them: no media, no marker
        s.execute(sa.update(Item).values(media=None))
        s.commit()
    calls = []
    real = previews.regenerate

    def counting(session, item):
        calls.append(item.id)
        return real(session, item)

    with mock.patch.object(previews, "regenerate", counting):
        assert media_cli.media_backfill(None, False) == 0
        assert sorted(calls) == sorted([png["id"], txt["id"]])
        assert _media(png["id"]) == {"checked_rev": media.MEDIA_REV}
        assert _media(txt["id"]) == {"checked_rev": media.MEDIA_REV}
        calls.clear()
        capsys.readouterr()
        assert media_cli.media_backfill(None, False) == 0
        assert calls == [] and "nothing to do" in capsys.readouterr().out

        # the marker is in meta.json: it survives rebuild-index, verify is clean
        assert cli.rebuild_index(wipe=True) == 0
        assert _media(png["id"]) == {"checked_rev": media.MEDIA_REV}
        assert cli.verify() == 0
        assert media_cli.media_backfill(None, False) == 0 and calls == []

        # --all still does everything
        assert media_cli.media_backfill(None, True) == 0
        assert sorted(calls) == sorted([png["id"], txt["id"]])

    # a read that fails leaves the item unmarked, so the next run retries it
    with SessionLocal() as s:
        s.execute(sa.update(Item).where(Item.id == png["id"]).values(media=None))
        s.commit()
    with mock.patch.object(thumbs, "generate_previews_isolated",
                           side_effect=RuntimeError("preview generation failed (exit -11)")):
        media_cli.media_backfill(None, False)
    assert _media(png["id"]) is None
    with SessionLocal() as s:
        ids = s.execute(sa.select(Item.id).where(media.needs_backfill())).scalars().all()
    assert ids == [png["id"]]
    # a later reader version (MEDIA_REV bump) picks up items checked before it
    with mock.patch.object(media, "MEDIA_REV", media.MEDIA_REV + 1):
        with SessionLocal() as s:
            ids = s.execute(sa.select(Item.id).where(media.needs_backfill())).scalars().all()
        assert sorted(ids) == sorted([png["id"], txt["id"]])


# ---------- B5: first-install library permissions ----------


def test_library_problem_names_the_owner_and_the_fix(tmp_path, monkeypatch):
    import os

    from kist import preflight

    lib = tmp_path / "library"
    # missing but creatable, then present and writable: fine
    assert preflight.library_problem(lib, (lib / "items",)) is None
    lib.mkdir()
    assert preflight.library_problem(lib, (lib / "items",)) is None
    # owned by someone else / read-only (what a root-created ./library looks like)
    lib.chmod(0o555)
    try:
        msg = preflight.library_problem(lib, host_path="/srv/kist/library", uid=1000, gid=1000)
        assert msg == (f"Kist can't write to {lib} (owned by uid {os.stat(lib).st_uid}, "
                       "Kist runs as uid 1000): run  sudo chown -R 1000:1000 /srv/kist/library"
                       "  or set KIST_UID/KIST_GID")
        # a missing subdirectory under it can't be created either
        monkeypatch.setenv("KIST_LIBRARY", "./library")
        msg = preflight.library_problem(tmp_path / "ok", (lib / "items",))
        assert msg and f"write to {lib} " in msg and "chown -R" in msg and "./library" in msg
    finally:
        lib.chmod(0o755)
    # a writable library with one root-owned subdirectory (an earlier root run)
    items = lib / "items"
    items.mkdir()
    items.chmod(0o555)
    try:
        assert f"write to {items} " in preflight.library_problem(lib, (items,))
    finally:
        items.chmod(0o755)


def test_preflight_exits_with_one_line(tmp_path, monkeypatch, capfd):
    from kist import preflight, worker
    from kist.config import get_settings

    lib = tmp_path / "library"
    lib.mkdir()
    lib.chmod(0o555)
    monkeypatch.setattr(get_settings(), "library_dir", lib)
    try:
        assert preflight.main() == 1
        err = capfd.readouterr().err.strip().splitlines()
        assert len(err) == 1 and err[0].startswith(f"Kist can't write to {lib} ")
        # the worker stops before touching anything
        import pytest

        with pytest.raises(SystemExit) as stop:
            worker.main()
        assert stop.value.code == 1
        logged = capfd.readouterr().err.strip().splitlines()
        assert len(logged) == 1 and "ERROR kist.preflight" in logged[0]
        assert logged[0].endswith("or set KIST_UID/KIST_GID")
    finally:
        lib.chmod(0o755)
    assert preflight.main() == 0


def test_app_csp_allows_a_same_origin_service_worker():
    """W2's service worker (/sw.js): with no worker-src or child-src, CSP
    falls back to script-src, which allows 'self'. Kept without an explicit
    worker-src so web workers stay under the same script-src as pages."""
    from kist.headers import app_csp

    for csp in (app_csp(), app_csp(shell=True)):
        rules = dict(r.strip().split(" ", 1) for r in csp.split(";"))
        allowed = rules.get("worker-src") or rules.get("child-src") or rules["script-src"]
        assert "'self'" in allowed.split()


# ---------- B7: the benchmark harness (scripts/bench) ----------


def _bench_module(name):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "bench" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"bench_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_make_library_writes_a_consistent_synthetic_library(client, clean):
    import os

    make = _bench_module("make_library")
    url = os.environ["KIST_DATABASE_URL"]
    assert make.main(["--items", "400", "--tags", "60", "--folders", "12",
                      "--database-url", url, "--library", str(clean)]) == 0
    # never on top of a library that has items
    assert make.main(["--items", "5", "--no-files", "--database-url", url]) == 2

    stats = client.get("/api/stats").json()
    assert stats["total_items"] + stats["trashed_items"] == 400
    assert stats["tag_count"] == 60 and stats["folder_count"] == 12
    page = client.get("/api/items", params={"limit": 5, "stacks": "collapse"}).json()
    buckets = client.get("/api/items/buckets", params={"order": "-taken",
                                                       "stacks": "collapse"}).json()
    assert buckets["total"] == page["total"] > 0
    # thumbnails are served from the hard-linked template
    item = page["items"][0]
    thumb = client.get(f"/api/items/{item['id']}/thumb")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"
    # nothing for the background backfills to pick up
    from kist.db import SessionLocal
    from kist.models import Item
    from kist.services import media

    with SessionLocal() as s:
        assert s.execute(sa.select(sa.func.count()).select_from(Item).where(
            media.needs_backfill())).scalar_one() == 0
        assert s.execute(sa.select(sa.func.count()).select_from(Item).where(
            Item.thumbhash.is_(None), Item.preview_status == "ready")).scalar_one() == 0
    # colour, text and similar searches find something
    assert client.get("/api/items", params={"color": "#808080", "color_tol": 100}
                      ).json()["items"]
    smart = client.get("/api/smart-folders").json()
    assert "Smart 0" in {sf["name"] for sf in smart}


def test_bench_run_helpers():
    from kist.services.search import decode_cursor

    run = _bench_module("run")
    assert decode_cursor(run.crafted_cursor("m", "")) == ("m", "")
    assert decode_cursor(run.crafted_cursor(0, "~")) == (0, "~")
    assert run.pct([5, 1, 3, 2, 4], 50) == 3 and run.pct(list(range(1, 101)), 95) == 95
    assert run.main(["--url", "https://kist.example.com", "--token", "x"]) == 2
