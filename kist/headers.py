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
        # WebAssembly only (never JS eval): the 3D viewer's meshopt decoder
        "script-src 'self' 'wasm-unsafe-eval'",
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

# Paths that are not the web app's shell. Everything else is index.html (or
# a static file next to it): no other site may frame it, not even to show
# the /add confirm card, so a page can't overlay it and trick a click.
NOT_SHELL = ("/api/", "/mcp", "/s/", "/assets/", "/docs", "/redoc", "/openapi.json")


def _map_origin(url: str) -> str | None:
    """The origin of a hosted map-tile provider (KIST_MAP_TILES_URL), which
    the web map fetches from; None for the self-hosted file."""
    from urllib.parse import urlsplit

    if not url.startswith(("https://", "http://")):
        return None
    parts = urlsplit(url)
    if not parts.hostname or any(c in parts.netloc for c in " ;'\"\\"):
        return None
    return f"{parts.scheme}://{parts.netloc}"


def app_csp(map_tiles_url: str = "", shell: bool = False, add_card: bool = False) -> str:
    """APP_CSP, plus the hosted map provider's origin when one is set, and
    frame-ancestors 'none' on the app shell. The /add card (the
    bookmarklet's confirm window) previews the image on the page it came
    from, so images from any web address may load there (only images:
    scripts and connections stay same-origin, and it sends no referrer)."""
    csp = APP_CSP
    if add_card:
        csp = csp.replace("img-src 'self' data: blob:", "img-src 'self' data: blob: https: http:")
    origin = _map_origin(map_tiles_url or "")
    if origin:
        csp = csp.replace("img-src 'self' data: blob:", f"img-src 'self' data: blob: {origin}")
        csp = csp.replace("connect-src 'self'", f"connect-src 'self' {origin}")
    if shell:
        csp = csp.replace("frame-ancestors 'self'", "frame-ancestors 'none'")
    return csp


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        settings = get_settings()
        hsts = settings.hsts_seconds
        shell = not (path.startswith(NOT_SHELL) or path in ("/robots.txt", "/s"))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "same-origin")
                headers.setdefault("X-Frame-Options", "DENY" if shell else "SAMEORIGIN")
                headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                headers.setdefault(
                    "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
                )
                if hsts > 0:
                    headers.setdefault("Strict-Transport-Security", f"max-age={hsts}")
                if not path.startswith(DOCS_PATHS):
                    # responses that set their own (sandboxed originals) keep it
                    headers.setdefault("Content-Security-Policy",
                                       app_csp(settings.map_tiles_url, shell,
                                               add_card=path == "/add"))
            await send(message)

        await self.app(scope, receive, send_with_headers)
