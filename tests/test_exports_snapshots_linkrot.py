"""Portable BagIt export, image export presets, page snapshots and the
link-rot watch."""

import hashlib
import io
import json
import socket
import zipfile
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import png_bytes
from PIL import Image

from kist import worker
from kist.config import get_settings
from kist.services import exports, linkrot, snapshots


def run_jobs():
    while worker.run_one():
        pass


def exif_jpeg_with_gps() -> bytes:
    im = Image.new("RGB", (400, 300), (200, 100, 50))
    exif = Image.Exif()
    exif[0x010F] = "CameraMaker"  # Make
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (52.0, 22.0, 1.0)
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


# ---------- BagIt ----------


def test_bagit_export_of_a_folder(client, upload):
    parent = client.post("/api/folders", json={"name": "Trips"}).json()
    child = client.post("/api/folders", json={"name": "Rome/2024", "parent_id": parent["id"]})
    child = child.json()
    a = upload("colosseum.png", folder_id=child["id"], tags="rome", notes="<b>wow</b>")["item"]
    b = upload("colosseum.jpg", data=png_bytes((1, 2, 3)), folder_id=child["id"])["item"]
    upload("elsewhere.png")
    res = client.post("/api/exports", json={"target": "folder", "target_id": parent["id"],
                                            "include": {"events": True}})
    assert res.status_code == 202, res.text
    export_id = res.json()["id"]
    assert client.get(f"/api/exports/{export_id}").json()["status"] == "pending"
    run_jobs()
    status = client.get(f"/api/exports/{export_id}").json()
    assert status["status"] == "done" and status["item_count"] == 2, status
    dl = client.get(status["download_url"])
    assert dl.status_code == 200 and dl.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(dl.content))
    names = set(zf.namelist())
    assert {"bagit.txt", "bag-info.txt", "manifest-sha256.txt", "tagmanifest-sha256.txt",
            "items.csv", "index.html"} <= names
    # folder names with a slash are made safe; readable, unique names
    import csv

    rows = {r["id"]: r for r in csv.DictReader(io.StringIO(zf.read("items.csv").decode()))}
    base = "data/Trips/Rome_2024"
    # same stem, different extension: still unique names (the .xmp is per stem)
    assert {rows[a["id"]]["path"], rows[b["id"]]["path"]} == {
        f"{base}/colosseum.jpg", f"{base}/colosseum (2).png"} or {
        rows[a["id"]]["path"], rows[b["id"]]["path"]} == {
        f"{base}/colosseum.png", f"{base}/colosseum (2).jpg"}
    a_path = rows[a["id"]]["path"]
    a_stem = a_path.rsplit(".", 1)[0]
    assert f"{a_path}.json" in names and f"{a_stem}.xmp" in names
    assert f"{a_path}.events.jsonl" in names
    assert f"thumbs/{a['id']}.webp" in names
    assert not any("elsewhere" in n for n in names)
    assert zf.read("bagit.txt").decode().startswith("BagIt-Version: 1.0")
    info = zf.read("bag-info.txt").decode()
    assert "Payload-Oxum:" in info and "Bagging-Date:" in info
    # every payload file is in the manifest with its real hash
    manifest = dict(reversed(line.split("  ", 1))
                    for line in zf.read("manifest-sha256.txt").decode().splitlines())
    payload = [n for n in names if n.startswith("data/")]
    assert sorted(manifest) == sorted(payload)
    for path, digest in manifest.items():
        assert hashlib.sha256(zf.read(path)).hexdigest() == digest
    tags = dict(reversed(line.split("  ", 1))
                for line in zf.read("tagmanifest-sha256.txt").decode().splitlines())
    assert "manifest-sha256.txt" in tags and "index.html" in tags
    assert zf.read(a_path) == (
        get_settings().items_dir / a["id"] / "original.png").read_bytes()
    index = zf.read("index.html").decode()
    assert "colosseum.png" in index and "<b>wow</b>" not in index and "&lt;b&gt;wow" in index
    assert f'src="thumbs/{a["id"]}.webp"' in index
    csv_text = zf.read("items.csv").decode()
    assert a["id"] in csv_text and b["id"] in csv_text and "rome" in csv_text


