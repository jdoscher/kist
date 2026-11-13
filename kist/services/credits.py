"""F4: credits and rights, the AI-made flag, and the print check.

Everything here builds on the item's provenance (services/provenance.py),
which stays the one durable record (meta.json + the items.provenance
column):

    provenance.rights_status   ref_only | comp | licensed | cleared (unset = not said)
    provenance.credit_exempt   true: this item needs no credit (own work ...)
    provenance.ai_generator    the generator a file's metadata names (Midjourney ...)

Credits
    credit_line(prov)      the line printed on exports, share pages and slides
    present_caption(item)  {title, credit, rights_status} for Present (F2)
    filters(spec)          search.build_filters' `credits=` kwarg:
                           rights_status, needs_credit, print_min, print_max
    clearance(ids)         the items a share/export would send out that
                           aren't licensed or cleared (a warning, never a block)

Auto-fill (never re-requests a page)
    file_fields(path)      creator/credit/copyright from EXIF and IPTC-IIM,
                           and AI generator names from EXIF Software, PNG
                           text chunks and XMP CreatorTool; ingest folds
                           these into provenance (provenance.from_file)
    page_fields(item_id)   author, site, licence link from the meta tags of
                           the item's STORED page snapshot
    autofill(...)          fills only empty fields; one edit event per item
                           (undoable), modified_at kept for background runs

Print check
    print_info(item)       print size at 300 and 150 dpi, papers it fits,
                           the file's own dpi and its ICC profile name;
                           read from the original's header on demand
                           (derived, so nothing to rebuild)

Settings live in library/.kist/credits.json ({clearance_warning: true}).
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from ..config import get_settings
from . import validation

log = logging.getLogger("kist.credits")

RIGHTS_STATUSES = ("ref_only", "comp", "licensed", "cleared")
RIGHTS_TEXT = {"ref_only": "Ref only", "comp": "Comp", "licensed": "Licensed",
               "cleared": "Cleared"}
# what may leave the building without a warning
CLEARED = frozenset({"licensed", "cleared"})

SETTINGS_FILE = "credits.json"
DEFAULT_SETTINGS = {"clearance_warning": True}

CREDIT_LINE_MAX = 160

# ---------- the credit line ----------


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def credit_line(prov: dict | None) -> str | None:
    """'Creator / Credit' (one of them when the other is missing or the
    same), or None. The licence is not part of the line (it can be long);
    outputs that have room print it beside."""
    prov = prov or {}
    creator = str(prov.get("creator") or "").strip()
    credit = str(prov.get("credit") or "").strip()
    if creator and credit and creator.lower() not in credit.lower():
        line = f"{creator} / {credit}"
    else:
        line = credit or creator
    return _clip(line, CREDIT_LINE_MAX) if line else None


def source_host(prov: dict | None) -> str | None:
    """The site name, else the page's host: 'via ...' when there's no credit."""
    prov = prov or {}
    if prov.get("site"):
        return _clip(prov["site"], 80)
    url = prov.get("page_url") or ""
    m = re.match(r"^https?://([^/:?#]+)", str(url))
    if not m:
        return None
    host = m.group(1).lower()
    return host[4:] if host.startswith("www.") else host


def item_credit(item) -> dict:
    """{line, source, license, rights_status} for any output."""
    from . import provenance as provenance_svc

    prov = provenance_svc.out(item)
    return {
        "line": credit_line(prov),
        "source": source_host(prov),
        "license": _clip(prov["license"], 120) if prov.get("license") else None,
        "rights_status": prov.get("rights_status"),
    }


def output_text(item, *, with_license: bool = True, with_source: bool = False) -> str | None:
    """One printable credit for outputs: 'Jane Doe / Magnum · CC BY 4.0'.
    with_source adds 'via example.com' when no credit is known; only for
    exports that carry the page URL anyway (BagIt): share pages, boards and
    slides never reveal where an item was saved from."""
    c = item_credit(item)
    parts = [c["line"]] if c["line"] else (
        [f"via {c['source']}"] if c["source"] and with_source else [])
    if with_license and c["license"] and parts:
        parts.append(c["license"])
    return " · ".join(parts) if parts else None


def present_caption(item) -> dict:
    """What Present (F2) shows under a slide: a title (page title or the
    caption; never the filename) and the credit. F2 reads this or the
    `credit_line` field every item carries in REST."""
    from . import provenance as provenance_svc

    prov = provenance_svc.out(item)
    title = prov.get("page_title") or getattr(item, "caption", None)
    return {"title": _clip(title, 200) if title else None, "credit": output_text(item),
            "rights_status": prov.get("rights_status")}


# ---------- settings (library/.kist/credits.json) ----------


def _settings_path() -> Path:
    return get_settings().state_dir / SETTINGS_FILE


def read_settings() -> dict:
    p = _settings_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_SETTINGS)
    except (OSError, ValueError):
        log.warning("%s is unreadable; using the defaults", p)
        return dict(DEFAULT_SETTINGS)
    out = dict(DEFAULT_SETTINGS)
    if isinstance(data, dict) and isinstance(data.get("clearance_warning"), bool):
        out["clearance_warning"] = data["clearance_warning"]
    return out


def write_settings(patch: dict) -> dict:
    from . import meta as meta_svc

    cur = read_settings()
    if "clearance_warning" in patch:
        if not isinstance(patch["clearance_warning"], bool):
            raise validation.ValidationError("clearance_warning must be true or false")
        cur["clearance_warning"] = patch["clearance_warning"]
    meta_svc.atomic_write_text(_settings_path(),
                               json.dumps({"version": 1, **cur}, indent=2))
    return cur


def check_file() -> list[str]:
    """verify: credits.json, if any, is readable JSON."""
    p = _settings_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
    except (OSError, ValueError):
        return [f"{SETTINGS_FILE} is unreadable (Settings → Credits rewrites it)"]
    return []


# ---------- filters (search.build_filters credits=) ----------

FILTER_KEYS = ("rights_status", "needs_credit", "print_min", "print_max")

DPI = 300
# paper sizes in inches (short, long)
PAPERS = {
    "a5": (5.83, 8.27), "a4": (8.27, 11.69), "a3": (11.69, 16.54), "a2": (16.54, 23.39),
    "a1": (23.39, 33.11), "letter": (8.5, 11.0), "tabloid": (11.0, 17.0),
}
PAPER_NAMES = {"a5": "A5", "a4": "A4", "a3": "A3", "a2": "A2", "a1": "A1", "letter": "Letter",
               "tabloid": "Tabloid"}
# F1's Low-res collection: items whose long edge prints under 5 in at 300
# dpi (1,500 px; NYC's cutoff). low_res_condition() is the SQL.
LOW_RES_PRINT_MAX = "5in"

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(in|inch|inches|\"|cm|mm)?\s*$", re.I)


def parse_print_size(value: str) -> tuple[float, float | None]:
    """'8', '8in', '20cm', '200mm' -> (long edge inches, None);
    'A4', 'letter' -> (long, short) of that paper. Raises ValidationError."""
    v = str(value or "").strip().lower()
    if v in PAPERS:
        short, long = PAPERS[v]
        return long, short
    m = _SIZE_RE.match(v)
    if not m:
        raise validation.ValidationError(
            "print size is a number of inches ('8', '8in'), centimetres ('20cm') "
            f"or a paper ({', '.join(PAPER_NAMES.values())})")
    n = float(m.group(1))
    unit = (m.group(2) or "in").lower()
    inches = n / 2.54 if unit == "cm" else n / 25.4 if unit == "mm" else n
    if not 0 < inches <= 1000:
        raise validation.ValidationError("print size is between 0 and 1000 inches")
    return inches, None


def print_condition(value: str, *, dpi: int = DPI):
    """SQL: the item prints at least this size at `dpi` (either way round)."""
    from ..models import Item

    long, short = parse_print_size(value)
    big = sa.func.greatest(Item.width, Item.height)
    small = sa.func.least(Item.width, Item.height)
    cond = big >= round(long * dpi)
    if short is not None:
        cond = sa.and_(cond, small >= round(short * dpi))
    return sa.and_(Item.width.isnot(None), Item.height.isnot(None), cond)


def too_small_condition(value: str, *, dpi: int = DPI):
    """SQL: has pixel dimensions and does NOT print this size at `dpi`."""
    from ..models import Item

    return sa.and_(Item.width.isnot(None), Item.height.isnot(None),
                   sa.not_(print_condition(value, dpi=dpi)))


def low_res_condition(threshold: str = LOW_RES_PRINT_MAX):
    """For F1's Low-res collection: too small to print `threshold` at 300 dpi."""
    return too_small_condition(threshold)


