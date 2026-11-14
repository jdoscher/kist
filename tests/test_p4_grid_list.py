"""The slim grid list (Kist 2.0 performance work, P4): GET /api/items?fields=grid
answers the same pages as the full list (ids, order, totals, cursors, match
kinds) with only the fields the web grid reads (schemas.GridItemOut), and
every one of those fields equals the full item's (narrowed where documented).
"""

import json
import os

import pytest

GRID_FIELDS = {
    "id", "filename", "ext", "mime", "kind", "size_bytes", "width", "height", "duration_s",
    "dominant_colors", "thumbhash", "folder_ids", "tags", "rating", "protected", "flag",
    "label", "state", "preview_status", "preview_rev", "has_thumb", "created_at",
    "modified_at", "taken_at", "match", "stack_id", "stack_count", "provenance", "media",
}


@pytest.fixture
def synthetic(client, clean):
    """make_library's synthetic library (700 items, no files), plus a few
    items with the narrowed blocks (AI-made provenance, scrub, cut-out)."""
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa

    from kist.db import SessionLocal

    path = Path(__file__).resolve().parent.parent / "scripts" / "bench" / "make_library.py"
    spec = importlib.util.spec_from_file_location("bench_make_library_p4", path)
    make = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make)
    assert make.main(["--items", "700", "--tags", "40", "--folders", "10", "--no-files",
                      "--database-url", os.environ["KIST_DATABASE_URL"],
                      "--library", str(clean)]) == 0
    with SessionLocal() as s:
        ids = [r[0] for r in s.execute(sa.text(
            "SELECT id FROM items WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT 3"))]
        s.execute(sa.text("UPDATE items SET provenance = CAST(:p AS jsonb) WHERE id = :i"), {
            "i": ids[0], "p": json.dumps({"ai_generated": True, "ai_generator": "Midjourney",
                                          "creator": "Someone", "license": "CC-BY"})})
        s.execute(sa.text("UPDATE items SET provenance = CAST(:p AS jsonb) WHERE id = :i"), {
            "i": ids[1], "p": json.dumps({"ai_generated": False, "creator": "Me"})})
        s.execute(sa.text("UPDATE items SET media = media || CAST(:m AS jsonb) WHERE id = :i"), {
            "i": ids[2], "m": json.dumps({
                "scrub": {"frames": 10, "cols": 5, "rows": 2, "frame_w": 160, "frame_h": 90},
                "cutout": {"source_item": ids[0], "model": "u2net"},
                "video": {"codec": "h264", "fps": 30}})})
        s.commit()
        tags = [r[0] for r in s.execute(sa.text(
            "SELECT t.name FROM tags t JOIN item_tags it ON it.tag_id = t.id "
            "GROUP BY t.name ORDER BY count(*) DESC, t.name"))]
        folders = [r[0] for r in s.execute(sa.text("SELECT id FROM folders ORDER BY id"))]
    return {"tags": tags, "folders": folders, "special": ids}


def make_token(client, name, scopes, **extra):
    res = client.post("/api/tokens", json={"name": name, "scopes": list(scopes), **extra})
    assert res.status_code == 201, res.text
    return res.json()


def as_token(client, plaintext):
    from fastapi.testclient import TestClient

    return TestClient(client.app, headers={"Authorization": f"Bearer {plaintext}"})


def _all_pages(client, params: dict, limit: int):
    out, cursor = [], None
    for _ in range(1000):
        q = {**params, "limit": limit, **({"cursor": cursor} if cursor else {})}
        res = client.get("/api/items", params=q)
        assert res.status_code == 200, (params, res.text)
        page = res.json()
        out.append(page)
        cursor = page["next_cursor"]
        if not cursor:
            return out
    raise AssertionError("too many pages")


def _cases(lib):
    tags, folders = lib["tags"], lib["folders"]
    return [
        {},
        {"stacks": "collapse"},
        {"stacks": "collapse", "order": "-taken"},
        {"order": "filename"},
        {"order": "-rating", "stacks": "collapse"},
        {"order": "-size"},
        {"order": "-modified"},
        {"order": "created"},
        {"tags": tags[0], "stacks": "collapse"},
        {"tags": f"{tags[1]},{tags[2]}"},
        {"tags_any": f"{tags[3]},{tags[10]}"},
        {"untagged": "true"},
        {"view": "unfiled", "stacks": "collapse"},
        {"folder": folders[0]},
        {"folder": "none"},
        {"type": "image", "rating": 3},
        {"state": "trashed"},
        {"q": "IMG"},
        {"flag": "pick"},
        {"label": "red"},
        {"has_gps": "true", "order": "-taken"},
    ]


