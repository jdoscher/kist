"""Present mode (Kist 2.0 F2): a folder, smart folder, board, selection or
search as a full-screen show.

    POST /api/present/slides   {source, compare?, pairs?} (+ the search's
                               query string when source.kind is "search")
    GET/POST /api/shows        saved shows; PATCH/DELETE /api/shows/{id}
    GET  /api/shows/{id}/slides
    POST /api/present/pdf      the same body, as a PDF (services/present_pdf)

A slide is one item, or two side by side (a compare slide: a chosen pair,
or an item with the other member of its stack, or with its previous
version). Every slide item carries what the show needs and nothing the
screen shouldn't show unasked: the image URL Present loads (the original
when the browser can show it colour-managed, else the server's display
copy; never a recompressed preview of a web-native file), a caption (title
and credit from provenance; the filename only when the show asks), the
presenter notes (the item's notes, then its caption), and whether Ken Burns
may ever move it (never drawings, documents, screenshots, PDFs or video).

Saved shows are plain JSON in library/.kist/shows.json, the only copy:
`kist rebuild-index` never touches it and `kist verify` checks it parses
and that its folders, smart folders and boards still exist. A saved show
of a search keeps the items the search found when it was saved (a show
shouldn't change under the presenter); folder, smart folder and board
shows stay live.

Folder-limited read tokens see only their items: every resolved item goes
through items.visible, and a board they can't see is a 404.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .. import access
from ..config import get_settings
from ..filetypes import kind_for
from ..ids import new_ulid
from ..models import Folder, Item, ItemText, SmartFolder
from . import items as items_svc
from . import meta as meta_svc
from . import provenance as provenance_svc
from . import selection, validation

FILE = "shows.json"
SOURCE_KINDS = ("folder", "smart", "board", "items", "search")
SURROUNDS = ("grey", "black")
GESTURE_SECONDS = (30, 60, 120, 300)
PAIR_MODES = ("none", "stacks", "versions")
PDF_LAYOUTS = ("slides", "sheet")
PAGE_SIZES = ("a4", "a3", "letter", "tabloid")
NAME_MAX = 120
MAX_SHOWS = 500
MAX_IDS = 2000
ID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

# browsers show these natively and colour-manage them from their own ICC
# profile (P3 stays P3): the original is the accurate image
WEB_NATIVE = {"png", "jpg", "jpeg", "gif", "webp", "avif"}
# a bigger original is shown from the 4096/2048 display copy instead
ORIGINAL_MAX_BYTES = 60 * 1024 * 1024
LOW_RES_LONG_SIDE = 1500

# tags that mark an item as a drawing or a document: Ken Burns never moves
# those (a pan crops the drawing; Chicago, Paris, LA and NYC panels)
STILL_WORDS = {
    "drawing", "drawings", "sketch", "sketches", "sketchbook", "pencil", "graphite",
    "ink", "line art", "lineart", "linework", "charcoal", "plan", "plans", "section",
    "sections", "elevation", "elevations", "diagram", "diagrams", "technical drawing",
    "document", "documents", "screenshot", "screenshots", "storyboard", "storyboards",
    "spec sheet", "page", "pages", "scan", "scans", "typography", "lettering",
}
# OCR words above this: a page of text, not a picture
DOCUMENT_WORDS = 25


class NotFound(Exception):
    def __init__(self, what: str):
        super().__init__(f"{what} not found")


# ---------- options ----------

DEFAULT_OPTIONS = {
    "surround": "grey",
    "ken_burns": False,
    "captions": True,
    "filenames": False,
    "advance_seconds": 0,
    "gesture_seconds": None,
    "pairs": "none",
}


def clean_options(data: dict | None) -> dict:
    data = dict(data or {})
    unknown = set(data) - set(DEFAULT_OPTIONS)
    if unknown:
        raise validation.ValidationError(f"unknown show options: {', '.join(sorted(unknown))}")
    out = {**DEFAULT_OPTIONS, **{k: v for k, v in data.items() if v is not None
                                  or k == "gesture_seconds"}}
    if out["surround"] not in SURROUNDS:
        raise validation.ValidationError("surround is grey or black")
    for key in ("ken_burns", "captions", "filenames"):
        if not isinstance(out[key], bool):
            raise validation.ValidationError(f"{key} is true or false")
    try:
        out["advance_seconds"] = int(out["advance_seconds"] or 0)
    except (TypeError, ValueError):
        raise validation.ValidationError("advance_seconds is a whole number") from None
    if not 0 <= out["advance_seconds"] <= 3600:
        raise validation.ValidationError("advance_seconds is 0 (off) to 3600")
    g = out["gesture_seconds"]
    if g is not None and g not in GESTURE_SECONDS:
        raise validation.ValidationError("gesture_seconds is 30, 60, 120, 300 or null")
    if out["pairs"] not in PAIR_MODES:
        raise validation.ValidationError(f"pairs is one of {', '.join(PAIR_MODES)}")
    return out


def clean_source(data: dict | None) -> dict:
    if not isinstance(data, dict):
        raise validation.ValidationError("source must be an object")
    kind = data.get("kind")
    if kind not in SOURCE_KINDS:
        raise validation.ValidationError(f"source.kind is one of {', '.join(SOURCE_KINDS)}")
    out: dict = {"kind": kind}
    if kind in ("folder", "smart", "board"):
        sid = data.get("id")
        if not isinstance(sid, str) or not sid:
            raise validation.ValidationError(f"a {kind} source needs id")
        out["id"] = sid
    if kind == "items":
        ids = _ids(data.get("item_ids"), "source.item_ids")
        if not ids:
            raise validation.ValidationError("an items source needs item_ids")
        out["item_ids"] = ids
    return out


def _ids(value, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
        raise validation.ValidationError(f"{name} is a list of item ids")
    ids = list(dict.fromkeys(value))
    if len(ids) > MAX_IDS:
        raise validation.ValidationError(f"{name} has at most {MAX_IDS} ids")
    return ids


def clean_compare(value) -> list[list[str]]:
    """Chosen pairs: [[a, b], ...]. b joins a's slide (and leaves its own)."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_IDS:
        raise validation.ValidationError("compare is a list of [a, b] item id pairs")
    out = []
    for pair in value:
        if (not isinstance(pair, list) or len(pair) != 2
                or not all(isinstance(i, str) for i in pair) or pair[0] == pair[1]):
            raise validation.ValidationError("each compare pair is two different item ids")
        out.append([pair[0], pair[1]])
    return out


