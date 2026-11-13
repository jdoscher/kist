"""Milestones 2 and 3 together (the int-m23 integration): one library with
a bit of everything, wiped and rebuilt from its files; the folder-limited
reader against every new route family; ranked search with the M2 organise
and M3 filters; the migration chain.

Uses the fake ml service of test_organise_ai (embeddings by mean colour,
one face per image whose identity is its dominant channel).
"""

import json

import sqlalchemy as sa
from conftest import png_bytes
from test_organise_ai import faces_on, ml  # noqa: F401  (fixtures)
from test_tokens import as_token, make_token, mcp_result

from kist import cli
from kist.config import get_settings
from kist.db import SessionLocal
from kist.models import Face, ItemColor, ItemEmbedding, ItemText
from kist.services import collections, faces


def solid(rgb):
    return png_bytes(color=rgb, size=(320, 240))


def drain() -> int:
    from test_ai import drain as _drain

    return _drain()


def ids(page) -> list[str]:
    return [i["id"] for i in page["items"]]


def _counts() -> dict:
    with SessionLocal() as s:
        return {
            name: s.execute(sa.select(sa.func.count()).select_from(model)).scalar_one()
            for name, model in (("item_colors", ItemColor), ("embeddings", ItemEmbedding),
                                ("texts", ItemText), ("faces", Face))
        }


ITEM_KEYS = ("filename", "tags", "implied_tags", "caption", "flag", "label", "stack_id",
             "stack_count", "folder_ids", "rating", "notes", "source_url", "state",
             # Milestones 4 and 5 (int-all)
             "provenance", "source_status", "source_wayback_url", "media", "taken_at", "place",
             "kind", "preview_status", "width", "height", "duration_s")
# files M4/M5 keep beside each item (none may vanish in a rebuild)
SIDE_FILES = ("original.xmp", "page/text.txt", "page/snapshot.html", "scrub.webp",
              "scrub.json", "waveform.json", "bundle.json")


def snapshot(client, items: dict[str, str]) -> dict:
    """Everything the three branches added, as the API and DB show it."""
    by_id = {i["id"]: i for i in client.get(
        "/api/items", params={"ids": ",".join(items.values())}).json()["items"]}
    out: dict = {"items": {k: {f: by_id[v][f] for f in ITEM_KEYS} for k, v in items.items()}}
    out["vocabulary"] = client.get("/api/vocabulary").json()
    out["people"] = [(p["id"], p["name"], p["face_count"])
                     for p in client.get("/api/people").json()]
    out["boards"] = [client.get(f"/api/boards/{b['id']}").json()
                     for b in client.get("/api/boards").json()]
    out["used_in"] = [b["id"] for b in client.get(f"/api/items/{items['r0']}/boards").json()]
    out["annotations"] = client.get(f"/api/items/{items['b0']}/annotations").json()
    out["templates"] = client.get("/api/templates").json()
    out["palette"] = client.get(f"/api/items/{items['r0']}/palette").json()
    out["collections"] = client.get("/api/collections/suggested").json()
    out["counts"] = _counts()
    # M4: share links, page snapshot text; M5: what's beside each item
    out["shares"] = [{k: s[k] for k in ("id", "slug", "target_kind", "target_id", "title")}
                     for s in client.get("/api/shares").json()]
    out["snapshot_text"] = {k: client.get(f"/api/items/{v}/snapshot/text").text
                            for k, v in items.items() if k in ("g0",)}
    items_dir = get_settings().items_dir
    out["side_files"] = {k: sorted(f for f in SIDE_FILES if (items_dir / v / f).is_file())
                         for k, v in items.items()}
    out["timeline"] = client.get("/api/timeline", params={"group": "year"}).json()
    searches = {
        "implied": {"tags": "shoes"},
        "alias": {"tags": "bootz"},
        "namespace": {"tags": "brand:*"},
        "annotation_q": {"q": "sector"},
        "caption_q": {"q": "lighthouse"},
        "flag": {"flag": "pick"},
        "label": {"label": "red"},
        "collapse": {"stacks": "collapse"},
        "colour": {"color": "#dc1414", "color_tol": 40},
        "colour_collapse": {"color": "#dc1414", "color_tol": 40, "stacks": "collapse"},
        "semantic_flag": {"semantic": "red", "flag": "pick"},
        "credit_tag": {"tags": "credit:jay"},
        # M4
        "page_q": {"q": "zeppelin"},
        "ai_generated": {"ai_generated": "true"},
        "license": {"license": "cc0"},
        "gone": {"source_status": "gone"},
        # M5
        "audio": {"type": "audio"},
        "bundle": {"type": "bundle"},
        "has_audio": {"has_audio": "true"},
        "taken": {"order": "-taken", "type": "video"},
        "semantic_audio_off": {"semantic": "red", "type": "audio"},
    }
    out["searches"] = {k: sorted(ids(client.get("/api/items", params=p).json()))
                       for k, p in searches.items()}
    return out


