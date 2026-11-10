"""Importers, each tested with a small export written in that format's
documented layout (see kist/importers/*.py for the layouts)."""

import io
import json
import socket
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from conftest import png_bytes
from PIL import Image

from kist import cli, worker
from kist.config import get_settings
from kist.db import SessionLocal
from kist.importers import ImportError_, run_import
from kist.services import ingest

XMP = """<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF
 xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description
 xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmp:Rating="5"><dc:subject><rdf:Bag><rdf:li>from-lightroom</rdf:li></rdf:Bag></dc:subject>
</rdf:Description></rdf:RDF></x:xmpmeta>"""


def jpg(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buf, "JPEG")
    return buf.getvalue()


def run(kind, source, **kw):
    with SessionLocal() as s:
        return run_import(s, kind, str(source), **kw)


def items(client, **params):
    return client.get("/api/items", params={"limit": 500, **params}).json()["items"]


def folder_paths(client) -> dict[str, str]:
    folders = {f["id"]: f for f in client.get("/api/folders").json()}

    def path(fid):
        f = folders[fid]
        return (path(f["parent_id"]) + "/" if f.get("parent_id") else "") + f["name"]

    return {fid: path(fid) for fid in folders}


@pytest.fixture
def fake_web(monkeypatch):
    pages: dict[str, tuple[bytes, str]] = {}

    def fake_download(url):
        if url not in pages:
            raise httpx.HTTPStatusError("404", request=httpx.Request("GET", url),
                                        response=httpx.Response(404))
        body, ctype = pages[url]
        staging = get_settings().staging_dir
        staging.mkdir(parents=True, exist_ok=True)
        path = staging / f"dl-{ingest.new_ulid()}"
        path.write_bytes(body)
        return path, url.rsplit("/", 1)[-1] or "download", ctype

    monkeypatch.setattr(ingest, "_download", fake_download)
    return pages


# ---------- Eagle ----------


def eagle_library(root: Path) -> Path:
    lib = root / "Mine.library"
    (lib / "images").mkdir(parents=True)
    (lib / "metadata.json").write_text(json.dumps({
        "folders": [{"id": "F1", "name": "Watches",
                     "children": [{"id": "F2", "name": "Vintage", "children": []}]}],
        "smartFolders": [
            {"name": "No tags", "conditions": [{"rules": [
                {"property": "tags", "method": "empty", "value": []}]}]},
            {"name": "Odd", "conditions": [{"rules": [
                {"property": "width", "method": ">", "value": 10},
                {"property": "tags", "method": "union", "value": ["a"]}]}]},
        ],
    }))

    def item(iid, name, data, **meta):
        d = lib / "images" / f"{iid}.info"
        d.mkdir()
        if data is not None:
            (d / f"{name}.png").write_bytes(data)
        (d / "metadata.json").write_text(json.dumps({"id": iid, "name": name, "ext": "png",
                                                     **meta}))

    omega = png_bytes((1, 2, 3))
    item("A", "omega", omega, tags=["watch"], annotation="Blue dial", url="https://shop.test/o",
         star=4, folders=["F2"], btime=1600000000000, modificationTime=1600000001000)
    item("B", "omega copy", omega, tags=["dup"], folders=["F1"])
    item("C", "gone", png_bytes((9, 9, 9)), isDeleted=True, modificationTime=1600000002000)
    item("D", "missing", None)
    return lib


def test_eagle_dry_run_changes_nothing(client, tmp_path):
    lib = eagle_library(tmp_path)
    report = run("eagle", lib, dry_run=True)
    assert report["found"] == 4
    assert report["imported"] == 2 and report["duplicates"] == 1
    assert report["errors_count"] == 1 and "missing" in report["errors"][0]["error"]
    assert items(client) == [] and client.get("/api/folders").json() == []


