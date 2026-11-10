"""SVG (Milestone 5): sanitizing, exact colours, rendering.

sanitize() returns an SVG that can't run script or load anything:
- a DOCTYPE or ENTITY declaration is refused outright (no entity expansion,
  no external DTDs), and the XML is parsed with expat, which fetches nothing;
- <script>, <foreignObject>, <iframe>, <embed>, <object>, <audio>, <video>,
  <handler>, <listener> are removed with everything inside them;
- every on* event attribute is removed;
- href / xlink:href may only point inside the document (#id) or be an
  inline raster image (data:image/png|jpeg|gif|webp);
- url(...) in attributes and styles may only be url(#id); @import and
  external url() in <style> are removed;
- <animate>/<set> that target href or event attributes are removed;
- elements and attributes from other XML namespaces (editor metadata such
  as Inkscape's or Sketch's) are dropped.

The result is what GET /api/items/{id}/svg returns for "Copy SVG", and what
thumbnails are rendered from (resvg-py or cairosvg when installed,
otherwise libvips' librsvg).
"""

import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"

MAX_SVG_BYTES = 20 * 1024 * 1024
MAX_ELEMENTS = 200_000

DROP_TAGS = {"script", "foreignObject", "iframe", "embed", "object", "audio", "video",
             "handler", "listener", "canvas"}
HREF_OK = re.compile(
    r"^\s*(#[\w.:-]+|data:image/(png|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=\s]*)\s*$",
    re.IGNORECASE,
)
URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
IMPORT_RE = re.compile(r"@import[^;]*;?", re.IGNORECASE)

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


class SvgError(ValueError):
    pass


def _local(tag: str) -> tuple[str | None, str]:
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return ns, name
    return None, tag


def _safe_urls(value: str) -> str:
    """Replace any url(...) that isn't url(#id) with none."""
    def fix(m: re.Match) -> str:
        target = m.group(2).strip()
        return m.group(0) if target.startswith("#") else "none"

    return URL_RE.sub(fix, value)


def _clean_style_text(css: str) -> str:
    css = IMPORT_RE.sub("", css)
    css = re.sub(r"expression\s*\(", "none(", css, flags=re.IGNORECASE)
    return _safe_urls(css)


def sanitize(data: bytes) -> str:
    if len(data) > MAX_SVG_BYTES:
        raise SvgError("SVG is too large to sanitize")
    head = data[:4096].lower()
    if b"<!doctype" in data.lower() or b"<!entity" in head or b"<!entity" in data.lower():
        raise SvgError("SVGs with a DOCTYPE or entity declarations are refused")
    try:
        parser = ET.XMLParser()
        root = ET.fromstring(data, parser=parser)
    except ET.ParseError as exc:
        raise SvgError(f"not a well-formed SVG: {exc}") from None
    ns, name = _local(root.tag)
    if name != "svg" or ns not in (SVG_NS, None):
        raise SvgError("the root element isn't <svg>")
    if ns is None:  # no xmlns: renderers need it
        for el in root.iter():
            if isinstance(el.tag, str) and not el.tag.startswith("{"):
                el.tag = f"{{{SVG_NS}}}{el.tag}"
    count = 0

    def clean(el: ET.Element) -> None:
        nonlocal count
        for child in list(el):
            count += 1
            if count > MAX_ELEMENTS:
                raise SvgError("SVG has too many elements")
            if not isinstance(child.tag, str):  # comments / processing instructions
                el.remove(child)
                continue
            cns, cname = _local(child.tag)
            if cns not in (SVG_NS, None) or cname in DROP_TAGS:
                el.remove(child)
                continue
            if cname in ("animate", "set", "animateTransform", "animateMotion"):
                target = (child.get("attributeName") or "").lower()
                if target.startswith("on") or "href" in target:
                    el.remove(child)
                    continue
            if cname == "style" and child.text:
                child.text = _clean_style_text(child.text)
            clean_attrs(child)
            clean(child)

    def clean_attrs(el: ET.Element) -> None:
        for key in list(el.attrib):
            ans, aname = _local(key)
            value = el.attrib[key]
            if ans not in (None, XLINK_NS, XML_NS):
                del el.attrib[key]
                continue
            if aname.lower().startswith("on"):
                del el.attrib[key]
                continue
            if aname == "href":
                if not HREF_OK.match(value):
                    del el.attrib[key]
                continue
            if aname == "style":
                el.attrib[key] = _clean_style_text(value)
            elif "url(" in value.lower():
                el.attrib[key] = _safe_urls(value)

    clean_attrs(root)
    clean(root)
    text = ET.tostring(root, encoding="unicode")
    return text


# ---------- colours ----------