def test_rebuild_and_verify_cover_milestones_2_to_5(client, upload, faces_on, clean,  # noqa: F811
                                                     capsys, tmp_path):
    folder = client.post("/api/folders", json={"name": "Shoots"}).json()
    fids = json.dumps([folder["id"]])
    items = {}
    for i in range(3):
        items[f"r{i}"] = upload(f"r{i}.png", data=solid((200 + i * 10, 10, 10)), tags="Boots",
                                folder_ids=fids)["item"]["id"]
    for i in range(2):
        items[f"b{i}"] = upload(f"b{i}.png", data=solid((10, 10, 200 + i * 10)),
                                tags="brand:rolex", folder_ids=fids)["item"]["id"]
    for i in range(4):
        items[f"g{i}"] = upload(f"g{i}.png", data=solid((10, 200 + i * 10, 10)))["item"]["id"]
    drain()

    # M2 organise: vocabulary, stack, faces + a named person, tag feedback, collections
    for path, body in (("aliases", {"alias": "Bootz", "tag": "Boots"}),
                       ("implies", {"tag": "Boots", "implied": "Shoes"})):
        assert client.post(f"/api/vocabulary/{path}", json=body).status_code in (200, 201)
    stack = client.post("/api/stacks", json={"item_ids": [items["r0"], items["r1"]],
                                             "cover": items["r1"]})
    assert stack.status_code in (200, 201), stack.text
    with SessionLocal() as s:
        assert faces.cluster_unassigned(s) >= 2
    red = max(client.get("/api/people").json(), key=lambda p: p["face_count"])
    client.patch(f"/api/people/{red['id']}", json={"name": "Ada"})
    assert client.post(f"/api/items/{items['g0']}/tag-suggestions/feedback",
                       json={"tag": "Boots", "accepted": False}).status_code == 200
    with SessionLocal() as s:
        collections.compute(s)
    assert collections.path().is_file()

    # M2 search: an edited caption (a caption event + sidecar), item_colors via trigger
    res = client.patch(f"/api/items/{items['g1']}", json={"caption": "A lighthouse at dusk"})
    assert res.status_code == 200, res.text

    # M3: board, annotation, template (credit becomes a tag), flags and labels
    board = client.post("/api/boards", json={"name": "Mood",
                                             "item_ids": [items["r0"], items["b0"]]})
    assert board.status_code == 201, board.text
    res = client.post(f"/api/items/{items['b0']}/annotations",
                      json={"kind": "box", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2,
                            "text": "sector dial"})
    assert res.status_code == 201, res.text
    tpl = client.post("/api/templates", json={"name": "Credit Jay", "fields": {
        "tags_add": ["client:acme"], "credit": "Jay", "label": "blue"}})
    assert tpl.status_code == 201, tpl.text
    res = client.post(f"/api/templates/{tpl.json()['id']}/apply",
                      json={"item_ids": [items["g2"]]})
    assert res.status_code == 200, res.text
    res = client.patch(f"/api/items/{items['r2']}", json={"flag": "pick", "label": "red"})
    assert res.status_code == 200, res.text

    # M4: provenance (+ XMP sidecar), a page snapshot, a share, a link-rot result
    res = client.patch(f"/api/items/{items['g0']}", json={"provenance": {
        "ai_generated": True, "license": "CC0", "creator": "Ana"}})
    assert res.status_code == 200, res.text
    res = client.post(f"/api/items/{items['g0']}/snapshot",
                      json={"html": "<p>The zeppelin over the harbour</p>",
                            "page_url": "https://news.test/z", "title": "Z"})
    assert res.status_code == 200, res.text
    share = client.post("/api/shares", json={"target_kind": "folder", "target_id": folder["id"],
                                             "title": "Shoots", "password": "pw pw"})
    assert share.status_code == 201, share.text
    gone = upload("gone.png", data=solid((90, 90, 90)), source_url="https://gone.test/x")
    items["gone"] = gone["item"]["id"]
    from kist.services import linkrot

    with SessionLocal() as s:
        linkrot.record(s, items["gone"], "gone", "https://web.archive.org/web/2020/x")
    # M5: a video (media block, scrub sidecar, a chosen cover), audio
    # (waveform), a bundle (manifest)
    import media_fixtures as F

    if F.HAVE_FFMPEG:
        items["vid"] = upload("v.mp4", F.video(tmp_path / "v.mp4").read_bytes(),
                              "application/octet-stream")["item"]["id"]
        res = client.post(f"/api/items/{items['vid']}/cover", json={"t": 2})
        assert res.status_code == 200, res.text
        items["song"] = upload("song.mp3", F.audio(tmp_path / "s.mp3").read_bytes(),
                               "application/octet-stream")["item"]["id"]
    items["kit"] = upload("kit.zip", F.zip_with(tmp_path / "kit.zip", {
        "kit/albedo.png": F.png((120, 100, 80), (64, 64)), "kit/LICENSE.txt": b"CC0"}
    ).read_bytes(), "application/zip")["item"]["id"]
    drain()

    before = snapshot(client, items)
    # the snapshot shows every part really happened
    assert before["searches"]["implied"] == sorted(items[k] for k in ("r0", "r1", "r2"))
    assert before["searches"]["alias"] == before["searches"]["implied"]
    assert before["searches"]["namespace"] == sorted([items["b0"], items["b1"]])
    assert before["searches"]["annotation_q"] == [items["b0"]]
    assert before["searches"]["caption_q"] == [items["g1"]]
    assert before["searches"]["flag"] == [items["r2"]]
    assert before["searches"]["semantic_flag"] == [items["r2"]]
    assert items["r0"] not in before["searches"]["collapse"]
    assert items["r1"] in before["searches"]["collapse"]
    assert len(before["searches"]["colour_collapse"]) == len(before["searches"]["colour"]) - 1
    assert before["searches"]["credit_tag"] == [items["g2"]]
    assert ("Ada" in [p[1] for p in before["people"]])
    assert before["items"]["r0"]["implied_tags"] == ["Shoes"]
    assert before["items"]["g1"]["caption"] == "A lighthouse at dusk"
    assert before["items"]["g2"]["label"] == "blue"
    assert before["used_in"] == [board.json()["id"]]
    assert all(before["counts"][k] > 0 for k in before["counts"])
    assert before["searches"]["page_q"] == [items["g0"]]
    assert before["searches"]["ai_generated"] == before["searches"]["license"] == [items["g0"]]
    assert before["searches"]["gone"] == [items["gone"]]
    assert before["searches"]["bundle"] == [items["kit"]]
    assert before["items"]["gone"]["source_wayback_url"].startswith("https://web.archive")
    assert "zeppelin" in before["snapshot_text"]["g0"]
    assert before["shares"][0]["slug"] and len(before["shares"]) == 1
    assert before["side_files"]["g0"] == ["original.xmp", "page/snapshot.html", "page/text.txt"]
    assert before["side_files"]["kit"] == ["bundle.json"]
    if "vid" in items:
        assert before["items"]["vid"]["media"]["cover_time"] == 2.0
        assert before["side_files"]["vid"] == ["scrub.json", "scrub.webp"]
        assert before["side_files"]["song"] == ["waveform.json"]
        assert before["searches"]["audio"] == [items["song"]]
        assert before["searches"]["has_audio"] == sorted([items["vid"], items["song"]])
        assert before["searches"]["semantic_audio_off"] == [items["song"]]
    capsys.readouterr()
    assert cli.verify() == 0, (capsys.readouterr().out, items)

    # wipe and rebuild from the files alone: nothing may be recomputed
    faces_on.down = True
    assert cli.rebuild_index(wipe=True) == 0
    after = snapshot(client, items)
    for key in before:
        assert after[key] == before[key], key
    capsys.readouterr()
    assert cli.verify() == 0, (capsys.readouterr().out, items)
    feedback = json.loads((get_settings().items_dir / items["g0"] / "ai" /
                           "tag-feedback.json").read_text())
    assert "Boots" in json.dumps(feedback)


