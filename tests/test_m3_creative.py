"""Milestone 3 backend: flags and labels, annotations, versions and compare,
templates and bulk rename, triage, the command palette, and quick look's
Range requests. Boards are in test_m3_boards.py."""

import json

from conftest import png_bytes
from test_tokens import as_token, make_token, mcp_result

from kist import cli

# ---------- flags and labels ----------


def test_flag_and_label_patch_filter_meta_and_undo(client, upload, clean):
    a = upload("a.png")["item"]
    b = upload("b.png")["item"]
    assert a["flag"] is None and a["label"] is None
    res = client.patch(f"/api/items/{a['id']}", json={"flag": "pick", "label": "red"},
                       headers={"X-Kist-Batch": "flag-batch"})
    assert res.status_code == 200, res.text
    assert res.json()["flag"] == "pick" and res.json()["label"] == "red"
    client.patch(f"/api/items/{b['id']}", json={"flag": "reject"})

    ids = lambda q: [i["id"] for i in client.get(f"/api/items?{q}").json()["items"]]  # noqa: E731
    assert ids("flag=pick") == [a["id"]]
    assert ids("flag=reject") == [b["id"]]
    assert ids("label=red") == [a["id"]]
    assert set(ids("label=none")) == {b["id"]}
    assert client.get("/api/items?flag=maybe").status_code == 422
    assert client.patch(f"/api/items/{a['id']}", json={"label": "pink"}).status_code == 422

    meta = json.loads((clean / "items" / a["id"] / "meta.json").read_text())
    assert meta["flag"] == "pick" and meta["label"] == "red"
    hist = client.get(f"/api/items/{a['id']}/history").json()
    assert hist[0]["kind"] == "edit" and hist[0]["after"] == {"flag": "pick", "label": "red"}

    # null clears; undo of the first batch is then skipped (changed later)
    assert client.patch(f"/api/items/{b['id']}", json={"flag": None}).json()["flag"] is None
    undo = client.post("/api/batches/flag-batch/undo").json()
    assert undo["undone"] == 1
    got = client.get(f"/api/items/{a['id']}").json()
    assert got["flag"] is None and got["label"] is None


def test_flag_label_survive_rebuild_and_verify_sees_drift(client, upload, clean, capsys):
    a = upload("a.png")["item"]
    client.patch(f"/api/items/{a['id']}", json={"flag": "pick", "label": "blue"})
    assert cli.verify() == 0
    assert cli.rebuild_index(wipe=True) == 0
    got = client.get(f"/api/items/{a['id']}").json()
    assert (got["flag"], got["label"]) == ("pick", "blue")
    # drift: meta.json edited by hand
    path = clean / "items" / a["id"] / "meta.json"
    meta = json.loads(path.read_text())
    meta["label"] = "green"
    path.write_text(json.dumps(meta))
    capsys.readouterr()
    assert cli.verify() == 2
    assert "label" in capsys.readouterr().out


def test_mcp_update_item_sets_flag_and_label(client, upload):
    a = upload("a.png")["item"]
    out = mcp_result(client, "update_item", {"id": a["id"], "flag": "reject", "label": "purple"})
    item = json.loads(out["content"][0]["text"])
    assert item["flag"] == "reject" and item["label"] == "purple"
    out = mcp_result(client, "search_items", {"flag": "reject"})
    assert [i["id"] for i in json.loads(out["content"][0]["text"])["items"]] == [a["id"]]
    out = mcp_result(client, "update_item", {"id": a["id"], "flag": "none"})
    assert json.loads(out["content"][0]["text"])["flag"] is None


# ---------- annotations ----------