def needs_credit_condition():
    """No credit and no creator, and not marked as needing none."""
    from ..models import Item

    p = Item.provenance
    return sa.and_(
        sa.func.coalesce(p["credit"].astext, "") == "",
        sa.func.coalesce(p["creator"].astext, "") == "",
        sa.func.coalesce(p["credit_exempt"].astext, "false") != "true",
    )


def _truthy(v) -> bool | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    raise validation.ValidationError("needs_credit must be true or false")


def filters(spec: dict | None) -> list:
    """SQL conditions for
        rights_status = ref_only|comp|licensed|cleared|none|uncleared (or a
                        comma list; 'none' = not said, 'uncleared' = anything
                        but licensed/cleared)
        needs_credit  = true|false
        print_min     = a size ('8in', '20cm', 'A4'): prints at least that at 300 dpi
        print_max     = a size: too small to print that at 300 dpi"""
    from ..models import Item

    spec = {k: v for k, v in (spec or {}).items() if v is not None and v != ""}
    out: list = []
    rs = spec.get("rights_status")
    if rs:
        values = [v.strip().lower() for v in str(rs).split(",") if v.strip()]
        col = Item.provenance["rights_status"].astext
        conds = []
        for v in values:
            if v == "none":
                conds.append(col.is_(None))
            elif v == "uncleared":
                conds.append(sa.or_(col.is_(None), col.notin_(sorted(CLEARED))))
            elif v in RIGHTS_STATUSES:
                conds.append(col == v)
            else:
                raise validation.ValidationError(
                    f"rights_status must be one of {', '.join(RIGHTS_STATUSES)}, "
                    "none or uncleared")
        if conds:
            out.append(sa.or_(*conds))
    need = _truthy(spec.get("needs_credit"))
    if need is not None:
        cond = needs_credit_condition()
        out.append(cond if need else sa.not_(cond))
    if spec.get("print_min"):
        out.append(print_condition(str(spec["print_min"])))
    if spec.get("print_max"):
        out.append(too_small_condition(str(spec["print_max"])))
    return out