def test_folder_limited_reader_across_milestone_2_and_3_routes(client, upload, faces_on):  # noqa: F811
    a = client.post("/api/folders", json={"name": "A"}).json()
    b = client.post("/api/folders", json={"name": "B"}).json()
    seen = [upload(f"s{i}.png", data=solid((220 + i, 20, 20)), tags="Boots",
                   folder_ids=json.dumps([a["id"]]))["item"]["id"] for i in range(2)]
    hidden = upload("h.png", data=solid((225, 25, 25)), tags="Boots,brand:x",
                    folder_ids=json.dumps([b["id"]]))["item"]["id"]
    drain()
    with SessionLocal() as s:
        faces.cluster_unassigned(s)
    client.post("/api/vocabulary/implies", json={"tag": "Boots", "implied": "Shoes"})
    for item_id in (*seen, hidden):
        client.patch(f"/api/items/{item_id}", json={"flag": "pick", "label": "red",
                                                    "caption": "secret lighthouse"})
        client.post(f"/api/items/{item_id}/annotations",
                    json={"kind": "point", "x": 0.5, "y": 0.5, "text": "secret dial"})
    # a stack across the boundary, covered by the hidden item
    mixed = client.post("/api/stacks", json={"item_ids": [hidden, seen[0]],
                                             "cover": hidden}).json()
    board_ok = client.post("/api/boards", json={"name": "ok", "item_ids": [seen[0]]}).json()
    board_mixed = client.post("/api/boards", json={"name": "mixed",
                                                   "item_ids": [seen[0], hidden]}).json()
    person = client.get(f"/api/items/{hidden}/faces").json()[0]["person_id"]

    fam = as_token(client, make_token(client, "Fam", ["read"], folder_ids=[a["id"]])["token"])
    allowed = set(seen)

    # every search mode, with the organise and creative filters
    for params in ({"tags": "shoes"}, {"tags": "brand:*"}, {"q": "secret"}, {"q": "dial"},
                   {"flag": "pick"}, {"label": "red"}, {"stacks": "collapse"},
                   {"semantic": "red"}, {"semantic": "red", "q": "secret"},
                   {"color": "#dc1414", "color_tol": 100}, {"text": "hello"},
                   {"similar": seen[0]}, {"person": person},
                   {"color": "#dc1414", "color_tol": 100, "stacks": "collapse"},
                   {"semantic": "red", "flag": "pick", "label": "red", "tags": "shoes"},
                   {"ids": f"{hidden},{seen[0]}"}):
        res = fam.get("/api/items", params=params)
        assert res.status_code == 200, (params, res.text)
        assert set(ids(res.json())) <= allowed, params
    page = fam.get("/api/items", params={"semantic": "red", "stacks": "collapse"}).json()
    assert seen[0] in ids(page)  # the stack's best visible member stands in for it
    res = fam.post("/api/search/image", params={"flag": "pick"},
                   files={"file": ("q.png", solid((220, 20, 20)), "image/png")})
    assert res.status_code == 200 and set(ids(res.json())) <= allowed

    # item sub-resources of a hidden item: all 404
    for sub in ("", "/palette", "/faces", "/annotations", "/versions", "/boards", "/history",
                "/links", "/thumb", "/file"):
        assert fam.get(f"/api/items/{hidden}{sub}").status_code == 404, sub
    for sub in ("/palette", "/faces", "/annotations", "/versions", "/boards"):
        assert fam.get(f"/api/items/{seen[0]}{sub}").status_code == 200, sub
    # stacks, people, boards: only what it sees
    got = fam.get(f"/api/stacks/{mixed['id']}").json()
    assert [i["id"] for i in got["items"]] == [seen[0]]
    assert all(p["item_count"] <= 2 for p in fam.get("/api/people").json())
    assert [x["id"] for x in fam.get("/api/boards").json()] == [board_ok["id"]]
    assert fam.get(f"/api/boards/{board_mixed['id']}").status_code == 404
    # owner-only families answer 404 (they would reveal the whole library)
    for path in ("/api/vocabulary", "/api/tags/cleanup", "/api/duplicates/groups",
                 "/api/collections/suggested", "/api/stacks/suggestions",
                 f"/api/items/{seen[0]}/tag-suggestions", "/api/palette?q=s",
                 "/api/triage/next", "/api/templates", "/api/smart-folders"):
        assert fam.get(path).status_code == 404, path
    # and over MCP
    for args in ({"tags": ["shoes"]}, {"query": "secret"}, {"flag": "pick"},
                 {"collapse_stacks": True}, {"semantic": "red"}, {"person": person},
                 {"color": "#dc1414", "color_tol": 100, "label": "red"}):
        res = mcp_result(fam, "search_items", args)
        assert not res.get("isError"), (args, res)
        found = {i["id"] for i in json.loads(res["content"][0]["text"])["items"]}
        assert found <= allowed, args
    for tool, args in (("item_palette", {"id": hidden}), ("list_annotations", {"item_id": hidden}),
                       ("get_board", {"id": board_mixed["id"]})):
        res = mcp_result(fam, tool, args)
        assert res["isError"] and "not found" in res["content"][0]["text"], tool
    for tool in ("get_vocabulary", "duplicate_groups", "suggested_collections", "tag_cleanup"):
        res = mcp_result(fam, tool, {})
        assert res["isError"] and "can't do that" in res["content"][0]["text"], tool


