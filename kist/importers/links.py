"""Pinterest, Are.na and Raindrop: collections of links to images.

None of these exports carry the images themselves (Are.na's channel
download can), so each entry is a URL Kist downloads through its SSRF
guard. A page URL is fine: Kist takes the page's og:image.

pinterest   the account data export ("Request your data"): a zip of HTML
            and/or JSON. Per pin it lists the board, title, details
            (description), the source link, the image link or its
            32-character image signature, and when it was saved. Parsed
            leniently: JSON lists of pin objects, or HTML/text where each
            pin is a run of "Label: value" lines. Image: the image URL, else
            i.pinimg.com/originals/<sig[0:2]>/<sig[2:4]>/<sig[4:6]>/<sig>.jpg,
            else the source page's og:image. Boards (and sections) become
            folders, details become notes, the link becomes page_url.
arena       a channel: a public URL (https://www.are.na/<user>/<channel>, or
            arena:<channel-slug>) read through the public API
            (api.are.na/v2/channels/<slug>/contents, no key needed for
            public channels), or the "Download channel" zip. A zip's
            manifest.json / blocks JSON is read in the API's block shape
            (id, class, title, description, source.url, image.original.url,
            attachment.url, created_at) with a local `file`/`path` when the
            block's file is in the zip; files without a manifest come in as
            they are. Text and channel blocks are skipped (counted).
raindrop    Raindrop.io's CSV export (id, title, note, excerpt, url, folder,
            tags, created, cover, highlights, favorite) or its HTML
            (Netscape bookmark) export. The cover image is downloaded, else
            the page's og:image; collections become folders.
"""

import csv
import io
import json
import re
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlsplit

from ..services import netguard
from .base import (
    Entry,
    Importer,
    ImportError_,
    clean_tags,
    is_media,
    parse_time,
    safe_url,
    unpack,
)

# ---------- Pinterest ----------

LABELS = {
    "title": "title", "pin title": "title", "name": "title",
    "details": "details", "description": "details", "pin description": "details", "note": "details",
    "link": "link", "url": "link", "source": "link", "source url": "link", "source link": "link",
    "website": "link", "destination link": "link",
    "image": "image", "image url": "image", "image link": "image", "media url": "image",
    "image signature": "sig", "image hash": "sig", "image_signature": "sig",
    "board": "board", "board name": "board", "board_name": "board",
    "section": "section", "board section": "section", "section name": "section",
    "created at": "created", "created": "created", "saved at": "created", "date": "created",
    "created_at": "created", "pin id": "id", "id": "id", "alt text": "alt",
}
SIG_RE = re.compile(r"^[0-9a-f]{32}$")


