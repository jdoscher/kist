import json

from conftest import png_bytes


def test_upload_creates_item_files_and_meta(upload, clean):
    item = upload("red.png")["item"]
    assert item["kind"] == "image"
    assert (item["width"], item["height"]) == (640, 480)
    item_dir = clean / "items" / item["id"]
    for name in ("original.png", "thumb_256.webp", "thumb_512.webp", "thumb_1024.webp"):
        assert (item_dir / name).is_file(), name
    meta = json.loads((item_dir / "meta.json").read_text())
    assert meta["id"] == item["id"] and meta["sha256"] == item["sha256"]


def test_exact_duplicate_returns_existing(client, upload):
    data = png_bytes((1, 2, 3))
    first = upload("a.png", data)
    second = upload("b.png", data)
    assert first["created"] and not second["created"]
    assert second["item"]["id"] == first["item"]["id"]


def test_upload_import_metadata(client, upload):
    created = upload(
        "old.png",
        tags="Cats, dogs",
        notes="hello",
        rating="4",
        source_url="https://example.com/x",
        created_at="2019-05-01T12:00:00Z",
    )["item"]
    assert created["tags"] == ["Cats", "dogs"]  # case is kept; uniqueness ignores it
    assert created["rating"] == 4
    assert created["source_url"] == "https://example.com/x"
    assert created["created_at"].startswith("2019-05-01")


def test_upload_with_deleted_at_lands_in_trash(client, upload):
    item = upload("gone.png", deleted_at="2020-01-01T00:00:00Z")["item"]
    live = client.get("/api/items").json()
    trash = client.get("/api/items?deleted=true").json()
    assert item["id"] not in [i["id"] for i in live["items"]]
    assert item["id"] in [i["id"] for i in trash["items"]]


def test_patch_tags_and_filters(client, upload):
    a = upload("alpha.png")["item"]
    b = upload("beta.png")["item"]
    c = upload("gamma.png")["item"]
    client.put(f"/api/items/{a['id']}/tags", json={"tags": ["red", "warm"]})
    client.put(f"/api/items/{b['id']}/tags", json={"tags": ["blue"]})
    client.patch(f"/api/items/{c['id']}", json={"rating": 5, "filename": "renamed.png"})

    def ids(query):
        return {i["id"] for i in client.get(f"/api/items?{query}").json()["items"]}

    assert ids("tags=red,warm") == {a["id"]}
    assert ids("tags_any=red,blue") == {a["id"], b["id"]}
    assert ids("untagged=true") == {c["id"]}
    assert ids("rating=5") == {c["id"]}
    assert ids("q=renamed") == {c["id"]}
    assert ids("type=video") == set()


def test_cursor_pagination_visits_every_item_once(client, upload):
    made = {upload(f"p{i}.png")["item"]["id"] for i in range(7)}
    seen, cursor = [], None
    while True:
        url = "/api/items?limit=3" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(url).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == sorted(made)


def test_trash_restore_and_hard_delete(client, upload, clean):
    item = upload()["item"]
    assert client.delete(f"/api/items/{item['id']}").json()["status"] == "trashed"
    assert client.get("/api/items").json()["total"] == 0
    client.post(f"/api/items/{item['id']}/restore")
    assert client.get("/api/items").json()["total"] == 1
    client.delete(f"/api/items/{item['id']}?hard=true")
    assert client.get(f"/api/items/{item['id']}").status_code == 404
    assert not (clean / "items" / item["id"]).exists()


def test_thumb_and_file_endpoints(client, upload):
    item = upload()["item"]
    for size in (256, 512, 1024):
        res = client.get(f"/api/items/{item['id']}/thumb?size={size}")
        assert res.status_code == 200 and res.headers["content-type"] == "image/webp"
    res = client.get(f"/api/items/{item['id']}/file")
    assert res.headers["content-type"] == "image/png"
    assert res.headers["content-disposition"].startswith("inline")
    assert res.headers["x-content-type-options"] == "nosniff"


SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    b'<script>alert(1)</script><rect width="10" height="10"/></svg>'
)
HTML = b"<!doctype html><html><body><script>alert(1)</script></body></html>"


def test_active_content_cannot_run_scripts(client, upload):
    svg = upload("evil.svg", SVG, "image/svg+xml")["item"]
    res = client.get(f"/api/items/{svg['id']}/file")
    assert "sandbox" in res.headers["content-security-policy"]

    page = upload("evil.html", HTML, "text/html")["item"]
    assert page["kind"] == "other"
    res = client.get(f"/api/items/{page['id']}/file")
    assert res.headers["content-disposition"].startswith("attachment")


def test_unknown_formats_are_stored_without_preview(client, upload):
    item = upload("notes.txt", b"just text\n", "text/plain")["item"]
    assert item["kind"] == "other" and not item["has_thumb"]


def test_stats(client, upload):
    upload()
    upload()
    stats = client.get("/api/stats").json()
    assert stats["total_items"] == 2 and stats["by_kind"]["image"] == 2
