"""Present: the show as a PDF leave-behind (POST /api/present/pdf,
GET /api/shows/{id}/pdf, the MCP tool present_pdf).

Two layouts, both built on the image export's pieces
(services/export_images: the same image source, decoder and font):

- "slides": one landscape page per slide (A4, A3, Letter or Tabloid at
  150 dpi), the image fitted whole (never cropped), a compare slide as two
  images side by side, and under it the caption: title and credit (and the
  licence), the filename only when the show shows filenames, the slide
  number, and the presenter notes when asked.
- "sheet": the existing contact sheet (export_images.contact_sheet), every
  item of the show in a grid.

Pages are white whatever the show's surround: it's for printing. Images
with an ICC profile are converted to sRGB, so a P3 or Adobe RGB original
keeps its colours in the PDF (Pillow's plain RGB conversion would drop the
profile and shift them).
"""

import io
import logging
import textwrap
from pathlib import Path

from PIL import Image, ImageCms, ImageDraw, ImageOps

from ..config import get_settings
from ..filetypes import kind_for
from . import export_images, thumbs, validation
from .present import PAGE_SIZES, PDF_LAYOUTS

log = logging.getLogger("kist.present")

DPI = 150
PAGES = {  # landscape, at 150 dpi
    "a4": (1754, 1240),
    "a3": (2480, 1754),
    "letter": (1650, 1275),
    "tabloid": (2550, 1650),
}
MARGIN = 72
GUTTER = 36
MAX_SLIDES = 300
# decode originals up to this size; bigger ones use the 2048 screen copy
ORIGINAL_MAX_BYTES = 30 * 1024 * 1024
WEB_NATIVE = {"png", "jpg", "jpeg", "gif", "webp", "avif"}


def clean_pdf_options(data: dict | None) -> dict:
    data = dict(data or {})
    unknown = set(data) - {"layout", "page", "captions", "filenames", "notes"}
    if unknown:
        raise validation.ValidationError(f"unknown PDF options: {', '.join(sorted(unknown))}")
    out = {"layout": data.get("layout") or "slides", "page": data.get("page") or "a4",
           "captions": data.get("captions", True), "filenames": data.get("filenames", False),
           "notes": data.get("notes", False)}
    if out["layout"] not in PDF_LAYOUTS:
        raise validation.ValidationError(f"layout is one of {', '.join(PDF_LAYOUTS)}")
    if out["page"] not in PAGE_SIZES:
        raise validation.ValidationError(f"page is one of {', '.join(PAGE_SIZES)}")
    for key in ("captions", "filenames", "notes"):
        if not isinstance(out[key], bool):
            raise validation.ValidationError(f"{key} is true or false")
    return out


def _source(item) -> Path | None:
    d = get_settings().items_dir / item.id
    original = d / f"original.{item.ext}"
    if (kind_for(item.mime, item.ext) == "image" and (item.ext or "").lower() in WEB_NATIVE
            and (item.size_bytes or 0) <= ORIGINAL_MAX_BYTES and original.is_file()):
        return original
    for name in ("display.webp", thumbs.SCREEN_FILE, "thumb_1024.webp"):
        if (d / name).is_file():
            return d / name
    return export_images._source(item)


_SRGB = None


def _to_srgb(im: Image.Image) -> Image.Image:
    """Convert to RGB through the image's own ICC profile, if it has one."""
    global _SRGB
    icc = im.info.get("icc_profile")
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        im2 = bg.convert("RGB")
        im2.info["icc_profile"] = icc
        im = im2
    if icc:
        try:
            if _SRGB is None:
                _SRGB = ImageCms.createProfile("sRGB")
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            if im.mode not in ("RGB", "CMYK"):
                im = im.convert("RGB")
            return ImageCms.profileToProfile(im, src, _SRGB, outputMode="RGB") or im.convert("RGB")
        except Exception:  # a broken profile: plain conversion
            log.debug("ICC conversion failed", exc_info=True)
    return im.convert("RGB")


def _image(item, box: tuple[int, int]) -> Image.Image | None:
    src = _source(item)
    if src is None:
        return None
    try:
        im = export_images._open(src)
        if hasattr(im, "draft") and im.format == "JPEG":
            im.draft("RGB", box)
        im = ImageOps.exif_transpose(im)
        icc = im.info.get("icc_profile")
        im.thumbnail(box, Image.Resampling.LANCZOS)
        if icc:
            im.info["icc_profile"] = icc
        return _to_srgb(im)
    except Exception:
        log.warning("present pdf: can't draw %s", item.id, exc_info=True)
        return None