def test_export_without_originals_and_of_a_selection(client, upload):
    a = upload("one.png")["item"]
    upload("two.png")
    res = client.post("/api/exports", json={"target": "items", "item_ids": [a["id"]],
                                            "include": {"originals": False, "sidecars": False}})
    run_jobs()
    export_id = res.json()["id"]
    zf = zipfile.ZipFile(io.BytesIO(client.get(f"/api/exports/{export_id}?download=1").content))
    data = [n for n in zf.namelist() if n.startswith("data/")]
    assert data == ["data/Unfiled/one.webp"]


def test_export_input_is_checked(client):
    for body in ({"target": "folder"}, {"target": "folder", "target_id": "01GHOST"},
                 {"target": "items", "item_ids": []}, {"target": "everything"},
                 {"target": "all", "format": "tar"}, {"target": "all", "include": {"x": True}}):
        assert client.post("/api/exports", json=body).status_code == 422, body
    assert client.get("/api/exports/not-an-id").status_code == 404
    # the client normalises the dots away: /tokens is the web app's page when
    # web/dist is built, a 404 otherwise; never the tokens file
    res = client.get("/api/exports/../../tokens")
    assert res.status_code == 404 or res.headers["content-type"].startswith("text/html")


def test_old_exports_are_deleted(client, upload, monkeypatch):
    upload("a.png")
    export_id = client.post("/api/exports", json={"target": "all"}).json()["id"]
    run_jobs()
    assert exports.zip_path(export_id).is_file()
    assert exports.purge_expired() == 0
    assert exports.purge_expired(datetime.now(UTC) + timedelta(days=8)) == 1
    assert not exports.zip_path(export_id).exists()
    assert client.get(f"/api/exports/{export_id}").status_code == 404


def test_export_names_are_safe():
    assert exports.safe_component("../../etc/passwd") == "_.._etc_passwd"
    assert exports.safe_component("..") == "untitled"
    assert exports.safe_component("a\nb\x00c") == "a_b_c"
    assert exports.bagit_path("data/50%\nx") == "data/50%25%0Ax"


# ---------- export presets ----------


def test_export_images_resizes_and_strips_gps(client, upload):
    item = upload("photo.jpg", data=exif_jpeg_with_gps(), mime="image/jpeg")["item"]
    res = client.post("/api/items/export-images", json={
        "item_ids": [item["id"]],
        "preset": {"max_side": 100, "format": "webp", "quality": 70}})
    assert res.status_code == 200, res.text
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    assert zf.namelist() == ["photo.webp"]
    out = Image.open(io.BytesIO(zf.read("photo.webp")))
    assert max(out.size) == 100 and out.format == "WEBP"
    assert not out.getexif()
    # keeping metadata still drops the location
    res = client.post("/api/items/export-images", json={
        "item_ids": [item["id"]], "preset": {"format": "jpg", "strip_metadata": False}})
    out = Image.open(io.BytesIO(zipfile.ZipFile(io.BytesIO(res.content)).read("photo.jpg")))
    exif = out.getexif()
    assert exif.get(0x010F) == "CameraMaker"
    assert 0x8825 not in exif and not exif.get_ifd(0x8825)


def test_watermark_and_saved_presets(client, upload):
    item = upload("w.png", data=png_bytes((0, 0, 0), (300, 200)))["item"]
    presets = {p["name"] for p in client.get("/api/export-presets").json()}
    assert {"web-2048", "contact-sheet", "client-proof"} <= presets
    res = client.put("/api/export-presets/proof-small", json={
        "max_side": 200, "format": "png",
        "watermark": {"text": "PROOF", "position": "center", "opacity": 1}})
    assert res.status_code == 200, res.text
    assert (get_settings().state_dir / "export-presets.json").is_file()
    out = client.post("/api/items/export-images",
                      json={"item_ids": [item["id"]], "preset": "proof-small"})
    assert out.status_code == 200
    img = Image.open(io.BytesIO(zipfile.ZipFile(io.BytesIO(out.content)).read("w.png")))
    assert img.size == (200, 133)
    assert max(img.convert("L").getdata()) > 200  # white text on the black image
    assert client.delete("/api/export-presets/proof-small").status_code == 200
    bad = [{"max_side": 5}, {"format": "gif"}, {"quality": 0},
           {"watermark": {"text": "", "position": "center"}},
           {"watermark": {"text": "x", "position": "middle"}}, {"contact_sheet": {"cols": 50}},
           {"unknown": 1}]
    for preset in bad:
        res = client.post("/api/items/export-images",
                          json={"item_ids": [item["id"]], "preset": preset})
        assert res.status_code == 422, preset
    assert client.post("/api/items/export-images",
                       json={"item_ids": [item["id"]], "preset": "nope"}).status_code == 422
    assert client.put("/api/export-presets/..%2Fx", json={}).status_code in (404, 405, 422)


