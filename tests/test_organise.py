"""Milestone 2 organise: vocabulary, tag cleanup and merge, stacks,
duplicates, and what each token scope may reach."""

import io
import json
from datetime import datetime

import pytest
from conftest import png_bytes
from PIL import Image
from test_tokens import FORBIDDEN, as_token, make_token, mcp_result

from kist import cli
from kist.config import get_settings


def tags_of(client, item_id):
    return client.get(f"/api/items/{item_id}").json()["tags"]


def ids(page):
    return {i["id"] for i in page["items"]}


def batch_of(client, item_id, kind="tags"):
    hist = client.get(f"/api/items/{item_id}/history").json()
    return next(e["batch_id"] for e in hist if e["kind"] == kind)


# ---------- vocabulary ----------


def test_alias_rewrites_items_as_one_undoable_batch(client, upload):
    a = upload("a.png", tags="Fashion")["item"]
    b = upload("b.png", tags="Fashion,Clothing")["item"]
    c = upload("c.png", tags="Clothing")["item"]
    res = client.post("/api/vocabulary/aliases", json={"alias": "Fashion", "tag": "Clothing"})
    assert res.status_code == 201, res.text
    assert res.json() == {"alias": "Fashion", "tag": "Clothing", "items": 2}
    assert tags_of(client, a["id"]) == ["Clothing"]
    assert tags_of(client, b["id"]) == ["Clothing"]
    assert tags_of(client, c["id"]) == ["Clothing"]
    # recorded as tag events, both in one batch
    batch = batch_of(client, a["id"])
    assert batch == batch_of(client, b["id"])
    ev = client.get(f"/api/items/{a['id']}/history").json()[0]
    assert ev["kind"] == "tags" and ev["before"]["tags"] == ["Fashion"]
    assert ev["after"]["tags"] == ["Clothing"]
    # the old tag is gone, the rule is on disk
    assert "Fashion" not in {t["name"] for t in client.get("/api/tags").json()}
    vocab = json.loads((get_settings().library_dir / "vocabulary.json").read_text())
    assert vocab["aliases"] == {"Fashion": "Clothing"}
    # new uses are rewritten on the way in: REST, upload and MCP
    d = upload("d.png", tags="fashion")["item"]
    assert d["tags"] == ["Clothing"]
    e = upload("e.png")["item"]
    assert client.patch(f"/api/items/{e['id']}/tags",
                        json={"add": ["FASHION"]}).json()["tags"] == ["Clothing"]
    f = upload("f.png")["item"]
    out = mcp_result(client, "set_tags", {"id": f["id"], "add": ["fashion"]})
    assert json.loads(out["content"][0]["text"])["tags"] == ["Clothing"]
    vocab = mcp_result(client, "get_vocabulary")
    assert json.loads(vocab["content"][0]["text"])["aliases"] == [
        {"alias": "Fashion", "tag": "Clothing"}]
    # undo the alias batch: the items carry Fashion again
    undo = client.post(f"/api/batches/{batch}/undo").json()
    assert undo["undone"] == 3 and not undo["skipped"]  # 2 items + the alias
    assert tags_of(client, a["id"]) == ["Fashion"]
    assert tags_of(client, b["id"]) == ["Clothing", "Fashion"]
    assert client.get("/api/vocabulary").json()["aliases"] == []


@pytest.mark.parametrize("body, message", [
    ({"alias": "Shoes", "tag": "shoes"}, "itself"),
    ({"alias": "Kicks", "tag": "Sneakers"}, "is itself an alias"),
    ({"alias": "Trainers", "tag": "Boots"}, "already an alias"),
    ({"alias": "Footwear", "tag": "Boots"}, "other aliases point"),
])
def test_alias_rules_are_checked(client, body, message):
    assert client.post("/api/vocabulary/aliases",
                       json={"alias": "Sneakers", "tag": "Footwear"}).status_code == 201
    assert client.post("/api/vocabulary/aliases",
                       json={"alias": "Trainers", "tag": "Footwear"}).status_code == 201
    res = client.post("/api/vocabulary/aliases", json=body)
    assert res.status_code == 422 and message in res.text, res.text


