"""Media (Milestone 5): what Kist reads from video, audio, photos, design
files, SVG, 3D models, fonts and bundles, and the actions on them.

Layout:
- generate() runs in the preview child process (thumbs.generate_previews
  calls it first). It makes the previews of the new kinds and returns the
  `media` block: ffprobe and EXIF metadata, scrub sprite info, SVG colours,
  model stats, the bundle summary.
- apply() stores a media block on an item: items.media plus the derived,
  indexed columns (taken_at, has_gps, camera, lens, vertical, has_audio,
  place, raw_pair_key). meta.json carries the block, so rebuild-index
  restores everything from it.
- The rest are the actions REST and MCP call: cover frame, save frame,
  clips and proxies (worker jobs), poster upload, SVG text, contact sheets.

Files in an item folder (all rebuildable from the original, except poster.*):
  scrub.webp + scrub.json   video / animated GIF+WebP scrub sprite
  waveform.json             audio peaks (BBC audiowaveform format)
  proxy.mp4 / proxy.m4a     web-safe copy for files browsers can't play
  model.glb                 OBJ/STL/PLY converted with trimesh
  bundle.json               the zip's contents
  sanitized.svg             what "Copy SVG" returns
  poster.png                a poster the user uploaded (models, .fig ...)
  preview_icon              marker: the thumbnails are a generic icon
"""

import json
import logging
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..config import get_settings
from ..filetypes import kind_for
from ..models import Item
from . import formats

log = logging.getLogger("kist.media")

# keys a regeneration keeps: the user's choices, and where a derived item came from
USER_KEYS = ("cover_time", "source_item", "source_time", "source_range", "poster", "proxy")
ICON_MARKER = "preview_icon"
POSTER_FILE = "poster.png"
SANITIZED_SVG = "sanitized.svg"
DERIVED_FILES = ("scrub.webp", "scrub.json", "waveform.json", ICON_MARKER, "bundle.json",
                 SANITIZED_SVG, "model.glb")
# kinds where "no thumbnail" is a normal outcome (previews.outcome -> none)
NO_PREVIEW_OK = ("font", "model", "bundle", "design")


# =====================================================================
# preview generation (child process)
# =====================================================================


def generate(original: Path, item_dir: Path, mime: str, ext: str, options: dict):
    """Previews for the kinds this module owns. Returns a thumbs.Probe, or
    None to let thumbs.generate_previews handle the file (plain images and
    PDFs); image_extras() then adds their metadata."""
    from . import thumbs

    kind = kind_for(mime, ext)
    probe = thumbs.Probe()
    if kind == "video":
        _video(original, item_dir, mime, ext, options, probe)
    elif kind == "audio":
        _audio(original, item_dir, mime, ext, probe)
    elif kind == "design":
        _design(original, item_dir, mime, probe)
    elif kind == "model":
        _model(original, item_dir, mime, probe)
    elif kind == "bundle":
        _bundle(original, item_dir, probe)
    elif kind == "font":
        _font(original, probe)  # font-info (family, glyphs, axes, scripts)
        _font_specimen(original, item_dir, probe)  # Milestone 2's thumbnail
    elif kind == "image" and ext.lower() in formats.RAW_EXTS and mime in formats.RAW_MIMES.values():
        _raw(original, item_dir, ext, probe)
    elif kind == "image" and "svg" in mime:
        _svg(original, item_dir, probe)
    else:
        return None
    return probe


def image_extras(original: Path, item_dir: Path, mime: str, ext: str, probe) -> None:
    """EXIF for photos and the scrub sprite for animated GIF/WebP (after
    thumbs made the image's own previews)."""
    media: dict = dict(probe.media or {})
    try:
        exif = exif_pillow(original)
        if exif:
            media["exif"] = exif
    except Exception as exc:
        log.debug("EXIF unreadable for %s: %s", original, exc)
    if mime in ("image/gif", "image/webp"):
        from . import video

        try:
            info = video.animation_scrub(original, item_dir)
        except Exception as exc:
            log.warning("animation scrub failed for %s: %s", original, exc)
            info = None
        if info:
            media["scrub"] = {k: v for k, v in info.items()
                              if k not in ("duration", "anim_frames")}
            media["animation"] = {"frames": info["anim_frames"], "duration": info["duration"]}
            probe.duration_s = info["duration"]
    probe.media = media or None


def _thumbs_from(png: Path, item_dir: Path, probe, *, screen: bool = True) -> None:
    from . import thumbs

    w, h = thumbs.make_thumbs_from(png, item_dir)
    if probe.width is None:
        probe.width, probe.height = w, h
    if screen and thumbs.needs_screen_copy(w, h):
        thumbs.make_screen_copy(png, item_dir)
    (item_dir / ICON_MARKER).unlink(missing_ok=True)


