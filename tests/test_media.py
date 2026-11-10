"""Milestone 5 through the API: video scrub/cover/frames/clips/proxies,
EXIF filters, RAW, design files, SVG, audio, 3D, fonts, bundles, the
timeline, the map, rebuild-index and access rules."""

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import media_fixtures as F  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not F.HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")


def up(upload, path_or_name, data=None, mime="application/octet-stream", **form):
    if isinstance(path_or_name, Path):
        return upload(path_or_name.name, path_or_name.read_bytes(), mime, **form)["item"]
    return upload(path_or_name, data, mime, **form)["item"]


def run_jobs():
    from kist import worker

    n = 0
    while worker.run_one():
        n += 1
    return n


def lib(item_id):
    from kist.config import get_settings

    return get_settings().items_dir / item_id


# ---------- video ----------


@needs_ffmpeg
def test_video_metadata_scrub_and_filters(client, upload, tmp_path):
    item = up(upload, F.video(tmp_path / "clip.mp4", seconds=3))
    tall = up(upload, F.video(tmp_path / "tall.mp4", size="180x320", audio=False))
    assert item["kind"] == "video" and item["preview_status"] == "ready"
    v = item["media"]["video"]
    assert v["codec"] == "h264" and v["fps"] == 10.0 and v["has_audio"] is True
    assert v["width"] == 320 and v["vertical"] is False
    assert item["media"]["playable"] == "native"
    assert item["media"]["scrub"]["frames"] == 20
    info = client.get(f"/api/items/{item['id']}/scrub.json").json()
    assert len(info["times"]) == 20 and info["cols"] == 5 and info["rows"] == 4
    res = client.get(f"/api/items/{item['id']}/scrub?v={item['preview_rev']}")
    assert res.status_code == 200 and res.headers["content-type"] == "image/webp"
    assert "immutable" in res.headers["cache-control"]

    ids = lambda q: {i["id"] for i in client.get(f"/api/items?{q}").json()["items"]}  # noqa: E731
    assert ids("vertical=true") == {tall["id"]}
    assert ids("has_audio=true") == {item["id"]}
    assert ids("type=video&duration_min=2.5&duration_max=3.5") == {item["id"], tall["id"]}
    assert ids("duration_max=1") == set()


@needs_ffmpeg
def test_cover_frame_and_its_undo(client, upload, tmp_path):
    item = up(upload, F.video(tmp_path / "clip.mp4", seconds=3))
    thumb = lib(item["id"]) / "thumb_256.webp"
    before = thumb.read_bytes()
    res = client.post(f"/api/items/{item['id']}/cover", json={"t": 2.5})
    assert res.status_code == 200, res.text
    after = res.json()
    assert after["media"]["cover_time"] == 2.5
    assert after["preview_rev"] > item["preview_rev"]
    assert thumb.read_bytes() != before
    assert client.post(f"/api/items/{item['id']}/cover", json={"t": 99}).status_code == 422
    hist = client.get(f"/api/items/{item['id']}/history").json()
    cover_ev = next(e for e in hist if e["kind"] == "cover")
    assert cover_ev["after"] == {"cover_time": 2.5}
    undo = client.post(f"/api/batches/{cover_ev['batch_id']}/undo")
    assert undo.status_code == 200 and undo.json()["undone"] == 1, undo.text
    again = client.get(f"/api/items/{item['id']}").json()
    assert "cover_time" not in (again["media"] or {})
    # meta.json carries the choice (rebuild-index keeps it)
    client.post(f"/api/items/{item['id']}/cover", json={"t": 1})
    meta = json.loads((lib(item["id"]) / "meta.json").read_text())
    assert meta["media"]["cover_time"] == 1.0


@needs_ffmpeg
def test_save_frame_makes_a_linked_item(client, upload, tmp_path):
    folder = client.post("/api/folders", json={"name": "Refs"}).json()
    item = up(upload, F.video(tmp_path / "clip.mp4"), tags="motion,logo",
              folder_id=folder["id"])
    res = client.post(f"/api/items/{item['id']}/frames", json={"t": 1.5})
    assert res.status_code == 201, res.text
    frame = res.json()["item"]
    assert frame["mime"] == "image/png" and frame["width"] == 320
    assert frame["tags"] == ["logo", "motion"] and frame["folder_ids"] == [folder["id"]]
    assert frame["media"]["source_item"] == item["id"]
    assert frame["media"]["source_time"] == 1.5
    links = client.get(f"/api/items/{frame['id']}/links").json()
    assert links[0]["kind"] == "derived_from" and links[0]["item"]["id"] == item["id"]
    assert client.post(f"/api/items/{item['id']}/frames", json={"t": 30}).status_code == 422
    png = up(upload, "still.png", F.png(), "image/png")
    assert client.post(f"/api/items/{png['id']}/frames", json={"t": 0}).status_code == 409


