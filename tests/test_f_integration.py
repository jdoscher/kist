"""The art-team features together (the f-int integration of F1-F7): one
query that uses every new filter in every listing, one library with a bit
of every feature's durable data wiped and rebuilt with the ml service down,
a folder-limited reader against every new route family, the MCP tool list,
and the cross-feature hooks (print size -> Low-res, Ken Burns blocks, show
members of a project, credits in Present and on the proofing page)."""

import json
import re

import httpx
import pytest
import sqlalchemy as sa
from conftest import png_bytes
from fastapi.testclient import TestClient
from test_f7_cutouts import cutout_jobs, drain, ml, photo_png  # noqa: F401  (fixture)
from test_mcp import init, rpc
from test_tokens import as_token, make_token

from kist import cli, worker
from kist.config import get_settings
from kist.db import SessionLocal
from kist.models import Item
from kist.services import ai, kinds, present, projects, selection

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
RED = (155, 36, 35)  # RAL 3001 Signal red
BLUE = (30, 60, 200)
RAL_CSV = "name,code,hex\nSignal red,RAL 3001,#9b2423\nAnthracite grey,RAL 7016,#383e42\n"


def run_jobs():
    while worker.run_one():
        pass


def ids(res) -> set[str]:
    assert res.status_code == 200, res.text
    return {i["id"] for i in res.json()["items"]}


def mcp(c, name, args):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": args}}
    res = c.post("/mcp", json=body, headers=MCP_HEADERS)
    assert res.status_code == 200, res.text
    out = res.json()["result"]
    assert not out.get("isError"), out
    return json.loads(out["content"][0]["text"])


def folder(client, name):
    res = client.post("/api/folders", json={"name": name})
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


def project_with_field(client, folder_id):
    p = client.post("/api/projects", json={"name": "Spring", "client": "Acme"}).json()
    assert client.post(f"/api/projects/{p['id']}/members",
                       json={"kind": "folder", "id": folder_id}).status_code == 200
    res = client.post(f"/api/projects/{p['id']}/fields",
                      json={"name": "Season", "type": "choice", "choices": ["SS27", "AW27"]})
    assert res.status_code == 201, res.text
    field = next(f for f in res.json()["fields"] if f["name"] == "Season")
    return p, field


def import_ral(client):
    res = client.post("/api/colourbooks", data={"name": "RAL"},
                      files={"file": ("ral.csv", RAL_CSV, "application/octet-stream")})
    assert res.status_code == 201, res.text
    return res.json()


# ---------- task 3: every new filter in one query, in every listing ----------


