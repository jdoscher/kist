"""Present: the phone remote's relay (Kist 2.0 F2).

The show window pairs a phone by QR code. Pairing makes a remote session
held in this process's memory (never on disk, never sent anywhere): an id,
a random token (only its SHA-256 is kept), the show's item ids, an expiry
(KIST_PRESENT_REMOTE_HOURS, default 4) and two small queues.

    show window (owner token)                  phone (remote token only)
    POST   /api/present/remote                 GET  /present/remote/<id>#<token>
    GET    /api/present/remote/<id>/poll  <--  POST /present-relay/<id>/cmd
    POST   /api/present/remote/<id>/state -->  GET  /present-relay/<id>/state
    DELETE /api/present/remote/<id>            GET  /present-relay/<id>/thumb/<item>

Both sides long-poll (no WebSocket, nothing outbound), so it works on the
LAN and through the reverse proxy. The token sits in the URL fragment, so
it never reaches a server or proxy log; the phone sends it in the
X-Kist-Remote header. A remote can do exactly four things: send next /
prev / pick / reject / first / last / goto / blank to its own show
window (pick and reject may name a compare slide's side), read the
state that window publishes (slide number, title, credit, notes), and
fetch thumbnails of that show's items. It never reads or writes the
library itself: a pick is applied by the show window, with the owner's
token, as an ordinary edit (in the item history, undoable). An unknown,
expired or revoked session is a plain 404.

Sessions live in one process: Kist runs one app process (uvicorn without
--workers). A restart ends every pairing; the show offers Pair again.
"""

import asyncio
import hashlib
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ..config import get_settings
from ..ids import new_ulid
from . import validation

COMMANDS = ("next", "prev", "pick", "reject", "first", "last", "goto", "blank")
MAX_SESSIONS = 20
MAX_EVENTS = 100
MAX_ITEMS = 2000
STATE_TEXT_MAX = 2000
# commands a phone may send: a burst, then this many a minute
CMD_BURST = 10
CMD_PER_MINUTE = 240
POLL_MAX_SECONDS = 25.0
POLL_STEP = 0.2
# a phone polled within this long: "Phone connected"
CONNECTED_SECONDS = 40.0


class NotFound(Exception):
    def __init__(self):
        super().__init__("remote not found")


class TooMany(Exception):
    pass


@dataclass
class Session:
    id: str
    token_hash: str
    name: str
    item_ids: frozenset[str]
    created_at: datetime
    expires_at: datetime
    owner: str
    to_show: deque = field(default_factory=lambda: deque(maxlen=MAX_EVENTS))
    seq: int = 0
    state: dict | None = None
    state_seq: int = 0
    remote_seen: float = 0.0
    cmd_times: deque = field(default_factory=lambda: deque(maxlen=CMD_PER_MINUTE))

    def live(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) < self.expires_at


_sessions: dict[str, Session] = {}
_lock = threading.Lock()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _prune() -> None:
    now = datetime.now(UTC)
    for sid in [s.id for s in _sessions.values() if not s.live(now)]:
        _sessions.pop(sid, None)


def reset() -> None:
    """Tests: forget every session."""
    with _lock:
        _sessions.clear()


def create(*, name: str, item_ids: list[str], owner: str) -> tuple[Session, str]:
    name = " ".join(str(name or "Show").split())[:120] or "Show"
    ids = list(dict.fromkeys(str(i) for i in item_ids or []))
    if len(ids) > MAX_ITEMS:
        raise validation.ValidationError(f"a remote covers at most {MAX_ITEMS} items")
    hours = max(0.05, float(get_settings().present_remote_hours))
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    s = Session(id=new_ulid(), token_hash=_hash(token), name=name, item_ids=frozenset(ids),
                created_at=now, expires_at=now + timedelta(hours=hours), owner=owner)
    with _lock:
        _prune()
        if len(_sessions) >= MAX_SESSIONS:
            # the oldest pairing makes room: shows from long ago aren't running
            oldest = min(_sessions.values(), key=lambda x: x.created_at)
            _sessions.pop(oldest.id, None)
        _sessions[s.id] = s
    return s, token


def get(session_id: str) -> Session:
    with _lock:
        s = _sessions.get(session_id)
        if s is None or not s.live():
            _sessions.pop(session_id, None)
            raise NotFound()
        return s