def _video(original, item_dir, mime, ext, options, probe) -> None:
    from . import video

    info = video.summarize(video.ffprobe(original, mime, ext))
    info.update(_video_place_and_time(info))
    duration = info.get("duration")
    probe.duration_s = duration
    probe.width, probe.height = info.get("width"), info.get("height")
    media: dict = {"video": info, "playable": video.playable(info, mime, ext)}
    if not info.get("has_video"):
        raise RuntimeError("this file has no video stream")
    t = options.get("cover_time")
    if not isinstance(t, (int, float)) or t < 0:
        t = video.default_cover_time(duration)
    with tempfile.TemporaryDirectory() as td:
        poster = Path(td) / "poster.png"
        poster.write_bytes(video.frame_png(original, mime, ext, float(t)))
        _thumbs_from(poster, item_dir, probe, screen=False)
    if probe.width is None:
        probe.width, probe.height = info.get("width"), info.get("height")
    try:
        media["scrub"] = video.video_scrub(original, mime, ext, duration, item_dir)
    except Exception as exc:  # the poster is what matters; the sprite is extra
        log.warning("scrub sprite failed for %s: %s", original, exc)
    probe.media = media


def _video_place_and_time(info: dict) -> dict:
    out: dict = {}
    loc = info.get("location")
    if loc:
        m = re.match(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", loc)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0.0, 0.0):
                out["lat"], out["lon"] = lat, lon
    created = info.get("creation_time")
    if created:
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if 1970 < dt.year < 2100:
                out["taken_at"] = dt.astimezone(UTC).replace(tzinfo=None).isoformat(
                    timespec="seconds")
        except ValueError:
            pass
    return out


def _audio(original, item_dir, mime, ext, probe) -> None:
    from . import audio, video

    info = video.summarize(video.ffprobe(original, mime, ext))
    probe.duration_s = info.get("duration")
    media: dict = {"audio": info, "playable": video.playable(info, mime, ext)}
    png = audio.generate(original, mime, ext, item_dir, info)
    try:
        _thumbs_from(png, item_dir, probe, screen=False)
    finally:
        png.unlink(missing_ok=True)
    media["waveform"] = True
    probe.media = media


def _design(original, item_dir, mime, probe) -> None:
    from . import design_thumbs

    poster = item_dir / POSTER_FILE
    with tempfile.TemporaryDirectory() as td:
        try:
            png, info = design_thumbs.extract(original, mime, Path(td))
        except design_thumbs.NoPreview as exc:
            info = {"format": design_thumbs.FORMATS.get(mime), "preview": str(exc)}
            png = None
        if png is not None:
            _thumbs_from(png, item_dir, probe)
        elif poster.is_file():
            _thumbs_from(poster, item_dir, probe)
            info["source"] = "poster"
        else:
            make_icon(item_dir, (info.get("format") or "file").upper(), probe)
    probe.media = {"design": info}


def _model(original, item_dir, mime, probe) -> None:
    from . import models3d

    stats = models3d.stats(original, mime)
    fmt = stats.get("format")
    try:
        if fmt in models3d.CONVERTIBLE and models3d.to_glb(original, fmt, item_dir / "model.glb"):
            stats["glb"] = True
    except Exception as exc:
        stats["glb_error"] = str(exc)[:200]
    poster = item_dir / POSTER_FILE
    if poster.is_file():
        _thumbs_from(poster, item_dir, probe)
    else:
        make_icon(item_dir, (fmt or "3D").upper(), probe)
    probe.media = {"model": stats}


def _bundle(original, item_dir, probe) -> None:
    from . import bundles, design_thumbs

    data = bundles.manifest(original)
    bundles.write_manifest(item_dir, data)
    media = {"bundle": bundles.summary(data)}
    cover = data.get("cover")
    poster = item_dir / POSTER_FILE
    done = False
    if poster.is_file():
        _thumbs_from(poster, item_dir, probe)
        done = True
    elif cover:
        try:
            blob = design_thumbs.read_zip_entry(
                original, [cover], limit=get_settings().bundle_max_entry_mb * 1024 * 1024
            )
            if blob:
                with tempfile.TemporaryDirectory() as td:
                    tmp = Path(td) / ("cover" + PurePosixPath(cover).suffix.lower())
                    tmp.write_bytes(blob)
                    _thumbs_from(tmp, item_dir, probe)
                    done = True
        except Exception as exc:
            log.warning("bundle cover %s unreadable: %s", cover, exc)
    if not done:
        make_icon(item_dir, "ZIP" if not data.get("model") else "3D", probe)
    probe.media = media


def _font_specimen(original: Path, item_dir: Path, probe) -> None:
    """The thumbnail of a font is Milestone 2's specimen (its name and a
    sample, rendered by Pillow/FreeType). A font FreeType can't render keeps
    no thumbnail (preview_status none) but still has its font-info; a file
    neither can read is a broken font, and its preview fails (as in M2)."""
    import tempfile

    from . import thumbs

    try:
        with tempfile.TemporaryDirectory() as td:
            poster = Path(td) / "specimen.png"
            thumbs.font_specimen(original, poster)
            probe.width, probe.height = thumbs.make_thumbs_from(poster, item_dir)
    except Exception as exc:
        if not ((probe.media or {}).get("font") or {}).get("family"):
            raise  # not even fontTools could name it: a broken font
        log.info("no font specimen for %s: %s", original, exc)


def _font(original, probe) -> None:
    from . import fonts

    try:
        info = fonts.info(original)
        probe.media = {"font": {k: info.get(k) for k in (
            "family", "style", "full_name", "weight", "glyph_count", "variable", "scripts",
            "axes", "outlines")}}
    except Exception as exc:
        probe.media = {"font": {"error": str(exc)[:200]}}


def _raw(original, item_dir, ext, probe) -> None:
    from PIL import Image, ImageOps

    from . import raw, thumbs

    data = raw.extract_preview(original)
    exif = exif_raw(original)
    with tempfile.TemporaryDirectory() as td:
        jpg = Path(td) / "preview.jpg"
        jpg.write_bytes(data)
        png = jpg  # used as is unless it has to be turned
        with Image.open(jpg) as im:
            orientation = (exif or {}).get("orientation")
            own = im.getexif().get(0x112)
            if (orientation and orientation != 1 and not own) or (own and own != 1):
                im.load()
                if own and own != 1:
                    im = ImageOps.exif_transpose(im)
                else:
                    im = _orient(im, orientation)
                png = Path(td) / "preview.png"
                im.convert("RGB").save(png)
        _thumbs_from(png, item_dir, probe)
        thumbs.make_display_copy(png, item_dir)
    media: dict = {"raw": {"format": ext.lower(), "preview": [probe.width, probe.height]}}
    if exif:
        media["exif"] = exif
    probe.media = media


def _orient(im, orientation: int):
    from PIL import Image

    ops = {
        2: [Image.Transpose.FLIP_LEFT_RIGHT], 3: [Image.Transpose.ROTATE_180],
        4: [Image.Transpose.FLIP_TOP_BOTTOM], 5: [Image.Transpose.TRANSPOSE],
        6: [Image.Transpose.ROTATE_270], 7: [Image.Transpose.TRANSVERSE],
        8: [Image.Transpose.ROTATE_90],
    }
    for op in ops.get(int(orientation), []):
        im = im.transpose(op)
    return im


def _svg(original, item_dir, probe) -> None:
    from . import svg

    text = svg.sanitize(original.read_bytes())
    (item_dir / SANITIZED_SVG).write_text(text, encoding="utf-8")
    info: dict = {"colors": svg.colors(text), "view_box": svg.view_box(text)}
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "svg.png"
        info["renderer"] = svg.render_png(text, png)
        _thumbs_from(png, item_dir, probe)
    probe.media = {"svg": info}


def make_icon(item_dir: Path, label: str, probe) -> None:
    """A generic tile with the format's name, until a real preview exists.
    The marker keeps it out of the perceptual hash and the colours."""
    from PIL import Image, ImageDraw, ImageFont

    from . import thumbs

    size = 1024
    img = Image.new("RGB", (size, size), (39, 39, 42))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((212, 162, 812, 862), radius=48, outline=(113, 113, 122), width=18)
    draw.polygon([(612, 162), (812, 362), (612, 362)], fill=(82, 82, 91))
    label = label[:6]
    try:
        font = ImageFont.load_default(size=150 if len(label) <= 4 else 110)
    except TypeError:  # Pillow without FreeType
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), label, font=font)
    draw.text(((size - (box[2] - box[0])) / 2, 560 - (box[3] - box[1]) / 2), label,
              fill=(228, 228, 231), font=font)
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "icon.png"
        img.save(png)
        w, h = thumbs.make_thumbs_from(png, item_dir)
    (item_dir / ICON_MARKER).touch()
    if probe.width is None:
        probe.width, probe.height = w, h