def test_contact_sheet_pdf(client, upload):
    ids = [upload(f"c{i}.png", rating=str(i % 5))["item"]["id"] for i in range(5)]
    res = client.post("/api/items/export-images", json={
        "item_ids": ids, "preset": {"contact_sheet": {"cols": 2, "caption": "filename+rating"}}})
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")
    trashed = upload("t.png")["item"]
    client.delete(f"/api/items/{trashed['id']}")
    res = client.post("/api/items/export-images",
                      json={"item_ids": [trashed["id"]], "preset": "web-2048"})
    assert res.status_code == 404


# ---------- page snapshots ----------

PAGE = """<html><head><title>The Article</title>
<script>steal(document.cookie)</script><meta http-equiv="refresh" content="0;url=https://evil">
<base href="https://evil/"></head>
<body onload="alert(1)"><h1>Big tower</h1><p>A long story about a <b>tower</b>.</p>
<a href="javascript:alert(2)">x</a><iframe src="https://evil/frame"></iframe>
<img src="data:image/png;base64,AAAA" onerror='alert(3)'><style>p{color:red}</style>
</body></html>"""


def test_browser_snapshot_is_cleaned_and_searchable_text(client, upload):
    item = upload("t.png", source_url="https://news.test/story")["item"]
    res = client.post(f"/api/items/{item['id']}/snapshot", json={"html": PAGE})
    assert res.status_code == 200, res.text
    info = res.json()
    assert info["title"] == "The Article" and info["source"] == "browser"
    page = client.get(f"/api/items/{item['id']}/snapshot")
    assert page.status_code == 200
    csp = page.headers["content-security-policy"]
    assert csp.startswith("sandbox") and "script-src" not in csp
    body = page.text.lower()
    for bad in ("<script", "onload=", "onerror=", "javascript:", "<iframe", "http-equiv",
                "<base"):
        assert bad not in body, bad
    text = client.get(f"/api/items/{item['id']}/snapshot/text").text
    assert "Big tower" in text and "A long story about a tower." in text
    assert "steal" not in text and "color:red" not in text
    from kist.db import SessionLocal
    from kist.models import ItemText

    with SessionLocal() as s:
        row = s.get(ItemText, (item["id"], "page"))
        assert row is not None and "Big tower" in row.text
    # also as a raw text/html body
    res = client.post(f"/api/items/{item['id']}/snapshot", content=b"<p>second</p>",
                      headers={"Content-Type": "text/html"})
    assert res.status_code == 200
    assert client.get(f"/api/items/{item['id']}/snapshot/text").text == "second"


def test_server_snapshots_are_off_by_default(client, upload):
    item = upload("t.png", source_url="https://news.test/story")["item"]
    res = client.post(f"/api/items/{item['id']}/snapshot")
    assert res.status_code == 409 and "KIST_SNAPSHOTS" in res.text


@pytest.fixture
def public_dns(monkeypatch):
    """Every host name resolves to one public address; nothing leaves the
    machine because requests go to an httpx.MockTransport."""
    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_server_snapshot_goes_through_the_guard(client, upload, monkeypatch, public_dns):
    monkeypatch.setattr(get_settings(), "snapshots", True)
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.url.host, request.headers["host"]))
        return httpx.Response(200, html="<title>Hi</title><p>served page</p>",
                              headers={"content-type": "text/html; charset=utf-8"})

    item = upload("t.png", source_url="https://news.test/story")["item"]
    from kist.db import SessionLocal

    with SessionLocal() as s:
        info = snapshots.fetch(s, item["id"], transport=httpx.MockTransport(handler))
    assert info["source"] == "server" and seen == [("93.184.216.34", "news.test")]
    assert "served page" in snapshots.text_of(item["id"])
    # a private address is refused before any request is made
    private = upload("p.png", source_url="http://192.168.1.1/admin")["item"]
    res = client.post(f"/api/items/{private['id']}/snapshot")
    assert res.status_code == 422 and len(seen) == 1