@needs_ffmpeg
def test_clips_run_as_jobs(client, upload, tmp_path):
    item = up(upload, F.video(tmp_path / "clip.mp4", seconds=3), tags="ref")
    bad = [({"start": 2, "end": 1}, 422), ({"start": 0, "end": 9}, 422),
           ({"start": 0, "end": 1, "format": "mp3"}, 422),
           ({"start": 0, "end": 1, "format": "avi"}, 422)]
    for body, status in bad:
        assert client.post(f"/api/items/{item['id']}/clip", json=body).status_code == status
    jobs = []
    for fmt in ("mp4", "gif", "webp"):
        res = client.post(f"/api/items/{item['id']}/clip",
                          json={"start": 0.5, "end": 1.5, "format": fmt, "max_side": 160})
        assert res.status_code == 202, res.text
        jobs.append(res.json())
    assert all(j["kind"] == "clip" and j["status"] == "pending" for j in jobs)
    assert run_jobs() == 3
    for job, mime in zip(jobs, ("video/mp4", "image/gif", "image/webp"), strict=True):
        done = client.get(f"/api/jobs/{job['id']}").json()
        assert done["status"] == "done", done
        new = client.get(f"/api/items/{done['result_item_id']}").json()
        assert new["mime"] == mime and new["tags"] == ["ref"]
        assert new["media"]["source_range"] == [0.5, 1.5]
        assert max(new["width"], new["height"]) <= 160
        assert client.get(f"/api/items/{new['id']}/links").json()[0]["kind"] == "derived_from"
    gif = client.get(f"/api/jobs/{jobs[1]['id']}").json()
    assert client.get(f"/api/items/{gif['result_item_id']}").json()["media"]["scrub"]


@needs_ffmpeg
def test_audio_items_waveform_and_clip(client, upload, tmp_path):
    item = up(upload, F.audio(tmp_path / "beep.mp3", seconds=2))
    assert item["kind"] == "audio" and item["preview_status"] == "ready"
    assert item["media"]["audio"]["title"] == "Beep"
    assert item["media"]["audio"]["artist"] == "Tester"
    assert item["duration_s"] == pytest.approx(2.0, abs=0.1)
    wave = client.get(f"/api/items/{item['id']}/waveform").json()
    assert wave["version"] == 2 and 900 <= wave["length"] <= 1001
    ids = {i["id"] for i in client.get("/api/items?type=audio&has_audio=true").json()["items"]}
    assert ids == {item["id"]}
    for fmt in ("wav", "mp3"):
        res = client.post(f"/api/items/{item['id']}/clip",
                          json={"start": 0, "end": 1, "format": fmt})
        assert res.status_code == 202, res.text
    assert client.post(f"/api/items/{item['id']}/clip",
                       json={"start": 0, "end": 1, "format": "gif"}).status_code == 422
    assert run_jobs() == 2
    kinds = {i["mime"] for i in client.get("/api/items?type=audio").json()["items"]}
    assert {"audio/x-wav", "audio/mpeg"} <= kinds
    for ext in ("wav", "flac", "ogg", "m4a", "aiff", "aac"):
        other = up(upload, F.audio(tmp_path / f"t.{ext}", seconds=1))
        assert other["kind"] == "audio" and other["preview_status"] == "ready", ext


