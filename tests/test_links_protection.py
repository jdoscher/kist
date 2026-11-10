"""Links between items, protected items, trash retention, folder descriptions."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import png_bytes
from fastapi.testclient import TestClient

from kist import cli

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def mcp(client, name, args=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": args or {}}}
    res = client.post("/mcp", json=body, headers=MCP_HEADERS)
    assert res.status_code == 200, res.text
    return res.json()["result"]


def meta(library, item_id):
    return json.loads((library / "items" / item_id / "meta.json").read_text())


# ---------- links ----------


def test_links_both_ways_with_meta_and_history(client, upload, clean):
    crop, shot = upload("crop.png")["item"], upload("shot.png")["item"]
    res = client.post(f"/api/items/{crop['id']}/links",
                      json={"dst_id": shot["id"], "kind": "derived_from"})
    assert res.status_code == 201, res.text
    out = res.json()
    assert out[0]["kind"] == "derived_from" and out[0]["direction"] == "out"
    assert out[0]["item"]["id"] == shot["id"] and "thumb_url" in out[0]["item"]
    back = client.get(f"/api/items/{shot['id']}/links").json()
    assert [(link["item"]["id"], link["direction"]) for link in back] == [(crop["id"], "in")]
    # linking again is a no-op
    client.post(f"/api/items/{crop['id']}/links",
                json={"dst_id": shot["id"], "kind": "derived_from"})
    assert len(client.get(f"/api/items/{crop['id']}/links").json()) == 1

    assert meta(clean, crop["id"])["links"] == [
        {"item_id": shot["id"], "kind": "derived_from", "direction": "out"}]
    assert meta(clean, shot["id"])["links"] == [
        {"item_id": crop["id"], "kind": "derived_from", "direction": "in"}]
    kinds = [e["kind"] for e in client.get(f"/api/items/{crop['id']}/history").json()]
    assert kinds[0] == "link"

    assert client.delete(
        f"/api/items/{crop['id']}/links/{shot['id']}?kind=derived_from"
    ).json() == {"removed": 1}
    assert client.get(f"/api/items/{shot['id']}/links").json() == []
    assert meta(clean, shot["id"])["links"] == []
    # and undoing the unlink brings it back
    unlink = client.get(f"/api/items/{crop['id']}/history").json()[0]
    assert unlink["kind"] == "unlink"
    assert client.post(f"/api/events/{unlink['id']}/revert").json()["undone"] == 1
    assert len(client.get(f"/api/items/{shot['id']}/links").json()) == 1


@pytest.mark.parametrize("body, status", [
    ({"dst_id": "SELF", "kind": "related"}, 422),
    ({"dst_id": "OTHER", "kind": "cousin"}, 422),
    ({"dst_id": "01NOPE", "kind": "related"}, 404),
])
def test_bad_links_are_refused(client, upload, body, status):
    a, b = upload()["item"], upload()["item"]
    body = {**body, "dst_id": {"SELF": a["id"], "OTHER": b["id"]}.get(body["dst_id"],
                                                                        body["dst_id"])}
    assert client.post(f"/api/items/{a['id']}/links", json=body).status_code == status


def test_not_duplicate_keeps_a_pair_out_of_duplicates(client, upload):
    a = upload("a.png", png_bytes((10, 10, 10)))["item"]
    b = upload("b.png", png_bytes((12, 12, 12)))["item"]
    assert client.get("/api/duplicates?threshold=20").json()
    client.post(f"/api/items/{b['id']}/links", json={"dst_id": a["id"], "kind": "not_duplicate"})
    assert client.get("/api/duplicates?threshold=20").json() == []


def test_versions_are_not_offered_as_duplicates(client, upload):
    a = upload("a.png", png_bytes((10, 10, 10)))["item"]
    b = upload("b.png", png_bytes((12, 12, 12)))["item"]
    assert client.get("/api/duplicates?threshold=20").json()
    assert client.get("/api/duplicates/groups", params={"kind": "same_image"}).json()
    client.post(f"/api/items/{b['id']}/links", json={"dst_id": a["id"], "kind": "version_of"})
    assert client.get("/api/duplicates?threshold=20").json() == []
    assert client.get("/api/duplicates/groups", params={"kind": "same_image"}).json() == []


def test_purging_an_item_drops_its_links_everywhere(client, upload, clean):
    a, b = upload()["item"], upload()["item"]
    client.post(f"/api/items/{a['id']}/links", json={"dst_id": b["id"], "kind": "alternate"})
    client.delete(f"/api/items/{a['id']}")
    client.delete(f"/api/items/{a['id']}?hard=true")
    assert client.get(f"/api/items/{b['id']}/links").json() == []
    assert meta(clean, b["id"])["links"] == []


def test_rebuild_restores_links_and_protection(client, upload, clean):
    a, b = upload()["item"], upload()["item"]
    client.post(f"/api/items/{a['id']}/links", json={"dst_id": b["id"], "kind": "version_of"})
    client.patch(f"/api/items/{b['id']}", json={"protected": True})
    assert cli.rebuild_index(wipe=True) == 0
    links = client.get(f"/api/items/{a['id']}/links").json()
    assert [(x["item"]["id"], x["kind"], x["direction"]) for x in links] == [
        (b["id"], "version_of", "out")]
    assert client.get(f"/api/items/{b['id']}").json()["protected"] is True


def test_link_items_over_mcp(client, upload):
    a, b = upload()["item"], upload()["item"]
    res = mcp(client, "link_items", {"id": a["id"], "dst_id": b["id"], "kind": "related"})
    assert json.loads(res["content"][0]["text"])["item"]["id"] == b["id"]
    res = mcp(client, "link_items", {"id": a["id"], "dst_id": b["id"], "kind": "nope"})
    assert res["isError"] and "unknown link kind" in res["content"][0]["text"]
    mcp(client, "link_items", {"id": a["id"], "dst_id": b["id"], "kind": "related",
                               "remove": True})
    assert client.get(f"/api/items/{a['id']}/links").json() == []


def test_folder_limited_reader_sees_only_visible_links(client, upload):
    shared = client.post("/api/folders", json={"name": "Shared"}).json()
    a = upload(folder_ids=json.dumps([shared["id"]]))["item"]
    b = upload(folder_ids=json.dumps([shared["id"]]))["item"]
    hidden = upload()["item"]
    for dst in (b, hidden):
        client.post(f"/api/items/{a['id']}/links", json={"dst_id": dst["id"], "kind": "related"})
    tok = client.post("/api/tokens", json={"name": "fam", "scopes": ["read"],
                                           "folder_ids": [shared["id"]]}).json()["token"]
    fam = TestClient(client.app, headers={"Authorization": f"Bearer {tok}"})
    assert [x["item"]["id"] for x in fam.get(f"/api/items/{a['id']}/links").json()] == [b["id"]]


# ---------- protected ----------


def test_protected_items_cant_be_trashed_or_purged(client, upload, clean):
    item = upload()["item"]
    res = client.patch(f"/api/items/{item['id']}", json={"protected": True})
    assert res.json()["protected"] is True and meta(clean, item["id"])["protected"] is True
    res = client.delete(f"/api/items/{item['id']}")
    assert res.status_code == 409 and res.json() == {"detail": "Protected: unprotect it first"}
    res = mcp(client, "trash_item", {"id": item["id"]})
    assert res["isError"] and "Protected: unprotect it first" in res["content"][0]["text"]

    # protected after it was trashed: still never purged
    other = upload()["item"]
    client.delete(f"/api/items/{other['id']}")
    client.patch(f"/api/items/{other['id']}", json={"protected": True})
    res = client.delete(f"/api/items/{other['id']}?hard=true")
    assert res.status_code == 409 and res.json()["detail"] == "Protected: unprotect it first"

    client.patch(f"/api/items/{item['id']}", json={"protected": False})
    assert client.delete(f"/api/items/{item['id']}").status_code == 200


def test_only_write_or_admin_unprotects(client, upload):
    item = upload()["item"]
    tokens = {
        s: client.post("/api/tokens", json={"name": s, "scopes": [s]}).json()["token"]
        for s in ("agent", "write")
    }
    agent = TestClient(client.app, headers={"Authorization": f"Bearer {tokens['agent']}"})
    writer = TestClient(client.app, headers={"Authorization": f"Bearer {tokens['write']}"})
    res = mcp(agent, "update_item", {"id": item["id"], "protected": True})
    assert json.loads(res["content"][0]["text"])["protected"] is True
    res = mcp(agent, "update_item", {"id": item["id"], "protected": False})
    assert res["isError"] and "can't do that" in res["content"][0]["text"]
    assert writer.patch(f"/api/items/{item['id']}", json={"protected": False}).status_code == 200


# ---------- retention ----------


def _since(clean, days_ago):
    state = clean / ".kist"
    state.mkdir(exist_ok=True)
    when = datetime.now(UTC) - timedelta(days=days_ago)
    (state / "retention.json").write_text(json.dumps({"since": when.isoformat()}))


def test_retention_purges_old_trash_but_never_protected(client, upload, clean):
    from kist.db import SessionLocal
    from kist.services import retention

    old = upload(deleted_at="2025-01-01T00:00:00Z")["item"]
    keep = upload(deleted_at="2025-01-01T00:00:00Z")["item"]
    client.patch(f"/api/items/{keep['id']}", json={"protected": True})
    recent = upload()["item"]
    client.delete(f"/api/items/{recent['id']}")
    live = upload()["item"]

    with SessionLocal() as s:
        assert retention.purge_expired(s, days=0) == []  # 0 = never
        # the first run only starts the clock: nothing goes on upgrade day
        assert retention.purge_expired(s, days=30) == []
    assert (clean / ".kist" / "retention.json").is_file()
    _since(clean, 31)
    with SessionLocal() as s:
        assert retention.purge_expired(s, days=30) == [old["id"]]
    assert client.get(f"/api/items/{old['id']}").status_code == 404
    for item in (keep, recent, live):
        assert client.get(f"/api/items/{item['id']}").status_code == 200
    batches = client.get("/api/batches?actor=worker").json()
    assert batches[0]["kinds"] == ["purge"] and batches[0]["source"] == "worker"


def test_worker_retention_uses_the_setting(client, upload, clean, monkeypatch):
    from kist import worker
    from kist.config import get_settings

    old = upload(deleted_at="2025-01-01T00:00:00Z")["item"]
    _since(clean, 400)
    monkeypatch.setattr(get_settings(), "trash_retention_days", 0)
    assert worker.purge_expired_trash() == 0
    monkeypatch.setattr(get_settings(), "trash_retention_days", 30)
    assert worker.purge_expired_trash() == 1
    assert client.get(f"/api/items/{old['id']}").status_code == 404


# ---------- folder descriptions ----------


def test_folder_descriptions(client, clean):
    folder = client.post("/api/folders", json={"name": "Capsules",
                                               "description": "Outfits for one season"}).json()
    assert folder["description"] == "Outfits for one season"
    res = client.patch(f"/api/folders/{folder['id']}", json={"description": "Spring only"})
    assert res.json()["description"] == "Spring only"
    assert client.get("/api/folders").json()[0]["description"] == "Spring only"
    on_disk = json.loads((clean / "folders.json").read_text())
    assert on_disk["folders"][0]["description"] == "Spring only"
    too_long = client.patch(f"/api/folders/{folder['id']}", json={"description": "x" * 5001})
    assert too_long.status_code == 422

    listed = mcp(client, "list_folders")
    assert json.loads(listed["content"][0]["text"])["description"] == "Spring only"
    made = mcp(client, "create_folder", {"name": "Refs", "description": "Reference shots"})
    assert json.loads(made["content"][0]["text"])["description"] == "Reference shots"

    (clean / "items").mkdir(exist_ok=True)
    assert cli.rebuild_index(wipe=True) == 0
    by_name = {f["name"]: f["description"] for f in client.get("/api/folders").json()}
    assert by_name == {"Capsules": "Spring only", "Refs": "Reference shots"}
