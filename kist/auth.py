import logging
import secrets

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from . import access
from .config import get_settings

log = logging.getLogger("kist.auth")

PUBLIC_PATHS = {"/api/health"}
# FastAPI's interactive docs describe the whole API: token only. In a
# browser the kist_token cookie the web UI sets is enough (they are GETs).
DOCS_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}


def needs_token(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return False
    return path.startswith(("/api", "/mcp")) or path in DOCS_PATHS


def token_matches(given: str, expected: str) -> bool:
    """Constant-time compare that can't raise: compare_digest refuses
    non-ASCII str, which used to turn a bad token into a 500."""
    return secrets.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def resolve(token: str) -> access.Principal | None:
    """The principal a bearer token stands for, or None. KIST_TOKEN is the
    owner (admin); anything else is looked up among the named tokens."""
    if token_matches(token, get_settings().token):
        return access.OWNER
    if not token.startswith("kist_") or len(token) > 200:
        return None
    from .db import SessionLocal
    from .services import tokens as tokens_svc

    with SessionLocal() as session:
        return tokens_svc.authenticate(session, token)


def _json(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


class BearerTokenMiddleware:
    """Require a token on /api, /mcp and the API docs, and check its scope.

    The token is accepted via `Authorization: Bearer <token>` or (for
    GET/HEAD only) the `kist_token` cookie the web UI sets so <img>/<video>
    tags work. Keeping the cookie read-only means another site can't make
    state-changing requests that ride on it. There is deliberately no
    `?token=` form: query strings end up in access logs (Kist's and the
    reverse proxy's), which would leak the token.

    The caller (access.Principal) is set for the rest of the request:
    routes, services and MCP tools read it with access.current(). A scope
    that doesn't cover the request gets 403 "This token can't do that"; a
    folder-limited read token gets 404 for every path outside its view.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if not needs_token(path):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        token = ""
        via_cookie = False
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        if not token and scope["method"] in ("GET", "HEAD"):
            token = request.cookies.get("kist_token", "")
            via_cookie = bool(token)

        principal = None
        if token:
            try:
                principal = await run_in_threadpool(resolve, token)
            except Exception:
                log.exception("token lookup failed")
                await _json(503, "Kist can't check that token right now")(scope, receive, send)
                return
        if principal is None:
            await _json(401, "invalid or missing token")(scope, receive, send)
            return

        query = scope.get("query_string", b"").decode("latin-1")
        denied = access.request_allowed(principal, scope["method"], path, query)
        if path in DOCS_PATHS and not principal.can(access.Action.READ):
            denied = 403
        if denied == 404:
            await _json(404, "Not Found")(scope, receive, send)
            return
        if denied:
            await _json(403, access.FORBIDDEN_MESSAGE)(scope, receive, send)
            return

        scope.setdefault("state", {})["principal"] = principal
        scope["state"]["via_cookie"] = via_cookie
        # item history: who did it, from where, and in which batch (one per
        # request unless the client groups several with X-Kist-Batch)
        from .services import events

        web = via_cookie or request.headers.get("x-kist-client", "").lower() == "web"
        batch = events.clean_batch_id(request.headers.get("x-kist-batch"))
        with access.acting_as(principal), events.acting(
            principal.name, "web" if web else "api", batch
        ):
            await self.app(scope, receive, send)