def test_annotation_crud_search_events_and_file(client, upload, clean):
    a = upload("collar.png")["item"]
    url = f"/api/items/{a['id']}/annotations"
    res = client.post(url, json={"kind": "box", "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4,
                                 "text": "this collar roll"})
    assert res.status_code == 201, res.text
    box = res.json()
    assert box["author"] == "owner" and box["resolved"] is False
    pin = client.post(url, json={"kind": "point", "x": 0.5, "y": 0.5, "text": "kerning"}).json()
    rng = client.post(url, json={"kind": "range", "t_start": 2.5, "t_end": 4,
                                 "text": "the cut"}).json()
    assert rng["t_end"] == 4.0
    for bad in (
        {"kind": "box", "x": 0.8, "y": 0, "w": 0.5, "h": 0.1},
        {"kind": "point", "x": 1.5, "y": 0},
        {"kind": "point", "x": 0.1},
        {"kind": "range", "t_start": 5, "t_end": 1},
        {"kind": "range"},
    ):
        assert client.post(url, json=bad).status_code == 422, bad
    assert [x["id"] for x in client.get(url).json()] == [box["id"], pin["id"], rng["id"]]

    # q finds annotation text
    found = client.get("/api/items?q=collar%20roll").json()["items"]
    assert [i["id"] for i in found] == [a["id"]]

    res = client.patch(f"{url}/{pin['id']}", json={"text": "tracking", "resolved": True})
    assert res.json()["text"] == "tracking" and res.json()["resolved"] is True
    assert client.patch(f"{url}/{pin['id']}", json={"x": 2}).status_code == 422
    assert client.delete(f"{url}/{rng['id']}").status_code == 200
    assert client.delete(f"{url}/{rng['id']}").status_code == 404

    disk = json.loads((clean / "items" / a["id"] / "annotations.json").read_text())
    assert {r["id"] for r in disk["annotations"]} == {box["id"], pin["id"]}
    kinds = [e["kind"] for e in client.get(f"/api/items/{a['id']}/history").json()]
    assert kinds.count("annotation") == 5
    assert cli.verify() == 0


def test_annotation_undo_and_rebuild(client, upload, clean):
    a = upload("a.png")["item"]
    url = f"/api/items/{a['id']}/annotations"
    keep = client.post(url, json={"kind": "point", "x": 0.2, "y": 0.2, "text": "keep"}).json()
    gone = client.post(url, json={"kind": "point", "x": 0.3, "y": 0.3, "text": "oops"},
                       headers={"X-Kist-Batch": "ann-add"}).json()
    client.patch(f"{url}/{keep['id']}", json={"text": "changed"},
                 headers={"X-Kist-Batch": "ann-edit"})
    assert client.post("/api/batches/ann-add/undo").json()["undone"] == 1
    assert client.post("/api/batches/ann-edit/undo").json()["undone"] == 1
    assert client.get(url).json()[0]["text"] == "keep"

    client.delete(f"{url}/{keep['id']}", headers={"X-Kist-Batch": "ann-del"})
    assert client.get(url).json() == []
    assert client.post("/api/batches/ann-del/undo").json()["undone"] == 1
    back = client.get(url).json()
    assert [(x["id"], x["text"]) for x in back] == [(keep["id"], "keep")]
    assert gone["id"] not in {x["id"] for x in back}
    # the edit was already undone and the annotation deleted since: skipped
    again = client.post("/api/batches/ann-edit/undo").json()
    assert again["undone"] == 0 and again["skipped"]

    assert cli.rebuild_index(wipe=True) == 0
    back = client.get(url).json()
    assert [(x["id"], x["text"]) for x in back] == [(keep["id"], "keep")]
    assert cli.verify() == 0
    # drift: a hand-edited annotations.json is reported
    path = clean / "items" / a["id"] / "annotations.json"
    data = json.loads(path.read_text())
    data["annotations"][0]["text"] = "edited"
    path.write_text(json.dumps(data))
    assert cli.verify() == 2


def test_mcp_annotate(client, upload):
    a = upload("dial.png")["item"]
    out = mcp_result(client, "annotate", {"item_id": a["id"], "text": "sector dial",
                                          "box": [0, 0, 0.5, 0.5]})
    rows = json.loads(out["content"][0]["text"]) if len(out["content"]) == 1 else [
        json.loads(c["text"]) for c in out["content"]]
    rows = rows if isinstance(rows, list) else [rows]
    assert rows[0]["kind"] == "box" and rows[0]["text"] == "sector dial"
    out = mcp_result(client, "annotate", {"item_id": a["id"], "text": "at 3s", "time": [3]})
    assert not out.get("isError"), out
    kinds = {x["kind"] for x in client.get(f"/api/items/{a['id']}/annotations").json()}
    assert kinds == {"box", "range"}
    assert client.get("/api/items?q=sector").json()["items"][0]["id"] == a["id"]