# ---------- resolving a source to items ----------


def _visible(items: list[Item]) -> list[Item]:
    return [i for i in items if i.deleted_at is None and items_svc.visible(i)]


def source_items(session: Session, source: dict, *, search_items=None) -> list[Item]:
    """The source's live items, in show order (a board in board order, a
    selection in its own order, a search in its ranking), cut at
    KIST_PRESENT_MAX_SLIDES. `search_items` is a callable(limit) giving a
    search's items (the API runs the request's own search)."""
    cap = max(1, int(get_settings().present_max_slides))
    kind = source["kind"]
    if kind == "search":
        if search_items is None:
            raise validation.ValidationError("a search source is only resolved by the API")
        found = search_items(cap)
    elif kind == "board":
        from . import boards

        try:
            boards.get_board(session, source["id"])  # 404 for a reader who can't see it
        except boards.NotFound:
            raise NotFound(f"board {source['id']}") from None
        found = selection.items(session, "board", source["id"], None, limit=cap)
    elif kind == "folder":
        folder = session.get(Folder, source["id"])
        reader = access.restricted()
        if folder is None or (reader is not None and folder.id not in reader.visible_folders):
            raise NotFound(f"folder {source['id']}")
        found = selection.items(session, "folder", source["id"], None, limit=cap)
    elif kind == "smart":
        if session.get(SmartFolder, source["id"]) is None or access.restricted() is not None:
            raise NotFound(f"smart folder {source['id']}")
        found = selection.items(session, "smart", source["id"], None, limit=cap)
    else:
        rows = {i.id: i for i in session.execute(
            sa.select(Item).where(Item.id.in_(source["item_ids"]))
        ).scalars()}
        found = [rows[i] for i in source["item_ids"] if i in rows][:cap]
    return _visible(list(found))[:cap]


