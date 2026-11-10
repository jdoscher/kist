"""Milestone 3 boards: the document, versioned saves, export, trash, undo,
rebuild/verify, who sees what, and the MCP tools."""

import io
import json
from datetime import UTC, datetime, timedelta

from PIL import Image
from test_tokens import as_token, make_token, mcp_result

from kist import cli


def _board(client, **body):
    res = client.post("/api/boards", json={"name": "Mood", **body})
    assert res.status_code == 201, res.text
    return res.json()


def _text(out):
    return json.loads(out["content"][0]["text"])


def test_create_layouts_files_and_used_in_boards(client, upload, clean):
    a = upload("a.png")["item"]
    b = upload("b.png")["item"]
    board = _board(client, item_ids=[a["id"], b["id"]], layout="grid",
                   nodes=[{"id": "n1", "type": "note", "x": 0, "y": -300, "w": 200, "h": 100,
                           "text": "warm 70s"}])
    assert board["version"] == 1 and board["item_count"] == 2
    assert board["state"] == "active" and board["missing_item_ids"] == []
    kinds = [n["type"] for n in board["nodes"]]
    assert kinds == ["note", "item", "item"]
    items = [n for n in board["nodes"] if n["type"] == "item"]
    assert {n["item_id"] for n in items} == {a["id"], b["id"]}
    assert items[0]["y"] > -200  # laid out below the note
    disk = json.loads((clean / "boards" / board["id"] / "board.json").read_text())
    assert disk["version"] == 1 and len(disk["nodes"]) == 3
    assert disk["canvas"] == {"bg": "#f5f5f4", "grid": 0}
    thumb = client.get(f"/api/boards/{board['id']}/thumb")
    assert thumb.status_code == 200
    assert Image.open(io.BytesIO(thumb.content)).format == "WEBP"
    used = client.get(f"/api/items/{a['id']}/boards").json()
    assert [x["id"] for x in used] == [board["id"]]
    masonry = _board(client, item_ids=[a["id"]], layout="masonry")
    assert masonry["nodes"][0]["w"] == 300
    listed = client.get("/api/boards").json()
    assert {x["id"] for x in listed} == {board["id"], masonry["id"]}
    assert client.get("/api/boards?q=nothing").json() == []


def test_validation(client, upload):
    a = upload("a.png")["item"]
    for body in (
        {"nodes": [{"type": "sticker"}]},
        {"nodes": [{"id": "x", "type": "note"}, {"id": "x", "type": "note"}]},
        {"nodes": [{"id": "x", "type": "note"}], "edges": [{"from": "x", "to": "nope"}]},
        {"nodes": [{"type": "item"}]},
        {"nodes": [{"type": "note", "w": -1}]},
        {"nodes": [{"type": "item", "item_id": a["id"], "crop": {"x": 0.5, "w": 0.9}}]},
        {"nodes": [{"type": "note", "children": ["a"]}]},
        {"nodes": [{"type": "note", "x": "left"}]},
    ):
        assert client.post("/api/boards", json={"name": "x", **body}).status_code == 422, body
    assert client.post("/api/boards", json={"name": ""}).status_code == 422
    res = client.post("/api/boards", json={"name": "x", "item_ids": ["01NOPE"]})
    assert res.status_code == 404


