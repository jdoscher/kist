"""Milestone 2 search: colour, text in images, meaning, more like this,
hybrid, search by image, palettes, fonts, batched embedding, access.

The fake ml service from test_ai stands in for the models: an image's
vector is its mean colour, and the words red/green/blue are those colours.
"""

import io
import json
import math
import random
import struct
import zlib

import pytest
import sqlalchemy as sa
from PIL import Image, ImageFont
from test_ai import (  # noqa: F401  (ml is a fixture)
    drain,
    jobs_of,
    ml,
    solid_png,
    text_pdf,
)
from test_mcp import call, init
from test_tokens import as_token, make_token, mcp_result

from kist import cli, worker
from kist.config import get_settings
from kist.db import SessionLocal
from kist.filetypes import kind_for
from kist.lifecycle import JobStatus
from kist.models import ItemColor, ItemEmbedding
from kist.services import ai, colour, ranking

# ---------- colour maths (no database) ----------

SHARMA = [  # Sharma, Wu & Dalal (2005) test data
    ((50, 2.6772, -79.7751), (50, 0, -82.7485), 2.0425),
    ((50, -1.3802, -84.2814), (50, 0, -82.7485), 1.0000),
    ((50, 2.5, 0), (73, 25, -18), 27.1492),
    ((50, 2.5, 0), (50, 3.1736, 0.5854), 1.0000),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
    ((90.9257, -0.5406, -0.9208), (88.6381, -0.8985, -0.7239), 1.5381),
    ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
]


def test_ciede2000_matches_the_reference_data():
    for a, b, expected in SHARMA:
        assert colour.ciede2000(a, b) == pytest.approx(expected, abs=1e-4)


def test_lab_box_never_drops_a_real_match():
    rng = random.Random(7)
    for _ in range(20000):
        q = tuple(rng.randint(0, 255) for _ in range(3))
        c = tuple(min(255, max(0, x + rng.randint(-80, 80))) for x in q)
        lq, lc = colour.rgb_to_lab(q), colour.rgb_to_lab(c)
        de = colour.ciede2000(lq, lc)
        for tol in (0, 30, 70, 100):
            max_de = colour.max_delta(tol)
            if de <= max_de:
                box = colour.lab_box(lq, max_de)
                assert all(lo <= v <= hi for (lo, hi), v in zip(box, lc, strict=True)), (q, c)


def test_hex_parsing_and_names():
    assert colour.parse_hex("#ABC") == "#aabbcc"
    assert colour.parse_hex("2f4f3a") == "#2f4f3a"
    with pytest.raises(ValueError):
        colour.parse_hex("red")
    assert colour.colour_name(colour.hex_to_lab("#d01010")) == "red"
    assert colour.colour_name(colour.hex_to_lab("#0b1a40")) == "navy"
    assert colour.max_delta(0) < colour.max_delta(30) < colour.max_delta(100)
    assert math.isclose(colour.max_delta(colour.DEFAULT_TOLERANCE), 16.4)
    pal = colour.palette([{"hex": "#ffffff", "pct": 0.2}, {"hex": "#003020", "pct": 0.7},
                          {"hex": "nope", "pct": 0.1}])
    assert [p["hex"] for p in pal] == ["#003020", "#ffffff"]
    assert pal[1]["name"] == "white"


def test_tsquery_only_passes_words():
    assert ranking.tsquery("Orvis recei") == "orvis:* & recei:*"
    assert ranking.tsquery("a' | b:* & !c") == "a:* & b:* & c:*"
    assert ranking.tsquery("  ...  ") is None


def test_fonts_are_a_kind():
    assert kind_for("font/sfnt", "otf") == "font"
    assert kind_for("font/woff2", "woff2") == "font"
    assert kind_for("application/vnd.ms-opentype", "otf") == "font"
    assert kind_for("application/octet-stream", "WOFF2") == "font"
    assert kind_for("text/plain", "ttf") == "other"


# ---------- the SQL side agrees with Python ----------


