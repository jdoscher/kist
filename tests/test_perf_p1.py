"""Perf P1: thumbnails without a database round trip, and the token cache."""

import io
import json
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from conftest import png_bytes
from fastapi.testclient import TestClient

FOREVER = "private, max-age=31536000, immutable"
AGAIN = "private, no-cache"


@contextmanager
def count_sql():
    """Count the SQL statements run while the block runs (every engine)."""
    from sqlalchemy.engine import Engine

    seen: list[str] = []

    def hook(conn, cursor, statement, *a):
        seen.append(statement)

    sa.event.listen(Engine, "before_cursor_execute", hook)
    try:
        yield seen
    finally:
        sa.event.remove(Engine, "before_cursor_execute", hook)


def as_token(client, plaintext):
    return TestClient(client.app, headers={"Authorization": f"Bearer {plaintext}"})


def make_token(client, name, scopes=("read",), **extra):
    res = client.post("/api/tokens", json={"name": name, "scopes": list(scopes), **extra})
    assert res.status_code == 201, res.text
    return res.json()


def test_owner_thumbnail_runs_no_sql(client, upload):
    item = upload("a.png", png_bytes(size=(1200, 800)))["item"]
    url = f"/api/items/{item['id']}/thumb?size=512&v={item['preview_rev']}"
    client.get(url)
    with count_sql() as seen:
        for size in (256, 512, 1024, 2048):
            r = client.get(f"/api/items/{item['id']}/thumb?size={size}&v={item['preview_rev']}")
            assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert seen == []


def test_named_token_is_looked_up_once_then_cached(client, upload):
    item = upload("a.png")["item"]
    reader = as_token(client, make_token(client, "Phone")["token"])
    url = f"/api/items/{item['id']}/thumb?v={item['preview_rev']}"
    with count_sql() as first:
        assert reader.get(url).status_code == 200
    assert first  # the token lookup
    with count_sql() as again:
        for _ in range(5):
            assert reader.get(url).status_code == 200
    assert again == []


def test_cache_control_without_a_rev_check(client, upload, clean):
    item = upload("big.png", png_bytes(size=(3000, 2000)))["item"]
    v = item["preview_rev"]
    url = f"/api/items/{item['id']}/thumb"
    assert client.get(f"{url}?size=512&v={v}").headers["cache-control"] == FOREVER
    # any version is cached once the requested file exists: the UI builds the
    # URL from the current preview_rev, so an odd v only ever holds newer bytes
    assert client.get(f"{url}?size=512&v={v + 7}").headers["cache-control"] == FOREVER
    assert client.get(f"{url}?size=512").headers["cache-control"] == AGAIN
    # a stand-in (512 missing -> 1024) is never cached for good
    (clean / "items" / item["id"] / "thumb_512.webp").unlink()
    r = client.get(f"{url}?size=512&v={v}")
    assert r.status_code == 200 and r.headers["cache-control"] == AGAIN
    for name in ("thumb_256.webp", "thumb_1024.webp", "display_2048.webp"):
        (clean / "items" / item["id"] / name).unlink()
    assert client.get(f"{url}?size=256&v={v}").status_code == 404
    assert client.get(f"{url}?size=512&v={v}").status_code == 404


def test_bytes_are_the_file(client, upload, clean):
    item = upload("a.png", png_bytes(size=(1500, 900)))["item"]
    d = clean / "items" / item["id"]
    for size, name in ((256, "thumb_256.webp"), (512, "thumb_512.webp"),
                       (1024, "thumb_1024.webp"), (2048, "display_2048.webp")):
        r = client.get(f"/api/items/{item['id']}/thumb?size={size}")
        assert r.content == (d / name).read_bytes(), name
        assert int(r.headers["content-length"]) == len(r.content)
    # a big file (the 4096 HEIC copy) is streamed, same bytes
    big = b"RIFF" + bytes(range(256)) * 9000  # ~2.3 MB, over READ_WHOLE_MAX
    (d / "display.webp").write_bytes(big)
    r = client.get(f"/api/items/{item['id']}/thumb?size=4096")
    assert r.status_code == 200 and r.content == big


@pytest.mark.parametrize("bad", [
    "..", "%2e%2e", "..%2fsecret", "01abcdefghjkmnpqrstvwxyz00",  # lowercase
    "81ARZ3NDEKTSV4RRFFQ69G5FAV",  # first character over 7: not a ULID
    "01ARZ3NDEKTSV4RRFFQ69G5FA", "01ARZ3NDEKTSV4RRFFQ69G5FAVX",  # 25 / 27
    "01ARZ3NDEKTSV4RRFFQ69G5FAU",  # U is not Crockford base 32
    "01ARZ3NDEKTSV4RRFFQ69G5F%2F", "01ARZ3NDEKTSV4RRFFQ69G5FA%00",
])
def test_malformed_ids_never_reach_the_file_system(client, clean, bad):
    # a decoy: something a traversal would find
    (clean / "thumb_256.webp").write_bytes(b"secret")
    (clean / "items").mkdir(exist_ok=True)
    for route in ("thumb", "cutout-preview"):
        r = client.get(f"/api/items/{bad}/{route}")
        assert r.status_code == 404, (bad, route, r.status_code)
        assert b"secret" not in r.content


def test_unknown_item_is_404(client, clean):
    from kist.ids import new_ulid

    assert client.get(f"/api/items/{new_ulid()}/thumb").status_code == 404


def test_thumbfiles_item_id_pattern_matches_new_ulids():
    from kist.api.thumbfiles import ITEM_ID
    from kist.ids import new_ulid

    assert all(ITEM_ID.fullmatch(new_ulid()) for _ in range(500))