# =====================================================================
# EXIF
# =====================================================================


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _text(v) -> str | None:
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    if not isinstance(v, str):
        return None
    v = v.replace("\x00", "").strip()
    return v[:200] or None


def _taken(value, offset=None) -> str | None:
    text = _text(value)
    if not text:
        return None
    m = re.match(r"^(\d{4})[:-](\d{2})[:-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})", text)
    if not m:
        return None
    try:
        dt = datetime(*(int(x) for x in m.groups()))
    except ValueError:
        return None
    if not 1900 < dt.year < 2100:
        return None
    return dt.isoformat()


def camera_name(make: str | None, model: str | None) -> str | None:
    if not model:
        return make
    if not make:
        return model
    first = make.split()[0]
    if model.lower().startswith(first.lower()):
        return model
    return f"{first} {model}"


def _shutter_text(s: float | None) -> str | None:
    if not s or s <= 0:
        return None
    if s >= 0.3:
        return f"{s:g}s"
    return f"1/{round(1 / s)}"


def normalize_exif(ifd0: dict, exif: dict, gps: dict) -> dict:
    """Pillow-numbered tag dicts -> the fields Kist keeps."""
    make, model = _text(ifd0.get(0x10F)), _text(ifd0.get(0x110))
    out: dict = {
        "make": make, "model": model, "camera": camera_name(make, model),
        "lens": _text(exif.get(0xA434)),
        "focal_length": _num(exif.get(0x920A)),
        "aperture": _num(exif.get(0x829D)),
        "shutter": _num(exif.get(0x829A)),
        "iso": _num(exif.get(0x8827)),
        "taken_at": _taken(exif.get(0x9003) or ifd0.get(0x132)),
        "offset": _text(exif.get(0x9011)),
        "orientation": _num(ifd0.get(0x112)),
        "software": _text(ifd0.get(0x131)),
    }
    if not out["lens"] and isinstance(exif.get(0xA432), (list, tuple)) and exif[0xA432]:
        spec = [_num(x) for x in exif[0xA432]]
        if len(spec) >= 2 and spec[0]:
            out["lens"] = (f"{spec[0]:g}mm" if spec[0] == spec[1] else
                           f"{spec[0]:g}-{spec[1]:g}mm")
    if out["iso"] is not None:
        out["iso"] = int(out["iso"])
    if out["orientation"] is not None:
        out["orientation"] = int(out["orientation"])
    out["shutter_text"] = _shutter_text(out["shutter"])
    lat = _gps(gps.get(2), gps.get(1))
    lon = _gps(gps.get(4), gps.get(3))
    if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0) \
            and -90 <= lat <= 90 and -180 <= lon <= 180:
        out["lat"], out["lon"] = lat, lon
    return {k: v for k, v in out.items() if v is not None}


