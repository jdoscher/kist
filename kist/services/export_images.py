"""Export presets: resized, converted, watermarked copies of images, or a
contact sheet, without touching the originals.

    POST /api/items/export-images {item_ids, preset}
    preset {max_side, format jpg|webp|png|avif, quality,
            strip_metadata (default true; GPS is always removed),
            watermark? {text, position, opacity},
            contact_sheet? {cols, caption}}

returns a zip of the images, or a PDF contact sheet. `preset` is an object
or the name of a saved preset. Saved presets are plain JSON in
library/.kist/export-presets.json (managed through /api/export-presets);
a few sensible ones exist until the file says otherwise.
"""

import io
import json
import re
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from ..config import get_settings
from ..filetypes import kind_for
from . import meta as meta_svc
from . import thumbs, validation

FILE = "export-presets.json"
FORMATS = {"jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP", "png": "PNG", "avif": "AVIF"}
POSITIONS = ("bottom-right", "bottom-left", "top-right", "top-left", "center")
MAX_ITEMS = 500
MAX_SIDE = 16384
NAME_RE = re.compile(r"^[A-Za-z0-9 _.-]{1,60}$")
GPS_IFD = 0x8825

BUILTIN = {
    "web-2048": {"max_side": 2048, "format": "jpg", "quality": 85, "strip_metadata": True},
    "full-res": {"max_side": None, "format": "jpg", "quality": 92, "strip_metadata": False},
    "instagram-1080": {"max_side": 1080, "format": "jpg", "quality": 90,
                       "strip_metadata": True},
    "client-proof": {"max_side": 1600, "format": "jpg", "quality": 80, "strip_metadata": True,
                     "watermark": {"text": "PROOF", "position": "center", "opacity": 0.35}},
    "contact-sheet": {"max_side": 400, "format": "jpg", "quality": 80, "strip_metadata": True,
                      "contact_sheet": {"cols": 4, "caption": "filename"}},
}


def clean(preset: dict) -> dict:
    if not isinstance(preset, dict):
        raise validation.ValidationError("preset must be an object or a preset name")
    known = {"name", "max_side", "format", "quality", "strip_metadata", "watermark",
             "contact_sheet"}
    unknown = set(preset) - known
    if unknown:
        raise validation.ValidationError(f"unknown preset fields: {', '.join(sorted(unknown))}")
    out: dict = {}
    if preset.get("name") is not None:
        name = str(preset["name"]).strip()
        if not NAME_RE.match(name):
            raise validation.ValidationError(
                "preset names are 1-60 letters, digits, spaces, '_', '-' or '.'")
        out["name"] = name
    side = preset.get("max_side")
    if side is not None:
        try:
            side = int(side)
        except (TypeError, ValueError):
            raise validation.ValidationError("max_side must be a whole number") from None
        if not 16 <= side <= MAX_SIDE:
            raise validation.ValidationError(f"max_side must be 16 to {MAX_SIDE}")
    out["max_side"] = side
    fmt = str(preset.get("format") or "jpg").lower()
    if fmt not in FORMATS:
        raise validation.ValidationError("format must be jpg, webp, png or avif")
    out["format"] = "jpg" if fmt == "jpeg" else fmt
    try:
        quality = int(preset.get("quality") if preset.get("quality") is not None else 85)
    except (TypeError, ValueError):
        raise validation.ValidationError("quality must be 1 to 100") from None
    if not 1 <= quality <= 100:
        raise validation.ValidationError("quality must be 1 to 100")
    out["quality"] = quality
    out["strip_metadata"] = bool(preset.get("strip_metadata", True))
    wm = preset.get("watermark")
    if wm:
        if not isinstance(wm, dict) or not str(wm.get("text") or "").strip():
            raise validation.ValidationError("watermark needs text")
        pos = str(wm.get("position") or "bottom-right")
        if pos not in POSITIONS:
            raise validation.ValidationError(f"watermark position: one of {', '.join(POSITIONS)}")
        try:
            opacity = float(wm.get("opacity") if wm.get("opacity") is not None else 0.5)
        except (TypeError, ValueError):
            raise validation.ValidationError("watermark opacity is 0 to 1") from None
        if not 0 <= opacity <= 1:
            raise validation.ValidationError("watermark opacity is 0 to 1")
        out["watermark"] = {"text": str(wm["text"])[:200], "position": pos, "opacity": opacity}
    cs = preset.get("contact_sheet")
    if cs:
        if not isinstance(cs, dict):
            cs = {}
        try:
            cols = int(cs.get("cols") or 4)
        except (TypeError, ValueError):
            raise validation.ValidationError("contact_sheet.cols is 1 to 10") from None
        if not 1 <= cols <= 10:
            raise validation.ValidationError("contact_sheet.cols is 1 to 10")
        caption = str(cs.get("caption") or "filename")
        if caption not in ("filename", "filename+rating", "none"):
            raise validation.ValidationError(
                "contact_sheet.caption is filename, filename+rating or none")
        out["contact_sheet"] = {"cols": cols, "caption": caption}
    return out