def test_new_filters_work_together_everywhere(client, upload):
    job, other = folder(client, "Job"), folder(client, "Other")
    p, season = project_with_field(client, job)
    import_ral(client)

    n = iter(range(100))

    # 700 x 520: not a screen's size, so only the names make screenshots; each
    # a shade apart (the same bytes would be the same item)
    def item(name, *, colour=RED, size=(700, 520), where=job, value="SS27",
             rights="licensed"):
        shade = (colour[0], colour[1], colour[2] + next(n))
        it = upload(name, data=png_bytes(color=shade, size=size), folder_id=where)["item"]
        if value and where == job:
            res = client.patch(f"/api/items/{it['id']}/fields",
                               json={"values": {season["id"]: value}})
            assert res.status_code == 200, res.text
        client.patch(f"/api/items/{it['id']}",
                     json={"provenance": {"rights_status": rights, "credit": "Studio X"}})
        return it["id"]

    want = item("Screenshot 2026-11-12 at 10.00.00.png")
    misses = {
        "kind": item("plain.png"),
        "field": item("Screenshot 2026-11-12 at 10.00.01.png", value="AW27"),
        "rights": item("Screenshot 2026-11-12 at 10.00.02.png", rights="comp"),
        "project": item("Screenshot 2026-11-12 at 10.00.03.png", where=other),
        "print": item("Screenshot 2026-11-12 at 10.00.04.png", size=(2000, 1600)),
        "colour": item("Screenshot 2026-11-12 at 10.00.05.png", colour=BLUE),
    }
    run_jobs()  # kinds sorting
    every = {"auto_kind": "screenshots", "rights_status": "licensed", "project": p["id"],
             "field.season": "SS27", "print_max": "5in"}
    colour = {"color": "book:ral/RAL 3001"}
    # each filter alone lets its miss through, all together keep only `want`
    assert misses["kind"] in ids(client.get("/api/items", params={"auto_kind": "-screenshots"}))
    plain = ids(client.get("/api/items", params=every))
    assert plain == {want, misses["colour"]}
    assert ids(client.get("/api/items", params={**every, **colour})) == {want}
    # ranked (text and colour ranking), timeline, map and buckets take them too
    assert ids(client.get("/api/items", params={**every, **colour, "order": "-modified",
                                                 "q": "screenshot"})) == {want}
    days = client.get("/api/timeline", params={"group": "year", **every}).json()
    assert sum(b["count"] for b in days) == 2
    assert client.get("/api/map", params={"zoom": 1, **every}).json()["total"] == 0
    buckets = client.get("/api/items/buckets", params={"sort": "created", **every})
    assert buckets.status_code == 200, buckets.text
    assert sum(b["count"] for b in buckets.json()["buckets"]) == 2
    # search by image keeps the filters (no embeddings here: the filter alone)
    by_image = client.post("/api/search/image", params=every,
                           files={"file": ("q.png", png_bytes(color=RED), "image/png")})
    assert by_image.status_code in (200, 503), by_image.text  # 503: no ml service here
    if by_image.status_code == 200:
        assert {i["id"] for i in by_image.json()["items"]} <= {want, misses["colour"]}
    # a smart folder saves every one of them, and its share shows the same items
    query = {"auto_kind": "screenshots", "rights_status": "licensed", "project": p["id"],
             "custom_fields": {"season": "SS27"}, "print_max": "5in"}
    sf = client.post("/api/smart-folders", json={"name": "Everything", "query": query})
    assert sf.status_code in (200, 201), sf.text
    saved = sf.json()["query"]
    for k, v in query.items():
        assert saved[k] == v, k
    with SessionLocal() as s:
        got = set(s.execute(sa.select(Item.id).where(*selection.smart_filters(saved)))
                  .scalars())
    assert got == {want, misses["colour"]}
    share = client.post("/api/shares", json={"target_kind": "smart",
                                             "target_id": sf.json()["id"]}).json()
    page = TestClient(client.app).get(f"/s/{share['slug']}").text
    shown = set(re.findall(r"/i/([0-9A-Z]{26})/thumb", page))
    assert shown == {want, misses["colour"]}
    # MCP: search_items and item_buckets take the same filters
    init(client)
    args = {"auto_kind": "screenshots", "rights_status": "licensed", "project": p["id"],
            "fields": {"season": "SS27"}, "print_max": "5in"}
    assert {i["id"] for i in mcp(client, "search_items", args)["items"]} == plain
    assert {i["id"] for i in mcp(client, "search_items", {**args, **colour})["items"]} == {want}
    counted = mcp(client, "item_buckets", args)
    assert sum(b["count"] for b in counted["buckets"]) == 2


# ---------- the hooks between features ----------


