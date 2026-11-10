"""Draw a board: a PNG/PDF (Pillow) or one self-contained HTML file.

The board's nodes are composited from the items' existing preview copies
(thumb_256/512/1024, display_2048, display), never the originals, so an
export is quick and needs no decoders. Notes, text, shapes, colour swatches,
links and group frames are drawn simply; edges are straight arrows along
the line between node centres, from one node's edge to the other's (as the
editor draws them). An item node whose item is gone (purged) is drawn as a grey
"missing item" box.

The HTML export is a single file with every image inlined as base64 and
absolutely positioned elements, so it opens offline in any browser.
"""

import base64
import html
import io
import math
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from ..config import get_settings
from . import thumbs

# the largest export we make (pixels); bigger requests are scaled down
MAX_PIXELS = 40_000_000
MAX_SIDE = 16_000
PAD = 40
DEFAULT_BG = "#f5f5f4"
NOTE_BG = "#fff3a3"
INK = "#1c1c1c"
MISSING_BG = "#d4d4d4"
PREVIEWS = (  # (file, longest side it can serve)
    ("thumb_256.webp", 256), ("thumb_512.webp", 512), ("thumb_1024.webp", 1024),
    (thumbs.SCREEN_FILE, 2048), ("display.webp", 4096),
)

# item_id -> does the item exist (else it is drawn as a missing item)
Resolver = Callable[[str], bool]


def preview_for(item_id: str, longest: float) -> Path | None:
    """The smallest preview file of the item that is at least `longest`
    pixels on its long side (else the largest there is)."""
    d = get_settings().items_dir / item_id
    have = [(d / name, side) for name, side in PREVIEWS if (d / name).is_file()]
    if not have:
        return None
    for path, side in have:
        if side >= longest:
            return path
    return have[-1][0]


