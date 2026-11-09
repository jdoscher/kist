"""Failure-mode fixes (2026-11-09 review): add/remove edits never overwrite
what another writer did."""

from test_mcp import init, rpc


def test_patch_tags_keeps_what_another_writer_added(client, upload):
    item = upload(tags="a,b")["item"]
    # an agent adds a tag while the browser still has ["a", "b"] cached
    init(client)
    args = {"id": item["id"], "add": ["agent"]}
    rpc(client, "tools/call", {"name": "set_tags", "arguments": args}, id_=2)
    # the browser removes "b": it sends only the change
    r = client.patch(f"/api/items/{item['id']}/tags", json={"remove": ["b"]})
    assert r.status_code == 200
    assert r.json()["tags"] == ["a", "agent"]
    # the old full-list PUT would have dropped the agent's tag
    old = client.put(f"/api/items/{item['id']}/tags", json={"tags": ["a"]})
    assert old.json()["tags"] == ["a"]


def test_patch_tags_add_is_idempotent_and_case_insensitive(client, upload):
    item = upload(tags="Cars")["item"]
    for _ in range(2):
        r = client.patch(f"/api/items/{item['id']}/tags", json={"add": ["cars", "red"]})
    assert r.json()["tags"] == ["Cars", "red"]


def test_patch_folders(client, upload):
    a = client.post("/api/folders", json={"name": "A"}).json()["id"]
    b = client.post("/api/folders", json={"name": "B"}).json()["id"]
    item = upload(folder_ids=a)["item"]
    r = client.patch(f"/api/items/{item['id']}/folders", json={"add": [b]})
    assert sorted(r.json()["folder_ids"]) == sorted([a, b])
    r = client.patch(f"/api/items/{item['id']}/folders", json={"remove": [a], "add": [b]})
    assert r.json()["folder_ids"] == [b]
    assert client.patch("/api/items/nope/folders", json={"add": [b]}).status_code == 404