# ---------- saved presets ----------


def _path() -> Path:
    return get_settings().state_dir / FILE


def list_presets() -> dict[str, dict]:
    p = _path()
    if not p.is_file():
        return {k: {**clean(v), "name": k} for k, v in BUILTIN.items()}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {k: {**clean(v), "name": k} for k, v in (data.get("presets") or {}).items()}
    except (OSError, ValueError, AttributeError):
        raise validation.ValidationError(f"{FILE} is unreadable; fix or remove it") from None


def _save(presets: dict[str, dict]) -> None:
    meta_svc.atomic_write_text(
        _path(), json.dumps({"version": 1, "presets": {
            k: {f: v for f, v in p.items() if f != "name"} for k, p in presets.items()
        }}, indent=2))


def put_preset(name: str, preset: dict) -> dict:
    cleaned = clean({**preset, "name": name})
    presets = list_presets()
    presets[cleaned["name"]] = cleaned
    _save(presets)
    return cleaned


def delete_preset(name: str) -> None:
    presets = list_presets()
    if name not in presets:
        raise validation.ValidationError(f"no preset named {name!r}")
    del presets[name]
    _save(presets)


def resolve(preset) -> dict:
    if isinstance(preset, str):
        presets = list_presets()
        if preset not in presets:
            raise validation.ValidationError(f"no preset named {preset!r}")
        return presets[preset]
    return clean(preset)


def check_file() -> list[str]:
    """verify: the presets file, if any, parses."""
    try:
        list_presets()
        return []
    except validation.ValidationError as exc:
        return [str(exc)]


# ---------- rendering ----------


def _source(item) -> Path | None:
    d = get_settings().items_dir / item.id
    original = d / f"original.{item.ext}"
    if kind_for(item.mime, item.ext) == "image" and original.is_file():
        return original
    for name in (thumbs.SCREEN_FILE, "display.webp", "thumb_1024.webp"):
        if (d / name).is_file():
            return d / name
    return None


