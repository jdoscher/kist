"""Milestone 5 units: format detection, SVG sanitizing, bundles, RAW,
design-file previews, 3D stats, fonts, ffmpeg arguments, geocoding, EXIF.
No database needed."""

import io
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import media_fixtures as F  # noqa: E402

from kist.filetypes import kind_for, serve_inline  # noqa: E402
from kist.services import (  # noqa: E402
    bundles,
    design_thumbs,
    fonts,
    formats,
    geo,
    media,
    models3d,
    raw,
    svg,
    timeline,
    video,
)

needs_ffmpeg = pytest.mark.skipif(not F.HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")


def sniff(path: Path) -> str:
    from kist.services.ingest import sniff_mime

    return formats.refine_mime(path, sniff_mime(path), path.suffix.lstrip("."))


# ---------- detection ----------


def test_formats_are_named_from_their_content(tmp_path):
    prev = F.jpeg(size=(120, 80))
    cases = {
        "a.nef": (F.tiff_raw(tmp_path / "a.nef", prev), "image/x-nikon-nef", "image"),
        "a.cr3": (F.cr3(tmp_path / "a.cr3", prev), "image/x-canon-cr3", "image"),
        "a.raf": (F.raf(tmp_path / "a.raf", prev), "image/x-fuji-raf", "image"),
        "a.psd": (F.psd(tmp_path / "a.psd"), "image/vnd.adobe.photoshop", "design"),
        "a.psb": (F.psd(tmp_path / "a.psb", version=2), "application/x-psb", "design"),
        "a.sketch": (F.zip_with(tmp_path / "a.sketch", {"previews/preview.png": F.png()}),
                     "application/x-sketch", "design"),
        "a.afdesign": (F.affinity(tmp_path / "a.afdesign"), "application/x-affinity", "design"),
        "a.blend": (F.blend(tmp_path / "a.blend"), "application/x-blender", "design"),
        "a.glb": (F.glb(tmp_path / "a.glb"), "model/gltf-binary", "model"),
        "a.stl": (F.stl_binary(tmp_path / "a.stl"), "model/stl", "model"),
        "a.zip": (F.zip_with(tmp_path / "a.zip", {"x.png": F.png()}), "application/zip",
                  "bundle"),
    }
    for name, (path, mime, kind) in cases.items():
        got = sniff(path)
        assert got == mime, name
        assert kind_for(got, path.suffix[1:]) == kind, name
    obj = tmp_path / "a.obj"
    obj.write_bytes(F.OBJ)
    assert sniff(obj) == "model/obj"
    ply = tmp_path / "a.ply"
    ply.write_bytes(F.PLY)
    assert sniff(ply) == "model/x-ply"


def test_a_name_alone_never_changes_the_type(tmp_path):
    # a PNG named .cr2, a plain zip named .sketch, text named .glb
    fake_raw = tmp_path / "x.cr2"
    fake_raw.write_bytes(F.png())
    assert sniff(fake_raw) == "image/png"
    plain = F.zip_with(tmp_path / "x.sketch", {"readme.txt": b"hi"})
    assert sniff(plain) == "application/zip"
    fake_glb = tmp_path / "x.glb"
    fake_glb.write_bytes(b"<html>not a model</html>")
    assert kind_for(sniff(fake_glb), "glb") == "other"
    html_obj = tmp_path / "x.obj"
    html_obj.write_bytes(b"<html><script>alert(1)</script></html>")
    assert not sniff(html_obj).startswith("model/")


def test_design_files_are_downloads_not_pages():
    assert not serve_inline("image/vnd.adobe.photoshop")
    assert not serve_inline("application/postscript")
    assert serve_inline("audio/mpeg")
    for mime in ("audio/flac", "model/stl", "font/woff2", "application/zip",
                 "application/x-sketch"):
        assert kind_for(mime, "x") in ("audio", "model", "font", "bundle", "design")


# ---------- SVG ----------

EVIL_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" viewBox="0 0 100 50"
     onload="alert(1)" inkscape:version="1.0">
  <script>alert(document.cookie)</script>
  <style>@import url(https://evil.example/x.css); .a { fill: url(https://evil.example/p) }
    .b { fill: #ABC; stroke: rgb(0, 128, 255) }</style>
  <foreignObject width="10" height="10"><iframe src="https://evil.example"/></foreignObject>
  <defs><linearGradient id="g"><stop offset="0" stop-color="gold"/></linearGradient></defs>
  <a href="javascript:alert(1)"><rect width="10" height="10" fill="#FF0000"/></a>
  <use xlink:href="https://evil.example/sprite.svg#x"/>
  <use href="#g"/>
  <image href="file:///etc/passwd"/>
  <image href="data:image/png;base64,iVBORw0KGgo="/>
  <rect width="5" height="5" fill="url(#g)" style="stroke: url(http://x/y); fill:#00ff00"
        onclick="steal()"/>
  <animate attributeName="href" to="javascript:alert(1)"/>
  <inkscape:path d="M0 0"/>
</svg>"""


def test_svg_sanitizer_removes_script_and_external_references():
    clean = svg.sanitize(EVIL_SVG)
    low = clean.lower()
    for bad in ("<script", "alert(", "onload", "onclick", "foreignobject", "iframe",
                "evil.example", "javascript:", "file://", "@import", "http://x", "inkscape"):
        assert bad not in low, bad
    assert 'href="#g"' in clean
    assert "data:image/png;base64" in clean
    assert 'fill="url(#g)"' in clean
    # still a valid SVG that renders
    import xml.etree.ElementTree as ET

    assert ET.fromstring(clean).tag.endswith("svg")


def test_svg_with_entities_or_doctype_is_refused():
    bomb = (b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa">'
            b'<!ENTITY b "&a;&a;&a;&a;&a;&a;">]><svg xmlns="http://www.w3.org/2000/svg">'
            b"<text>&b;</text></svg>")
    with pytest.raises(svg.SvgError):
        svg.sanitize(bomb)
    with pytest.raises(svg.SvgError):
        svg.sanitize(b"<html><body/></html>")
    with pytest.raises(svg.SvgError):
        svg.sanitize(b"<svg")


def test_svg_colors_are_exact():
    text = svg.sanitize(EVIL_SVG)
    hexes = [c["hex"] for c in svg.colors(text)]
    for want in ("#ff0000", "#00ff00", "#aabbcc", "#0080ff", "#ffd700"):
        assert want in hexes
    assert svg.normalize_color("none") is None
    assert svg.normalize_color("currentColor") is None
    assert svg.normalize_color("rgb(100%, 0%, 0%)") == "#ff0000"


def test_svg_without_xmlns_still_renders(tmp_path):
    text = svg.sanitize(b'<svg width="20" height="10"><rect width="20" height="10" '
                        b'fill="red"/></svg>')
    out = tmp_path / "x.png"
    svg.render_png(text, out)
    from PIL import Image

    with Image.open(out) as im:
        assert im.width == 2 * im.height


# ---------- bundles ----------


@pytest.mark.parametrize("bad", [
    "../etc/passwd", "a/../../b", "/etc/passwd", "C:/Windows/win.ini", "a\\..\\b",
    "a/./b", "", "a\x00b", "a//b",
])
def test_bundle_paths_that_leave_the_zip_are_refused(bad):
    with pytest.raises(bundles.BundleError):
        bundles.check_path(bad)


def test_bundle_manifest_and_cover(tmp_path):
    z = F.zip_with(tmp_path / "kit.zip", {
        "kit/model.gltf": b"{}", "kit/tex/rock_normal.png": F.png(),
        "kit/tex/rock_basecolor.png": F.png(), "kit/LICENSE": b"CC0",
        "__MACOSX/._x.png": b"junk", "kit/preview.jpg": F.jpeg(),
    })
    data = bundles.manifest(z)
    assert data["count"] == 6
    assert data["cover"] == "kit/preview.jpg"
    assert data["model"] == "kit/model.gltf"
    assert data["docs"] == ["kit/LICENSE"]
    blob, mime = bundles.read_entry(z, "kit/tex/rock_normal.png")
    assert mime == "image/png" and blob.startswith(b"\x89PNG")
    with pytest.raises(FileNotFoundError):
        bundles.read_entry(z, "kit/missing.png")


def test_zip_bombs_hit_the_caps(tmp_path, monkeypatch):
    from kist.config import get_settings

    z = tmp_path / "bomb.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zeros.bin", b"\x00" * (3 * 1024 * 1024))  # ~3 KB compressed
        for i in range(5):
            zf.writestr(f"f{i}.txt", b"x")
    settings = get_settings()
    monkeypatch.setattr(settings, "bundle_max_entry_mb", 1)
    data = bundles.manifest(z)
    assert next(e for e in data["entries"] if e["path"] == "zeros.bin")["too_large"]
    with pytest.raises(bundles.BundleError):
        bundles.read_entry(z, "zeros.bin")
    monkeypatch.setattr(settings, "bundle_max_total_mb", 2)
    with pytest.raises(bundles.BundleError):
        bundles.manifest(z)
    monkeypatch.setattr(settings, "bundle_max_total_mb", 100)
    monkeypatch.setattr(settings, "bundle_max_entries", 3)
    with pytest.raises(bundles.BundleError):
        bundles.manifest(z)


def test_design_zip_entries_are_capped(tmp_path):
    z = F.zip_with(tmp_path / "big.sketch", {"previews/preview.png": b"\x00" * 4096})
    with pytest.raises(ValueError):
        design_thumbs.read_zip_entry(z, ["previews/preview.png"], limit=1024)


# ---------- RAW ----------


def test_raw_previews_come_from_the_embedded_jpeg(tmp_path):
    from PIL import Image

    big = F.jpeg(size=(640, 480))
    files = [F.tiff_raw(tmp_path / "a.nef", big), F.tiff_raw(tmp_path / "a.cr2", big),
             F.tiff_raw(tmp_path / "a.orf", big, header=b"IIRO"),
             F.tiff_raw(tmp_path / "a.rw2", big, header=b"IIU\x00"),
             F.cr3(tmp_path / "a.cr3", big), F.raf(tmp_path / "a.raf", big)]
    for path in files:
        data = raw.extract_preview(path)
        with Image.open(io.BytesIO(data)) as im:
            assert im.size == (640, 480), path.name  # the largest one, not the thumbnail


def test_raw_preview_scan_fallback_and_failure(tmp_path):
    odd = tmp_path / "odd.pef"
    odd.write_bytes(b"\x00" * 1000 + F.jpeg(size=(200, 100)) + b"\x00" * 500)
    from PIL import Image

    with Image.open(io.BytesIO(raw.extract_preview(odd))) as im:
        assert im.size == (200, 100)
    empty = tmp_path / "none.dng"
    empty.write_bytes(b"II*\x00" + b"\x00" * 100)
    with pytest.raises(RuntimeError):
        raw.extract_preview(empty)


def test_raw_exif_without_exiftool(tmp_path, monkeypatch):
    from kist.config import get_settings

    monkeypatch.setattr(get_settings(), "exiftool", False)
    nef = F.tiff_raw(tmp_path / "a.nef", F.jpeg())
    data = media.exif_raw(nef)
    assert data["camera"] == "NIKON Z 6"
    assert data["taken_at"] == "2022-08-01T09:15:30"
    assert data["iso"] == 800 and data["aperture"] == 4.0 and data["shutter_text"] == "1/500"
    cr3 = media.exif_raw(F.cr3(tmp_path / "a.cr3", F.jpeg()))
    assert cr3["camera"] == "Canon EOS R6" and cr3["iso"] == 1600


@pytest.mark.skipif(not shutil.which("exiftool"), reason="exiftool not installed")
def test_raw_exif_with_exiftool(tmp_path):
    nef = F.tiff_raw(tmp_path / "a.nef", F.jpeg())
    data = media.exif_exiftool(nef)
    assert data["make"] == "NIKON CORPORATION" and data["iso"] == 800


def test_camera_names():
    assert media.camera_name("Canon", "Canon EOS R5") == "Canon EOS R5"
    assert media.camera_name("SONY", "ILCE-7M3") == "SONY ILCE-7M3"
    assert media.camera_name("NIKON CORPORATION", "NIKON Z 6") == "NIKON Z 6"
    assert media.camera_name(None, "X100V") == "X100V"


def test_exif_from_a_jpeg():
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
        f.write(F.jpeg(exif=F.exif_bytes(gps=(-33.8568, 151.2153))))
        f.flush()
        data = media.exif_pillow(Path(f.name))
    assert data["lens"] == "RF24-70mm F2.8 L IS USM"
    assert data["focal_length"] == 50.0 and data["iso"] == 400
    assert data["lat"] == pytest.approx(-33.8568, abs=1e-4)
    assert data["lon"] == pytest.approx(151.2153, abs=1e-4)


# ---------- design files ----------


def _extract(path: Path, tmp_path: Path):
    from PIL import Image

    mime = sniff(path)
    out, info = design_thumbs.extract(path, mime, tmp_path)
    with Image.open(out) as im:
        return im.size, im.convert("RGB").getpixel((im.width // 2, im.height // 2)), info


def test_design_previews(tmp_path):
    size, px, info = _extract(F.psd(tmp_path / "a.psd", color=(10, 200, 30)), tmp_path)
    assert size == (40, 30) and px == (10, 200, 30) and info["doc_width"] == 40
    # PSB: Pillow can't composite it, so the thumbnail resource is used
    size, _px, info = _extract(F.psd(tmp_path / "b.psb", version=2,
                                     thumb=F.jpeg(size=(80, 60))), tmp_path)
    assert size == (80, 60) and info["format"] == "psb"
    for name, entries in {
        "a.sketch": {"previews/preview.png": F.png((1, 2, 3), (30, 20))},
        "a.procreate": {"QuickLook/Thumbnail.png": F.png((1, 2, 3), (30, 20))},
        "a.kra": {"mergedimage.png": F.png((1, 2, 3), (30, 20))},
        "a.ora": {"stack.xml": b"<image/>", "mergedimage.png": F.png((1, 2, 3), (30, 20))},
    }.items():
        size, px, _ = _extract(F.zip_with(tmp_path / name, entries), tmp_path)
        assert (size, px) == ((30, 20), (1, 2, 3)), name
    size, px, _ = _extract(F.affinity(tmp_path / "a.afdesign"), tmp_path)
    assert size == (50, 40) and px == (0, 0, 255)
    size, px, _ = _extract(F.blend(tmp_path / "a.blend"), tmp_path)
    assert size == (16, 12) and px == (255, 128, 0)
    size, px, _ = _extract(F.dos_eps(tmp_path / "a.eps"), tmp_path)
    assert size == (30, 20) and px == (90, 10, 160)
    (tmp_path / "a.ai").write_bytes(F.pdf_bytes())
    size, px, _ = _extract(tmp_path / "a.ai", tmp_path)
    assert px[0] > 200 and px[1] < 50


def test_epsi_preview_ink_is_black(tmp_path):
    from PIL import Image

    out, _ = design_thumbs.extract(F.epsi(tmp_path / "x.eps"), "application/postscript",
                                   tmp_path)
    with Image.open(out) as im:
        assert im.size == (8, 2)
        assert im.getpixel((0, 0)) == 0 and im.getpixel((0, 1)) == 255


def test_gzipped_blend_and_figma_without_preview(tmp_path):
    import gzip

    plain = F.blend(tmp_path / "p.blend").read_bytes()
    gz = tmp_path / "g.blend"
    gz.write_bytes(gzip.compress(plain))
    assert sniff(gz) == "application/x-blender"
    size, _px, _ = _extract(gz, tmp_path)
    assert size == (16, 12)
    fig = tmp_path / "a.fig"
    fig.write_bytes(b"fig-kiwi\x0f\x00\x00\x00" + b"\x00" * 64)
    assert sniff(fig) == "application/x-figma"
    with pytest.raises(design_thumbs.NoPreview):
        design_thumbs.extract(fig, "application/x-figma", tmp_path)
    figzip = F.zip_with(tmp_path / "b.fig", {"canvas.fig": b"fig-kiwi", "thumbnail.png":
                                             F.png((5, 6, 7))})
    assert _extract(figzip, tmp_path)[1] == (5, 6, 7)


# ---------- 3D ----------


def test_model_stats(tmp_path):
    s = models3d.stats(F.glb(tmp_path / "a.glb"), "model/gltf-binary")
    assert s["triangles"] == 1 and s["vertices"] == 3 and s["size"] == [1.0, 2.0, 0.0]
    obj = tmp_path / "a.obj"
    obj.write_bytes(F.OBJ)
    s = models3d.stats(obj, "model/obj")
    assert s["vertices"] == 4 and s["triangles"] == 2 and s["size"] == [1.0, 1.0, 3.0]
    s = models3d.stats(F.stl_binary(tmp_path / "a.stl"), "model/stl")
    assert s["triangles"] == 2
    ply = tmp_path / "a.ply"
    ply.write_bytes(F.PLY)
    assert models3d.stats(ply, "model/x-ply") == {"format": "ply", "vertices": 3,
                                                   "triangles": 1}


def test_obj_to_glb_only_with_trimesh(tmp_path, monkeypatch):
    obj = tmp_path / "a.obj"
    obj.write_bytes(F.OBJ)
    out = tmp_path / "model.glb"
    if models3d.trimesh_available():
        assert models3d.to_glb(obj, "obj", out)
        assert out.read_bytes()[:4] == b"glTF"
    monkeypatch.setattr(models3d, "trimesh_available", lambda: False)
    assert models3d.to_glb(obj, "obj", tmp_path / "other.glb") is False
    assert models3d.to_glb(obj, "fbx", tmp_path / "other.glb") is False


# ---------- fonts ----------



def _font(tmp_path, **kw):
    pytest.importorskip("fontTools")
    return F.font(tmp_path / "t.ttf", **kw)


def test_font_info_and_glyphs(tmp_path):
    path = _font(tmp_path, variable=True)
    info = fonts.info(path)
    assert info["family"] == "Kist Test" and info["style"] == "Regular"
    assert info["glyph_count"] == 53 and info["scripts"] == ["Latin"]
    assert info["variable"] and info["axes"][0]["tag"] == "wght"
    page = fonts.glyphs(path, 0)
    assert page["total"] == 52 and page["glyphs"][0]["hex"] == "U+0041"
    assert page["glyphs"][0]["name"] == "LATIN CAPITAL LETTER A"
    assert fonts.glyphs(path, 5)["glyphs"] == []


def test_font_info_without_fonttools(tmp_path, monkeypatch):
    path = _font(tmp_path, variable=True)
    with_ft = fonts.info(path)
    import builtins

    real_import = builtins.__import__

    def no_fonttools(name, *a, **kw):
        if name.startswith("fontTools"):
            raise ImportError(name)
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_fonttools)
    without = fonts.info(path)
    for key in ("family", "style", "glyph_count", "scripts", "variable", "codepoint_count"):
        assert without[key] == with_ft[key], key
    assert without["axes"][0]["tag"] == "wght" and without["axes"][0]["max"] == 900


# ---------- ffmpeg ----------


def test_ffmpeg_always_gets_a_named_demuxer_and_local_files_only(tmp_path):
    args = video.input_args(tmp_path / "a.mkv", "video/x-matroska", "mkv", seek=1.5)
    assert args[:4] == ["-protocol_whitelist", "file", "-f", "matroska"]
    assert "-ss" in args
    # a type ffmpeg isn't trusted with is refused, never guessed
    for mime in ("text/plain", "application/x-mpegurl", "application/octet-stream"):
        with pytest.raises(video.Unsupported):
            video.input_args(tmp_path / "a.m3u8", mime, "m3u8")
    assert video.input_args(tmp_path / "a.mp4", "application/octet-stream", "mp4")[3] == "mov"


def test_ffmpeg_runs_niced_with_a_thread_cap(monkeypatch):
    from kist.config import get_settings

    monkeypatch.setattr(get_settings(), "ffmpeg_threads", 3)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd

        class R:
            returncode = 0
            stdout = b""
            stderr = b""
        return R()

    monkeypatch.setattr(video.subprocess, "run", fake_run)
    video.run_ffmpeg(["-i", "x"], priority="heavy")
    cmd = seen["cmd"]
    if shutil.which("nice"):
        assert cmd[:3] == ["nice", "-n", "15"]
    assert cmd[cmd.index("-threads") + 1] == "3"


def test_hwaccel_arguments(monkeypatch):
    from kist.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "hwaccel", "none")
    assert video._hw_video_args(1920) is None
    monkeypatch.setattr(s, "hwaccel", "vaapi")
    inp, out = video._hw_video_args(1920)
    assert "-hwaccel" in inp and "vaapi" in inp and "h264_vaapi" in out
    monkeypatch.setattr(s, "hwaccel", "qsv")
    inp, out = video._hw_video_args(None)
    assert "qsv" in inp and "h264_qsv" in out


def test_playable_rules():
    h264 = {"has_video": True, "codec": "h264", "pix_fmt": "yuv420p", "has_audio": True,
            "audio_codec": "aac"}
    assert video.playable(h264, "video/mp4", "mp4") == "native"
    assert video.playable({**h264, "codec": "hevc"}, "video/mp4", "mp4") == "proxy"
    assert video.playable({**h264, "codec": "prores"}, "video/quicktime", "mov") == "proxy"
    assert video.playable({**h264, "pix_fmt": "yuv422p10le"}, "video/mp4", "mp4") == "proxy"
    assert video.playable({**h264, "audio_codec": "pcm_s24le"}, "video/quicktime",
                          "mov") == "proxy"
    assert video.playable(h264, "video/x-msvideo", "avi") == "proxy"
    vp9 = {"has_video": True, "codec": "vp9", "has_audio": True, "audio_codec": "opus"}
    assert video.playable(vp9, "video/webm", "webm") == "native"
    assert video.playable({"has_audio": True, "audio_codec": "mp3"}, "audio/mpeg",
                          "mp3") == "native"
    assert video.playable({"has_audio": True, "audio_codec": "pcm_s16be"}, "audio/x-aiff",
                          "aiff") == "proxy"
    assert video.playable({}, "video/mp4", "mp4") == "none"


@needs_ffmpeg
def test_hardware_failure_falls_back_to_software(tmp_path, monkeypatch):
    from kist.config import get_settings

    monkeypatch.setattr(get_settings(), "hwaccel", "vaapi")
    monkeypatch.setattr(get_settings(), "vaapi_device", str(tmp_path / "no-such-device"))
    src = F.video(tmp_path / "s.mp4", seconds=1)
    out = tmp_path / "o.mp4"
    assert video.encode_h264(src, "video/mp4", "mp4", out, scale_to=160) == "sw"
    assert out.stat().st_size > 0


@needs_ffmpeg
def test_waveform_peaks(tmp_path):
    from kist.services import audio

    src = F.audio(tmp_path / "a.wav", seconds=2)
    wave = audio.peaks(src, "audio/x-wav", "wav", 2.0, buckets=100)
    assert wave["version"] == 2 and wave["bits"] == 8
    assert 99 <= wave["length"] <= 101
    # ffmpeg's test sine is at 1/8 of full scale: about +-16 in 8 bits
    assert 12 <= max(wave["data"]) <= 20 and -20 <= min(wave["data"]) <= -12


@needs_ffmpeg
def test_scrub_sprite_and_contact_sheet(tmp_path):
    from PIL import Image

    src = F.video(tmp_path / "a.mp4", seconds=2)
    info = video.video_scrub(src, "video/mp4", "mp4", 2.0, tmp_path, frames=6)
    assert info == {"frames": 6, "cols": 5, "rows": 2, "frame_w": 256, "frame_h": 192}
    data = json.loads((tmp_path / "scrub.json").read_text())
    assert data["times"] == [round(2 * (i + 0.5) / 6, 3) for i in range(6)]
    with Image.open(tmp_path / "scrub.webp") as im:
        assert im.size == (5 * 256, 2 * 192)
    sheet = video.contact_sheet(tmp_path, 4)
    with Image.open(io.BytesIO(sheet)) as im:
        assert im.size == (2 * 256, 2 * 192)


def test_timestamps():
    assert video.timestamp(0) == "0:00.00"
    assert video.timestamp(75.5) == "1:15.50"
    assert video.timestamp(3725) == "1:02:05.00"
    assert video.scrub_times(None, 20) == [0.0]


# ---------- geo, timeline helpers ----------

COUNTRIES = (b"#ISO\tISO3\tnum\tfips\tCountry\nFR\tFRA\t250\tFR\tFrance\n"
             b"AU\tAUS\t36\tAS\tAustralia\n")
ADMIN1 = b"FR.11\tIle-de-France\tIle-de-France\t3012874\n"


def geonames_row(gid, name, lat, lon, cc, admin1="11") -> bytes:
    cols = [str(gid), name, name, "", str(lat), str(lon), "P", "PPL", cc, "", admin1, "", "",
            "", "1000", "", "", "Europe/Paris", "2020-01-01"]
    return ("\t".join(cols) + "\n").encode()


def write_geonames(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    rows = (geonames_row(1, "Paris", 48.85341, 2.3488, "FR")
            + geonames_row(2, "Versailles", 48.80359, 2.13424, "FR")
            + geonames_row(3, "Sydney", -33.86785, 151.20732, "AU", "02"))
    with zipfile.ZipFile(directory / "cities500.zip", "w") as zf:
        zf.writestr("cities500.txt", rows)
    (directory / "countryInfo.txt").write_bytes(COUNTRIES)
    (directory / "admin1CodesASCII.txt").write_bytes(ADMIN1)
    return directory


def test_offline_reverse_geocoding(tmp_path):
    src = write_geonames(tmp_path / "src")
    assert geo.download(src) == 3
    eiffel = geo.lookup(48.8584, 2.2945)
    assert eiffel["city"] == "Paris" and eiffel["country"] == "France"
    assert eiffel["region"] == "Ile-de-France"
    assert geo.label(eiffel) == "Paris, France"
    assert geo.lookup(48.80, 2.13)["city"] == "Versailles"
    assert geo.lookup(0.0, -30.0) is None  # mid-Atlantic: nothing within 50 km
    geo.places_file().unlink()
    geo.reset()
    assert geo.lookup(48.8584, 2.2945) is None


def test_bbox_and_grid():
    assert timeline.parse_bbox("-10,40,10,50") == (-10, 40, 10, 50)
    from kist.services.validation import ValidationError

    for bad in ("1,2,3", "a,b,c,d", "0,60,10,50", "-200,0,0,10"):
        with pytest.raises(ValidationError):
            timeline.parse_bbox(bad)
    assert timeline.cell_degrees(0) == 90.0
    assert timeline.cell_degrees(10) == 360 / 1024 / 4


# ---------- real RAW files (opt-in) ----------

RAW_SAMPLES = os.environ.get("KIST_TEST_RAW_DIR")
# Verified with these CC0 samples from https://raw.pixls.us/data/ (about 180 MB, so
# not in the repo): Canon/PowerShot S90/RAW_CANON_S90.CR2, Canon/Canon EOS R100/
# _MG_2195[1].CR3, Nikon/1 J1/_DSC0355.NEF, Fujifilm/FinePix F550EXR/DSCF6714.RAF,
# Sony/DSC-HX95/DSC00018.ARW, Panasonic/DC-FZ80/P1010306.RW2, Olympus/C5050Z/
# RAW_OLYMPUS_C5050Z.ORF, Pentax/K-01/_IGP2068.DNG, Pentax/K-1/IMGP8550.PEF


@pytest.mark.skipif(not RAW_SAMPLES, reason="set KIST_TEST_RAW_DIR to a folder of real RAWs")
def test_real_raw_samples():
    from PIL import Image

    seen = set()
    for path in sorted(Path(RAW_SAMPLES).iterdir()):
        ext = path.suffix.lstrip(".").lower()
        if ext not in formats.RAW_EXTS:
            continue
        seen.add(ext)
        assert sniff(path) == formats.RAW_MIMES[ext], path.name
        with Image.open(io.BytesIO(raw.extract_preview(path))) as im:
            assert im.width >= 160, path.name
        exif = media.exif_raw(path)
        assert exif.get("camera") and exif.get("taken_at"), path.name
    assert seen