def test_implied_tags_are_virtual_and_loops_are_refused(client, upload):
    boots = upload("boots.png", tags="Boots")["item"]
    shoes = upload("shoes.png", tags="Shoes")["item"]
    other = upload("hat.png", tags="Hat")["item"]
    for tag, implied in (("Boots", "Shoes"), ("Shoes", "Footwear")):
        assert client.post("/api/vocabulary/implies",
                           json={"tag": tag, "implied": implied}).status_code == 201
    # a loop, directly or through another tag, is refused
    for tag, implied in (("Shoes", "Boots"), ("Footwear", "Boots"), ("Boots", "boots")):
        res = client.post("/api/vocabulary/implies", json={"tag": tag, "implied": implied})
        assert res.status_code == 422, (tag, implied, res.text)
    assert ids(client.get("/api/items", params={"tags": "footwear"}).json()) == {
        boots["id"], shoes["id"]}
    assert ids(client.get("/api/items", params={"tags": "Shoes"}).json()) == {
        boots["id"], shoes["id"]}
    assert ids(client.get("/api/items", params={"tags": "Boots"}).json()) == {boots["id"]}
    assert ids(client.get("/api/items", params={"tags_any": "footwear,hat"}).json()) == {
        boots["id"], shoes["id"], other["id"]}
    assert client.get("/api/items", params={"tags": "Footwear"}).json()["total"] == 2
    # implied tags show on the item but are never stored on it
    got = client.get(f"/api/items/{boots['id']}").json()
    assert got["tags"] == ["Boots"] and got["implied_tags"] == ["Footwear", "Shoes"]
    meta = json.loads((get_settings().items_dir / boots["id"] / "meta.json").read_text())
    assert meta["tags"] == ["Boots"]
    # removing the rule cleans everything up
    assert client.delete("/api/vocabulary/implies",
                         params={"tag": "Shoes", "implied": "Footwear"}).json()["removed"]
    assert client.get("/api/items", params={"tags": "footwear"}).json()["items"] == []
    assert client.get(f"/api/items/{boots['id']}").json()["implied_tags"] == ["Shoes"]
    # an alias works in searches too
    client.post("/api/vocabulary/aliases", json={"alias": "Booties", "tag": "Boots"})
    assert ids(client.get("/api/items", params={"tags": "booties"}).json()) == {boots["id"]}
    vocab = client.get("/api/vocabulary").json()
    assert vocab["implies"] == [{"tag": "Boots", "implied": "Shoes"}]


def test_namespaces(client, upload):
    a = upload("a.png", tags="brand:Rolex,Watch")["item"]
    b = upload("b.png", tags="brand:Omega")["item"]
    upload("c.png", tags="place:Paris")
    tags = client.get("/api/tags", params={"namespace": "brand"}).json()
    assert {(t["name"], t["namespace"], t["value"]) for t in tags} == {
        ("brand:Rolex", "brand", "Rolex"), ("brand:Omega", "brand", "Omega")}
    plain = client.get("/api/tags", params={"namespace": "none"}).json()
    assert [t["name"] for t in plain] == ["Watch"]
    assert ids(client.get("/api/items", params={"tags": "brand:*"}).json()) == {
        a["id"], b["id"]}
    vocab = client.get("/api/vocabulary").json()
    assert vocab["namespaces"] == [{"namespace": "brand", "tags": 2},
                                   {"namespace": "place", "tags": 1}]


# ---------- cleanup, merge, rename ----------


def test_cleanup_suggestions(client, upload):
    upload("1.png", tags="Restaurant,Stationery,dark mode")
    upload("2.png", tags="Restaurant,Stationery,demo1")
    upload("3.png", tags="Restaurants,Stationery,demo2")
    upload("4.png", tags="Staitionery,dark-mode")
    client.post("/api/smart-folders", json={"name": "Old", "query": {"tags": ["Gone"]}})
    found = client.get("/api/tags/cleanup").json()
    by_kind = {}
    for s in found:
        by_kind.setdefault(s["kind"], []).append(s)
    assert set(by_kind) >= {"clash", "near", "one_off", "smart_folder"}
    clash = by_kind["clash"][0]
    assert set(clash["tags"]) == {"dark mode", "dark-mode"}
    nears = [set(s["tags"]) for s in by_kind["near"]]
    assert {"Restaurant", "Restaurants"} in nears
    assert {"Stationery", "Staitionery"} in nears
    assert not any("demo1" in n for n in nears)  # numbered on purpose
    stat = next(s for s in by_kind["near"] if "Stationery" in s["tags"])
    assert stat["proposal"] == {"action": "merge", "into": "Stationery", "from": ["Staitionery"]}
    assert stat["counts"] == {"Stationery": 3, "Staitionery": 1}
    assert {s["tags"][0] for s in by_kind["one_off"]} >= {"Restaurants", "Staitionery"}
    assert by_kind["smart_folder"][0]["proposal"]["missing_tags"] == ["Gone"]
    # unused since a date in the future: everything
    later = client.get("/api/tags/cleanup", params={"unused_since": "2999-01-01T00:00:00Z"})
    assert len([s for s in later.json() if s["kind"] == "unused"]) == 8
    assert not [s for s in found if s["kind"] == "unused"]  # all used just now


