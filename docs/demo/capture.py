"""Capture Kist's README / docs screenshots from a running instance.

Read-only: it only opens pages and takes pictures; it never adds, tags or
moves anything. Run from the repository root:

    KIST_URL=https://kist.example.com KIST_TOKEN=... \
      uv run --no-project --with playwright --with pillow python docs/demo/capture.py [shot ...]

(first time: `uv run --no-project --with playwright python -m playwright
install chromium`). Each shot is a URL path plus a theme and a viewport;
pictures land in docs/assets/screenshots/<name>.webp (.png without Pillow). With
KIST_SHOT_ITEM=<item id> it adds the phone item sheet (sheet-dark, sheet-light,
sheet-gallery); the published ones show Hokusai's Great Wave. Point it at a demo
library seeded with docs/demo/seed.py, not a personal one: whatever is in
the grid ends up in the README.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "assets" / "screenshots"
DESKTOP = dict(width=1440, height=900)
PHONE = dict(width=390, height=844)

# The demo library's tags (docs/demo/seed.py). SAFE limits the grid to them
# so a shot never shows anything else that happens to be in the library.
SAFE = os.environ.get("KIST_SHOT_FILTER", "")


def _q(path: str) -> str:
    if not SAFE:
        return path
    return path + ("&" if "?" in path else "?") + SAFE


# name: (path, theme, viewport, wait-for selector or None, extra ms to settle)
SHOTS: dict[str, tuple[str, str, dict, str | None, int]] = {
    "grid-dark": ("/all", "dark", DESKTOP, None, 2500),
    "grid-light": ("/all", "light", DESKTOP, None, 2500),
    "grid-gallery": ("/all", "gallery", DESKTOP, None, 2500),
    "phone-dark": ("/all", "dark", PHONE, None, 2500),
    "phone-light": ("/all", "light", PHONE, None, 2500),
    "phone-gallery": ("/all", "gallery", PHONE, None, 2500),
    # search by colour: a deep print blue, the default tolerance
    "colour-dark": ("/all?color=%233a6f8f", "dark", DESKTOP, None, 3000),
}


def item_shot(name: str, item: str, theme: str, vp: dict) -> None:
    SHOTS[name] = (f"/all?item={item}", theme, vp, None, 3500)


def webp(png: Path) -> Path:
    """WebP at q86 is ~1/7 of the PNG and looks the same in a README."""
    try:
        from PIL import Image
    except ImportError:
        return png
    out = png.with_suffix(".webp")
    Image.open(png).convert("RGB").save(out, "WEBP", quality=86, method=6)
    png.unlink()
    return out


def main(argv: list[str]) -> None:
    url = os.environ["KIST_URL"].rstrip("/")
    token = os.environ["KIST_TOKEN"]
    # KIST_SHOT_ITEM=<item id>: the phone item sheet for that item in each theme
    if sheet_item := os.environ.get("KIST_SHOT_ITEM"):
        for theme in ("dark", "light", "gallery"):
            item_shot(f"sheet-{theme}", sheet_item, theme, PHONE)
    for spec in os.environ.get("KIST_SHOT_ITEMS", "").split():
        # name=item_id:theme:desktop|phone
        name, rest = spec.split("=", 1)
        item, theme, vp = rest.split(":")
        item_shot(name, item, theme, DESKTOP if vp == "desktop" else PHONE)
    names = argv or list(SHOTS)
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name in names:
            path, theme, vp, sel, settle = SHOTS[name]
            phone = vp is PHONE
            ctx = browser.new_context(
                viewport=vp,
                device_scale_factor=2 if phone else 1,
                is_mobile=phone,
                has_touch=phone,
                color_scheme="light" if theme == "light" else "dark",
            )
            # sign in and pick the theme before the first paint
            ctx.add_init_script(
                f"try{{localStorage.setItem('kist_token',{token!r});"
                f"localStorage.setItem('kist.theme',{theme!r});"
                # hide the first-run checklist: it's not what the picture is about
                "localStorage.setItem('kist.setup','{\"dismissed\":true}')}catch(e){}"
            )
            page = ctx.new_page()
            page.goto(url + _q(path), wait_until="networkidle")
            if sel:
                page.wait_for_selector(sel)
            page.wait_for_timeout(settle)
            png = OUT / f"{name}.png"
            page.screenshot(path=str(png))
            out = webp(png)
            print("captured", out.relative_to(ROOT))
            ctx.close()
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1:])