# ---------- slides ----------


def image_src(item: Item) -> tuple[str | None, str]:
    """(URL, which copy) of the image Present shows: the original when the
    browser renders it with its own colour profile, else the full-res
    display copy (HEIC, TIFF, PSD, RAW: 4096, falling back to the 2048
    copy), else the largest thumbnail. `present=1` lets the service worker
    keep it for offline shows without caching anything else under /api."""
    kind = kind_for(item.mime, item.ext)
    ext = (item.ext or "").lower()
    rev = item.preview_rev or 0
    if kind == "video":
        return f"/api/items/{item.id}/file?present=1", "original"
    if kind == "image" and ext in WEB_NATIVE and (item.size_bytes or 0) <= ORIGINAL_MAX_BYTES:
        return f"/api/items/{item.id}/file?present=1", "original"
    if item.preview_status != "ready":
        return None, "none"
    if kind == "image":
        return f"/api/items/{item.id}/thumb?size=4096&v={rev}&present=1", "display"
    return f"/api/items/{item.id}/thumb?size=2048&v={rev}&present=1", "screen"


def credit_of(item: Item) -> str:
    """The credit line a caption shows: F4's credit line ("Creator /
    Credit", the line every export prints; the licence is its own field).
    Never where the item was saved from."""
    from . import credits as credits_svc

    try:
        return str(credits_svc.item_credit(item)["line"] or "")
    except Exception:  # a caption must never break a show
        prov = provenance_svc.out(item)
        return str(prov.get("credit") or prov.get("creator") or "")


def _ocr_words(session: Session, ids: list[str]) -> dict[str, int]:
    if not ids:
        return {}
    rows = session.execute(
        sa.select(ItemText.item_id, ItemText.text)
        .where(ItemText.item_id.in_(ids), ItemText.source.in_(("ocr", "pdf")))
    ).all()
    out: dict[str, int] = {}
    for item_id, text in rows:
        out[item_id] = out.get(item_id, 0) + len((text or "").split())
    return out


def _kind_blocked(session: Session, ids: list[str]) -> set[str]:
    """F1 (utility collections) hook: `kinds.motion_blocked_ids(session,
    ids)` names the items in its Drawings, Documents and Screenshots
    collections. Absent in builds without F1."""
    try:
        from . import kinds  # type: ignore[attr-defined]
    except ImportError:
        return set()
    fn = getattr(kinds, "motion_blocked_ids", None)
    if not callable(fn):
        return set()
    try:
        return set(fn(session, ids))
    except Exception:
        return set()


def motion_ok_map(session: Session, items: list[Item]) -> dict[str, bool]:
    ids = [i.id for i in items]
    words = _ocr_words(session, ids)
    blocked = _kind_blocked(session, ids)
    out = {}
    for item in items:
        ok = kind_for(item.mime, item.ext) == "image" and (item.ext or "").lower() != "svg"
        if ok and (item.id in blocked or words.get(item.id, 0) >= DOCUMENT_WORDS):
            ok = False
        if ok and any(t.name.lower() in STILL_WORDS
                      or t.name.lower().rsplit(":", 1)[-1] in STILL_WORDS for t in item.tags):
            ok = False
        out[item.id] = ok
    return out