def test_cleanup_on_real_vocabulary(client, upload):
    """Jay's real tags: Interiors/Exteriors and Shoes/Skies are different
    words, not misspellings; credit:/license:/source: tags on one item are
    normal provenance, not one-offs; format: values aren't compared across
    the prefix."""
    upload("1.png", tags="Interiors,Shoes,format:reel,credit:Unsplash")
    upload("2.png", tags="Interiors,Exteriors,Skies,format:gif,license:CC-BY-4.0")
    upload("3.png", tags="Watches,Accessories,source:instagram")
    upload("4.png", tags="Watch,Accesories")
    found = client.get("/api/tags/cleanup").json()
    nears = [set(s["tags"]) for s in found if s["kind"] == "near"]
    assert {"Interiors", "Exteriors"} not in nears
    assert {"Shoes", "Skies"} not in nears
    assert {"Watches", "Watch"} in nears and {"Accessories", "Accesories"} in nears
    assert not any(n & {"format:reel", "format:gif"} for n in nears)
    one_offs = {s["tags"][0] for s in found if s["kind"] == "one_off"}
    assert not one_offs & {"credit:Unsplash", "license:CC-BY-4.0", "source:instagram"}
    assert {"Exteriors", "Skies", "format:reel"} <= one_offs
    later = client.get("/api/tags/cleanup", params={"unused_since": "2999-01-01T00:00:00Z"}).json()
    unused = {s["tags"][0] for s in later if s["kind"] == "unused"}
    assert "Interiors" in unused and "credit:Unsplash" not in unused


def test_merge_is_one_batch_with_a_dry_run(client, upload):
    a = upload("a.png", tags="Restaurants")["item"]
    b = upload("b.png", tags="Restaurant,Food")["item"]
    c = upload("c.png", tags="restaurante")["item"]
    smart = client.post("/api/smart-folders",
                        json={"name": "Eat", "query": {"tags_any": ["Restaurants", "Food"]}}).json()
    body = {"from": ["Restaurants", "restaurante"], "into": "Restaurant"}
    dry = client.post("/api/tags/merge", params={"dry_run": 1}, json=body).json()
    assert dry["items"] == 2 and dry["dry_run"]
    assert dry["smart_folders"] == [{"id": smart["id"], "name": "Eat", "tags": ["Restaurants"]}]
    assert tags_of(client, a["id"]) == ["Restaurants"]  # nothing changed
    res = client.post("/api/tags/merge", json={**body, "keep_alias": True}).json()
    assert res["items"] == 2
    assert tags_of(client, a["id"]) == ["Restaurant"]
    assert tags_of(client, c["id"]) == ["Restaurant"]
    assert tags_of(client, b["id"]) == ["Food", "Restaurant"]
    assert client.get("/api/smart-folders").json()[0]["query"]["tags_any"] == [
        "Restaurant", "Food"]
    vocab = client.get("/api/vocabulary").json()
    assert {a["alias"] for a in vocab["aliases"]} == {"Restaurants", "restaurante"}
    # one batch: undo puts every item back
    # one batch: undo puts every item back, and the aliases and smart folder
    batches = client.get("/api/batches").json()
    assert batches[0]["count"] == 3 and batches[0]["kinds"] == ["tags", "vocab_rules"]
    undo = client.post(f"/api/batches/{batches[0]['batch_id']}/undo").json()
    assert undo["undone"] == 3 and not undo["skipped"]
    assert tags_of(client, a["id"]) == ["Restaurants"]
    assert tags_of(client, c["id"]) == ["restaurante"]
    assert client.get("/api/vocabulary").json()["aliases"] == []
    assert client.get("/api/smart-folders").json()[0]["query"]["tags_any"] == [
        "Restaurants", "Food"]
    # merging into itself is refused
    assert client.post("/api/tags/merge",
                       json={"from": ["Food"], "into": "food"}).status_code == 422


