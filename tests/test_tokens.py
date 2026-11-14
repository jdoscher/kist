"""Named, scoped tokens: what each scope reaches, on every surface."""

import json
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from conftest import TOKEN
from fastapi.testclient import TestClient

from kist import cli

FORBIDDEN = {"detail": "This token can't do that"}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def make_token(client, name="t", scopes=("read",), **extra):
    res = client.post("/api/tokens", json={"name": name, "scopes": list(scopes), **extra})
    assert res.status_code == 201, res.text
    return res.json()


def as_token(client, plaintext):
    return TestClient(client.app, headers={"Authorization": f"Bearer {plaintext}"})


def mcp_call(c, name, args=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": args or {}}}
    res = c.post("/mcp", json=body, headers=MCP_HEADERS)
    return res


def mcp_result(c, name, args=None):
    res = mcp_call(c, name, args)
    assert res.status_code == 200, res.text
    return res.json()["result"]


def test_create_list_revoke_and_me(client):
    created = make_token(client, "Shortcut", ["add"])
    assert created["token"].startswith("kist_") and created["active"]
    listed = client.get("/api/tokens").json()
    assert [t["name"] for t in listed] == ["Shortcut"]
    assert "token" not in listed[0] and "token_sha256" not in listed[0]

    assert client.get("/api/me").json() == {
        "name": "owner", "scopes": ["admin"], "folder_ids": [], "expires_at": None,
    }
    shortcut = as_token(client, created["token"])
    assert shortcut.get("/api/me").json()["name"] == "Shortcut"
    assert client.get("/api/tokens").json()[0]["last_used_at"] is not None

    revoked = client.delete(f"/api/tokens/{created['id']}").json()
    assert revoked["revoked_at"] and not revoked["active"]
    assert shortcut.get("/api/me").status_code == 401
    assert client.delete("/api/tokens/01NOPE").status_code == 404


def test_a_revoked_or_expired_token_entry_can_be_removed(client, clean):
    from kist.db import SessionLocal
    from kist.models import ApiToken
    from kist.services import verify

    live = make_token(client, "Phone", ["add"])
    # a working token can't be removed: revoke first
    r = client.delete(f"/api/tokens/{live['id']}/entry")
    assert r.status_code == 409 and "revoke it" in r.json()["detail"]

    client.delete(f"/api/tokens/{live['id']}")
    assert client.delete(f"/api/tokens/{live['id']}/entry").status_code == 204
    assert client.get("/api/tokens").json() == []
    assert client.delete(f"/api/tokens/{live['id']}/entry").status_code == 404

    # expired (never revoked) also qualifies
    old = make_token(client, "Old", ["read"])
    with SessionLocal() as s:
        s.execute(sa.update(ApiToken).where(ApiToken.id == old["id"])
                  .values(expires_at=datetime.now(UTC) - timedelta(days=1)))
        s.commit()
    assert client.delete(f"/api/tokens/{old['id']}/entry").status_code == 204

    # tokens.json follows, so rebuild-index doesn't bring them back and verify is clean
    assert json.loads((clean / ".kist" / "tokens.json").read_text())["tokens"] == []
    with SessionLocal() as s:
        assert verify.run(s).problems == []


def test_only_admin_removes_token_entries(client):
    gone = make_token(client, "Gone", ["read"])
    client.delete(f"/api/tokens/{gone['id']}")
    writer = as_token(client, make_token(client, "W", ["write"])["token"])
    assert writer.delete(f"/api/tokens/{gone['id']}/entry").status_code == 403


def test_expired_tokens_are_refused(client):
    from kist.db import SessionLocal
    from kist.models import ApiToken

    soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    created = make_token(client, "Brief", ["read"], expires_at=soon)
    c = as_token(client, created["token"])
    assert c.get("/api/items").status_code == 200
    with SessionLocal() as s:
        s.execute(sa.update(ApiToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        s.commit()
    # a database edit behind the app's back (no API changes expiry): the
    # app's token cache would see it within CACHE_SECONDS; check it now
    from kist.services import tokens as tokens_svc

    tokens_svc.forget_cached()
    assert c.get("/api/items").status_code == 401
    assert c.post("/mcp", json={}, headers=MCP_HEADERS).status_code == 401


@pytest.mark.parametrize(
    "body, message",
    [
        ({"name": "x", "scopes": []}, "at least one scope"),
        ({"name": "x", "scopes": ["root"]}, "unknown scope"),
        ({"name": "owner", "scopes": ["read"]}, "reserved"),
        ({"name": "x", "scopes": ["write"], "folder_ids": ["01F"]}, "read scope"),
        ({"name": "x", "scopes": ["read"], "folder_ids": ["01GHOST"]}, "Unknown folder"),
        ({"name": "x", "scopes": ["read"], "expires_at": "2020-01-01T00:00:00Z"}, "past"),
    ],
)
def test_token_input_is_checked(client, body, message):
    res = client.post("/api/tokens", json=body)
    assert res.status_code == 422 and message in res.text, res.text


def test_names_are_unique_among_live_tokens(client):
    first = make_token(client, "Agent", ["agent"])
    assert client.post("/api/tokens", json={"name": "agent", "scopes": ["read"]}).status_code == 422
    client.delete(f"/api/tokens/{first['id']}")
    make_token(client, "Agent", ["agent"])  # the name is free again


def test_add_token_can_only_add(client):
    add = as_token(client, make_token(client, "Phone", ["add"])["token"])
    from conftest import png_bytes

    res = add.post("/api/items", files={"file": ("a.png", png_bytes(), "image/png")})
    assert res.status_code == 201
    item_id = res.json()["item"]["id"]
    for method, path in [
        ("GET", "/api/items"), ("GET", f"/api/items/{item_id}"),
        ("GET", f"/api/items/{item_id}/file"), ("GET", f"/api/items/{item_id}/thumb"),
        ("GET", "/api/folders"), ("PATCH", f"/api/items/{item_id}"),
        ("DELETE", f"/api/items/{item_id}"), ("GET", "/api/tokens"),
        ("POST", "/mcp"),
    ]:
        res = add.request(method, path, json={})
        assert res.status_code == 403 and res.json() == FORBIDDEN, (method, path, res.text)
    assert add.get("/api/me").status_code == 200


def test_read_token_reads_but_never_writes(client, upload):
    item = upload()["item"]
    reader = as_token(client, make_token(client, "Patty", ["read"])["token"])
    assert reader.get("/api/items").json()["total"] == 1
    assert reader.get(f"/api/items/{item['id']}/file").status_code == 200
    assert reader.get("/api/stats").status_code == 200
    for method, path in [
        ("POST", "/api/folders"), ("PATCH", f"/api/items/{item['id']}"),
        ("DELETE", f"/api/items/{item['id']}"), ("GET", "/api/tokens"),
        ("POST", "/api/tokens"),
    ]:
        res = reader.request(method, path, json={"name": "x"})
        assert res.status_code == 403 and res.json() == FORBIDDEN, (method, path)
    # MCP: reading tools work, writing tools say why they don't
    assert item["id"] in json.dumps(mcp_result(reader, "search_items"))
    res = mcp_result(reader, "set_tags", {"id": item["id"], "add": ["x"]})
    assert res["isError"] and "This token can't do that" in res["content"][0]["text"]
    res = mcp_result(reader, "add_item", {"url": "https://example.com/a.png"})
    assert res["isError"] and "This token can't do that" in res["content"][0]["text"]


def test_read_token_cookie_works_for_gets(client, upload):
    upload()
    plaintext = make_token(client, "Viewer", ["read"])["token"]
    anon = TestClient(client.app)
    anon.cookies.set("kist_token", plaintext)
    assert anon.get("/api/items").status_code == 200
    assert anon.post("/api/folders", json={"name": "x"}).status_code == 401


def test_agent_token_edits_but_never_hard_deletes(client, upload):
    item = upload()["item"]
    agent = as_token(client, make_token(client, "Claude", ["agent"])["token"])
    assert agent.patch(f"/api/items/{item['id']}", json={"rating": 3}).status_code == 200
    assert agent.delete(f"/api/items/{item['id']}").status_code == 200  # to the trash
    for q in ("hard=true", "hard=1", "hard=True", "hard=on", "x=1&hard=yes"):
        res = agent.delete(f"/api/items/{item['id']}?{q}")
        assert res.status_code == 403 and res.json() == FORBIDDEN, q
    assert agent.get("/api/tokens").status_code == 403
    res = mcp_result(agent, "set_tags", {"id": item["id"], "add": ["agent"]})
    assert not res.get("isError"), res


def test_write_token_hard_deletes_but_manages_no_tokens(client, upload):
    item = upload()["item"]
    writer = as_token(client, make_token(client, "Script", ["write"])["token"])
    writer.delete(f"/api/items/{item['id']}")
    assert writer.delete(f"/api/items/{item['id']}?hard=true").status_code == 200
    assert writer.get("/api/tokens").status_code == 403
    assert writer.post("/api/tokens", json={"name": "x", "scopes": ["admin"]}).status_code == 403


def test_helper_token_reaches_only_the_ai_job_endpoints(client):
    helper = as_token(client, make_token(client, "Mac", ["helper"])["token"])
    assert helper.get("/api/items").status_code == 403
    assert helper.post("/mcp", json={}, headers=MCP_HEADERS).status_code == 403
    assert helper.post("/api/ai/jobs/claim", json={}).status_code == 200
    for scopes in (["read"], ["write"], ["agent"], ["add"]):
        other = as_token(client, make_token(client, f"n{scopes[0]}", scopes)["token"])
        assert other.post("/api/ai/jobs/claim", json={}).status_code == 403


@pytest.fixture
def library(client, upload):
    """Folders A (with child A1) and B, and items filed around them."""
    a = client.post("/api/folders", json={"name": "A"}).json()
    a1 = client.post("/api/folders", json={"name": "A1", "parent_id": a["id"]}).json()
    b = client.post("/api/folders", json={"name": "B"}).json()
    items = {
        "a": upload("a.png", folder_ids=json.dumps([a["id"]]))["item"],
        "a1": upload("a1.png", folder_ids=json.dumps([a1["id"]]))["item"],
        "b": upload("b.png", folder_ids=json.dumps([b["id"]]))["item"],
        "ab": upload("ab.png", folder_ids=json.dumps([a["id"], b["id"]]))["item"],
        "loose": upload("loose.png")["item"],
        "trashed": upload("t.png", folder_ids=json.dumps([a["id"]]),
                          deleted_at="2026-01-01T00:00:00Z")["item"],
    }
    return {"A": a, "A1": a1, "B": b}, items


def test_folder_limited_reader_sees_only_its_folders(client, library):
    folders, items = library
    created = make_token(client, "Family", ["read"], folder_ids=[folders["A"]["id"]])
    assert created["folder_ids"] == [folders["A"]["id"]]
    fam = as_token(client, created["token"])
    me = fam.get("/api/me").json()
    assert me["folder_ids"] == [folders["A"]["id"]] and me["scopes"] == ["read"]

    page = fam.get("/api/items").json()
    seen = {i["id"] for i in page["items"]}
    assert seen == {items["a"]["id"], items["a1"]["id"], items["ab"]["id"]}
    assert page["total"] == 3
    ab = next(i for i in page["items"] if i["id"] == items["ab"]["id"])
    assert ab["folder_ids"] == [folders["A"]["id"]]  # B is not shown
    for query in (f"folder={folders['B']['id']}", "state=trashed", "folder=none",
                  "view=unfiled", "deleted=true"):
        assert fam.get(f"/api/items?{query}").json()["items"] == [], query

    hidden = [items["b"], items["loose"], items["trashed"]]
    for item in hidden:
        for suffix in ("", "/file", "/thumb", "/thumb?size=1024", "/history", "/links"):
            res = fam.get(f"/api/items/{item['id']}{suffix}")
            assert res.status_code == 404, (item["filename"], suffix, res.text)
    assert fam.get(f"/api/items/{items['a1']['id']}/file").status_code == 200
    assert fam.get(f"/api/items/{items['a']['id']}/thumb").status_code == 200

    tree = fam.get("/api/folders").json()
    assert {f["name"]: f["parent_id"] for f in tree} == {"A": None, "A1": folders["A"]["id"]}

    # every other path is a 404, writes a 403
    for path in ("/api/tags", "/api/stats", "/api/duplicates", "/api/smart-folders",
                 "/api/jobs", "/api/health/deep", "/api/batches", "/api/archive-health",
                 "/api/ai/status", "/docs", "/openapi.json"):
        assert fam.get(path).status_code == 404, path
    assert fam.post("/api/folders", json={"name": "x"}).status_code == 403


def test_folder_limited_reader_over_mcp(client, library):
    folders, items = library
    fam = as_token(client, make_token(client, "Kids", ["read"],
                                      folder_ids=[folders["A"]["id"]])["token"])
    found = mcp_result(fam, "search_items", {"limit": 100})
    ids = {i["id"] for i in json.loads(found["content"][0]["text"])["items"]}
    assert ids == {items["a"]["id"], items["a1"]["id"], items["ab"]["id"]}
    for tool, args in [
        ("get_item", {"id": items["b"]["id"]}),
        ("view_item", {"id": items["b"]["id"], "size": 256}),
        ("get_item", {"id": items["trashed"]["id"]}),
    ]:
        res = mcp_result(fam, tool, args)
        assert res["isError"] and "not found" in res["content"][0]["text"], (tool, res)
    assert not mcp_result(fam, "view_item", {"id": items["a"]["id"], "size": 256}).get("isError")
    for tool in ("list_tags", "library_stats", "find_duplicates", "list_smart_folders",
                 "list_jobs", "recent_batches"):
        res = mcp_result(fam, tool)
        assert res["isError"] and "can't do that" in res["content"][0]["text"], tool
    listed = mcp_result(fam, "list_folders")
    names = {json.loads(c["text"])["name"] for c in listed["content"]}
    assert names == {"A", "A1"}


def test_rebuild_restores_tokens_from_disk(client, clean):
    keep = make_token(client, "Keep", ["read"])
    gone = make_token(client, "Gone", ["read"])
    client.delete(f"/api/tokens/{gone['id']}")
    on_disk = json.loads((clean / ".kist" / "tokens.json").read_text())
    assert {t["name"] for t in on_disk["tokens"]} == {"Keep", "Gone"}
    assert all("token" not in t for t in on_disk["tokens"])
    assert keep["token"] not in (clean / ".kist" / "tokens.json").read_text()

    (clean / "items").mkdir(exist_ok=True)
    assert cli.rebuild_index(wipe=True) == 0
    assert as_token(client, keep["token"]).get("/api/me").json()["name"] == "Keep"
    assert as_token(client, gone["token"]).get("/api/me").status_code == 401


def test_verify_reports_a_stale_tokens_file(client, clean):
    from kist.db import SessionLocal
    from kist.services import verify

    make_token(client, "One", ["read"])
    with SessionLocal() as s:
        assert verify.run(s).problems == []
    (clean / ".kist" / "tokens.json").unlink()
    with SessionLocal() as s:
        assert "tokens.json missing" in verify.run(s).problems
    assert cli.rewrite_meta() == 0
    with SessionLocal() as s:
        assert verify.run(s).problems == []


def test_owner_token_is_still_admin(client):
    owner = TestClient(client.app, headers={"Authorization": f"Bearer {TOKEN}"})
    assert owner.get("/api/tokens").status_code == 200
