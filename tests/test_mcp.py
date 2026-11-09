import json

HEADERS = {"Accept": "application/json, text/event-stream"}


def rpc(client, method, params=None, id_=1):
    body = {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}
    res = client.post("/mcp", json=body, headers=HEADERS)
    assert res.status_code == 200, res.text
    return res.json()


def init(client):
    return rpc(client, "initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    })


def test_initialize_and_list_tools(client):
    assert init(client)["result"]["serverInfo"]["name"] == "kist"
    tools = {t["name"] for t in rpc(client, "tools/list", id_=2)["result"]["tools"]}
    assert {"search_items", "view_item", "add_item", "set_tags", "trash_item"} <= tools
    assert not any("delete" in t and "trash" not in t for t in tools)


def test_search_and_view_through_mcp(client, upload):
    item = upload("seen.png", tags="mcp")["item"]
    init(client)
    found = rpc(client, "tools/call", {
        "name": "search_items", "arguments": {"tags": ["mcp"]},
    }, id_=3)["result"]
    assert item["id"] in str(found)

    viewed = rpc(client, "tools/call", {
        "name": "view_item", "arguments": {"id": item["id"], "size": 256},
    }, id_=4)["result"]
    assert viewed["content"][0]["type"] == "image"


def call(client, name, args, id_=10):
    return rpc(client, "tools/call", {"name": name, "arguments": args}, id_=id_)["result"]


def test_tool_errors_reach_the_agent_with_their_reason(client, upload):
    item = upload()["item"]
    init(client)
    cases = [
        ("update_item", {"id": item["id"]}, "nothing to update"),
        ("get_item", {"id": "01NOPE"}, "item 01NOPE not found"),
        ("update_item", {"id": item["id"], "rating": 9}, "rating must be from 0 to 5"),
        ("update_item", {"id": item["id"], "source_url": "pinterest.com/x"}, "http(s) URL"),
        ("set_folders", {"id": item["id"], "add": ["01GHOST"]}, "Unknown folder: 01GHOST"),
        ("create_folder", {"name": "   "}, "names can't be empty"),
        ("add_item", {"file_path": "/etc/passwd"}, "must be inside an import directory"),
        ("add_item", {}, "exactly one of url or file_path"),
        ("search_items", {"cursor": "junk"}, "invalid cursor"),
        ("retry_job", {"id": 999}, "job 999 not found"),
        ("view_item", {"id": item["id"], "size": 256}, None),
    ]
    for i, (name, args, message) in enumerate(cases):
        res = call(client, name, args, id_=20 + i)
        if message is None:
            assert not res.get("isError"), res
            continue
        assert res.get("isError"), (name, res)
        assert message in res["content"][0]["text"], (name, res)


def test_update_item_folder_id_adds_a_membership(client, upload):
    a = client.post("/api/folders", json={"name": "A"}).json()
    b = client.post("/api/folders", json={"name": "B"}).json()
    item = upload(folder_id=a["id"])["item"]
    init(client)
    res = call(client, "update_item", {"id": item["id"], "folder_id": b["id"], "rating": 2})
    out = json.loads(res["content"][0]["text"])
    assert sorted(out["folder_ids"]) == sorted([a["id"], b["id"]]) and out["rating"] == 2


def test_every_tool_returns_items_in_one_shape(client, upload):
    item = upload()["item"]
    init(client)
    got = json.loads(call(client, "get_item", {"id": item["id"]})["content"][0]["text"])
    found = json.loads(call(client, "search_items", {}, 11)["content"][0]["text"])["items"][0]
    assert got == found
    assert got["web_url"] == f"/all?item={item['id']}"
    assert got["file_url"] == f"/api/items/{item['id']}/file"
    assert "phash" not in got and "dominant_colors" not in got


def test_rest_and_mcp_agree_on_stats_and_duplicates(client, upload):
    from conftest import png_bytes

    upload("a.png", png_bytes((10, 10, 10)))
    upload("b.png", png_bytes((12, 12, 12)))  # same picture, different bytes
    upload("c.txt", b"text", "text/plain")
    upload("d.png", png_bytes((50, 60, 70)), deleted_at="2020-01-01T00:00:00Z")
    client.post("/api/folders", json={"name": "F"})
    init(client)
    rest = client.get("/api/stats").json()
    mcp = json.loads(call(client, "library_stats", {})["content"][0]["text"])
    assert rest == mcp
    assert rest["total_items"] == 3 and rest["trashed_items"] == 1
    assert rest["by_kind"] == {"pdf": 0, "video": 0, "image": 2, "other": 1}

    pairs = client.get("/api/duplicates?threshold=20").json()
    res = call(client, "find_duplicates", {"threshold": 20}, 12)
    # a list comes back as one content block per entry
    mcp_pairs = [json.loads(c["text"]) for c in res["content"]]
    assert [(p["a"]["id"], p["b"]["id"], p["distance"]) for p in pairs] == [
        (p["a"]["id"], p["b"]["id"], p["distance"]) for p in mcp_pairs
    ]
    assert pairs