def slide_item(item: Item, motion_ok: bool) -> dict:
    src, src_kind = image_src(item)
    cutout = isinstance((item.media or {}).get("cutout"), dict)  # F7
    quick = "cutout-preview" if cutout else "thumb"
    prov = provenance_svc.out(item)
    long_side = max(item.width or 0, item.height or 0)
    return {
        "id": item.id,
        "filename": item.filename,
        "ext": item.ext,
        "mime": item.mime,
        "kind": kind_for(item.mime, item.ext),
        "width": item.width,
        "height": item.height,
        "size_bytes": item.size_bytes,
        "preview_rev": item.preview_rev or 0,
        "src": src,
        "src_kind": src_kind,
        # F7: a cut-out's quick copy keeps its transparency (the 1024
        # thumbnail is flattened)
        "thumb": (f"/api/items/{item.id}/{quick}?size=1024&v={item.preview_rev or 0}&present=1"
                  if item.preview_status == "ready" else None),
        "cutout": cutout,
        "title": str(prov.get("page_title") or ""),
        "credit": credit_of(item),
        "license": str(prov.get("license") or ""),
        "notes": item.notes or "",
        "caption": item.caption or "",
        "flag": item.flag,
        "rating": item.rating or 0,
        "stack_id": item.stack_id,
        "motion_ok": motion_ok,
        "low_res": bool(long_side) and long_side < LOW_RES_LONG_SIDE
        and kind_for(item.mime, item.ext) == "image",
    }


def _previous_version(session: Session, item: Item) -> Item | None:
    from . import versions

    try:
        chain = versions.chain(session, item.id)
    except Exception:
        return None
    ids = [c.id for c in chain]
    if len(ids) < 2 or item.id not in ids:
        return None
    n = ids.index(item.id)
    other = chain[n - 1] if n > 0 else chain[1]
    return other if other.deleted_at is None and items_svc.visible(other) else None


def _stack_partner(session: Session, item: Item, show_ids: set[str]) -> Item | None:
    if not item.stack_id:
        return None
    from . import stacks

    try:
        members = stacks.members(session, item.stack_id)
    except Exception:
        return None
    others = [m for m in members if m.id != item.id and m.deleted_at is None
              and items_svc.visible(m)]
    # prefer a member that's in the show too, so the pair reads as one
    others.sort(key=lambda m: (m.id not in show_ids, m.created_at))
    return others[0] if others else None


def build_slides(session: Session, items: list[Item], *, compare: list[list[str]] | None = None,
                 pairs: str = "none") -> list[dict]:
    """Items -> slides. A chosen pair [a, b] puts b beside a (b's own slide
    goes); `pairs` "stacks" or "versions" pairs every other item with its
    stack partner or its previous version."""
    by_id = {i.id: i for i in items}
    show_ids = set(by_id)
    partner: dict[str, str] = {}
    absorbed: set[str] = set()
    extra: dict[str, Item] = {}
    for a, b in compare or []:
        if a in by_id and a not in absorbed and a not in partner and b != a:
            other = by_id.get(b)
            if other is None:
                row = session.get(Item, b)
                if row is None or row.deleted_at is not None or not items_svc.visible(row):
                    continue
                other = extra[b] = row
            partner[a] = b
            absorbed.add(b)
    if pairs in ("stacks", "versions"):
        for item in items:
            if item.id in partner or item.id in absorbed:
                continue
            other = (_stack_partner(session, item, show_ids) if pairs == "stacks"
                     else _previous_version(session, item))
            if other is None or other.id in partner or other.id == item.id:
                continue
            if other.id in by_id and other.id in absorbed:
                continue
            partner[item.id] = other.id
            absorbed.add(other.id)
            if other.id not in by_id:
                extra[other.id] = other
    everything = list(items) + [i for k, i in extra.items() if k not in by_id]
    motion = motion_ok_map(session, everything)
    lookup = {**by_id, **extra}
    slides = []
    for item in items:
        if item.id in absorbed and item.id not in partner:
            continue
        if item.id in partner:
            other = lookup[partner[item.id]]
            # versions read old -> new, left to right
            left, right = item, other
            if pairs == "versions" and item.id not in [a for a, _ in compare or []] \
                    and other.created_at < item.created_at:
                left, right = other, item
            slides.append({"kind": "compare", "items": [
                slide_item(left, motion[left.id]), slide_item(right, motion[right.id])]})
        else:
            slides.append({"kind": "single", "items": [slide_item(item, motion[item.id])]})
    return slides