def _fit(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _caption_lines(it: dict, opts: dict) -> list[str]:
    head = " — ".join(x for x in (it.get("title"), it.get("credit")) if x)
    if it.get("license"):
        head = f"{head} · {it['license']}" if head else it["license"]
    lines = [head] if head else []
    if opts["filenames"]:
        lines.append(it["filename"])
    return lines


def _slides_pdf(resolved: dict, items_by_id: dict, opts: dict) -> list[Image.Image]:
    page_w, page_h = PAGES[opts["page"]]
    font = export_images._font(22)
    small = export_images._font(18)
    slides = resolved["slides"][:MAX_SLIDES]
    pages = []
    for n, slide in enumerate(slides, 1):
        page = Image.new("RGB", (page_w, page_h), "white")
        draw = ImageDraw.Draw(page)
        cells = slide["items"]
        notes = ""
        if opts["notes"]:
            notes = "  ".join(x for x in (c.get("notes") or c.get("caption") for c in cells) if x)
        chars = int((page_w - 2 * MARGIN) / 11)
        note_lines = textwrap.wrap(notes, width=chars)[:4] if notes else []
        cap_h = (64 if opts["captions"] or opts["filenames"] else 34) + 26 * len(note_lines)
        area_w = page_w - 2 * MARGIN
        area_h = page_h - 2 * MARGIN - cap_h
        cell_w = (area_w - GUTTER * (len(cells) - 1)) // len(cells)
        per_cell_cap = 52 if (opts["captions"] or opts["filenames"]) and len(cells) > 1 else 0
        for k, it in enumerate(cells):
            item = items_by_id.get(it["id"])
            x = MARGIN + k * (cell_w + GUTTER)
            box = (cell_w, area_h - per_cell_cap)
            im = _image(item, box) if item is not None else None
            if im is not None:
                page.paste(im, (x + (cell_w - im.width) // 2,
                                MARGIN + (box[1] - im.height) // 2))
            else:
                draw.rectangle((x, MARGIN, x + cell_w, MARGIN + box[1]), outline="#999999")
                draw.text((x + 12, MARGIN + 12), _fit(draw, it["filename"], small, cell_w - 24),
                          fill="#555555", font=small)
            if per_cell_cap:
                y = MARGIN + box[1] + 8
                for line in _caption_lines(it, opts)[:2]:
                    draw.text((x, y), _fit(draw, line, small, cell_w), fill="#222222", font=small)
                    y += 22
        y = page_h - MARGIN - cap_h + 18
        if len(cells) == 1 and (opts["captions"] or opts["filenames"]):
            lines = _caption_lines(cells[0], opts) if opts["captions"] else (
                [cells[0]["filename"]] if opts["filenames"] else [])
            if lines:
                draw.text((MARGIN, y), _fit(draw, lines[0], font, area_w - 140), fill="#111111",
                          font=font)
                if len(lines) > 1:
                    draw.text((MARGIN, y + 28), _fit(draw, lines[1], small, area_w - 140),
                              fill="#555555", font=small)
        num = f"{n} / {len(slides)}"
        draw.text((page_w - MARGIN - draw.textlength(num, font=small), y), num, fill="#555555",
                  font=small)
        ny = page_h - MARGIN - 26 * len(note_lines)
        for line in note_lines:
            draw.text((MARGIN, ny), line, fill="#333333", font=small)
            ny += 26
        pages.append(page)
    return pages


def render(session, resolved: dict, options: dict | None = None) -> bytes:
    """The resolved show (present.resolve / show_slides) as PDF bytes."""
    from ..models import Item

    opts = clean_pdf_options(options)
    ids = list(dict.fromkeys(it["id"] for s in resolved["slides"] for it in s["items"]))
    rows = {i.id: i for i in session.query(Item).filter(Item.id.in_(ids or [""])).all()}
    if opts["layout"] == "sheet":
        ordered = [rows[i] for i in ids if i in rows][:export_images.MAX_ITEMS]
        return export_images.contact_sheet(ordered, {"contact_sheet": {
            "cols": 4, "caption": "filename" if opts["filenames"] else "none"}})
    pages = _slides_pdf(resolved, rows, opts)
    if not pages:
        pages = [Image.new("RGB", PAGES[opts["page"]], "white")]
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", resolution=float(DPI), save_all=True, append_images=pages[1:],
                  title=resolved.get("name") or "Show", producer="Kist")
    return buf.getvalue()