def test_sql_colour_maths_matches_python(database):
    rng = random.Random(3)
    hexes = ["#" + bytes(rng.randrange(256) for _ in range(3)).hex() for _ in range(60)]
    with SessionLocal() as s:
        for h1, h2 in zip(hexes, reversed(hexes), strict=True):
            lab1, lab2 = s.execute(sa.text("SELECT kist_hex_lab(:a), kist_hex_lab(:b)"),
                                   {"a": h1, "b": h2}).one()
            assert lab1 == pytest.approx(list(colour.hex_to_lab(h1)), abs=1e-6)
            de = s.execute(sa.text("SELECT kist_ciede2000(:l1,:a1,:b1,:l2,:a2,:b2)"),
                           dict(zip(("l1", "a1", "b1", "l2", "a2", "b2"),
                                    [*lab1, *lab2], strict=True))).scalar_one()
            assert de == pytest.approx(colour.ciede2000(lab1, lab2), abs=1e-6)
        for a, b, expected in SHARMA:
            de = s.execute(sa.text("SELECT kist_ciede2000(:l1,:a1,:b1,:l2,:a2,:b2)"),
                           dict(zip(("l1", "a1", "b1", "l2", "a2", "b2"), [*a, *b],
                                    strict=True))).scalar_one()
            assert de == pytest.approx(expected, abs=1e-4)
        assert s.execute(sa.text("SELECT kist_hex_lab('zzz')")).scalar_one() is None


# ---------- fixtures ----------


def ids(page) -> list[str]:
    return [i["id"] for i in page["items"]]


@pytest.fixture
def colours(client, upload, ml):  # noqa: F811
    """Solid images (red, dark red, green, blue, grey), embedded."""
    out = {}
    for name, rgb in (("red", (220, 20, 20)), ("darkred", (150, 15, 20)),
                      ("green", (20, 200, 40)), ("blue", (20, 40, 220)),
                      ("grey", (128, 128, 128))):
        out[name] = upload(f"{name}.png", data=solid_png(rgb), tags=name)["item"]["id"]
    drain()
    return out


# ---------- item_colors ----------


def test_item_colors_follow_dominant_colors(client, upload, colours):
    with SessionLocal() as s:
        rows = s.execute(sa.select(ItemColor).where(ItemColor.item_id == colours["red"])).scalars()
        rows = list(rows)
        assert len(rows) >= 1
        lab = colour.hex_to_lab("#dc1414")
        assert any(colour.ciede2000(lab, (r.l, r.a, r.b)) < 1 for r in rows)
        # the trigger keeps the table in step with the JSON
        s.execute(sa.text("UPDATE items SET dominant_colors = "
                          "'[{\"hex\": \"#00ff00\", \"pct\": 1.0}]' WHERE id = :i"),
                  {"i": colours["red"]})
        s.commit()
        s.expire_all()
        rows = list(s.execute(sa.select(ItemColor).where(ItemColor.item_id == colours["red"]))
                    .scalars())
        assert len(rows) == 1 and rows[0].pct == pytest.approx(1.0)
        s.execute(sa.text("UPDATE items SET dominant_colors = NULL WHERE id = :i"),
                  {"i": colours["red"]})
        s.commit()
        assert s.execute(sa.select(sa.func.count()).select_from(ItemColor)
                         .where(ItemColor.item_id == colours["red"])).scalar_one() == 0


def test_rebuild_restores_item_colors_and_vectors_from_disk(client, upload, colours, ml):  # noqa: F811
    with SessionLocal() as s:
        before = s.execute(sa.select(sa.func.count()).select_from(ItemColor)).scalar_one()
    assert before >= 5
    ml.down = True  # nothing is recomputed
    assert cli.rebuild_index(wipe=True) == 0
    with SessionLocal() as s:
        assert s.execute(sa.select(sa.func.count()).select_from(ItemColor)).scalar_one() == before
        assert s.execute(sa.select(sa.func.count()).select_from(ItemEmbedding)).scalar_one() == 5
    page = client.get("/api/items", params={"color": "#dc1414"}).json()
    assert ids(page)[0] == colours["red"]