@needs_ffmpeg
def test_range_requests_and_proxies(client, upload, tmp_path):
    native = up(upload, F.video(tmp_path / "ok.mp4"))
    res = client.get(f"/api/items/{native['id']}/file", headers={"Range": "bytes=0-99"})
    assert res.status_code == 206 and len(res.content) == 100
    res = client.get(f"/api/items/{native['id']}/stream", headers={"Range": "bytes=10-19"})
    assert res.status_code == 206 and len(res.content) == 10
    assert res.headers["content-type"] == "video/mp4"

    odd = up(upload, F.video(tmp_path / "old.avi", codec="mpeg4"))
    assert odd["media"]["playable"] == "proxy"
    res = client.get(f"/api/items/{odd['id']}/stream")
    assert res.status_code == 202 and res.json()["status"] == "preparing"
    again = client.get(f"/api/items/{odd['id']}/stream")
    assert again.json()["job"]["id"] == res.json()["job"]["id"]  # queued once
    assert run_jobs() == 1
    item = client.get(f"/api/items/{odd['id']}").json()
    assert item["media"]["proxy"]["status"] == "ready"
    assert item["modified_at"] == odd["modified_at"]  # a proxy isn't an edit
    res = client.get(f"/api/items/{odd['id']}/stream", headers={"Range": "bytes=0-9"})
    assert res.status_code == 206 and res.headers["content-type"] == "video/mp4"
    assert (lib(odd["id"]) / "proxy.mp4").read_bytes()[4:8] == b"ftyp"
    aiff = up(upload, F.audio(tmp_path / "a.aiff"))
    assert client.get(f"/api/items/{aiff['id']}/stream").status_code == 202
    run_jobs()
    res = client.get(f"/api/items/{aiff['id']}/stream")
    assert res.status_code == 200 and res.headers["content-type"] == "audio/mp4"


def test_heavy_jobs_wait_for_light_ones(client, clean):
    from kist.db import SessionLocal
    from kist.models import Job
    from kist.services import jobs

    with SessionLocal() as s:
        s.add(Job(kind="proxy", payload={"item_id": "x"}))
        s.add(Job(kind="regen_preview", payload={"item_id": "y"}))
        s.commit()
        assert jobs.claim_next(s).kind == "regen_preview"
        assert jobs.claim_next(s).kind == "proxy"


@needs_ffmpeg
def test_animated_gif_scrub_and_mcp_view_video(client, upload, tmp_path):
    from test_mcp import call, init

    gif = up(upload, F.animated(tmp_path / "a.gif", "GIF"), mime="image/gif")
    assert gif["kind"] == "image" and gif["media"]["animation"]["frames"] == 12
    assert gif["duration_s"] == pytest.approx(1.2)
    webp = up(upload, F.animated(tmp_path / "a.webp", "WEBP"), mime="image/webp")
    assert webp["media"]["scrub"]["frames"] == 12
    vid = up(upload, F.video(tmp_path / "v.mp4"))
    init(client)
    out = call(client, "view_video", {"item_id": vid["id"], "frames": 4})
    content = out["content"][0]
    assert content["type"] == "image" and content["mimeType"] == "image/png"
    import base64

    from PIL import Image

    with Image.open(io.BytesIO(base64.b64decode(content["data"]))) as im:
        assert im.size == (512, 384)
    still = up(upload, "s.png", F.png(), "image/png")
    err = call(client, "view_video", {"item_id": still["id"]})
    assert err["isError"] and "no scrub frames" in err["content"][0]["text"]
    saved = call(client, "save_frame", {"item_id": vid["id"], "t": 1})
    assert not saved.get("isError"), saved


# ---------- photos ----------


def _geo(tmp_path):
    from test_media_unit import write_geonames

    from kist.services import geo

    geo.download(write_geonames(tmp_path / "geonames"))