def test_eagle_import(client, tmp_path):
    lib = eagle_library(tmp_path)
    report = run("eagle", lib)
    assert report["imported"] == 2 and report["duplicates"] == 1, report
    assert report["folder_path"].startswith("Imported/eagle ")
    paths = folder_paths(client)
    run_folder = report["folder_path"]
    live = items(client)
    assert len(live) == 1
    omega = live[0]
    assert omega["filename"] == "omega.png" and omega["rating"] == 4
    assert omega["notes"] == "Blue dial"
    assert set(omega["tags"]) == {"watch", "dup"}  # the duplicate merged in
    assert sorted(paths[f] for f in omega["folder_ids"]) == [
        f"{run_folder}/Watches", f"{run_folder}/Watches/Vintage"]
    prov = omega["provenance"]
    assert prov["method"] == "import" and prov["imported_from"] == "eagle"
    assert prov["original_id"] == "A" and prov["page_url"] == "https://shop.test/o"
    assert datetime.fromisoformat(omega["created_at"]) == datetime.fromtimestamp(1600000000, UTC)
    trashed = items(client, state="trashed")
    assert [t["filename"] for t in trashed] == ["gone.png"]
    smart = [s["name"] for s in client.get("/api/smart-folders").json()]
    assert smart == [f"{run_folder.split('/', 1)[1]}: No tags"]
    assert report["smart_folders_skipped"][0]["name"] == "Odd"
    # a second run gets its own folder; everything is a duplicate
    again = run("eagle", lib)
    assert again["imported"] == 0 and again["duplicates"] == 3
    assert again["folder_path"] == run_folder + " (2)"
    # the run is one history batch: undo takes it back
    res = client.post(f"/api/batches/{report['batch_id']}/undo")
    assert res.status_code == 200
    assert items(client) == []


# ---------- Pinterest ----------

PIN_HTML = """<html><body><h1>Your Pinterest data</h1>
<h2>Pins</h2>
<div><p>Board name: Watches</p><p>Title: Omega Seamaster</p><p>Details: Blue dial, 1960s</p>
<p>Link: <a href="https://shop.test/omega">https://shop.test/omega</a></p>
<p>Image signature: 0123456789abcdef0123456789abcdef</p>
<p>Created at: 2021-05-01T10:00:00</p></div>
<div><p>Board name: Watches</p><p>Title: Rolex</p><p>Link: https://blog.test/rolex</p></div>
<div><p>Board name: Cars</p><p>Title: no link at all</p><p>Details: nothing</p></div>
</body></html>"""


def test_pinterest_export(client, tmp_path, fake_web):
    src = tmp_path / "pinterest-export.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("pinterest/pins.html", PIN_HTML)
        zf.writestr("pinterest/more.json", json.dumps({"pins": [
            {"title": "JSON pin", "link": "https://cars.test/", "image_url":
             "https://i.pinimg.com/originals/aa/bb/cc/json.jpg", "board": {"name": "Cars"}}]}))
    fake_web["https://i.pinimg.com/originals/01/23/45/0123456789abcdef0123456789abcdef.jpg"] = (
        jpg((10, 10, 10)), "image/jpeg")
    fake_web["https://blog.test/rolex"] = (
        b'<meta property="og:image" content="https://blog.test/rolex.png">', "text/html")
    fake_web["https://blog.test/rolex.png"] = (png_bytes((20, 20, 20)), "image/png")
    fake_web["https://i.pinimg.com/originals/aa/bb/cc/json.jpg"] = (jpg((30, 30, 30)),
                                                                    "image/jpeg")
    dry = run("pinterest", src, dry_run=True)
    assert dry["to_download"] == 3 and dry["found"] == 3
    report = run("pinterest", src)
    assert report["imported"] == 3, report
    paths = folder_paths(client)
    got = {i["provenance"].get("page_url"): i for i in items(client)}
    omega = got["https://shop.test/omega"]
    assert omega["notes"] == "Omega Seamaster\n\nBlue dial, 1960s"
    assert omega["provenance"]["image_url"].endswith("0123456789abcdef0123456789abcdef.jpg")
    assert omega["provenance"]["imported_from"] == "pinterest"
    assert [paths[f] for f in omega["folder_ids"]] == [report["folder_path"] + "/Watches"]
    assert got["https://blog.test/rolex"]["provenance"]["image_url"] == (
        "https://blog.test/rolex.png")
    assert [paths[f] for f in got["https://cars.test/"]["folder_ids"]] == [
        report["folder_path"] + "/Cars"]


# ---------- Are.na ----------


@pytest.fixture
def public_dns(monkeypatch):
    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