# ---------- versions and compare ----------


def test_versions_chain_upload_and_ids_fetch(client, upload, clean):
    folder = client.post("/api/folders", json={"name": "Logos"}).json()
    v1 = upload("logo.png", tags="brand", folder_ids=json.dumps([folder["id"]]))["item"]
    res = client.post(f"/api/items/{v1['id']}/versions",
                      files={"file": ("logo-v2.png", png_bytes((1, 2, 3)), "image/png")})
    assert res.status_code == 201, res.text
    v2 = res.json()["item"]
    assert v2["folder_ids"] == [folder["id"]] and v2["tags"] == ["brand"]
    # uploading against the old one still extends the chain at its head
    v3 = client.post(f"/api/items/{v1['id']}/versions",
                     files={"file": ("logo-v3.png", png_bytes((4, 5, 6)), "image/png")}).json()
    v3 = v3["item"]
    chain = client.get(f"/api/items/{v2['id']}/versions").json()
    assert [i["id"] for i in chain["items"]] == [v1["id"], v2["id"], v3["id"]]
    assert chain["current_id"] == v3["id"]
    links = client.get(f"/api/items/{v3['id']}/links").json()
    assert links == [{"item": links[0]["item"], "kind": "version_of", "direction": "out"}]
    assert links[0]["item"]["id"] == v2["id"]
    lone = upload("lone.png")["item"]
    assert client.get(f"/api/items/{lone['id']}/versions").json()["current_id"] == lone["id"]
    assert client.post("/api/items/01NOPE/versions",
                       files={"file": ("x.png", png_bytes(), "image/png")}).status_code == 404

    # compare: these items, in this order; unknown ids are left out
    got = client.get(f"/api/items?ids={v3['id']},01NOPE,{v1['id']},{v3['id']}").json()
    assert [i["id"] for i in got["items"]] == [v3["id"], v1["id"]]
    assert got["total"] == 2 and got["next_cursor"] is None


def test_a_trashed_version_is_not_current(client, upload, clean):
    """Trashing the newest version left it in the chain as 'v2 · current'
    (seen on dev0); the live one before it is current again, a new upload
    extends from that, and the trashed one still sees its chain."""
    v1 = upload("room.png")["item"]
    v2 = client.post(f"/api/items/{v1['id']}/versions",
                     files={"file": ("room-v2.png", png_bytes((9, 9, 9)), "image/png")})
    v2 = v2.json()["item"]
    assert client.delete(f"/api/items/{v2['id']}").json()["status"] == "trashed"
    chain = client.get(f"/api/items/{v1['id']}/versions").json()
    assert [i["id"] for i in chain["items"]] == [v1["id"]]
    assert chain["current_id"] == v1["id"]
    own = client.get(f"/api/items/{v2['id']}/versions").json()
    assert [i["id"] for i in own["items"]] == [v1["id"], v2["id"]] and own["current_id"] == v1["id"]
    v3 = client.post(f"/api/items/{v1['id']}/versions",
                     files={"file": ("room-v3.png", png_bytes((7, 7, 7)), "image/png")})
    v3 = v3.json()["item"]
    links = client.get(f"/api/items/{v3['id']}/links").json()
    assert [x["item"]["id"] for x in links if x["kind"] == "version_of"] == [v1["id"]]
    assert client.get(f"/api/items/{v1['id']}/versions").json()["current_id"] == v3["id"]
    # restored: it's back in the chain
    client.post(f"/api/items/{v2['id']}/restore")
    ids = [i["id"] for i in client.get(f"/api/items/{v1['id']}/versions").json()["items"]]
    assert set(ids) == {v1["id"], v2["id"], v3["id"]}


def test_file_serves_http_range_for_the_loupe(client, upload):
    data = png_bytes(size=(300, 200))
    a = upload("big.png", data=data)["item"]
    res = client.get(f"/api/items/{a['id']}/file", headers={"Range": "bytes=0-99"})
    assert res.status_code == 206
    assert res.content == data[:100]
    assert res.headers["content-range"] == f"bytes 0-99/{len(data)}"
    assert res.headers["accept-ranges"] == "bytes"
    tail = client.get(f"/api/items/{a['id']}/file", headers={"Range": "bytes=-10"})
    assert tail.status_code == 206 and tail.content == data[-10:]
    bad = client.get(f"/api/items/{a['id']}/file", headers={"Range": f"bytes={len(data)}-"})
    assert bad.status_code == 416
    # the 2048 display copy is served for the loupe (falls back to a smaller copy)
    assert client.get(f"/api/items/{a['id']}/thumb?size=2048").status_code == 200


