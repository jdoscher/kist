"""Provenance, the AI-generated flag (XMP / C2PA), XMP sidecars and the iOS
Shortcut response."""

import io
import json
import os
import time

import pytest
from conftest import png_bytes
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from kist import cli
from kist.config import get_settings
from kist.services import ingest, provenance, xmp

XMP_PACKET = """<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"
  xmlns:xmpRights="http://ns.adobe.com/xap/1.0/rights/"
  xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/"
  xmlns:darktable="http://darktable.sf.net/"
  xmp:Rating="{rating}" xmp:Label="Red" photoshop:Credit="ACME Wire"
  xmpRights:WebStatement="https://example.com/rights"
  Iptc4xmpExt:DigitalSourceType="http://cv.iptc.org/newscodes/digitalsourcetype/{dst}"
  darktable:history_end="7">
 <dc:subject><rdf:Bag><rdf:li>tower</rdf:li><rdf:li>night</rdf:li></rdf:Bag></dc:subject>
 <dc:description><rdf:Alt><rdf:li xml:lang="x-default">Shot from the bridge</rdf:li>
 </rdf:Alt></dc:description>
 <dc:creator><rdf:Seq><rdf:li>Ada Lovelace</rdf:li></rdf:Seq></dc:creator>
 <xmpRights:UsageTerms><rdf:Alt><rdf:li xml:lang="x-default">CC-BY-4.0</rdf:li>
 </rdf:Alt></xmpRights:UsageTerms>
</rdf:Description></rdf:RDF></x:xmpmeta>
<?xpacket end="w"?>"""


def packet(rating=4, dst="trainedAlgorithmicMedia") -> str:
    return XMP_PACKET.replace("{rating}", str(rating)).replace("{dst}", dst)


def jpeg_with_xmp(color=(10, 20, 30), **kw) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, "JPEG", xmp=packet(**kw).encode("utf-8"))
    return buf.getvalue()


def jpeg_with_c2pa(color=(40, 50, 60), ai=True) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, "JPEG")
    data = buf.getvalue()
    body = (b"JP\x00\x01\x00\x00\x00\x01" + b"\x00\x00\x00\x40jumb\x00\x00\x00\x18jumdc2pa"
            + b"\x00" * 8 + b"c2pa.actions" + (b" trainedAlgorithmicMedia" if ai else b""))
    seg = b"\xff\xeb" + (len(body) + 2).to_bytes(2, "big") + body
    return data[:2] + seg + data[2:]


def png_with_c2pa() -> bytes:
    import struct
    import zlib

    data = png_bytes((7, 8, 9), (32, 32))
    chunk = b"jumb....c2pa manifest"
    crc = zlib.crc32(b"caBX" + chunk) & 0xFFFFFFFF
    blob = struct.pack(">I", len(chunk)) + b"caBX" + chunk + struct.pack(">I", crc)
    return data[:33] + blob + data[33:]  # right after IHDR


@pytest.fixture
def fake_web(monkeypatch):
    pages: dict[str, tuple[bytes, str]] = {}

    def fake_download(url):
        body, ctype = pages[url]
        staging = get_settings().staging_dir
        staging.mkdir(parents=True, exist_ok=True)
        path = staging / f"dl-{ingest.new_ulid()}"
        path.write_bytes(body)
        return path, url.rsplit("/", 1)[-1] or "download", ctype

    monkeypatch.setattr(ingest, "_download", fake_download)
    return pages


def test_upload_records_how_and_when(client, upload):
    item = upload("a.png")["item"]
    prov = item["provenance"]
    assert prov["method"] == "upload" and prov["captured_at"]
    assert "page_url" not in prov
    with_source = upload("b.png", source_url="https://site.test/page")["item"]
    assert with_source["provenance"]["page_url"] == "https://site.test/page"
    pasted = upload("c.png", provenance=json.dumps({"method": "paste", "creator": "Me"}))["item"]
    assert pasted["provenance"]["method"] == "paste"
    assert pasted["provenance"]["creator"] == "Me"
    bad = client.post("/api/items", files={"file": ("d.png", png_bytes((1, 1, 1)), "image/png")},
                      data={"provenance": json.dumps({"method": "teleport"})})
    assert bad.status_code == 422


