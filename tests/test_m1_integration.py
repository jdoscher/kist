"""Where the platform (tokens, history, retention, rebuild) meets the AI
infrastructure (helpers, sidecars, ?semantic=). Uses test_ai's fake ml."""

import json
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from test_ai import drain, ml, solid_png  # noqa: F401  (ml is a fixture)
from test_tokens import MCP_HEADERS, as_token, make_token, mcp_result

from kist import cli
from kist.config import get_settings
from kist.db import SessionLocal
from kist.models import ItemEmbedding, ItemEvent, ItemText

FORBIDDEN = {"detail": "This token can't do that"}


def test_helper_token_runs_jobs_and_nothing_else(client, upload, ml):  # noqa: F811
    item = upload("shot.png", data=solid_png((0, 0, 220)))["item"]
    helper = as_token(client, make_token(client, "Mac mini", ["helper"])["token"])

    body = helper.post("/api/ai/jobs/claim", json={"kinds": ["embed"]}).json()
    [job] = body["jobs"]
    assert job["image_url"] == f"/api/ai/jobs/{job['id']}/image"
    img = helper.get(job["image_url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/webp"
    vec = [0.0] * 512
    vec[2] = 1.0
    res = helper.post(f"/api/ai/jobs/{job['id']}/result",
                      json={"vector": vec, "model": body["model"]})
    assert res.status_code == 200, res.text
    # no X-Kist-Helper header: the helper is known by its token's name
    status = client.get("/api/ai/status").json()
    assert [h["name"] for h in status["helpers"]] == ["Mac mini"]

    # ...and that's all a helper token reaches
    for path in ("/api/items", f"/api/items/{item['id']}", f"/api/items/{item['id']}/thumb",
                 f"/api/items/{item['id']}/file", "/api/ai/status", "/api/jobs",
                 "/api/archive-health"):
        res = helper.get(path)
        assert res.status_code == 403 and res.json() == FORBIDDEN, path
    assert helper.post("/mcp", json={}, headers=MCP_HEADERS).status_code == 403

    # a job image only for the helper holding the job
    [ocr] = client.post("/api/ai/jobs/claim", json={"kinds": ["ocr"]},
                        headers={"X-Kist-Helper": "quarry"}).json()["jobs"]
    assert helper.get(f"/api/ai/jobs/{ocr['id']}/image").status_code == 409
    assert client.get(f"/api/ai/jobs/{ocr['id']}/image",
                      headers={"X-Kist-Helper": "quarry"}).status_code == 200


def test_other_scopes_cant_use_the_helper_endpoints_or_ai_status(client, upload, ml):  # noqa: F811
    upload("shot.png")
    for scope in ("read", "add", "agent", "write"):
        other = as_token(client, make_token(client, f"t-{scope}", [scope])["token"])
        assert other.post("/api/ai/jobs/claim", json={}).status_code == 403, scope
        assert other.get("/api/ai/jobs/1/image").status_code == 403, scope
        assert other.post("/api/ai/jobs/1/result", json={"text": ""}).status_code == 403, scope
        res = other.get("/api/ai/status")
        assert res.status_code == 403 and res.json() == FORBIDDEN, scope
    assert client.get("/api/ai/status").status_code == 200  # the owner is admin

    # MCP ai_status follows the MCP rules: any MCP scope may read it
    reader = as_token(client, make_token(client, "mcp-reader", ["read"])["token"])
    out = mcp_result(reader, "ai_status")
    assert not out.get("isError") and "embedded" in out["content"][0]["text"]


def test_ai_results_are_not_item_history(client, upload, ml, clean):  # noqa: F811
    item = upload("shot.png", data=solid_png((10, 200, 10)))["item"]
    assert drain() == 2  # embed + ocr
    with SessionLocal() as s:
        kinds = s.execute(sa.select(ItemEvent.kind, ItemEvent.actor)
                          .where(ItemEvent.item_id == item["id"])).all()
        assert s.get(ItemText, (item["id"], "ocr")) is not None
    assert kinds == [("create", "owner")]
    assert client.get("/api/batches?actor=worker").json() == []
    lines = (clean / "items" / item["id"] / "events.jsonl").read_text().splitlines()
    assert [json.loads(line)["kind"] for line in lines] == ["create"]


def _folders_and_items(client, upload):
    a = client.post("/api/folders", json={"name": "A"}).json()
    b = client.post("/api/folders", json={"name": "B"}).json()
    items = {}
    for name, rgb, folder in (("r.png", (230, 10, 10), a), ("g.png", (10, 230, 10), b),
                              ("b.png", (10, 10, 230), a), ("g2.png", (20, 220, 20), b),
                              ("k.png", (20, 20, 20), a)):
        items[name] = upload(name, data=solid_png(rgb),
                             folder_ids=json.dumps([folder["id"]]))["item"]["id"]
    return a, b, items


def test_semantic_search_respects_a_folder_limited_reader(client, upload, ml):  # noqa: F811
    a, _b, items = _folders_and_items(client, upload)
    drain()
    visible = {items["r.png"], items["b.png"], items["k.png"]}
    fam = as_token(client, make_token(client, "Family", ["read"], folder_ids=[a["id"]])["token"])

    page = fam.get("/api/items", params={"semantic": "green"}).json()
    assert {i["id"] for i in page["items"]} == visible
    assert page["total"] == 3

    # paging a semantic search: an offset cursor, total on the first page only
    seen, cursor, totals = [], None, []
    while True:
        params = {"semantic": "green", "limit": 1}
        if cursor:
            params["cursor"] = cursor
        res = fam.get("/api/items", params=params)
        assert res.status_code == 200, res.text
        body = res.json()
        seen += [i["id"] for i in body["items"]]
        totals.append(body["total"])
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 3 and set(seen) == visible
    assert totals[0] == 3 and totals[1:] == [None, None]

    # the AI endpoints are outside a limited reader's view
    assert fam.get("/api/ai/status").status_code == 404
    assert fam.post("/api/ai/jobs/claim", json={}).status_code == 404
    out = mcp_result(fam, "ai_status")
    assert out["isError"] and "can't do that" in out["content"][0]["text"]


def test_semantic_paging_for_the_owner(client, upload, ml):  # noqa: F811
    _a, _b, items = _folders_and_items(client, upload)
    drain()
    first = client.get("/api/items", params={"semantic": "green", "limit": 2}).json()
    assert first["total"] == 5
    assert {i["id"] for i in first["items"]} == {items["g.png"], items["g2.png"]}
    rest = client.get("/api/items", params={"semantic": "green", "limit": 10,
                                            "cursor": first["next_cursor"]}).json()
    assert rest["total"] is None and rest["next_cursor"] is None
    assert len(rest["items"]) == 3
    assert not {i["id"] for i in rest["items"]} & {i["id"] for i in first["items"]}
    # a keyword cursor can't continue a semantic search, nor the other way round
    plain = client.get("/api/items", params={"limit": 2}).json()
    assert client.get("/api/items", params={"semantic": "green",
                                             "cursor": plain["next_cursor"]}).status_code == 400
    assert client.get("/api/items", params={"cursor": first["next_cursor"]}).status_code == 400


def test_purging_removes_ai_rows_and_sidecars(client, upload, ml, clean):  # noqa: F811
    from kist.services import retention

    old = upload("old.png")["item"]
    by_hand = upload("hand.png")["item"]
    keep = upload("keep.png")["item"]
    drain()
    items_dir = get_settings().items_dir
    assert (items_dir / old["id"] / "ai" / "ocr.json").is_file()

    with SessionLocal() as s:
        s.execute(sa.text("UPDATE items SET deleted_at = :at WHERE id = :id"),
                  {"at": datetime.now(UTC) - timedelta(days=40), "id": old["id"]})
        s.commit()
    (clean / ".kist").mkdir(exist_ok=True)
    (clean / ".kist" / "retention.json").write_text(
        json.dumps({"since": (datetime.now(UTC) - timedelta(days=60)).isoformat()}))
    with SessionLocal() as s:
        assert retention.purge_expired(s, days=30) == [old["id"]]
    client.delete(f"/api/items/{by_hand['id']}")
    assert client.delete(f"/api/items/{by_hand['id']}?hard=true").status_code == 200

    with SessionLocal() as s:
        for table in (ItemEmbedding, ItemText):
            ids = set(s.execute(sa.select(table.item_id)).scalars())
            assert ids == {keep["id"]}, table.__tablename__
    for gone in (old, by_hand):
        assert not (items_dir / gone["id"]).exists()
    assert cli.verify() == 0


def test_rebuild_restores_platform_state_and_ai_results_together(client, upload, ml, clean):  # noqa: F811
    a = upload("a.png", data=solid_png((200, 10, 10)))["item"]
    b = upload("b.png", data=solid_png((10, 10, 200)))["item"]
    folder = client.post("/api/folders", json={"name": "Refs", "description": "kept"}).json()
    client.post(f"/api/items/{a['id']}/links", json={"dst_id": b["id"], "kind": "derived_from"})
    client.patch(f"/api/items/{b['id']}", json={"protected": True, "rating": 4})
    client.patch(f"/api/items/{a['id']}/folders", json={"add": [folder["id"]]})
    reader = make_token(client, "Reader", ["read"])
    drain()
    history_before = {i["id"]: client.get(f"/api/items/{i['id']}/history").json()
                      for i in (a, b)}
    assert cli.verify() == 0

    ml.down = True  # a recompute would fail: rebuild must read the sidecars
    ml.calls.clear()
    assert cli.rebuild_index(wipe=True) == 0
    assert ml.calls == []

    # platform state
    assert client.get(f"/api/items/{b['id']}").json()["protected"] is True
    links = client.get(f"/api/items/{a['id']}/links").json()
    assert [(x["item"]["id"], x["kind"]) for x in links] == [(b["id"], "derived_from")]
    for i in (a, b):
        assert client.get(f"/api/items/{i['id']}/history").json() == history_before[i["id"]]
    assert as_token(client, reader["token"]).get("/api/me").json()["name"] == "Reader"
    folders = client.get("/api/folders").json()
    assert [f["description"] for f in folders if f["id"] == folder["id"]] == ["kept"]
    # AI results
    with SessionLocal() as s:
        assert set(s.execute(sa.select(ItemEmbedding.item_id)).scalars()) == {a["id"], b["id"]}
        assert set(s.execute(sa.select(ItemText.item_id)).scalars()) == {a["id"], b["id"]}
    # and verify agrees about all of it
    assert cli.verify() == 0
    # semantic search works off the restored vectors (the ml service is back)
    ml.down = False
    page = client.get("/api/items", params={"semantic": "blue"}).json()
    assert page["items"][0]["id"] == b["id"]
