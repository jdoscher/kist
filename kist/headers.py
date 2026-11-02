"""Browser security headers on every response.

Kist keeps its token where page script can read it (localStorage and the
cookie <img> tags use), so the main defence is never letting foreign script
run on this origin: a strict Content-Security-Policy, no framing by other
sites, and HSTS so the browser only ever talks to it over TLS.
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import get_settings

APP_CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        # React sets style attributes; harmless without script
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "frame-src 'self'",  # the PDF preview iframes /api/items/<id>/file
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'self'",
    ]
)

# FastAPI's interactive docs load Swagger/ReDoc from a CDN with inline script
DOCS_PATHS = ("/docs", "/redoc")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        hsts = get_settings().hsts_seconds

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "same-origin")
                headers.setdefault("X-Frame-Options", "SAMEORIGIN")
                headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                headers.setdefault(
                    "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
                )
                if hsts > 0:
                    headers.setdefault("Strict-Transport-Security", f"max-age={hsts}")
                if not path.startswith(DOCS_PATHS):
                    # responses that set their own (sandboxed originals) keep it
                    headers.setdefault("Content-Security-Policy", APP_CSP)
            await send(message)

        await self.app(scope, receive, send_with_headers)