def _merge(client, batch, from_, into):
    res = client.post("/api/tags/merge", json={"from": from_, "into": into, "keep_alias": True},
                      headers={"X-Kist-Batch": batch})
    assert res.status_code == 200, res.text
    return res.json()


def _aliases(client):
    return {a["alias"]: a["tag"] for a in client.get("/api/vocabulary").json()["aliases"]}


def test_merge_undo_takes_the_alias_back(client, upload):
    """Recent changes' undo (the batch endpoint) reverses the merge's alias,
    not just the retag, so a later "Accesories" isn't silently rewritten."""
    a = upload("a.png", tags="Accesories")["item"]
    upload("b.png", tags="Accessories")
    _merge(client, "cleanup-1", ["Accesories"], "Accessories")
    assert _aliases(client) == {"Accesories": "Accessories"}
    assert "vocab_rules" in client.get("/api/batches").json()[0]["kinds"]
    undo = client.post("/api/batches/cleanup-1/undo").json()
    assert undo["undone"] == 2 and not undo["skipped"], undo
    assert tags_of(client, a["id"]) == ["Accesories"]
    assert _aliases(client) == {}
    vocab = json.loads((get_settings().library_dir / "vocabulary.json").read_text())
    assert vocab["aliases"] == {}
    # the old spelling is kept as typed again
    c = upload("c.png", tags="Accesories")["item"]
    assert c["tags"] == ["Accesories"]
    # the undo is a batch too: undoing it merges again, alias and all
    client.post(f"/api/batches/{undo['batch_id']}/undo")
    assert tags_of(client, a["id"]) == ["Accessories"]
    assert _aliases(client) == {"Accesories": "Accessories"}
    assert cli.verify() == 0


def test_merge_undo_through_mcp_takes_the_alias_back(client, upload):
    a = upload("a.png", tags="Interior")["item"]
    _merge(client, "cleanup-mcp", ["Interior"], "Interiors")
    out = mcp_result(client, "undo_batch", {"batch_id": "cleanup-mcp"})
    assert not out.get("isError"), out
    body = json.loads(out["content"][0]["text"])
    assert body["undone"] == 2 and not body["skipped"]
    assert tags_of(client, a["id"]) == ["Interior"]
    assert _aliases(client) == {}


def test_merge_undo_keeps_an_alias_changed_later(client, upload):
    """The same conflict rule as item undo: an alias someone changed after
    the merge is left alone, and the undo says why."""
    a = upload("a.png", tags="Bagz")["item"]
    _merge(client, "cleanup-2", ["Bagz"], "Bags")
    assert client.delete("/api/vocabulary/aliases", params={"alias": "Bagz"},
                         headers={"X-Kist-Batch": "later-1"}).json() == {"removed": True}
    assert client.post("/api/vocabulary/aliases", json={"alias": "Bagz", "tag": "Luggage"},
                       headers={"X-Kist-Batch": "later-2"}).status_code == 201
    undo = client.post("/api/batches/cleanup-2/undo").json()
    assert undo["undone"] == 1  # the item's tags
    assert [(s["target"], s["reason"]) for s in undo["skipped"]] == [
        ("vocabulary", "changed later: alias:bagz")]
    assert tags_of(client, a["id"]) == ["Bagz"]
    assert _aliases(client) == {"Bagz": "Luggage"}
    # the latest change itself still undoes
    assert client.post("/api/batches/later-2/undo").json()["undone"] == 1
    assert _aliases(client) == {}
    assert cli.verify() == 0


def test_merge_stays_undoable_after_rebuild_index(client, upload):
    a = upload("a.png", tags="Shoe")["item"]
    _merge(client, "cleanup-3", ["Shoe"], "Shoes")
    assert cli.verify() == 0
    from kist.db import SessionLocal
    from kist.models import VocabEvent

    assert cli.rebuild_index(wipe=True) == 0
    with SessionLocal() as s:  # the wipe emptied it; the file brought it back
        assert s.query(VocabEvent).count() == 1
    assert _aliases(client) == {"Shoe": "Shoes"}
    assert "vocab_rules" in {k for b in client.get("/api/batches").json() for k in b["kinds"]}
    undo = client.post("/api/batches/cleanup-3/undo").json()
    assert undo["undone"] == 2 and not undo["skipped"], undo
    assert tags_of(client, a["id"]) == ["Shoe"]
    assert _aliases(client) == {}
    assert cli.verify() == 0