def from_params(source: dict) -> dict | None:
    """The credits= kwarg from a flat dict (a smart folder's query, MCP)."""
    spec = {k: source.get(k) for k in FILTER_KEYS if source.get(k) not in (None, "")}
    return spec or None


# ---------- clearance ----------


def clearance(session, item_ids: list[str], *, limit: int = 100) -> dict:
    """The items in `item_ids` that aren't licensed or cleared, for the
    warning before a share or an export. Never blocks anything."""
    from ..models import Item
    from . import items as items_svc

    ids = list(dict.fromkeys(item_ids or []))
    rows: list[dict] = []
    n_uncleared = 0
    by_status: dict[str, int] = {}
    for start in range(0, len(ids), 1000):
        chunk = ids[start:start + 1000]
        for item in session.execute(sa.select(Item).where(Item.id.in_(chunk))).scalars():
            if item.deleted_at is not None or not items_svc.visible(item):
                continue
            status = (item.provenance or {}).get("rights_status")
            if status in CLEARED:
                continue
            n_uncleared += 1
            by_status[status or "none"] = by_status.get(status or "none", 0) + 1
            if len(rows) < limit:
                rows.append({"id": item.id, "filename": item.filename,
                             "rights_status": status, "credit_line": credit_line(item.provenance)})
    order = {i: n for n, i in enumerate(ids)}
    rows.sort(key=lambda r: order.get(r["id"], 0))
    return {"enabled": read_settings()["clearance_warning"], "total": len(ids),
            "uncleared": n_uncleared, "by_status": by_status, "items": rows}