def test_url_add_records_page_and_image(client, fake_web):
    fake_web["https://blog.test/post"] = (
        b'<html><head><title>A post</title><meta property="og:image" content="/hero.png">'
        b"</head></html>", "text/html")
    fake_web["https://blog.test/hero.png"] = (png_bytes((9, 9, 9)), "image/png")
    item = client.post("/api/items", json={"url": "https://blog.test/post"}).json()["item"]
    prov = item["provenance"]
    assert prov["method"] == "url"
    assert prov["page_url"] == "https://blog.test/post" == item["source_url"]
    assert prov["image_url"] == "https://blog.test/hero.png"
    assert prov["page_title"] == "A post"


def test_provenance_edits_are_events_and_undoable(client, upload):
    item = upload("a.png", source_url="https://old.test/")["item"]
    res = client.patch(f"/api/items/{item['id']}", json={"provenance": {
        "creator": "Grace", "license": "CC-BY-4.0", "ai_generated": False,
        "page_url": "https://new.test/page"}})
    assert res.status_code == 200, res.text
    got = res.json()
    assert got["source_url"] == "https://new.test/page"
    assert got["provenance"]["creator"] == "Grace" and got["provenance"]["ai_generated"] is False
    kinds = [e["kind"] for e in client.get(f"/api/items/{item['id']}/history").json()]
    assert kinds[0] == "edit"
    # null removes a field
    got = client.patch(f"/api/items/{item['id']}", json={"provenance": {"creator": None}}).json()
    assert "creator" not in got["provenance"]
    batch = client.get("/api/batches").json()[0]["batch_id"]
    assert client.post(f"/api/batches/{batch}/undo").status_code == 200
    got = client.get(f"/api/items/{item['id']}").json()
    assert got["provenance"]["creator"] == "Grace"
    for bad in ({"page_url": "javascript:alert(1)"}, {"nope": 1}, {"ai_generated": "yes"},
                {"geo": {"lat": 200, "lon": 0}}):
        res = client.patch(f"/api/items/{item['id']}", json={"provenance": bad})
        assert res.status_code == 422, bad


def test_filters_by_ai_flag_and_license(client, upload):
    a = upload("a.png")["item"]
    b = upload("b.png")["item"]
    c = upload("c.png")["item"]
    client.patch(f"/api/items/{a['id']}", json={"provenance": {"ai_generated": True,
                                                               "license": "CC0"}})
    client.patch(f"/api/items/{b['id']}", json={"provenance": {"ai_generated": False}})

    def ids(**params):
        return {i["id"] for i in client.get("/api/items", params=params).json()["items"]}

    assert ids(ai_generated="true") == {a["id"]}
    assert ids(ai_generated="false") == {b["id"]}
    assert ids(ai_generated="unknown") == {c["id"]}
    assert ids(license="cc0") == {a["id"]}
    assert ids(license="none") == {b["id"], c["id"]}
    assert client.get("/api/items", params={"ai_generated": "maybe"}).status_code == 422


def test_embedded_xmp_comes_in(client, upload):
    item = upload("x.jpg", data=jpeg_with_xmp(), mime="image/jpeg")["item"]
    assert item["rating"] == 4
    assert {"tower", "night", "label:Red"} <= set(item["tags"])
    assert item["notes"] == "Shot from the bridge"
    prov = item["provenance"]
    assert prov["ai_generated"] is True
    assert prov["creator"] == "Ada Lovelace" and prov["credit"] == "ACME Wire"
    assert prov["license"] == "CC-BY-4.0" and prov["rights_url"] == "https://example.com/rights"
    # the caller's own values win over the file's
    other = upload("y.jpg", data=jpeg_with_xmp((1, 2, 3), rating=2, dst="digitalCapture"),
                   mime="image/jpeg", rating="5", notes="mine")["item"]
    assert other["rating"] == 5 and other["notes"] == "mine"
    assert other["provenance"]["ai_generated"] is False


def test_c2pa_manifests_are_noticed(client, upload):
    ai = upload("c.jpg", data=jpeg_with_c2pa(), mime="image/jpeg")["item"]
    assert ai["provenance"]["c2pa"] is True and ai["provenance"]["ai_generated"] is True
    plain = upload("d.jpg", data=jpeg_with_c2pa((1, 1, 1), ai=False), mime="image/jpeg")["item"]
    assert plain["provenance"]["c2pa"] is True and "ai_generated" not in plain["provenance"]
    png = upload("e.png", data=png_with_c2pa())["item"]
    assert png["provenance"]["c2pa"] is True
    assert "c2pa" not in upload("f.png")["item"]["provenance"]