def test_slim_list_has_the_same_pages_as_the_full_list(client, synthetic):
    checked = 0
    for params in _cases(synthetic):
        for limit in (37, 120):
            full = _all_pages(client, params, limit)
            slim = _all_pages(client, {**params, "fields": "grid"}, limit)
            assert len(full) == len(slim), params
            for f, g in zip(full, slim, strict=True):
                assert [i["id"] for i in f["items"]] == [i["id"] for i in g["items"]], params
                assert f["total"] == g["total"], params
                assert f["next_cursor"] == g["next_cursor"], params
                assert [i["match"] for i in f["items"]] == [i["match"] for i in g["items"]], params
                checked += len(g["items"])
    assert checked > 3000


def test_slim_items_are_the_full_items_fields(client, synthetic):
    full = {i["id"]: i for i in client.get("/api/items", params={"limit": 500}).json()["items"]}
    slim = client.get("/api/items", params={"limit": 500, "fields": "grid"}).json()["items"]
    assert len(slim) == len(full) == 500
    for g in slim:
        f = full[g["id"]]
        assert set(g) == GRID_FIELDS
        for key in GRID_FIELDS - {"dominant_colors", "provenance", "media"}:
            assert g[key] == f[key], (key, g["id"])
        # the first colour only
        colours = f["dominant_colors"]
        assert g["dominant_colors"] == (colours[:1] if colours else colours)
        # only the grid's media blocks
        media = f["media"] or {}
        assert g["media"] == ({k: media[k] for k in ("scrub", "cutout") if k in media} or None)
    # AI-made: the badge's keys only; not AI-made: nothing
    ai, human, video = synthetic["special"]
    by = {i["id"]: i for i in slim}
    assert by[ai]["provenance"] == {"ai_generated": True, "ai_generator": "Midjourney"}
    assert full[ai]["provenance"]["creator"] == "Someone"
    assert by[human]["provenance"] == {}
    assert set(by[video]["media"]) == {"scrub", "cutout"}
    assert "video" in full[video]["media"]
    # what the grid leaves to GET /api/items/{id}
    one = client.get(f"/api/items/{ai}").json()
    for key in ("notes", "sha256", "custom_fields", "source_url", "caption", "implied_tags"):
        assert key in one and key not in by[ai]


def test_slim_list_is_smaller(client, synthetic):
    full = client.get("/api/items", params={"limit": 200, "stacks": "collapse"})
    slim = client.get("/api/items", params={"limit": 200, "stacks": "collapse", "fields": "grid"})
    assert len(slim.content) < len(full.content) * 0.7


def test_default_list_is_unchanged_and_fields_is_checked(client, synthetic):
    page = client.get("/api/items", params={"limit": 3}).json()
    assert {"notes", "sha256", "provenance", "custom_fields", "thumb_url"} <= set(page["items"][0])
    assert client.get("/api/items", params={"fields": "everything"}).status_code == 422
    # ids= (compare) ignores fields and stays full
    ids = ",".join(i["id"] for i in page["items"])
    got = client.get("/api/items", params={"ids": ids, "fields": "grid"}).json()
    assert "notes" in got["items"][0]


def test_folder_limited_reader_gets_its_folders_only_in_the_slim_list(client, upload):
    a = client.post("/api/folders", json={"name": "A"}).json()
    b = client.post("/api/folders", json={"name": "B"}).json()
    ab = upload("ab.png", folder_ids=json.dumps([a["id"], b["id"]]))["item"]
    upload("b.png", folder_ids=json.dumps([b["id"]]))
    created = make_token(client, "Family", ["read"], folder_ids=[a["id"]])
    fam = as_token(client, created["token"])
    page = fam.get("/api/items", params={"fields": "grid"}).json()
    assert [i["id"] for i in page["items"]] == [ab["id"]] and page["total"] == 1
    assert page["items"][0]["folder_ids"] == [a["id"]]


def test_search_by_image_takes_fields_grid(client, upload, monkeypatch):
    from kist.api import search as search_api
    from kist.services import ai, search
    from kist.services.items import get_item

    first = upload("x.png")["item"]
    monkeypatch.setattr(ai, "ml_embed_image", lambda data: [0.0] * 512)

    def fake_search(session, **kw):
        match = {first["id"]: {"kind": "similar", "score": 0.9}}
        return search.SearchResult(items=[get_item(session, first["id"])], next_cursor=None,
                                   total=1, matches=match)

    monkeypatch.setattr(search_api.search, "search", fake_search)
    files = {"file": ("q.png", b"not read: the embedding is stubbed", "image/png")}
    full = client.post("/api/search/image", files=files).json()
    slim = client.post("/api/search/image", params={"fields": "grid"}, files=files).json()
    assert [i["id"] for i in slim["items"]] == [i["id"] for i in full["items"]] == [first["id"]]
    assert set(slim["items"][0]) == GRID_FIELDS and "notes" in full["items"][0]
    expected = {"kind": "similar", "score": 0.9, "snippet": None}
    assert slim["items"][0]["match"] == full["items"][0]["match"] == expected