def clearance_for_target(session, target: str, target_id: str | None,
                         item_ids: list[str] | None) -> dict:
    from . import selection

    if target not in ("folder", "smart", "board", "items", "all"):
        raise validation.ValidationError("target is folder, smart, board, items or all")
    return clearance(session, selection.ids(session, target, target_id, item_ids))


# ---------- reading files: EXIF, IPTC-IIM, PNG text, XMP CreatorTool ----------

# generator names that only an AI image tool writes into metadata
GENERATORS = (
    ("midjourney", "Midjourney"), ("dall-e", "DALL·E"), ("dall·e", "DALL·E"), ("dalle", "DALL·E"),
    ("openai", "OpenAI"), ("stable diffusion", "Stable Diffusion"), ("stablediffusion",
                                                                     "Stable Diffusion"),
    ("sdxl", "Stable Diffusion"), ("comfyui", "ComfyUI"), ("automatic1111", "Stable Diffusion"),
    ("novelai", "NovelAI"), ("firefly", "Adobe Firefly"), ("imagen", "Google Imagen"),
    ("leonardo.ai", "Leonardo.Ai"), ("ideogram", "Ideogram"), ("invokeai", "InvokeAI"),
    ("fooocus", "Fooocus"), ("flux.1", "FLUX"), ("black forest labs", "FLUX"),
    ("dreamstudio", "Stable Diffusion"), ("nightcafe", "NightCafe"), ("craiyon", "Craiyon"),
    ("bing image creator", "Bing Image Creator"), ("gemini", "Google Gemini"),
)
# PNG text keys that generators write (A1111 'parameters', ComfyUI
# 'prompt'/'workflow', InvokeAI 'invokeai_metadata', NovelAI 'Software')
PNG_GENERATOR_KEYS = {
    "parameters": "Stable Diffusion", "prompt": "ComfyUI", "workflow": "ComfyUI",
    "invokeai_metadata": "InvokeAI", "sd-metadata": "Stable Diffusion", "dream": "Stable Diffusion",
}
RASTER_FORMATS = {"JPEG", "PNG", "TIFF", "WEBP", "GIF", "BMP", "MPO", "PSD", "JPEG2000"}
SKIP_EXTS = {"heic", "heif", "avif"}  # never via Pillow here (two libheif copies)


def generator_from_text(text: str | None) -> str | None:
    t = (text or "").lower()
    if not t:
        return None
    for needle, name in GENERATORS:
        if needle in t:
            return name
    return None