def test_ranked_search_collapses_stacks_after_ranking(client, upload, ml):  # noqa: F811
    near = upload("near.png", data=solid((220, 20, 20)))["item"]["id"]
    far = upload("far.png", data=solid((120, 60, 60)))["item"]["id"]
    alone = upload("alone.png", data=solid((200, 30, 30)))["item"]["id"]
    drain()
    client.post("/api/stacks", json={"item_ids": [far, near], "cover": far})
    plain = ids(client.get("/api/items", params={"stacks": "collapse"}).json())
    assert far in plain and near not in plain  # plain search: the cover
    page = client.get("/api/items", params={"semantic": "red", "stacks": "collapse"}).json()
    got = ids(page)
    # ranked: the best-ranked member stands for the stack, once
    assert near in got and far not in got and alone in got
    assert page["total"] == len(got) == 2
    row = next(i for i in page["items"] if i["id"] == near)
    assert row["stack_count"] == 2 and row["match"]["kind"] == "meaning"
    # paging stays consistent with the collapse
    first = client.get("/api/items", params={"semantic": "red", "stacks": "collapse",
                                             "limit": 1}).json()
    second = client.get("/api/items", params={"semantic": "red", "stacks": "collapse",
                                              "limit": 1, "cursor": first["next_cursor"]}).json()
    assert ids(first) + ids(second) == got and second["next_cursor"] is None
    # the trash isn't collapsed
    client.delete(f"/api/items/{near}")
    client.delete(f"/api/items/{far}")
    trash = client.get("/api/items", params={"semantic": "red", "stacks": "collapse",
                                             "state": "trashed"}).json()
    assert set(ids(trash)) == {near, far}