# ---------- colour search + palette ----------


def test_colour_search_ranks_by_palette_match(client, colours):
    page = client.get("/api/items", params={"color": "#dc1414"}).json()
    assert ids(page)[0] == colours["red"]
    assert set(ids(page)) <= {colours["red"], colours["darkred"]}
    assert page["total"] == len(page["items"])
    m = page["items"][0]["match"]
    assert m["kind"] == "color" and 0.9 < m["score"] <= 1.0
    # tolerance: 0 is nearly exact, 100 reaches the dark red too
    exact = client.get("/api/items", params={"color": "#dc1414", "color_tol": 0}).json()
    assert ids(exact) == [colours["red"]]
    wide = client.get("/api/items", params={"color": "#dc1414", "color_tol": 100}).json()
    assert colours["darkred"] in ids(wide)
    # filters apply first
    only = client.get("/api/items", params={"color": "#dc1414", "color_tol": 100,
                                            "tags": "darkred"}).json()
    assert ids(only) == [colours["darkred"]]
    assert client.get("/api/items", params={"color": "crimson"}).status_code == 422
    assert client.get("/api/items", params={"color": "#fff", "color_tol": 101}).status_code == 422


def test_palette_endpoint(client, colours):
    pal = client.get(f"/api/items/{colours['blue']}/palette").json()
    assert pal and set(pal[0]) == {"hex", "pct", "name"}
    assert pal[0]["name"] == "blue" and pal[0]["pct"] > 0.5
    assert client.get("/api/items/01NOPE/palette").status_code == 404


# ---------- text in images ----------


@pytest.fixture
def docs(client, upload, ml):  # noqa: F811
    out = {
        "receipt": upload("receipt.pdf", data=text_pdf("Orvis receipt 4471 waders"),
                          mime="application/pdf")["item"]["id"],
        "memo": upload("memo.pdf", data=text_pdf("Quarterly memo about Zero Trust"),
                       mime="application/pdf")["item"]["id"],
        "photo": upload("orvis-logo.png", data=solid_png((10, 90, 30)))["item"]["id"],
    }
    drain()
    return out


def test_text_search_matches_only_inside_items_with_snippets(client, docs):
    page = client.get("/api/items", params={"text": "orvis"}).json()
    assert ids(page) == [docs["receipt"]]  # the filename orvis-logo.png doesn't count
    m = page["items"][0]["match"]
    assert m["kind"] == "text" and m["score"] > 0
    assert "<<Orvis>>" in m["snippet"] and "receipt" in m["snippet"]
    # every word, prefixes match
    assert ids(client.get("/api/items", params={"text": "zero tru"}).json()) == [docs["memo"]]
    assert ids(client.get("/api/items", params={"text": "zero orvis"}).json()) == []
    assert client.get("/api/items", params={"text": "  !! "}).status_code == 422


def test_keyword_search_also_reads_text_and_says_where(client, docs):
    page = client.get("/api/items", params={"q": "4471"}).json()
    assert ids(page) == [docs["receipt"]]
    m = page["items"][0]["match"]
    assert m["kind"] == "keyword" and "<<4471>>" in m["snippet"]
    # a filename hit still works, without a snippet
    page = client.get("/api/items", params={"q": "orvis"}).json()
    assert set(ids(page)) == {docs["receipt"], docs["photo"]}
    logo = next(i for i in page["items"] if i["id"] == docs["photo"])
    assert logo["match"]["kind"] == "keyword" and "snippet" not in (logo["match"] or {}) or \
        logo["match"].get("snippet") is None
    # a plain listing has no match
    assert client.get("/api/items").json()["items"][0]["match"] is None