def test_rename_and_case_change(client, upload):
    a = upload("a.png", tags="cpu")["item"]
    res = client.post("/api/tags/rename", json={"from": "cpu", "to": "CPU"}).json()
    assert res["items"] == 1
    assert tags_of(client, a["id"]) == ["CPU"]
    meta = json.loads((get_settings().items_dir / a["id"] / "meta.json").read_text())
    assert meta["tags"] == ["CPU"]
    client.post("/api/tags/rename", json={"from": "CPU", "to": "Processor"})
    assert tags_of(client, a["id"]) == ["Processor"]
    assert client.post("/api/tags/rename",
                       json={"from": "nope", "to": "x"}).status_code == 422
    assert cli.verify() == 0


# ---------- stacks ----------


def exif_jpeg(when: str, rgb=(90, 120, 30), make="Fujifilm", model="X-T5") -> bytes:
    im = Image.new("RGB", (320, 240), rgb)
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = make, model
    exif.get_ifd(0x8769)[0x9003] = when
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def test_stacks_collapse_events_undo_and_meta(client, upload):
    a = upload("a.png")["item"]
    b = upload("b.png")["item"]
    c = upload("c.png")["item"]
    lone = upload("lone.png")["item"]
    res = client.post("/api/stacks", json={"item_ids": [a["id"], b["id"], c["id"]],
                                           "cover": b["id"]})
    assert res.status_code == 201, res.text
    stack = res.json()
    assert stack["cover_item_id"] == b["id"] and stack["count"] == 3
    page = client.get("/api/items", params={"stacks": "collapse"}).json()
    assert ids(page) == {b["id"], lone["id"]} and page["total"] == 2
    cover = next(i for i in page["items"] if i["id"] == b["id"])
    assert cover["stack_id"] == stack["id"] and cover["stack_count"] == 3
    assert len(client.get("/api/items").json()["items"]) == 4  # not collapsed
    meta = json.loads((get_settings().items_dir / b["id"] / "meta.json").read_text())
    assert meta["stack"] == {"id": stack["id"], "cover": True}
    # the cover in the trash: the next member stands in for the stack
    client.delete(f"/api/items/{b['id']}")
    page = client.get("/api/items", params={"stacks": "collapse"}).json()
    assert ids(page) == {min(a["id"], c["id"]), lone["id"]}
    client.post(f"/api/items/{b['id']}/restore")
    # change the cover, then undo that
    res = client.patch(f"/api/stacks/{stack['id']}", json={"cover": c["id"]})
    assert res.json()["cover_item_id"] == c["id"]
    hist = client.get(f"/api/items/{c['id']}/history").json()
    assert hist[0]["kind"] == "stack" and hist[0]["after"]["cover"] is True
    client.post(f"/api/batches/{hist[0]['batch_id']}/undo")
    assert client.get(f"/api/stacks/{stack['id']}").json()["cover_item_id"] == b["id"]
    assert cli.verify() == 0
    # rebuild-index brings the stack back from meta.json
    assert cli.rebuild_index(wipe=True) == 0
    again = client.get(f"/api/stacks/{stack['id']}").json()
    assert again["cover_item_id"] == b["id"] and again["count"] == 3
    # unstack, then undo that: the stack is back with its cover
    assert client.delete(f"/api/stacks/{stack['id']}").json() == {"unstacked": 3}
    assert client.get(f"/api/items/{a['id']}").json()["stack_id"] is None
    last = client.get(f"/api/items/{a['id']}/history").json()[0]
    assert last["kind"] == "stack" and last["after"] == {"stack_id": None, "cover": False}
    out = client.post(f"/api/batches/{last['batch_id']}/undo").json()
    assert out["undone"] == 3, out
    back = client.get(f"/api/stacks/{stack['id']}").json()
    assert back["cover_item_id"] == b["id"] and back["count"] == 3
    assert cli.verify() == 0
    # a stacking undone straight away leaves nothing behind
    d, e = upload("d.png")["item"], upload("e.png")["item"]
    s2 = client.post("/api/stacks", json={"item_ids": [d["id"], e["id"]]}).json()
    batch = client.get(f"/api/items/{d['id']}/history").json()[0]["batch_id"]
    assert client.post(f"/api/batches/{batch}/undo").json()["undone"] == 2
    assert client.get(f"/api/stacks/{s2['id']}").status_code == 404
    assert cli.verify() == 0