def _gps(values, ref) -> float | None:
    if not isinstance(values, (list, tuple)) or len(values) < 3:
        return None
    nums = [_num(v) for v in values[:3]]
    if any(n is None for n in nums):
        return None
    deg = nums[0] + nums[1] / 60 + nums[2] / 3600
    if _text(ref) in ("S", "W"):
        deg = -deg
    return round(deg, 7)


HEIF_EXIF_EXTS = {".heic", ".heif", ".avif"}


def _exif_blob_vips(path: Path) -> bytes | None:
    import pyvips

    im = pyvips.Image.new_from_file(str(path), access="sequential")
    if "exif-data" not in im.get_fields():
        return None
    return im.get("exif-data")


def exif_pillow(path: Path) -> dict:
    from PIL import Image

    if path.suffix.lower() in HEIF_EXIF_EXTS:
        # HEIF: never pillow_heif here. The preview child has already tried
        # the photo with libvips (the system libheif); opening it again with
        # pillow_heif loads its own bundled libheif into the same process,
        # and that segfaults (exit -11 on every iPhone photo in the Docker
        # image, Kist 2.0 M5). exiftool runs in its own process and reads
        # current iPhone HEICs, which Debian's libheif 1.15 refuses
        # ("Metadata not correctly assigned to image"); libvips is the
        # fallback when exiftool is off or missing.
        import shutil as _sh

        if get_settings().exiftool and _sh.which("exiftool"):
            try:
                found = exif_exiftool(path)
            except Exception as exc:
                log.debug("exiftool failed for %s: %s", path, exc)
                found = {}
            if found:
                return found
        try:
            blob = _exif_blob_vips(path)
        except Exception as exc:
            log.debug("libvips can't read HEIF EXIF of %s: %s", path, exc)
            return {}
        if not blob:
            return {}
        exif = Image.Exif()
        exif.load(blob)
        if not exif:
            return {}
        return normalize_exif(dict(exif), dict(exif.get_ifd(0x8769)),
                              dict(exif.get_ifd(0x8825)))
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except Exception:
        pass
    with Image.open(path) as im:
        exif = im.getexif()
        if not exif:
            return {}
        ifd0 = dict(exif)
        sub = dict(exif.get_ifd(0x8769))
        gps = dict(exif.get_ifd(0x8825))
    return normalize_exif(ifd0, sub, gps)


def exif_raw(path: Path) -> dict:
    """RAW camera data: exiftool when installed (reads maker notes, so lens
    names are complete), else the built-in TIFF/CR3 reader."""
    import shutil as _sh


    found: dict = {}
    if get_settings().exiftool and _sh.which("exiftool"):
        try:
            found = exif_exiftool(path)
        except Exception as exc:
            log.debug("exiftool failed for %s: %s", path, exc)
    try:
        builtin = _exif_raw_builtin(path)
    except Exception as exc:
        log.debug("RAW EXIF unreadable for %s: %s", path, exc)
        builtin = {}
    # exiftool wins; the built-in reader fills what it left out (e.g. the
    # lens specification when there's no lens name)
    return {**builtin, **found}


def _exif_raw_builtin(path: Path) -> dict:
    from . import raw

    tags = raw.read_exif(path)
    if "jpeg" in tags:  # RAF: the embedded JPEG carries the EXIF
        import io

        from PIL import Image

        with Image.open(io.BytesIO(tags["jpeg"])) as im:
            exif = im.getexif()
            return normalize_exif(dict(exif), dict(exif.get_ifd(0x8769)),
                                  dict(exif.get_ifd(0x8825)))
    if tags:
        return normalize_exif(tags.get("ifd0", {}), tags.get("exif", {}), tags.get("gps", {}))
    return {}


