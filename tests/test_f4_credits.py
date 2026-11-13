"""F4: credits and rights, the AI-made flag from metadata, auto-fill from
stored files, the clearance check and the print check."""

import io
import json
import struct
from datetime import datetime

import pytest
from conftest import png_bytes
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from kist import cli
from kist.config import get_settings
from kist.services import credits

# ---------- helpers ----------


def _iim(records: dict[tuple[int, int], str]) -> bytes:
    """A JPEG APP13 Photoshop segment carrying IPTC-IIM records."""
    data = b""
    for (rec, ds), text in records.items():
        raw = text.encode("utf-8")
        data += b"\x1c" + bytes([rec, ds]) + struct.pack(">H", len(raw)) + raw
    if len(data) % 2:
        data += b"\x00"
    res = b"8BIM" + struct.pack(">H", 0x0404) + b"\x00\x00" + struct.pack(">I", len(data)) + data
    body = b"Photoshop 3.0\x00" + res
    return b"\xff\xed" + struct.pack(">H", len(body) + 2) + body


def jpeg_with_metadata(color=(30, 60, 90), size=(900, 600), *, artist=None, copyright_=None,
                       software=None, iim=None, dpi=None, icc=None) -> bytes:
    im = Image.new("RGB", size, color)
    exif = Image.Exif()
    if artist:
        exif[0x013B] = artist
    if copyright_:
        exif[0x8298] = copyright_
    if software:
        exif[0x0131] = software
    kw = {"exif": exif.tobytes()}
    if dpi:
        kw["dpi"] = (dpi, dpi)
    if icc:
        kw["icc_profile"] = icc
    buf = io.BytesIO()
    im.save(buf, "JPEG", **kw)
    data = buf.getvalue()
    if iim:
        data = data[:2] + _iim(iim) + data[2:]
    return data


def png_with_text(color=(9, 99, 199), **text) -> bytes:
    info = PngInfo()
    for k, v in text.items():
        info.add_text(k, v)
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, "PNG", pnginfo=info)
    return buf.getvalue()


def srgb_icc() -> bytes:
    from PIL import ImageCms

    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


PAGE = """<!doctype html><html><head><title>Tower</title>
<meta property="og:site_name" content="Daily Towers">
<meta property="og:title" content="A tall tower">
<meta name="author" content="By Jane Doe">
<link rel="license" href="https://creativecommons.org/licenses/by/4.0/">
<meta name="generator" content="Midjourney CMS">
</head><body><p>Story</p></body></html>"""


def ids(page) -> set[str]:
    return {i["id"] for i in page["items"]}


# ---------- fields, validation, credit line ----------


def test_rights_status_field_and_credit_line(client, upload):
    item = upload("a.png")["item"]
    res = client.patch(f"/api/items/{item['id']}", json={"provenance": {
        "rights_status": "Ref only", "creator": "Jane Doe", "credit": "Magnum Photos"}})
    assert res.status_code == 200, res.text
    got = res.json()
    assert got["provenance"]["rights_status"] == "ref_only"
    assert got["credit_line"] == "Jane Doe / Magnum Photos"
    bad = client.patch(f"/api/items/{item['id']}", json={"provenance": {"rights_status": "maybe"}})
    assert bad.status_code == 422 and "rights_status" in bad.text
    # null removes it (the way out of a status)
    got = client.patch(f"/api/items/{item['id']}",
                       json={"provenance": {"rights_status": None}}).json()
    assert "rights_status" not in got["provenance"]
    # meta.json carries it (the durable copy)
    client.patch(f"/api/items/{item['id']}", json={"provenance": {"rights_status": "comp"}})
    meta = json.loads((get_settings().items_dir / item["id"] / "meta.json").read_text())
    assert meta["provenance"]["rights_status"] == "comp"


