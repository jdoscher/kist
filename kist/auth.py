import secrets

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import get_settings

PUBLIC_PATHS = {"/api/health"}


class BearerTokenMiddleware:
    """Require the shared bearer token on /api and /mcp routes.

    The token is accepted via `Authorization: Bearer <token>`, a
    `token` query parameter (curl convenience), or — for GET/HEAD only — the
    `kist_token` cookie the web UI sets so <img>/<video> tags work. Keeping
    the cookie read-only means another site can't make state-changing
    requests that ride on it.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if not (path.startswith("/api") or path.startswith("/mcp")) or path in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        token = ""
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        if not token and scope["method"] in ("GET", "HEAD"):
            token = request.cookies.get("kist_token", "")
        if not token:
            token = request.query_params.get("token", "")

        if not token or not secrets.compare_digest(token, get_settings().token):
            response = JSONResponse({"detail": "invalid or missing token"}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