def test_has_text_filter(client, docs):
    with_text = ids(client.get("/api/items", params={"has_text": "true"}).json())
    # the PDFs have text; the photo's OCR (fake) found some too
    assert {docs["receipt"], docs["memo"]} <= set(with_text)
    with SessionLocal() as s:
        s.execute(sa.text("UPDATE item_text SET text = '' WHERE item_id = :i"),
                  {"i": docs["photo"]})
        s.commit()
    without = ids(client.get("/api/items", params={"has_text": "false"}).json())
    assert without == [docs["photo"]]


# ---------- meaning, more like this, hybrid ----------


def test_semantic_results_carry_meaning_matches(client, colours):
    page = client.get("/api/items", params={"semantic": "blue"}).json()
    assert ids(page)[0] == colours["blue"]
    assert page["items"][0]["match"]["kind"] == "meaning"
    scores = [i["match"]["score"] for i in page["items"]]
    assert scores == sorted(scores, reverse=True)


def test_more_like_this(client, colours):
    page = client.get("/api/items", params={"similar": colours["red"]}).json()
    assert colours["red"] not in ids(page)  # not its own match
    assert ids(page)[0] == colours["darkred"]
    assert page["items"][0]["match"]["kind"] == "similar"
    assert page["total"] == 4  # everything else
    assert client.get("/api/items", params={"similar": "01NOPE"}).status_code == 404
    with SessionLocal() as s:
        s.execute(sa.delete(ItemEmbedding).where(ItemEmbedding.item_id == colours["grey"]))
        s.commit()
    res = client.get("/api/items", params={"similar": colours["grey"]})
    assert res.status_code == 422 and "no embedding" in res.json()["detail"]


def test_hybrid_fuses_keywords_and_meaning(client, upload, ml):  # noqa: F811
    boat = upload("boat.png", data=solid_png((230, 10, 10)))["item"]["id"]
    car = upload("car.png", data=solid_png((10, 10, 230)))["item"]["id"]
    tree = upload("tree.png", data=solid_png((10, 230, 10)))["item"]["id"]
    drain()
    page = client.get("/api/items", params={"q": "car", "semantic": "red"}).json()
    kinds = {i["id"]: i["match"]["kind"] for i in page["items"]}
    assert kinds[car] == "hybrid"  # matched the word and has a vector
    assert kinds[boat] == "meaning" and kinds[tree] == "meaning"
    order = ids(page)
    assert order.index(car) < order.index(tree)  # the keyword hit lifts it
    assert order[0] in (boat, car)
    assert page["total"] == 3  # q is a signal here, not a filter
    scores = [i["match"]["score"] for i in page["items"]]
    assert scores == sorted(scores, reverse=True) and all(sc < 0.04 for sc in scores)


def test_colour_and_meaning_combine(client, colours):
    page = client.get("/api/items", params={"semantic": "red", "color": "#dc1414",
                                            "color_tol": 50}).json()
    assert set(ids(page)) == {colours["red"], colours["darkred"]}
    assert page["items"][0]["match"]["kind"] == "hybrid"


def test_ranked_paging_and_cursors(client, colours):
    first = client.get("/api/items", params={"semantic": "green", "limit": 2}).json()
    assert len(first["items"]) == 2 and first["total"] == 5 and first["next_cursor"]
    seen = ids(first)
    cursor = first["next_cursor"]
    while cursor:
        page = client.get("/api/items", params={"semantic": "green", "limit": 2,
                                                "cursor": cursor}).json()
        assert page["total"] is None
        seen += ids(page)
        cursor = page["next_cursor"]
    assert len(seen) == len(set(seen)) == 5
    # a ranked cursor isn't a plain one, and the other way round
    plain = client.get("/api/items", params={"limit": 2}).json()
    assert client.get("/api/items", params={"semantic": "green",
                                            "cursor": plain["next_cursor"]}).status_code == 400
    assert client.get("/api/items", params={"cursor": first["next_cursor"]}).status_code == 400


# ---------- search by image ----------


