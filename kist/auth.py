import secrets

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import get_settings

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


class BearerTokenMiddleware:
    """Require the shared bearer token on /api, /mcp and the API docs.

    The token is accepted via `Authorization: Bearer <token>` or — for
    GET/HEAD only — the `kist_token` cookie the web UI sets so <img>/<video>
    tags work. Keeping the cookie read-only means another site can't make
    state-changing requests that ride on it. There is deliberately no
    `?token=` form: query strings end up in access logs (Kist's and the
    reverse proxy's), which would leak the token.
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
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        if not token and scope["method"] in ("GET", "HEAD"):
            token = request.cookies.get("kist_token", "")

        if not token or not token_matches(token, get_settings().token):
            response = JSONResponse({"detail": "invalid or missing token"}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