def test_unstack_and_dissolve(client, upload):
    a, b, c = (upload(f"{n}.png")["item"] for n in "abc")
    s1 = client.post("/api/stacks", json={"item_ids": [a["id"], b["id"]]}).json()
    # moving b into a new stack leaves s1 with one member: it dissolves
    s2 = client.post("/api/stacks", json={"item_ids": [b["id"], c["id"]]}).json()
    assert client.get(f"/api/stacks/{s1['id']}").status_code == 404
    assert client.get(f"/api/items/{a['id']}").json()["stack_id"] is None
    assert client.delete(f"/api/stacks/{s2['id']}").json() == {"unstacked": 2}
    s3 = client.post("/api/stacks", json={"item_ids": [a["id"], b["id"], c["id"]]}).json()
    left = client.patch(f"/api/stacks/{s3['id']}", json={"remove": [a["id"]]}).json()
    assert left["cover_item_id"] in (b["id"], c["id"]) and left["count"] == 2
    gone = client.patch(f"/api/stacks/{s3['id']}", json={"remove": [b["id"]]}).json()
    assert gone == {"id": s3["id"], "dissolved": True}
    assert client.get(f"/api/items/{c['id']}").json()["stack_id"] is None
    assert client.post("/api/stacks", json={"item_ids": [a["id"], a["id"]]}).status_code == 422
    assert client.post("/api/stacks", json={"item_ids": [a["id"], b["id"]],
                                            "cover": c["id"]}).status_code == 422


def test_stack_suggestions(client, upload):
    jpg = upload("IMG_0001.jpg", data=exif_jpeg("2026:05:01 10:00:00"), mime="image/jpeg")
    raw = upload("IMG_0001.dng", data=exif_jpeg("2026:05:01 10:00:00", rgb=(91, 120, 30)),
                 mime="image/jpeg")
    # a reused name from another day is no pair
    upload("IMG_0002.jpg", data=exif_jpeg("2026:05:01 11:00:00", rgb=(1, 2, 3)), mime="image/jpeg")
    upload("IMG_0002.dng", data=exif_jpeg("2027:05:01 11:00:00", rgb=(1, 2, 4)), mime="image/jpeg")
    burst = [upload(f"B{i}.jpg", data=exif_jpeg(f"2026:06:01 12:00:0{i}", rgb=(10 * i, 0, 0)),
                    mime="image/jpeg")["item"] for i in range(3)]
    other_cam = upload("C.jpg", data=exif_jpeg("2026:06:01 12:00:01", make="Canon",
                                               model="R5", rgb=(5, 5, 99)), mime="image/jpeg")
    v1, v2 = upload("v1.png")["item"], upload("v2.png")["item"]
    client.post(f"/api/items/{v2['id']}/links", json={"dst_id": v1["id"], "kind": "version_of"})
    found = client.get("/api/stacks/suggestions").json()
    kinds = {s["kind"]: s for s in found}
    assert set(kinds) == {"raw_jpeg", "burst", "version"}, found
    assert set(kinds["raw_jpeg"]["item_ids"]) == {jpg["item"]["id"], raw["item"]["id"]}
    assert kinds["raw_jpeg"]["cover_item_id"] == jpg["item"]["id"]
    assert set(kinds["burst"]["item_ids"]) == {b["id"] for b in burst}
    assert other_cam["item"]["id"] not in kinds["burst"]["item_ids"]
    assert set(kinds["version"]["item_ids"]) == {v1["id"], v2["id"]}
    # MCP: suggest, then stack
    sug = mcp_result(client, "suggest_stacks")
    assert len(sug["content"]) == 3
    made = json.loads(mcp_result(client, "stack_items", {
        "item_ids": kinds["burst"]["item_ids"]})["content"][0]["text"])
    assert len(made["items"]) == 3
    assert {s["kind"] for s in client.get("/api/stacks/suggestions").json()} == {
        "raw_jpeg", "version"}


# ---------- duplicates ----------


