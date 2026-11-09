"""Item history (events), batches and undo."""

import json

import sqlalchemy as sa
from fastapi.testclient import TestClient

from kist import cli

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def mcp(client, name, args=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": args or {}}}
    res = client.post("/mcp", json=body, headers=MCP_HEADERS)
    assert res.status_code == 200, res.text
    return res.json()["result"]


def batch(name):
    return {"X-Kist-Batch": name}


def history(client, item_id):
    res = client.get(f"/api/items/{item_id}/history")
    assert res.status_code == 200, res.text
    return res.json()


def disk_events(library, item_id):
    path = library / "items" / item_id / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_every_change_is_an_event_with_a_file_copy(client, upload, clean):
    folder = client.post("/api/folders", json={"name": "F"}).json()
    item = upload("e.png")["item"]
    iid = item["id"]
    client.patch(f"/api/items/{iid}", json={"rating": 4, "notes": "hi"})
    client.patch(f"/api/items/{iid}/tags", json={"add": ["red"]})
    client.patch(f"/api/items/{iid}/folders", json={"add": [folder["id"]]})
    client.patch(f"/api/items/{iid}", json={"protected": True})
    client.patch(f"/api/items/{iid}", json={"protected": False})
    client.delete(f"/api/items/{iid}")
    client.post(f"/api/items/{iid}/restore")

    events = history(client, iid)
    assert [e["kind"] for e in events] == [
        "restore", "trash", "protect", "protect", "folders", "tags", "edit", "create",
    ]
    edit = events[-2]
    assert edit["before"] == {"rating": 0, "notes": ""}
    assert edit["after"] == {"rating": 4, "notes": "hi"}
    assert events[-3]["after"] == {"tags": ["red"]}
    assert all(e["actor"] == "owner" and e["source"] == "api" for e in events)
    # one batch per request
    assert len({e["batch_id"] for e in events}) == len(events)
    on_disk = disk_events(clean, iid)
    assert [e["id"] for e in on_disk] == sorted(e["id"] for e in events)
    assert on_disk[-1]["kind"] == "restore"


def test_batch_header_and_web_source(client, upload):
    a, b = upload()["item"], upload()["item"]
    for item in (a, b):
        client.patch(f"/api/items/{item['id']}/tags", json={"add": ["x"]},
                     headers={**batch("retag-1"), "X-Kist-Client": "web"})
    batches = client.get("/api/batches").json()
    assert batches[0]["batch_id"] == "retag-1"
    assert batches[0]["count"] == 2 and batches[0]["kinds"] == ["tags"]
    assert batches[0]["source"] == "web" and batches[0]["actor"] == "owner"
    # a bad batch id is replaced, not trusted
    client.patch(f"/api/items/{a['id']}", json={"rating": 1}, headers=batch("no spaces!"))
    assert client.get("/api/batches?limit=1").json()[0]["batch_id"] != "no spaces!"


def test_undo_a_batch_and_redo_it(client, upload):
    items = [upload()["item"] for _ in range(3)]
    for item in items:
        client.patch(f"/api/items/{item['id']}/tags", json={"add": ["wrong"]},
                     headers=batch("agent-run"))
        client.patch(f"/api/items/{item['id']}", json={"rating": 5}, headers=batch("agent-run"))
    res = client.post("/api/batches/agent-run/undo")
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["undone"] == 6 and out["skipped"] == []
    for item in items:
        got = client.get(f"/api/items/{item['id']}").json()
        assert got["tags"] == [] and got["rating"] == 0
    # the undo is a batch of its own: undoing it redoes the change
    assert client.get("/api/batches?limit=1").json()[0]["batch_id"] == out["batch_id"]
    assert client.post(f"/api/batches/{out['batch_id']}/undo").json()["undone"] == 6
    got = client.get(f"/api/items/{items[0]['id']}").json()
    assert got["tags"] == ["wrong"] and got["rating"] == 5


def test_undo_skips_what_changed_later(client, upload):
    item = upload()["item"]
    iid = item["id"]
    client.patch(f"/api/items/{iid}", json={"rating": 2}, headers=batch("b1"))
    client.patch(f"/api/items/{iid}", json={"notes": "keep?"}, headers=batch("b1"))
    client.patch(f"/api/items/{iid}/tags", json={"add": ["x", "y"]}, headers=batch("b1"))
    client.patch(f"/api/items/{iid}", json={"rating": 4})  # a later, separate edit
    client.patch(f"/api/items/{iid}/tags", json={"remove": ["y"]})
    out = client.post("/api/batches/b1/undo").json()
    assert out["undone"] == 1  # the notes
    reasons = {s["reason"] for s in out["skipped"]}
    assert reasons == {"changed later: rating", "changed later: tag:y"}
    assert all(s["item_id"] == iid for s in out["skipped"])
    got = client.get(f"/api/items/{iid}").json()
    assert got["rating"] == 4 and got["notes"] == "" and got["tags"] == ["x"]


def test_revert_one_event(client, upload):
    item = upload()["item"]
    client.patch(f"/api/items/{item['id']}", json={"filename": "renamed.png", "rating": 3})
    edit = history(client, item["id"])[0]
    out = client.post(f"/api/events/{edit['id']}/revert").json()
    assert out["undone"] == 1
    got = client.get(f"/api/items/{item['id']}").json()
    assert got["filename"] == "e.png" or got["filename"] == item["filename"]
    assert got["rating"] == 0
    assert client.post("/api/events/999999/revert").status_code == 404
    assert client.post("/api/batches/nope/undo").status_code == 404


def test_undo_trash_create_and_purge(client, upload, clean):
    a = upload()["item"]
    client.delete(f"/api/items/{a['id']}", headers=batch("trash-a"))
    assert client.post("/api/batches/trash-a/undo").json()["undone"] == 1
    assert client.get(f"/api/items/{a['id']}").json()["state"] == "active"

    # the item was trashed and restored since it was made: that touched its state
    create = history(client, a["id"])[-1]
    assert create["kind"] == "create" and create["after"]["state"] == "active"
    out = client.post(f"/api/events/{create['id']}/revert").json()
    assert out["undone"] == 0 and out["skipped"][0]["reason"] == "changed later: state"
    b = upload()["item"]
    create = history(client, b["id"])[-1]
    assert client.post(f"/api/events/{create['id']}/revert").json()["undone"] == 1
    assert client.get(f"/api/items/{b['id']}").json()["state"] == "trashed"
    client.delete(f"/api/items/{a['id']}")

    client.delete(f"/api/items/{a['id']}?hard=true", headers=batch("purge-a"))
    out = client.post("/api/batches/purge-a/undo").json()
    assert out["undone"] == 0 and "permanently deleted" in out["skipped"][0]["reason"]
    # its whole history survives the purge, on disk too
    purged = [json.loads(x) for x in
              (clean / ".kist" / "events-purged.jsonl").read_text().splitlines()]
    kinds = sorted(e["kind"] for e in purged if e["item_id"] == a["id"])
    assert "purge" in kinds and "create" in kinds and "trash" in kinds


def test_undo_respects_protection_and_scopes(client, upload):
    item = upload()["item"]
    agent_token = client.post("/api/tokens", json={"name": "Claude", "scopes": ["agent"]}).json()
    agent = TestClient(client.app, headers={"Authorization": f"Bearer {agent_token['token']}"})
    assert agent.patch(f"/api/items/{item['id']}", json={"protected": True},
                       headers=batch("lock")).status_code == 200
    # an agent can protect but never unprotect, not even by undo
    res = agent.patch(f"/api/items/{item['id']}", json={"protected": False})
    assert res.status_code == 403 and res.json() == {"detail": "This token can't do that"}
    out = agent.post("/api/batches/lock/undo").json()
    assert out["undone"] == 0 and out["skipped"][0]["reason"] == "This token can't do that"
    assert history(client, item["id"])[0]["actor"] == "Claude"
    # the owner can
    assert client.post("/api/batches/lock/undo").json()["undone"] == 1

    reader_token = client.post("/api/tokens", json={"name": "R", "scopes": ["read"]}).json()
    reader = TestClient(client.app, headers={"Authorization": f"Bearer {reader_token['token']}"})
    assert reader.get("/api/batches").status_code == 200
    assert reader.get(f"/api/items/{item['id']}/history").status_code == 200
    assert reader.post("/api/batches/lock/undo").status_code == 403


def test_deleting_a_folder_records_the_unfiling(client, upload):
    parent = client.post("/api/folders", json={"name": "P"}).json()
    child = client.post("/api/folders", json={"name": "C", "parent_id": parent["id"]}).json()
    item = upload(folder_ids=json.dumps([child["id"]]))["item"]
    client.delete(f"/api/folders/{parent['id']}")
    last = history(client, item["id"])[0]
    assert last["kind"] == "folders"
    assert last["before"] == {"folder_ids": [child["id"]]} and last["after"] == {"folder_ids": []}


def test_mcp_tool_calls_are_batches_an_agent_can_undo(client, upload):
    a, b = upload()["item"], upload()["item"]
    mcp(client, "set_tags", {"id": a["id"], "add": ["mcp"]})
    mcp(client, "set_tags", {"id": b["id"], "add": ["mcp"]})
    recent = [json.loads(c["text"])
              for c in mcp(client, "recent_batches", {"limit": 5})["content"]]
    assert recent[0]["source"] == "mcp" and recent[0]["count"] == 1
    assert recent[0]["batch_id"] != recent[1]["batch_id"]  # one per tool call
    res = mcp(client, "undo_batch", {"batch_id": recent[0]["batch_id"]})
    assert json.loads(res["content"][0]["text"])["undone"] == 1
    assert client.get(f"/api/items/{b['id']}").json()["tags"] == []
    assert client.get(f"/api/items/{a['id']}").json()["tags"] == ["mcp"]
    res = mcp(client, "undo_batch", {"batch_id": "missing"})
    assert res["isError"] and "not found" in res["content"][0]["text"]


def test_imports_from_the_inbox_are_the_importer(client, clean):
    from conftest import png_bytes

    from kist import worker
    from kist.db import SessionLocal
    from kist.models import Job

    staged = clean / ".staging" / "inbox-x-drop.png"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(png_bytes((1, 2, 3)))
    with SessionLocal() as s:
        s.add(Job(kind="ingest", payload={"path": str(staged), "filename": "drop.png"}))
        s.commit()
    assert worker.run_one()
    item = client.get("/api/items").json()["items"][0]
    created = history(client, item["id"])[0]
    assert created["kind"] == "create"
    assert (created["actor"], created["source"]) == ("importer", "import")


def test_rebuild_restores_history_and_ids_continue(client, upload, clean):
    item = upload()["item"]
    client.patch(f"/api/items/{item['id']}", json={"rating": 2})
    gone = upload()["item"]
    client.delete(f"/api/items/{gone['id']}")
    client.delete(f"/api/items/{gone['id']}?hard=true")
    before = history(client, item["id"])
    from kist.db import SessionLocal
    from kist.models import ItemEvent

    with SessionLocal() as s:
        total = s.execute(sa.select(sa.func.count()).select_from(ItemEvent)).scalar_one()

    assert cli.rebuild_index(wipe=True) == 0
    assert history(client, item["id"]) == before
    with SessionLocal() as s:
        assert s.execute(sa.select(sa.func.count()).select_from(ItemEvent)).scalar_one() == total
        purged = s.execute(
            sa.select(ItemEvent.kind).where(ItemEvent.item_id == gone["id"])
        ).scalars().all()
    assert sorted(purged) == ["create", "purge", "trash"]
    client.patch(f"/api/items/{item['id']}", json={"rating": 3})
    assert history(client, item["id"])[0]["id"] > max(e["id"] for e in before)


def test_verify_notices_missing_event_lines(client, upload, clean):
    from kist.db import SessionLocal
    from kist.services import verify

    item = upload()["item"]
    client.patch(f"/api/items/{item['id']}", json={"rating": 2})
    path = clean / "items" / item["id"] / "events.jsonl"
    path.write_text(path.read_text().splitlines()[0] + "\n")
    with SessionLocal() as s:
        problems = verify.run(s).problems
    assert any("events.jsonl has 1 of 2 events" in p for p in problems), problems
    assert cli.rewrite_meta() == 0
    with SessionLocal() as s:
        assert verify.run(s).problems == []
    assert len(disk_events(clean, item["id"])) == 2