def test_exif_filters_place_timeline_and_map(client, upload, tmp_path):
    _geo(tmp_path)
    paris = up(upload, "IMG_0001.jpg", F.jpeg(exif=F.exif_bytes()), "image/jpeg")
    sydney = up(upload, "IMG_0002.jpg", F.jpeg(color=(1, 2, 3), exif=F.exif_bytes(
        make="SONY", model="ILCE-7M3", lens="FE 85mm F1.8", taken="2021:06:15 08:00:00",
        gps=(-33.8568, 151.2153))), "image/jpeg")
    plain = up(upload, "plain.png", F.png(), "image/png")
    assert paris["taken_at"].startswith("2023-06-15T14:30:00")
    assert paris["place"] == "Paris, France"
    assert paris["media"]["exif"]["camera"] == "Canon EOS R5"
    assert paris["media"]["place"]["region"] == "Ile-de-France"
    ids = lambda q: [i["id"] for i in client.get(f"/api/items?{q}").json()["items"]]  # noqa: E731
    assert ids("camera=eos") == [paris["id"]]
    assert ids("lens=85mm") == [sydney["id"]]
    assert set(ids("has_gps=true")) == {paris["id"], sydney["id"]}
    assert ids("has_gps=false") == [plain["id"]]
    assert ids("taken_after=2022-01-01T00:00:00Z") == [paris["id"]]
    assert ids("place=australia") == [sydney["id"]]
    assert ids("q=Paris") == [paris["id"]]
    # date taken order: undated items sort by when they were added
    assert ids("order=-taken") == [plain["id"], paris["id"], sydney["id"]]
    assert ids("order=taken") == [sydney["id"], paris["id"], plain["id"]]
    page = client.get("/api/items?order=-taken&limit=1").json()
    nxt = client.get(f"/api/items?order=-taken&limit=1&cursor={page['next_cursor']}").json()
    assert nxt["items"][0]["id"] == paris["id"]

    days = client.get("/api/timeline?group=day").json()
    keys = {d["key"]: d for d in days}
    assert keys["2023-06-15"] == {"key": "2023-06-15", "count": 1, "first_item_id": paris["id"]}
    assert "2021-06-15" in keys
    months = client.get("/api/timeline?group=month&camera=sony").json()
    assert months == [{"key": "2021-06", "count": 1, "first_item_id": sydney["id"]}]
    otd = client.get("/api/on-this-day?day=2025-06-15").json()
    assert [y["year"] for y in otd["years"]] == [2023, 2021]
    assert otd["years"][0]["years_ago"] == 2
    assert otd["years"][0]["items"][0]["id"] == paris["id"]

    world = client.get("/api/map?zoom=1").json()
    assert world["total"] == 2 and len(world["clusters"]) == 2
    europe = client.get("/api/map?bbox=-10,35,30,60&zoom=4").json()
    assert europe["total"] == 1 and europe["clusters"][0]["item_id"] == paris["id"]
    wrap = client.get("/api/map?bbox=150,-40,-170,0&zoom=3").json()  # across 180
    assert wrap["total"] == 1 and wrap["clusters"][0]["item_id"] == sydney["id"]
    assert client.get("/api/map?bbox=1,2,3").status_code == 422
    cfg = client.get("/api/map/config").json()
    assert cfg["place_names"] is True and cfg["tiles_url"] is None
    from kist.config import get_settings

    maps = get_settings().library_dir / "maps"
    maps.mkdir()
    (maps / "basemap.pmtiles").write_bytes(b"PMTiles" + bytes(200))
    assert client.get("/api/map/config").json()["tiles_url"] == "/api/map/basemap.pmtiles"
    res = client.get("/api/map/basemap.pmtiles", headers={"Range": "bytes=0-6"})
    assert res.status_code == 206 and res.content == b"PMTiles"


def test_raw_files_preview_from_their_embedded_jpeg(client, upload, tmp_path):
    taken = "2022:08:01 09:15:30"
    nef = up(upload, F.tiff_raw(tmp_path / "DSC_0042.NEF", F.jpeg(size=(1600, 1200)),
                                taken=taken))
    assert nef["mime"] == "image/x-nikon-nef" and nef["kind"] == "image"
    assert nef["preview_status"] == "ready" and nef["width"] == 1600
    assert nef["phash"] is not None
    for name in ("display.webp", "display_2048.webp", "thumb_1024.webp"):
        assert (lib(nef["id"]) / name).is_file(), name
    assert nef["media"]["exif"]["camera"] == "NIKON Z 6"
    assert nef["media"]["raw"]["format"] == "nef"
    jpg = up(upload, "DSC_0042.JPG", F.jpeg(exif=F.exif_bytes(
        make="NIKON CORPORATION", model="NIKON Z 6", taken=taken, gps=None)), "image/jpeg")
    from kist.db import SessionLocal
    from kist.models import Item

    with SessionLocal() as s:
        keys = {s.get(Item, i).raw_pair_key for i in (nef["id"], jpg["id"])}
    assert keys == {"dsc_0042|2022-08-01T09:15:30"}
    cr3 = up(upload, F.cr3(tmp_path / "IMG_1.CR3", F.jpeg(size=(1620, 1080))))
    assert cr3["mime"] == "image/x-canon-cr3" and cr3["width"] == 1620
    raf = up(upload, F.raf(tmp_path / "DSCF1.RAF", F.jpeg()))
    assert raf["preview_status"] == "ready"


