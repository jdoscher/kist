"""Kist 2.0 F2: Present mode. Slides (order, captions, image choice, Ken
Burns rules, compare slides), saved shows (durable in shows.json, survive a
rebuild, checked by verify), the PDF, the phone remote's relay, and scope:
a folder-limited reader never sees an item outside its folders, and a
phone remote reaches nothing but its own show."""

import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import png_bytes
from fastapi.testclient import TestClient
from PIL import Image, ImageCms
from test_tokens import MCP_HEADERS, as_token, make_token, mcp_result

from kist import cli
from kist.config import get_settings
from kist.services import present as present_svc
from kist.services import present_remote as remote_svc


@pytest.fixture(autouse=True)
def _fresh_remotes():
    remote_svc.reset()
    yield
    remote_svc.reset()


def anon(client) -> TestClient:
    return TestClient(client.app, follow_redirects=False)


def slides(client, source, **body):
    res = client.post("/api/present/slides", json={"source": source, **body})
    assert res.status_code == 200, res.text
    return res.json()


def ids_of(resolved):
    return [[it["id"] for it in s["items"]] for s in resolved["slides"]]


def folder_with(client, upload, n, name="Show"):
    folder = client.post("/api/folders", json={"name": name}).json()
    items = [upload(f"{name}-{i}.png", folder_id=folder["id"])["item"] for i in range(n)]
    return folder, items


# ---------- slides ----------


def test_items_play_in_order_with_captions_and_no_filenames(client, upload):
    a = upload("IMG_4482_final_FINAL.png", provenance=json.dumps(
        {"page_title": "Villa Savoye", "credit": "Photo: A. Person", "license": "CC BY 4.0"}))
    b = upload("b.png", provenance=json.dumps({"creator": "Jane Maker"}))["item"]
    a = a["item"]
    client.patch(f"/api/items/{a['id']}", json={"notes": "Ask about the ramp"})
    got = slides(client, {"kind": "items", "item_ids": [b["id"], a["id"]]})
    assert ids_of(got) == [[b["id"]], [a["id"]]]
    first, second = got["slides"][1]["items"][0], got["slides"][0]["items"][0]
    assert first["title"] == "Villa Savoye" and first["credit"] == "Photo: A. Person"
    assert first["license"] == "CC BY 4.0" and first["notes"] == "Ask about the ramp"
    assert second["credit"] == "Jane Maker"  # no credit: the creator
    # the original, colour-managed by the browser, never a recompressed copy
    assert first["src"] == f"/api/items/{a['id']}/file?present=1"
    assert first["src_kind"] == "original"
    # the filename is there for the toggle, never in the caption fields
    assert "IMG_4482" not in first["title"] + first["credit"]
    assert got["item_count"] == 2 and got["low_res"] == 2  # 640x480 test images


def test_folder_board_and_search_sources(client, upload):
    folder, items = folder_with(client, upload, 3)
    got = slides(client, {"kind": "folder", "id": folder["id"]})
    assert sorted(i[0] for i in ids_of(got)) == sorted(i["id"] for i in items)
    assert got["name"] == "Show"
    # a board plays in board order (top to bottom, then left to right)
    board = client.post("/api/boards", json={"name": "Mood", "item_ids": [
        items[2]["id"], items[0]["id"], items[1]["id"]], "layout": "grid"}).json()
    order = slides(client, {"kind": "board", "id": board["id"]})
    doc = client.get(f"/api/boards/{board['id']}").json()
    nodes = sorted((n for n in doc["nodes"] if n["type"] == "item"),
                   key=lambda n: (n["y"], n["x"]))
    assert [i[0] for i in ids_of(order)] == [n["item_id"] for n in nodes]
    # a search runs the grid's own query string
    client.patch(f"/api/items/{items[1]['id']}/tags", json={"add": ["chairs"]})
    res = client.post("/api/present/slides?tags=chairs", json={"source": {"kind": "search"}})
    assert ids_of(res.json()) == [[items[1]["id"]]]
    assert client.post("/api/present/slides", json={
        "source": {"kind": "board", "id": "01NOPE"}}).status_code == 404


def test_ken_burns_never_moves_drawings_or_documents(client, upload):
    photo = upload("photo.png")["item"]
    sketch = upload("sketch.png")["item"]
    client.patch(f"/api/items/{sketch['id']}/tags", json={"add": ["Pencil"]})
    got = slides(client, {"kind": "items", "item_ids": [photo["id"], sketch["id"]]})
    motion = {s["items"][0]["id"]: s["items"][0]["motion_ok"] for s in got["slides"]}
    assert motion == {photo["id"]: True, sketch["id"]: False}
    assert present_svc.DEFAULT_OPTIONS["ken_burns"] is False


