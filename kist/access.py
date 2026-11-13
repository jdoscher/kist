"""Who is calling, and what they may do.

Every request to /api or /mcp is made by a Principal: the owner (KIST_TOKEN,
scope admin) or a named token from the api_tokens table. Scopes grant
actions; `request_allowed` maps an HTTP request to them (BearerTokenMiddleware
asks it before any route runs), MCP tools declare the action they need, and
services read `current()` for the few rules that depend on the caller (a
read token limited to some folders sees only their items).

No principal at all (the worker, the CLI) means the system itself: no limits.
"""

import contextlib
import contextvars
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Scope(StrEnum):
    ADMIN = "admin"  # everything, including token management
    WRITE = "write"  # all REST edits, hard delete from the trash; no tokens
    AGENT = "agent"  # what MCP allows: edits, never a hard delete; no tokens
    ADD = "add"  # POST /api/items and GET /api/me only
    READ = "read"  # GETs only (optionally only some folders' items)
    HELPER = "helper"  # the AI job claim/report endpoints only


class Action(StrEnum):
    READ = "read"
    EDIT = "edit"
    ADD = "add"
    HARD_DELETE = "hard_delete"
    UNPROTECT = "unprotect"
    TOKENS = "tokens"
    HELPER = "helper"


GRANTS: dict[Scope, frozenset[Action]] = {
    Scope.ADMIN: frozenset(Action),
    Scope.WRITE: frozenset(
        {Action.READ, Action.EDIT, Action.ADD, Action.HARD_DELETE, Action.UNPROTECT}
    ),
    Scope.AGENT: frozenset({Action.READ, Action.EDIT, Action.ADD}),
    Scope.ADD: frozenset({Action.ADD}),
    Scope.READ: frozenset({Action.READ}),
    Scope.HELPER: frozenset({Action.HELPER}),
}

FORBIDDEN_MESSAGE = "This token can't do that"
OWNER_NAME = "owner"


class Forbidden(Exception):
    """The caller's token lacks the scope (HTTP 403)."""

    def __init__(self, message: str = FORBIDDEN_MESSAGE):
        super().__init__(message)


@dataclass(frozen=True)
class Principal:
    name: str
    scopes: frozenset[str]
    # read scope only: the folders (subfolders included) whose items it sees
    folder_ids: tuple[str, ...] = ()
    token_id: str | None = None
    expires_at: datetime | None = None
    # folder_ids expanded to every subfolder when the token was checked
    visible_folders: frozenset[str] = field(default_factory=frozenset)

    def can(self, action: Action) -> bool:
        return any(action in GRANTS.get(Scope(s), ()) for s in self.scopes if s in Scope)

    @property
    def restricted(self) -> bool:
        """A folder-limited reader: sees only live items in its folders."""
        return bool(self.folder_ids)


OWNER = Principal(OWNER_NAME, frozenset({Scope.ADMIN}))

_current: contextvars.ContextVar[Principal | None] = contextvars.ContextVar(
    "kist_principal", default=None
)


def current() -> Principal | None:
    return _current.get()


@contextlib.contextmanager
def acting_as(principal: Principal | None):
    token = _current.set(principal)
    try:
        yield principal
    finally:
        _current.reset(token)


def require(action: Action) -> None:
    """Raise Forbidden unless the caller (if any) may do `action`."""
    p = current()
    if p is not None and not p.can(action):
        raise Forbidden()


def restricted() -> Principal | None:
    """The caller, if it is a folder-limited reader."""
    p = current()
    return p if p is not None and p.restricted else None


def visible_folder_filter(folder_ids):
    """Restrict an iterable of folder ids to what the caller may see."""
    p = restricted()
    if p is None:
        return list(folder_ids)
    return [f for f in folder_ids if f in p.visible_folders]


# ---------- HTTP requests -> actions ----------

_ITEM = r"/api/items/[^/]+"
# the only paths a folder-limited reader can reach; anything else is a 404
RESTRICTED_PATHS = [
    re.compile(p)
    for p in (
        r"^/api/me$",
        r"^/api/version$",
        r"^/api/items$",
        rf"^{_ITEM}$",
        rf"^{_ITEM}/(file|thumb|history|links|palette|faces)$",
        r"^/api/folders$",
        r"^/api/search/image$",
        # filtered to the reader's items (services/stacks.py, faces.py)
        r"^/api/people$",
        r"^/api/stacks/(?!suggestions$)[^/]+$",
        r"^/api/faces/[^/]+/thumb$",
        # Milestone 3: filtered to what the reader sees (services/boards.visible)
        rf"^{_ITEM}/(annotations|versions|boards)$",
        r"^/api/boards$",
        r"^/api/boards/[^/]+(/(thumb|export))?$",
        # Milestone 5 reads (POSTs still need an edit scope)
        rf"^{_ITEM}/(scrub|scrub\.json|stream|waveform|svg|model|font-info|glyphs|bundle)$",
        rf"^{_ITEM}/bundle/.+$",
        r"^/api/(timeline|on-this-day|map|map/config|map/basemap\.pmtiles)$",
        # 2.0 F2 Present: slides and the PDF of what the reader sees
        # (services/present.py filters every item through items.visible)
        r"^/api/present/(slides|pdf)$",
        r"^/api/shows$",
        r"^/api/shows/[^/]+(/(slides|pdf))?$",
    )
]
HELPER_PREFIX = "/api/ai/jobs"
TOKENS_PREFIX = "/api/tokens"
# harmless for any caller: who am I, and the web UI's crash reports
ANYONE = {"/api/me", "/api/client-errors"}
# POSTs that only read (a search with an uploaded image): the read scope may
READ_POSTS = {"/api/search/image", "/api/present/slides", "/api/present/pdf"}


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _scope_allows(scope: str, method: str, path: str, hard: bool) -> bool:
    reading = method in ("GET", "HEAD")
    if scope == Scope.ADMIN:
        return True
    if scope == Scope.HELPER:
        return _under(path, HELPER_PREFIX)
    if scope == Scope.ADD:
        # and a page snapshot for an item it just added (api/m4.save_snapshot
        # checks it is the token's own item, under 10 minutes old)
        return method == "POST" and (path == "/api/items"
                                     or bool(re.match(rf"^{_ITEM}/snapshot$", path)))
    if _under(path, TOKENS_PREFIX) or _under(path, HELPER_PREFIX):
        return False
    if _under(path, "/mcp"):
        return scope in (Scope.WRITE, Scope.AGENT, Scope.READ)
    if scope == Scope.READ:
        return reading or (method == "POST" and path in READ_POSTS)
    if scope == Scope.AGENT and method == "DELETE" and hard and re.match(rf"^{_ITEM}$", path):
        return False
    return scope in (Scope.WRITE, Scope.AGENT)


def request_allowed(principal: Principal, method: str, path: str, query: str) -> int | None:
    """None if the request may go ahead, else the HTTP status to answer:
    404 for a path a folder-limited reader can't see, 403 for a missing scope."""
    if path in ANYONE:
        return None
    if principal.restricted and not any(p.match(path) for p in RESTRICTED_PATHS):
        if not _under(path, "/mcp"):
            return 404
    hard = bool(re.search(r"(^|&)hard=(true|1|yes|on|t|y)(&|$)", query, re.IGNORECASE))
    if any(_scope_allows(s, method, path, hard) for s in principal.scopes):
        return None
    return 403