# ---------- templates ----------


def test_template_crud_apply_as_one_batch_and_undo(client, upload, clean):
    folder = client.post("/api/folders", json={"name": "Shoot"}).json()
    res = client.post("/api/templates", json={"name": "Client shoot", "fields": {
        "tags_add": ["client:acme"], "folder_add": [folder["id"]], "notes_append": "Shot for Acme",
        "source_url": "https://acme.example/brief", "rating": 4, "label": "green",
        "flag": "pick", "credit": "Jo Smith", "license": "CC-BY"}})
    assert res.status_code == 201, res.text
    tpl = res.json()
    assert tpl["fields"]["label"] == "green"
    assert client.get("/api/templates").json()[0]["id"] == tpl["id"]
    assert (clean / ".kist" / "templates.json").is_file()
    bad = client.post("/api/templates", json={"name": "x", "fields": {"folder_add": ["01NO"]}})
    assert bad.status_code == 422

    a = upload("a.png", notes="existing")["item"]
    b = upload("b.png")["item"]
    res = client.post(f"/api/templates/{tpl['id']}/apply",
                      json={"item_ids": [a["id"], b["id"]]})
    assert res.status_code == 200, res.text
    batch = res.json()["batch_id"]
    for it in res.json()["items"]:
        assert set(it["tags"]) == {"client:acme", "credit:Jo Smith", "license:CC-BY"}
        assert it["folder_ids"] == [folder["id"]]
        assert (it["rating"], it["label"], it["flag"]) == (4, "green", "pick")
        assert it["source_url"] == "https://acme.example/brief"
    got_a = client.get(f"/api/items/{a['id']}").json()
    assert got_a["notes"] == "existing\nShot for Acme"
    batches = client.get("/api/batches").json()
    assert batches[0]["batch_id"] == batch

    undo = client.post(f"/api/batches/{batch}/undo").json()
    assert undo["skipped"] == []
    for item, notes in ((a, "existing"), (b, "")):
        got = client.get(f"/api/items/{item['id']}").json()
        assert got["tags"] == [] and got["folder_ids"] == []
        assert (got["rating"], got["label"], got["flag"], got["notes"]) == (0, None, None, notes)

    # unknown item: nothing is applied
    res = client.post(f"/api/templates/{tpl['id']}/apply", json={"item_ids": [a["id"], "01NO"]})
    assert res.status_code == 404
    assert client.get(f"/api/items/{a['id']}").json()["tags"] == []

    res = client.put(f"/api/templates/{tpl['id']}", json={"name": "Renamed", "fields": {}})
    assert res.json()["name"] == "Renamed" and res.json()["fields"]["tags_add"] == []
    assert cli.verify() == 0
    assert client.delete(f"/api/templates/{tpl['id']}").status_code == 200
    assert client.delete(f"/api/templates/{tpl['id']}").status_code == 404


def test_template_survives_rebuild_and_verify_flags_lost_folder(client, upload, clean, capsys):
    upload("a.png")
    folder = client.post("/api/folders", json={"name": "F"}).json()
    client.post("/api/templates", json={"name": "T", "fields": {"folder_add": [folder["id"]]}})
    assert cli.rebuild_index(wipe=True) == 0
    assert [t["name"] for t in client.get("/api/templates").json()] == ["T"]
    client.delete(f"/api/folders/{folder['id']}")
    capsys.readouterr()
    assert cli.verify() == 2
    assert "no longer exists" in capsys.readouterr().out


# ---------- bulk rename ----------