def test_xmp_parser_refuses_entities():
    evil = ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">&a;</x:xmpmeta>')
    with pytest.raises(ValueError):
        xmp.parse_properties(evil)


def test_sidecar_is_written_and_merged(client, upload):
    item = upload("s.jpg", data=jpeg_with_xmp((5, 5, 5)), mime="image/jpeg")["item"]
    path = get_settings().items_dir / item["id"] / "original.xmp"
    assert path.is_file()
    fields = xmp.read_sidecar(path)
    assert fields["rating"] == 4 and "tower" in fields["tags"]
    client.patch(f"/api/items/{item['id']}", json={"rating": 1, "notes": "new note"})
    client.patch(f"/api/items/{item['id']}/tags", json={"add": ["extra"]})
    fields = xmp.read_sidecar(path)
    assert fields["rating"] == 1 and fields["notes"] == "new note" and "extra" in fields["tags"]
    assert fields["label"] == "Red"
    assert fields["provenance"]["ai_generated"] is True
    # properties other tools wrote are kept
    text = path.read_text()
    path.write_text(text.replace("<rdf:Description ", '<rdf:Description xmlns:darktable='
                                 '"http://darktable.sf.net/" darktable:history_end="9" ', 1))
    client.patch(f"/api/items/{item['id']}", json={"rating": 2})
    text = path.read_text()
    assert 'history_end="9"' in text and xmp.read_sidecar(path)["rating"] == 2
    # not for non-images
    pdf = upload("doc.pdf", data=b"%PDF-1.4\n%%EOF\n", mime="application/pdf")["item"]
    assert not (get_settings().items_dir / pdf["id"] / "original.xmp").exists()


def test_sidecars_can_be_turned_off(client, upload, monkeypatch):
    monkeypatch.setattr(get_settings(), "xmp_sidecars", False)
    item = upload("n.png")["item"]
    assert not (get_settings().items_dir / item["id"] / "original.xmp").exists()


def test_inbox_takes_a_neighbouring_xmp_along(client):
    from kist import worker

    inbox = get_settings().inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)
    photo = inbox / "IMG_0001.png"
    photo.write_bytes(png_bytes((3, 33, 133)))
    side = inbox / "IMG_0001.png.xmp"
    side.write_text(packet(rating=3, dst="digitalCapture"))
    lone = inbox / "orphan.xmp"
    lone.write_text(packet())
    old = time.time() - 60
    for p in (photo, side, lone):
        os.utime(p, (old, old))
    ingest.scan_inbox()
    assert not photo.exists() and not side.exists() and lone.exists()
    assert worker.run_one()
    items = client.get("/api/items").json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["rating"] == 3 and "night" in item["tags"]
    assert item["provenance"]["method"] == "inbox"
    kept = (get_settings().items_dir / item["id"] / "original.xmp").read_text()
    assert 'history_end="7"' in kept  # the sidecar's own properties survive
    assert not list(get_settings().staging_dir.glob("xmpside-*"))


def test_kist_xmp_export_writes_beside_files(client, upload, tmp_path):
    data = png_bytes((99, 1, 1))
    item = upload("round.png", data=data, tags="trip")["item"]
    client.patch(f"/api/items/{item['id']}", json={"rating": 5})
    (tmp_path / "round-export.png").write_bytes(data)
    (tmp_path / "stranger.png").write_bytes(png_bytes((1, 99, 1)))
    assert cli.xmp_cmd(str(tmp_path), rewrite=True) == 0
    fields = xmp.read_sidecar(tmp_path / "round-export.xmp")
    assert fields["rating"] == 5 and fields["tags"] == ["trip"]
    assert not (tmp_path / "stranger.xmp").exists()