# ---------- design, SVG, 3D, fonts ----------


def test_design_files_get_their_embedded_previews(client, upload, tmp_path):
    psd = up(upload, F.psd(tmp_path / "art.psd", size=(400, 300)))
    assert psd["kind"] == "design" and psd["preview_status"] == "ready"
    assert psd["width"] == 400 and psd["media"]["design"]["doc_width"] == 400
    res = client.get(f"/api/items/{psd['id']}/file")
    assert res.headers["content-disposition"].startswith("attachment")
    sketch = up(upload, F.zip_with(tmp_path / "ui.sketch", {
        "document.json": b"{}", "previews/preview.png": F.png((9, 9, 9), (300, 200))}))
    assert sketch["mime"] == "application/x-sketch" and sketch["width"] == 300
    ids = {i["id"] for i in client.get("/api/items?type=design").json()["items"]}
    assert ids == {psd["id"], sketch["id"]}

    fig = up(upload, "board.fig", b"fig-kiwi\x0f\x00\x00\x00" + b"\x00" * 64)
    assert fig["kind"] == "design" and fig["preview_status"] == "ready"
    assert (lib(fig["id"]) / "preview_icon").is_file()
    assert fig["phash"] is None and fig["dominant_colors"] is None  # icons aren't compared
    poster = F.png((200, 10, 10), (640, 480))
    res = client.post(f"/api/items/{fig['id']}/poster", files={"file": ("p.png", poster,
                                                                          "image/png")})
    assert res.status_code == 200, res.text
    after = res.json()
    assert after["width"] == 640 and after["phash"] is not None
    assert after["preview_rev"] > fig["preview_rev"]
    assert not (lib(fig["id"]) / "preview_icon").exists()
    # the poster survives a preview retry
    client.post(f"/api/items/{fig['id']}/preview")
    run_jobs()
    assert client.get(f"/api/items/{fig['id']}").json()["width"] == 640
    bad = client.post(f"/api/items/{fig['id']}/poster", content=b"<svg/>",
                      headers={"content-type": "image/svg+xml"})
    assert bad.status_code == 422
    photo = up(upload, "p.png", F.png(), "image/png")
    assert client.post(f"/api/items/{photo['id']}/poster", content=poster,
                       headers={"content-type": "image/png"}).status_code == 409


def test_svg_is_sanitized_for_copy_and_thumbnails(client, upload):
    from test_media_unit import EVIL_SVG

    item = up(upload, "logo.svg", EVIL_SVG, "image/svg+xml")
    assert item["preview_status"] == "ready", item["preview_error"]
    hexes = [c["hex"] for c in item["media"]["svg"]["colors"]]
    assert "#ff0000" in hexes and "#00ff00" in hexes
    res = client.get(f"/api/items/{item['id']}/svg")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/svg+xml")
    assert "sandbox" in res.headers["content-security-policy"]
    assert "<script" not in res.text and "evil.example" not in res.text
    png = up(upload, "x.png", F.png(), "image/png")
    assert client.get(f"/api/items/{png['id']}/svg").status_code == 409


def test_models_get_stats_conversion_and_posters(client, upload, tmp_path):
    from kist.services import models3d

    glb = up(upload, F.glb(tmp_path / "chair.glb"))
    assert glb["kind"] == "model" and glb["preview_status"] == "ready"
    assert glb["media"]["model"]["triangles"] == 1
    res = client.get(f"/api/items/{glb['id']}/model")
    assert res.status_code == 200 and res.content[:4] == b"glTF"
    obj = up(upload, "mesh.obj", F.OBJ, "text/plain")
    assert obj["mime"] == "model/obj"
    res = client.get(f"/api/items/{obj['id']}/model")
    if models3d.trimesh_available():
        assert obj["media"]["model"]["glb"] is True
        assert res.status_code == 200 and res.content[:4] == b"glTF"
    else:
        assert res.status_code == 404
    fbx = up(upload, "rig.fbx", b"Kaydara FBX Binary  \x00\x1a\x00" + b"\x00" * 64)
    assert fbx["kind"] == "model" and client.get(f"/api/items/{fbx['id']}/model").status_code \
        == 404
    res = client.post(f"/api/items/{glb['id']}/poster", content=F.png((0, 90, 0), (800, 600)),
                      headers={"content-type": "image/png"})
    assert res.status_code == 200 and res.json()["width"] == 800
    hist = client.get(f"/api/items/{glb['id']}/history").json()
    assert hist[0]["kind"] == "poster"
    ids = {i["id"] for i in client.get("/api/items?type=model").json()["items"]}
    assert ids == {glb["id"], obj["id"], fbx["id"]}