def test_hooks_between_features(client, upload):
    # F1 <- F4: Low-res is a print size (5 in at 300 dpi = 1,500 px)
    assert kinds._print_size is kinds._f4_print_size
    small = upload("small.png", data=png_bytes(size=(1400, 900)))["item"]  # 4.7 in
    big = upload("big.png", data=png_bytes(size=(1600, 900)))["item"]  # 5.3 in
    run_jobs()
    low = ids(client.get("/api/items", params={"auto_kind": "low-res"}))
    assert small["id"] in low and big["id"] not in low  # F1's old 1,200 px said neither
    # F1 <- F4: AI-made follows the same flag as F4's badge/filter; an
    # explicit "not AI" wins over the source type
    client.patch("/api/kinds/ai-made", json={"enabled": True})
    client.patch(f"/api/items/{small['id']}", json={"provenance": {
        "ai_generated": True, "ai_generator": "Midjourney"}})
    client.patch(f"/api/items/{big['id']}", json={"provenance": {
        "ai_generated": False,
        "digital_source_type": "http://cv.iptc.org/newscodes/digitalsourcetype/"
                               "trainedAlgorithmicMedia"}})
    run_jobs()
    assert ids(client.get("/api/items", params={"auto_kind": "ai-made"})) == \
        ids(client.get("/api/items", params={"ai_generated": "true"})) == {small["id"]}
    why = {k["kind"]: k for k in client.get(f"/api/items/{small['id']}/kinds").json()}
    assert "Midjourney" in why["ai-made"]["why"]

    # F2 <- F1: Ken Burns never moves a drawing, document or screenshot
    shot = upload("Screenshot 2026-11-12 at 09.00.00.png")["item"]
    photo = upload("photo.png", data=png_bytes(size=(1800, 1200)))["item"]
    run_jobs()
    with SessionLocal() as s:
        assert kinds.motion_blocked_ids(s, [shot["id"], photo["id"]]) == {shot["id"]}
        items = [s.get(Item, shot["id"]), s.get(Item, photo["id"])]
        ok = present.motion_ok_map(s, items)
    assert ok == {shot["id"]: False, photo["id"]: True}

    # F2 <- F4: a slide's caption carries F4's credit line, the licence apart
    client.patch(f"/api/items/{photo['id']}", json={"provenance": {
        "creator": "A. Maker", "credit": "Studio X", "license": "CC-BY-4.0"}})
    slides = client.post("/api/present/slides", json={
        "source": {"kind": "items", "item_ids": [photo["id"]]}})
    assert slides.status_code == 200, slides.text
    it = slides.json()["slides"][0]["items"][0]
    assert it["credit"] == "A. Maker / Studio X" and it["license"] == "CC-BY-4.0"
    pdf = client.post("/api/present/pdf", json={
        "source": {"kind": "items", "item_ids": [photo["id"]]}})
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"

    # F6 <- F2: a project holds saved shows
    f = folder(client, "Crit")
    show = client.post("/api/shows", json={"name": "Crit", "source": {"kind": "folder",
                                                                      "id": f}}).json()
    p = client.post("/api/projects", json={"name": "Spring"}).json()
    res = client.post(f"/api/projects/{p['id']}/members", json={"kind": "show", "id": show["id"]})
    assert res.status_code == 200, res.text
    member = next(m for m in res.json()["members"] if m["kind"] == "show")
    assert member["name"] == "Crit" and member["missing"] is False
    assert member["web_url"] == f"/?present={show['id']}"
    assert "show" in projects.MEMBER_KINDS
    client.delete(f"/api/shows/{show['id']}")
    got = client.get(f"/api/projects/{p['id']}").json()
    assert next(m for m in got["members"] if m["kind"] == "show")["missing"] is True

    # F3 + F4: the share page and the proofing item page both print the credit
    f2 = folder(client, "Proof")
    one = upload("proof.png", folder_id=f2)["item"]
    client.patch(f"/api/items/{one['id']}", json={"provenance": {"credit": "Studio Y"}})
    s = client.post("/api/shares", json={"target_kind": "folder", "target_id": f2}).json()
    client.patch(f"/api/shares/{s['id']}/proofing", json={"enabled": True})
    guest = TestClient(client.app, follow_redirects=False)
    gallery = guest.get(f"/s/{s['slug']}").text
    assert "Studio Y" in gallery and 'name="csrf"' in gallery
    assert "Studio Y" in guest.get(f"/s/{s['slug']}/i/{one['id']}").text


def test_cutout_slides_and_jobs_order(client, upload, ml):  # noqa: F811
    item = upload("chair.png", data=photo_png())["item"]
    client.post(f"/api/items/{item['id']}/cutout", json={})
    # a queued kinds re-sort never holds back the cut-out someone waits for
    with SessionLocal() as s:
        kinds.queue_all(s)
        assert not ai.light_jobs_waiting(s)
    assert drain() == ["done"]
    cut = cutout_jobs()[-1].payload["result_item_id"]
    slides = client.post("/api/present/slides", json={
        "source": {"kind": "items", "item_ids": [cut, item["id"]]}}).json()
    got = {i["id"]: i for sl in slides["slides"] for i in sl["items"]}
    assert got[cut]["cutout"] is True and "/cutout-preview?" in got[cut]["thumb"]
    assert got[item["id"]]["cutout"] is False and "/thumb?" in got[item["id"]]["thumb"]
    assert client.get(got[cut]["thumb"]).headers["content-type"] == "image/webp"


# ---------- task 5: one rebuild across every feature ----------