BLOCKS = [
    {"id": 1, "class": "Image", "title": "Poster", "description": "Swiss style",
     "image": {"original": {"url": "https://images.are.na/poster.png"}},
     "source": {"url": "https://designer.test/poster"}, "connected_at": "2020-02-02T00:00:00Z"},
    {"id": 2, "class": "Link", "title": "A site",
     "image": {"original": {"url": "https://images.are.na/shot.png"}},
     "source": {"url": "https://site.test/"}},
    {"id": 3, "class": "Text", "content": "just words"},
    {"id": 4, "class": "Attachment", "title": "Spec",
     "attachment": {"url": "https://attachments.are.na/spec.pdf"}},
]


def test_arena_public_channel(client, fake_web, public_dns):
    def handler(request: httpx.Request):
        assert request.headers["host"] == "api.are.na"
        if request.url.path == "/v2/channels/type-posters":
            return httpx.Response(200, json={"title": "Type posters", "length": 4})
        if request.url.path == "/v2/channels/type-posters/contents":
            page = request.url.params.get("page")
            return httpx.Response(200, json={"contents": BLOCKS if page == "1" else []})
        return httpx.Response(404)

    fake_web["https://images.are.na/poster.png"] = (png_bytes((40, 1, 1)), "image/png")
    fake_web["https://images.are.na/shot.png"] = (png_bytes((41, 1, 1)), "image/png")
    fake_web["https://attachments.are.na/spec.pdf"] = (b"%PDF-1.4\n%%EOF\n", "application/pdf")
    report = run("arena", "https://www.are.na/someone/type-posters",
                 transport=httpx.MockTransport(handler))
    assert report["found"] == 4 and report["skipped"] == 1 and report["imported"] == 3, report
    paths = folder_paths(client)
    poster = next(i for i in items(client) if i["provenance"].get("original_id") == "1")
    assert poster["notes"] == "Poster\n\nSwiss style"
    assert poster["provenance"]["page_url"] == "https://designer.test/poster"
    assert [paths[f] for f in poster["folder_ids"]] == [report["folder_path"] + "/Type posters"]
    with pytest.raises(ImportError_):
        run("arena", "https://example.com/not-arena")


def test_arena_channel_zip(client, tmp_path):
    src = tmp_path / "channel.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("type-posters/manifest.json", json.dumps({
            "channel": {"title": "Type posters"},
            "blocks": [{**BLOCKS[0], "file": "assets/poster.png"},
                       {**BLOCKS[2]}, {"id": 9, "class": "Image", "file": "../../etc/passwd"}]}))
        zf.writestr("type-posters/assets/poster.png", png_bytes((50, 5, 5)))
    report = run("arena", src)
    assert report["imported"] == 1 and report["skipped"] == 2, report
    (item,) = items(client)
    assert item["filename"] == "poster.png" and item["provenance"]["original_id"] == "1"


# ---------- Raindrop ----------


def test_raindrop_csv_and_html(client, tmp_path, fake_web):
    csv_text = (
        "id,title,note,excerpt,url,folder,tags,created,cover,highlights,favorite\n"
        "11,Chair,my note,An excerpt,https://furniture.test/chair,Design/Chairs,"
        "\"wood, mid-century\",2022-03-04T05:06:07.000Z,https://cdn.test/chair.jpg,,true\n"
        "12,No cover,,,https://blog.test/lamp,Design,,2022-03-05T00:00:00.000Z,,,false\n"
        "13,Broken,,,notaurl,Design,,,,,false\n"
    )
    (tmp_path / "raindrop.csv").write_text(csv_text)
    fake_web["https://cdn.test/chair.jpg"] = (jpg((60, 6, 6)), "image/jpeg")
    fake_web["https://blog.test/lamp"] = (
        b'<meta property="og:image" content="/lamp.png">', "text/html")
    fake_web["https://blog.test/lamp.png"] = (png_bytes((61, 6, 6)), "image/png")
    report = run("raindrop", tmp_path / "raindrop.csv")
    assert report["imported"] == 2 and report["errors_count"] == 1, report
    paths = folder_paths(client)
    chair = next(i for i in items(client) if i["provenance"]["original_id"] == "11")
    assert set(chair["tags"]) == {"wood", "mid-century", "favorite"}
    assert chair["notes"] == "Chair\n\nmy note\n\nAn excerpt"
    assert chair["provenance"]["page_url"] == "https://furniture.test/chair"
    assert [paths[f] for f in chair["folder_ids"]] == [report["folder_path"] + "/Design/Chairs"]
    html = """<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><p>
    <DT><H3>Inspiration</H3><DL><p>
    <DT><A HREF="https://site.test/x" ADD_DATE="1650000000" TAGS="blue,red"
      DATA-COVER="https://cdn.test/x.png">X</A><DD>a note
    </DL><p></DL>"""
    (tmp_path / "bm.html").write_text(html)
    fake_web["https://cdn.test/x.png"] = (png_bytes((62, 6, 6)), "image/png")
    report = run("raindrop", tmp_path / "bm.html")
    assert report["imported"] == 1, report
    x = next(i for i in items(client) if i["provenance"].get("page_url") == "https://site.test/x")
    assert set(x["tags"]) == {"blue", "red"} and "a note" in x["notes"]
    assert [paths.get(f) or folder_paths(client)[f] for f in x["folder_ids"]] == [
        report["folder_path"] + "/Inspiration"]