def test_search_by_image(client, colours, ml):  # noqa: F811
    before = client.get("/api/items").json()["total"]
    res = client.post("/api/search/image", files={"file": ("q.png", solid_png((40, 60, 230)),
                                                           "image/png")})
    assert res.status_code == 200, res.text
    page = res.json()
    assert ids(page)[0] == colours["blue"]
    assert page["items"][0]["match"]["kind"] == "similar" and page["total"] == 5
    assert client.get("/api/items").json()["total"] == before  # nothing stored
    # filters as query params
    res = client.post("/api/search/image", params={"tags": "red"},
                      files={"file": ("q.png", solid_png((40, 60, 230)), "image/png")})
    assert ids(res.json()) == [colours["red"]]
    assert client.post("/api/search/image", data={"x": "1"}).status_code == 422
    ml.down = True
    res = client.post("/api/search/image", files={"file": ("q.png", solid_png((1, 2, 3)),
                                                           "image/png")})
    assert res.status_code == 503


# ---------- access: a folder-limited reader ----------


def test_folder_limited_reader_cannot_see_outside_through_any_search(client, upload, ml):  # noqa: F811
    folder = client.post("/api/folders", json={"name": "Shared"}).json()
    inside = upload("in.png", data=solid_png((220, 20, 20)),
                    folder_ids=json.dumps([folder["id"]]))["item"]["id"]
    outside = upload("out.png", data=solid_png((225, 25, 25)))["item"]["id"]
    pdf_out = upload("secret.pdf", data=text_pdf("Orvis secret"),
                     mime="application/pdf")["item"]["id"]
    drain()
    fam = as_token(client, make_token(client, "Family", ["read"],
                                      folder_ids=[folder["id"]])["token"])
    for params in ({"semantic": "red"}, {"color": "#dc1414", "color_tol": 100},
                   {"text": "orvis"}, {"q": "orvis"}, {"has_text": "true"},
                   {"similar": inside}):
        page = fam.get("/api/items", params=params).json()
        assert outside not in ids(page) and pdf_out not in ids(page), params
        assert set(ids(page)) <= {inside}, params
    assert fam.get("/api/items", params={"similar": outside}).status_code == 404
    assert fam.get(f"/api/items/{outside}/palette").status_code == 404
    assert fam.get(f"/api/items/{inside}/palette").status_code == 200
    res = fam.post("/api/search/image", files={"file": ("q.png", solid_png((220, 20, 20)),
                                                        "image/png")})
    assert res.status_code == 200 and ids(res.json()) == [inside]
    # a read token searches by image but never asks for captions
    reader = as_token(client, make_token(client, "Reader", ["read"])["token"])
    assert reader.post("/api/search/image", files={"file": ("q.png", solid_png((1, 1, 1)),
                                                            "image/png")}).status_code == 200
    assert reader.post(f"/api/items/{inside}/caption").status_code == 403
    # over MCP too
    for tool, args in (("search_items", {"semantic": "red"}),
                       ("search_items", {"color": "#dc1414", "color_tol": 100}),
                       ("search_items", {"text": "orvis"}),
                       ("search_items", {"similar_to": inside})):
        res = mcp_result(fam, tool, args)
        assert not res.get("isError"), res
        found = {i["id"] for i in json.loads(res["content"][0]["text"])["items"]}
        assert found <= {inside}, (tool, args)
    res = mcp_result(fam, "item_palette", {"id": outside})
    assert res["isError"] and "not found" in res["content"][0]["text"]
    res = mcp_result(fam, "search_items", {"similar_to": outside})
    assert res["isError"] and "not found" in res["content"][0]["text"]


# ---------- MCP ----------


def structured(result):
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