def test_bulk_rename_dry_run_tokens_collisions_and_undo(client, upload, clean):
    folder = client.post("/api/folders", json={"name": "Acme"}).json()
    fids = json.dumps([folder["id"]])
    a = upload("IMG_001.png", tags="client:acme", folder_ids=fids)["item"]
    b = upload("IMG_002.png", tags="client:acme", folder_ids=fids)["item"]
    upload("acme-002.png")  # another item already has a would-be name
    body = {"item_ids": [a["id"], b["id"]], "pattern": "{tag:client}-{n:3}", "start": 1}
    res = client.post("/api/items/rename", json=body)
    assert res.status_code == 200, res.text
    rows = res.json()
    assert rows["dry_run"] is True and rows["batch_id"] is None
    assert [(r["before"], r["after"]) for r in rows["items"]] == [
        ("IMG_001.png", "acme-001.png"), ("IMG_002.png", "acme-002 (2).png")]
    assert client.get(f"/api/items/{a['id']}").json()["filename"] == "IMG_001.png"

    res = client.post("/api/items/rename", json={
        "item_ids": [a["id"], b["id"]], "pattern": "{folder}_{w}x{h}_{date:%Y}.{ext}",
        "dry_run": True}).json()
    year = a["created_at"][:4]
    assert [r["after"] for r in res["items"]] == [
        f"Acme_640x480_{year}.png", f"Acme_640x480_{year} (2).png"]

    res = client.post("/api/items/rename", json={**body, "dry_run": False}).json()
    assert res["batch_id"]
    assert client.get(f"/api/items/{b['id']}").json()["filename"] == "acme-002 (2).png"
    undo = client.post(f"/api/batches/{res['batch_id']}/undo").json()
    assert undo["undone"] == 2
    assert client.get(f"/api/items/{a['id']}").json()["filename"] == "IMG_001.png"

    for pattern in ("{bogus}", "{n:x}", "{tag}", " ", "{tag:nope}"):
        r = client.post("/api/items/rename", json={**body, "pattern": pattern})
        assert r.status_code == 422, pattern


# ---------- triage and palette ----------


def test_triage_queue_walks_the_view_in_order(client, upload):
    items = [upload(f"t{i}.png")["item"] for i in range(4)]
    newest_first = [i["id"] for i in reversed(items)]
    res = client.get("/api/triage/next?view=unfiled&prefetch=2").json()
    assert res["item"]["id"] == newest_first[0]
    assert res["prefetch_ids"] == newest_first[1:3]
    assert res["remaining"] == 4
    # file the first; the queue carries on after it although it left the view
    client.patch(f"/api/items/{newest_first[0]}/tags", json={"add": ["done"]})
    res = client.get(f"/api/triage/next?after={newest_first[0]}&prefetch=5").json()
    assert res["item"]["id"] == newest_first[1]
    assert res["prefetch_ids"] == newest_first[2:]
    assert res["remaining"] == 3
    res = client.get(f"/api/triage/next?after={newest_first[-1]}").json()
    assert res["item"] is None and res["prefetch_ids"] == []
    res = client.get("/api/triage/next?view=all&order=created&prefetch=0").json()
    assert res["item"]["id"] == items[0]["id"] and res["remaining"] == 4
    assert client.get("/api/triage/next?view=bogus").status_code == 422
    assert client.get("/api/triage/next?after=01NOPE").status_code == 404


def test_triage_decisions_take_items_out_of_the_unfiled_queue(client, upload):
    items = [upload(f"d{i}.png")["item"] for i in range(5)]
    q = lambda extra="": client.get(f"/api/triage/next?view=unfiled{extra}").json()  # noqa: E731
    assert q()["remaining"] == 5
    a, b, c, d, _ = (i["id"] for i in items)
    client.patch(f"/api/items/{a}", json={"flag": "pick"})
    client.patch(f"/api/items/{b}", json={"flag": "reject"})
    client.patch(f"/api/items/{c}", json={"rating": 3})
    client.patch(f"/api/items/{d}", json={"label": "red"}, headers={"X-Kist-Batch": "lab"})
    res = q()
    assert res["remaining"] == 1 and res["item"]["id"] == items[4]["id"]
    # still in the Unfiled view: a decision isn't filing
    assert client.get("/api/items?view=unfiled").json()["total"] == 5
    # the cursor carries on after a decided item (newest first: e is first)
    assert q(f"&after={d}")["item"] is None
    # undo puts it back in the queue
    assert client.post("/api/batches/lab/undo").status_code == 200
    assert q()["remaining"] == 2
    # undecided=false: the whole view, as before
    assert q("&undecided=false")["remaining"] == 5
    # view=all keeps every item unless asked
    all_ = client.get("/api/triage/next?view=all").json()
    assert all_["remaining"] == 5
    assert client.get("/api/triage/next?view=all&undecided=true").json()["remaining"] == 2


