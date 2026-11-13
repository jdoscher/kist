"""Share links: public, read-only views of a folder, smart folder, board or
a hand-picked set of items, at /s/<slug>. Kist's first surface that needs
no token, so everything here fails closed:

- the slug is 128 random bits; a share only ever serves the items it
  covers (checked per request, so a smart-folder share stays live and an
  item filed out of a shared folder stops being served);
- trashed items are never served; originals only with allow_original;
- a password share keeps a signed, HttpOnly cookie scoped to /s/<slug>
  after the right password; wrong passwords are rate-limited per link;
- an expired or revoked share answers 410;
- views are counted (view_count, last_viewed_at) without storing who.

library/.kist/shares.json is the durable copy (`kist rebuild-index`
restores it). The password is kept only as a scrypt hash.
"""

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..config import get_settings
from ..ids import new_ulid
from ..models import Item, Share
from . import meta as meta_svc
from . import selection, validation

log = logging.getLogger("kist.shares")

FILE = "shares.json"
TARGET_KINDS = ("folder", "smart", "board", "items")
SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
ITEM_ID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
TITLE_MAX = 200
PASSWORD_MAX = 200
MAX_ITEMS = 1000  # an items share lists at most this many
DEFAULT_DAYS = 30
COOKIE_HOURS = 12
ATTEMPT_WINDOW = 15 * 60
# view counters reach shares.json at most this often
COUNTER_SYNC_SECONDS = 300


class NotFound(Exception):
    def __init__(self, what: str):
        super().__init__(f"share {what} not found")


class Gone(Exception):
    """Expired or revoked (HTTP 410)."""


# ---------- passwords ----------

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **_SCRYPT)
    salt_b64, digest_b64 = base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt_b64}${digest_b64}"


def check_password(stored: str | None, password: str) -> bool:
    if not stored or not password or len(password) > PASSWORD_MAX:
        return False
    try:
        _, n, r, p, salt, digest = stored.split("$")
        want = base64.b64decode(digest)
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt),
                             n=int(n), r=int(r), p=int(p), dklen=len(want))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


# ---------- the visitor's cookie ----------


def _cookie_key() -> bytes:
    # tied to the owner token: rotating KIST_TOKEN signs every visitor out
    return hashlib.sha256(b"kist-share-cookie\0" + get_settings().token.encode()).digest()


def cookie_name(slug: str) -> str:
    return "kist_share"


def make_cookie(share: Share) -> tuple[str, int]:
    """(value, max_age seconds) for a visitor who gave the right password.
    The signature covers the password hash, so changing the password signs
    everyone out."""
    max_age = COOKIE_HOURS * 3600
    if share.expires_at is not None:
        left = int((share.expires_at - datetime.now(UTC)).total_seconds())
        max_age = max(1, min(max_age, left))
    until = int(time.time()) + max_age
    msg = f"{share.slug}|{until}|{share.password_hash}".encode()
    sig = hmac.new(_cookie_key(), msg, hashlib.sha256).hexdigest()
    return f"{until}.{sig}", max_age