def test_snapshot_text_survives_a_rebuild(client, upload):
    from kist import cli
    from kist.db import SessionLocal
    from kist.models import ItemText
    from kist.services import verify

    item = upload("t.png")["item"]
    client.post(f"/api/items/{item['id']}/snapshot", json={"html": "<p>kept words</p>"})
    with SessionLocal() as s:
        assert verify.run(s).problems == []
    assert cli.rebuild_index(wipe=True) == 0
    with SessionLocal() as s:
        assert "kept words" in s.get(ItemText, (item["id"], "page")).text
        assert verify.run(s).problems == []
        s.query(ItemText).delete()
        s.commit()
        assert any("page/text.txt" in p for p in verify.run(s).problems)


# ---------- link-rot watch ----------


@pytest.fixture
def link_check_on(monkeypatch):
    """The link-rot watch is opt-in since 2.0 (KIST_LINK_CHECK_DAYS=0)."""
    from kist.config import get_settings

    monkeypatch.setattr(get_settings(), "link_check_days", 7.0)


def test_link_check_records_status(client, upload, public_dns, link_check_on):
    urls = {
        "https://ok.test/a": httpx.Response(200, text="fine"),
        "https://gone.test/b": httpx.Response(404),
        "https://moved.test/c": httpx.Response(301, headers={"location": "https://new.test/c"}),
        "https://new.test/c": httpx.Response(200),
        "https://broken.test/d": httpx.Response(503),
    }

    def handler(request: httpx.Request):
        url = f"https://{request.headers['host']}{request.url.path}"
        return urls[url]

    items = {u: upload(f"{i}.png", source_url=u)["item"]["id"]
             for i, u in enumerate(list(urls)[:3] + ["https://broken.test/d"])}
    upload("nosource.png")
    from kist.db import SessionLocal

    sleeps = []
    with SessionLocal() as s:
        counts = linkrot.run_batch(s, transport=httpx.MockTransport(handler),
                                   sleep=sleeps.append)
    assert counts == {"ok": 1, "moved": 1, "gone": 1, "error": 1}
    assert sleeps and all(t >= 2 for t in sleeps)  # polite
    got = {u: client.get(f"/api/items/{i}").json()["source_status"] for u, i in items.items()}
    assert got == {"https://ok.test/a": "ok", "https://gone.test/b": "gone",
                   "https://moved.test/c": "moved", "https://broken.test/d": "error"}
    gone = client.get("/api/items", params={"source_status": "gone"}).json()["items"]
    assert [i["id"] for i in gone] == [items["https://gone.test/b"]]
    assert client.get("/api/stats").json()["source_gone"] == 1
    # checked items aren't due again for a week; the item's history is untouched
    with SessionLocal() as s:
        assert linkrot.due_ids(s) == []
    kinds = [e["kind"] for e in client.get(
        f"/api/items/{items['https://gone.test/b']}/history").json()]
    assert kinds == ["create"]


def _stamp(client, item_id):
    return client.get(f"/api/items/{item_id}").json()["modified_at"]


def test_link_check_leaves_modified_at(client, upload, public_dns, link_check_on):
    """A source check is bookkeeping: it must not reorder "recently
    modified" or move the base an open editor will send."""
    item = upload("k.png", source_url="https://ok.test/k")["item"]
    base = _stamp(client, item["id"])
    from kist.db import SessionLocal

    with SessionLocal() as s:
        linkrot.run_batch(s, transport=httpx.MockTransport(lambda r: httpx.Response(200)),
                          sleep=lambda t: None)
    got = client.get(f"/api/items/{item['id']}").json()
    assert got["source_status"] == "ok"
    assert got["modified_at"] == base
    # a PATCH based on what the editor saw before the check goes through
    res = client.patch(f"/api/items/{item['id']}",
                       json={"notes": "after the check", "base_modified_at": base})
    assert res.status_code == 200, res.text
    assert res.json()["modified_at"] != base  # a real edit still moves it
    from kist.services import verify
    with SessionLocal() as s:
        assert verify.run(s).problems == []


