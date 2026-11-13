"""F6: projects, pinned sidebar entries and per-project custom fields."""

import json

from conftest import LIBRARY
from fastapi.testclient import TestClient

from kist import cli

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def folder(client, name, parent=None):
    res = client.post("/api/folders", json={"name": name, "parent_id": parent})
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


def project(c, name="Spring campaign", **extra):
    res = c.post("/api/projects", json={"name": name, **extra})
    assert res.status_code == 201, res.text
    return res.json()


def add_member(client, pid, kind, mid):
    res = client.post(f"/api/projects/{pid}/members", json={"kind": kind, "id": mid})
    assert res.status_code == 200, res.text
    return res.json()


def add_field(client, pid, name, ftype="text", **extra):
    res = client.post(f"/api/projects/{pid}/fields", json={"name": name, "type": ftype, **extra})
    assert res.status_code == 201, res.text
    return next(f for f in res.json()["fields"] if f["name"] == name)


def ids(res):
    assert res.status_code == 200, res.text
    return {i["id"] for i in res.json()["items"]}


def setup_job(client, upload):
    """A project over folder Job (with subfolder Job/Shots) plus a field set."""
    job = folder(client, "Job")
    shots = folder(client, "Shots", job)
    other = folder(client, "Other")
    p = project(client, client="Acme", job_number="J-104", phase="Pitch")
    add_member(client, p["id"], "folder", job)
    f = {
        "season": add_field(client, p["id"], "Season", "choice", choices=["SS27", "AW27"]),
        "edition": add_field(client, p["id"], "Edition", "number"),
        "shot": add_field(client, p["id"], "Shot date", "date"),
        "paper": add_field(client, p["id"], "Paper"),
        "ref": add_field(client, p["id"], "Reference", "url"),
    }
    a = upload("a.png", folder_id=job)["item"]
    b = upload("b.png", folder_id=shots)["item"]
    c = upload("c.png", folder_id=other)["item"]
    return p, f, a, b, c, {"job": job, "shots": shots, "other": other}


def test_project_crud_members_and_status(client, upload):
    job = folder(client, "Job")
    p = project(client, client="Acme", job_number="J-104", phase="Pitch", notes="Brief\nline 2")
    assert p["status"] == "active" and p["client"] == "Acme" and p["members"] == []
    smart = client.post("/api/smart-folders", json={"name": "Reds", "query": {"tags": ["red"]}})
    add_member(client, p["id"], "folder", job)
    got = add_member(client, p["id"], "smart", smart.json()["id"])
    assert [(m["kind"], m["name"], m["missing"]) for m in got["members"]] == [
        ("folder", "Job", False), ("smart", "Reds", False)]
    # adding twice is a no-op; unknown targets and kinds are refused
    assert len(add_member(client, p["id"], "folder", job)["members"]) == 2
    assert client.post(f"/api/projects/{p['id']}/members",
                       json={"kind": "folder", "id": "01NOPE"}).status_code == 422
    assert client.post(f"/api/projects/{p['id']}/members",
                       json={"kind": "show", "id": "01X"}).status_code == 422
    # a member deleted later is listed as missing until removed
    client.delete(f"/api/folders/{job}")
    got = client.get(f"/api/projects/{p['id']}").json()
    assert got["members"][0]["missing"] is True
    got = client.delete(f"/api/projects/{p['id']}/members/folder/{job}").json()
    assert [m["kind"] for m in got["members"]] == ["smart"]

    assert client.patch(f"/api/projects/{p['id']}", json={"status": "archived"}).json()[
        "status"] == "archived"
    assert client.get("/api/projects").json() == []
    assert [x["id"] for x in client.get("/api/projects?status=archived").json()] == [p["id"]]
    assert client.patch(f"/api/projects/{p['id']}", json={"status": "gone"}).status_code == 422
    assert client.post("/api/projects", json={"name": "  "}).status_code == 422
    assert client.delete(f"/api/projects/{p['id']}").status_code == 200
    assert client.get(f"/api/projects/{p['id']}").status_code == 404
    on_disk = json.loads((LIBRARY / ".kist" / "projects.json").read_text())
    assert on_disk["projects"] == []