# ---------- Google Takeout ----------


def test_google_takeout(client, tmp_path):
    root = tmp_path / "Takeout" / "Google Photos"
    album = root / "Trip to Rome"
    year = root / "Photos from 2020"
    album.mkdir(parents=True)
    year.mkdir(parents=True)
    (album / "metadata.json").write_text(json.dumps({"title": "Trip to Rome"}))
    (album / "IMG_1.jpg").write_bytes(jpg((70, 7, 7)))
    (album / "IMG_1.jpg.supplemental-metadata.json").write_text(json.dumps({
        "title": "IMG_1.jpg", "description": "Forum at dusk",
        "photoTakenTime": {"timestamp": "1590000000"},
        "geoData": {"latitude": 41.89, "longitude": 12.49, "altitude": 0},
        "favorited": True}))
    (year / "IMG_2(1).jpg").write_bytes(jpg((7, 170, 7)))
    (year / "IMG_2.jpg(1).json").write_text(json.dumps({
        "title": "IMG_2(1).jpg", "photoTakenTime": {"timestamp": "1580000000"}}))
    (year / "A_very_long_file_name_that_google_cut_short.jpg").write_bytes(jpg((7, 7, 250)))
    (year / "A_very_long_file_name_that_google_cut_short.jpg.supplemental-met.json").write_text(
        json.dumps({"title": "A_very_long_file_name_that_google_cut_short.jpg",
                    "description": "cut name", "photoTakenTime": {"timestamp": "1570000000"}}))
    report = run("takeout", tmp_path / "Takeout")
    assert report["imported"] == 3 and report["errors_count"] == 0, report
    paths = folder_paths(client)
    by_name = {i["filename"]: i for i in items(client)}
    one = by_name["IMG_1.jpg"]
    assert one["notes"] == "Forum at dusk" and "favorite" in one["tags"]
    assert one["provenance"]["geo"] == {"lat": 41.89, "lon": 12.49}
    assert datetime.fromisoformat(one["created_at"]) == datetime.fromtimestamp(1590000000, UTC)
    assert [paths[f] for f in one["folder_ids"]] == [report["folder_path"] + "/Trip to Rome"]
    two = by_name["IMG_2(1).jpg"]
    assert datetime.fromisoformat(two["created_at"]) == datetime.fromtimestamp(1580000000, UTC)
    assert [paths[f] for f in two["folder_ids"]] == [report["folder_path"]]  # no year folder
    assert by_name["A_very_long_file_name_that_google_cut_short.jpg"]["notes"] == "cut name"


# ---------- Apple Photos (osxphotos) ----------


def test_apple_photos_osxphotos_export(client, tmp_path):
    album = tmp_path / "export" / "Summer"
    album.mkdir(parents=True)
    (album / "IMG_7.jpg").write_bytes(jpg((80, 8, 8)))
    (album / "IMG_7.jpg.json").write_text(json.dumps([{
        "SourceFile": "IMG_7.jpg", "XMP:Title": "Beach", "XMP:Description": "Low tide",
        "XMP:Subject": ["sea", "sand"], "XMP:PersonInImage": ["Ada"],
        "XMP:TagsList": ["sea", "sand"], "XMP:Rating": 3,
        "EXIF:DateTimeOriginal": "2019:07:01 18:30:00", "EXIF:OffsetTimeOriginal": "+02:00",
        "EXIF:GPSLatitude": 43.5, "EXIF:GPSLatitudeRef": "N",
        "EXIF:GPSLongitude": 7.1, "EXIF:GPSLongitudeRef": "W"}]))
    (tmp_path / "export" / "loose.png").write_bytes(png_bytes((81, 8, 8)))
    (tmp_path / "export" / "loose.json").write_text(json.dumps([{"XMP:Subject": "single"}]))
    report = run("apple-photos", tmp_path / "export")
    assert report["imported"] == 2, report
    by_name = {i["filename"]: i for i in items(client)}
    beach = by_name["IMG_7.jpg"]
    assert beach["notes"] == "Beach\n\nLow tide" and beach["rating"] == 3
    assert set(beach["tags"]) == {"sea", "sand", "person:Ada"}
    assert beach["provenance"]["geo"] == {"lat": 43.5, "lon": -7.1}
    assert datetime.fromisoformat(beach["created_at"]) == datetime(2019, 7, 1, 16, 30, tzinfo=UTC)
    assert by_name["loose.png"]["tags"] == ["single"]