def test_mcp_search_tools(client, colours, docs):
    init(client)
    out = structured(call(client, "search_items", {"semantic": "blue", "limit": 2}))
    assert out["items"][0]["id"] == colours["blue"]
    assert out["items"][0]["match"]["kind"] == "meaning" and out["next_cursor"]
    out = structured(call(client, "search_items", {"color": "#dc1414"}))
    assert out["items"][0]["id"] == colours["red"]
    out = structured(call(client, "search_items", {"text": "orvis"}))
    assert [i["id"] for i in out["items"]] == [docs["receipt"]]
    assert "<<Orvis>>" in out["items"][0]["match"]["snippet"]
    out = structured(call(client, "search_items", {"similar_to": colours["red"]}))
    assert out["items"][0]["id"] == colours["darkred"]
    # a plain search has no match key at all (small results)
    out = structured(call(client, "search_items", {"tags": ["green"]}))
    assert "match" not in out["items"][0]
    pal = call(client, "item_palette", {"id": colours["green"]})
    pal = pal.get("structuredContent", {}).get("result") or [
        json.loads(c["text"]) for c in pal["content"]]
    assert pal[0]["name"] == "green"
    names = {t["name"] for t in
             __import__("test_mcp").rpc(client, "tools/list", id_=3)["result"]["tools"]}
    assert {"search_by_image", "item_palette"} <= names


def test_mcp_search_by_image(client, colours, ml):  # noqa: F811
    init(client)
    imports = get_settings().library_dir / "import"
    imports.mkdir(parents=True, exist_ok=True)
    (imports / "query.png").write_bytes(solid_png((220, 20, 20)))
    query = str(imports / "query.png")
    out = structured(call(client, "search_by_image", {"file_path": query}))
    assert out["items"][0]["id"] == colours["red"]
    assert out["items"][0]["match"]["kind"] == "similar"
    out = structured(call(client, "search_by_image",
                          {"file_path": query, "tags": ["green"]}))
    assert [i["id"] for i in out["items"]] == [colours["green"]]
    res = call(client, "search_by_image", {})
    assert res["isError"] and "exactly one" in res["content"][0]["text"]
    res = call(client, "search_by_image", {"file_path": "/etc/passwd"})
    assert res["isError"]
    ml.down = True
    res = call(client, "search_by_image", {"file_path": query})
    assert res["isError"] and "ml service" in res["content"][0]["text"]


# ---------- fonts ----------


def otf_bytes() -> bytes:
    return ImageFont.load_default(size=20).font_bytes  # Aileron Regular (OFL)


def woff_from_sfnt(data: bytes) -> bytes:
    """WOFF 1.0 wrapping of an OpenType file (zlib per table)."""
    flavor, num = struct.unpack(">IH", data[:6])
    tables = []
    for i in range(num):
        tag, checksum, offset, length = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
        raw = data[offset:offset + length]
        comp = zlib.compress(raw)
        tables.append((tag, checksum, raw if len(comp) >= length else comp, length))
    header_len, dir_len = 44, 20 * num
    offset = header_len + dir_len
    body = b""
    directory = b""
    for tag, checksum, blob, orig in tables:
        directory += struct.pack(">4sIIII", tag, offset + len(body), len(blob), orig, checksum)
        body += blob + b"\0" * (-len(blob) % 4)
    total = header_len + dir_len + len(body)
    sfnt_size = 12 + 16 * num + sum((t[3] + 3) & ~3 for t in tables)
    header = struct.pack(">4sIIHHIHHIIIII", b"wOFF", flavor, total, num, 0, sfnt_size,
                         1, 0, 0, 0, 0, 0, 0)
    return header + directory + body


def test_font_files_get_a_specimen(client, upload):
    item = upload("Aileron-Regular.otf", data=otf_bytes(), mime="font/otf")["item"]
    assert item["kind"] == "font"
    assert item["preview_status"] == "ready" and item["has_thumb"]
    d = get_settings().items_dir / item["id"]
    with Image.open(d / "thumb_1024.webp") as im:
        assert im.width > im.height
    page = client.get("/api/items", params={"type": "font"}).json()
    assert ids(page) == [item["id"]]
    assert client.get("/api/stats").json()["by_kind"]["font"] == 1
    upload("plain.png")
    assert ids(client.get("/api/items", params={"type": "font"}).json()) == [item["id"]]
    assert item["id"] not in ids(client.get("/api/items", params={"type": "other"}).json())


