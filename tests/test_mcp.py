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
