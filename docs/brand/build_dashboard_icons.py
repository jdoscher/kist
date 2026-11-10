"""Build the icon files that dashboard icon collections ask for.

    python docs/brand/build_dashboard_icons.py      # needs npx (resvg) for the PNGs

Writes docs/brand/dashboard-icons/:

- kist.svg        the brass mark (#E8B04B) on transparent
- kist-light.svg  white, for dark dashboards   (dashboard-icons' "-light")
- kist-dark.svg   #1C1B19, for light dashboards (dashboard-icons' "-dark")
- the same as 512 px-high PNGs, for previews and for collections that want PNG

homarr-labs/dashboard-icons wants SVG with no embedded raster, cropped to
the content (no empty margin), kebab-case names, and -light / -dark variants
for single-colour icons; it renders PNG and WebP at 512 px itself. selfh.st
icons are requested in its GitHub discussions with the same files. The marks
come from build_brand.py's construction, so they never drift from the app's.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_brand as bb  # noqa: E402

OUT = bb.BRAND / "dashboard-icons"


def cropped(fill: str) -> str:
    # the bare mark (GLYPH) fills 4..60 of its 64-unit grid: crop to exactly that
    g = bb.GLYPH
    lo, size = g["f0"], g["f1"] - g["f0"]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{lo} {lo} {size} {size}">'
        f'<path fill="{fill}" d="{bb.d(g)}"/></svg>\n'
    )


def main() -> None:
    OUT.mkdir(exist_ok=True)
    files = {"kist.svg": bb.BRASS, "kist-light.svg": "#FFFFFF", "kist-dark.svg": bb.INK_LIGHT}
    for name, fill in files.items():
        p = OUT / name
        p.write_text(cropped(fill))
        bb.render(p, p.with_suffix(".png"), 512)
        print("wrote", p.relative_to(bb.ROOT), "+ .png")


if __name__ == "__main__":
    main()