class _Lines(HTMLParser):
    """HTML -> text lines, keeping link targets beside their text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = [""]
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "dt", "dd", "td"):
            self.lines.append("")
        if tag == "a":
            href = dict(attrs).get("href")
            if href and href.startswith(("http://", "https://")):
                self.lines[-1] += f" <{href}> "
        if tag == "img":
            src = dict(attrs).get("src")
            if src and src.startswith(("http://", "https://")):
                self.lines.append(f"Image: {src}")
                self.lines.append("")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.lines[-1] += data


def _label_value(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None
    label, value = line.split(":", 1)
    key = LABELS.get(" ".join(label.lower().split()))
    if key is None:
        return None
    value = value.strip()
    m = re.search(r"<(https?://[^>]+)>", value)
    if m:
        value = m.group(1) if key in ("link", "image") or not value.replace(
            m.group(0), "").strip() else value.replace(m.group(0), "").strip()
    return key, value.strip()


def pins_from_text(lines: list[str]) -> list[dict]:
    pins, cur = [], {}
    for raw in lines:
        line = " ".join(raw.split())
        if not line:
            continue
        lv = _label_value(line)
        if lv is None:
            continue
        key, value = lv
        # a label seen twice starts the next pin (once this one has a link)
        if key in cur and any(k in cur for k in ("link", "image", "sig")):
            pins.append(cur)
            cur = {} if key in ("board", "section") else {
                k: cur[k] for k in ("board", "section") if k in cur}
        cur[key] = value
    if cur:
        pins.append(cur)
    return [p for p in pins if p.get("link") or p.get("image") or p.get("sig")]


def pins_from_json(data) -> list[dict]:
    rows = data
    if isinstance(data, dict):
        for k in ("pins", "Pins", "data", "items"):
            if isinstance(data.get(k), list):
                rows = data[k]
                break
        else:
            rows = []
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        pin = {}
        for k, v in r.items():
            key = LABELS.get(" ".join(str(k).lower().replace("_", " ").split())) or LABELS.get(k)
            if key and v not in (None, ""):
                if isinstance(v, dict):  # {"board": {"name": ...}}
                    v = v.get("name") or v.get("url") or ""
                pin[key] = str(v)
        if pin.get("link") or pin.get("image") or pin.get("sig"):
            out.append(pin)
    return out


def pinimg_url(sig: str) -> str:
    return f"https://i.pinimg.com/originals/{sig[0:2]}/{sig[2:4]}/{sig[4:6]}/{sig}.jpg"


class PinterestImporter(Importer):
    kind = "pinterest"

    def entries(self) -> Iterator[Entry]:
        root = unpack(Path(self.source), self.workdir)
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        pins: list[dict] = []
        for f in files:
            suffix = f.suffix.lower()
            try:
                if suffix == ".json":
                    pins += pins_from_json(json.loads(f.read_text(encoding="utf-8")))
                elif suffix in (".html", ".htm", ".txt"):
                    text = f.read_text(encoding="utf-8", errors="replace")
                    if suffix == ".txt":
                        lines = text.splitlines()
                    else:
                        p = _Lines()
                        p.feed(text)
                        lines = p.lines
                    pins += pins_from_text(lines)
            except (OSError, ValueError):
                yield Entry(key=f.name, error=f"{f.name} unreadable")
        if not pins and not files:
            raise ImportError_("no Pinterest export there")
        seen = set()
        for n, pin in enumerate(pins):
            sig = (pin.get("sig") or "").lower()
            image = safe_url(pin.get("image"))
            if not image and SIG_RE.match(sig):
                image = pinimg_url(sig)
            link = safe_url(pin.get("link"))
            url = image or link
            key = pin.get("id") or sig or url or str(n)
            if key in seen:
                continue
            seen.add(key)
            board = [b for b in (pin.get("board"), pin.get("section")) if b]
            notes = "\n\n".join(x for x in (pin.get("title"), pin.get("details")) if x)
            if not url:
                yield Entry(key=key, error="no image or link for this pin")
                continue
            yield Entry(key=key, url=url, folders=[board] if board else [], notes=notes,
                        created_at=parse_time(pin.get("created")),
                        provenance={"page_url": link, "image_url": image})


# ---------- Are.na ----------

API = "https://api.are.na/v2"
PER_PAGE = 100
MAX_PAGES = 200
CHANNEL_URL = re.compile(r"^https?://(?:www\.)?are\.na/[^/]+/(?P<slug>[A-Za-z0-9_-]+)/?$")


def channel_slug(source: str) -> str | None:
    if source.startswith("arena:"):
        slug = source[len("arena:"):].strip()
    else:
        m = CHANNEL_URL.match(source.strip())
        slug = m.group("slug") if m else None
    return slug if slug and re.match(r"^[A-Za-z0-9_-]{1,200}$", slug) else None


def get_json(url: str, transport=None):
    from ..services.ingest import DEFAULT_USER_AGENT

    with netguard.guarded_get(url, {"User-Agent": DEFAULT_USER_AGENT,
                                    "Accept": "application/json"},
                              transport=transport) as (resp, _final, _deadline):
        body = b""
        for chunk in resp.iter_bytes(65536):
            body += chunk
            if len(body) > 20 * 1024 * 1024:
                raise ImportError_("the Are.na answer is too large")
    return json.loads(body)


def block_entry(block: dict, folders: list[list[str]], local: Path | None = None) -> Entry:
    cls = str(block.get("class") or block.get("base_class") or "")
    key = str(block.get("id") or block.get("title") or "?")
    source = block.get("source") if isinstance(block.get("source"), dict) else {}
    page = safe_url(source.get("url"))
    image = None
    img = block.get("image") if isinstance(block.get("image"), dict) else {}
    for size in ("original", "large", "display"):
        if isinstance(img.get(size), dict) and img[size].get("url"):
            image = safe_url(img[size]["url"])
            break
    att = block.get("attachment") if isinstance(block.get("attachment"), dict) else {}
    attachment = safe_url(att.get("url"))
    notes = "\n\n".join(str(x) for x in (block.get("title"), block.get("description"))
                        if x and str(x).strip())
    entry = Entry(key=key, folders=folders, notes=notes,
                  created_at=parse_time(block.get("connected_at") or block.get("created_at")),
                  provenance={"page_url": page, "image_url": image or attachment})
    if local is not None and local.is_file():
        entry.path = local
        entry.filename = local.name
    elif cls in ("Text", "Channel"):
        entry.skip = f"{cls.lower()} block"
    elif image or attachment:
        entry.url = image or attachment
    elif page:
        entry.url = page
    else:
        entry.skip = "nothing to download"
    return entry


class ArenaImporter(Importer):
    kind = "arena"

    def entries(self) -> Iterator[Entry]:
        slug = channel_slug(self.source)
        if slug is not None:
            yield from self._api(slug)
            return
        if self.source.startswith(("http://", "https://")):
            raise ImportError_("not an Are.na channel URL (https://www.are.na/<user>/<channel>)")
        root = unpack(Path(self.source), self.workdir)
        if not root.is_dir():
            raise ImportError_("expected an Are.na channel zip or folder")
        yield from self._export(root)

    def _api(self, slug: str) -> Iterator[Entry]:
        try:
            info = get_json(f"{API}/channels/{quote(slug)}?per=1", self.transport)
        except Exception as exc:
            raise ImportError_(f"Are.na channel {slug!r} couldn't be read: {exc}") from None
        title = str(info.get("title") or slug)
        for page in range(1, MAX_PAGES + 1):
            data = get_json(f"{API}/channels/{quote(slug)}/contents?page={page}&per={PER_PAGE}",
                            self.transport)
            blocks = data.get("contents") if isinstance(data, dict) else None
            if not blocks:
                break
            for block in blocks:
                if isinstance(block, dict):
                    yield block_entry(block, [[title]])
            if len(blocks) < PER_PAGE:
                break

    def _export(self, root: Path) -> Iterator[Entry]:
        manifest = None
        for name in ("manifest.json", "index.json", "channel.json", "blocks.json"):
            found = sorted(root.rglob(name))
            if found:
                manifest = found[0]
                break
        if manifest is not None:
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ImportError_(f"{manifest.name} unreadable: {exc}") from None
            channel = data.get("channel") if isinstance(data, dict) else None
            title = str((channel or {}).get("title") or (data.get("title") if isinstance(
                data, dict) else None) or manifest.parent.name)
            blocks = []
            if isinstance(data, list):
                blocks = data
            else:
                for k in ("blocks", "contents", "items"):
                    if isinstance(data.get(k), list):
                        blocks = data[k]
                        break
            base = manifest.parent.resolve()
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                local = None
                for k in ("file", "path", "filename", "local_path", "asset"):
                    if isinstance(block.get(k), str) and block[k]:
                        cand = (manifest.parent / block[k]).resolve()
                        if cand.is_relative_to(base):  # never outside the export
                            local = cand
                        break
                yield block_entry(block, [[title]], local)
            return
        # no manifest: the files themselves, in their folders
        for f in sorted(p for p in root.rglob("*") if p.is_file() and is_media(p)):
            parts = list(f.parent.relative_to(root).parts)
            yield Entry(key=str(f.relative_to(root)), path=f, filename=f.name,
                        folders=[parts] if parts else [])


# ---------- Raindrop ----------


class _Bookmarks(HTMLParser):
    """Netscape bookmark HTML: <H3> folders, <A HREF ADD_DATE TAGS>, <DD> notes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.pending_folder: str | None = None
        self.in_h3 = self.in_a = self.in_dd = False
        self.items: list[dict] = []
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "h3":
            self.in_h3, self.pending_folder = True, ""
        elif tag == "dl":
            if self.pending_folder is not None:
                self.stack.append(self.pending_folder)
                self.pending_folder = None
            else:
                self.stack.append("")
        elif tag == "a":
            self.in_a = True
            self.items.append({"url": a.get("href"), "created": a.get("add_date"),
                               "tags": a.get("tags") or "", "cover": a.get("data-cover"),
                               "title": "", "note": "",
                               "folder": [s for s in self.stack if s]})
        elif tag == "dd":
            self.in_dd = True

    def handle_endtag(self, tag):
        if tag == "h3":
            self.in_h3 = False
        elif tag == "a":
            self.in_a = False
        elif tag == "dl" and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self.in_h3 and self.pending_folder is not None:
            self.pending_folder += data
        elif self.in_a and self.items:
            self.items[-1]["title"] += data
        elif self.in_dd and self.items:
            self.items[-1]["note"] += data