def test_fonts(client, upload, tmp_path):
    pytest.importorskip("fontTools")
    font = up(upload, F.font(tmp_path / "Kist-Regular.ttf"))
    assert font["kind"] == "font" and font["mime"] == "font/ttf"
    assert font["preview_status"] == "none"
    assert font["media"]["font"]["family"] == "Kist Test"
    info = client.get(f"/api/items/{font['id']}/font-info").json()
    assert info["glyph_count"] == 53 and info["scripts"] == ["Latin"]
    glyphs = client.get(f"/api/items/{font['id']}/glyphs?page=0").json()
    assert glyphs["total"] == 52 and glyphs["pages"] == 1
    res = client.get(f"/api/items/{font['id']}/file")
    assert res.headers["content-type"] == "font/ttf"
    assert res.headers["cross-origin-resource-policy"] == "same-origin"
    png = up(upload, "x.png", F.png(), "image/png")
    assert client.get(f"/api/items/{png['id']}/font-info").status_code == 409


# ---------- bundles ----------


def test_bundles_list_preview_and_serve_files(client, upload, tmp_path):
    z = F.zip_with(tmp_path / "rock-kit.zip", {
        "rock/model.glb": F.glb(tmp_path / "m.glb").read_bytes(),
        "rock/textures/rock_albedo.png": F.png((120, 100, 80), (256, 256)),
        "rock/textures/rock_normal.png": F.png((128, 128, 255), (256, 256)),
        "rock/LICENSE.txt": b"CC0",
        "rock/page.html": b"<script>alert(1)</script>",
    })
    item = up(upload, z, mime="application/zip")
    assert item["kind"] == "bundle" and item["preview_status"] == "ready"
    assert item["media"]["bundle"]["cover"] == "rock/textures/rock_albedo.png"
    assert item["media"]["bundle"]["model"] == "rock/model.glb"
    assert item["width"] == 256
    manifest = client.get(f"/api/items/{item['id']}/bundle").json()
    assert manifest["count"] == 5 and manifest["kinds"]["image"] == 2
    base = f"/api/items/{item['id']}/bundle"
    res = client.get(f"{base}/rock/textures/rock_albedo.png")
    assert res.status_code == 200 and res.headers["content-type"] == "image/png"
    assert res.headers["content-disposition"].startswith("inline")
    res = client.get(f"{base}/rock/page.html")
    assert res.headers["content-type"] == "application/octet-stream"
    assert res.headers["content-disposition"].startswith("attachment")
    assert res.headers["content-security-policy"] == "sandbox"
    assert client.get(f"{base}/rock/missing.png").status_code == 404
    for bad in ("..%2f..%2fmeta.json", "rock/..%2F..%2Fx", "%2Fetc%2Fpasswd",
                "rock%5C..%5Cx", "rock//textures/rock_albedo.png"):
        res = client.get(f"{base}/{bad}")
        assert res.status_code in (400, 404), bad
        assert b"CC0" not in res.content and b'"sha256"' not in res.content