# ---------- generic sidecars ----------


def test_sidecar_folder(client, tmp_path):
    (tmp_path / "set" / "sub").mkdir(parents=True)
    (tmp_path / "set" / "a.png").write_bytes(png_bytes((90, 9, 9)))
    (tmp_path / "set" / "a.png.json").write_text(json.dumps({
        "title": "A", "tags": ["x", "y"], "rating": 2, "url": "https://src.test/a",
        "creator": "Jo", "license": "CC0", "ai_generated": True,
        "created_at": "2015-01-01T00:00:00Z"}))
    (tmp_path / "set" / "sub" / "b.png").write_bytes(png_bytes((91, 9, 9)))
    (tmp_path / "set" / "sub" / "b.xmp").write_text(XMP)
    report = run("sidecar", tmp_path / "set", folder="My set")
    assert report["imported"] == 2 and report["folder_path"] == "Imported/My set", report
    by_name = {i["filename"]: i for i in items(client)}
    a = by_name["a.png"]
    assert a["tags"] == ["x", "y"] and a["rating"] == 2 and a["notes"] == "A"
    assert a["provenance"]["creator"] == "Jo" and a["provenance"]["ai_generated"] is True
    assert a["source_url"] == "https://src.test/a"
    b = by_name["b.png"]
    assert b["rating"] == 5 and b["tags"] == ["from-lightroom"]
    assert [folder_paths(client)[f] for f in b["folder_ids"]] == ["Imported/My set/sub"]


# ---------- safety, API, CLI ----------


def test_zip_traversal_is_refused(client, tmp_path):
    src = tmp_path / "evil.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("../../escape.png", png_bytes())
    with pytest.raises(ImportError_):
        run("sidecar", src)
    assert not (tmp_path.parent / "escape.png").exists()
    assert items(client) == []


def test_imports_through_the_api(client, tmp_path):
    from test_tokens import as_token, make_token

    outside = eagle_library(tmp_path)
    res = client.post("/api/imports", json={"kind": "eagle", "source": str(outside)})
    assert res.status_code == 422 and "import directory" in res.text
    assert client.post("/api/imports", json={"kind": "flickr", "source": "x"}).status_code == 422
    import_dir = get_settings().library_dir / "import"
    lib = eagle_library(import_dir)
    res = client.post("/api/imports", json={"kind": "eagle", "source": str(lib),
                                            "dry_run": True})
    assert res.status_code == 202, res.text
    job_id = res.json()["id"]
    while worker.run_one():
        pass
    got = client.get(f"/api/imports/{job_id}").json()
    assert got["status"] == "done" and got["report"]["duplicates"] == 1
    assert got["report"]["dry_run"] is True and items(client) == []
    reader = as_token(client, make_token(client, name="r", scopes=["read"])["token"])
    assert reader.post("/api/imports", json={"kind": "eagle",
                                             "source": str(lib)}).status_code == 403
    agent = as_token(client, make_token(client, name="ag", scopes=["agent"])["token"])
    assert agent.post("/api/imports", json={"kind": "eagle",
                                            "source": str(lib)}).status_code == 403


def test_cli_import(client, tmp_path, capsys):
    lib = eagle_library(tmp_path)
    assert cli.import_cmd("eagle", str(lib), dry_run=True, folder=None) == 2  # 1 error
    out = capsys.readouterr().out
    assert '"duplicates": 1' in out and "dry run" in out
    assert cli.import_cmd("eagle", str(tmp_path / "nothing"), dry_run=False, folder=None) == 1
