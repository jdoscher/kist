"""Colour maths for palettes and colour search (no dependencies).

Dominant colours (items.dominant_colors, also in meta.json) are converted to
CIE Lab (D65) and kept in `item_colors(item_id, l, a, b, pct)`. A database
trigger keeps that table in step with items.dominant_colors (migration 0201
has the same maths in SQL: `kist_hex_lab`, `kist_ciede2000`), so ingest,
preview retries and rebuild-index all fill it without extra code.

Colour search ranks an item by how much of its palette lies near the
colour asked for:

    score = sum(pct * (1 - dE / max_dE))   over its colours with dE <= max_dE

where dE is CIEDE2000 and max_dE comes from the tolerance (0-100). Before
CIEDE2000 runs, a Lab bounding box (`lab_box`) narrows the candidates with
the item_colors index; the box is wide enough never to drop a real match
(tests/test_search_m2.py checks that against random colours).
"""

import math
import re

HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")

DEFAULT_TOLERANCE = 30


def parse_hex(value: str) -> str:
    """'#AbC', 'aabbcc' -> '#aabbcc'. ValueError when it isn't a hex colour."""
    m = HEX_RE.match((value or "").strip())
    if not m:
        raise ValueError(f"{value!r} isn't a hex colour like #2f4f3a")
    h = m.group(1).lower()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    h = parse_hex(value)[1:]
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _f(t: float) -> float:
    return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116


def rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """sRGB (0-255) -> CIE Lab, D65 white."""
    r, g, b = (_lin(c) for c in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883
    fx, fy, fz = _f(x), _f(y), _f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def hex_to_lab(value: str) -> tuple[float, float, float]:
    return rgb_to_lab(hex_to_rgb(value))


def ciede2000(lab1, lab2) -> float:
    """CIEDE2000 colour difference (Sharma, Wu & Dalal 2005; kL=kC=kH=1)."""
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    cbar7 = ((c1 + c2) / 2) ** 7
    g = 0.5 * (1 - math.sqrt(cbar7 / (cbar7 + 25**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if c1p else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if c2p else 0.0
    dlp = l2 - l1
    dcp = c2p - c1p
    if c1p * c2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp / 2))
    lbp = (l1 + l2) / 2
    cbp = (c1p + c2p) / 2
    if c1p * c2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hbp = (h1p + h2p + 360) / 2
    else:
        hbp = (h1p + h2p - 360) / 2
    t = (1 - 0.17 * math.cos(math.radians(hbp - 30))
         + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6))
         - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dtheta = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    cbp7 = cbp**7
    rc = 2 * math.sqrt(cbp7 / (cbp7 + 25**7))
    sl = 1 + 0.015 * (lbp - 50) ** 2 / math.sqrt(20 + (lbp - 50) ** 2)
    sc = 1 + 0.045 * cbp
    sh = 1 + 0.015 * cbp * t
    rt = -math.sin(math.radians(2 * dtheta)) * rc
    return math.sqrt(
        (dlp / sl) ** 2 + (dcp / sc) ** 2 + (dHp / sh) ** 2 + rt * (dcp / sc) * (dHp / sh)
    )


def max_delta(tolerance: float) -> float:
    """Tolerance 0-100 -> the largest CIEDE2000 difference that still
    matches: 0 is nearly exact (dE 2), the default 30 about 16 (clearly the
    same colour family), 100 is 50 (anything in the neighbourhood)."""
    tol = min(max(float(tolerance), 0.0), 100.0)
    return 2.0 + tol * 0.48


def lab_box(lab, max_de: float) -> tuple[tuple[float, float], ...]:
    """A Lab box that holds every colour within CIEDE2000 `max_de` of `lab`.

    CIEDE2000 shrinks lightness differences by S_L (at most ~1.75) and
    chroma/hue differences by S_C/S_H, which grow with the mean chroma
    (1 + 0.045 C), and the a* axis is stretched by up to 1.5 (the G factor).
    The rotation term can lower dE further by up to a factor of ~1.6 for
    blues. So the box widens with the query's chroma and keeps a margin;
    it's only a prefilter, CIEDE2000 itself decides.
    """
    light, a, b = lab
    c = math.hypot(a, b)
    dl = max_de * 1.8 * 1.6
    # mean chroma can be at most (c + c2)/2 with c2 <= c + reach; solve the
    # reach for the worst case S_C = 1 + 0.045 * (c + reach/2)
    reach = max_de * (1 + 0.045 * c) * 2.4
    reach = max_de * (1 + 0.045 * (c + reach / 2)) * 2.4
    return (light - dl, light + dl), (a - reach, a + reach), (b - reach, b + reach)


# A small set of everyday names for palette swatches (nearest by CIEDE2000).
NAMED_COLOURS: dict[str, str] = {
    "black": "#000000", "charcoal": "#36454f", "grey": "#808080", "silver": "#c0c0c0",
    "white": "#ffffff", "ivory": "#fffff0", "beige": "#d9c8a9", "tan": "#d2b48c",
    "brown": "#7b4a2a", "chocolate": "#3f2512", "maroon": "#800000", "red": "#d62828",
    "crimson": "#a4161a", "pink": "#f4a6c0", "hot pink": "#ff3ea5", "coral": "#ff7f50",
    "orange": "#f77f00", "rust": "#b7410e", "gold": "#d4a017", "mustard": "#e1ad01",
    "yellow": "#ffd60a", "cream": "#fff3c4", "olive": "#708238", "lime": "#a7d129",
    "green": "#2e9e44", "forest green": "#1f4d2b", "racing green": "#004225",
    "mint": "#a8e6cf", "teal": "#008080", "turquoise": "#30d5c8", "cyan": "#00b7eb",
    "sky blue": "#87ceeb", "royal blue": "#4169e1", "cobalt": "#0047ab", "blue": "#1030e8",
    "navy": "#14213d", "indigo": "#3f2a87",
    "purple": "#7b2cbf", "lavender": "#c8b6e2", "magenta": "#c2187d", "plum": "#6e2c5e",
}
_NAMED_LAB = {name: hex_to_lab(h) for name, h in NAMED_COLOURS.items()}


def colour_name(lab) -> str:
    return min(_NAMED_LAB, key=lambda n: ciede2000(lab, _NAMED_LAB[n]))


def palette(dominant_colors: list[dict] | None) -> list[dict]:
    """[{hex, pct, name}] from items.dominant_colors (largest share first)."""
    out = []
    for entry in dominant_colors or []:
        try:
            h = parse_hex(str(entry.get("hex", "")))
        except ValueError:
            continue
        pct = entry.get("pct")
        out.append({
            "hex": h,
            "pct": float(pct) if isinstance(pct, (int, float)) else 0.0,
            "name": colour_name(hex_to_lab(h)),
        })
    out.sort(key=lambda e: -e["pct"])
    return out