def check_token(session_id: str, token: str | None) -> Session:
    """The session a phone's token opens, or NotFound (also for a wrong
    token: a guess can't tell a real session from none)."""
    s = get(session_id)
    if not token or not secrets.compare_digest(_hash(token), s.token_hash):
        raise NotFound()
    s.remote_seen = time.monotonic()
    return s


def revoke(session_id: str) -> None:
    with _lock:
        if _sessions.pop(session_id, None) is None:
            raise NotFound()


def info(s: Session) -> dict:
    return {
        "id": s.id, "name": s.name, "created_at": s.created_at, "expires_at": s.expires_at,
        "connected": bool(s.remote_seen) and time.monotonic() - s.remote_seen < CONNECTED_SECONDS,
    }


# ---------- phone -> show window ----------


def send_command(s: Session, cmd: str, index: int | None = None) -> dict:
    if cmd not in COMMANDS:
        raise validation.ValidationError(f"cmd is one of {', '.join(COMMANDS)}")
    if index is not None and (not isinstance(index, int) or isinstance(index, bool)
                              or not 0 <= index <= 100_000):
        raise validation.ValidationError("index is a slide number from 0")
    if cmd == "goto" and index is None:
        raise validation.ValidationError("goto needs an index")
    if cmd in ("pick", "reject") and index is not None and index > 1:
        raise validation.ValidationError(
            "pick and reject take index 0 or 1 (a compare slide's side)")
    now = time.monotonic()
    with _lock:
        recent = [t for t in s.cmd_times if now - t < 1.0]
        if len(recent) >= CMD_BURST or (
                len(s.cmd_times) >= CMD_PER_MINUTE and now - s.cmd_times[0] < 60.0):
            raise TooMany()
        s.cmd_times.append(now)
        s.seq += 1
        event = {"seq": s.seq, "cmd": cmd}
        if index is not None and cmd in ("goto", "pick", "reject"):
            event["index"] = index
        s.to_show.append(event)
    return event


async def wait_commands(s: Session, after: int, wait: float) -> dict:
    """The show window's long poll: commands after `after`."""
    deadline = time.monotonic() + max(0.0, min(float(wait), POLL_MAX_SECONDS))
    while True:
        with _lock:
            events = [e for e in s.to_show if e["seq"] > after]
            seq = s.seq
        if events or time.monotonic() >= deadline or s.id not in _sessions:
            return {"seq": seq, "events": events, **info(s)}
        await asyncio.sleep(POLL_STEP)


# ---------- show window -> phone ----------

STATE_KEYS = {"index": int, "count": int, "item_id": str, "next_item_id": str, "title": str,
              "credit": str, "notes": str, "flag": str, "compare": bool, "blank": bool,
              "timer": str}


def set_state(s: Session, data: dict) -> dict:
    if not isinstance(data, dict):
        raise validation.ValidationError("state is an object")
    out: dict = {}
    for key, value in data.items():
        kind = STATE_KEYS.get(key)
        if kind is None:
            raise validation.ValidationError(f"unknown state field {key!r}")
        if value is None:
            out[key] = None
            continue
        if kind is int and (isinstance(value, bool) or not isinstance(value, int)):
            raise validation.ValidationError(f"{key} is a number")
        if kind is bool and not isinstance(value, bool):
            raise validation.ValidationError(f"{key} is true or false")
        if kind is str:
            if not isinstance(value, str):
                raise validation.ValidationError(f"{key} is text")
            value = value[:STATE_TEXT_MAX]
        out[key] = value
    for key in ("item_id", "next_item_id"):
        # the phone may only ever be pointed at this show's items
        if out.get(key) and out[key] not in s.item_ids:
            out[key] = None
    with _lock:
        s.state_seq += 1
        s.state = {**out, "seq": s.state_seq}
    return s.state


async def wait_state(s: Session, after: int, wait: float) -> dict:
    """The phone's long poll: the newest state once it's newer than `after`."""
    deadline = time.monotonic() + max(0.0, min(float(wait), POLL_MAX_SECONDS))
    while True:
        with _lock:
            state, seq = s.state, s.state_seq
        if seq > after or time.monotonic() >= deadline or s.id not in _sessions:
            return {"seq": seq, "state": state, "name": s.name,
                    "expires_at": s.expires_at.isoformat()}
        await asyncio.sleep(POLL_STEP)
