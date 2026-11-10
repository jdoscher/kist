"""Page snapshots: a copy of the page an item came from, kept beside it.

    POST /api/items/{id}/snapshot {html?}

With html, it is the self-contained page the browser extension captured
(SingleFile-style, so logged-in and paywalled pages work). Without, the
server fetches page_url once through the SSRF guard (only with
KIST_SNAPSHOTS=1). Either way scripts, event handlers and javascript: links
are stripped, and the item folder gains

    page/snapshot.html   the page
    page/text.txt        its readable text (search: item_text source "page")
    page/snapshot.json   when, how, from where, sizes

GET /api/items/{id}/snapshot serves the page under `CSP: sandbox`, so even
something the stripping missed can't run on Kist's origin.
"""

import json
import logging
import re
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Item, ItemText
from . import meta as meta_svc
from . import netguard, validation

log = logging.getLogger("kist.snapshots")

DIR = "page"
HTML_FILE = "snapshot.html"
TEXT_FILE = "text.txt"
INFO_FILE = "snapshot.json"
SOURCE = "page"
MAX_HTML_BYTES = 25 * 1024 * 1024
MAX_TEXT = 1_000_000
SNAPSHOT_CSP = (
    "sandbox; default-src 'none'; img-src data: blob:; media-src data:; "
    "style-src 'unsafe-inline' data:; font-src data:; frame-ancestors 'self'"
)


class SnapshotsOff(validation.ValidationError):
    pass


def page_dir(item_id: str) -> Path:
    return meta_svc.item_dir(item_id) / DIR


# ---------- cleaning ----------