def test_credit_line_rules():
    assert credits.credit_line({}) is None
    assert credits.credit_line({"creator": "Ann"}) == "Ann"
    assert credits.credit_line({"credit": "AP"}) == "AP"
    assert credits.credit_line({"creator": "Ann", "credit": "Ann / AP"}) == "Ann / AP"
    assert credits.source_host({"page_url": "https://www.example.com/x"}) == "example.com"
    assert credits.parse_print_size("A4") == (11.69, 8.27)
    assert credits.parse_print_size("20cm")[0] == pytest.approx(7.874, rel=1e-3)
    from kist.services.validation import ValidationError

    with pytest.raises(ValidationError):
        credits.parse_print_size("big")


def test_filters_rights_needs_credit_and_print(client, upload):
    small = upload("small.png", data=png_bytes(size=(600, 400)))["item"]
    big = upload("big.png", data=png_bytes(color=(1, 2, 3), size=(3600, 2600)))["item"]
    own = upload("own.png", data=png_bytes(color=(4, 5, 6), size=(700, 700)))["item"]
    client.patch(f"/api/items/{small['id']}", json={"provenance": {"rights_status": "cleared",
                                                                    "credit": "AP"}})
    client.patch(f"/api/items/{big['id']}", json={"provenance": {"rights_status": "comp"}})
    client.patch(f"/api/items/{own['id']}", json={"provenance": {"credit_exempt": True}})

    def q(params):
        res = client.get("/api/items", params=params)
        assert res.status_code == 200, res.text
        return ids(res.json())

    assert q({"rights_status": "cleared"}) == {small["id"]}
    assert q({"rights_status": "uncleared"}) == {big["id"], own["id"]}
    assert q({"rights_status": "none"}) == {own["id"]}
    assert q({"rights_status": "comp,cleared"}) == {small["id"], big["id"]}
    assert client.get("/api/items", params={"rights_status": "nope"}).status_code == 422
    # needs a credit: no credit/creator and not marked "no credit needed"
    assert q({"needs_credit": "true"}) == {big["id"]}
    assert q({"needs_credit": "false"}) == {small["id"], own["id"]}
    # print size at 300 dpi: 3600 px = 12 in, fits A4 (2480x3508) both ways
    assert q({"print_min": "A4"}) == {big["id"]}
    assert q({"print_min": "10in"}) == {big["id"]}
    assert q({"print_max": "5in"}) == {small["id"], own["id"]}
    assert q({"print_min": "20cm"}) == {big["id"]}  # 7.9 in = 2362 px
    assert client.get("/api/items", params={"print_min": "huge"}).status_code == 422
    # ranked searches (colour) and the timeline buckets take them too
    assert q({"needs_credit": "true", "color": "#010203"}) == {big["id"]}
    res = client.get("/api/items/buckets", params={"needs_credit": "true"})
    assert res.status_code == 200, res.text
    assert sum(b["count"] for b in res.json()["buckets"]) == 1
    # a smart folder keeps them
    sf = client.post("/api/smart-folders", json={"name": "Needs credit",
                                                  "query": {"needs_credit": True}})
    assert sf.status_code in (200, 201), sf.text
    assert sf.json()["query"]["needs_credit"] is True
    from kist.db import SessionLocal
    from kist.models import Item
    from kist.services import selection

    with SessionLocal() as s:
        conds = selection.smart_filters({"needs_credit": True})
        found = [i.id for i in s.query(Item).filter(*conds)]
    assert found == [big["id"]]
    # summary counts for the sidebar
    summary = client.get("/api/credits/summary").json()
    assert summary == {"needs_credit": 1, "uncleared": 2, "ai_generated": 0}


# ---------- metadata on ingest: EXIF / IPTC-IIM / generators ----------


def test_ingest_reads_exif_and_iim_credits(client, upload):
    data = jpeg_with_metadata(artist="Exif Artist", copyright_="(c) 2020 Exif",
                              iim={(2, 80): "Iim Byline", (2, 110): "Iim Credit",
                                   (2, 116): "(c) Iim"})
    item = upload("c.jpg", data=data, mime="image/jpeg")["item"]
    prov = item["provenance"]
    assert prov["creator"] == "Iim Byline"  # IIM wins over EXIF Artist
    assert prov["credit"] == "Iim Credit"
    assert prov["license"] == "(c) Iim"
    assert item["credit_line"] == "Iim Byline / Iim Credit"
    assert "ai_generated" not in prov