def test_compare_slides_from_a_pair_and_from_stacks(client, upload):
    a, b, c = (upload(f"{n}.png")["item"] for n in "abc")
    got = slides(client, {"kind": "items", "item_ids": [a["id"], b["id"], c["id"]]},
                 compare=[[a["id"], c["id"]]])
    assert ids_of(got) == [[a["id"], c["id"]], [b["id"]]]
    assert got["slides"][0]["kind"] == "compare"
    client.post("/api/stacks", json={"item_ids": [b["id"], c["id"]]})
    stacked = slides(client, {"kind": "items", "item_ids": [a["id"], b["id"]]}, pairs="stacks")
    assert ids_of(stacked) == [[a["id"]], [b["id"], c["id"]]]
    bad = client.post("/api/present/slides", json={
        "source": {"kind": "items", "item_ids": [a["id"]]}, "compare": [[a["id"], a["id"]]]})
    assert bad.status_code == 422


def test_trashed_items_drop_out(client, upload):
    a, b = upload("a.png")["item"], upload("b.png")["item"]
    client.delete(f"/api/items/{b['id']}")
    got = slides(client, {"kind": "items", "item_ids": [a["id"], b["id"]]})
    assert ids_of(got) == [[a["id"]]]


# ---------- saved shows, rebuild, verify ----------


def test_saved_shows_survive_a_rebuild_and_verify_checks_them(client, upload):
    folder, items = folder_with(client, upload, 2)
    show = client.post("/api/shows", json={
        "name": "Crit 14 Nov", "source": {"kind": "folder", "id": folder["id"]},
        "options": {"surround": "black", "gesture_seconds": 60},
        "compare": [[items[0]["id"], items[1]["id"]]]}).json()
    assert show["options"]["surround"] == "black" and show["options"]["ken_burns"] is False
    # a search show keeps what the search found
    found = client.post("/api/shows", json={"name": "Found", "source": {"kind": "search"},
                                            "item_ids": [items[1]["id"]]}).json()
    assert found["source"] == {"kind": "items", "item_ids": [items[1]["id"]]}
    assert [s["name"] for s in client.get("/api/shows").json()] == ["Crit 14 Nov", "Found"]
    patched = client.patch(f"/api/shows/{show['id']}", json={"name": "Crit 15 Nov",
                                                             "options": {"advance_seconds": 8}})
    assert patched.json()["options"]["advance_seconds"] == 8
    assert patched.json()["options"]["surround"] == "black"
    assert client.patch(f"/api/shows/{show['id']}", json={
        "options": {"surround": "pink"}}).status_code == 422

    from kist.db import SessionLocal
    from kist.services import verify

    with SessionLocal() as s:
        assert verify.run(s).problems == []
    assert cli.rebuild_index(wipe=True) == 0
    got = client.get(f"/api/shows/{show['id']}/slides").json()
    assert got["name"] == "Crit 15 Nov" and got["options"]["gesture_seconds"] == 60
    assert ids_of(got) == [[items[0]["id"], items[1]["id"]]]

    assert client.delete(f"/api/shows/{found['id']}").status_code == 204
    assert client.get(f"/api/shows/{found['id']}").status_code == 404
    # verify: a show whose folder is gone, and an unreadable file
    client.delete(f"/api/folders/{folder['id']}")
    with SessionLocal() as s:
        assert any("shows.json" in p and "Unknown folder" in p for p in verify.run(s).problems)
    present_svc.path().write_text("{nope")
    with SessionLocal() as s:
        assert any("shows.json is unreadable" in p for p in verify.run(s).problems)
    assert client.get("/api/shows").status_code == 422
    present_svc.path().unlink()  # the way out: remove (or fix) the file
    assert client.get("/api/shows").json() == []


# ---------- PDF ----------


def _icc_png() -> bytes:
    im = Image.new("RGB", (900, 600), (30, 120, 200))
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    buf = io.BytesIO()
    im.save(buf, "PNG", icc_profile=profile)
    return buf.getvalue()


def test_pdf_has_a_page_per_slide(client, upload):
    a = upload("a.png", data=_icc_png(), provenance=json.dumps({"credit": "Studio X"}))["item"]
    b, c = upload("b.png")["item"], upload("c.png")["item"]
    body = {"source": {"kind": "items", "item_ids": [a["id"], b["id"], c["id"]]},
            "compare": [[b["id"], c["id"]]], "pdf": {"page": "a3", "notes": True}}
    res = client.post("/api/present/pdf", json=body)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF") and res.content.count(b"/Type /Page\n") == 2
    sheet = client.post("/api/present/pdf", json={**body, "pdf": {"layout": "sheet"}})
    assert sheet.status_code == 200 and sheet.content.startswith(b"%PDF")
    assert client.post("/api/present/pdf", json={**body, "pdf": {"page": "b9"}}).status_code == 422
    show = client.post("/api/shows", json={"name": "Leave-behind", "source": body["source"]})
    pdf = client.get(f"/api/shows/{show.json()['id']}/pdf?page=letter")
    assert pdf.status_code == 200 and "Leave-behind.pdf" in pdf.headers["content-disposition"]