def exif_exiftool(path: Path) -> dict:
    import subprocess

    out = subprocess.run(
        ["exiftool", "-j", "-n", "-fast2", "-Make", "-Model", "-LensModel", "-LensID",
         "-FocalLength", "-FNumber", "-ExposureTime", "-ISO", "-DateTimeOriginal",
         "-OffsetTimeOriginal", "-Orientation", "-GPSLatitude", "-GPSLongitude", "-Software",
         "--", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        return {}
    rows = json.loads(out.stdout or "[]")
    if not rows:
        return {}
    r = rows[0]
    make, model = _text(r.get("Make")), _text(r.get("Model"))
    lens = _text(r.get("LensModel")) or _text(r.get("LensID"))
    data: dict = {
        "make": make, "model": model, "camera": camera_name(make, model), "lens": lens,
        "focal_length": _num(r.get("FocalLength")), "aperture": _num(r.get("FNumber")),
        "shutter": _num(r.get("ExposureTime")), "iso": _num(r.get("ISO")),
        "taken_at": _taken(r.get("DateTimeOriginal")), "offset": _text(r.get("OffsetTimeOriginal")),
        "orientation": _num(r.get("Orientation")), "software": _text(r.get("Software")),
    }
    lat, lon = _num(r.get("GPSLatitude")), _num(r.get("GPSLongitude"))
    if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0):
        data["lat"], data["lon"] = round(lat, 7), round(lon, 7)
    if data["iso"] is not None:
        data["iso"] = int(data["iso"])
    if data["orientation"] is not None:
        data["orientation"] = int(data["orientation"])
    data["shutter_text"] = _shutter_text(data["shutter"])
    return {k: v for k, v in data.items() if v is not None}


# =====================================================================
# storing it on an item
# =====================================================================


def merge(old: dict | None, new: dict | None) -> dict | None:
    """A regeneration's media block, keeping the user's choices (and the
    place, when the GPS position didn't change)."""
    out = dict(new or {})
    old = old or {}
    for key in USER_KEYS:
        if key in old and key not in out:
            out[key] = old[key]
    if "place" in old and _position(old) == _position(out) and _position(out) is not None:
        out["place"] = old["place"]
    return out or None


def _position(media: dict) -> tuple[float, float] | None:
    for block in ("exif", "video"):
        b = media.get(block) or {}
        if b.get("lat") is not None and b.get("lon") is not None:
            return float(b["lat"]), float(b["lon"])
    return None


def _parse_dt(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def apply(item: Item, media: dict | None, *, geocode: bool = True) -> None:
    """Store a media block and the columns derived from it."""
    media = dict(media) if media else None
    exif = (media or {}).get("exif") or {}
    av = (media or {}).get("video") or (media or {}).get("audio") or {}
    kind = kind_for(item.mime, item.ext)
    item.taken_at = _parse_dt(exif.get("taken_at") or av.get("taken_at"))
    pos = _position(media or {})
    item.has_gps = pos is not None
    item.lat, item.lon = pos if pos else (None, None)
    item.camera = exif.get("camera")
    item.lens = exif.get("lens")
    if kind == "video" and "vertical" in av:
        item.vertical = bool(av["vertical"])
    elif kind in ("image", "video") and item.width and item.height:
        item.vertical = item.height > item.width
    else:
        item.vertical = None
    if kind == "audio":
        item.has_audio = True
    elif kind == "video":
        item.has_audio = bool(av.get("has_audio"))
    else:
        item.has_audio = None
    if media is not None and pos is not None and "place" not in media and geocode:
        try:
            from . import geo

            found = geo.lookup(*pos)
        except Exception:
            log.exception("reverse geocoding failed")
            found = None
        if found:
            media["place"] = found
    from . import geo as geo_svc

    item.place = geo_svc.label((media or {}).get("place")) if pos else None
    item.raw_pair_key = None
    if kind == "image" and item.taken_at is not None:
        stem = PurePosixPath(item.filename or "").stem.lower()
        if stem:
            item.raw_pair_key = f"{stem}|{exif.get('taken_at')}"
    item.media = media


def update_media(session: Session, item_id: str, changes: dict, *, touch: bool = False) -> None:
    """Change keys of items.media (None removes one) and rewrite meta.json.
    Derived state (a proxy's status) keeps modified_at; user choices move it."""
    from . import meta as meta_svc

    item = session.get(Item, item_id, with_for_update=True, populate_existing=True)
    if item is None:
        session.rollback()
        return
    media = dict(item.media or {})
    for k, v in changes.items():
        if v is None:
            media.pop(k, None)
        else:
            media[k] = v
    # a Core UPDATE never moves modified_at by itself (models._touch_on_edit)
    values: dict = {"media": media or None,
                    "modified_at": sa.func.now() if touch else Item.modified_at}
    session.execute(sa.update(Item).where(Item.id == item_id).values(**values))
    session.commit()
    meta_svc.sync_item(session, item_id)


# =====================================================================
# filters (GET /api/items) and the taken order
# =====================================================================

MEDIA_FILTERS = ("taken_after", "taken_before", "has_gps", "camera", "lens", "duration_min",
                 "duration_max", "vertical", "has_audio", "place", "date_after", "date_before",
                 "bbox")


def _bbox(raw) -> tuple[float, float, float, float]:
    """west,south,east,north (a string or a 4-sequence) -> floats, checked."""
    from .validation import ValidationError

    try:
        parts = raw.split(",") if isinstance(raw, str) else list(raw)
        w, s, e, n = (float(x) for x in parts)
    except (TypeError, ValueError):
        raise ValidationError("bbox must be west,south,east,north in degrees") from None
    if not (-180 <= w <= 180 and -180 <= e <= 180 and -90 <= s <= n <= 90):
        raise ValidationError("bbox is out of range (west,south,east,north)")
    return w, s, e, n


def filters(params: dict | None) -> list:
    if not params:
        return []
    out: list = []
    p = {k: v for k, v in params.items() if v is not None and v != ""}
    if "taken_after" in p:
        out.append(Item.taken_at >= p["taken_after"])
    if "taken_before" in p:
        out.append(Item.taken_at <= p["taken_before"])
    if "has_gps" in p:
        out.append(Item.has_gps.is_(bool(p["has_gps"])))
    if "camera" in p:
        out.append(Item.camera.ilike(f"%{p['camera']}%"))
    if "lens" in p:
        out.append(Item.lens.ilike(f"%{p['lens']}%"))
    if "duration_min" in p:
        out.append(Item.duration_s >= float(p["duration_min"]))
    if "duration_max" in p:
        out.append(Item.duration_s <= float(p["duration_max"]))
    if "vertical" in p:
        out.append(Item.vertical.is_(bool(p["vertical"])))
    if "has_audio" in p:
        out.append(Item.has_audio.is_(bool(p["has_audio"])))
    if "place" in p:
        out.append(Item.place.ilike(f"%{p['place']}%"))
    # the timeline's date: taken, else added (what /api/timeline groups by)
    if "date_after" in p:
        out.append(Item.taken_sort >= p["date_after"])
    if "date_before" in p:
        out.append(Item.taken_sort <= p["date_before"])
    # a map area (the web map's clusters), west,south,east,north
    if "bbox" in p:
        w, s, e, n = _bbox(p["bbox"])
        out += [Item.has_gps.is_(True), Item.lat.between(s, n)]
        out.append(Item.lon.between(w, e) if w <= e else sa.or_(Item.lon >= w, Item.lon <= e))
    return out


# =====================================================================
# actions
# =====================================================================


def item_paths(item: Item) -> tuple[Path, Path]:
    item_dir = get_settings().items_dir / item.id
    return item_dir, item_dir / f"original.{item.ext}"


def _kind(item: Item) -> str:
    return kind_for(item.mime, item.ext)


def _duration(item: Item) -> float | None:
    return item.duration_s or ((item.media or {}).get("video") or {}).get("duration")


def check_time(item: Item, t: float, what: str = "t") -> float:
    from .validation import ValidationError

    try:
        t = float(t)
    except (TypeError, ValueError):
        raise ValidationError(f"{what} must be a number of seconds") from None
    if t != t or t < 0:
        raise ValidationError(f"{what} must be 0 or more seconds")
    dur = _duration(item)
    if dur is not None and t > dur + 0.001:
        raise ValidationError(f"{what} is past the end ({dur:.3f} s)")
    return t


def set_cover(session: Session, item_id: str, t: float | None) -> Item:
    """Use the frame at `t` seconds as the video's thumbnail (None: the
    default frame). Records a `cover` event (undo puts the old one back)."""
    from ..lifecycle import TransitionError
    from . import events, previews
    from . import items as items_svc

    item = items_svc.get_item(session, item_id)
    if _kind(item) != "video":
        raise TransitionError("only videos have a cover frame")
    if t is not None:
        t = round(check_time(item, t), 3)
    old = (item.media or {}).get("cover_time")
    media = dict(item.media or {})
    if t is None:
        media.pop("cover_time", None)
    else:
        media["cover_time"] = t
    item.media = media
    item.modified_at = datetime.now(UTC)  # a choice, not derived (BOOKKEEPING_COLUMNS)
    events.record(session, item.id, "cover", {"cover_time": old}, {"cover_time": t})
    session.commit()
    previews.regenerate(session, item)
    session.refresh(item)
    return item


def save_frame(session: Session, item_id: str, t: float):
    """Save the frame at `t` as a new PNG item, linked derived_from the
    video; it gets the video's tags and folders. Returns ingest's result."""
    from ..ids import new_ulid
    from ..lifecycle import TransitionError
    from . import ingest, links, video
    from . import items as items_svc

    item = items_svc.get_item(session, item_id)
    if _kind(item) != "video":
        raise TransitionError("frames can only be saved from videos")
    t = round(check_time(item, t), 3)
    _dir, original = item_paths(item)
    settings = get_settings()
    settings.staging_dir.mkdir(parents=True, exist_ok=True)
    staging = settings.staging_dir / f"frame-{new_ulid()}.png"
    staging.write_bytes(video.frame_png(original, item.mime, item.ext, t, timeout=300))
    stem = PurePosixPath(item.filename).stem
    name = ingest.sanitize_filename(f"{stem} @ {video.timestamp(t).replace(':', '-')}.png")
    tags = [tg.name for tg in item.tags]
    folders = [f.id for f in item.folders]
    try:
        result = ingest.ingest_file(session, staging, name, tag_names=tags, folder_ids=folders)
    finally:
        staging.unlink(missing_ok=True)
    _mark_derived(session, result.item.id, item.id, {"source_time": t}, result.created)
    links.link(session, result.item.id, item.id, "derived_from")
    session.refresh(result.item)
    return result


def _mark_derived(session: Session, new_id: str, src_id: str, extra: dict, created: bool) -> None:
    if not created:
        return
    update_media(session, new_id, {"source_item": src_id, **extra})


def queue_clip(session: Session, item_id: str, start: float, end: float, fmt: str,
               max_side: int | None):
    """Validate a clip request and queue it (a worker job)."""
    from ..lifecycle import JobKind, TransitionError
    from ..models import Job
    from . import events, validation, video
    from . import items as items_svc

    item = items_svc.get_item(session, item_id)
    kind = _kind(item)
    allowed = video.CLIP_FORMATS.get(kind)
    if not allowed:
        raise TransitionError("clips can be cut from videos and audio only")
    if fmt not in allowed:
        raise validation.ValidationError(
            f"format must be one of {', '.join(allowed)} for {kind}"
        )
    start, end = check_time(item, start, "start"), check_time(item, end, "end")
    if end <= start:
        raise validation.ValidationError("end must be after start")
    settings = get_settings()
    limit = settings.gif_max_seconds if fmt in ("gif", "webp") else settings.clip_max_seconds
    if end - start > limit:
        raise validation.ValidationError(f"a {fmt} clip can be at most {limit:g} s long")
    if max_side is not None and not 16 <= int(max_side) <= 8192:
        raise validation.ValidationError("max_side must be between 16 and 8192")
    ctx = events.context()
    job = Job(kind=JobKind.CLIP, payload={
        "item_id": item.id, "filename": item.filename, "start": start, "end": end,
        "format": fmt, "max_side": max_side, "actor": ctx.actor, "source": ctx.source,
        "batch_id": ctx.batch_id,
    })
    session.add(job)
    session.commit()
    return job


def run_clip(session: Session, job) -> None:
    from ..ids import new_ulid
    from . import events, ingest, links, video

    p = dict(job.payload or {})
    item = session.get(Item, p.get("item_id"))
    if item is None:
        raise RuntimeError("the source item no longer exists")
    _dir, original = item_paths(item)
    settings = get_settings()
    settings.staging_dir.mkdir(parents=True, exist_ok=True)
    fmt = p["format"]
    staging = settings.staging_dir / f"clip-{new_ulid()}.{fmt}"
    try:
        video.make_clip(original, item.mime, item.ext, staging, start=float(p["start"]),
                        end=float(p["end"]), fmt=fmt, max_side=p.get("max_side"))
        stem = PurePosixPath(item.filename).stem
        name = ingest.sanitize_filename(
            f"{stem} {video.timestamp(p['start']).replace(':', '-')}-"
            f"{video.timestamp(p['end']).replace(':', '-')}.{fmt}"
        )
        with events.acting(p.get("actor") or "worker", p.get("source") or "worker",
                           p.get("batch_id")):
            result = ingest.ingest_file(
                session, staging, name, tag_names=[t.name for t in item.tags],
                folder_ids=[f.id for f in item.folders],
            )
            _mark_derived(session, result.item.id, item.id,
                          {"source_range": [p["start"], p["end"]]}, result.created)
            links.link(session, result.item.id, item.id, "derived_from")
    finally:
        staging.unlink(missing_ok=True)
    job.payload = {**p, "result_item_id": result.item.id}
    session.commit()


def proxy_state(item: Item) -> dict:
    media = item.media or {}
    item_dir, _ = item_paths(item)
    from . import video

    path = video.proxy_path(item_dir)
    playable = media.get("playable") or "none"
    status = (media.get("proxy") or {}).get("status")
    if path is not None:
        status = "ready"
    return {"playable": playable, "proxy": status, "path": path}


def queue_proxy(session: Session, item: Item):
    """Queue a proxy job (once: an open one is reused)."""
    from ..lifecycle import JobKind, JobStatus
    from ..models import Job

    open_job = session.execute(
        sa.select(Job).where(
            Job.kind == JobKind.PROXY,
            Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
            Job.payload["item_id"].astext == item.id,
        )
    ).scalars().first()
    if open_job is not None:
        return open_job
    job = Job(kind=JobKind.PROXY, payload={"item_id": item.id, "filename": item.filename})
    session.add(job)
    session.commit()
    update_media(session, item.id, {"proxy": {"status": "pending"}})
    return job


def run_proxy(session: Session, job) -> None:
    from . import video

    p = job.payload or {}
    item = session.get(Item, p.get("item_id"))
    if item is None:
        return
    item_dir, original = item_paths(item)
    info = (item.media or {}).get("video") or (item.media or {}).get("audio") or {}
    try:
        out = video.make_proxy(original, item.mime, item.ext, item_dir,
                               has_video=bool(info.get("has_video", _kind(item) == "video")))
    except Exception as exc:
        update_media(session, item.id, {"proxy": {"status": "failed", "error": str(exc)[-300:]}})
        raise
    update_media(session, item.id, {"proxy": {"status": "ready", "file": out.name}})


def run_job(session: Session, job) -> None:
    from ..lifecycle import JobKind

    if job.kind == JobKind.CLIP:
        run_clip(session, job)
    elif job.kind == JobKind.PROXY:
        run_proxy(session, job)
    else:
        raise ValueError(f"unknown media job {job.kind}")


POSTER_MAX_BYTES = 25 * 1024 * 1024
POSTER_KINDS = ("model", "design", "bundle")


def set_poster(session: Session, item_id: str, data: bytes) -> Item:
    """A poster image for a model (rendered by the web's <model-viewer>),
    a design file without a preview, or a bundle. Kept as poster.png (it
    can't be rebuilt from the original) and used for the thumbnails."""
    import io

    from PIL import Image

    from ..lifecycle import TransitionError
    from . import events, previews, validation
    from . import items as items_svc

    item = items_svc.get_item(session, item_id)
    if _kind(item) not in POSTER_KINDS:
        raise TransitionError("posters are for 3D models, design files and bundles")
    if len(data) > POSTER_MAX_BYTES:
        raise validation.ValidationError("the poster image is too large (25 MB at most)")
    try:
        Image.MAX_IMAGE_PIXELS = 50_000_000
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ("PNG", "JPEG", "WEBP"):
                raise validation.ValidationError("the poster must be a PNG, JPEG or WebP image")
            im.load()
            img = im.convert("RGBA" if "A" in im.getbands() else "RGB")
    except validation.ValidationError:
        raise
    except Exception:
        raise validation.ValidationError("the poster isn't a readable image") from None
    item_dir, _ = item_paths(item)
    tmp = item_dir / ".poster.tmp.png"
    img.save(tmp, "PNG")
    tmp.replace(item_dir / POSTER_FILE)
    media = dict(item.media or {})
    media["poster"] = {"at": datetime.now(UTC).isoformat(timespec="seconds"),
                       "width": img.width, "height": img.height}
    item.media = media
    item.modified_at = datetime.now(UTC)  # a choice, not derived (BOOKKEEPING_COLUMNS)
    events.record(session, item.id, "poster", None, {"poster": True})
    session.commit()
    previews.regenerate(session, item)
    session.refresh(item)
    return item


def sanitized_svg(item: Item) -> str:
    from ..lifecycle import TransitionError
    from . import svg

    if "svg" not in item.mime:
        raise TransitionError("this item isn't an SVG")
    item_dir, original = item_paths(item)
    cached = item_dir / SANITIZED_SVG
    if cached.is_file():
        return cached.read_text(encoding="utf-8")
    text = svg.sanitize(original.read_bytes())
    try:
        cached.write_text(text, encoding="utf-8")
    except OSError:
        pass
    return text


def contact_sheet(item: Item, frames: int = 9) -> bytes:
    from ..lifecycle import TransitionError
    from . import video

    item_dir, _ = item_paths(item)
    if not (item_dir / "scrub.webp").is_file():
        raise TransitionError(
            "this item has no scrub frames (videos and animated GIF/WebP get them with "
            "their previews; retry_preview or `kist media-backfill` makes them)"
        )
    return video.contact_sheet(item_dir, max(1, min(int(frames), 36)))


# =====================================================================
# backfill (kist media-backfill)
# =====================================================================


def backfill_one(session: Session, item: Item, *, refine: bool = True) -> str:
    """Bring an older item up to Milestone 5: a more precise MIME type
    (e.g. a .sketch zip), then previews + metadata again. Returns what was
    done."""
    from . import ingest, previews

    item_dir, original = item_paths(item)
    if not original.is_file():
        return "missing original"
    changed = False
    if refine:
        sniffed = ingest.sniff_mime(original)
        better = formats.refine_mime(original, sniffed, item.ext)
        if better != item.mime:
            item.mime = better
            changed = True
            session.commit()
    previews.regenerate(session, item)
    return "reclassified + regenerated" if changed else "regenerated"


def backfill(session: Session, limit: int | None = None, all_items: bool = False,
             progress=None) -> dict:
    """Items without a media block (or every item with all_items)."""
    q = sa.select(Item.id).where(Item.deleted_at.is_(None)).order_by(Item.id)
    if not all_items:
        q = q.where(Item.media.is_(None))
    if limit:
        q = q.limit(limit)
    ids = session.execute(q).scalars().all()
    counts: dict[str, int] = {}
    for item_id in ids:
        item = session.get(Item, item_id)
        if item is None:
            continue
        try:
            what = backfill_one(session, item)
        except Exception as exc:
            session.rollback()
            log.warning("media backfill failed for %s: %s", item_id, exc)
            what = "failed"
        counts[what] = counts.get(what, 0) + 1
        if progress:
            progress(item_id, what)
    return counts


def geocode_missing(session: Session) -> int:
    """Fill `place` for items with GPS and no place (after geo-download)."""
    from . import geo
    from . import meta as meta_svc

    if not geo.available():
        return 0
    ids = session.execute(
        sa.select(Item.id).where(Item.has_gps.is_(True), Item.place.is_(None))
    ).scalars().all()
    n = 0
    for item_id in ids:
        item = session.get(Item, item_id)
        if item is None or item.lat is None or item.lon is None:
            continue
        found = geo.lookup(item.lat, item.lon)
        if not found:
            continue
        media = dict(item.media or {})
        media["place"] = found
        session.execute(sa.update(Item).where(Item.id == item_id).values(
            media=media, place=geo.label(found), modified_at=Item.modified_at))
        session.commit()
        meta_svc.sync_item(session, item_id)
        n += 1
    return n


def remove_derived(item_dir: Path) -> None:
    for name in DERIVED_FILES:
        (item_dir / name).unlink(missing_ok=True)
    for name in ("proxy.mp4", "proxy.m4a"):
        (item_dir / name).unlink(missing_ok=True)
