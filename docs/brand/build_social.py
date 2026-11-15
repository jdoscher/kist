"""Build Kist's social preview card (GitHub "Social preview", og:image).

    python docs/brand/build_social.py            # docs/brand/social-preview.png
    python docs/brand/build_social.py --fetch    # first re-download the collage images

1280 x 640, solid #131312 (GitHub: PNG under 1 MB, 1280 x 640; a solid
background because some apps show transparency badly). Left: the dark
lockup, the tagline and the proof line; right: a justified grid of
public-domain works from the demo library (docs/demo/library.json: The Met
Open Access, CC0),
cached small in docs/brand/social-src/ so a rebuild needs no network. The
words and the mark stay inside the centre 1100 x 540, which every platform
shows uncropped.

Needs what build_brand.py needs (fontTools; Instrument Serif from
web/node_modules/@fontsource, or --font <instrument-serif-latin-400-normal.woff>;
npx for resvg) plus Pillow.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_brand as bb  # noqa: E402

W, H = 1280, 640
SAFE_X, SAFE_Y = (W - 1100) // 2, (H - 540) // 2  # 90, 50
SRC = bb.BRAND / "social-src"
OUT = bb.BRAND / "social-preview.png"
DIM = "#9C9A93"  # text-dim on dark (docs/brand.md)
TAGLINE = "Keep what catches your eye."
PROOF = "Self-hosted. Plain files. Agent-ready."
UA = "kist-brand-build/2.0 (https://github.com/jdoscher/kist)"

# Collage: demo library names (library.json "name") in reading order, three rows.
COLLAGE = [
    ["Hokusai, The Great Wave", "Van Gogh, Wheat Field with Cypresses"],
    ["Vermeer, Young Woman with a Water Pitcher", "Hokusai, Red Fuji", "Van Gogh, Irises"],
    ["Bruegel, The Harvesters", "Hiroshige, Sudden Shower over Shin-Ōhashi"],
]


def lib_files() -> dict[str, dict]:
    lib = json.loads((bb.ROOT / "docs/demo/library.json").read_text())
    return {i["name"]: i for i in lib["items"]}


def resolve(name: str, files: dict[str, dict]) -> str:
    if name not in files:
        raise SystemExit(f"{name!r} is not in docs/demo/library.json")
    return name


def slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.rsplit(".", 1)[0].lower())[:48].strip("-") + ".jpg"


def fetch() -> None:
    from PIL import Image

    files = lib_files()
    SRC.mkdir(exist_ok=True)
    credits = []
    for row in COLLAGE:
        for name in row:
            f = resolve(name, files)
            # the Met's web-large copy (about 1,000 px) is plenty for a 640 px tile
            url = files[f]["image"].replace("/original/", "/web-large/")
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                data = r.read()
            with tempfile.NamedTemporaryFile(suffix=".img") as t:
                t.write(data)
                t.flush()
                im = Image.open(t.name).convert("RGB")
                im.thumbnail((640, 640))
                im.save(SRC / slug(f), quality=80, optimize=True, progressive=True)
            i = files[f]
            credits.append(f"- {slug(f)}: {i['creator']}, {i['title']}, {i['date']}. {i['credit']} ({i['page']})")
            print("fetched", slug(f))
    (SRC / "CREDITS.md").write_text(
        "# Social card images\n\nThe Metropolitan Museum of Art, Open Access (CC0); see docs/demo/library.json.\n\n" + "\n".join(credits) + "\n"
    )


def text_path(s: str, font_path: Path, size: float, x: float, baseline: float, tracking: float = -0.01) -> tuple[str, float]:
    """Outline `s` in the font at `size` px, left edge x, on `baseline`.
    Returns (svg path d, advance width in px)."""
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont

    font = TTFont(str(font_path))
    cmap, gs, upm = font.getBestCmap(), font.getGlyphSet(), font["head"].unitsPerEm
    k = size / upm
    cx = 0.0
    out = []
    for ch in s:
        g = cmap.get(ord(ch))
        if g is None:
            continue
        pen = SVGPathPen(gs, ntos=bb.fmt)
        gs[g].draw(TransformPen(pen, (k, 0, 0, -k, x + cx, baseline)))
        out.append(pen.getCommands())
        cx += gs[g].width * k + tracking * size
    return "".join(out), cx - tracking * size


def card_svg(font: Path) -> tuple[str, float]:
    w = bb.wordmark()
    lock = bb.lockup_svg(w, bb.INK_DARK, bb.INK_DARK)
    # the lockup's own viewBox, placed by a nested <svg>
    vb = lock.split('viewBox="')[1].split('"')[0]
    vx, vy, vw, vh = (float(v) for v in vb.split())
    inner = lock.split(">", 1)[1].rsplit("</svg>", 1)[0]
    lock_h = 128
    lock_w = lock_h * vw / vh
    x0 = SAFE_X + 4
    ly = 150
    italic = Path(str(font).replace("-normal", "-italic"))
    tag_d, _ = text_path(TAGLINE, font, 54, x0 + 10, ly + lock_h + 92)
    proof_d, _ = text_path(PROOF, italic if italic.exists() else font, 34, x0 + 12, ly + lock_h + 150, tracking=0)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
        f'<svg x="{x0 - 10}" y="{ly}" width="{bb.fmt(lock_w)}" height="{lock_h}" viewBox="{vb}">{inner}</svg>'
        f'<path fill="{bb.INK_DARK}" d="{tag_d}"/>'
        f'<path fill="{DIM}" d="{proof_d}"/>'
        "</svg>\n"
    ), lock_w


def collage(card, x0: int) -> None:
    """Justified rows from x0 to the right edge, bleeding off it, top and bottom."""
    from PIL import Image, ImageDraw

    files = lib_files()
    gap, rows = 10, len(COLLAGE)
    row_h = (H + 2 * 36 - gap * (rows - 1)) / rows  # rows overhang top and bottom by 36
    width = W + 60 - x0  # and the right edge by 60
    y = -36.0
    for r, row in enumerate(COLLAGE):
        ims = [Image.open(SRC / slug(resolve(n, files))).convert("RGB") for n in row]
        ratios = [im.width / im.height for im in ims]
        # stretch every row to the full width; crop each image to its share
        total = sum(ratios)
        avail = width - gap * (len(ims) - 1)
        x = float(x0)
        # stagger rows a little, like a hand-placed board
        x -= (r % 2) * 28
        for im, ratio in zip(ims, ratios, strict=True):
            tw = round(avail * ratio / total)
            th = round(row_h)
            # cover-crop to tw x th
            s = max(tw / im.width, th / im.height)
            rs = im.resize((max(tw, round(im.width * s)), max(th, round(im.height * s))), Image.Resampling.LANCZOS)
            left = (rs.width - tw) // 2
            top = (rs.height - th) // 2
            tile = rs.crop((left, top, left + tw, top + th))
            mask = Image.new("L", (tw, th), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, tw - 1, th - 1), radius=8, fill=255)
            card.paste(tile, (round(x), round(y)), mask)
            x += tw + gap
        y += row_h + gap
    # fade the collage's left edge into the background, so the words own the left
    fade_w = 120
    bg = Image.new("RGB", (fade_w, H), bb.BG_DARK)
    grad = Image.linear_gradient("L").rotate(-90).resize((fade_w, H))  # opaque at the left
    card.paste(Image.new("RGB", (x0 - 40, H), bb.BG_DARK), (0, 0))
    card.paste(bg, (x0 - 40, 0), grad)


def main() -> None:
    from PIL import Image

    args = sys.argv[1:]
    if "--font" in args:
        bb.FONT = Path(args[args.index("--font") + 1]).resolve()
    if not bb.FONT.exists():
        raise SystemExit(f"no font at {bb.FONT}: run `npm ci` in web/, or pass --font")
    if "--fetch" in args or not SRC.exists():
        fetch()
    svg, lock_w = card_svg(bb.FONT)
    card = Image.new("RGB", (W, H), bb.BG_DARK)
    collage(card, x0=700)
    with tempfile.TemporaryDirectory() as tmp:
        sp, pp = Path(tmp) / "card.svg", Path(tmp) / "card.png"
        sp.write_text(svg)
        subprocess.run(["npx", "--yes", "@resvg/resvg-js-cli", "--fit-width", str(W), str(sp), str(pp)], check=True, capture_output=True)
        words = Image.open(pp).convert("RGBA")
    card.paste(words, (0, 0), words)
    card.save(OUT, optimize=True)
    print("wrote", OUT.relative_to(bb.ROOT), f"{OUT.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