def test_zip_with_traversal_names_is_stored_but_never_extracted(client, upload, tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../escape.txt", b"pwned")
        zf.writestr("/abs.txt", b"pwned")
        zf.writestr("ok.png", F.png())
    item = up(upload, z, mime="application/zip")
    manifest = client.get(f"/api/items/{item['id']}/bundle").json()
    safe = {e["path"]: e["safe"] for e in manifest["entries"]}
    assert safe == {"../../escape.txt": False, "/abs.txt": False, "ok.png": True}
    from kist.config import get_settings

    assert not (get_settings().library_dir / "escape.txt").exists()
    assert not (get_settings().items_dir / "escape.txt").exists()


# ---------- durability and access ----------


@needs_ffmpeg
def test_rebuild_index_restores_media(client, upload, tmp_path):
    from kist import cli

    _geo(tmp_path)
    photo = up(upload, "IMG_9.jpg", F.jpeg(exif=F.exif_bytes()), "image/jpeg")
    vid = up(upload, F.video(tmp_path / "v.mp4"))
    client.post(f"/api/items/{vid['id']}/cover", json={"t": 2})
    assert cli.rebuild_index(wipe=True) == 0
    back = client.get(f"/api/items/{photo['id']}").json()
    assert back["place"] == "Paris, France" and back["taken_at"] == photo["taken_at"]
    assert back["media"] == photo["media"]
    ids = [i["id"] for i in client.get("/api/items?camera=canon&has_gps=true").json()["items"]]
    assert ids == [photo["id"]]
    v = client.get(f"/api/items/{vid['id']}").json()
    assert v["media"]["cover_time"] == 2.0 and v["media"]["scrub"]["frames"] == 20
    assert [i["id"] for i in client.get("/api/items?has_audio=true").json()["items"]] == \
        [vid["id"]]


def test_media_backfill_upgrades_older_items(client, upload, tmp_path):
    import sqlalchemy as sa

    from kist import media_cli
    from kist.db import SessionLocal
    from kist.models import Item

    photo = up(upload, "old.jpg", F.jpeg(exif=F.exif_bytes()), "image/jpeg")
    sketch = up(upload, F.zip_with(tmp_path / "old.sketch",
                                   {"previews/preview.png": F.png()}))
    with SessionLocal() as s:  # as Kist 1.x left them
        s.execute(sa.update(Item).values(media=None, taken_at=None, camera=None,
                                         has_gps=False))
        s.execute(sa.update(Item).where(Item.id == sketch["id"])
                  .values(mime="application/zip"))
        s.commit()
    assert client.get("/api/items?has_gps=true").json()["items"] == []
    assert media_cli.media_backfill(None, False) == 0
    assert client.get(f"/api/items/{photo['id']}").json()["media"]["exif"]["camera"]
    back = client.get(f"/api/items/{sketch['id']}").json()
    assert back["mime"] == "application/x-sketch" and back["kind"] == "design"
    assert media_cli.media_backfill(None, False) == 0  # nothing left to do


def test_media_routes_follow_token_scopes(client, upload, tmp_path):
    folder = client.post("/api/folders", json={"name": "Shared"}).json()
    inside = up(upload, "in.jpg", F.jpeg(exif=F.exif_bytes()), "image/jpeg",
                folder_id=folder["id"])
    outside = up(upload, "out.jpg", F.jpeg(color=(9, 9, 9), exif=F.exif_bytes()), "image/jpeg")
    reader = client.post("/api/tokens", json={"name": "r", "scopes": ["read"]}).json()["token"]
    limited = client.post("/api/tokens", json={
        "name": "fam", "scopes": ["read"], "folder_ids": [folder["id"]]}).json()["token"]
    h = {"Authorization": f"Bearer {reader}"}
    assert client.get("/api/timeline", headers=h).status_code == 200
    assert client.get("/api/map", headers=h).status_code == 200
    assert client.post(f"/api/items/{inside['id']}/poster", content=b"x",
                       headers=h).status_code == 403
    assert client.post(f"/api/items/{inside['id']}/clip", json={"start": 0, "end": 1},
                       headers=h).status_code == 403
    hl = {"Authorization": f"Bearer {limited}"}
    days = client.get("/api/timeline?group=day", headers=hl).json()
    assert days == [{"key": "2023-06-15", "count": 1, "first_item_id": inside["id"]}]
    assert client.get("/api/map", headers=hl).json()["total"] == 1
    assert client.get(f"/api/items/{outside['id']}/scrub.json", headers=hl).status_code == 404
    assert client.get(f"/api/items/{outside['id']}/svg", headers=hl).status_code == 404


def test_text_pretending_to_be_media_never_reaches_ffmpeg(client, upload):
    playlist = b"#EXTM3U\n#EXTINF:1,\nfile:///etc/passwd\n"
    for name in ("x.mp3", "x.mp4", "x.wav"):
        item = up(upload, name, playlist, "audio/mpeg")
        assert item["kind"] == "other" and item["preview_status"] == "none"
        assert item["media"] is None