def test_palette_mixes_kinds(client, upload):
    client.post("/api/folders", json={"name": "Moodboards"})
    client.post("/api/smart-folders", json={"name": "Mood picks", "query": {"q": "x"}})
    upload("mood-01.png", tags="mood")
    client.post("/api/boards", json={"name": "Mood for Acme"})
    res = client.get("/api/palette?q=mood")
    assert res.status_code == 200, res.text
    kinds = {r["kind"] for r in res.json()}
    assert {"folder", "smart_folder", "tag", "board", "item"} <= kinds
    assert all(set(r) == {"kind", "id", "title", "subtitle"} for r in res.json())
    actions = client.get("/api/palette?q=trash").json()
    assert {"kind": "action", "id": "trash", "title": "Trash", "subtitle": ""} in actions
    assert all(r["kind"] == "action" for r in client.get("/api/palette").json())


# ---------- scopes ----------


def test_scopes_for_m3_endpoints(client, upload):
    folder_a = client.post("/api/folders", json={"name": "A"}).json()
    folder_b = client.post("/api/folders", json={"name": "B"}).json()
    seen = upload("seen.png", folder_ids=json.dumps([folder_a["id"]]))["item"]
    hidden = upload("hidden.png", folder_ids=json.dumps([folder_b["id"]]))["item"]
    client.post(f"/api/items/{seen['id']}/annotations",
                json={"kind": "point", "x": 0.1, "y": 0.1, "text": "hello"})
    reader = as_token(client, make_token(client, "reader")["token"])
    limited = as_token(client, make_token(client, "fam", folder_ids=[folder_a["id"]])["token"])
    adder = as_token(client, make_token(client, "adder", ["add"])["token"])
    agent = as_token(client, make_token(client, "agent", ["agent"])["token"])

    # readers can read but not write
    assert reader.get(f"/api/items/{seen['id']}/annotations").status_code == 200
    assert reader.get("/api/palette?q=s").status_code == 200
    assert reader.get("/api/triage/next").status_code == 200
    assert reader.get("/api/templates").status_code == 200
    for method, url, body in (
        ("post", f"/api/items/{seen['id']}/annotations", {"kind": "point", "x": 0, "y": 0}),
        ("post", "/api/templates", {"name": "x"}),
        ("post", "/api/items/rename", {"item_ids": [seen["id"]], "pattern": "x"}),
        ("patch", f"/api/items/{seen['id']}", {"flag": "pick"}),
    ):
        assert getattr(reader, method)(url, json=body).status_code == 403
        assert getattr(adder, method)(url, json=body).status_code == 403
    assert agent.post("/api/templates", json={"name": "ok"}).status_code == 201
    assert agent.post(f"/api/items/{seen['id']}/annotations",
                      json={"kind": "point", "x": 0, "y": 0}).status_code == 201

    # a folder-limited reader: annotations/versions of what it sees only
    assert limited.get(f"/api/items/{seen['id']}/annotations").status_code == 200
    assert limited.get(f"/api/items/{hidden['id']}/annotations").status_code == 404
    assert limited.get(f"/api/items/{seen['id']}/versions").status_code == 200
    assert limited.get(f"/api/items/{hidden['id']}/versions").status_code == 404
    got = limited.get(f"/api/items?ids={hidden['id']},{seen['id']}").json()
    assert [i["id"] for i in got["items"]] == [seen["id"]]
    for path in ("/api/palette?q=s", "/api/triage/next", "/api/templates"):
        assert limited.get(path).status_code == 404, path
    # annotation text of an invisible item doesn't leak through q
    client.post(f"/api/items/{hidden['id']}/annotations",
                json={"kind": "point", "x": 0.1, "y": 0.1, "text": "secret"})
    assert limited.get("/api/items?q=secret").json()["items"] == []
