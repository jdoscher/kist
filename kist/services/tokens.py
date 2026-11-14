"""Named, scoped API tokens (access.Scope).

The plaintext is shown once, when a token is made; only its SHA-256 is
stored, in Postgres and in library/.kist/tokens.json. That file is the
durable copy: `kist rebuild-index` restores every token from it, so a
database loss doesn't sign out the phone Shortcut or the agents.
"""

import hashlib
import json
import logging
import secrets
import time
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..access import OWNER_NAME, Principal, Scope
from ..config import get_settings
from ..ids import new_ulid
from ..models import ApiToken, Folder
from . import meta as meta_svc
from . import validation

log = logging.getLogger("kist.tokens")

FILE = "tokens.json"
NAME_MAX = 100
PREFIX = "kist_"
# last_used_at is written at most this often per token
TOUCH_EVERY = timedelta(seconds=60)


class NotFound(Exception):
    def __init__(self, token_id: str):
        super().__init__(f"token {token_id} not found")


class StillActive(Exception):
    """Only a revoked or expired token's entry can be removed."""

    def __init__(self, name: str):
        super().__init__(f"“{name}” still works: revoke it before removing it from the list")


def digest(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def is_active(token: ApiToken, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return token.revoked_at is None and (token.expires_at is None or token.expires_at > now)


def token_out(token: ApiToken) -> dict:
    return {
        "id": token.id,
        "name": token.name,
        "scopes": list(token.scopes),
        "folder_ids": list(token.folder_ids or []),
        "created_at": token.created_at,
        "expires_at": token.expires_at,
        "last_used_at": token.last_used_at,
        "revoked_at": token.revoked_at,
        "active": is_active(token),
    }


def _clean_scopes(scopes: list[str]) -> list[str]:
    out: list[str] = []
    for s in scopes or []:
        s = str(s).strip().lower()
        if s not in tuple(Scope):
            raise validation.ValidationError(
                f"unknown scope {s!r}; expected some of {', '.join(Scope)}"
            )
        if s not in out:
            out.append(s)
    if not out:
        raise validation.ValidationError("a token needs at least one scope")
    return out


def create(
    session: Session,
    name: str,
    scopes: list[str],
    folder_ids: list[str] | None = None,
    expires_at: datetime | None = None,
) -> tuple[ApiToken, str]:
    """Make a token. Returns (row, plaintext); the plaintext is never stored."""
    name = " ".join(str(name or "").split())
    if not name or len(name) > NAME_MAX:
        raise validation.ValidationError(f"token names are 1 to {NAME_MAX} characters")
    if name.lower() in (OWNER_NAME, "cli", "worker", "importer"):
        raise validation.ValidationError(f"{name!r} is reserved; pick another name")
    scopes = _clean_scopes(scopes)
    folder_ids = list(dict.fromkeys(f for f in folder_ids or [] if f))
    if folder_ids:
        if Scope.READ not in scopes or not set(scopes) <= {Scope.READ, Scope.ADD}:
            raise validation.ValidationError(
                "folder_ids limit what a read token sees; use them with the read "
                "scope (and optionally add) only"
            )
        for fid in folder_ids:
            if session.get(Folder, fid) is None:
                raise validation.UnknownFolder(fid)
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise validation.ValidationError("expires_at is in the past")
    meta_svc.advisory_lock(session, FILE)
    taken = session.execute(
        sa.select(ApiToken).where(sa.func.lower(ApiToken.name) == name.lower())
    ).scalars().all()
    if any(is_active(t) for t in taken):
        raise validation.ValidationError(f"a token named {name!r} already exists")
    plaintext = PREFIX + secrets.token_urlsafe(32)
    token = ApiToken(
        id=new_ulid(),
        name=name,
        token_sha256=digest(plaintext),
        scopes=scopes,
        folder_ids=folder_ids,
        created_at=datetime.now(UTC),
        expires_at=expires_at,
    )
    session.add(token)
    session.commit()
    forget_cached()
    sync_file(session)
    return token, plaintext


def list_tokens(session: Session) -> list[ApiToken]:
    return list(session.execute(sa.select(ApiToken).order_by(ApiToken.created_at)).scalars())


def revoke(session: Session, token_id: str) -> ApiToken:
    token = session.get(ApiToken, token_id)
    if token is None:
        raise NotFound(token_id)
    if token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
        session.commit()
        forget_cached()
        sync_file(session)
    return token


def remove(session: Session, token_id: str) -> None:
    """Delete a revoked or expired token's entry, here and in tokens.json
    (so rebuild-index doesn't bring it back). Changes it made keep its name
    in item history, which stores the name, not the token."""
    token = session.get(ApiToken, token_id)
    if token is None:
        raise NotFound(token_id)
    if is_active(token):
        raise StillActive(token.name)
    session.delete(token)
    session.commit()
    forget_cached()
    sync_file(session)


def visible_folders(session: Session, folder_ids: list[str]) -> frozenset[str]:
    """folder_ids plus every folder under them."""
    if not folder_ids:
        return frozenset()
    tree = (
        sa.select(Folder.id).where(Folder.id.in_(folder_ids))
        .cte(name="visible", recursive=True)
    )
    tree = tree.union(sa.select(Folder.id).where(Folder.parent_id == tree.c.id))
    return frozenset(session.execute(sa.select(tree.c.id)).scalars())


# ---------- the in-process principal cache ----------
# A named token used to cost a database query on every request, so a grid
# of thumbnails paid one per image: 1,050 thumbnails/s with a named read
# token against 2,250 with the owner token (20k items, 30 connections,
# scripts/bench/thumbs.py, 2026-11-14); cached, both run at 2,250. The
# principal a token stands for is kept for CACHE_SECONDS, keyed by the
# token's SHA-256 (never the plaintext). Creating, revoking or removing a
# token, restoring tokens.json and every folder create/move/delete
# (visible_folders of a folder-limited reader) clear the cache at once in
# this process. Kist serves HTTP from one process (the worker serves none),
# so that is every request; another process that authenticated a token
# would see a change within CACHE_SECONDS. Only successful lookups are
# kept: a token made a moment ago works right away, and a wrong one is
# looked up every time. An entry never outlives the token's expires_at.
CACHE_SECONDS = 5.0
CACHE_MAX = 256
_cache: dict[str, tuple[float, Principal]] = {}


def forget_cached() -> None:
    """Drop every cached principal (tokens or folders changed)."""
    _cache.clear()


def cached_principal(plaintext: str) -> Principal | None:
    """The cached principal for a named token, or None (look it up)."""
    key = digest(plaintext)
    hit = _cache.get(key)
    if hit is None:
        return None
    until, principal = hit
    if time.monotonic() >= until or (
        principal.expires_at is not None and principal.expires_at <= datetime.now(UTC)
    ):
        _cache.pop(key, None)
        return None
    return principal


def _remember(plaintext: str, principal: Principal) -> None:
    if len(_cache) >= CACHE_MAX:
        _cache.clear()
    _cache[digest(plaintext)] = (time.monotonic() + CACHE_SECONDS, principal)


def authenticate(session: Session, plaintext: str) -> Principal | None:
    """The principal for a named token, or None if it is unknown, revoked
    or expired. Successful answers are cached briefly (see above)."""
    principal = _authenticate(session, plaintext)
    if principal is not None:
        _remember(plaintext, principal)
    return principal


def _authenticate(session: Session, plaintext: str) -> Principal | None:
    token = session.execute(
        sa.select(ApiToken).where(ApiToken.token_sha256 == digest(plaintext))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if token is None or not is_active(token, now):
        return None
    if token.last_used_at is None or now - token.last_used_at > TOUCH_EVERY:
        session.execute(
            sa.update(ApiToken).where(ApiToken.id == token.id).values(last_used_at=now)
        )
        session.commit()
    folder_ids = tuple(token.folder_ids or ())
    return Principal(
        name=token.name,
        scopes=frozenset(token.scopes),
        folder_ids=folder_ids,
        token_id=token.id,
        expires_at=token.expires_at,
        visible_folders=visible_folders(session, list(folder_ids)),
    )


# ---------- the durable copy ----------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def file_rows(session: Session) -> list[dict]:
    return [
        {
            "id": t.id,
            "name": t.name,
            "token_sha256": t.token_sha256,
            "scopes": list(t.scopes),
            "folder_ids": list(t.folder_ids or []),
            "created_at": _iso(t.created_at),
            "expires_at": _iso(t.expires_at),
            "revoked_at": _iso(t.revoked_at),
        }
        for t in list_tokens(session)
    ]


def path():
    return get_settings().state_dir / FILE


def sync_file(session: Session) -> bool:
    """Bring library/.kist/tokens.json up to the committed tokens."""
    try:
        meta_svc.advisory_lock(session, FILE)
        rows = file_rows(session)
        meta_svc.atomic_write_text(path(), json.dumps({"version": 1, "tokens": rows}, indent=2))
        session.commit()
        return True
    except Exception:
        session.rollback()
        log.exception("tokens.json not written (kist rewrite-meta writes it)")
        meta_svc.note_pending(FILE)
        return False


def _dt(value) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def restore(session: Session) -> tuple[int, str | None]:
    """Insert the tokens in tokens.json that the database lacks.
    Returns (restored, problem)."""
    p = path()
    if not p.is_file():
        return 0, None
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))["tokens"]
        restored = 0
        for r in rows:
            if session.get(ApiToken, r["id"]) is not None:
                continue
            if session.execute(
                sa.select(ApiToken.id).where(ApiToken.token_sha256 == r["token_sha256"])
            ).first():
                continue
            session.add(
                ApiToken(
                    id=r["id"],
                    name=str(r["name"]),
                    token_sha256=str(r["token_sha256"]),
                    scopes=[s for s in r.get("scopes", []) if s in tuple(Scope)],
                    folder_ids=list(r.get("folder_ids") or []),
                    created_at=_dt(r.get("created_at")) or datetime.now(UTC),
                    expires_at=_dt(r.get("expires_at")),
                    revoked_at=_dt(r.get("revoked_at")),
                )
            )
            restored += 1
        session.commit()
        forget_cached()
        return restored, None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        session.rollback()
        return 0, str(exc)


def check_file(session: Session) -> list[str]:
    """verify: does tokens.json match the database?"""
    rows = {r["id"]: r for r in file_rows(session)}
    p = path()
    if not p.is_file():
        return ["tokens.json missing"] if rows else []
    try:
        disk = {r["id"]: r for r in json.loads(p.read_text(encoding="utf-8"))["tokens"]}
    except (OSError, ValueError, KeyError, TypeError):
        return ["tokens.json unreadable"]
    problems = []
    for tid in sorted(rows.keys() - disk.keys()):
        problems.append(f"tokens.json: token {tid} ({rows[tid]['name']}) missing")
    for tid in sorted(disk.keys() - rows.keys()):
        problems.append(f"tokens.json: token {tid} is not in the database")
    for tid in sorted(rows.keys() & disk.keys()):
        a, b = rows[tid], disk[tid]
        same = all(a[k] == b.get(k) for k in ("name", "token_sha256", "scopes", "folder_ids"))
        same = same and all(_dt(a[k]) == _dt(b.get(k)) for k in ("expires_at", "revoked_at"))
        if not same:
            problems.append(f"tokens.json: token {tid} ({a['name']}) differs")
    return problems