NAMED = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000",
    "blue": "#0000ff", "yellow": "#ffff00", "orange": "#ffa500", "purple": "#800080",
    "gray": "#808080", "grey": "#808080", "silver": "#c0c0c0", "navy": "#000080",
    "teal": "#008080", "maroon": "#800000", "olive": "#808000", "lime": "#00ff00",
    "aqua": "#00ffff", "cyan": "#00ffff", "fuchsia": "#ff00ff", "magenta": "#ff00ff",
    "pink": "#ffc0cb", "brown": "#a52a2a", "gold": "#ffd700",
}
COLOR_PROPS = ("fill", "stroke", "stop-color", "flood-color", "lighting-color", "color")


def normalize_color(value: str) -> str | None:
    v = value.strip().lower()
    if not v or v in ("none", "transparent", "currentcolor", "inherit") or v.startswith("url("):
        return None
    if v.startswith("#"):
        h = v[1:]
        if re.fullmatch(r"[0-9a-f]{3,4}", h):
            return "#" + "".join(c * 2 for c in h[:3])
        if re.fullmatch(r"[0-9a-f]{6}([0-9a-f]{2})?", h):
            return "#" + h[:6]
        return None
    m = re.fullmatch(r"rgba?\(\s*([\d.]+%?)\s*[, ]\s*([\d.]+%?)\s*[, ]\s*([\d.]+%?).*\)", v)
    if m:
        out = []
        for part in m.groups():
            n = float(part[:-1]) * 2.55 if part.endswith("%") else float(part)
            out.append(max(0, min(255, round(n))))
        return "#{:02x}{:02x}{:02x}".format(*out)
    return NAMED.get(v)


def colors(svg_text: str) -> list[dict]:
    """The exact fill/stroke colours used, most frequent first."""
    root = ET.fromstring(svg_text)
    counts: Counter[str] = Counter()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for prop in COLOR_PROPS:
            v = el.get(prop)
            if v and (c := normalize_color(v)):
                counts[c] += 1
        style = el.get("style") or ""
        if _local(el.tag)[1] == "style" and el.text:
            style += ";" + el.text
        for decl in re.split(r"[;{}]", style):
            prop, _, val = decl.partition(":")
            if prop.strip().lower() in COLOR_PROPS and (c := normalize_color(val)):
                counts[c] += 1
    return [{"hex": c, "count": n} for c, n in counts.most_common(64)]


def view_box(svg_text: str) -> str | None:
    root = ET.fromstring(svg_text)
    return root.get("viewBox")


# ---------- rendering ----------

RENDER_MAX = 2048


def _length(value: str | None) -> float | None:
    m = re.match(r"^\s*([\d.]+)\s*(px)?\s*$", value or "")
    try:
        return float(m.group(1)) if m else None
    except ValueError:
        return None


def aspect(svg_text: str) -> float:
    """height / width from the viewBox, else width/height (1 if unknown)."""
    root = ET.fromstring(svg_text)
    vb = (root.get("viewBox") or "").replace(",", " ").split()
    try:
        if len(vb) == 4 and float(vb[2]) > 0 and float(vb[3]) > 0:
            return float(vb[3]) / float(vb[2])
    except ValueError:
        pass
    w, h = _length(root.get("width")), _length(root.get("height"))
    if w and h:
        return h / w
    return 1.0


def render_png(svg_text: str, out: Path) -> str:
    """Render the sanitized SVG to a PNG (long side RENDER_MAX at most).
    Returns the renderer used."""
    ratio = min(max(aspect(svg_text), 1 / 64), 64)
    size = {"width": RENDER_MAX} if ratio <= 1 else {"height": RENDER_MAX}
    try:
        import resvg_py  # type: ignore[import-not-found]
    except ImportError:
        resvg_py = None
    if resvg_py is not None:
        try:
            data = resvg_py.svg_to_bytes(svg_string=svg_text, **size)
            png = bytes(data)
            _fit(png, out)
            return "resvg"
        except Exception:
            pass
    try:
        import cairosvg  # type: ignore[import-not-found]
    except (ImportError, OSError):
        cairosvg = None
    if cairosvg is not None:
        try:
            png = cairosvg.svg2png(bytestring=svg_text.encode(), unsafe=False,
                                   **{f"output_{k}": v for k, v in size.items()})
            _fit(png, out)
            return "cairosvg"
        except Exception:
            pass
    import pyvips

    img = pyvips.Image.thumbnail_buffer(svg_text.encode(), RENDER_MAX, height=RENDER_MAX)
    img.pngsave(str(out))
    return "librsvg"


def _fit(png: bytes, out: Path) -> None:
    import io

    from PIL import Image

    with Image.open(io.BytesIO(png)) as im:
        im.thumbnail((RENDER_MAX, RENDER_MAX))
        im.save(out, "PNG")