def test_show_member_kind_can_be_registered():
    from kist.services import projects as svc

    try:
        svc.register_member_kind("show", lambda session, ids: {i: {"name": "Crit"} for i in ids})
        assert "show" in svc.MEMBER_KINDS
    finally:
        svc.MEMBER_KINDS.pop("show", None)


def test_custom_fields_values_scope_and_validation(client, upload):
    p, f, a, b, c, _ = setup_job(client, upload)
    # an item in a subfolder of the project's folder is in the project
    res = client.patch(f"/api/items/{b['id']}/fields", json={"values": {
        f["season"]["id"]: "ss27", f["edition"]["id"]: "3", f["shot"]["id"]: "2026-03-01",
        f["paper"]["id"]: "Hahnemühle  Photo Rag", f["ref"]["id"]: "https://example.com/x"}})
    assert res.status_code == 200, res.text
    values = res.json()["item"]["custom_fields"]
    assert values == {f["season"]["id"]: "SS27", f["edition"]["id"]: 3,
                      f["shot"]["id"]: "2026-03-01", f["paper"]["id"]: "Hahnemühle Photo Rag",
                      f["ref"]["id"]: "https://example.com/x"}
    listed = client.get(f"/api/items/{b['id']}/fields").json()
    assert listed[0]["project"]["id"] == p["id"]
    assert {x["name"]: x["value"] for x in listed[0]["fields"]}["Edition"] == 3
    # outside the project's folders: refused, and nothing listed
    out = client.patch(f"/api/items/{c['id']}/fields", json={"values": {f["paper"]["id"]: "x"}})
    assert out.status_code == 422 and "isn't in that project's folders" in out.text
    assert client.get(f"/api/items/{c['id']}/fields").json() == []
    bad = [(f["edition"]["id"], "three"), (f["shot"]["id"], "March"),
           (f["season"]["id"], "SS99"), (f["ref"]["id"], "example.com"), ("01NOPE", "x")]
    for fid, value in bad:
        r = client.patch(f"/api/items/{a['id']}/fields", json={"values": {fid: value}})
        assert r.status_code == 422, (fid, value, r.text)
    # durable in meta.json; verify is clean
    meta = json.loads((LIBRARY / "items" / b["id"] / "meta.json").read_text())
    assert meta["custom_fields"][f["edition"]["id"]] == 3
    from kist.db import SessionLocal
    from kist.services import verify

    with SessionLocal() as session:
        assert verify.run(session).problems == []
    # clearing with null / ""
    res = client.patch(f"/api/items/{b['id']}/fields",
                       json={"values": {f["paper"]["id"]: None, f["ref"]["id"]: ""}})
    assert f["paper"]["id"] not in res.json()["item"]["custom_fields"]
    assert f["ref"]["id"] not in res.json()["item"]["custom_fields"]
    # a field's type can't change; names stay unique per project
    assert client.patch(f"/api/projects/{p['id']}/fields/{f['paper']['id']}",
                        json={"type": "number"}).status_code == 422
    assert client.post(f"/api/projects/{p['id']}/fields",
                       json={"name": "season", "type": "text"}).status_code == 422