def source_name(session: Session, source: dict) -> str:
    kind = source["kind"]
    if kind == "folder":
        f = session.get(Folder, source["id"])
        return f.name if f else "Folder"
    if kind == "smart":
        s = session.get(SmartFolder, source["id"])
        return s.name if s else "Smart folder"
    if kind == "board":
        from ..models import Board

        b = session.get(Board, source["id"])
        return b.name if b else "Board"
    if kind == "items":
        n = len(source.get("item_ids") or [])
        return f"{n} item{'' if n == 1 else 's'}"
    return "Search"


def resolve(session: Session, source: dict, *, compare=None, pairs: str = "none",
            search_items=None) -> dict:
    source = clean_source(source)
    compare = clean_compare(compare)
    if pairs not in PAIR_MODES:
        raise validation.ValidationError(f"pairs is one of {', '.join(PAIR_MODES)}")
    items = source_items(session, source, search_items=search_items)
    slides = build_slides(session, items, compare=compare, pairs=pairs)
    flat = [it for s in slides for it in s["items"]]
    return {
        "name": source_name(session, source),
        "source": source,
        "slides": slides,
        "item_count": len({it["id"] for it in flat}),
        "truncated": len(items) >= max(1, int(get_settings().present_max_slides)),
        "low_res": sum(1 for it in flat if it["low_res"]),
        # what an offline copy of the whole show needs (originals' sizes;
        # display copies count as a rough 2 MB)
        "offline_bytes": sum(it["size_bytes"] if it["src_kind"] == "original" else 2_000_000
                             for it in flat if it["src"]),
    }


# ---------- saved shows (library/.kist/shows.json) ----------


def path() -> Path:
    return get_settings().state_dir / FILE


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read() -> list[dict]:
    p = path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        shows = data["shows"]
        if not isinstance(shows, list):
            raise ValueError
        return shows
    except (OSError, ValueError, KeyError, TypeError):
        raise validation.ValidationError(f"{FILE} is unreadable; fix or remove it") from None


def _write(shows: list[dict]) -> None:
    meta_svc.atomic_write_text(path(), json.dumps({"version": 1, "shows": shows}, indent=2,
                                                  ensure_ascii=False))


def _clean_name(name) -> str:
    text = " ".join(str(name or "").split())
    if not text:
        raise validation.ValidationError("a show needs a name")
    if len(text) > NAME_MAX:
        raise validation.ValidationError(f"a show name is at most {NAME_MAX} characters")
    return text


def _visible_show(session: Session, show: dict) -> bool:
    """A folder-limited reader sees a saved show only when it can see its
    source (its folder, or every listed item)."""
    reader = access.restricted()
    if reader is None:
        return True
    src = show.get("source") or {}
    if src.get("kind") == "folder":
        return src.get("id") in reader.visible_folders
    if src.get("kind") == "items":
        ids = src.get("item_ids") or []
        rows = session.execute(sa.select(Item).where(Item.id.in_(ids or [""]))).scalars().all()
        return bool(rows) and len(rows) == len(ids) and all(items_svc.visible(i) for i in rows)
    if src.get("kind") == "board":
        from . import boards

        try:
            boards.get_board(session, src.get("id") or "")
            return True
        except boards.NotFound:
            return False
    return False


def list_shows(session: Session) -> list[dict]:
    return [s for s in _read() if _visible_show(session, s)]