def _open(path: Path) -> Image.Image:
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    Image.MAX_IMAGE_PIXELS = 300_000_000
    im = Image.open(path)
    im.load()
    return im


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _watermark(im: Image.Image, wm: dict) -> Image.Image:
    base = im.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    size = max(12, min(base.size) // 18)
    font = _font(size)
    box = draw.textbbox((0, 0), wm["text"], font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    pad = size // 2
    x = {"bottom-right": base.width - w - pad, "top-right": base.width - w - pad,
         "bottom-left": pad, "top-left": pad, "center": (base.width - w) // 2}[wm["position"]]
    y = {"bottom-right": base.height - h - pad, "bottom-left": base.height - h - pad,
         "top-right": pad, "top-left": pad, "center": (base.height - h) // 2}[wm["position"]]
    alpha = int(255 * wm["opacity"])
    draw.text((x + 1, y + 1), wm["text"], font=font, fill=(0, 0, 0, alpha))
    draw.text((x, y), wm["text"], font=font, fill=(255, 255, 255, alpha))
    return Image.alpha_composite(base, layer)


def render(path: Path, preset: dict) -> tuple[bytes, str]:
    """One processed image: (bytes, extension)."""
    im = _open(path)
    exif = im.getexif()
    icc = im.info.get("icc_profile")
    im = ImageOps.exif_transpose(im)
    side = preset.get("max_side")
    if side and max(im.size) > side:
        im.thumbnail((side, side), Image.Resampling.LANCZOS)
    if preset.get("watermark"):
        im = _watermark(im, preset["watermark"])
    fmt = FORMATS[preset["format"]]
    if fmt == "JPEG" and im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    elif im.mode not in ("RGB", "RGBA", "L", "LA"):
        im = im.convert("RGBA" if "A" in im.mode else "RGB")
    kwargs: dict = {}
    if preset["format"] in ("jpg", "webp", "avif"):
        kwargs["quality"] = preset["quality"]
    if not preset.get("strip_metadata", True):
        # keep the camera data, never the location
        if GPS_IFD in exif:
            del exif[GPS_IFD]
        exif[0x0112] = 1  # the pixels are upright now
        kwargs["exif"] = exif.tobytes()
        if icc:
            kwargs["icc_profile"] = icc
    elif icc:
        kwargs["icc_profile"] = icc  # colour, not metadata: keeps colours right
    buf = io.BytesIO()
    im.save(buf, fmt, **kwargs)
    return buf.getvalue(), preset["format"]


def export_zip(items: list, preset: dict) -> bytes:
    from .exports import Names, safe_component

    names = Names()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for item in items:
            src = _source(item)
            if src is None:
                continue
            data, ext = render(src, preset)
            raw = item.filename.rsplit(".", 1)[0] if "." in item.filename else item.filename
            stem = names.claim("", safe_component(raw, item.id), ext)
            zf.writestr(f"{stem}.{ext}", data)
    return buf.getvalue()


def contact_sheet(items: list, preset: dict) -> bytes:
    """A PDF of A4 pages (150 dpi) with a grid of the images and captions."""
    cs = preset.get("contact_sheet") or {"cols": 4, "caption": "filename"}
    cols = cs["cols"]
    page_w, page_h, margin = 1240, 1754, 60
    cell_w = (page_w - 2 * margin) // cols
    caption_h = 0 if cs["caption"] == "none" else 36
    cell_h = cell_w + caption_h
    rows_per_page = max(1, (page_h - 2 * margin) // cell_h)
    font = _font(16)
    pages: list[Image.Image] = []
    page = None
    for n, item in enumerate(items):
        slot = n % (cols * rows_per_page)
        if slot == 0:
            page = Image.new("RGB", (page_w, page_h), "white")
            pages.append(page)
        src = _source(item)
        x = margin + (slot % cols) * cell_w
        y = margin + (slot // cols) * cell_h
        draw = ImageDraw.Draw(page)
        if src is not None:
            try:
                im = ImageOps.exif_transpose(_open(src)).convert("RGB")
                im.thumbnail((cell_w - 12, cell_w - 12), Image.Resampling.LANCZOS)
                page.paste(im, (x + (cell_w - im.width) // 2, y + (cell_w - im.height) // 2))
            except Exception:
                draw.rectangle((x + 6, y + 6, x + cell_w - 6, y + cell_w - 6), outline="#999")
        if caption_h:
            text = item.filename
            if cs["caption"] == "filename+rating" and item.rating:
                text += "  " + "*" * item.rating
            while text and draw.textlength(text, font=font) > cell_w - 8:
                text = text[:-2]
            draw.text((x + 4, y + cell_w + 6), text, fill="black", font=font)
    if not pages:
        pages = [Image.new("RGB", (page_w, page_h), "white")]
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", resolution=150.0, save_all=True, append_images=pages[1:])
    return buf.getvalue()


def export(session, item_ids: list[str], preset) -> tuple[bytes, str, str]:
    """(body, media type, download name)."""
    from . import items as items_svc

    preset = resolve(preset)
    ids = list(dict.fromkeys(item_ids or []))
    if not ids:
        raise validation.ValidationError("item_ids is empty")
    if len(ids) > MAX_ITEMS:
        raise validation.ValidationError(f"at most {MAX_ITEMS} items at once")
    items = [items_svc.get_item(session, i, with_deleted=False) for i in ids]
    name = preset.get("name") or "export"
    if preset.get("contact_sheet"):
        return contact_sheet(items, preset), "application/pdf", f"{name}.pdf"
    return export_zip(items, preset), "application/zip", f"{name}.zip"