@pytest.mark.parametrize(("text", "generator"), [
    ({"parameters": "a cat, Steps: 20, Sampler: Euler"}, "Stable Diffusion"),
    ({"prompt": "{\"3\": {}}"}, "ComfyUI"),
    ({"Software": "NovelAI"}, "NovelAI"),
])
def test_ingest_flags_ai_from_png_generator_text(client, upload, text, generator):
    item = upload("gen.png", data=png_with_text(**text))["item"]
    assert item["provenance"]["ai_generated"] is True
    assert item["provenance"]["ai_generator"] == generator
    got = client.get("/api/items", params={"ai_generated": "true"}).json()
    assert ids(got) == {item["id"]}


def test_exif_software_generator_and_plain_software(client, upload):
    ai = upload("mj.jpg", data=jpeg_with_metadata(software="Midjourney v6"),
                mime="image/jpeg")["item"]
    assert ai["provenance"]["ai_generator"] == "Midjourney"
    plain = upload("ps.jpg", data=jpeg_with_metadata(color=(1, 1, 1),
                                                     software="Adobe Photoshop 25.0"),
                   mime="image/jpeg")["item"]
    assert "ai_generated" not in plain["provenance"]
    # the user's "no" wins and stays (flag, never hide: nothing filters it out)
    res = client.patch(f"/api/items/{ai['id']}", json={"provenance": {"ai_generated": False}})
    assert res.json()["provenance"]["ai_generated"] is False


# ---------- auto-fill from the stored snapshot ----------


def test_snapshot_autofills_empty_fields_without_moving_modified(client, upload):
    item = upload("t.png", source_url="https://towers.test/story")["item"]
    before = client.get(f"/api/items/{item['id']}").json()
    res = client.post(f"/api/items/{item['id']}/snapshot", json={"html": PAGE})
    assert res.status_code == 200, res.text
    got = client.get(f"/api/items/{item['id']}").json()
    prov = got["provenance"]
    assert prov["creator"] == "Jane Doe"
    assert prov["site"] == "Daily Towers"
    assert prov["rights_url"] == "https://creativecommons.org/licenses/by/4.0/"
    assert prov.get("ai_generated") is None  # a page's generator tag is its CMS
    assert got["modified_at"] == before["modified_at"]
    hist = client.get(f"/api/items/{item['id']}/history").json()
    edits = [e for e in hist if e["kind"] == "edit"]
    assert edits and "creator" in edits[0]["after"]["provenance"]


def test_autofill_never_overwrites_and_suggestions_are_read_only(client, upload):
    item = upload("t.png", source_url="https://towers.test/s")["item"]
    client.patch(f"/api/items/{item['id']}", json={"provenance": {"creator": "Mine"}})
    d = get_settings().items_dir / item["id"] / "page"
    d.mkdir(parents=True, exist_ok=True)
    (d / "snapshot.html").write_text(PAGE)
    info = client.get(f"/api/items/{item['id']}/credits").json()
    assert "creator" not in info["suggestions"]
    assert info["suggestions"]["site"] == "Daily Towers"
    assert info["line"] == "Mine" and info["caption"]["credit"] == "Mine"
    res = client.post(f"/api/items/{item['id']}/credits/autofill")
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["filled"]["site"] == "Daily Towers"
    assert out["item"]["provenance"]["creator"] == "Mine"
    # nothing left to fill: a second run changes nothing
    assert client.post(f"/api/items/{item['id']}/credits/autofill").json()["filled"] == {}