def test_field_filters_everywhere(client, upload):
    p, f, a, b, c, folders = setup_job(client, upload)
    client.patch(f"/api/items/{a['id']}/fields", json={"values": {
        f["season"]["id"]: "SS27", f["edition"]["id"]: 2, f["shot"]["id"]: "2026-01-10",
        f["paper"]["id"]: "Photo Rag 308"}})
    client.patch(f"/api/items/{b['id']}/fields", json={"values": {
        f["season"]["id"]: "AW27", f["edition"]["id"]: 7.5, f["shot"]["id"]: "2026-04-01"}})
    q = lambda s: ids(client.get(f"/api/items?{s}"))  # noqa: E731
    assert q("field.season=SS27") == {a["id"]}
    assert q("field.season=ss27|aw27") == {a["id"], b["id"]}
    assert q("field.edition=%3E%3D3") == {b["id"]}
    assert q("field.edition=1..2") == {a["id"]}
    assert q("field.edition=2") == {a["id"]}
    assert q("field.shot_date=2026-02-01..") == {b["id"]}
    assert q("field.shot_date=%3C2026-02-01") == {a["id"]}
    assert q("field.paper=rag") == {a["id"]}
    assert q("field.paper=*") == {a["id"]}
    assert q("field.paper=none") == {b["id"], c["id"]}
    assert q("field.nonesuch=x") == set()
    assert client.get("/api/items?field.edition=lots").status_code == 422
    assert q(f"project={p['id']}") == {a["id"], b["id"]}
    assert q(f"project={p['id']}&field.season=AW27") == {b["id"]}
    assert q("project=01NOPE") == set()
    # timeline / buckets go through filters_from_params
    tl = client.get("/api/timeline?field.season=SS27&group=month")
    assert tl.status_code == 200, tl.text
    assert "1" in json.dumps(tl.json())
    # a smart folder keeps them, and so does a share of it
    smart = client.post("/api/smart-folders", json={
        "name": "AW", "query": {"custom_fields": {"season": "AW27"}, "project": p["id"]}})
    assert smart.status_code in (200, 201), smart.text
    assert smart.json()["query"]["custom_fields"] == {"season": "AW27"}
    from kist.db import SessionLocal
    from kist.models import Item
    from kist.services import selection

    with SessionLocal() as session:
        import sqlalchemy as sa

        conds = selection.smart_filters(smart.json()["query"])
        got = set(session.execute(sa.select(Item.id).where(*conds)).scalars())
    assert got == {b["id"]}
    # field index for filter pickers
    keys = {x["key"]: x for x in client.get("/api/fields").json()}
    assert keys["season"]["choices"] == ["SS27", "AW27"]
    assert keys["shot_date"]["type"] == "date"


def test_undo_bulk_and_field_delete(client, upload):
    p, f, a, b, c, _ = setup_job(client, upload)
    before = client.get(f"/api/items/{a['id']}").json()["modified_at"]
    res = client.patch(f"/api/items/{a['id']}/fields",
                       json={"values": {f["paper"]["id"]: "Rag"}},
                       headers={"X-Kist-Batch": "fields-1"})
    assert res.status_code == 200
    assert client.get(f"/api/items/{a['id']}").json()["modified_at"] != before
    hist = client.get(f"/api/items/{a['id']}/history").json()
    assert any(e["kind"] == "fields" for e in (hist if isinstance(hist, list)
                                                else hist.get("events", [])))
    undo = client.post("/api/batches/fields-1/undo").json()
    assert undo["undone"] == 1, undo
    assert client.get(f"/api/items/{a['id']}").json()["custom_fields"] == {}

    bulk = client.post("/api/fields/bulk", json={
        "item_ids": [a["id"], b["id"], c["id"]], "values": {f["season"]["id"]: "AW27"}},
        headers={"X-Kist-Batch": "bulk-1"}).json()
    assert bulk["updated"] == 2
    assert [s["id"] for s in bulk["skipped"]] == [c["id"]]
    assert client.post("/api/fields/bulk", json={
        "item_ids": [a["id"]], "values": {f["edition"]["id"]: "x"}}).status_code == 422
    assert client.post("/api/batches/bulk-1/undo").json()["undone"] == 2

    client.patch(f"/api/items/{b['id']}/fields", json={"values": {f["paper"]["id"]: "Rag"}})
    got = client.delete(f"/api/projects/{p['id']}/fields/{f['paper']['id']}").json()
    assert f["paper"]["id"] not in {x["id"] for x in got["fields"]}
    assert client.get(f"/api/items/{b['id']}").json()["custom_fields"] == {}
    meta = json.loads((LIBRARY / "items" / b["id"] / "meta.json").read_text())
    assert meta["custom_fields"] == {}
    # deleting the project clears the rest
    client.patch(f"/api/items/{b['id']}/fields", json={"values": {f["edition"]["id"]: 4}})
    out = client.delete(f"/api/projects/{p['id']}").json()
    assert out["values_cleared"] == 1
    assert client.get(f"/api/items/{b['id']}").json()["custom_fields"] == {}