def test_provenance_and_source_checks_survive_a_rebuild(client, upload):
    from kist.db import SessionLocal
    from kist.services import linkrot, verify

    item = upload("p.png", source_url="https://gone.test/x")["item"]
    client.patch(f"/api/items/{item['id']}", json={"provenance": {"credit": "Wire",
                                                                  "ai_generated": True}})
    with SessionLocal() as s:
        linkrot.record(s, item["id"], "gone", "https://web.archive.org/web/2020/x")
    with SessionLocal() as s:
        assert verify.run(s).problems == []
    assert cli.rebuild_index(wipe=True) == 0
    got = client.get(f"/api/items/{item['id']}").json()
    assert got["provenance"]["credit"] == "Wire" and got["provenance"]["ai_generated"] is True
    assert got["provenance"]["page_url"] == "https://gone.test/x"
    assert got["source_status"] == "gone"
    assert got["source_wayback_url"] == "https://web.archive.org/web/2020/x"
    with SessionLocal() as s:
        assert verify.run(s).problems == []
    # an older meta.json without the M4 keys is not drift
    meta_path = get_settings().items_dir / item["id"] / "meta.json"
    meta = json.loads(meta_path.read_text())
    assert meta["provenance"]["page_url"] == "https://gone.test/x"
    meta["provenance"]["credit"] = "Someone else"
    meta_path.write_text(json.dumps(meta))
    with SessionLocal() as s:
        assert any("provenance" in p for p in verify.run(s).problems)


def test_ios_shortcut_gets_a_short_answer(client, fake_web, upload):
    fake_web["https://img.test/cat.png"] = (png_bytes((4, 4, 4)), "image/png")
    body = {"url": "https://img.test/cat.png", "title": "A cat", "text": "look at this cat"}
    res = client.post("/api/items", json=body, headers={"Accept": "text/plain"})
    assert res.status_code == 201
    assert res.headers["content-type"].startswith("text/plain")
    assert res.text == "Saved to Kist: cat.png"
    again = client.post("/api/items", json=body, headers={"Accept": "text/plain"})
    assert again.status_code == 200 and again.text == "Already in Kist: cat.png"
    item = client.get("/api/items").json()["items"][0]
    assert item["provenance"]["page_title"] == "A cat"
    assert "look at this cat" in item["notes"]
    up = client.post("/api/items", files={"file": ("p.png", png_bytes((5, 5, 5)), "image/png")},
                     headers={"Accept": "text/plain"})
    assert up.status_code == 201 and up.text == "Saved to Kist: p.png"
    # JSON stays the default
    assert client.post("/api/items", json={"url": "https://img.test/cat.png"}).json()["item"]


def test_shortcut_instructions_are_admin_only(client):
    from test_tokens import as_token, make_token

    res = client.get("/api/shortcut")
    assert res.status_code == 200
    data = res.json()
    assert data["endpoint"].endswith("/api/items") and data["steps"]
    reader = as_token(client, make_token(client, name="r", scopes=["read"])["token"])
    assert reader.get("/api/shortcut").status_code == 403
    adder = as_token(client, make_token(client, name="a", scopes=["add"])["token"])
    assert adder.get("/api/shortcut").status_code == 403


def test_mcp_set_provenance(client, upload):
    from test_tokens import mcp_result

    item = upload("m.png")["item"]
    result = mcp_result(client, "set_provenance", {"id": item["id"], "creator": "Agent Smith",
                                                   "ai_generated": True})
    assert not result.get("isError"), result
    got = client.get(f"/api/items/{item['id']}").json()
    assert got["provenance"]["creator"] == "Agent Smith"


def test_unit_clean_and_detect():
    assert provenance.clean({"captured_at": "2020-01-01T00:00:00Z"})["captured_at"].startswith(
        "2020-01-01T00:00:00")
    assert xmp.ai_from_source_type("http://cv.iptc.org/newscodes/digitalsourcetype/"
                                   "compositeWithTrainedAlgorithmicMedia") is True
    assert xmp.ai_from_source_type("digitalArt") is None
    fields = xmp.read_fields(packet())
    assert fields["provenance"]["digital_source_type"].endswith("trainedAlgorithmicMedia")
    built = xmp.build({"rating": 3, "tags": ["a"], "notes": "n",
                       "provenance": {"ai_generated": True, "page_url": "https://x.test/"}})
    back = xmp.read_fields(built)
    assert back["rating"] == 3 and back["provenance"]["page_url"] == "https://x.test/"
    assert back["provenance"]["ai_generated"] is True
    pi = PngInfo()
    pi.add_itxt("XML:com.adobe.xmp", packet(rating=2))
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "PNG", pnginfo=pi)
    from pathlib import Path

    tmp = Path(get_settings().staging_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    f = tmp / "itxt.png"
    f.write_bytes(buf.getvalue())
    assert xmp.read_embedded(f)["rating"] == 2
    f.unlink()