def _txt(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        for enc in ("utf-8", "latin-1"):
            try:
                value = value.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    if isinstance(value, (list, tuple)):
        value = ", ".join(t for t in (_txt(v) for v in value) if t)
    text = " ".join(str(value).replace("\x00", " ").split())
    return text[:2000] or None


def _iso_bmff(path: Path) -> bool:
    """HEIF/AVIF/MP4 (an ISO-BMFF 'ftyp' box): never opened with Pillow here,
    whatever the name says (two libheif copies in one process crash)."""
    try:
        with path.open("rb") as f:
            return f.read(12)[4:8] == b"ftyp"
    except OSError:
        return True


def _open_image(path: Path):
    from PIL import Image

    if _iso_bmff(path):
        raise ValueError("ISO-BMFF file: not read here")
    Image.MAX_IMAGE_PIXELS = 300_000_000
    return Image.open(path)


def file_fields(path: Path, ext: str | None = None) -> dict:
    """Provenance fields a file's own metadata states: creator (IIM By-line,
    EXIF Artist), credit (IIM Credit), license (IIM/EXIF copyright notice)
    and, when a generator names itself, ai_generated + ai_generator. Only
    metadata, never pixels. {} for anything unreadable."""
    ext = (ext or path.suffix.lstrip(".")).lower()
    if ext in SKIP_EXTS:
        return {}
    out: dict[str, Any] = {}
    software: list[str] = []
    try:
        with _open_image(path) as im:
            if im.format not in RASTER_FORMATS:
                return {}
            try:
                exif = im.getexif()
            except Exception:
                exif = {}
            artist = _txt(exif.get(0x013B)) if exif else None
            copyright_ = _txt(exif.get(0x8298)) if exif else None
            if exif and exif.get(0x0131):
                software.append(_txt(exif.get(0x0131)) or "")
            iptc = {}
            try:
                from PIL import IptcImagePlugin

                iptc = IptcImagePlugin.getiptcinfo(im) or {}
            except Exception:
                iptc = {}
            byline = _txt(iptc.get((2, 80)))
            credit = _txt(iptc.get((2, 110)))
            iim_copy = _txt(iptc.get((2, 116)))
            if byline or artist:
                out["creator"] = byline or artist
            if credit:
                out["credit"] = credit
            if iim_copy or copyright_:
                out["license"] = iim_copy or copyright_
            info = getattr(im, "info", {}) or {}
            if im.format == "PNG":
                for key, name in PNG_GENERATOR_KEYS.items():
                    if key in info and _txt(info[key]):
                        out.setdefault("ai_generator", name)
                for key in ("Software", "Source", "Comment", "Description", "Generator"):
                    if info.get(key):
                        software.append(_txt(info[key]) or "")
                if not out.get("creator") and _txt(info.get("Author")):
                    out["creator"] = _txt(info.get("Author"))
                if not out.get("license") and _txt(info.get("Copyright")):
                    out["license"] = _txt(info.get("Copyright"))
    except Exception as exc:  # noqa: BLE001 - any unreadable file is just "nothing"
        log.debug("metadata of %s not read: %s", path.name, exc)
        return out
    try:
        from . import xmp

        packet = xmp.embedded_packet(path)
        if packet:
            props = xmp.parse_properties(packet)
            software += props.get(xmp._q("xmp", "CreatorTool"), [])
    except Exception:  # noqa: BLE001
        pass
    for s in software:
        name = generator_from_text(s)
        if name:
            out.setdefault("ai_generator", name)
            break
    if out.get("ai_generator"):
        out["ai_generated"] = True
    return {k: v for k, v in out.items() if v not in (None, "")}


# ---------- reading the stored page snapshot ----------

SNAPSHOT_HEAD_BYTES = 2 * 1024 * 1024
_CREATOR_META = ("author", "article:author", "dc.creator", "dcterms.creator", "citation_author",
                 "parsely-author", "sailthru.author", "byl", "byline", "photographer",
                 "og:image:credit", "twitter:creator")
_LICENSE_META = ("license", "dcterms.license", "dc.rights", "dcterms.rights", "rights",
                 "copyright")


class _Meta(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.license_href: str | None = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("name") or a.get("property") or a.get("itemprop") or "").strip().lower()
            content = a.get("content", "").strip()
            if key and content and key not in self.meta:
                self.meta[key] = content
        elif tag == "link" and "license" in (a.get("rel") or "").lower().split():
            self.license_href = self.license_href or a.get("href", "").strip() or None


def _person(value: str | None) -> str | None:
    v = _txt(value)
    if not v or re.match(r"^https?://", v):
        return None  # article:author is often a profile URL: not a name
    v = re.sub(r"^(by|photo(graph)?( by)?|credit)\s*[:\-]?\s+", "", v, flags=re.I).strip()
    if v.startswith("@") and " " not in v:
        return None  # a bare social handle says too little to print
    return v[:200] or None


def page_fields(item_id: str) -> dict:
    """Provenance fields from the meta tags of the item's stored snapshot
    (page/snapshot.html). Reads the file only; never the network."""
    from . import snapshots

    path = snapshots.page_dir(item_id) / snapshots.HTML_FILE
    try:
        with path.open("rb") as f:
            head = f.read(SNAPSHOT_HEAD_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return {}
    parser = _Meta()
    try:
        parser.feed(head)
    except Exception:  # noqa: BLE001 - malformed html: keep what was read
        pass
    m = parser.meta
    out: dict[str, Any] = {}
    for key in _CREATOR_META:
        name = _person(m.get(key))
        if name:
            out["creator"] = name
            break
    if m.get("og:site_name"):
        out["site"] = (_txt(m["og:site_name"]) or "")[:200]
    title = m.get("og:title") or m.get("twitter:title")
    if title:
        out["page_title"] = _txt(title)
    for key in _LICENSE_META:
        if _txt(m.get(key)):
            out["license"] = (_txt(m[key]) or "")[:500]
            break
    href = parser.license_href or ""
    if href.startswith(("http://", "https://")):
        out["rights_url"] = href
    # a page's <meta name=generator> is its CMS, never the image's: not used
    return {k: v for k, v in out.items() if v}


# ---------- auto-fill ----------

FILLABLE = ("creator", "credit", "license", "rights_url", "site", "page_title", "ai_generated",
            "ai_generator")


def original_path(item) -> Path:
    return get_settings().items_dir / item.id / f"original.{item.ext}"


def suggestions(item) -> dict:
    """What the item's own files say that its provenance doesn't have yet:
    embedded metadata first, then the stored page snapshot. Only empty
    fields; ai_generated only when nobody said either way."""
    from . import provenance as provenance_svc

    have = provenance_svc.out(item)
    found: dict[str, Any] = {}
    page = page_fields(item.id)
    from_file = file_fields(original_path(item), item.ext)
    for src in (page, from_file):  # the file wins over the page
        found.update(src)
    sw = ((item.media or {}).get("exif") or {}).get("software") if item.media else None
    gen = generator_from_text(sw)
    if gen and "ai_generator" not in found:
        found["ai_generator"], found["ai_generated"] = gen, True
    out: dict[str, Any] = {}
    for key in FILLABLE:
        if key not in found:
            continue
        if key == "ai_generated":
            if have.get("ai_generated") is None:
                out[key] = found[key]
        elif not have.get(key):
            out[key] = found[key]
    try:
        out = provenance_svc.clean(out)
    except validation.ValidationError:
        cleaned = {}
        for k, v in out.items():
            try:
                cleaned.update(provenance_svc.clean({k: v}))
            except validation.ValidationError:
                continue
        out = cleaned
    return out


def autofill(session, item_id: str, *, keep_modified: bool = True) -> dict:
    """Fill the item's empty provenance fields from its stored files. One
    edit event (undoable with the batch); with keep_modified, modified_at
    stays (background runs: the snapshot hook, the backfill)."""
    from . import items as items_svc

    item = items_svc.get_item(session, item_id, with_deleted=False)
    patch = suggestions(item)
    if not patch:
        return {}
    if not keep_modified:
        items_svc.update_item(session, item_id, fields={"provenance": patch})
        return patch
    from sqlalchemy.orm.attributes import flag_modified

    from . import events
    from . import provenance as provenance_svc

    item = items_svc.get_item(session, item_id, for_update=True)
    before = events.state(item)
    provenance_svc.merge(item, patch)
    events.record_changes(session, item.id, before, events.state(item))
    # marks modified_at as set (to its own value), so models._touch_on_edit
    # leaves it: derived from files already in the library, not an edit
    flag_modified(item, "modified_at")
    items_svc.save(session, item)
    return patch


def backfill(session, *, dry_run: bool = False, limit: int | None = None,
             progress=None) -> dict:
    """kist credits-backfill: auto-fill every live item from its stored
    files. Safe to re-run: items with nothing new are skipped."""
    from ..models import Item

    ids = list(session.execute(
        sa.select(Item.id).where(Item.deleted_at.is_(None)).order_by(Item.id)
    ).scalars())
    counts = {"checked": 0, "filled": 0, "fields": 0, "ai_flagged": 0}
    for item_id in ids:
        if limit is not None and counts["filled"] >= limit:
            break
        counts["checked"] += 1
        try:
            if dry_run:
                item = session.get(Item, item_id)
                patch = suggestions(item) if item is not None else {}
            else:
                patch = autofill(session, item_id, keep_modified=True)
        except Exception:  # noqa: BLE001 - one bad file never stops the run
            session.rollback()
            log.exception("credits auto-fill of %s failed", item_id)
            continue
        if patch:
            counts["filled"] += 1
            counts["fields"] += len(patch)
            counts["ai_flagged"] += int(patch.get("ai_generated") is True)
            if progress:
                progress(item_id, patch)
        if counts["checked"] % 200 == 0:
            session.expunge_all()
    return counts


# ---------- print check ----------


def _icc_description(icc: bytes) -> str | None:
    """The profile's name from its 'desc' tag (ICC v2 textDescription or v4
    multiLocalizedUnicode)."""
    try:
        n = int.from_bytes(icc[128:132], "big")
        for i in range(min(n, 100)):
            off = 132 + i * 12
            sig = icc[off:off + 4]
            if sig != b"desc":
                continue
            start = int.from_bytes(icc[off + 4:off + 8], "big")
            size = int.from_bytes(icc[off + 8:off + 12], "big")
            data = icc[start:start + size]
            kind = data[:4]
            if kind == b"desc":
                count = int.from_bytes(data[8:12], "big")
                text = data[12:12 + count].split(b"\x00", 1)[0]
                return text.decode("latin-1").strip() or None
            if kind == b"mluc":
                records = int.from_bytes(data[8:12], "big")
                rec_size = int.from_bytes(data[12:16], "big")
                if records < 1:
                    return None
                ln = int.from_bytes(data[16 + 4:16 + 8], "big")
                o = int.from_bytes(data[16 + 8:16 + 12], "big")
                del rec_size
                return data[o:o + ln].decode("utf-16-be", errors="replace").strip("\x00 ") or None
    except Exception:  # noqa: BLE001
        return None
    return None


ICC_SPACES = {b"RGB ": "RGB", b"CMYK": "CMYK", b"GRAY": "Grey", b"Lab ": "Lab"}
MODE_SPACES = {"CMYK": "CMYK", "L": "Grey", "LA": "Grey", "1": "Black and white", "LAB": "Lab"}


@lru_cache(maxsize=2048)
def _header_info(path_str: str, sha256: str, ext: str) -> dict:
    """ICC profile, colour space and the file's own dpi (cached by sha256)."""
    del sha256  # part of the cache key: a new version is a new file
    out: dict[str, Any] = {"icc": None, "colour_space": None, "file_dpi": None}
    if ext.lower() in SKIP_EXTS:
        return out
    try:
        with _open_image(Path(path_str)) as im:
            if im.format not in RASTER_FORMATS:
                return out
            icc = im.info.get("icc_profile")
            if icc:
                out["icc"] = _icc_description(icc)
                out["colour_space"] = ICC_SPACES.get(icc[16:20])
            out["colour_space"] = out["colour_space"] or MODE_SPACES.get(im.mode) or (
                "RGB" if im.mode.startswith("RGB") or im.mode == "P" else None)
            dpi = im.info.get("dpi")
            if dpi and isinstance(dpi, tuple) and dpi[0]:
                out["file_dpi"] = round(float(dpi[0]))
    except Exception as exc:  # noqa: BLE001
        log.debug("header of %s not read: %s", path_str, exc)
    return out


def _size(px: int, dpi: int) -> dict:
    inches = px / dpi
    return {"in": round(inches, 2), "cm": round(inches * 2.54, 1)}


def fits(width: int, height: int, dpi: int) -> list[str]:
    big, small = max(width, height), min(width, height)
    return [PAPER_NAMES[k] for k, (s, long) in PAPERS.items()
            if big >= round(long * dpi) and small >= round(s * dpi)]


def print_info(item) -> dict:
    """The print check for the info panel and MCP."""
    from ..filetypes import kind_for

    w, h = item.width, item.height
    out: dict[str, Any] = {"width": w, "height": h, "sizes": {}, "fits": {}, "low_res": None,
                           "icc": None, "colour_space": None, "file_dpi": None}
    if w and h:
        for dpi in (300, 150):
            out["sizes"][str(dpi)] = {"width": _size(w, dpi), "height": _size(h, dpi)}
            out["fits"][str(dpi)] = fits(w, h, dpi)
        low_long, _ = parse_print_size(LOW_RES_PRINT_MAX)
        out["low_res"] = max(w, h) < round(low_long * DPI)
    if kind_for(item.mime, item.ext) == "image":
        path = original_path(item)
        if path.is_file():
            out.update(_header_info(str(path), item.sha256, item.ext))
    out["untagged"] = out["colour_space"] in ("RGB", None) and not out["icc"] and bool(w)
    return out


# ---------- credits on outputs: boards ----------


def board_credit_lines(session, doc: dict) -> list[str]:
    """One line per credited item on a board, in board order, deduplicated;
    only items the caller can see (a folder-limited reader's export)."""
    from ..models import Item
    from . import items as items_svc
    from .boards import item_ids_of

    seen: set[str] = set()
    out: list[str] = []
    for item_id in item_ids_of(doc):
        item = session.get(Item, item_id)
        if item is None or item.deleted_at is not None or not items_svc.visible(item):
            continue
        text = output_text(item)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def board_footer(session, doc: dict, img, scale: float):
    """The rendered board with a 'Credits' band under it (PNG and PDF)."""
    from PIL import Image, ImageDraw

    from . import board_render as render

    lines = board_credit_lines(session, doc)
    if not lines:
        return img
    size = max(11.0, min(28.0, 13 * max(scale, 0.5)))
    font = render._font(size)
    probe = ImageDraw.Draw(img)
    pad = int(size)
    width = max(1, img.width - 2 * pad)
    wrapped: list[str] = []
    for line in ["Credits"] + lines:
        wrapped += render._wrap(probe, line, font, width) or [line]
    line_h = int(size * 1.4)
    band = Image.new("RGB", (img.width, pad * 2 + line_h * len(wrapped)),
                     render.canvas_bg(doc))
    d = ImageDraw.Draw(band)
    ink = render.canvas_ink(doc)
    d.line((pad, 0, img.width - pad, 0), fill=ink, width=1)
    y = pad
    for line in wrapped:
        d.text((pad, y), line, fill=ink, font=font)
        y += line_h
    out = Image.new("RGB", (img.width, img.height + band.height), render.canvas_bg(doc))
    out.paste(img, (0, 0))
    out.paste(band, (0, img.height))
    return out


def board_html_credits(session, doc: dict) -> str:
    """A <footer> for the board's HTML export ('' without credits)."""
    import html as html_mod

    from . import board_render as render

    lines = board_credit_lines(session, doc)
    if not lines:
        return ""
    items = "".join(f"<li>{html_mod.escape(t)}</li>" for t in lines)
    ink = render.canvas_ink(doc)
    return (f'<footer class="credits" style="padding:16px;color:{html_mod.escape(ink)};'
            'font:13px/1.4 system-ui,sans-serif"><strong>Credits</strong>'
            f'<ul style="margin:6px 0 0;padding-left:18px">{items}</ul></footer>')


def bag_credits(rows: list[dict]) -> str:
    """CREDITS.txt for a BagIt export: each file and its credit, then the
    files with none (so nobody mistakes 'missing' for 'not needed')."""
    with_credit = [r for r in rows if r.get("credit_line")]
    without = [r for r in rows if not r.get("credit_line")]
    out = ["Credits", "=======", ""]
    for r in with_credit:
        status = RIGHTS_TEXT.get(r.get("rights_status") or "", "")
        out.append(f"{r['path']}\n    {r['credit_line']}" + (f"  [{status}]" if status else ""))
    if without:
        out += ["", "No credit recorded", "------------------"]
        out += [r["path"] for r in without]
    return "\n".join(out) + "\n"
