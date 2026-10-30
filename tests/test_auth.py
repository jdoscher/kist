from conftest import TOKEN
from fastapi.testclient import TestClient


def bare(client):
    return TestClient(client.app)


def test_health_is_public(client):
    assert bare(client).get("/api/health").status_code == 200


def test_api_requires_token(client):
    anon = bare(client)
    assert anon.get("/api/items").status_code == 401
    assert anon.get("/api/items", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert anon.get(f"/api/items?token={TOKEN}").status_code == 200


def test_cookie_only_authenticates_reads(client):
    anon = bare(client)
    anon.cookies.set("kist_token", TOKEN)
    assert anon.get("/api/items").status_code == 200
    # a cookie must not authorize writes (cross-site request forgery)
    assert anon.post("/api/folders", json={"name": "x"}).status_code == 401


def test_mcp_requires_token(client):
    assert bare(client).post("/mcp", json={}).status_code == 401


def test_web_ui_is_not_gated(client):
    res = bare(client).get("/")
    assert res.status_code in (200, 404)  # 404 when web/dist isn't built