def test_pins_order_archive_and_dangling(client, upload):
    job = folder(client, "Job")
    gone = folder(client, "Gone")
    upload("t.png", tags="moodboard")
    p = project(client)
    smart = client.post("/api/smart-folders", json={"name": "Reds", "query": {}}).json()
    for body in ({"kind": "project", "ref": p["id"]}, {"kind": "folder", "ref": job},
                 {"kind": "folder", "ref": gone}, {"kind": "smart", "ref": smart["id"]},
                 {"kind": "tag", "ref": "Moodboard"},
                 {"kind": "search", "name": "Blue picks", "query": "?color=%230000ff&flag=pick"}):
        res = client.post("/api/pins", json=body)
        assert res.status_code == 200, (body, res.text)
    pins = client.get("/api/pins").json()
    assert [x["kind"] for x in pins] == ["project", "folder", "folder", "smart", "tag", "search"]
    assert pins[4]["name"] == "moodboard" and pins[5]["query"] == "color=%230000ff&flag=pick"
    # pinning twice is a no-op; unknown targets are refused
    assert len(client.post("/api/pins", json={"kind": "folder", "ref": job}).json()) == 6
    assert client.post("/api/pins", json={"kind": "folder", "ref": "01NOPE"}).status_code == 422
    assert client.post("/api/pins", json={"kind": "tag", "ref": "nope"}).status_code == 422
    # drag to reorder
    order = [{"kind": "search", "ref": pins[5]["ref"]}, {"kind": "folder", "ref": job}]
    got = client.put("/api/pins/order", json={"pins": order}).json()
    assert [x["kind"] for x in got][:2] == ["search", "folder"]
    # a deleted folder drops out; an archived project drops out and comes back
    client.delete(f"/api/folders/{gone}")
    client.patch(f"/api/projects/{p['id']}", json={"status": "archived"})
    kinds = [(x["kind"], x["ref"]) for x in client.get("/api/pins").json()]
    assert ("folder", gone) not in kinds and ("project", p["id"]) not in kinds
    client.patch(f"/api/projects/{p['id']}", json={"status": "active"})
    assert ("project", p["id"]) in [(x["kind"], x["ref"]) for x in client.get("/api/pins").json()]
    got = client.delete("/api/pins?kind=tag&ref=moodboard").json()
    assert "tag" not in [x["kind"] for x in got]