def test_new_version_becomes_the_stack_cover(client, upload, clean):
    old = upload("v1.png", data=solid((200, 10, 10)))["item"]["id"]
    other = upload("raw.png", data=solid((10, 200, 10)))["item"]["id"]
    stack = client.post("/api/stacks", json={"item_ids": [old, other], "cover": old}).json()
    res = client.post(f"/api/items/{old}/versions",
                      files={"file": ("v2.png", solid((210, 12, 12)), "image/png")})
    assert res.status_code in (200, 201), res.text
    new = res.json()["item"]["id"]
    got = client.get(f"/api/stacks/{stack['id']}").json()
    assert got["cover_item_id"] == new and {i["id"] for i in got["items"]} == {old, other, new}
    assert ids(client.get("/api/items", params={"stacks": "collapse"}).json()) == [new]
    # a chain outside any stack becomes one, the new version its cover
    lone = upload("lone.png", data=solid((10, 10, 200)))["item"]["id"]
    res = client.post(f"/api/items/{lone}/versions",
                      files={"file": ("lone2.png", solid((12, 12, 210)), "image/png")})
    v2 = res.json()["item"]
    assert v2["stack_id"]
    got = client.get(f"/api/stacks/{v2['stack_id']}").json()
    assert got["cover_item_id"] == v2["id"] and {i["id"] for i in got["items"]} == {lone, v2["id"]}
    # a third version joins that stack as the new cover
    v3 = client.post(f"/api/items/{lone}/versions",
                     files={"file": ("lone3.png", solid((14, 14, 220)), "image/png")})
    v3 = v3.json()["item"]
    got = client.get(f"/api/stacks/{v2['stack_id']}").json()
    assert got["cover_item_id"] == v3["id"] and len(got["items"]) == 3
    assert v3["id"] in ids(client.get("/api/items", params={"stacks": "collapse"}).json())
    assert lone not in ids(client.get("/api/items", params={"stacks": "collapse"}).json())