def _snapshot(client, ids_: dict[str, str], share_id: str, project_id: str) -> dict:
    out: dict = {}
    by_id = {i["id"]: i for i in client.get(
        "/api/items", params={"ids": ",".join(ids_.values()), "limit": 100}).json()["items"]}
    keep = ("provenance", "credit_line", "custom_fields", "modified_at", "folder_ids", "tags",
            "media", "flag")
    out["items"] = {k: {f: by_id[v].get(f) for f in keep} for k, v in ids_.items()}
    out["kinds"] = {k: sorted((r["kind"], r.get("verdict"), r.get("member"))
                              for r in client.get(f"/api/items/{v}/kinds").json())
                    for k, v in ids_.items()}
    ov = client.get("/api/kinds").json()
    out["kind_settings"] = {k: ov[k] for k in ("min_items", "low_res_px", "low_res_in")}
    out["shows"] = client.get("/api/shows").json()
    out["proofing"] = client.get(f"/api/shares/{share_id}/proofing").json()
    out["feedback"] = sorted((r["id"], r["kind"], r["round"], r.get("verdict"), r.get("text"),
                              r["guest_name"], r["accepted"])
                             for r in client.get("/api/feedback").json())
    out["credits"] = client.get("/api/credits/settings").json()
    out["books"] = client.get("/api/colourbooks").json()
    out["codes"] = client.get(f"/api/items/{ids_['red']}/palette/codes").json()
    out["project"] = client.get(f"/api/projects/{project_id}").json()
    out["pins"] = client.get("/api/pins").json()
    out["cutouts"] = [i["id"] for i in client.get(f"/api/items/{ids_['chair']}/cutouts").json()]
    out["links"] = client.get(f"/api/items/{ids_['cut']}/links").json()
    out["searches"] = {
        name: sorted(ids(client.get("/api/items", params=params)))
        for name, params in {
            "not_screenshot": {"auto_kind": "screenshots:rejected"},
            "licensed": {"rights_status": "licensed"},
            "project": {"project": project_id},
            "field": {"field.season": "SS27"},
            "colour_code": {"color": "book:ral/RAL 3001"},
        }.items()
    }
    return out


def test_rebuild_and_verify_cover_every_feature(client, upload, ml, capsys):  # noqa: F811
    job = folder(client, "Job")
    p, season = project_with_field(client, job)
    ids_ = {
        "shot": upload("Screenshot 2026-11-12 at 08.00.00.png", folder_id=job)["item"]["id"],
        "red": upload("red.png", data=png_bytes(color=RED), folder_id=job)["item"]["id"],
        "chair": upload("chair.png", data=photo_png(), folder_id=job)["item"]["id"],
    }
    run_jobs()
    # F1: a kind correction (and a settings change)
    res = client.post("/api/kinds/screenshots/correct",
                      json={"item_ids": [ids_["shot"]], "verdict": False})
    assert res.status_code == 200, res.text
    client.patch("/api/kinds", json={"min_items": 3, "low_res_in": 4})
    # F4: credits and rights, and its settings
    client.patch(f"/api/items/{ids_['red']}", json={"provenance": {
        "rights_status": "licensed", "credit": "Studio X", "creator": "A. Maker"}})
    client.patch("/api/credits/settings", json={"clearance_warning": False})
    # F5: a colour book
    import_ral(client)
    # F6: a custom field value and pins
    client.patch(f"/api/items/{ids_['red']}/fields", json={"values": {season["id"]: "SS27"}})
    # F2: a saved show, which the project holds (the F6 <- F2 hook)
    show = client.post("/api/shows", json={"name": "Crit", "source": {"kind": "folder",
                                                                      "id": job},
                                           "options": {"surround": "black"}}).json()
    client.post(f"/api/projects/{p['id']}/members", json={"kind": "show", "id": show["id"]})
    client.post("/api/pins", json={"kind": "project", "ref": p["id"]})
    client.post("/api/pins", json={"kind": "folder", "ref": job})
    # F3: a share with guest feedback, one accepted
    share = client.post("/api/shares", json={"target_kind": "folder", "target_id": job}).json()
    client.patch(f"/api/shares/{share['id']}/proofing", json={"enabled": True})
    guest = TestClient(client.app, follow_redirects=False)
    csrf = re.search(r'name="csrf" value="([^"]+)"', guest.get(f"/s/{share['slug']}").text)[1]

    def act(**form):
        res = guest.post(f"/s/{share['slug']}/proof", data={"csrf": csrf, "back": "1", **form})
        assert res.status_code == 303, res.text

    act(action="name", name="Bo")
    act(action="pick", item_id=ids_["red"])
    act(action="comment", item_id=ids_["chair"], text="love it")
    mark = next(r for r in client.get("/api/feedback").json() if r["kind"] == "mark")
    assert client.post("/api/feedback/accept", json={"mark_ids": [mark["id"]]}).status_code == 200
    # F7: a cut-out item
    client.post(f"/api/items/{ids_['chair']}/cutout", json={})
    assert drain() == ["done"]
    ids_["cut"] = cutout_jobs()[-1].payload["result_item_id"]
    run_jobs()

    before = _snapshot(client, ids_, share["id"], p["id"])
    assert before["searches"]["not_screenshot"] == [ids_["shot"]]
    assert before["searches"]["colour_code"] == [ids_["red"]]
    assert before["items"]["red"]["flag"] == "pick"  # the accepted guest pick
    assert any(m["kind"] == "show" and not m["missing"] for m in before["project"]["members"])
    assert cli.verify() == 0

    # the ml service is down for the rebuild: only the library's files are left
    def down(request):
        raise httpx.ConnectError("ml service down", request=request)

    ai.set_transport(httpx.MockTransport(down))
    kinds._prompt_cache.clear()
    assert cli.rebuild_index(wipe=True) == 0
    after = _snapshot(client, ids_, share["id"], p["id"])
    for key in before:
        assert after[key] == before[key], key
    assert cli.verify() == 0
    capsys.readouterr()