def test_folder_limited_reader_sees_only_its_pins(client, upload):
    mine = folder(client, "Mine")
    secret = folder(client, "Secret")
    p = project(client, name="Secret job")
    add_member(client, p["id"], "folder", secret)
    for body in ({"kind": "folder", "ref": mine}, {"kind": "folder", "ref": secret},
                 {"kind": "project", "ref": p["id"]},
                 {"kind": "search", "name": "x", "query": "q=secret"}):
        client.post("/api/pins", json=body)
    item = upload("s.png", folder_id=secret)["item"]
    f = add_field(client, p["id"], "Client code")
    client.patch(f"/api/items/{item['id']}/fields", json={"values": {f["id"]: "ACME-1"}})

    tok = client.post("/api/tokens", json={"name": "r", "scopes": ["read"],
                                           "folder_ids": [mine]}).json()
    reader = TestClient(client.app, headers={"Authorization": f"Bearer {tok['token']}"})
    assert [(x["kind"], x["ref"]) for x in reader.get("/api/pins").json()] == [("folder", mine)]
    assert reader.get("/api/projects").status_code == 404
    assert reader.get(f"/api/projects/{p['id']}").status_code == 404
    assert reader.get("/api/fields").status_code == 404
    assert reader.get(f"/api/items/{item['id']}/fields").status_code == 404
    assert reader.post("/api/pins", json={"kind": "folder", "ref": mine}).status_code in (403, 404)
    # filters never widen the reader's scope
    res = reader.get(f"/api/items?project={p['id']}")
    assert res.status_code == 200 and res.json()["items"] == []
    res = reader.get("/api/items?field.client_code=ACME")
    assert res.status_code == 200 and res.json()["items"] == []
    # a plain (unlimited) read token can list projects but not change them
    tok2 = client.post("/api/tokens", json={"name": "r2", "scopes": ["read"]}).json()
    plain = TestClient(client.app, headers={"Authorization": f"Bearer {tok2['token']}"})
    assert plain.get("/api/projects").status_code == 200
    assert plain.post("/api/projects", json={"name": "x"}).status_code == 403


def test_mcp_tools(client, upload):
    p, f, a, b, c, _ = setup_job(client, upload)

    def call(name, args):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": args}}
        res = client.post("/mcp", json=body, headers=MCP_HEADERS)
        assert res.status_code == 200, res.text
        out = res.json()["result"]
        assert not out.get("isError"), out
        return json.loads(out["content"][0]["text"]) if out.get("content") else out

    listed = call("list_projects", {})
    listed = listed if isinstance(listed, list) else [listed]
    assert listed[0]["id"] == p["id"]
    out = call("set_field_values", {"item_ids": [a["id"], c["id"]],
                                    "values": {f["season"]["id"]: "SS27"}})
    assert out["updated"] == 1 and out["skipped"][0]["id"] == c["id"]
    got = call("get_project", {"id": p["id"]})
    assert got["item_count"] == 2 and {i["id"] for i in got["recent_items"]} == {a["id"], b["id"]}
    found = call("search_items", {"fields": {"season": "SS27"}})
    assert [i["id"] for i in found["items"]] == [a["id"]]


def test_rebuild_keeps_projects_pins_and_values(client, upload):
    p, f, a, b, c, folders = setup_job(client, upload)
    client.patch(f"/api/items/{b['id']}/fields", json={"values": {
        f["season"]["id"]: "AW27", f["edition"]["id"]: 12}})
    client.post("/api/pins", json={"kind": "project", "ref": p["id"]})
    client.post("/api/pins", json={"kind": "folder", "ref": folders["shots"]})
    before_item = client.get(f"/api/items/{b['id']}").json()
    before_project = client.get(f"/api/projects/{p['id']}").json()
    before_pins = client.get("/api/pins").json()

    assert cli.rebuild_index(wipe=True) == 0

    after_item = client.get(f"/api/items/{b['id']}").json()
    assert after_item["custom_fields"] == before_item["custom_fields"]
    assert after_item["modified_at"] == before_item["modified_at"]
    assert client.get(f"/api/projects/{p['id']}").json() == before_project
    assert client.get("/api/pins").json() == before_pins
    assert ids(client.get("/api/items?field.edition=%3E10")) == {b["id"]}
    from kist.db import SessionLocal
    from kist.services import verify

    with SessionLocal() as session:
        assert verify.run(session).problems == []


def test_verify_reports_a_broken_projects_file(client):
    project(client)
    (LIBRARY / ".kist" / "projects.json").write_text("{not json")
    from kist.db import SessionLocal
    from kist.services import verify

    with SessionLocal() as session:
        assert "projects.json unreadable" in verify.run(session).problems