def test_backfill_cli_fills_old_items_in_one_undoable_batch(client, upload, capsys):
    a = upload("a.jpg", data=jpeg_with_metadata(artist="Old Artist"), mime="image/jpeg")["item"]
    b = upload("b.png")["item"]
    # pretend these were added before F4: drop what ingest read
    from kist.db import SessionLocal
    from kist.models import Item

    with SessionLocal() as s:
        for i in (a["id"], b["id"]):
            row = s.get(Item, i)
            row.provenance = {k: v for k, v in (row.provenance or {}).items()
                              if k not in ("creator",)}
        s.commit()
    d = get_settings().items_dir / b["id"] / "page"
    d.mkdir(parents=True, exist_ok=True)
    (d / "snapshot.html").write_text(PAGE)
    before = {i: client.get(f"/api/items/{i}").json()["modified_at"] for i in (a["id"], b["id"])}

    class Args:
        dry_run = True
        limit = None

    from kist import credits_cli

    assert credits_cli.run(Args) == 0
    assert "would fill 2 items" in capsys.readouterr().out
    assert "creator" not in client.get(f"/api/items/{a['id']}").json()["provenance"]
    Args.dry_run = False
    assert credits_cli.run(Args) == 0
    out = capsys.readouterr().out
    assert "filled 2 items" in out
    ga, gb = (client.get(f"/api/items/{i}").json() for i in (a["id"], b["id"]))
    assert ga["provenance"]["creator"] == "Old Artist"
    assert gb["provenance"]["creator"] == "Jane Doe"
    assert {ga["modified_at"], gb["modified_at"]} == {before[a["id"]], before[b["id"]]}
    batch = out.rsplit("batch ", 1)[1].strip()
    assert client.post(f"/api/batches/{batch}/undo").status_code == 200
    assert "creator" not in client.get(f"/api/items/{a['id']}").json()["provenance"]
    # re-running is safe
    assert credits_cli.run(Args) == 0


# ---------- bulk edit and clearance ----------


def test_bulk_credits_one_batch_and_undo(client, upload):
    items = [upload(f"{n}.png")["item"]["id"] for n in range(3)]
    res = client.post("/api/credits/bulk", json={
        "item_ids": items, "provenance": {"credit": "Studio X", "rights_status": "licensed"}},
        headers={"X-Kist-Batch": "credits-1"})
    assert res.status_code == 200, res.text
    assert res.json()["updated"] == 3 and res.json()["batch_id"] == "credits-1"
    for i in items:
        p = client.get(f"/api/items/{i}").json()["provenance"]
        assert p["credit"] == "Studio X" and p["rights_status"] == "licensed"
    assert client.post("/api/credits/bulk", json={
        "item_ids": items, "provenance": {"page_url": "https://x.test"}}).status_code == 422
    assert client.post("/api/credits/bulk", json={
        "item_ids": items, "provenance": {"rights_status": "x"}}).status_code == 422
    assert client.post("/api/batches/credits-1/undo").status_code == 200
    for i in items:
        assert "credit" not in client.get(f"/api/items/{i}").json()["provenance"]


def test_clearance_lists_uncleared_items_and_can_be_turned_off(client, upload):
    folder = client.post("/api/folders", json={"name": "Pitch"}).json()
    a = upload("a.png", folder_id=folder["id"])["item"]
    b = upload("b.png", folder_id=folder["id"])["item"]
    c = upload("c.png", folder_id=folder["id"])["item"]
    client.patch(f"/api/items/{a['id']}", json={"provenance": {"rights_status": "cleared"}})
    client.patch(f"/api/items/{b['id']}", json={"provenance": {"rights_status": "comp"}})
    res = client.post("/api/credits/clearance", json={"target": "folder",
                                                      "target_id": folder["id"]})
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["enabled"] is True and out["total"] == 3 and out["uncleared"] == 2
    assert {r["id"] for r in out["items"]} == {b["id"], c["id"]}
    assert out["by_status"] == {"comp": 1, "none": 1}
    assert client.patch("/api/credits/settings",
                        json={"clearance_warning": False}).json()["clearance_warning"] is False
    assert client.post("/api/credits/clearance",
                       json={"item_ids": [a["id"], b["id"]]}).json()["enabled"] is False
    assert json.loads((get_settings().state_dir / "credits.json").read_text())[
        "clearance_warning"] is False


# ---------- print check ----------


