"""Share links are Kist's first surface without a token: what they serve,
and everything they must never serve."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from conftest import png_bytes
from fastapi.testclient import TestClient

from kist import auth, cli
from kist.config import get_settings
from kist.services import shares as shares_svc


@pytest.fixture(autouse=True)
def _fresh_attempts():
    shares_svc.reset_attempts()
    yield
    shares_svc.reset_attempts()


def anon(client) -> TestClient:
    """A visitor: no token, no cookies."""
    return TestClient(client.app, follow_redirects=False)


def make_share(client, **body) -> dict:
    res = client.post("/api/shares", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def folder_with_items(client, upload, n=2, name="Shared"):
    folder = client.post("/api/folders", json={"name": name}).json()
    items = [upload(f"p{i}.png", folder_id=folder["id"])["item"] for i in range(n)]
    return folder, items


def test_a_folder_share_shows_its_items_to_anyone(client, upload):
    folder, items = folder_with_items(client, upload)
    outside = upload("private.png")["item"]
    share = make_share(client, target_kind="folder", target_id=folder["id"], title="Mood")
    v = anon(client)
    page = v.get(f"/s/{share['slug']}")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    for it in items:
        assert f"/s/{share['slug']}/i/{it['id']}/thumb" in page.text
    assert outside["id"] not in page.text
    thumb = v.get(f"/s/{share['slug']}/i/{items[0]['id']}/thumb")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"
    display = v.get(f"/s/{share['slug']}/i/{items[0]['id']}/display")
    assert display.status_code == 200


def test_share_pages_are_locked_down(client, upload):
    folder, _ = folder_with_items(client, upload, 1)
    share = make_share(client, target_kind="folder", target_id=folder["id"])
    res = anon(client).get(f"/s/{share['slug']}")
    csp = res.headers["content-security-policy"]
    assert "sandbox" in csp and "default-src 'none'" in csp
    assert "allow-scripts" not in csp and "script-src" not in csp
    assert "frame-ancestors 'none'" in csp
    assert "noindex" in res.headers["x-robots-tag"]
    assert res.headers["referrer-policy"] == "no-referrer"
    assert "no-store" in res.headers["cache-control"]
    assert "<script" not in res.text.lower()
    assert 'name="robots" content="noindex' in res.text
    # a visitor is never handed a cookie (no token, no session) for a plain share
    assert "set-cookie" not in res.headers
    robots = anon(client).get("/robots.txt")
    assert robots.status_code == 200 and "Disallow: /s/" in robots.text


def test_only_share_paths_are_public():
    for path in ("/s/abc", "/s/abc/i/x/thumb", "/robots.txt", "/api/health"):
        assert not auth.needs_token(path), path
    for path in ("/api/items", "/api/shares", "/api/s/abc", "/mcp", "/mcp/",
                 "/api/exports", "/docs", "/openapi.json", "/api/shortcut"):
        assert auth.needs_token(path), path


def test_nothing_else_answers_without_a_token(client, upload):
    item = upload()["item"]
    folder, _ = folder_with_items(client, upload, 1)
    share = make_share(client, target_kind="folder", target_id=folder["id"])
    v = anon(client)
    v.cookies.set("kist_share", "1.abc")  # a share cookie opens nothing else
    for method, path in [
        ("GET", "/api/items"), ("GET", f"/api/items/{item['id']}"),
        ("GET", f"/api/items/{item['id']}/file"), ("GET", f"/api/items/{item['id']}/thumb"),
        ("GET", "/api/shares"), ("POST", "/api/shares"), ("GET", f"/api/shares/{share['id']}"),
        ("GET", "/api/exports"), ("POST", "/api/exports"), ("GET", "/api/export-presets"),
        ("POST", "/api/items/export-images"), ("POST", "/api/imports"),
        ("GET", "/api/imports/1"), ("GET", "/api/shortcut"),
        ("GET", f"/api/items/{item['id']}/snapshot"), ("GET", "/api/folders"),
        ("POST", "/mcp"), ("GET", "/openapi.json"),
    ]:
        res = v.request(method, path)
        assert res.status_code == 401, (method, path, res.status_code)


def test_items_outside_the_share_are_never_served(client, upload):
    folder, items = folder_with_items(client, upload)
    sibling, others = folder_with_items(client, upload, 1, name="Sibling")
    loose = upload("loose.png")["item"]
    share = make_share(client, target_kind="folder", target_id=folder["id"],
                       allow_download=True, allow_original=True)
    v = anon(client)
    base = f"/s/{share['slug']}/i"
    for other in (others[0], loose):
        for variant in ("thumb", "display", "file"):
            res = v.get(f"{base}/{other['id']}/{variant}")
            assert res.status_code == 404, (other["filename"], variant)
            assert res.headers["content-type"].startswith("text/html")
    # junk ids are just as unknown
    for bad in ("x", "0" * 26, "../" + items[0]["id"], items[0]["id"].lower()):
        assert v.get(f"{base}/{bad}/thumb").status_code == 404
    # trashed or filed elsewhere: no longer served
    client.delete(f"/api/items/{items[0]['id']}")
    assert v.get(f"{base}/{items[0]['id']}/thumb").status_code == 404
    client.put(f"/api/items/{items[1]['id']}/folders", json={"folder_ids": [sibling["id"]]})
    assert v.get(f"{base}/{items[1]['id']}/thumb").status_code == 404


def test_path_traversal_goes_nowhere(client, upload):
    folder, items = folder_with_items(client, upload, 1)
    share = make_share(client, target_kind="folder", target_id=folder["id"],
                       allow_download=True, allow_original=True)
    v = anon(client)
    slug, iid = share["slug"], items[0]["id"]
    for path in [
        f"/s/{slug}/i/{iid}/meta.json",
        f"/s/{slug}/i/{iid}/..%2Fmeta.json",
        f"/s/{slug}/i/{iid}/%2e%2e%2f%2e%2e%2f.kist%2Fshares.json",
        f"/s/{slug}/i/..%2F..%2Fapi%2Fitems/thumb",
        f"/s/{slug}/../../api/items",
        f"/s/{slug}/i/{iid}/original.png",
        "/s/..%2Fapi%2Fitems",
        "/s/%2e%2e/api/items",
    ]:
        res = v.get(path)
        assert res.status_code in (401, 404), (path, res.status_code)
        assert '"items"' not in res.text and "scrypt" not in res.text
    # a trailing slash only redirects to the page itself
    res = v.get(f"/s/{slug}/")
    assert res.status_code == 404 or res.headers["location"].endswith(f"/s/{slug}")


def test_slugs_are_long_and_random(client, upload):
    folder, _ = folder_with_items(client, upload, 1)
    slugs = {make_share(client, target_kind="folder", target_id=folder["id"])["slug"]
             for _ in range(5)}
    assert len(slugs) == 5
    assert all(len(s) >= 22 and re.fullmatch(r"[A-Za-z0-9_-]+", s) for s in slugs)
    assert anon(client).get("/s/" + "A" * 22).status_code == 404
    assert anon(client).get("/s/short").status_code == 404


def test_password_share(client, upload):
    folder, items = folder_with_items(client, upload, 1)
    share = make_share(client, target_kind="folder", target_id=folder["id"],
                       password="correct horse")
    assert share["has_password"] and "password" not in share
    v = anon(client)
    gate = v.get(f"/s/{share['slug']}")
    assert gate.status_code == 401 and 'type="password"' in gate.text
    assert items[0]["id"] not in gate.text
    assert v.get(f"/s/{share['slug']}/i/{items[0]['id']}/thumb").status_code == 401
    wrong = v.post(f"/s/{share['slug']}", data={"password": "nope"})
    assert wrong.status_code == 401 and "set-cookie" not in wrong.headers
    ok = v.post(f"/s/{share['slug']}", data={"password": "correct horse"})
    assert ok.status_code == 303 and ok.headers["location"] == f"/s/{share['slug']}"
    cookie = ok.headers["set-cookie"]
    assert "HttpOnly" in cookie and f"Path=/s/{share['slug']}" in cookie
    assert "kist_token" not in cookie
    assert v.get(f"/s/{share['slug']}").status_code == 200
    assert v.get(f"/s/{share['slug']}/i/{items[0]['id']}/thumb").status_code == 200

    # the cookie belongs to this link only, and a forged one opens nothing
    other = make_share(client, target_kind="folder", target_id=folder["id"],
                       password="correct horse")
    value = v.cookies.get("kist_share")
    w = anon(client)
    w.cookies.set("kist_share", value)
    assert w.get(f"/s/{other['slug']}").status_code == 401
    w.cookies.set("kist_share", value[:-4] + "0000")
    assert w.get(f"/s/{share['slug']}").status_code == 401
    w.cookies.set("kist_share", "99999999999.deadbeef")
    assert w.get(f"/s/{share['slug']}").status_code == 401

    # changing the password signs every visitor out
    client.patch(f"/api/shares/{share['id']}", json={"password": "battery staple"})
    assert v.get(f"/s/{share['slug']}").status_code == 401


def test_password_attempts_are_rate_limited(client, upload, monkeypatch):
    monkeypatch.setattr(get_settings(), "share_password_attempts", 3)
    folder, _ = folder_with_items(client, upload, 1)
    share = make_share(client, target_kind="folder", target_id=folder["id"], password="sesame!")
    v = anon(client)
    for _ in range(3):
        assert v.post(f"/s/{share['slug']}", data={"password": "guess"}).status_code == 401
    blocked = v.post(f"/s/{share['slug']}", data={"password": "sesame!"})
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    # other links aren't affected
    other = make_share(client, target_kind="folder", target_id=folder["id"], password="sesame!")
    assert v.post(f"/s/{other['slug']}", data={"password": "sesame!"}).status_code == 303
    # an oversized password body is refused without hashing it
    big = v.post(f"/s/{other['slug']}", data={"password": "x" * 10_000})
    assert big.status_code in (401, 413)


def test_expired_and_revoked_shares_are_gone(client, upload):
    folder, items = folder_with_items(client, upload, 1)
    share = make_share(client, target_kind="folder", target_id=folder["id"])
    revoked = make_share(client, target_kind="folder", target_id=folder["id"])
    client.post(f"/api/shares/{revoked['id']}/revoke")
    expired = make_share(client, target_kind="folder", target_id=folder["id"])
    from kist.db import SessionLocal
    from kist.models import Share

    with SessionLocal() as s:
        row = s.get(Share, expired["id"])
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        s.commit()
    v = anon(client)
    assert v.get(f"/s/{share['slug']}").status_code == 200
    for dead in (revoked, expired):
        assert v.get(f"/s/{dead['slug']}").status_code == 410
        assert v.get(f"/s/{dead['slug']}/i/{items[0]['id']}/thumb").status_code == 410
        assert v.post(f"/s/{dead['slug']}", data={"password": "x"}).status_code == 410
    listed = {s["id"]: s["status"] for s in client.get("/api/shares").json()}
    assert listed == {share["id"]: "active", revoked["id"]: "revoked",
                      expired["id"]: "expired"}
    # the default expiry is 30 days; 0 means never
    assert share["expires_at"] is not None
    forever = make_share(client, target_kind="folder", target_id=folder["id"], expires_in_days=0)
    assert forever["expires_at"] is None


def test_downloads_follow_the_share_settings(client, upload):
    data = png_bytes((1, 2, 3))
    folder = client.post("/api/folders", json={"name": "F"}).json()
    item = upload("orig.png", data=data, folder_id=folder["id"])["item"]
    v = anon(client)
    view_only = make_share(client, target_kind="folder", target_id=folder["id"])
    assert v.get(f"/s/{view_only['slug']}/i/{item['id']}/file").status_code == 403
    assert "download" not in v.get(f"/s/{view_only['slug']}").text.split("<main>")[1]
    copies = make_share(client, target_kind="folder", target_id=folder["id"],
                        allow_download=True)
    res = v.get(f"/s/{copies['slug']}/i/{item['id']}/file")
    assert res.status_code == 200 and res.headers["content-type"] == "image/webp"
    assert "attachment" in res.headers["content-disposition"]
    assert res.content != data
    originals = make_share(client, target_kind="folder", target_id=folder["id"],
                           allow_download=True, allow_original=True)
    res = v.get(f"/s/{originals['slug']}/i/{item['id']}/file")
    assert res.status_code == 200 and res.content == data
    assert "sandbox" in res.headers["content-security-policy"]
    # allow_original without allow_download is not a thing
    odd = make_share(client, target_kind="folder", target_id=folder["id"], allow_original=True)
    assert odd["allow_original"] is False


def test_titles_and_filenames_are_escaped(client, upload):
    folder = client.post("/api/folders", json={"name": "F"}).json()
    evil = '"><img src=x onerror=alert(1)>.png'
    item = upload("x.png", folder_id=folder["id"],
                  source_url="https://private.example/secret-page")["item"]
    assert client.patch(f"/api/items/{item['id']}", json={"filename": evil}).status_code == 200
    share = make_share(client, target_kind="folder", target_id=folder["id"],
                       title="<script>alert(1)</script>", password="pass word")
    v = anon(client)
    gate = v.get(f"/s/{share['slug']}")
    assert "<script>alert" not in gate.text and "&lt;script&gt;" in gate.text
    v.post(f"/s/{share['slug']}", data={"password": "pass word"})
    page = v.get(f"/s/{share['slug']}")
    assert page.status_code == 200
    assert "<script>alert" not in page.text
    assert "<img src=x" not in page.text and "onerror=alert(1)>" not in page.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in page.text
    # where an item came from is not shown to visitors
    assert "private.example" not in page.text
    assert item["id"] in page.text


def test_items_and_smart_shares(client, upload):
    a, b, c = (upload(f"{n}.png", tags="red" if n != "c" else "blue")["item"]
               for n in "abc")
    pick = make_share(client, target_kind="items", item_ids=[b["id"], a["id"]])
    v = anon(client)
    page = v.get(f"/s/{pick['slug']}").text
    assert page.index(b["id"]) < page.index(a["id"]) and c["id"] not in page
    smart = client.post("/api/smart-folders", json={
        "name": "Reds", "query": {"tags": ["red"], "deleted": True}}).json()
    live = make_share(client, target_kind="smart", target_id=smart["id"])
    page = v.get(f"/s/{live['slug']}").text
    assert a["id"] in page and b["id"] in page and c["id"] not in page
    # live: a new red item shows up; the trash never does, whatever the query says
    d = upload("d.png", tags="red")["item"]
    client.delete(f"/api/items/{a['id']}")
    page = v.get(f"/s/{live['slug']}").text
    assert d["id"] in page and a["id"] not in page
    bad = client.post("/api/shares", json={"target_kind": "items", "item_ids": ["01NOPE"]})
    assert bad.status_code == 422
    assert client.post("/api/shares", json={"target_kind": "board",
                                            "target_id": "x"}).status_code == 422
    assert client.post("/api/shares", json={"target_kind": "folder",
                                            "target_id": "01GHOST"}).status_code == 422


def test_only_write_and_admin_tokens_manage_shares(client, upload):
    from test_tokens import as_token, make_token

    folder, _ = folder_with_items(client, upload, 1)
    body = {"target_kind": "folder", "target_id": folder["id"]}
    for scope, status in (("read", 403), ("agent", 403), ("add", 403)):
        tok = make_token(client, name=f"t-{scope}", scopes=[scope])["token"]
        c = as_token(client, tok)
        assert c.post("/api/shares", json=body).status_code == status, scope
        assert c.get("/api/shares").status_code == 403, scope
    writer = as_token(client, make_token(client, name="w", scopes=["write"])["token"])
    res = writer.post("/api/shares", json=body)
    assert res.status_code == 201 and res.json()["created_by"] == "w"


def test_views_are_counted_without_the_visitor(client, upload):
    folder, _ = folder_with_items(client, upload, 1)
    share = make_share(client, target_kind="folder", target_id=folder["id"])
    v = anon(client)
    v.get(f"/s/{share['slug']}", headers={"X-Forwarded-For": "203.0.113.9"})
    v.get(f"/s/{share['slug']}")
    got = client.get(f"/api/shares/{share['id']}").json()
    assert got["view_count"] == 2 and got["last_viewed_at"]
    shares_svc._last_counter_sync = 0.0
    v.get(f"/s/{share['slug']}")
    on_disk = shares_svc.path().read_text()
    assert "203.0.113" not in on_disk and "testclient" not in on_disk


def test_shares_survive_a_rebuild_and_verify_checks_them(client, upload):
    folder, items = folder_with_items(client, upload, 1)
    share = make_share(client, target_kind="folder", target_id=folder["id"], password="pw pw")
    text = shares_svc.path().read_text()
    assert "pw pw" not in text and "scrypt$" in text
    from kist.db import SessionLocal
    from kist.services import verify

    with SessionLocal() as s:
        assert verify.run(s).problems == []
    assert cli.rebuild_index(wipe=True) == 0
    got = client.get(f"/api/shares/{share['id']}").json()
    assert got["slug"] == share["slug"] and got["has_password"]
    v = anon(client)
    assert v.post(f"/s/{share['slug']}", data={"password": "pw pw"}).status_code == 303
    assert v.get(f"/s/{share['slug']}/i/{items[0]['id']}/thumb").status_code == 200
    # drift is reported
    shares_svc.path().write_text('{"version": 1, "shares": []}')
    with SessionLocal() as s:
        assert any("shares.json" in p for p in verify.run(s).problems)


def test_access_log_drops_visitor_addresses():
    import logging

    from kist.share_pages import NoVisitorAddress

    def rec(path):
        r = logging.LogRecord("uvicorn.access", logging.INFO, "", 0,
                              '%s - "%s %s HTTP/%s" %d', ("203.0.113.7:5555", "GET", path,
                                                          "1.1", 200), None)
        NoVisitorAddress().filter(r)
        return r.getMessage()

    assert "203.0.113.7" not in rec("/s/abcdefghijklmnopqrstuv")
    assert "203.0.113.7" in rec("/api/items")
    assert any(isinstance(f, NoVisitorAddress)
               for f in logging.getLogger("uvicorn.access").filters)


def test_unnormalised_paths_never_skip_the_token(client, upload):
    """A raw '..' in the path (clients normally clean it up) must not turn
    an API path into a public one."""
    import asyncio

    upload()

    async def call(path: str) -> tuple[int, bytes]:
        sent: list = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": "GET", "scheme": "http", "path": path,
                 "raw_path": path.encode(), "query_string": b"", "root_path": "",
                 "headers": [(b"host", b"testserver")], "client": ("203.0.113.1", 1),
                 "server": ("testserver", 80), "state": {}}
        await client.app(scope, receive, send)
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return status, body

    for path in ("/s/x/../../api/items", "/s/../api/items", "/s/../mcp",
                 "/s/abcdefghijklmnopqrstuv/../../api/tokens"):
        status, body = asyncio.run(call(path))
        assert status in (401, 404), (path, status)
        assert b'"items"' not in body and b"kist_" not in body
