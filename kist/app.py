from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import api_router
from .auth import BearerTokenMiddleware
from .config import get_settings
from .mcp_server import mcp, mcp_http_app

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


WEAK_TOKENS = {"", "change-me", "changeme", "secret", "token"}
MIN_TOKEN_LENGTH = 16


def check_token(token: str) -> None:
    if token.strip() in WEAK_TOKENS or len(token) < MIN_TOKEN_LENGTH:
        raise RuntimeError(
            "KIST_TOKEN is unset or too weak — set it in .env to a random value "
            f"of at least {MIN_TOKEN_LENGTH} characters (e.g. `openssl rand -hex 24`)"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    check_token(settings.token)
    for d in (settings.items_dir, settings.inbox_dir, settings.staging_dir,
              *settings.import_dirs):
        d.mkdir(parents=True, exist_ok=True)
    # the mounted MCP sub-app's own lifespan never runs, so start its
    # session manager here
    async with mcp.session_manager.run():
        yield


class McpPathRewrite:
    """Starlette mounts match /mcp/* but not bare /mcp; clients POST to /mcp."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/mcp":
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
        await self.app(scope, receive, send)


app = FastAPI(title="Kist", version=__version__, lifespan=lifespan)
app.include_router(api_router, prefix="/api")
app.mount("/mcp", mcp_http_app)
app.add_middleware(BearerTokenMiddleware)
app.add_middleware(McpPathRewrite)

if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        candidate = (WEB_DIST / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(WEB_DIST):
            return FileResponse(candidate)
        # revalidate every load so a redeploy's new asset hashes are picked up
        return FileResponse(WEB_DIST / "index.html", headers={"Cache-Control": "no-cache"})