def test_put_and_patch_versions_and_stale_409(client, upload):
    a = upload("a.png")["item"]
    board = _board(client, item_ids=[a["id"]])
    bid = board["id"]
    doc = {"base_version": 1, "name": "Renamed", "canvas": {"bg": "#000000"},
           "nodes": board["nodes"] + [{"id": "t", "type": "text", "x": 5, "y": 5, "text": "hi"}],
           "edges": [{"id": "e", "from": "t", "to": board["nodes"][0]["id"], "label": "see"}]}
    res = client.put(f"/api/boards/{bid}", json=doc)
    assert res.status_code == 200, res.text
    assert res.json()["version"] == 2 and res.json()["name"] == "Renamed"
    stale = client.put(f"/api/boards/{bid}", json={**doc, "name": "Other"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["board"]["version"] == 2
    # nothing changed: no new version
    same = client.put(f"/api/boards/{bid}", json={**doc, "base_version": 2}).json()
    assert same["version"] == 2

    res = client.patch(f"/api/boards/{bid}", json={
        "base_version": 2,
        "add": [{"id": "g", "type": "group", "x": -10, "y": -10, "w": 500, "h": 500,
                 "children": ["t"], "text": "Refs"}],
        "update": [{"id": "t", "text": "hello", "style": {"color": "#ff0000"}}],
        "edges_update": [{"id": "e", "label": None}],
        "canvas": {"grid": 20},
    })
    assert res.status_code == 200, res.text
    got = res.json()
    assert got["version"] == 3 and got["canvas"] == {"bg": "#000000", "grid": 20}
    t = next(n for n in got["nodes"] if n["id"] == "t")
    assert t["text"] == "hello" and t["style"] == {"color": "#ff0000"}
    assert "label" not in got["edges"][0]
    assert client.patch(f"/api/boards/{bid}", json={"base_version": 2, "remove": ["t"]}
                        ).status_code == 409
    # remove a node: its edges and group membership go too
    got = client.patch(f"/api/boards/{bid}", json={"base_version": 3, "remove": ["t"]}).json()
    assert got["edges"] == [] and next(n for n in got["nodes"] if n["id"] == "g")[
        "children"] == []
    # without base_version the ops apply to the current board (agents)
    got = client.patch(f"/api/boards/{bid}", json={"name": "Latest"}).json()
    assert got["version"] == 5 and got["name"] == "Latest"
    assert client.patch(f"/api/boards/{bid}", json={"update": [{"id": "nope"}]}
                        ).status_code == 422
    assert client.patch(f"/api/boards/{bid}", json={"add": [{"type": "item",
                                                            "item_id": "01NOPE"}]}
                        ).status_code == 404
    added = client.post(f"/api/boards/{bid}/items", json={"item_ids": [a["id"]]}).json()
    assert added["version"] == 6 and added["item_count"] == 1
    assert sum(n["type"] == "item" for n in added["nodes"]) == 2


def test_duplicate_trash_restore_and_hard_delete(client, upload, clean):
    a = upload("a.png")["item"]
    board = _board(client, item_ids=[a["id"]])
    bid = board["id"]
    copy = client.post(f"/api/boards/{bid}/duplicate").json()
    assert copy["name"] == "Mood copy" and copy["version"] == 1
    assert copy["nodes"] == board["nodes"] and copy["id"] != bid

    assert client.delete(f"/api/boards/{bid}?hard=true").status_code == 409  # not trashed
    res = client.delete(f"/api/boards/{bid}")
    assert res.json()["status"] == "trashed"
    assert [b["id"] for b in client.get("/api/boards?state=trashed").json()] == [bid]
    assert bid not in {b["id"] for b in client.get("/api/boards").json()}
    assert client.get(f"/api/items/{a['id']}/boards").json()[0]["id"] == copy["id"]
    stale = client.patch(f"/api/boards/{bid}", json={"name": "x"})
    assert stale.status_code == 409
    agent = as_token(client, make_token(client, "agent", ["agent"])["token"])
    assert agent.delete(f"/api/boards/{bid}?hard=true").status_code == 403
    assert client.post(f"/api/boards/{bid}/restore").json()["state"] == "active"
    client.delete(f"/api/boards/{bid}")
    assert client.delete(f"/api/boards/{bid}?hard=true").json()["hard"] is True
    assert client.get(f"/api/boards/{bid}").status_code == 404
    assert not (clean / "boards" / bid).exists()
    purged = (clean / ".kist" / "board-events-purged.jsonl").read_text().splitlines()
    purged = sorted((json.loads(x) for x in purged), key=lambda e: e["id"])
    assert [e["kind"] for e in purged] == ["create", "trash", "restore", "trash",
                                                       "purge"]
    assert cli.verify() == 0


def test_retention_purges_old_trashed_boards(client, upload):
    from kist.db import SessionLocal
    from kist.models import Board
    from kist.services import boards as boards_svc

    old = _board(client)
    fresh = _board(client)
    client.delete(f"/api/boards/{old['id']}")
    client.delete(f"/api/boards/{fresh['id']}")
    with SessionLocal() as s:
        s.get(Board, old["id"]).deleted_at = datetime.now(UTC) - timedelta(days=40)
        s.commit()
        now = datetime.now(UTC)
        # the retention clock started 35 days ago
        assert boards_svc.purge_expired(s, days=30, now=now - timedelta(days=35)) == []
        assert boards_svc.purge_expired(s, days=30, now=now) == [old["id"]]
    assert client.get(f"/api/boards/{fresh['id']}").json()["state"] == "trashed"


def test_purged_item_leaves_a_missing_node_and_export_still_works(client, upload):
    a = upload("a.png")["item"]
    b = upload("b.png")["item"]
    board = _board(client, item_ids=[a["id"], b["id"]])
    client.delete(f"/api/items/{a['id']}")
    client.delete(f"/api/items/{a['id']}?hard=true")
    got = client.get(f"/api/boards/{board['id']}").json()
    assert got["missing_item_ids"] == [a["id"]] and got["item_count"] == 2
    assert len(got["nodes"]) == 2
    res = client.get(f"/api/boards/{board['id']}/export?format=png")
    assert res.status_code == 200
    # the board can still be saved with the missing node in it
    res = client.patch(f"/api/boards/{board['id']}", json={"name": "Still fine"})
    assert res.status_code == 200, res.text
    assert cli.verify() == 0


def test_export_png_pdf_html_are_valid_files(client, upload):
    a = upload("a.png")["item"]
    board = _board(client, item_ids=[a["id"]], nodes=[
        {"id": "n", "type": "note", "x": 0, "y": -200, "w": 240, "h": 120,
         "text": "<script>alert(1)</script> a long note that has to wrap across lines"},
        {"id": "s", "type": "shape", "x": 300, "y": -200, "w": 100, "h": 100, "rot": 15,
         "style": {"shape": "ellipse", "stroke": "#ff0000"}},
        {"id": "c", "type": "color", "x": 450, "y": -200, "w": 80, "h": 80, "text": "#336699"},
        {"id": "l", "type": "link", "x": 600, "y": -200, "w": 160, "h": 60,
         "style": {"url": "https://example.com"}},
        {"id": "g", "type": "group", "x": -20, "y": -220, "w": 900, "h": 600, "text": "Refs"},
        {"id": "t", "type": "text", "x": 0, "y": 400, "w": 200, "h": 40, "text": "caption"},
    ], edges=[{"id": "e", "from": "n", "to": "s", "label": "this"}])
    item_node = next(n for n in board["nodes"] if n["type"] == "item")
    client.patch(f"/api/boards/{board['id']}", json={
        "update": [{"id": item_node["id"], "crop": {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}}]})
    url = f"/api/boards/{board['id']}/export"

    png = client.get(f"{url}?format=png&scale=1")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(png.content))
    img.verify()
    img = Image.open(io.BytesIO(png.content))
    assert img.format == "PNG" and img.width > 900
    big = Image.open(io.BytesIO(client.get(f"{url}?format=png&scale=2").content))
    assert big.width == 2 * img.width or abs(big.width - 2 * img.width) <= 2
    capped = Image.open(io.BytesIO(
        client.get(f"{url}?format=png&scale=4&max_pixels=100000").content))
    assert capped.width * capped.height <= 100_000

    pdf = client.get(f"{url}?format=pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-") and b"%%EOF" in pdf.content[-64:]

    html = client.get(f"{url}?format=html")
    assert html.status_code == 200 and html.headers["content-type"].startswith("text/html")
    assert "sandbox" in html.headers["content-security-policy"]
    text = html.text
    assert text.startswith("<!doctype html>") and "data:image/webp;base64," in text
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "src=\"http" not in text and "/api/" not in text  # opens offline
    assert 'href="https://example.com"' in text
    assert client.get(f"{url}?format=gif").status_code == 422
    assert client.get(f"{url}?scale=100").status_code == 422


def test_undo_board_edits_per_batch(client, upload):
    a = upload("a.png")["item"]
    board = _board(client, item_ids=[a["id"]])
    bid = board["id"]
    h = {"X-Kist-Batch": "board-edit"}
    client.patch(f"/api/boards/{bid}", json={"name": "Step 1"}, headers=h)
    client.patch(f"/api/boards/{bid}", json={"add": [{"id": "n", "type": "note"}]}, headers=h)
    assert client.get(f"/api/boards/{bid}").json()["version"] == 3
    batches = client.get("/api/batches").json()
    assert batches[0]["batch_id"] == "board-edit" and batches[0]["kinds"] == ["board_update"]
    assert batches[0]["count"] == 2
    undo = client.post("/api/batches/board-edit/undo").json()
    assert undo == {"undone": 2, "skipped": [], "batch_id": undo["batch_id"]}
    got = client.get(f"/api/boards/{bid}").json()
    assert got["name"] == "Mood" and got["nodes"] == board["nodes"] and got["version"] == 5
    # redo = undo the undo
    client.post(f"/api/batches/{undo['batch_id']}/undo")
    got = client.get(f"/api/boards/{bid}").json()
    assert got["name"] == "Step 1" and len(got["nodes"]) == 2

    # a later edit outside the batch: skipped, with the reason
    client.patch(f"/api/boards/{bid}", json={"name": "Mine"}, headers={"X-Kist-Batch": "b1"})
    client.patch(f"/api/boards/{bid}", json={"name": "Someone else"})
    res = client.post("/api/batches/b1/undo").json()
    assert res["undone"] == 0
    assert res["skipped"][0]["target"] == "board" and "changed later" in res["skipped"][0][
        "reason"]

    # undoing the create puts the board in the trash; a trash is undone by a restore
    created = client.post("/api/boards", json={"name": "Oops"},
                          headers={"X-Kist-Batch": "mk"}).json()
    client.post("/api/batches/mk/undo")
    assert client.get(f"/api/boards/{created['id']}").json()["state"] == "trashed"


def test_one_batch_spans_items_and_boards(client, upload):
    a = upload("a.png")["item"]
    board = _board(client)
    h = {"X-Kist-Batch": "mixed"}
    client.patch(f"/api/items/{a['id']}", json={"flag": "pick"}, headers=h)
    client.post(f"/api/boards/{board['id']}/items", json={"item_ids": [a["id"]]}, headers=h)
    batch = next(b for b in client.get("/api/batches").json() if b["batch_id"] == "mixed")
    assert batch["count"] == 2 and batch["kinds"] == ["board_update", "edit"]
    assert client.post("/api/batches/mixed/undo").json()["undone"] == 2
    assert client.get(f"/api/items/{a['id']}").json()["flag"] is None
    assert client.get(f"/api/boards/{board['id']}").json()["nodes"] == []


def test_rebuild_restores_boards_and_verify_reports_drift(client, upload, clean, capsys):
    a = upload("a.png")["item"]
    keep = _board(client, item_ids=[a["id"]])
    client.patch(f"/api/boards/{keep['id']}", json={"name": "Edited"})
    trashed = _board(client)
    client.delete(f"/api/boards/{trashed['id']}")
    before = {b["id"]: client.get(f"/api/boards/{b['id']}").json() for b in (keep, trashed)}
    assert cli.verify() == 0

    assert cli.rebuild_index(wipe=True) == 0
    for bid, doc in before.items():
        assert client.get(f"/api/boards/{bid}").json() == doc
    assert [b["id"] for b in client.get(f"/api/items/{a['id']}/boards").json()] == [keep["id"]]
    batches = client.get("/api/batches").json()
    assert any("board_update" in b["kinds"] for b in batches)
    assert cli.verify() == 0
    # new events continue after the restored ids
    client.patch(f"/api/boards/{keep['id']}", json={"name": "After"})
    assert cli.verify() == 0

    path = clean / "boards" / keep["id"] / "board.json"
    doc = json.loads(path.read_text())
    doc["name"] = "hand edited"
    path.write_text(json.dumps(doc))
    (clean / "boards" / "01STRAY").mkdir()
    capsys.readouterr()
    assert cli.verify() == 2
    out = capsys.readouterr().out
    assert "board.json differs" in out and "01STRAY" in out
    assert cli.rewrite_meta() == 0
    (clean / "boards" / "01STRAY").rmdir()
    assert cli.verify() == 0


def test_folder_limited_reader_sees_only_fully_visible_boards(client, upload):
    fa = client.post("/api/folders", json={"name": "A"}).json()
    fb = client.post("/api/folders", json={"name": "B"}).json()
    seen = upload("seen.png", folder_ids=json.dumps([fa["id"]]))["item"]
    hidden = upload("hidden.png", folder_ids=json.dumps([fb["id"]]))["item"]
    ok = _board(client, name="Visible", item_ids=[seen["id"]])
    mixed = _board(client, name="Mixed", item_ids=[seen["id"], hidden["id"]])
    notes = _board(client, name="Notes", nodes=[{"type": "note", "text": "private"}])
    limited = as_token(client, make_token(client, "fam", folder_ids=[fa["id"]])["token"])
    reader = as_token(client, make_token(client, "reader")["token"])

    assert [b["id"] for b in limited.get("/api/boards").json()] == [ok["id"]]
    assert limited.get(f"/api/boards/{ok['id']}").status_code == 200
    for b in (mixed, notes):
        assert limited.get(f"/api/boards/{b['id']}").status_code == 404
        assert limited.get(f"/api/boards/{b['id']}/export?format=html").status_code == 404
        assert limited.get(f"/api/boards/{b['id']}/thumb").status_code == 404
    assert [b["id"] for b in limited.get(f"/api/items/{seen['id']}/boards").json()] == [ok["id"]]
    assert limited.get(f"/api/items/{hidden['id']}/boards").status_code == 404
    assert limited.get(f"/api/boards/{ok['id']}/export?format=png").status_code == 200
    assert limited.patch(f"/api/boards/{ok['id']}", json={"name": "x"}).status_code == 403
    assert limited.post(f"/api/boards/{ok['id']}/duplicate").status_code == 404

    assert len(reader.get("/api/boards").json()) == 3
    assert reader.put(f"/api/boards/{ok['id']}", json={"base_version": 1, "name": "x"}
                      ).status_code == 403
    assert reader.post("/api/boards", json={"name": "x"}).status_code == 403
    out = mcp_result(limited, "list_boards")
    rows = [json.loads(c["text"]) for c in out["content"]]
    assert [r["id"] for r in rows] == [ok["id"]]


def test_mcp_board_tools(client, upload):
    a = upload("a.png")["item"]
    b = upload("b.png")["item"]
    board = _text(mcp_result(client, "create_board", {"name": "Agent board",
                                                       "item_ids": [a["id"]],
                                                       "layout": "masonry"}))
    assert board["version"] == 1 and board["web_url"].endswith(f"/boards/{board['id']}")
    board = _text(mcp_result(client, "add_to_board", {"id": board["id"], "item_ids": [b["id"]],
                                                      "notes": ["sector dials"]}))
    assert [n["type"] for n in board["nodes"]] == ["item", "item", "note"]
    got = _text(mcp_result(client, "get_board", {"id": board["id"]}))
    assert got["version"] == 3
    listed = mcp_result(client, "list_boards")
    assert json.loads(listed["content"][0]["text"])["id"] == board["id"]
    err = mcp_result(client, "get_board", {"id": "01NOPE"})
    assert err["isError"] and "not found" in err["content"][0]["text"]
