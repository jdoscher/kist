"""Milestones 4 and 5 on top of 2 and 3 (the int-all integration): board
shares and exports, the browser extension's and the /add page's requests,
add-only snapshots, page text in search, one filter path for M4/M5 (REST,
timeline, smart-folder shares, MCP), RAW+JPEG stack suggestions from M5's
raw_pair_key, what new kinds get embedded, and the folder-limited reader
against the M4/M5 routes. The combined rebuild test is in
test_m23_integration.py."""

import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import media_fixtures as F
import pytest
import sqlalchemy as sa
from conftest import png_bytes
from fastapi.testclient import TestClient
from test_importers import fake_web  # noqa: F401  (fixture)
from test_organise_ai import ml  # noqa: F401  (fixture)
from test_tokens import as_token, make_token, mcp_result

from kist import worker
from kist.db import SessionLocal
from kist.models import Item, ItemText

needs_ffmpeg = pytest.mark.skipif(not F.HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")


def run_jobs():
    while worker.run_one():
        pass


def ids(page) -> list[str]:
    return [i["id"] for i in page["items"]]


def anon(client) -> TestClient:
    return TestClient(client.app, follow_redirects=False)


def solid(rgb):
    return png_bytes(color=rgb, size=(120, 90))


# ---------- boards: shares and exports ----------


def test_board_share_and_board_export_end_to_end(client, upload):
    top = upload("top.png", data=solid((200, 10, 10)))["item"]
    low = upload("low.png", data=solid((10, 200, 10)))["item"]
    gone = upload("gone.png", data=solid((10, 10, 200)))["item"]
    outside = upload("outside.png", data=solid((90, 90, 90)))["item"]
    board = client.post("/api/boards", json={"name": "Mood", "item_ids": [top["id"]]}).json()
    # low goes under top; gone is on the board but trashed afterwards
    nodes = board["nodes"]
    y = max(n["y"] + n["h"] for n in nodes) + 50
    res = client.patch(f"/api/boards/{board['id']}", json={"add": [
        {"type": "item", "item_id": low["id"], "x": 0, "y": y, "w": 100, "h": 80},
        {"type": "item", "item_id": gone["id"], "x": 300, "y": y + 200, "w": 100, "h": 80},
        {"type": "note", "text": "not an item", "x": 0, "y": -500, "w": 100, "h": 80},
    ]})
    assert res.status_code == 200, res.text
    client.delete(f"/api/items/{gone['id']}")

    from kist.services import boards

    with SessionLocal() as s:
        assert boards.share_item_ids(s, board["id"]) == [top["id"], low["id"]]

    share = client.post("/api/shares", json={"target_kind": "board",
                                             "target_id": board["id"]})
    assert share.status_code == 201, share.text
    slug = share.json()["slug"]
    v = anon(client)
    page = v.get(f"/s/{slug}")
    assert page.status_code == 200
    assert page.text.index(top["id"]) < page.text.index(low["id"])  # reading order
    assert gone["id"] not in page.text and outside["id"] not in page.text
    assert v.get(f"/s/{slug}/i/{low['id']}/thumb").status_code == 200
    for other in (gone, outside):
        assert v.get(f"/s/{slug}/i/{other['id']}/thumb").status_code == 404

    res = client.post("/api/exports", json={"target": "board", "target_id": board["id"]})
    assert res.status_code == 202, res.text
    run_jobs()
    status = client.get(f"/api/exports/{res.json()['id']}").json()
    assert status["status"] == "done" and status["item_count"] == 2, status
    zf = zipfile.ZipFile(io.BytesIO(client.get(status["download_url"]).content))
    data = [n for n in zf.namelist() if n.startswith("data/") and n.endswith(".png")]
    assert sorted(n.rsplit("/", 1)[-1] for n in data) == ["low.png", "top.png"]

    # a trashed board can't be shared; an unknown one is a 422
    client.delete(f"/api/boards/{board['id']}")
    for target in (board["id"], "01NOBOARD"):
        assert client.post("/api/shares", json={"target_kind": "board",
                                                "target_id": target}).status_code == 422


def test_smart_folder_share_uses_every_filter(client, upload):
    picked = upload("picked.png")["item"]
    other = upload("other.png")["item"]
    client.patch(f"/api/items/{picked['id']}", json={"flag": "pick", "provenance": {
        "ai_generated": True}})
    client.patch(f"/api/items/{other['id']}", json={"flag": "pick"})
    smart = client.post("/api/smart-folders", json={"name": "AI picks", "query": {
        "flag": "pick", "ai_generated": "true"}})
    assert smart.status_code == 201, smart.text
    assert smart.json()["query"]["ai_generated"] == "true"
    share = client.post("/api/shares", json={"target_kind": "smart",
                                             "target_id": smart.json()["id"]}).json()
    page = anon(client).get(f"/s/{share['slug']}").text
    assert picked["id"] in page and other["id"] not in page


# ---------- the browser extension and the /add page ----------


def test_extension_upload_sends_flat_provenance(client, upload):
    form = {"page_url": "https://blog.test/post", "image_url": "https://cdn.test/a.jpg",
            "title": "  A   post ", "captured_at": "2026-11-09T10:00:00Z",
            "method": "extension", "author": "Ana", "credit": "Blog", "site": "The Blog",
            "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "source_url": "https://blog.test/post"}
    item = upload("a.png", **form)["item"]
    prov = item["provenance"]
    assert prov["method"] == "extension" and prov["page_url"] == "https://blog.test/post"
    assert prov["image_url"] == "https://cdn.test/a.jpg" and prov["page_title"] == "A post"
    assert prov["creator"] == "Ana" and prov["credit"] == "Blog" and prov["site"] == "The Blog"
    assert prov["license"] == "CC BY 4.0"
    assert prov["rights_url"] == "https://creativecommons.org/licenses/by/4.0/"
    assert prov["captured_at"].startswith("2026-11-09T10:00:00")
    assert item["source_url"] == "https://blog.test/post"
    # a bad flat value is dropped, never fails the save; a provenance object wins
    item = upload("b.png", image_url="data:image/png;base64,xx", method="carrier-pigeon",
                  provenance=json.dumps({"credit": "Object"}), credit="Flat")["item"]
    assert "image_url" not in item["provenance"]
    assert item["provenance"]["method"] == "upload"
    assert item["provenance"]["credit"] == "Object"
    found = client.get("/api/items", params={"license": "cc by 4.0"}).json()
    assert len(found["items"]) == 1


def test_add_page_json_request(client, fake_web):  # noqa: F811
    fake_web["https://img.test/cat.png"] = (png_bytes((4, 5, 6)), "image/png")
    body = {"url": "https://img.test/cat.png", "source_url": "https://site.test/cats",
            "page_url": "https://site.test/cats", "image_url": "https://img.test/cat.png",
            "title": "Cats <img src=x onerror=alert(1)>", "captured_at": "2026-11-09T12:00:00Z",
            "method": "bookmarklet", "tags": ["cats"], "folder_ids": [], "notes": "from /add"}
    folder = client.post("/api/folders", json={"name": "Inbox"}).json()
    body["folder_ids"] = [folder["id"]]
    res = client.post("/api/items", json=body, headers={"X-Kist-Client": "web"})
    assert res.status_code == 201, res.text
    item = res.json()["item"]
    assert res.json()["created"] is True
    prov = item["provenance"]
    assert prov["method"] == "bookmarklet" and prov["page_url"] == "https://site.test/cats"
    assert prov["image_url"] == "https://img.test/cat.png"
    assert prov["page_title"] == "Cats <img src=x onerror=alert(1)>"  # stored as text
    assert item["tags"] == ["cats"] and item["folder_ids"] == [folder["id"]]
    assert item["notes"] == "from /add"
    again = client.post("/api/items", json=body)
    assert again.json()["created"] is False and again.json()["item"]["id"] == item["id"]


def test_add_token_snapshots_only_its_own_fresh_items(client, upload):
    tok = make_token(client, "Browser", ["add"])
    adder = as_token(client, tok["token"])
    res = adder.post("/api/items", files={"file": ("mine.png", solid((1, 2, 3)), "image/png")},
                     data={"page_url": "https://news.test/story", "method": "extension"})
    assert res.status_code == 201, res.text
    mine = res.json()["item"]
    with SessionLocal() as s:
        assert s.get(Item, mine["id"]).added_by_token == tok["id"]
    html = ("<html><head><title>Story</title><script>steal()</script></head>"
            "<body><p>The zeppelin landed quietly.</p></body></html>")
    snap = {"html": html, "page_url": "https://news.test/story", "title": "Story",
            "captured_at": "2026-11-09T09:00:00Z"}
    res = adder.post(f"/api/items/{mine['id']}/snapshot", json=snap)
    assert res.status_code == 200, res.text
    assert res.json()["url"] == "https://news.test/story"
    assert res.json()["captured_at"].startswith("2026-11-09T09:00:00")
    # still add-only: it can't read the snapshot back, or the item
    assert adder.get(f"/api/items/{mine['id']}/snapshot").status_code == 403
    assert adder.get(f"/api/items/{mine['id']}").status_code == 403
    saved = client.get(f"/api/items/{mine['id']}/snapshot")
    assert saved.status_code == 200 and "steal()" not in saved.text

    # someone else's item, a missing item, or its own item after 10 minutes: 403
    theirs = upload("theirs.png")["item"]
    assert adder.post(f"/api/items/{theirs['id']}/snapshot", json=snap).status_code == 403
    assert adder.post("/api/items/01NOSUCHITEM000000000000/snapshot",
                      json=snap).status_code == 403
    with SessionLocal() as s:
        s.execute(sa.update(Item).where(Item.id == mine["id"]).values(
            created_at=datetime.now(UTC) - timedelta(minutes=11)))
        s.commit()
    assert adder.post(f"/api/items/{mine['id']}/snapshot", json=snap).status_code == 403
    # a write token may snapshot anything, also as a raw text/html body
    writer = as_token(client, make_token(client, "w", ["write"])["token"])
    res = writer.post(f"/api/items/{theirs['id']}/snapshot",
                      content=b"<p>Raw body about lighthouses</p>",
                      headers={"Content-Type": "text/html; charset=utf-8"})
    assert res.status_code == 200, res.text
    assert writer.post(f"/api/items/{theirs['id']}/snapshot", json={"html": " "}
                       ).status_code == 422

    # page text: found by q (plain and in a hybrid's keyword half), not by text=
    assert ids(client.get("/api/items", params={"q": "zeppelin"}).json()) == [mine["id"]]
    assert ids(client.get("/api/items", params={"text": "zeppelin"}).json()) == []
    assert ids(client.get("/api/items", params={"has_text": "true"}).json()) == []
    page = client.get("/api/items", params={"q": "lighthouses"}).json()
    assert ids(page) == [theirs["id"]] and "lighthouses" in page["items"][0]["match"]["snippet"]


# ---------- one filter path ----------


def test_m4_m5_filters_in_every_listing(client, upload, tmp_path):
    photo = upload("IMG_1.jpg", F.jpeg(exif=F.exif_bytes()), "image/jpeg")["item"]
    plain = upload("plain.png")["item"]
    client.patch(f"/api/items/{photo['id']}", json={"flag": "pick", "provenance": {
        "license": "CC0", "ai_generated": False}})
    client.patch(f"/api/items/{plain['id']}", json={"flag": "pick"})
    for params in ({"camera": "eos"}, {"license": "cc0"}, {"ai_generated": "false"},
                   {"has_gps": "true", "flag": "pick"}, {"camera": "eos", "q": "IMG"},
                   {"camera": "eos", "color": "#1e78c8", "color_tol": 100}):
        assert ids(client.get("/api/items", params=params).json()) == [photo["id"]], params
    # ranked modes take them too (colour above; text= is ranked)
    assert ids(client.get("/api/items", params={"text": "x", "camera": "eos"}).json()) == []
    unknown = client.get("/api/items", params={"ai_generated": "unknown"}).json()
    assert plain["id"] in ids(unknown) and photo["id"] not in ids(unknown)
    # timeline, on-this-day and map take every GET /api/items filter
    total = sum(b["count"] for b in client.get("/api/timeline?group=year").json())
    assert total == 2
    picks = client.get("/api/timeline", params={"group": "year", "license": "cc0"}).json()
    assert sum(b["count"] for b in picks) == 1 and picks[0]["first_item_id"] == photo["id"]
    assert client.get("/api/timeline", params={"flag": "reject"}).json() == []
    pins = client.get("/api/map", params={"zoom": 2, "license": "cc0"}).json()
    assert sum(c["count"] for c in pins["clusters"]) == 1
    assert client.get("/api/map", params={"zoom": 2, "ai_generated": "true"}).json()[
        "clusters"] == []
    assert client.get("/api/timeline", params={"ai_generated": "maybe"}).status_code == 422

    # MCP: search_items takes the M4/M5 filters, search_by_image person and collapse
    def mcp_ids(args):
        res = mcp_result(client, "search_items", args)
        assert not res.get("isError"), res
        return [i["id"] for i in json.loads(res["content"][0]["text"])["items"]]

    assert mcp_ids({"camera": "EOS", "has_gps": True}) == [photo["id"]]
    assert mcp_ids({"ai_generated": False, "license": "CC0"}) == [photo["id"]]
    assert mcp_ids({"taken_after": "2023-06-01", "taken_before": "2023-07-01"}) == [photo["id"]]
    assert mcp_ids({"type": "image", "place": "nowhere"}) == []
    res = mcp_result(client, "search_items", {"taken_after": "last week"})
    assert res["isError"] and "ISO" in res["content"][0]["text"]
    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                        headers={"Accept": "application/json, text/event-stream"}).json()
    by_name = {t["name"]: t["inputSchema"]["properties"] for t in tools["result"]["tools"]}
    m45 = {"ai_generated", "license", "source_status", "taken_after", "taken_before", "camera",
           "lens", "has_gps", "place", "vertical", "has_audio", "duration_min", "duration_max"}
    assert m45 <= set(by_name["search_items"])
    assert m45 | {"person", "collapse_stacks", "flag", "label"} <= set(by_name["search_by_image"])


def test_search_by_image_with_person_and_collapse(client, upload, fake_web, ml):  # noqa: F811
    from test_ai import drain

    a = upload("a.png", data=solid((220, 20, 20)))["item"]
    b = upload("b.png", data=solid((210, 30, 30)))["item"]
    c = upload("c.png", data=solid((200, 40, 40)))["item"]
    drain()
    client.post("/api/stacks", json={"item_ids": [a["id"], b["id"]], "cover": a["id"]})
    fake_web["https://img.test/q.png"] = (solid((220, 20, 20)), "image/png")
    res = mcp_result(client, "search_by_image", {"url": "https://img.test/q.png",
                                                 "collapse_stacks": True})
    assert not res.get("isError"), res
    found = [i["id"] for i in json.loads(res["content"][0]["text"])["items"]]
    assert c["id"] in found and len([i for i in found if i in (a["id"], b["id"])]) == 1
    res = mcp_result(client, "search_by_image", {"url": "https://img.test/q.png",
                                                 "person": "01NOPERSON"})
    assert json.loads(res["content"][0]["text"])["items"] == []


# ---------- RAW pairs, embeddings of the new kinds ----------


def test_raw_and_jpeg_pair_from_raw_pair_key(client, upload, tmp_path):
    taken = "2022:08:01 09:15:30"
    nef = upload("DSC_0042.NEF", F.tiff_raw(tmp_path / "DSC_0042.NEF",
                                            F.jpeg(size=(800, 600)), taken=taken).read_bytes(),
                 "application/octet-stream")["item"]
    jpg = upload("DSC_0042.JPG", F.jpeg(exif=F.exif_bytes(
        make="NIKON CORPORATION", model="NIKON Z 6", taken=taken, gps=None)),
        "image/jpeg")["item"]
    # the same name from another shoot (another time) is no pair
    later = upload("DSC_0043.NEF", F.tiff_raw(tmp_path / "DSC_0043.NEF",
                                              F.jpeg(size=(800, 600)),
                                              taken="2022:08:01 10:00:00").read_bytes(),
                   "application/octet-stream")["item"]
    upload("DSC_0043.JPG", F.jpeg(exif=F.exif_bytes(
        make="NIKON CORPORATION", model="NIKON Z 6", taken="2023:01:01 00:00:00", gps=None)),
        "image/jpeg")
    sugg = client.get("/api/stacks/suggestions").json()
    pairs = [s for s in sugg if s["kind"] == "raw_jpeg"]
    assert [sorted(p["item_ids"]) for p in pairs] == [sorted([nef["id"], jpg["id"]])]
    assert pairs[0]["cover_item_id"] == jpg["id"]
    assert all(later["id"] not in s["item_ids"] for s in sugg if s["kind"] == "raw_jpeg")


@needs_ffmpeg
def test_what_the_new_kinds_get_embedded(client, upload, tmp_path):
    from kist.services import ai

    song = upload("song.mp3", F.audio(tmp_path / "song.mp3").read_bytes(),
                  "application/octet-stream")["item"]
    psd = upload("art.psd", F.psd(tmp_path / "art.psd", size=(200, 150)).read_bytes(),
                 "application/octet-stream")["item"]
    fig = upload("x.fig", b"fig-kiwi" + b"\x00" * 64, "application/octet-stream")["item"]
    font = upload("Kist-Regular.ttf", F.font(tmp_path / "Kist-Regular.ttf").read_bytes(),
                  "application/octet-stream")["item"]
    assert song["kind"] == "audio" and song["preview_status"] == "ready"
    assert song["phash"] is None and song["dominant_colors"] is None
    assert psd["kind"] == "design" and psd["dominant_colors"]
    assert font["kind"] == "font" and font["preview_status"] == "ready"
    with SessionLocal() as s:
        got = {k: ai.embed_source(s.get(Item, v["id"])) is not None
               for k, v in (("audio", song), ("design", psd), ("font", font), ("fig", fig))}
    assert got == {"audio": False, "design": True, "font": True, "fig": False}


# ---------- the folder-limited reader and the M4/M5 routes ----------


@needs_ffmpeg
def test_folder_limited_reader_across_milestone_4_and_5_routes(client, upload, tmp_path):
    a = client.post("/api/folders", json={"name": "A"}).json()
    b = client.post("/api/folders", json={"name": "B"}).json()
    in_a = json.dumps([a["id"]])
    in_b = json.dumps([b["id"]])
    seen = upload("IMG_1.jpg", F.jpeg(exif=F.exif_bytes()), "image/jpeg",
                  folder_ids=in_a)["item"]
    seen_vid = upload("v.mp4", F.video(tmp_path / "v.mp4").read_bytes(),
                      "application/octet-stream", folder_ids=in_a)["item"]
    hidden = upload("IMG_2.jpg", F.jpeg(color=(1, 2, 3), exif=F.exif_bytes()), "image/jpeg",
                    folder_ids=in_b)["item"]
    hidden_vid = upload("h.mp4", F.video(tmp_path / "h.mp4", size="160x120").read_bytes(),
                        "application/octet-stream", folder_ids=in_b)["item"]
    hidden_zip = upload("kit.zip", F.zip_with(tmp_path / "kit.zip", {
        "kit/a.png": F.png()}).read_bytes(), "application/zip", folder_ids=in_b)["item"]
    for item in (seen, hidden):
        client.patch(f"/api/items/{item['id']}", json={"provenance": {"license": "CC0"}})
        client.post(f"/api/items/{item['id']}/snapshot", json={"html": "<p>secret page</p>"})
    share_b = client.post("/api/shares", json={"target_kind": "folder",
                                               "target_id": b["id"]}).json()
    share_a = client.post("/api/shares", json={"target_kind": "folder",
                                               "target_id": a["id"]}).json()
    exp = client.post("/api/exports", json={"target": "all"}).json()
    allowed = {seen["id"], seen_vid["id"]}

    fam = as_token(client, make_token(client, "Fam", ["read"], folder_ids=[a["id"]])["token"])
    for params in ({"license": "cc0"}, {"camera": "eos"}, {"has_gps": "true"},
                   {"q": "secret"}, {"type": "video"}, {"type": "bundle"},
                   {"order": "-taken"}, {"has_audio": "true"}, {"source_status": "unchecked"}):
        res = fam.get("/api/items", params=params)
        assert res.status_code == 200, (params, res.text)
        assert set(ids(res.json())) <= allowed, params
    # timeline, on-this-day and map count only what it sees
    assert sum(x["count"] for x in fam.get("/api/timeline?group=year").json()) == 2
    pins = fam.get("/api/map", params={"zoom": 2}).json()
    assert sum(c["count"] for c in pins["clusters"]) == 1
    otd = fam.get("/api/on-this-day", params={"day": "2024-06-15"}).json()
    assert {i["id"] for y in otd["years"] for i in y["items"]} <= allowed
    # item media routes: its own work, a hidden item's are 404
    assert fam.get(f"/api/items/{seen_vid['id']}/stream").status_code in (200, 206)
    assert fam.get(f"/api/items/{seen_vid['id']}/scrub.json").status_code == 200
    for path in (f"{hidden_vid['id']}/stream", f"{hidden_vid['id']}/scrub.json",
                 f"{hidden_zip['id']}/bundle", f"{hidden_zip['id']}/bundle/kit/a.png",
                 f"{hidden['id']}/snapshot", f"{hidden['id']}/snapshot/text"):
        assert fam.get(f"/api/items/{path}").status_code == 404, path
    # M4 management is the owner's: 404 for it (not even 403: nothing to learn)
    for path in ("/api/shares", f"/api/shares/{share_b['id']}", "/api/exports",
                 f"/api/exports/{exp['id']}", "/api/export-presets", "/api/imports",
                 f"/api/items/{seen['id']}/snapshot"):
        assert fam.get(path).status_code == 404, path
    for path, body in (("/api/shares", {"target_kind": "folder", "target_id": a["id"]}),
                       ("/api/exports", {"target": "all"}),
                       (f"/api/items/{seen['id']}/snapshot", {"html": "<p>x</p>"}),
                       (f"/api/items/{seen_vid['id']}/cover", {"t": 1})):
        assert fam.post(path, json=body).status_code == 404, path
    # /s/ is public but holds only its own share's items, token or not
    for c in (anon(client), fam):
        page = c.get(f"/s/{share_a['slug']}")
        assert page.status_code == 200
        assert seen["id"] in page.text and hidden["id"] not in page.text
        assert c.get(f"/s/{share_a['slug']}/i/{hidden['id']}/thumb").status_code == 404
        assert c.get(f"/s/{share_a['slug']}/i/{hidden['id']}/file").status_code == 404
    assert hidden["id"] in anon(client).get(f"/s/{share_b['slug']}").text  # shared on purpose
    # and over MCP
    for args in ({"license": "CC0"}, {"camera": "EOS"}, {"query": "secret"},
                 {"type": "video"}, {"has_gps": True}):
        res = mcp_result(fam, "search_items", args)
        assert not res.get("isError"), (args, res)
        assert {i["id"] for i in json.loads(res["content"][0]["text"])["items"]} <= allowed
    for tool, args in (("get_page_snapshot", {"id": hidden["id"]}),
                       ("export_collection", {"target": "all"})):
        res = mcp_result(fam, tool, args)
        assert res["isError"], tool
    with SessionLocal() as s:
        texts = s.execute(sa.select(ItemText.item_id).where(ItemText.source == "page")).all()
    assert len(texts) == 2