# ---------- a folder-limited reader against every new route family ----------


def test_folder_limited_reader_across_the_new_routes(client, upload, ml):  # noqa: F811
    a, b = folder(client, "A"), folder(client, "B")
    seen = upload("Screenshot 2026-11-12 at 07.00.00.png", data=png_bytes(color=RED),
                  folder_id=a)["item"]["id"]
    hidden = upload("Screenshot 2026-11-12 at 07.00.01.png", data=photo_png(),
                    folder_id=b)["item"]["id"]
    run_jobs()
    for i in (seen, hidden):
        client.patch(f"/api/items/{i}", json={"provenance": {"rights_status": "licensed",
                                                             "credit": "Studio X"}})
    import_ral(client)
    p, season = project_with_field(client, a)
    client.post(f"/api/projects/{p['id']}/members", json={"kind": "folder", "id": b})
    for i in (seen, hidden):
        client.patch(f"/api/items/{i}/fields", json={"values": {season["id"]: "SS27"}})
    client.post(f"/api/items/{hidden}/cutout", json={})
    assert drain() == ["done"]
    cut = cutout_jobs()[-1].payload["result_item_id"]  # filed in B like its source
    show_a = client.post("/api/shows", json={"name": "A", "source": {"kind": "folder",
                                                                     "id": a}}).json()
    show_b = client.post("/api/shows", json={"name": "B", "source": {"kind": "folder",
                                                                     "id": b}}).json()
    client.post("/api/pins", json={"kind": "folder", "ref": a})
    client.post("/api/pins", json={"kind": "folder", "ref": b})
    client.post("/api/pins", json={"kind": "project", "ref": p["id"]})
    share = client.post("/api/shares", json={"target_kind": "folder", "target_id": b}).json()
    client.patch(f"/api/shares/{share['id']}/proofing", json={"enabled": True})

    fam = as_token(client, make_token(client, "Fam", ["read"], folder_ids=[a])["token"])
    allowed = {seen}
    # every new filter, alone and together, sees only the reader's items
    for params in ({"auto_kind": "screenshots"}, {"rights_status": "licensed"},
                   {"needs_credit": "false"}, {"print_max": "5in"}, {"project": p["id"]},
                   {"field.season": "SS27"}, {"color": "book:ral/RAL 3001"},
                   {"auto_kind": "screenshots", "project": p["id"], "field.season": "SS27",
                    "rights_status": "licensed"}):
        assert ids(fam.get("/api/items", params=params)) <= allowed, params
        tl = fam.get("/api/timeline", params={"group": "year", **params})
        if "color" not in params:
            assert sum(x["count"] for x in tl.json()) <= 1, params
    # per-item reads of a hidden item are 404, like the item itself
    for path in ("kinds", "print", "credits", "palette/codes", "cutout-preview"):
        assert fam.get(f"/api/items/{hidden}/{path}").status_code == 404, path
        assert fam.get(f"/api/items/{cut}/{path}").status_code == 404, path
    assert fam.get(f"/api/items/{seen}/kinds").status_code == 200
    assert fam.get(f"/api/items/{seen}/credits").status_code == 200
    counts = {k["id"]: k["count"] for k in fam.get("/api/kinds").json()["kinds"]}
    assert counts["screenshots"] <= 1
    assert fam.get("/api/colourbooks/lookup", params={"q": "RAL 3001"}).status_code == 200
    # Present: only its own items and shows
    assert [s["id"] for s in fam.get("/api/shows").json()] == [show_a["id"]]
    assert fam.get(f"/api/shows/{show_b['id']}/slides").status_code == 404
    got = fam.post("/api/present/slides", json={
        "source": {"kind": "items", "item_ids": [seen, hidden, cut]}})
    assert got.status_code == 200
    assert {i["id"] for sl in got.json()["slides"] for i in sl["items"]} <= allowed
    # pins: its folder only (no project, no other folder)
    assert [(x["kind"], x["ref"]) for x in fam.get("/api/pins").json()] == [("folder", a)]
    # the rest of the new families aren't there for it, and writes are refused
    for path in ("/api/projects", f"/api/projects/{p['id']}", "/api/fields", "/api/feedback",
                 "/api/feedback/summary", "/api/colourbooks", "/api/cutouts/status",
                 f"/api/shares/{share['id']}/proofing", f"/api/items/{hidden}/cutouts",
                 f"/api/items/{seen}/fields", "/api/credits/summary"):
        assert fam.get(path).status_code == 404, path
    for method, path, body in (
            ("post", "/api/kinds/screenshots/correct", {"item_ids": [seen], "verdict": False}),
            ("post", f"/api/items/{seen}/cutout", {}),
            ("post", "/api/shows", {"name": "x", "source": {"kind": "folder", "id": a}}),
            ("post", "/api/present/remote", {"show_id": show_a["id"]}),
            ("patch", f"/api/items/{seen}/fields", {"values": {season["id"]: "AW27"}})):
        assert getattr(fam, method)(path, json=body).status_code in (403, 404), path
    # MCP for the reader: the same scope
    init(fam)
    found = mcp(fam, "search_items", {"auto_kind": "screenshots", "project": p["id"]})
    assert {i["id"] for i in found["items"]} <= allowed
    shows = mcp(fam, "list_shows", {})
    shows = shows if isinstance(shows, list) else [shows]
    assert [s["id"] for s in shows] == [show_a["id"]]