# ---------- scope ----------


def test_a_folder_limited_reader_sees_only_its_items(client, upload):
    mine, seen = folder_with(client, upload, 2, "Mine")
    other, hidden = folder_with(client, upload, 1, "Other")
    reader = as_token(client, make_token(client, name="r", scopes=["read"],
                                         folder_ids=[mine["id"]])["token"])
    every = [i["id"] for i in seen + hidden]
    got = reader.post("/api/present/slides", json={"source": {"kind": "items",
                                                              "item_ids": every}})
    assert got.status_code == 200
    assert {s["items"][0]["id"] for s in got.json()["slides"]} == {i["id"] for i in seen}
    # a pair partner outside its folders is left out too
    paired = reader.post("/api/present/slides", json={
        "source": {"kind": "items", "item_ids": [seen[0]["id"]]},
        "compare": [[seen[0]["id"], hidden[0]["id"]]]}).json()
    assert ids_of(paired) == [[seen[0]["id"]]]
    assert reader.post("/api/present/slides", json={
        "source": {"kind": "folder", "id": other["id"]}}).status_code == 404
    board = client.post("/api/boards", json={"name": "Mixed",
                                             "item_ids": [seen[0]["id"], hidden[0]["id"]]})
    assert reader.post("/api/present/slides", json={
        "source": {"kind": "board", "id": board.json()["id"]}}).status_code == 404
    # a search is the reader's own search
    res = reader.post("/api/present/slides", json={"source": {"kind": "search"}})
    assert {s["items"][0]["id"] for s in res.json()["slides"]} == {i["id"] for i in seen}
    pdf = reader.post("/api/present/pdf", json={"source": {"kind": "items", "item_ids": every}})
    assert pdf.status_code == 200 and pdf.content.count(b"/Type /Page\n") == 2
    # saved shows: only those whose source it can see
    client.post("/api/shows", json={"name": "ok", "source": {"kind": "folder", "id": mine["id"]}})
    client.post("/api/shows", json={"name": "no", "source": {"kind": "folder", "id": other["id"]}})
    assert [s["name"] for s in reader.get("/api/shows").json()] == ["ok"]
    # no saving, no remote
    assert reader.post("/api/shows", json={"name": "x", "source": {
        "kind": "folder", "id": mine["id"]}}).status_code in (403, 404)
    assert reader.post("/api/present/remote", json={"name": "x"}).status_code == 404


def test_read_tokens_present_but_never_write(client, upload):
    a = upload("a.png")["item"]
    reader = as_token(client, make_token(client, name="r2", scopes=["read"])["token"])
    assert reader.post("/api/present/slides", json={
        "source": {"kind": "items", "item_ids": [a["id"]]}}).status_code == 200
    assert reader.post("/api/shows", json={"name": "x", "source": {
        "kind": "items", "item_ids": [a["id"]]}}).status_code == 403
    assert reader.post("/api/present/remote", json={"name": "x"}).status_code == 403
    adder = as_token(client, make_token(client, name="add", scopes=["add"])["token"])
    assert adder.post("/api/present/slides", json={
        "source": {"kind": "items", "item_ids": [a["id"]]}}).status_code == 403
    assert anon(client).post("/api/present/slides", json={}).status_code == 401


# ---------- phone remote ----------


def pair(client, item_ids, name="Crit"):
    res = client.post("/api/present/remote", json={"name": name, "item_ids": item_ids})
    assert res.status_code == 201, res.text
    return res.json()