def bounds(doc: dict) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) around every node (rotation ignored), padded."""
    nodes = doc.get("nodes") or []
    if not nodes:
        return 0.0, 0.0, 800.0, 600.0
    x0 = min(n["x"] for n in nodes)
    y0 = min(n["y"] for n in nodes)
    x1 = max(n["x"] + n["w"] for n in nodes)
    y1 = max(n["y"] + n["h"] for n in nodes)
    return x0 - PAD, y0 - PAD, x1 + PAD, y1 + PAD


def fit_scale(doc: dict, scale: float, max_pixels: int = MAX_PIXELS) -> float:
    x0, y0, x1, y1 = bounds(doc)
    w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    scale = max(0.01, scale)
    if w * h * scale * scale > max_pixels:
        scale = math.sqrt(max_pixels / (w * h))
    scale = min(scale, MAX_SIDE / w, MAX_SIDE / h)
    return scale


def _color(value, default: str) -> str:
    if isinstance(value, str) and value.strip():
        v = value.strip()
        try:
            from PIL import ImageColor

            ImageColor.getrgb(v)
            return v
        except ValueError:
            return default
    return default


def _font(size: float):
    try:
        return ImageFont.load_default(size=max(6, int(size)))
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: float) -> list[str]:
    lines: list[str] = []
    for para in str(text).splitlines() or [""]:
        words = para.split(" ")
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip() if line else word
            if draw.textlength(candidate, font=font) <= width or not line:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def edge_points(a: dict, b: dict) -> tuple[float, float, float, float]:
    """Where an arrow from node a to node b starts and ends, in board
    coordinates: on the line between their centres, where it leaves a's box
    and enters b's (centre to centre drew the line over both pictures and
    hid the arrowhead inside the target)."""
    ax, ay = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2
    bx, by = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2

    def exit_t(n: dict, dx: float, dy: float) -> float:
        # fraction of (dx, dy) from a node's centre to its border
        ts = []
        if dx:
            ts.append(n["w"] / 2 / abs(dx))
        if dy:
            ts.append(n["h"] / 2 / abs(dy))
        return min(ts) if ts else 0.0

    dx, dy = bx - ax, by - ay
    t0, t1 = exit_t(a, dx, dy), 1.0 - exit_t(b, dx, dy)
    if t0 >= t1:  # overlapping boxes: centre to centre
        return ax, ay, bx, by
    return ax + dx * t0, ay + dy * t0, ax + dx * t1, ay + dy * t1


def ordered(doc: dict) -> list[dict]:
    """Nodes back to front: groups first (frames sit behind), then by z."""
    nodes = list(doc.get("nodes") or [])
    return [n for _, n in sorted(
        enumerate(nodes), key=lambda p: (p[1]["type"] != "group", p[1].get("z", 0), p[0])
    )]


def _item_tile(item_id: str, crop: dict | None, w: int, h: int,
               exists: Resolver) -> Image.Image | None:
    if not exists(item_id):
        return None
    path = preview_for(item_id, max(w, h) / max(0.05, min((crop or {}).get("w", 1),
                                                          (crop or {}).get("h", 1))))
    if path is None:
        return None
    with Image.open(path) as src:
        img = src.convert("RGBA")
    if crop:
        cw, ch = img.size
        box = (
            int(crop.get("x", 0) * cw), int(crop.get("y", 0) * ch),
            int((crop.get("x", 0) + crop.get("w", 1)) * cw),
            int((crop.get("y", 0) + crop.get("h", 1)) * ch),
        )
        if box[2] > box[0] and box[3] > box[1]:
            img = img.crop(box)
    # fill the node like CSS object-fit: cover
    return ImageOps.fit(img, (max(1, w), max(1, h)), method=Image.Resampling.LANCZOS)


def render(doc: dict, scale: float, exists: Resolver,
           max_pixels: int = MAX_PIXELS) -> Image.Image:
    """The board as an RGB image at `scale` (fitted under max_pixels)."""
    scale = fit_scale(doc, scale, max_pixels)
    x0, y0, x1, y1 = bounds(doc)
    size = (max(1, int((x1 - x0) * scale)), max(1, int((y1 - y0) * scale)))
    bg = _color((doc.get("canvas") or {}).get("bg"), DEFAULT_BG)
    canvas = Image.new("RGBA", size, bg)
    by_id = {n["id"]: n for n in doc.get("nodes") or []}

    def box(n: dict) -> tuple[int, int, int, int]:
        return (int((n["x"] - x0) * scale), int((n["y"] - y0) * scale),
                max(1, int(n["w"] * scale)), max(1, int(n["h"] * scale)))

    for n in ordered(doc):
        left, top, w, h = box(n)
        style = n.get("style") or {}
        tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(tile)
        t = n["type"]
        font = _font(float(style.get("font_size", 16)) * scale)
        if t == "item":
            img = _item_tile(n.get("item_id", ""), n.get("crop"), w, h, exists)
            if img is None:
                d.rectangle((0, 0, w - 1, h - 1), fill=MISSING_BG, outline="#999999")
                label = "missing item" if not exists(n.get("item_id", "")) else "no preview"
                d.text((6, 6), label, fill="#555555", font=_font(14 * scale))
            else:
                tile.paste(img, (0, 0))
        elif t == "note":
            d.rectangle((0, 0, w - 1, h - 1), fill=_color(style.get("bg"), NOTE_BG))
            _text(d, n.get("text") or "", font, w, _color(style.get("color"), INK), scale)
        elif t == "text":
            _text(d, n.get("text") or "", font, w, _color(style.get("color"), INK), scale)
        elif t == "color":
            fill = _color(style.get("bg") or n.get("text"), "#888888")
            d.rectangle((0, 0, w - 1, h - 1), fill=fill)
            d.text((6, h - 22 * scale), str(n.get("text") or fill), fill="#ffffff",
                   font=_font(12 * scale))
        elif t == "shape":
            stroke = _color(style.get("stroke"), INK)
            fill = _color(style.get("fill"), "") or None
            width = max(1, int(float(style.get("stroke_width", 2)) * scale))
            if style.get("shape") == "ellipse":
                d.ellipse((0, 0, w - 1, h - 1), outline=stroke, fill=fill, width=width)
            else:
                d.rectangle((0, 0, w - 1, h - 1), outline=stroke, fill=fill, width=width)
        elif t == "link":
            d.rectangle((0, 0, w - 1, h - 1), fill="#ffffff", outline="#3b82f6", width=2)
            _text(d, n.get("text") or str(style.get("url") or "link"), font, w, "#1d4ed8", scale)
        elif t == "group":
            d.rectangle((0, 0, w - 1, h - 1), fill=_color(style.get("bg"), "#00000010"),
                        outline=_color(style.get("stroke"), "#8a8a8a"),
                        width=max(1, int(2 * scale)))
            if n.get("text"):
                d.text((8 * scale, 6 * scale), str(n["text"]), fill=INK, font=_font(14 * scale))
        rot = float(n.get("rot") or 0)
        if rot:
            tile = tile.rotate(-rot, expand=True, resample=Image.Resampling.BICUBIC)
            left += (w - tile.width) // 2
            top += (h - tile.height) // 2
        canvas.paste(tile, (left, top), tile)

    draw = ImageDraw.Draw(canvas)
    for e in doc.get("edges") or []:
        a, b = by_id.get(e["from"]), by_id.get(e["to"])
        if not a or not b:
            continue
        pax, pay, pbx, pby = edge_points(a, b)
        ax, ay = (pax - x0) * scale, (pay - y0) * scale
        bx, by = (pbx - x0) * scale, (pby - y0) * scale
        color = _color((e.get("style") or {}).get("stroke"), INK)
        width = max(1, int(2 * scale))
        draw.line((ax, ay, bx, by), fill=color, width=width)
        angle = math.atan2(by - ay, bx - ax)
        head = 12 * scale
        draw.polygon([
            (bx, by),
            (bx - head * math.cos(angle - 0.4), by - head * math.sin(angle - 0.4)),
            (bx - head * math.cos(angle + 0.4), by - head * math.sin(angle + 0.4)),
        ], fill=color)
        if e.get("label"):
            draw.text(((ax + bx) / 2 + 4, (ay + by) / 2 + 4), str(e["label"]), fill=color,
                      font=_font(13 * scale))
    return canvas.convert("RGB")


def _text(d: ImageDraw.ImageDraw, text: str, font, w: int, color: str, scale: float) -> None:
    pad = 8 * scale
    y = pad
    line_h = (font.size if hasattr(font, "size") else 12) * 1.25
    for line in _wrap(d, text, font, max(1, w - 2 * pad)):
        d.text((pad, y), line, fill=color, font=font)
        y += line_h


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False)
    return buf.getvalue()


def pdf_bytes(img: Image.Image, scale: float) -> bytes:
    buf = io.BytesIO()
    # one board pixel at scale 1 is one PDF point
    img.save(buf, "PDF", resolution=72.0 * max(scale, 0.01))
    return buf.getvalue()


def snapshot(doc: dict, exists: Resolver, longest: int = 512) -> Image.Image:
    """A small preview of the whole board (thumb.webp)."""
    x0, y0, x1, y1 = bounds(doc)
    scale = longest / max(1.0, x1 - x0, y1 - y0)
    return render(doc, min(scale, 1.0), exists, max_pixels=longest * longest)


# ---------- HTML ----------


def _data_uri(path: Path) -> str:
    mime = "image/webp" if path.suffix == ".webp" else "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def render_html(doc: dict, exists: Resolver, title: str) -> str:
    """One self-contained HTML file: inlined images, absolutely positioned."""
    x0, y0, x1, y1 = bounds(doc)
    width, height = int(x1 - x0), int(y1 - y0)
    bg = _color((doc.get("canvas") or {}).get("bg"), DEFAULT_BG)
    esc = html.escape
    parts: list[str] = []
    uris: dict[str, str] = {}
    for n in ordered(doc):
        style = n.get("style") or {}
        pos = (f"left:{n['x'] - x0:.1f}px;top:{n['y'] - y0:.1f}px;"
               f"width:{n['w']:.1f}px;height:{n['h']:.1f}px;z-index:{int(n.get('z', 0))};")
        if n.get("rot"):
            pos += f"transform:rotate({float(n['rot']):.2f}deg);"
        t = n["type"]
        if t == "item":
            item_id = n.get("item_id", "")
            path = preview_for(item_id, max(n["w"], n["h"])) if exists(item_id) else None
            if path is None:
                parts.append(f'<div class="n missing" style="{pos}">missing item</div>')
                continue
            key = str(path)
            if key not in uris:
                uris[key] = _data_uri(path)
            crop = n.get("crop") or {}
            cw, ch = float(crop.get("w", 1) or 1), float(crop.get("h", 1) or 1)
            cx, cy = float(crop.get("x", 0)), float(crop.get("y", 0))
            img_style = (f"position:absolute;width:{100 / cw:.4f}%;height:{100 / ch:.4f}%;"
                         f"left:{-cx / cw * 100:.4f}%;top:{-cy / ch * 100:.4f}%;object-fit:cover;")
            parts.append(f'<div class="n item" style="{pos}">'
                         f'<img alt="" style="{img_style}" src="{uris[key]}"></div>')
        elif t in ("note", "text", "link", "color"):
            bgc = {"note": _color(style.get("bg"), NOTE_BG), "text": "transparent",
                   "link": "#ffffff", "color": _color(style.get("bg") or n.get("text"),
                                                       "#888888")}[t]
            body = esc(str(n.get("text") or ""))
            if t == "link" and style.get("url"):
                body = (f'<a href="{esc(str(style["url"]))}" rel="noopener noreferrer">'
                        f'{body or esc(str(style["url"]))}</a>')
            parts.append(f'<div class="n {t}" style="{pos}background:{esc(bgc)};'
                         f'color:{esc(_color(style.get("color"), INK))};'
                         f'font-size:{float(style.get("font_size", 16)):.0f}px">{body}</div>')
        elif t == "shape":
            radius = "50%" if style.get("shape") == "ellipse" else "0"
            parts.append(f'<div class="n" style="{pos}border:2px solid '
                         f'{esc(_color(style.get("stroke"), INK))};border-radius:{radius};'
                         f'background:{esc(_color(style.get("fill"), "transparent"))}"></div>')
        elif t == "group":
            parts.append(f'<div class="n group" style="{pos}">{esc(str(n.get("text") or ""))}'
                         "</div>")
    by_id = {n["id"]: n for n in doc.get("nodes") or []}
    lines = []
    for e in doc.get("edges") or []:
        a, b = by_id.get(e["from"]), by_id.get(e["to"])
        if not a or not b:
            continue
        pax, pay, pbx, pby = edge_points(a, b)
        ax, ay, bx, by = pax - x0, pay - y0, pbx - x0, pby - y0
        lines.append(f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" '
                     'marker-end="url(#a)"/>')
        if e.get("label"):
            lines.append(f'<text x="{(ax + bx) / 2 + 4:.1f}" y="{(ay + by) / 2 - 4:.1f}">'
                         f'{esc(str(e["label"]))}</text>')
    svg = (f'<svg width="{width}" height="{height}" style="position:absolute;left:0;top:0;'
           f'z-index:100000;pointer-events:none"><defs><marker id="a" viewBox="0 0 10 10" '
           'refX="10" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
           f'<path d="M0,0L10,5L0,10z" fill="{INK}"/></marker></defs>'
           f'<g stroke="{INK}" stroke-width="2" fill="{INK}">{"".join(lines)}</g></svg>')
    return (
        "<!doctype html>\n<html><head><meta charset=\"utf-8\">"
        f"<title>{esc(title)}</title>"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<style>body{margin:0;background:" + esc(bg) + ";font-family:system-ui,sans-serif}"
        ".board{position:relative;width:" + str(width) + "px;height:" + str(height) + "px}"
        ".n{position:absolute;box-sizing:border-box;overflow:hidden;white-space:pre-wrap;"
        "padding:0}.note,.text,.link,.color{padding:8px}.item img{display:block}"
        ".missing{background:" + MISSING_BG + ";color:#555;padding:6px;font-size:13px}"
        ".group{border:2px solid #8a8a8a;padding:6px;font-size:14px}</style></head>"
        f'<body><div class="board">{"".join(parts)}{svg}</div></body></html>\n'
    )