def test_print_check_sizes_profile_and_dpi(client, upload):
    data = jpeg_with_metadata(size=(3000, 2000), dpi=72, icc=srgb_icc())
    item = upload("p.jpg", data=data, mime="image/jpeg")["item"]
    res = client.get(f"/api/items/{item['id']}/print")
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["sizes"]["300"]["width"] == {"in": 10.0, "cm": 25.4}
    assert out["sizes"]["150"]["height"]["in"] == pytest.approx(13.33)
    assert "A5" in out["fits"]["300"] and "A4" not in out["fits"]["300"]
    assert "A3" in out["fits"]["150"]
    assert out["file_dpi"] == 72
    assert out["icc"] and "sRGB" in out["icc"]
    assert out["colour_space"] == "RGB" and out["untagged"] is False and out["low_res"] is False
    plain = upload("q.png", data=png_bytes(size=(800, 600)))["item"]
    out = client.get(f"/api/items/{plain['id']}/print").json()
    assert out["icc"] is None and out["untagged"] is True and out["low_res"] is True
    # F1's low-res hook
    from kist.db import SessionLocal
    from kist.models import Item

    with SessionLocal() as s:
        low = {i.id for i in s.query(Item).filter(credits.low_res_condition())}
    assert low == {plain["id"]}


def test_icc_v4_description_parses():
    # a minimal v4 profile header with one mluc 'desc' tag
    text = "Display P3".encode("utf-16-be")
    mluc = b"mluc" + b"\0" * 4 + struct.pack(">II", 1, 12) + b"enUS" + struct.pack(
        ">II", len(text), 28) + text
    tag_table = struct.pack(">I", 1) + b"desc" + struct.pack(">II", 144, len(mluc))
    header = bytearray(128)
    header[16:20] = b"RGB "
    icc = bytes(header) + tag_table + mluc
    assert credits._icc_description(icc) == "Display P3"


# ---------- scope, rebuild ----------


def test_folder_limited_reader_sees_only_its_items(client, upload):
    from test_tokens import as_token, make_token

    folder = client.post("/api/folders", json={"name": "Shared"}).json()
    inside = upload("in.png", folder_id=folder["id"])["item"]
    outside = upload("out.png")["item"]
    tok = make_token(client, "reader", ["read"], folder_ids=[folder["id"]])
    r = as_token(client, tok["token"])
    assert r.get(f"/api/items/{inside['id']}/print").status_code == 200
    assert r.get(f"/api/items/{outside['id']}/print").status_code == 404
    assert r.get(f"/api/items/{outside['id']}/credits").status_code == 404
    got = r.get("/api/items", params={"needs_credit": "true"}).json()
    assert ids(got) == {inside["id"]}
    assert r.get("/api/credits/summary").status_code == 404
    assert r.post("/api/credits/clearance", json={"item_ids": [outside["id"]]}).status_code == 404
    plain_reader = as_token(client, make_token(client, "r2", ["read"])["token"])
    assert plain_reader.post("/api/credits/bulk", json={
        "item_ids": [inside["id"]], "provenance": {"credit": "x"}}).status_code == 403
    assert plain_reader.post(f"/api/items/{inside['id']}/credits/autofill").status_code == 403


def test_rebuild_keeps_rights_and_settings(client, upload):
    item = upload("r.png")["item"]
    client.patch(f"/api/items/{item['id']}", json={"provenance": {
        "rights_status": "licensed", "credit_exempt": True, "ai_generator": "Midjourney"}})
    client.patch("/api/credits/settings", json={"clearance_warning": False})
    assert cli.rebuild_index(wipe=True) == 0
    got = client.get(f"/api/items/{item['id']}").json()["provenance"]
    assert got["rights_status"] == "licensed" and got["credit_exempt"] is True
    assert got["ai_generator"] == "Midjourney"
    assert client.get("/api/credits/settings").json() == {"clearance_warning": False}
    assert cli.verify() == 0
    # the XMP sidecar carries them for other tools
    from kist.services import xmp

    side = xmp.read_sidecar(get_settings().items_dir / item["id"] / "original.xmp")
    assert side["provenance"]["rights_status"] == "licensed"
    assert side["provenance"]["credit_exempt"] is True


def test_settings_file_unreadable_is_reported(client):
    (get_settings().state_dir).mkdir(parents=True, exist_ok=True)
    (get_settings().state_dir / "credits.json").write_text("{nope")
    assert credits.check_file()
    assert credits.read_settings() == {"clearance_warning": True}
    assert isinstance(datetime.now(), datetime)


