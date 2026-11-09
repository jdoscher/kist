"""Build Kist's brand assets: the Brass corners mark, the wordmark and the icon kit.

Everything is generated from the construction below, so a change to the
mark is one edit here plus a re-run. Run from the repository root:

    python docs/brand/build_brand.py            # SVG masters + web/src/brandPaths.ts
    python docs/brand/build_brand.py --raster   # also the PNG/ICO icons (needs npx + Pillow)

Needs fontTools (reads Instrument Serif from web/node_modules/@fontsource,
so run `npm ci` in web/ first) and, for --raster, Pillow plus Node (resvg via
npx). The wordmark is outlined to paths, so nothing here needs the font at
runtime. Construction notes: docs/brand.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "docs" / "brand"
PUBLIC = ROOT / "web" / "public"
FONT = ROOT / "web/node_modules/@fontsource/instrument-serif/files/instrument-serif-latin-400-normal.woff"

# ---------- palette (docs/brand.md) ----------
BRASS = "#E8B04B"  # on dark
BRASS_DEEP = "#8C5A00"  # on light
TILE = "#1C1C1A"  # app tile
INK_DARK = "#ECEAE4"  # text on dark
INK_LIGHT = "#1C1B19"  # text on light
PAPER = "#FAF9F6"
BG_DARK = "#131312"

# ---------- the mark: two brass corners and the kept item ----------
# All on a 64-unit grid. Every length is a multiple of 4, so at 16 px (4
# units per pixel) every edge lands on a whole pixel.
R_OUTER = 2.0  # outer corner of each bracket: stamped metal, not a line icon
R_INNER = 0.5  # inner corner and arm ends
R_ITEM = 1.5


def rounded_poly(pts: list[tuple[float, float]], radii: list[float]) -> str:
    """Closed path through axis-aligned `pts`, each corner rounded by its radius."""
    n = len(pts)
    out = []
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        r = radii[i]
        din = ((p1[0] - p0[0]) and (1 if p1[0] > p0[0] else -1), (p1[1] - p0[1]) and (1 if p1[1] > p0[1] else -1))
        dout = ((p2[0] - p1[0]) and (1 if p2[0] > p1[0] else -1), (p2[1] - p1[1]) and (1 if p2[1] > p1[1] else -1))
        a = (p1[0] - din[0] * r, p1[1] - din[1] * r)
        b = (p1[0] + dout[0] * r, p1[1] + dout[1] * r)
        sweep = 1 if din[0] * dout[1] - din[1] * dout[0] > 0 else 0
        out.append(f"{'M' if i == 0 else 'L'}{fmt(a[0])} {fmt(a[1])}A{fmt(r)} {fmt(r)} 0 0 {sweep} {fmt(b[0])} {fmt(b[1])}")
    return "".join(out) + "Z"


def fmt(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def mark_paths(f0: float, f1: float, arm: float, thick: float, item: float) -> list[str]:
    """Top-left bracket, bottom-right bracket, centre tile, for frame f0..f1."""
    tl = [(f0, f0), (f0 + arm, f0), (f0 + arm, f0 + thick), (f0 + thick, f0 + thick), (f0 + thick, f0 + arm), (f0, f0 + arm)]
    br = [(f1, f1), (f1 - arm, f1), (f1 - arm, f1 - thick), (f1 - thick, f1 - thick), (f1 - thick, f1 - arm), (f1, f1 - arm)]
    radii = [R_OUTER, R_INNER, R_INNER, R_INNER, R_INNER, R_INNER]
    c = (f0 + f1) / 2
    h = item / 2
    centre = [(c - h, c - h), (c + h, c - h), (c + h, c + h), (c - h, c + h)]
    return [rounded_poly(tl, radii), rounded_poly(br, radii), rounded_poly(centre, [R_ITEM] * 4)]


# The three sizes of one construction (frame, arm, thickness, kept item):
APP = dict(f0=12, f1=52, arm=20, thick=8, item=8)  # on a full tile: app icons, lockup
TAB = dict(f0=8, f1=56, arm=20, thick=8, item=8)  # on a small tile: favicon.ico / 32 px
GLYPH = dict(f0=4, f1=60, arm=24, thick=8, item=8)  # bare mark: favicon.svg, mono, in-app


def d(geom: dict) -> str:
    return "".join(mark_paths(**geom))


def svg(body: str, size: int = 64, extra: str = "") -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}"{extra}>{body}</svg>\n'


# ---------- wordmark: lowercase kist, Instrument Serif, brass square i-dot ----------
def wordmark() -> dict:
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont

    font = TTFont(str(FONT))
    cmap = font.getBestCmap()
    gs = font.getGlyphSet()
    upm = font["head"].unitsPerEm
    asc = font["hhea"].ascent
    tracking = -0.01 * upm  # -1 %
    x = 0.0
    ink = []
    # the dot of the regular i, for where the square goes and how big it is
    rp = RecordingPen()
    gs[cmap[ord("i")]].draw(rp)
    # split i into contours; the one whose lowest point is highest is the dot
    contours, cur = [], []
    for op, args in rp.value:
        cur.append((op, args))
        if op in ("closePath", "endPath"):
            contours.append(cur)
            cur = []
    def cbounds(c):
        pts = [p for _, a in c for p in a]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)
    dot = max((cbounds(c) for c in contours), key=lambda b: b[1])
    stem = min((cbounds(c) for c in contours), key=lambda b: b[1])
    dot_cx, dot_cy = (dot[0] + dot[2]) / 2, (dot[1] + dot[3]) / 2
    side = round((dot[2] - dot[0]) * 0.92)
    stem_w = stem[2] - stem[0]
    dot_rect = None
    for ch in "kist":
        name = cmap[0x131] if ch == "i" else cmap[ord(ch)]
        pen = SVGPathPen(gs, ntos=lambda v: fmt(v))
        # font units are y-up; SVG is y-down with the baseline at `asc`
        gs[name].draw(TransformPen(pen, (1, 0, 0, -1, x, asc)))
        ink.append(pen.getCommands())
        if ch == "i":
            dot_rect = (x + dot_cx - side / 2, asc - dot_cy - side / 2, side)
        x += gs[name].width + tracking
    width = x - tracking
    top = min(dot_rect[1], asc - 720)
    return dict(
        ink="".join(ink),
        dot=dot_rect,
        width=width,
        height=asc - font["hhea"].descent,
        asc=asc,
        cap=font["OS/2"].sCapHeight,
        xh=font["OS/2"].sxHeight,
        stem=stem_w,
        top=top,
    )


def wordmark_svg(w: dict, ink: str, brass: str, pad: float = 0) -> str:
    x, y, s = w["dot"]
    top = w["top"] - pad
    bottom = w["asc"] + 20 + pad  # baseline plus overshoot
    vb = f"{fmt(-pad)} {fmt(top)} {fmt(w['width'] + 2 * pad)} {fmt(bottom - top)}"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" role="img" aria-label="kist">'
        f'<path fill="{ink}" d="{w["ink"]}"/>'
        f'<rect fill="{brass}" x="{fmt(x)}" y="{fmt(y)}" width="{fmt(s)}" height="{fmt(s)}" rx="{fmt(s * 0.08)}"/></svg>\n'
    )


def lockup_svg(w: dict, ink: str, brass: str) -> str:
    """Mark + wordmark. Mark height = cap height x 1.25, centred on the cap
    height; gap = twice the i's stem width."""
    mh = w["cap"] * 1.25
    scale = mh / 56  # GLYPH frame is 56 units
    gap = w["stem"] * 2
    cap_mid = w["asc"] - w["cap"] / 2
    my = cap_mid - mh / 2
    tx = mh + gap
    x, y, s = w["dot"]
    top = min(my, w["top"]) - 40
    bottom = max(my + mh, w["asc"] + 20) + 40
    total = tx + w["width"] + 40
    g = f'<g transform="translate({fmt(-4 * scale)} {fmt(my - 4 * scale)}) scale({fmt(scale)})" fill="{brass}"><path d="{d(GLYPH)}"/></g>'
    t = f'<g transform="translate({fmt(tx)} 0)"><path fill="{ink}" d="{w["ink"]}"/><rect fill="{brass}" x="{fmt(x)}" y="{fmt(y)}" width="{fmt(s)}" height="{fmt(s)}" rx="{fmt(s * 0.08)}"/></g>'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-40 {fmt(top)} {fmt(total + 40)} {fmt(bottom - top)}" role="img" aria-label="Kist">{g}{t}</svg>\n'
    )