def test_woff_fonts_too(client, upload):
    item = upload("Aileron.woff", data=woff_from_sfnt(otf_bytes()), mime="font/woff")["item"]
    assert item["kind"] == "font"
    assert item["preview_status"] == "ready", item["preview_error"]


def test_a_broken_font_fails_its_preview_only(client, upload):
    item = upload("broken.ttf", data=b"\x00\x01\x00\x00" + b"\x00" * 200, mime="font/ttf")["item"]
    assert item["kind"] in ("font", "other")
    if item["kind"] == "font":
        assert item["preview_status"] == "failed"


# ---------- batched embedding ----------


def test_worker_embeds_in_batches(client, upload, ml):  # noqa: F811
    for i in range(7):
        upload(f"{i}.png", data=solid_png((30 * i, 20, 200)))
    with SessionLocal() as s:  # OCR jobs out of the way
        s.execute(sa.text("DELETE FROM jobs WHERE kind = 'ocr'"))
        s.commit()
    lane = worker.AiLane(2, batch=4)
    started = lane.fill()
    assert started == 7  # one thread got 4, the other 3
    while lane.wait(5):
        lane.fill()
    assert ml.calls.count("/embed/images") == 2 and "/embed/image" not in ml.calls
    assert {j.status for j in jobs_of("embed")} == {JobStatus.DONE}
    with SessionLocal() as s:
        assert s.execute(sa.select(sa.func.count()).select_from(ItemEmbedding)).scalar_one() == 7


def test_one_bad_image_fails_only_its_job(client, upload, ml):  # noqa: F811
    good = [upload(f"{i}.png", data=solid_png((10 * i, 50, 50)))["item"]["id"] for i in range(3)]
    with SessionLocal() as s:
        s.execute(sa.text("DELETE FROM jobs WHERE kind = 'ocr'"))
        s.commit()
    (get_settings().items_dir / good[1] / "thumb_512.webp").write_bytes(b"not an image")
    with SessionLocal() as s:
        jobs = ai.claim_batch(s, runner="worker", kinds=("embed",), batch=8)
        assert len(jobs) == 3
        ids_ = [j.id for j in jobs]
    assert worker.run_ai_batch(ids_) == "failed"
    by_item = {j.payload["item_id"]: j for j in jobs_of("embed")}
    assert by_item[good[1]].status == JobStatus.PENDING  # a retry is due
    assert "can't process" in by_item[good[1]].last_error
    assert by_item[good[0]].status == by_item[good[2]].status == JobStatus.DONE


def test_batches_fall_back_for_an_older_ml_service(client, upload, ml):  # noqa: F811
    ml.batch = False
    for i in range(3):
        upload(f"{i}.png", data=solid_png((10, 10 * i, 90)))
    with SessionLocal() as s:
        jobs = ai.claim_batch(s, runner="worker", kinds=("embed",), batch=8)
    assert worker.run_ai_batch([j.id for j in jobs]) == "done"
    assert ml.calls.count("/embed/image") == 3


def test_ml_down_puts_a_whole_batch_back(client, upload, ml):  # noqa: F811
    for i in range(3):
        upload(f"{i}.png", data=solid_png((10, 10 * i, 90)))
    with SessionLocal() as s:
        jobs = ai.claim_batch(s, runner="worker", kinds=("embed",), batch=8)
    ml.down = True
    assert worker.run_ai_batch([j.id for j in jobs]) == "unavailable"
    assert {(j.status, j.attempts) for j in jobs_of("embed")} == {(JobStatus.PENDING, 0)}


def test_claim_batch_keeps_queue_order(client, upload, ml):  # noqa: F811
    upload("a.png", data=solid_png((1, 2, 3)))  # embed then ocr (a screenshot)
    upload("b.png", data=solid_png((4, 5, 6)))
    with SessionLocal() as s:
        first = ai.claim_batch(s, runner="worker", kinds=("embed", "ocr"), batch=8)
        assert [j.kind for j in first] == ["embed", "embed"]  # the oldest, plus embeds
        second = ai.claim_batch(s, runner="worker", kinds=("embed", "ocr"), batch=8)
        assert [j.kind for j in second] == ["ocr"]