_SCRIPT = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_SCRIPT_OPEN = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
_HANDLER = re.compile(r"""\son[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.IGNORECASE)
_JS_URL = re.compile(r"""(href|src|action|formaction|xlink:href)\s*=\s*(["']?)\s*javascript:""",
                     re.IGNORECASE)
_META_REFRESH = re.compile(r"<meta\b[^>]*http-equiv\s*=\s*[\"']?refresh[^>]*>", re.IGNORECASE)
_BASE = re.compile(r"<base\b[^>]*>", re.IGNORECASE)
_EMBEDS = re.compile(r"<(iframe|frame|object|embed)\b[^>]*>(.*?</\1\s*>)?",
                     re.IGNORECASE | re.DOTALL)


def strip_active(html: str) -> str:
    """Remove scripts, inline handlers, javascript: URLs, refreshes, <base>
    and embedded frames. Defence in depth: the page is also served
    sandboxed with no script allowed."""
    prev = None
    while prev != html:  # nested/overlapping tricks: repeat until stable
        prev = html
        html = _SCRIPT.sub("", html)
        html = _SCRIPT_OPEN.sub("", html)
        html = _HANDLER.sub(" ", html)
        html = _JS_URL.sub(r"\1=\2about:blank#", html)
        html = _META_REFRESH.sub("", html)
        html = _BASE.sub("", html)
        html = _EMBEDS.sub("", html)
    return html


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section",
             "article", "header", "footer", "blockquote", "pre", "figcaption", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if not self.skip:
            self.parts.append(data)


def readable_text(html: str) -> tuple[str, str]:
    """(title, text) with the standard library's parser (BeautifulSoup is
    used when it is installed)."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-not-found]

        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        for tag in soup(["script", "style", "noscript", "template", "svg", "head"]):
            tag.decompose()
        text = soup.get_text("\n")
    except ImportError:
        p = _Text()
        try:
            p.feed(html)
            p.close()
        except Exception:
            pass
        title, text = p.title, "".join(p.parts)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    out = "\n".join(line for line in lines if line)
    return " ".join(title.split()), out[:MAX_TEXT]


# ---------- saving ----------


def save(session: Session, item_id: str, html: str, *, source: str,
         url: str | None = None) -> dict:
    from . import items as items_svc

    item = items_svc.get_item(session, item_id, with_deleted=False)
    if len(html.encode("utf-8", errors="replace")) > MAX_HTML_BYTES:
        raise validation.ValidationError(
            f"snapshots are at most {MAX_HTML_BYTES // (1024 * 1024)} MB")
    clean = strip_active(html)
    title, text = readable_text(clean)
    d = page_dir(item.id)
    meta_svc.atomic_write_text(d / HTML_FILE, clean)
    meta_svc.atomic_write_text(d / TEXT_FILE, text)
    info = {
        "captured_at": datetime.now(UTC).isoformat(), "source": source,
        "url": url or item.source_url, "title": title,
        "html_bytes": len(clean.encode("utf-8")), "text_chars": len(text),
    }
    meta_svc.atomic_write_text(d / INFO_FILE, json.dumps(info, indent=2, ensure_ascii=False))
    _upsert_text(session, item.id, text)
    session.commit()
    return info


def _upsert_text(session: Session, item_id: str, text: str) -> None:
    session.execute(sa.delete(ItemText).where(ItemText.item_id == item_id,
                                              ItemText.source == SOURCE))
    if text.strip():
        session.add(ItemText(item_id=item_id, source=SOURCE, text=text.replace("\x00", ""),
                             lang=None, model="html", created_at=datetime.now(UTC)))


def fetch(session: Session, item_id: str, *, transport=None) -> dict:
    """Fetch the item's page_url once on the server (KIST_SNAPSHOTS=1)."""
    from . import items as items_svc
    from .ingest import DEFAULT_USER_AGENT

    settings = get_settings()
    if not settings.snapshots:
        raise SnapshotsOff(
            "server-side snapshots are off (KIST_SNAPSHOTS=1 turns them on); "
            "send the page's html instead")
    item = items_svc.get_item(session, item_id, with_deleted=False)
    if not item.source_url:
        raise validation.ValidationError("this item has no page_url to snapshot")
    headers = {"User-Agent": settings.user_agent or DEFAULT_USER_AGENT,
               "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"}
    chunks: list[bytes] = []
    received = 0
    with netguard.guarded_get(item.source_url, headers, transport=transport) as (
        resp, final_url, deadline
    ):
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype not in ("text/html", "application/xhtml+xml"):
            raise validation.ValidationError(f"page_url isn't a web page ({ctype or 'unknown'})")
        for chunk in resp.iter_bytes(256 * 1024):
            received += len(chunk)
            if received > MAX_HTML_BYTES:
                raise validation.ValidationError("that page is too large to snapshot")
            if time.monotonic() > deadline:
                raise netguard.DownloadTimeout("the page took too long")
            chunks.append(chunk)
        charset = resp.charset_encoding or "utf-8"
    html = b"".join(chunks).decode(charset, errors="replace")
    return save(session, item.id, html, source="server", url=final_url)


def read(item_id: str) -> tuple[Path, dict] | None:
    d = page_dir(item_id)
    if not (d / HTML_FILE).is_file():
        return None
    try:
        info = json.loads((d / INFO_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    return d / HTML_FILE, info


def text_of(item_id: str) -> str | None:
    try:
        return (page_dir(item_id) / TEXT_FILE).read_text(encoding="utf-8")
    except OSError:
        return None


# ---------- the durable copy ----------


def restore(session: Session) -> int:
    """rebuild-index: item_text rows (source page) from page/text.txt."""
    restored = 0
    have = set(session.execute(
        sa.select(ItemText.item_id).where(ItemText.source == SOURCE)).scalars())
    items_dir = get_settings().items_dir
    if not items_dir.is_dir():
        return 0
    for text_file in sorted(items_dir.glob(f"*/{DIR}/{TEXT_FILE}")):
        item_id = text_file.parent.parent.name
        if item_id in have or session.get(Item, item_id) is None:
            continue
        try:
            text = text_file.read_text(encoding="utf-8")[:MAX_TEXT]
        except OSError:
            continue
        _upsert_text(session, item_id, text)
        restored += 1
    session.commit()
    return restored


def check(session: Session) -> list[str]:
    """verify: page text rows and page/text.txt agree."""
    problems = []
    rows = set(session.execute(
        sa.select(ItemText.item_id).where(ItemText.source == SOURCE)).scalars())
    items_dir = get_settings().items_dir
    on_disk = set()
    if items_dir.is_dir():
        on_disk = {p.parent.parent.name for p in items_dir.glob(f"*/{DIR}/{TEXT_FILE}")
                   if p.stat().st_size > 0}
    for item_id in sorted(rows - on_disk):
        problems.append(f"{item_id}: page snapshot text has no page/{TEXT_FILE}")
    known = set(session.execute(sa.select(Item.id).where(Item.id.in_(sorted(on_disk))))
                .scalars()) if on_disk else set()
    for item_id in sorted((on_disk & known) - rows):
        problems.append(f"{item_id}: page/{TEXT_FILE} is not in the database "
                        "(kist rebuild-index loads it)")
    return problems