def test_background_bookkeeping_keeps_modified_at_and_edits_move_it(client, upload):
    from kist.db import SessionLocal
    from kist.models import Item
    from kist.services import previews

    item = upload("b.png")["item"]
    run_jobs()
    base = _stamp(client, item["id"])
    with SessionLocal() as s:  # a preview retry/backfill writes through the ORM
        previews.regenerate(s, s.get(Item, item["id"]))
        assert previews.add_screen_copy(s, s.get(Item, item["id"])) in (True, False)
    assert _stamp(client, item["id"]) == base
    with SessionLocal() as s:  # an ORM edit with no explicit touch still moves it
        row = s.get(Item, item["id"])
        row.notes = "edited"
        s.commit()
        assert row.modified_at > datetime.fromisoformat(base)


def test_wayback_is_opt_in(client, upload, monkeypatch, public_dns, link_check_on):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.headers["host"])
        if request.headers["host"] == "archive.org":
            return httpx.Response(200, json={"archived_snapshots": {"closest": {
                "available": True, "url": "http://web.archive.org/web/2019/https://gone.test/"}}})
        return httpx.Response(410)

    item = upload("g.png", source_url="https://gone.test/")["item"]
    from kist.db import SessionLocal

    with SessionLocal() as s:
        linkrot.run_batch(s, transport=httpx.MockTransport(handler), sleep=lambda t: None)
    assert calls == ["gone.test"]  # archive.org never asked
    monkeypatch.setattr(get_settings(), "wayback", True)
    with SessionLocal() as s:
        s.execute(__import__("sqlalchemy").text("UPDATE items SET source_checked_at = NULL"))
        s.commit()
        linkrot.run_batch(s, transport=httpx.MockTransport(handler), sleep=lambda t: None)
    assert calls[-1] == "archive.org"
    got = client.get(f"/api/items/{item['id']}").json()
    assert got["source_wayback_url"] == "https://web.archive.org/web/2019/https://gone.test/"


def test_link_check_refuses_private_addresses(client, upload, link_check_on):
    item = upload("l.png", source_url="http://127.0.0.1:5432/")["item"]
    from kist.db import SessionLocal

    def handler(request):  # pragma: no cover - must never be reached
        raise AssertionError("a private address was fetched")

    with SessionLocal() as s:
        linkrot.run_batch(s, transport=httpx.MockTransport(handler), sleep=lambda t: None)
    assert client.get(f"/api/items/{item['id']}").json()["source_status"] == "error"


def test_mcp_export_and_snapshot_tools(client, upload):
    from test_tokens import mcp_result

    item = upload("m.png")["item"]
    res = mcp_result(client, "export_collection", {"target": "all"})
    assert not res.get("isError"), res
    export_id = res["structuredContent"]["id"] if "structuredContent" in res else json.loads(
        res["content"][0]["text"])["id"]
    run_jobs()
    status = mcp_result(client, "export_status", {"id": export_id})
    assert "done" in json.dumps(status)
    missing = mcp_result(client, "get_page_snapshot", {"id": item["id"]})
    assert missing.get("isError")
    client.post(f"/api/items/{item['id']}/snapshot", json={"html": "<p>agent can read</p>"})
    found = mcp_result(client, "get_page_snapshot", {"id": item["id"]})
    assert "agent can read" in json.dumps(found)


def test_link_check_is_off_by_default(client, upload, public_dns):
    """Since 2.0 Kist leaves source sites alone once it has the copy: no
    link checks unless KIST_LINK_CHECK_DAYS is set (Jay, 2026-11-10)."""
    from kist import worker
    from kist.config import Settings
    from kist.db import SessionLocal
    from kist.services import linkrot

    assert Settings.model_fields["link_check_days"].default == 0
    upload("a.png", source_url="https://gone.test/x")
    with SessionLocal() as s:
        assert linkrot.due_ids(s) == []
    assert worker.start_link_check() is False