def cookie_valid(share: Share, value: str | None) -> bool:
    if not value or "." not in value or len(value) > 200:
        return False
    until_s, sig = value.split(".", 1)
    if not until_s.isdigit() or int(until_s) < time.time():
        return False
    msg = f"{share.slug}|{until_s}|{share.password_hash}".encode()
    want = hmac.new(_cookie_key(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, sig)


# ---------- password rate limit (in memory; nothing about the visitor kept) ----------

_attempts: dict[str, deque] = {}
_attempts_lock = threading.Lock()


def too_many_attempts(slug: str) -> int | None:
    """Seconds to wait when this link has had too many wrong passwords."""
    limit = max(1, get_settings().share_password_attempts)
    now = time.monotonic()
    with _attempts_lock:
        q = _attempts.get(slug)
        if not q:
            return None
        while q and now - q[0] > ATTEMPT_WINDOW:
            q.popleft()
        if len(q) >= limit:
            return int(ATTEMPT_WINDOW - (now - q[0])) + 1
    return None


def note_failure(slug: str) -> None:
    with _attempts_lock:
        _attempts.setdefault(slug, deque()).append(time.monotonic())
        if len(_attempts) > 10_000:  # bounded memory under a slug-spraying attack
            for key in list(_attempts)[:5000]:
                _attempts.pop(key, None)


def reset_attempts() -> None:
    with _attempts_lock:
        _attempts.clear()


# ---------- state ----------


def status(share: Share, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if share.revoked_at is not None:
        return "revoked"
    if share.expires_at is not None and share.expires_at <= now:
        return "expired"
    return "active"


def share_out(share: Share) -> dict:
    base = get_settings().public_url.rstrip("/")
    return {
        "id": share.id,
        "slug": share.slug,
        "url": f"{base}/s/{share.slug}",
        "target_kind": share.target_kind,
        "target_id": share.target_id,
        "item_ids": list(share.item_ids or []) if share.target_kind == "items" else None,
        "title": share.title,
        "has_password": bool(share.password_hash),
        "expires_at": share.expires_at,
        "allow_download": share.allow_download,
        "allow_original": share.allow_original,
        "created_by": share.created_by,
        "created_at": share.created_at,
        "revoked_at": share.revoked_at,
        "view_count": share.view_count or 0,
        "last_viewed_at": share.last_viewed_at,
        "status": status(share),
    }


def _check_target(session: Session, kind: str, target_id: str | None,
                  item_ids: list[str] | None) -> tuple[str | None, list[str] | None]:
    if kind not in TARGET_KINDS:
        raise validation.ValidationError(
            f"target_kind must be one of {', '.join(TARGET_KINDS)}"
        )
    return selection.check(session, kind, target_id, item_ids, max_items=MAX_ITEMS,
                           allow_all=False)


def _clean_title(title) -> str:
    title = " ".join(str(title or "").split())
    if len(title) > TITLE_MAX:
        raise validation.ValidationError(f"titles are at most {TITLE_MAX} characters")
    return title


def _clean_password(password) -> str | None:
    if password is None or password == "":
        return None
    password = str(password)
    if len(password) < 4 or len(password) > PASSWORD_MAX:
        raise validation.ValidationError(f"passwords are 4 to {PASSWORD_MAX} characters")
    return hash_password(password)


def _clean_expiry(expires_at: datetime | None) -> datetime | None:
    if expires_at is None:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        raise validation.ValidationError("expires_at is in the past")
    return expires_at


def create(
    session: Session,
    *,
    target_kind: str,
    target_id: str | None = None,
    item_ids: list[str] | None = None,
    title: str = "",
    password: str | None = None,
    expires_at: datetime | None = None,
    expires_in_days: float | None = None,
    allow_download: bool = False,
    allow_original: bool = False,
    created_by: str = "owner",
) -> Share:
    """expires_at wins; else expires_in_days (default 30; 0 = never)."""
    target_id, item_ids = _check_target(session, target_kind, target_id, item_ids)
    if expires_at is None:
        days = DEFAULT_DAYS if expires_in_days is None else float(expires_in_days)
        if days < 0:
            raise validation.ValidationError("expires_in_days can't be negative")
        expires_at = datetime.now(UTC) + timedelta(days=days) if days else None
    share = Share(
        id=new_ulid(),
        slug=secrets.token_urlsafe(16),
        target_kind=target_kind,
        target_id=target_id,
        item_ids=item_ids,
        title=_clean_title(title),
        password_hash=_clean_password(password),
        expires_at=_clean_expiry(expires_at),
        allow_download=bool(allow_download),
        allow_original=bool(allow_original) and bool(allow_download),
        created_by=created_by,
        created_at=datetime.now(UTC),
        view_count=0,
    )
    session.add(share)
    session.commit()
    sync_file(session)
    return share


def list_shares(session: Session) -> list[Share]:
    return list(session.execute(sa.select(Share).order_by(Share.created_at.desc())).scalars())


def get(session: Session, share_id: str) -> Share:
    share = session.get(Share, share_id)
    if share is None:
        raise NotFound(share_id)
    return share


def update(session: Session, share_id: str, fields: dict) -> Share:
    share = get(session, share_id)
    if "title" in fields and fields["title"] is not None:
        share.title = _clean_title(fields["title"])
    if "password" in fields:
        share.password_hash = _clean_password(fields["password"])
    if "expires_at" in fields:
        share.expires_at = _clean_expiry(fields["expires_at"])
    if fields.get("allow_download") is not None:
        share.allow_download = bool(fields["allow_download"])
    if fields.get("allow_original") is not None:
        share.allow_original = bool(fields["allow_original"])
    share.allow_original = share.allow_original and share.allow_download
    if fields.get("item_ids") is not None and share.target_kind == "items":
        _, share.item_ids = _check_target(session, "items", None, fields["item_ids"])
    if fields.get("revoked"):
        share.revoked_at = share.revoked_at or datetime.now(UTC)
    session.commit()
    sync_file(session)
    return share


def revoke(session: Session, share_id: str) -> Share:
    return update(session, share_id, {"revoked": True})


def delete(session: Session, share_id: str) -> None:
    share = get(session, share_id)
    session.delete(share)
    session.commit()
    sync_file(session)
    from . import proofing  # F3: its guest feedback goes with it

    proofing.on_share_deleted(share_id)


# ---------- the public side ----------


def by_slug(session: Session, slug: str) -> Share:
    """The share for a slug: NotFound, or Gone when expired or revoked."""
    if not SLUG_RE.match(slug or ""):
        raise NotFound("?")
    share = session.execute(sa.select(Share).where(Share.slug == slug)).scalar_one_or_none()
    if share is None:
        raise NotFound("?")
    if status(share) != "active":
        raise Gone(status(share))
    return share


def _filters(session: Session, share: Share) -> list:
    """SQL conditions for the items a share covers (always live ones)."""
    return selection.filters(session, share.target_kind, share.target_id, share.item_ids)


def items(session: Session, share: Share, limit: int = 500, offset: int = 0) -> list[Item]:
    return selection.items(session, share.target_kind, share.target_id, share.item_ids,
                           limit=limit, offset=offset)


def count(session: Session, share: Share) -> int:
    return selection.count(session, share.target_kind, share.target_id, share.item_ids)


def item_in_share(session: Session, share: Share, item_id: str) -> Item | None:
    """The item, if this share covers it right now; None otherwise (the
    caller answers 404 either way, so a guessed id learns nothing)."""
    if not ITEM_ID_RE.match(item_id or ""):
        return None
    return session.execute(
        sa.select(Item).where(Item.id == item_id, *_filters(session, share))
    ).scalar_one_or_none()


_last_counter_sync = 0.0


def record_view(session: Session, share: Share) -> None:
    """Count a gallery view. Nothing about the visitor is kept."""
    global _last_counter_sync
    session.execute(
        sa.update(Share).where(Share.id == share.id).values(
            view_count=Share.view_count + 1, last_viewed_at=datetime.now(UTC)
        )
    )
    session.commit()
    log.info("share %s viewed", share.id)
    if time.monotonic() - _last_counter_sync > COUNTER_SYNC_SECONDS:
        _last_counter_sync = time.monotonic()
        sync_file(session)


# ---------- the durable copy ----------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def file_rows(session: Session) -> list[dict]:
    return [
        {
            "id": s.id, "slug": s.slug, "target_kind": s.target_kind,
            "target_id": s.target_id, "item_ids": list(s.item_ids) if s.item_ids else None,
            "title": s.title, "password_hash": s.password_hash,
            "expires_at": _iso(s.expires_at), "allow_download": s.allow_download,
            "allow_original": s.allow_original, "created_by": s.created_by,
            "created_at": _iso(s.created_at), "revoked_at": _iso(s.revoked_at),
            "view_count": s.view_count or 0, "last_viewed_at": _iso(s.last_viewed_at),
        }
        for s in session.execute(sa.select(Share).order_by(Share.id)).scalars()
    ]


def path():
    return get_settings().state_dir / FILE


def sync_file(session: Session) -> bool:
    try:
        meta_svc.advisory_lock(session, FILE)
        rows = file_rows(session)
        meta_svc.atomic_write_text(path(), json.dumps({"version": 1, "shares": rows}, indent=2))
        session.commit()
        return True
    except Exception:
        session.rollback()
        log.exception("shares.json not written (kist rewrite-meta writes it)")
        meta_svc.note_pending(FILE)
        return False


def _parse(value):
    return datetime.fromisoformat(value) if value else None


def restore(session: Session) -> tuple[int, str | None]:
    p = path()
    if not p.is_file():
        return 0, None
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))["shares"]
        restored = 0
        for r in rows:
            if session.get(Share, r["id"]) is not None:
                continue
            if session.execute(sa.select(Share.id).where(Share.slug == r["slug"])).first():
                continue
            if r.get("target_kind") not in TARGET_KINDS:
                continue
            session.add(Share(
                id=r["id"], slug=str(r["slug"]), target_kind=r["target_kind"],
                target_id=r.get("target_id"), item_ids=r.get("item_ids"),
                title=str(r.get("title") or ""), password_hash=r.get("password_hash"),
                expires_at=_parse(r.get("expires_at")),
                allow_download=bool(r.get("allow_download")),
                allow_original=bool(r.get("allow_original")),
                created_by=str(r.get("created_by") or "owner"),
                created_at=_parse(r.get("created_at")) or datetime.now(UTC),
                revoked_at=_parse(r.get("revoked_at")),
                view_count=int(r.get("view_count") or 0),
                last_viewed_at=_parse(r.get("last_viewed_at")),
            ))
            restored += 1
        session.commit()
        return restored, None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        session.rollback()
        return 0, str(exc)


COMPARED = ("slug", "target_kind", "target_id", "item_ids", "title", "password_hash",
            "allow_download", "allow_original")


def check_file(session: Session) -> list[str]:
    """verify: shares.json matches the database (view counters aside)."""
    rows = {r["id"]: r for r in file_rows(session)}
    p = path()
    if not p.is_file():
        return ["shares.json missing"] if rows else []
    try:
        disk = {r["id"]: r for r in json.loads(p.read_text(encoding="utf-8"))["shares"]}
    except (OSError, ValueError, KeyError, TypeError):
        return ["shares.json unreadable"]
    problems = []
    for sid in sorted(rows.keys() - disk.keys()):
        problems.append(f"shares.json: share {sid} missing")
    for sid in sorted(disk.keys() - rows.keys()):
        problems.append(f"shares.json: share {sid} is not in the database")
    for sid in sorted(rows.keys() & disk.keys()):
        a, b = rows[sid], disk[sid]
        same = all(a[k] == b.get(k) for k in COMPARED)
        same = same and all(_parse(a[k]) == _parse(b.get(k)) for k in ("expires_at", "revoked_at"))
        if not same:
            problems.append(f"shares.json: share {sid} differs")
    return problems
