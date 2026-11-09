from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import api_router
from .auth import BearerTokenMiddleware
from .compress import CompressText
from .config import get_settings
from .headers import SecurityHeadersMiddleware
from .lifecycle import TransitionError
from .mcp_server import mcp, mcp_http_app
from .observability import RequestContext, configure_logging
from .schemas import ItemOut
from .services import folders as folders_svc
from .services import items as items_svc
from .services import jobs as jobs_svc
from .services import smart_folders as smart_svc
from .services.search import BadCursor
from .services.validation import ValidationError

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


WEAK_TOKENS = {"", "change-me", "changeme", "secret", "token"}
MIN_TOKEN_LENGTH = 16

configure_logging()


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
              settings.state_dir, *settings.import_dirs):
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


# /docs, /redoc and /openapi.json exist but need the token (auth.DOCS_PATHS)
app = FastAPI(title="Kist", version=__version__, lifespan=lifespan)
app.include_router(api_router, prefix="/api")
app.mount("/mcp", mcp_http_app)
app.add_middleware(BearerTokenMiddleware)
app.add_middleware(McpPathRewrite)
app.add_middleware(CompressText)  # gzip JSON/JS/CSS, never media
app.add_middleware(RequestContext)  # X-Request-ID; unhandled errors -> logged 500
app.add_middleware(SecurityHeadersMiddleware)  # outermost: 401s and 500s get headers too


# ---------- service errors -> HTTP (one place, so routes don't fetch twice) ----------


def _error(status: int, detail) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


@app.exception_handler(items_svc.NotFound)
@app.exception_handler(jobs_svc.NotFound)
@app.exception_handler(smart_svc.NotFound)
@app.exception_handler(folders_svc.FolderNotFound)
async def not_found(request: Request, exc: Exception):
    return _error(404, str(exc))


@app.exception_handler(ValidationError)
async def invalid(request: Request, exc: ValidationError):
    return _error(422, str(exc))


@app.exception_handler(BadCursor)
async def bad_cursor(request: Request, exc: BadCursor):
    return _error(400, str(exc))


@app.exception_handler(folders_svc.FolderError)
async def folder_error(request: Request, exc: Exception):
    return _error(400, str(exc))


@app.exception_handler(TransitionError)
async def transition_error(request: Request, exc: TransitionError):
    return _error(409, str(exc))


@app.exception_handler(items_svc.Conflict)
async def conflict(request: Request, exc: items_svc.Conflict):
    return _error(
        409,
        {
            "message": exc.message,
            "item": ItemOut.from_item(exc.item).model_dump(mode="json"),
        },
    )


if WEB_DIST.is_dir():
    class HashedAssets(StaticFiles):
        """Vite puts a content hash in every asset name: cache them for good."""

        def file_response(self, *args, **kwargs):
            response = super().file_response(*args, **kwargs)
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return response

    app.mount("/assets", HashedAssets(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        candidate = (WEB_DIST / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(WEB_DIST):
            return FileResponse(candidate)
        # revalidate every load so a redeploy's new asset hashes are picked up
        return FileResponse(WEB_DIST / "index.html", headers={"Cache-Control": "no-cache"})