class RaindropImporter(Importer):
    kind = "raindrop"

    def entries(self) -> Iterator[Entry]:
        path = Path(self.source)
        root = unpack(path, self.workdir)
        files = [root] if root.is_file() else sorted(
            p for p in root.rglob("*") if p.suffix.lower() in (".csv", ".html", ".htm"))
        if not files:
            raise ImportError_("no Raindrop CSV or HTML export there")
        for f in files:
            text = f.read_text(encoding="utf-8-sig", errors="replace")
            rows = self._csv(text) if f.suffix.lower() == ".csv" else self._html(text)
            for row in rows:
                yield self._entry(row)

    def _csv(self, text: str) -> list[dict]:
        out = []
        for r in csv.DictReader(io.StringIO(text)):
            r = {str(k).strip().lower(): (v or "") for k, v in r.items() if k}
            folder = [p.strip() for p in re.split(r"\s*/\s*", r.get("folder", "")) if p.strip()]
            out.append({"id": r.get("id"), "url": r.get("url"), "title": r.get("title", ""),
                        "note": "\n\n".join(x for x in (r.get("note"), r.get("excerpt")) if x),
                        "tags": r.get("tags", ""), "created": r.get("created"),
                        "cover": r.get("cover"), "folder": folder,
                        "favorite": r.get("favorite", "").lower() in ("true", "1", "yes")})
        return out

    def _html(self, text: str) -> list[dict]:
        p = _Bookmarks()
        p.feed(text)
        return p.items

    def _entry(self, row: dict) -> Entry:
        page = safe_url(row.get("url"))
        cover = safe_url(row.get("cover"))
        key = str(row.get("id") or page or row.get("title") or "?")
        tags = clean_tags(row.get("tags") or "")
        if row.get("favorite"):
            tags.append("favorite")
        notes = "\n\n".join(x.strip() for x in (row.get("title"), row.get("note"))
                            if x and x.strip())
        entry = Entry(key=key, url=cover or page, notes=notes, tags=tags,
                      folders=[row["folder"]] if row.get("folder") else [],
                      created_at=parse_time(row.get("created")),
                      provenance={"page_url": page, "image_url": cover})
        if not entry.url:
            entry.error = "no URL"
        elif urlsplit(entry.url).scheme not in ("http", "https"):
            entry.error = "not a web address"
        return entry