def scaled(size) -> bytes:
    """One picture (a gradient with two shapes) at a given size."""
    from PIL import ImageDraw

    im = Image.new("RGB", (800, 600))
    px = im.load()
    for y in range(600):
        for x in range(0, 800, 4):
            for dx in range(4):
                px[x + dx, y] = (x * 255 // 800, y * 255 // 600, 128)
    d = ImageDraw.Draw(im)
    d.ellipse((100, 100, 400, 400), fill=(250, 250, 30))
    d.rectangle((500, 50, 750, 300), fill=(20, 20, 20))
    return png_bytes_of(im.resize(size, Image.Resampling.LANCZOS))


def png_bytes_of(im) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_duplicate_groups_and_resolve_undo(client, upload):
    folder = client.post("/api/folders", json={"name": "F"}).json()
    big = upload("big.png", data=scaled((800, 600)), tags="A", rating="2")["item"]
    small = upload("small.png", data=scaled((400, 300)), tags="B", rating="4",
                   notes="from the old drive", source_url="https://example.com/p",
                   folder_ids=json.dumps([folder["id"]]))["item"]
    tiny = upload("tiny.png", data=scaled((200, 150)))["item"]
    upload("other.png", data=png_bytes(color=(1, 200, 3)))
    groups = client.get("/api/duplicates/groups", params={"kind": "same_image"}).json()
    assert len(groups) == 1
    g = groups[0]
    assert {i["id"] for i in g["items"]} == {big["id"], small["id"], tiny["id"]}
    assert g["suggested_keep"] == big["id"]  # the largest
    # not_duplicate keeps a pair apart
    client.post(f"/api/items/{tiny['id']}/links", json={"dst_id": big["id"],
                                                       "kind": "not_duplicate"})
    client.post(f"/api/items/{tiny['id']}/links", json={"dst_id": small["id"],
                                                       "kind": "not_duplicate"})
    g = client.get("/api/duplicates/groups", params={"kind": "same_image"}).json()[0]
    assert {i["id"] for i in g["items"]} == {big["id"], small["id"]}
    assert client.get("/api/duplicates/groups", params={"kind": "exact"}).json() == []

    res = client.post("/api/duplicates/resolve", json={"keep": big["id"], "others": [small["id"]]})
    assert res.status_code == 200, res.text
    keep = res.json()["keep"]
    assert keep["tags"] == ["A", "B"] and keep["rating"] == 4
    assert keep["folder_ids"] == [folder["id"]]
    assert keep["notes"] == "from the old drive"
    assert keep["source_url"] == "https://example.com/p"
    assert client.get(f"/api/items/{small['id']}").json()["state"] == "trashed"
    batch = res.json()["batch_id"]
    assert {b["batch_id"] for b in client.get("/api/batches").json()[:1]} == {batch}
    undo = client.post(f"/api/batches/{batch}/undo").json()
    assert not undo["skipped"], undo
    back = client.get(f"/api/items/{big['id']}").json()
    assert back["tags"] == ["A"] and back["rating"] == 2 and back["folder_ids"] == []
    assert back["notes"] == "" and back["source_url"] is None
    assert client.get(f"/api/items/{small['id']}").json()["state"] == "active"
    # a protected other is refused before anything changes
    client.patch(f"/api/items/{small['id']}", json={"protected": True})
    res = client.post("/api/duplicates/resolve", json={"keep": big["id"], "others": [small["id"]]})
    assert res.status_code == 409
    assert client.get(f"/api/items/{big['id']}").json()["tags"] == ["A"]


# ---------- scopes ----------


@pytest.fixture
def two_folders(client, upload):
    a = client.post("/api/folders", json={"name": "A"}).json()
    b = client.post("/api/folders", json={"name": "B"}).json()
    in_a = upload("a.png", folder_ids=json.dumps([a["id"]]), tags="x")["item"]
    in_b = upload("b.png", folder_ids=json.dumps([b["id"]]), tags="x")["item"]
    in_b2 = upload("b2.png", folder_ids=json.dumps([b["id"]]))["item"]
    return a, b, in_a, in_b, in_b2


def test_folder_limited_reader_sees_only_its_items(client, two_folders):
    a, b, in_a, in_b, in_b2 = two_folders
    mixed = client.post("/api/stacks", json={"item_ids": [in_b["id"], in_a["id"]],
                                             "cover": in_b["id"]}).json()
    fam = as_token(client, make_token(client, "Fam", ["read"], folder_ids=[a["id"]])["token"])
    got = fam.get(f"/api/stacks/{mixed['id']}").json()
    assert [i["id"] for i in got["items"]] == [in_a["id"]] and got["count"] == 1
    assert got["cover_item_id"] == in_a["id"]
    page = fam.get("/api/items", params={"stacks": "collapse"}).json()
    assert ids(page) == {in_a["id"]}
    assert page["items"][0]["stack_count"] == 1
    # a stack wholly outside its folders doesn't exist for it
    only_b = client.post("/api/stacks", json={"item_ids": [in_b2["id"], in_b["id"]]}).json()
    assert fam.get(f"/api/stacks/{only_b['id']}").status_code == 404
    for path in ("/api/duplicates/groups", "/api/collections/suggested", "/api/vocabulary",
                 "/api/tags/cleanup", f"/api/items/{in_a['id']}/tag-suggestions",
                 "/api/stacks/suggestions"):
        assert fam.get(path).status_code == 404, path
    assert fam.get("/api/people").json() == []
    assert fam.get(f"/api/items/{in_a['id']}/faces").json() == []
    assert fam.get(f"/api/items/{in_b['id']}/faces").status_code == 404
    for tool in ("get_vocabulary", "suggest_tags", "duplicate_groups", "suggest_stacks",
                 "suggested_collections", "tag_cleanup"):
        res = mcp_result(fam, tool, {"id": in_a["id"]} if tool == "suggest_tags" else {})
        assert res["isError"] and "can't do that" in res["content"][0]["text"], tool


def test_read_token_reads_but_never_organises(client, two_folders):
    a, b, in_a, in_b, _ = two_folders
    reader = as_token(client, make_token(client, "R", ["read"])["token"])
    assert reader.get("/api/vocabulary").status_code == 200
    assert reader.get("/api/duplicates/groups").status_code == 200
    assert reader.get("/api/tags/cleanup").status_code == 200
    for method, path, body in (
        ("post", "/api/vocabulary/aliases", {"alias": "y", "tag": "x"}),
        ("post", "/api/vocabulary/implies", {"tag": "y", "implied": "x"}),
        ("post", "/api/tags/merge", {"from": ["x"], "into": "z"}),
        ("post", "/api/tags/rename", {"from": "x", "to": "z"}),
        ("post", "/api/duplicates/resolve", {"keep": in_a["id"], "others": [in_b["id"]]}),
        ("post", "/api/stacks", {"item_ids": [in_a["id"], in_b["id"]]}),
        ("post", "/api/people/merge", {"from": ["p"], "into": "q"}),
        ("post", f"/api/items/{in_a['id']}/tag-suggestions/feedback",
         {"tag": "x", "accepted": False}),
    ):
        res = getattr(reader, method)(path, json=body)
        assert res.status_code == 403 and res.json() == FORBIDDEN, path
    assert reader.delete("/api/vocabulary/aliases", params={"alias": "y"}).status_code == 403
    res = mcp_result(reader, "merge_tags", {"from_tags": ["x"], "into": "z"})
    assert res["isError"]
    agent = as_token(client, make_token(client, "Ag", ["agent"])["token"])
    res = agent.post("/api/tags/merge", json={"from": ["x"], "into": "z"})
    assert res.status_code == 200 and res.json()["items"] == 2


def test_vocabulary_survives_rebuild_and_verify_checks_it(client, upload, capsys):
    upload("a.png", tags="Boots")
    client.post("/api/vocabulary/aliases", json={"alias": "Bootz", "tag": "Boots"})
    client.post("/api/vocabulary/implies", json={"tag": "Boots", "implied": "Shoes"})
    assert cli.verify() == 0
    assert cli.rebuild_index(wipe=True) == 0
    vocab = client.get("/api/vocabulary").json()
    assert vocab["aliases"] == [{"alias": "Bootz", "tag": "Boots"}]
    assert vocab["implies"] == [{"tag": "Boots", "implied": "Shoes"}]
    assert client.get("/api/items", params={"tags": "shoes"}).json()["total"] == 1
    # a stale file is reported, rewrite-meta fixes it
    path = get_settings().library_dir / "vocabulary.json"
    path.write_text(json.dumps({"version": 1, "aliases": {}, "implies": []}))
    capsys.readouterr()
    assert cli.verify() != 0
    assert "vocabulary.json" in capsys.readouterr().out
    assert cli.rewrite_meta() == 0
    assert cli.verify() == 0


def test_smart_folder_query_takes_stacks_and_person(client):
    res = client.post("/api/smart-folders", json={
        "name": "Covers", "query": {"stacks": "collapse", "person": "p1"}})
    assert res.status_code == 201, res.text
    assert res.json()["query"]["stacks"] == "collapse"


def test_bad_values(client):
    assert client.get("/api/items", params={"stacks": "expand"}).status_code == 422
    assert client.get("/api/duplicates/groups", params={"kind": "nope"}).status_code == 422
    assert client.get("/api/stacks/NOPE").status_code == 404
    assert client.post("/api/collections/suggested/nope/accept",
                       json={"name": "x"}).status_code == 404
    assert isinstance(datetime.now(), datetime)