# ---------- credits on outputs ----------


def _credited(client, upload, name="c.png", **prov):
    item = upload(name)["item"]
    client.patch(f"/api/items/{item['id']}", json={"provenance": {
        "creator": "Ann Lee", "credit": "Lee Studio", "license": "CC BY 4.0", **prov}})
    return item


def test_board_exports_carry_credits(client, upload):
    a = _credited(client, upload, "a.png")
    b = upload("b.png")["item"]
    board = client.post("/api/boards", json={"name": "Mood", "item_ids": [a["id"], b["id"]],
                                              "layout": "grid"}).json()
    html = client.get(f"/api/boards/{board['id']}/export?format=html").text
    assert 'class="credits"' in html and "Ann Lee / Lee Studio · CC BY 4.0" in html
    png = client.get(f"/api/boards/{board['id']}/export?format=png&scale=0.5")
    assert png.status_code == 200
    with_band = Image.open(io.BytesIO(png.content)).size
    client.patch(f"/api/items/{a['id']}", json={"provenance": {
        "creator": None, "credit": None, "license": None}})
    plain = Image.open(io.BytesIO(client.get(
        f"/api/boards/{board['id']}/export?format=png&scale=0.5").content)).size
    assert with_band[0] == plain[0] and with_band[1] > plain[1]
    assert 'class="credits"' not in client.get(
        f"/api/boards/{board['id']}/export?format=html").text
    pdf = client.get(f"/api/boards/{board['id']}/export?format=pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_contact_sheet_and_bagit_carry_credits(client, upload):
    import csv
    import zipfile

    from kist import worker

    a = _credited(client, upload, "a.png", rights_status="cleared")
    b = upload("b.png")["item"]
    res = client.post("/api/items/export-images", json={
        "item_ids": [a["id"], b["id"]], "preset": "contact-sheet"})
    assert res.status_code == 200 and res.content.startswith(b"%PDF")
    res = client.post("/api/exports", json={"target": "items", "item_ids": [a["id"], b["id"]]})
    export_id = res.json()["id"]
    while worker.run_one():
        pass
    status = client.get(f"/api/exports/{export_id}").json()
    zf = zipfile.ZipFile(io.BytesIO(client.get(status["download_url"]).content))
    rows = {r["id"]: r for r in csv.DictReader(io.StringIO(zf.read("items.csv").decode()))}
    assert rows[a["id"]]["credit_line"] == "Ann Lee / Lee Studio · CC BY 4.0"
    assert rows[a["id"]]["rights_status"] == "cleared"
    text = zf.read("CREDITS.txt").decode()
    assert "Ann Lee / Lee Studio" in text and "[Cleared]" in text
    assert "No credit recorded" in text and rows[b["id"]]["path"] in text
    assert "CREDITS.txt" in zf.read("tagmanifest-sha256.txt").decode()


def test_share_page_shows_credits(client, upload):
    from fastapi.testclient import TestClient

    folder = client.post("/api/folders", json={"name": "Out"}).json()
    a = _credited(client, upload, "a.png")
    client.patch(f"/api/items/{a['id']}", json={"folder_ids": [folder["id"]]})
    res = client.post("/api/shares", json={"target_kind": "folder", "target_id": folder["id"]})
    assert res.status_code == 201, res.text
    anon = TestClient(client.app)
    page = anon.get(f"/s/{res.json()['slug']}")
    assert page.status_code == 200
    assert '<span class="credit">Ann Lee / Lee Studio · CC BY 4.0</span>' in page.text


def test_present_caption_hook(client, upload):
    a = _credited(client, upload, "a.png", page_title="Tower at dusk")
    got = client.get(f"/api/items/{a['id']}/credits").json()
    assert got["caption"] == {"title": "Tower at dusk",
                              "credit": "Ann Lee / Lee Studio · CC BY 4.0",
                              "rights_status": None}
    assert client.get(f"/api/items/{a['id']}").json()["credit_line"] == "Ann Lee / Lee Studio"