def build_svgs() -> dict[str, str]:
    files: dict[str, str] = {}
    glyph, app, tab = d(GLYPH), d(APP), d(TAB)
    # favicon.svg: the bare mark, deep brass on light browser chrome, bright on dark
    files["web/public/favicon.svg"] = svg(
        f"<style>path{{fill:{BRASS_DEEP}}}@media (prefers-color-scheme:dark){{path{{fill:{BRASS}}}}}</style><path d=\"{glyph}\"/>"
    )
    # single-colour marks: mono (currentColor-friendly black), dashboard -light/-dark
    files["web/public/icon-mono.svg"] = svg(f'<path fill="#000" d="{glyph}"/>')
    files["docs/brand/kist.svg"] = svg(f'<path fill="{BRASS}" d="{glyph}"/>')
    files["docs/brand/kist-light.svg"] = svg(f'<path fill="#FFFFFF" d="{glyph}"/>')
    files["docs/brand/kist-dark.svg"] = svg(f'<path fill="{INK_LIGHT}" d="{glyph}"/>')
    # app tiles
    files["docs/brand/icon-tile.svg"] = svg(f'<rect width="64" height="64" rx="14" fill="{TILE}"/><path fill="{BRASS}" d="{app}"/>')
    files["docs/brand/icon-tile-light.svg"] = svg(f'<rect width="64" height="64" rx="14" fill="{PAPER}"/><path fill="{BRASS_DEEP}" d="{app}"/>')
    files["docs/brand/icon-fullbleed.svg"] = svg(f'<rect width="64" height="64" fill="{TILE}"/><path fill="{BRASS}" d="{app}"/>')
    # maskable: full bleed, mark scaled 0.8 about the centre so its corners
    # sit 22.6 units from the centre, inside the 25.6-unit (40 %) safe circle
    files["docs/brand/icon-maskable.svg"] = svg(
        f'<rect width="64" height="64" fill="{TILE}"/><path fill="{BRASS}" transform="translate(6.4 6.4) scale(.8)" d="{app}"/>'
    )
    files["docs/brand/icon-tab.svg"] = svg(f'<rect width="64" height="64" rx="12" fill="{TILE}"/><path fill="{BRASS}" d="{tab}"/>')
    w = wordmark()
    files["docs/brand/wordmark-dark.svg"] = wordmark_svg(w, INK_DARK, BRASS, pad=30)
    files["docs/brand/wordmark-light.svg"] = wordmark_svg(w, INK_LIGHT, BRASS_DEEP, pad=30)
    files["docs/brand/lockup-dark.svg"] = lockup_svg(w, INK_DARK, BRASS)
    files["docs/brand/lockup-light.svg"] = lockup_svg(w, INK_LIGHT, BRASS_DEEP)
    x, y, s = w["dot"]
    top = w["top"]
    files["web/src/brandPaths.ts"] = (
        "// Generated by docs/brand/build_brand.py: do not edit by hand.\n"
        "// The Brass corners mark (64-unit grid) and the outlined kist wordmark.\n\n"
        f"export const MARK_PATH = {json.dumps(glyph)}\n\n"
        f"export const WORDMARK = {{\n"
        f"  viewBox: {json.dumps(f'0 {fmt(top)} {fmt(w["width"])} {fmt(w["asc"] + 20 - top)}')},\n"
        f"  ink: {json.dumps(w['ink'])},\n"
        f"  dot: {{ x: {fmt(x)}, y: {fmt(y)}, size: {fmt(s)} }},\n"
        f"}}\n"
    )
    return files