# ---------- MCP: every new tool, once ----------

NEW_TOOLS = {
    "list_kinds", "correct_kind",  # F1
    "list_shows", "create_show",  # F2
    "guest_feedback", "accept_guest_feedback",  # F3
    "set_credits", "item_credits", "credits_clearance", "print_check",  # F4
    "nearest_colour_code", "list_colour_books",  # F5
    "list_projects", "get_project", "set_field_values",  # F6
    "cut_out_subject",  # F7
}


def test_mcp_lists_every_new_tool_once(client):
    init(client)
    tools = rpc(client, "tools/list", id_=2)["result"]["tools"]
    names = [t["name"] for t in tools]
    assert len(names) == len(set(names))
    assert NEW_TOOLS <= set(names), NEW_TOOLS - set(names)
    search = next(t for t in tools if t["name"] == "search_items")["inputSchema"]["properties"]
    assert {"auto_kind", "rights_status", "needs_credit", "print_min", "print_max", "project",
            "fields", "color"} <= set(search)
    buckets = next(t for t in tools if t["name"] == "item_buckets")["inputSchema"]["properties"]
    assert {"auto_kind", "rights_status", "project", "fields"} <= set(buckets)


@pytest.mark.parametrize("value", ["drawings", "-screenshots", "drawings:unsure"])
def test_smart_folder_keeps_a_kind(client, value):
    sf = client.post("/api/smart-folders", json={"name": "K", "query": {"auto_kind": value}})
    assert sf.status_code in (200, 201), sf.text
    listed = {x["id"]: x for x in client.get("/api/smart-folders").json()}
    assert listed[sf.json()["id"]]["query"]["auto_kind"] == value
    assert get_settings().kinds