def get_show(session: Session, show_id: str) -> dict:
    for s in _read():
        if s.get("id") == show_id and _visible_show(session, s):
            return s
    raise NotFound(f"show {show_id}")


def create_show(session: Session, *, name, source: dict, options: dict | None = None,
                compare=None, search_item_ids: list[str] | None = None) -> dict:
    source = clean_source(source)
    if source["kind"] == "search":
        # a saved search show keeps what the search found (see the docstring)
        ids = _ids(search_item_ids, "item_ids")
        if not ids:
            raise validation.ValidationError("a saved search show needs the item_ids it found")
        source = {"kind": "items", "item_ids": ids}
    _check_source_exists(session, source)
    shows = _read()
    if len(shows) >= MAX_SHOWS:
        raise validation.ValidationError(f"at most {MAX_SHOWS} saved shows")
    now = _now()
    show = {"id": new_ulid(), "name": _clean_name(name), "source": source,
            "options": clean_options(options), "compare": clean_compare(compare),
            "created_at": now, "modified_at": now}
    shows.append(show)
    _write(shows)
    return show


def _check_source_exists(session: Session, source: dict) -> None:
    kind = source["kind"]
    if kind == "folder" and session.get(Folder, source["id"]) is None:
        raise validation.UnknownFolder(source["id"])
    if kind == "smart" and session.get(SmartFolder, source["id"]) is None:
        raise validation.ValidationError(f"Unknown smart folder: {source['id']}")
    if kind == "board":
        from ..models import Board

        b = session.get(Board, source["id"])
        if b is None or b.deleted_at is not None:
            raise validation.ValidationError(f"Unknown board: {source['id']}")


def update_show(session: Session, show_id: str, fields: dict) -> dict:
    shows = _read()
    for s in shows:
        if s.get("id") == show_id:
            break
    else:
        raise NotFound(f"show {show_id}")
    unknown = set(fields) - {"name", "options", "compare", "item_ids"}
    if unknown:
        raise validation.ValidationError(f"unknown fields: {', '.join(sorted(unknown))}")
    if "name" in fields and fields["name"] is not None:
        s["name"] = _clean_name(fields["name"])
    if fields.get("options") is not None:
        s["options"] = clean_options({**s.get("options", {}), **fields["options"]})
    if fields.get("compare") is not None:
        s["compare"] = clean_compare(fields["compare"])
    if fields.get("item_ids") is not None:
        if s["source"]["kind"] != "items":
            raise validation.ValidationError("only a show of chosen items takes item_ids")
        ids = _ids(fields["item_ids"], "item_ids")
        if not ids:
            raise validation.ValidationError("a show needs at least one item")
        s["source"]["item_ids"] = ids
    s["modified_at"] = _now()
    _write(shows)
    return s


def delete_show(session: Session, show_id: str) -> None:
    shows = _read()
    keep = [s for s in shows if s.get("id") != show_id]
    if len(keep) == len(shows):
        raise NotFound(f"show {show_id}")
    _write(keep)


def show_slides(session: Session, show_id: str) -> dict:
    show = get_show(session, show_id)
    opts = clean_options(show.get("options"))
    out = resolve(session, show["source"], compare=show.get("compare"), pairs=opts["pairs"])
    out.update(name=show["name"], show_id=show["id"], options=opts)
    return out


def check_file(session: Session) -> list[str]:
    """verify: shows.json parses, and its folders, smart folders and boards
    still exist. Items that went to the trash just drop out of a show."""
    try:
        shows = _read()
    except validation.ValidationError as exc:
        return [str(exc)]
    problems = []
    for s in shows:
        try:
            src = clean_source(s.get("source"))
            clean_options(s.get("options"))
            _check_source_exists(session, src)
        except (validation.ValidationError, validation.UnknownFolder) as exc:
            problems.append(f"{FILE}: show {s.get('id')} ({s.get('name')}): {exc}")
    return problems