def write(files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        print("wrote", rel)


def render(svg_path: Path, png: Path, width: int) -> None:
    subprocess.run(
        ["npx", "--yes", "@resvg/resvg-js-cli", "--fit-width", str(width), str(svg_path), str(png)],
        check=True,
        capture_output=True,
    )


def raster() -> None:
    from PIL import Image

    B = BRAND
    render(B / "icon-fullbleed.svg", PUBLIC / "apple-touch-icon.png", 180)
    render(B / "icon-tile.svg", PUBLIC / "icon-192.png", 192)
    render(B / "icon-tile.svg", PUBLIC / "icon-512.png", 512)
    render(B / "icon-maskable.svg", PUBLIC / "icon-maskable-512.png", 512)
    render(B / "icon-tab.svg", PUBLIC / "favicon-32.png", 32)
    with tempfile.TemporaryDirectory() as tmp:
        frames = []
        for s in (16, 32, 48):
            out = Path(tmp) / f"f{s}.png"
            render(B / "icon-tab.svg", out, s)  # rendered at each size, not downscaled
            frames.append(Image.open(out).convert("RGBA"))
        frames[-1].save(PUBLIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)], append_images=frames[:-1])
    for p in ("apple-touch-icon.png", "icon-192.png", "icon-512.png", "icon-maskable-512.png", "favicon-32.png", "favicon.ico"):
        print("rendered", p)


if __name__ == "__main__":
    write(build_svgs())
    if "--raster" in sys.argv:
        raster()