def test_palette_lists_named_people_when_faces_are_on(client, upload, faces_on):  # noqa: F811
    upload("r.png", data=solid((220, 20, 20)))
    upload("r2.png", data=solid((225, 20, 20)))
    drain()
    with SessionLocal() as s:
        faces.cluster_unassigned(s)
    pid = client.get("/api/people").json()[0]["id"]
    client.patch(f"/api/people/{pid}", json={"name": "Ada Lovelace"})
    rows = client.get("/api/palette", params={"q": "ada"}).json()
    assert {"kind": "person", "id": pid, "title": "Ada Lovelace",
            "subtitle": "Person · 2 items"} in rows


def test_mcp_tool_list_is_one_coherent_set(client):
    from test_mcp import init, rpc

    init(client)
    tools = rpc(client, "tools/list", id_=2)["result"]["tools"]
    names = [t["name"] for t in tools]
    assert len(names) == len(set(names))
    assert {"search_items", "search_by_image", "item_palette", "get_vocabulary",
            "suggest_tags", "list_people", "stack_items", "list_boards", "create_board",
            "annotate", "update_item"} <= set(names)
    params = next(t for t in tools if t["name"] == "search_items")["inputSchema"]["properties"]
    assert {"semantic", "similar_to", "color", "color_tol", "text", "has_text",
            "collapse_stacks", "person", "flag", "label", "tags", "tags_any"} <= set(params)
    update = next(t for t in tools if t["name"] == "update_item")["inputSchema"]["properties"]
    assert {"caption", "flag", "label"} <= set(update)


def test_migrations_are_one_line():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_heads() == ["0951"]  # F1 0901 -> F3 0921 -> F6 0951
    chain = [r.revision for r in script.walk_revisions("base", "heads")]
    assert chain[:15] == ["0951", "0921", "0901", "0803", "0802", "0801", "0701", "0603", "0602",
                          "0601", "0501", "0401", "0301", "0201", "0101"]