def test_ml_service_batch_endpoint(monkeypatch):
    """kist.ml.server's /embed/images with a stand-in engine (no models)."""
    from fastapi.testclient import TestClient

    from kist.ml import server

    monkeypatch.setenv("KIST_ML_PRELOAD", "")

    class Engine:
        model, dim = "ViT-B-32__openai", 3

        def prepare_many(self, datas):
            out = []
            for d in datas:
                try:
                    out.append(Image.open(io.BytesIO(d)).convert("RGB").getpixel((0, 0)))
                except OSError as exc:
                    out.append(f"can't process this image: {exc}")
            return out

        def embed_many(self, prepared):
            return [p if isinstance(p, str) else [c / 255 for c in p] for p in prepared]

        def unload_idle(self):
            return []

    with TestClient(server.app) as c:
        monkeypatch.setattr(server.state, "clip", Engine())
        files = [("images", ("a.png", solid_png((255, 0, 0)), "image/png")),
                 ("images", ("b.bin", b"nope", "application/octet-stream")),
                 ("images", ("c.png", solid_png((0, 0, 255)), "image/png"))]
        res = c.post("/embed/images", files=files)
        assert res.status_code == 200, res.text
        out = res.json()["results"]
        assert out[0]["vector"] == [1, 0, 0] and "error" in out[1]
        assert out[2]["vector"] == [0, 0, 1]
        too_many = [("images", (f"{i}.png", b"x", "image/png")) for i in range(33)]
        assert c.post("/embed/images", files=too_many).status_code == 413
        assert c.post("/embed/images", data={"x": "1"}).status_code == 422


def test_ai_status_reports_batching(client, ml):  # noqa: F811
    data = client.get("/api/ai/status").json()
    assert data["concurrency"] == 2 and data["embed_batch"] == 16
    assert data["captions"]["provider"] is None and data["captioned"] == 0


def test_small_saturated_areas_survive_the_palette(client, upload):
    im = Image.new("RGB", (800, 600), (120, 120, 125))
    im.paste((200, 16, 22), (300, 220, 520, 400))  # a red car's share of a street
    buf = io.BytesIO()
    im.save(buf, "PNG")
    item = upload("street.png", data=buf.getvalue())["item"]
    reds = [c for c in item["dominant_colors"]
            if colour.ciede2000(colour.hex_to_lab(c["hex"]), colour.hex_to_lab("#c8101e")) < 10]
    assert reds and 0.05 < reds[0]["pct"] < 0.2
    assert ids(client.get("/api/items", params={"color": "#c8102e"}).json()) == [item["id"]]


def test_regen_colors_and_old_font_items(client, upload, capsys):
    item = upload("a.png", data=solid_png((10, 120, 200)))["item"]
    font = upload("Aileron.otf", data=otf_bytes(), mime="font/otf")["item"]
    with SessionLocal() as s:  # how M1 left them: a muddy palette, fonts as "other"
        s.execute(sa.text("UPDATE items SET dominant_colors = "
                          "'[{\"hex\": \"#808080\", \"pct\": 1.0}]' WHERE id = :i"),
                  {"i": item["id"]})
        s.execute(sa.text("UPDATE items SET preview_status = 'none', width = NULL "
                          "WHERE id = :i"), {"i": font["id"]})
        s.commit()
    assert cli.regen_previews(False, colors_only=True) == 0
    got = client.get(f"/api/items/{item['id']}").json()
    assert got["dominant_colors"][0]["hex"] != "#808080"
    assert got["modified_at"] == item["modified_at"]  # not an edit
    assert ids(client.get("/api/items", params={"color": "#0a78c8"}).json()) == [item["id"]]
    assert cli.regen_previews(False) == 0
    assert client.get(f"/api/items/{font['id']}").json()["preview_status"] == "ready"