@pytest.fixture
def family(client, upload):
    a = client.post("/api/folders", json={"name": "A"}).json()
    b = client.post("/api/folders", json={"name": "B"}).json()
    items = {
        "a": upload("a.png", folder_ids=json.dumps([a["id"]]))["item"],
        "b": upload("b.png", folder_ids=json.dumps([b["id"]]))["item"],
        "loose": upload("loose.png")["item"],
        "trashed": upload("t.png", folder_ids=json.dumps([a["id"]]),
                          deleted_at="2026-01-01T00:00:00Z")["item"],
    }
    fam = as_token(client, make_token(client, "Family", folder_ids=[a["id"]])["token"])
    return fam, {"A": a, "B": b}, items


def test_folder_limited_reader_keeps_the_full_check(client, family):
    fam, _folders, items = family
    for key in ("b", "loose", "trashed"):
        for route in ("thumb", "thumb?size=1024", "thumb?size=2048", "cutout-preview"):
            r = fam.get(f"/api/items/{items[key]['id']}/{route}")
            assert r.status_code == 404, (key, route)
    with count_sql() as seen:
        assert fam.get(f"/api/items/{items['a']['id']}/thumb").status_code == 200
    assert any("items" in s for s in seen)  # it loaded the item
    # the owner still sees trashed and unfiled items' thumbnails
    for key in ("b", "loose", "trashed"):
        assert client.get(f"/api/items/{items[key]['id']}/thumb").status_code == 200


def test_folder_moves_reach_a_cached_reader_at_once(client, family):
    fam, folders, items = family
    url = f"/api/items/{items['b']['id']}/thumb"
    assert fam.get(url).status_code == 404  # (its principal is cached now)
    # B moves under A: its items show up right away, not after the cache's TTL
    r = client.patch(f"/api/folders/{folders['B']['id']}", json={"parent_id": folders["A"]["id"]})
    assert r.status_code == 200, r.text
    assert fam.get(url).status_code == 200
    # and out again: hidden right away
    client.patch(f"/api/folders/{folders['B']['id']}", json={"parent_id": None})
    assert fam.get(url).status_code == 404


def test_revoking_a_token_takes_effect_at_once(client, upload):
    item = upload("a.png")["item"]
    made = make_token(client, "Laptop", ["write"])
    t = as_token(client, made["token"])
    url = f"/api/items/{item['id']}/thumb"
    assert t.get(url).status_code == 200
    assert t.get(url).status_code == 200  # cached
    assert client.delete(f"/api/tokens/{made['id']}").status_code == 200
    assert t.get(url).status_code == 401
    assert t.get("/api/items").status_code == 401


def test_a_cached_token_expires_on_time(client, upload, monkeypatch):
    from kist.services import tokens as tokens_svc

    item = upload("a.png")["item"]
    made = make_token(client, "Soon", expires_at=(datetime.now(UTC) + timedelta(seconds=30))
                      .isoformat())
    t = as_token(client, made["token"])
    url = f"/api/items/{item['id']}/thumb"
    assert t.get(url).status_code == 200  # cached for CACHE_SECONDS from now
    later = datetime.now(UTC) + timedelta(seconds=31)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return later

    monkeypatch.setattr(tokens_svc, "datetime", Clock)
    assert t.get(url).status_code == 401


def test_the_cache_holds_for_a_few_seconds_only(client, upload, monkeypatch):
    from kist.db import SessionLocal
    from kist.models import ApiToken
    from kist.services import tokens as tokens_svc

    item = upload("a.png")["item"]
    made = make_token(client, "Gone")
    t = as_token(client, made["token"])
    url = f"/api/items/{item['id']}/thumb"
    assert t.get(url).status_code == 200
    # another process deletes the row behind this one's back
    with SessionLocal() as s:
        s.execute(sa.delete(ApiToken).where(ApiToken.id == made["id"]))
        s.commit()
    assert t.get(url).status_code == 200  # still cached here
    now = time.monotonic()
    monkeypatch.setattr(tokens_svc.time, "monotonic", lambda: now + tokens_svc.CACHE_SECONDS + 0.1)
    assert t.get(url).status_code == 401
    assert tokens_svc.CACHE_SECONDS <= 5


def test_a_wrong_token_is_never_cached(client, upload):
    item = upload("a.png")["item"]
    wrong = as_token(client, "kist_" + "x" * 43)
    for _ in range(2):
        assert wrong.get(f"/api/items/{item['id']}/thumb").status_code == 401


def test_cutout_preview_from_the_file_without_sql(client, upload, clean):
    from PIL import Image

    buf = io.BytesIO()
    im = Image.new("RGBA", (800, 600), (200, 40, 40, 255))
    im.putalpha(Image.new("L", (800, 600), 128))
    im.save(buf, "PNG")
    item = upload("alpha.png", buf.getvalue())["item"]
    url = f"/api/items/{item['id']}/cutout-preview?size=1024&v={item['preview_rev']}"
    first = client.get(url)  # makes alpha_1024.webp (database path)
    assert first.status_code == 200 and first.headers["cache-control"] == FOREVER
    with count_sql() as seen:
        r = client.get(url)
    assert seen == [] and r.status_code == 200 and r.content == first.content
    assert r.headers["cache-control"] == FOREVER
    # no transparency: still a 404
    plain = upload("plain.png")["item"]
    assert client.get(f"/api/items/{plain['id']}/cutout-preview").status_code == 404


def test_bench_thumbs_refuses_the_real_library():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "bench" / "thumbs.py"
    spec = importlib.util.spec_from_file_location("bench_thumbs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--url", "https://kist.example.com", "--token", "x"]) == 2
