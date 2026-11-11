"""Link-rot watch: is the page each item came from still there?

Off by default (KIST_LINK_CHECK_DAYS=0): once Kist has its copy, it leaves
the source site alone. With KIST_LINK_CHECK_DAYS set, the worker re-checks
every item's page_url (source_url) about that often, a small batch at a time,
one request every KIST_LINK_CHECK_DELAY_SECONDS and never the same host
twice within 10 seconds. Requests go through the SSRF guard. Each check
records items.source_status and source_checked_at (kept in meta.json):

    ok      the page answers
    moved   it answers, but only after redirecting to another address
    gone    404/410, or the host name no longer exists
    error   anything else (5xx, timeouts, refused, blocked addresses);
            usually temporary, so it is retried on the next round

With KIST_WAYBACK=1 (off by default: it tells archive.org which pages you
kept) a gone or failing source is looked up in the Wayback Machine's
availability API and the archived copy's URL is kept as
source_wayback_url. The result is informational: the item's info panel
and GET /api/items?source_status=gone (/api/stats still counts source_gone),
never "Needs attention" in the web app, because the copy is what matters.
"""

import logging
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlsplit

import httpx
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Item
from . import meta as meta_svc
from . import netguard

log = logging.getLogger("kist.linkrot")

BATCH = 50
HOST_GAP_SECONDS = 10.0
WAYBACK_API = "https://archive.org/wayback/available?url="
STATUSES = ("ok", "moved", "gone", "error")


def _headers() -> dict:
    from .ingest import DEFAULT_USER_AGENT

    return {"User-Agent": get_settings().user_agent or DEFAULT_USER_AGENT,
            "Accept": "text/html,*/*;q=0.5"}


def _same_place(a: str, b: str) -> bool:
    pa, pb = urlsplit(a), urlsplit(b)
    path_a, path_b = pa.path.rstrip("/") or "/", pb.path.rstrip("/") or "/"
    host_a = (pa.hostname or "").removeprefix("www.")
    host_b = (pb.hostname or "").removeprefix("www.")
    return host_a == host_b and path_a == path_b


def check_url(url: str, *, transport=None) -> tuple[str, str | None]:
    """(status, moved_to) for one URL. Only the headers are read."""
    try:
        with netguard.guarded_get(url, _headers(), transport=transport) as (resp, final, _):
            if not _same_place(url, final):
                return "moved", final
            return "ok", None
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (404, 410):
            return "gone", None
        return "error", None
    except httpx.ConnectError as exc:
        # netguard raises ConnectError("could not resolve ...") for a dead domain
        if "resolve" in str(exc):
            return "gone", None
        return "error", None
    except (httpx.HTTPError, netguard.BlockedURLError, netguard.DownloadTimeout, ValueError):
        return "error", None


def wayback_lookup(url: str, *, transport=None) -> str | None:
    """The closest archived copy of url, from the Wayback Machine's
    availability API (KIST_WAYBACK=1 only)."""
    if not get_settings().wayback:
        return None
    import json

    try:
        with netguard.guarded_get(WAYBACK_API + quote(url, safe=""), _headers(),
                                  transport=transport) as (resp, _final, deadline):
            body = b""
            for chunk in resp.iter_bytes(65536):
                body += chunk
                if len(body) > 1_000_000 or time.monotonic() > deadline:
                    return None
        data = json.loads(body)
        closest = (data.get("archived_snapshots") or {}).get("closest") or {}
        if closest.get("available") and str(closest.get("url", "")).startswith(
            ("http://", "https://")
        ):
            return str(closest["url"]).replace("http://", "https://", 1)
    except (httpx.HTTPError, netguard.BlockedURLError, netguard.DownloadTimeout, ValueError):
        return None
    return None


def due_ids(session: Session, limit: int = BATCH) -> list[str]:
    days = get_settings().link_check_days
    if days <= 0:
        return []
    cutoff = datetime.now(UTC) - timedelta(days=days)
    return list(session.execute(
        sa.select(Item.id).where(
            Item.deleted_at.is_(None),
            Item.source_url.isnot(None),
            sa.or_(Item.source_checked_at.is_(None), Item.source_checked_at < cutoff),
        ).order_by(Item.source_checked_at.asc().nulls_first(), Item.id).limit(limit)
    ).scalars())


def record(session: Session, item_id: str, status: str, wayback: str | None = None) -> None:
    """Save a check's result. Not an edit (no history event, modified_at
    stays): it is what Kist observed, written to meta.json all the same."""
    values = {"source_status": status, "source_checked_at": datetime.now(UTC),
              "modified_at": Item.modified_at}
    if wayback:
        values["source_wayback_url"] = wayback
    session.execute(sa.update(Item).where(Item.id == item_id).values(**values))
    session.commit()
    meta_svc.sync_item(session, item_id)


def run_batch(session: Session, *, limit: int = BATCH, transport=None, sleep=time.sleep,
              wayback_transport=None) -> dict:
    """Check up to `limit` due sources, politely. Returns counts."""
    settings = get_settings()
    counts = {s: 0 for s in STATUSES}
    last_host: dict[str, float] = {}
    for n, item_id in enumerate(due_ids(session, limit)):
        item = session.get(Item, item_id)
        if item is None or not item.source_url:
            continue
        url = item.source_url
        host = urlsplit(url).hostname or ""
        wait = HOST_GAP_SECONDS - (time.monotonic() - last_host.get(host, -1e9))
        if n:
            wait = max(wait, settings.link_check_delay_seconds)
        if wait > 0 and n:
            sleep(wait)
        status, _moved = check_url(url, transport=transport)
        last_host[host] = time.monotonic()
        wayback = None
        if status in ("gone", "error") and settings.wayback and not item.source_wayback_url:
            wayback = wayback_lookup(url, transport=wayback_transport or transport)
        record(session, item_id, status, wayback)
        counts[status] += 1
    if any(counts.values()):
        log.info("link check: %s", ", ".join(f"{k} {v}" for k, v in counts.items() if v))
    return counts


def gone_count(session: Session) -> int:
    return session.execute(
        sa.select(sa.func.count()).select_from(Item).where(
            Item.deleted_at.is_(None), Item.source_status == "gone")
    ).scalar_one()