def test_remote_relays_commands_and_state(client, upload):
    a, b = upload("a.png")["item"], upload("b.png")["item"]
    r = pair(client, [a["id"], b["id"]])
    assert r["path"] == f"/present/remote/{r['id']}#{r['token']}"
    phone = anon(client)
    h = {"X-Kist-Remote": r["token"]}
    # the show window publishes a state; the phone reads it
    client.post(f"/api/present/remote/{r['id']}/state", json={
        "index": 0, "count": 2, "item_id": a["id"], "next_item_id": "01NOTINSHOW",
        "title": "Villa", "notes": "ramp"})
    st = phone.get(f"/present-relay/{r['id']}/state?after=0&wait=0", headers=h).json()
    assert st["state"]["title"] == "Villa" and st["state"]["next_item_id"] is None
    assert st["name"] == "Crit"
    # the phone sends next; the show window's poll gets it
    assert phone.post(f"/present-relay/{r['id']}/cmd", json={"cmd": "next"},
                      headers=h).status_code == 200
    phone.post(f"/present-relay/{r['id']}/cmd", json={"cmd": "pick", "index": 1}, headers=h)
    polled = client.get(f"/api/present/remote/{r['id']}/poll?after=0&wait=0").json()
    assert [e["cmd"] for e in polled["events"]] == ["next", "pick"] and polled["connected"]
    assert polled["events"][1]["index"] == 1
    assert phone.post(f"/present-relay/{r['id']}/cmd", json={"cmd": "pick", "index": 2},
                      headers=h).status_code == 422
    again = client.get(f"/api/present/remote/{r['id']}/poll?after={polled['seq']}&wait=0")
    assert again.json()["events"] == []
    assert phone.post(f"/present-relay/{r['id']}/cmd", json={"cmd": "delete"},
                      headers=h).status_code == 422
    # thumbnails of this show's items only
    assert phone.get(f"/present-relay/{r['id']}/thumb/{a['id']}", headers=h).status_code == 200
    other = upload("c.png")["item"]
    assert phone.get(f"/present-relay/{r['id']}/thumb/{other['id']}",
                     headers=h).status_code == 404


def test_remote_tokens_are_scoped_and_revocable(client, upload):
    a = upload("a.png")["item"]
    r = pair(client, [a["id"]])
    r2 = pair(client, [a["id"]], "Other")
    phone = anon(client)
    base = f"/present-relay/{r['id']}"
    for headers in ({}, {"X-Kist-Remote": "guess"}, {"X-Kist-Remote": r2["token"]},
                    {"Authorization": "Bearer " + get_settings().token}):
        assert phone.get(f"{base}/state?wait=0", headers=headers).status_code == 404
        assert phone.post(f"{base}/cmd", json={"cmd": "next"},
                          headers=headers).status_code == 404
    h = {"X-Kist-Remote": r["token"]}
    # the remote token opens nothing in the API
    for path in ("/api/items", f"/api/items/{a['id']}/file", "/api/shows",
                 f"/api/present/remote/{r['id']}/poll?wait=0"):
        assert phone.get(path, headers={"Authorization": f"Bearer {r['token']}"}
                         ).status_code == 401
    # the show window's side needs the Kist token
    assert phone.get(f"/api/present/remote/{r['id']}/poll?wait=0").status_code == 401
    # never stored in plain text
    assert all(r["token"] not in s.token_hash for s in remote_svc._sessions.values())
    # rate limit: a burst, then 429
    codes = [phone.post(f"{base}/cmd", json={"cmd": "next"}, headers=h).status_code
             for _ in range(remote_svc.CMD_BURST + 3)]
    assert codes.count(200) == remote_svc.CMD_BURST and 429 in codes
    assert phone.post(f"{base}/cmd", content=b"x" * 2000, headers=h).status_code == 413
    # revoke
    assert client.delete(f"/api/present/remote/{r['id']}").status_code == 204
    assert phone.get(f"{base}/state?wait=0", headers=h).status_code == 404
    assert client.delete(f"/api/present/remote/{r['id']}").status_code == 404
    # expiry
    s2 = remote_svc.get(r2["id"])
    s2.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert phone.get(f"/present-relay/{r2['id']}/state?wait=0",
                     headers={"X-Kist-Remote": r2["token"]}).status_code == 404


def test_remote_hours_default():
    from kist.config import Settings

    assert Settings.model_fields["present_remote_hours"].default == 4.0
    assert Settings.model_fields["present_max_slides"].default == 500


def test_the_remote_page_is_the_web_app(client):
    res = anon(client).get("/present/remote/01ABC")
    # the SPA serves it when web/dist is built; never an API answer
    assert res.status_code in (200, 404)
    if res.status_code == 200:
        assert "text/html" in res.headers["content-type"]
    assert anon(client).get("/present-relay/nope").status_code == 404


# ---------- MCP ----------


def test_mcp_create_and_list_shows(client, upload):
    folder, items = folder_with(client, upload, 2)
    out = mcp_result(client, "create_show", {"name": "Agent show", "source_kind": "folder",
                                             "source_id": folder["id"], "surround": "black"})
    data = json.loads(out["content"][0]["text"])
    assert data["slide_count"] == 2 and data["web_url"] == f"/?present={data['id']}"
    listed = mcp_result(client, "list_shows")
    assert "Agent show" in json.dumps(listed)
    assert MCP_HEADERS  # the shared MCP headers are what test_tokens uses


def test_png_bytes_helper_is_shared():
    assert png_bytes()[:4] == b"\x89PNG"
