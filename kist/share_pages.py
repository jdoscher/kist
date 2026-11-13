"""The public share pages: /s/<slug> and /robots.txt.

The only routes Kist serves without a token (auth.PUBLIC_PREFIXES). They are
plain server-rendered HTML with no script at all, under a sandboxing CSP
that allows nothing but this origin's images and inline styles. Every
response says noindex. The web app's token (kist_token cookie or bearer
header) is never read here and never set; a visitor's only state is the
HttpOnly kist_share cookie a password share sets, scoped to its own path.
"""

import html
import logging
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_session
from .filetypes import serve_inline
from .services import shares as shares_svc
from .services import thumbs

log = logging.getLogger("kist.share_pages")

router = APIRouter(include_in_schema=False)


class NoVisitorAddress(logging.Filter):
    """uvicorn's access log names the client's address on every line; for
    share pages it is replaced by "-", so visitors to a link aren't kept
    in Kist's logs (a reverse proxy's own log is its own business)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and str(args[2]).startswith("/s/"):
            record.args = ("-", *args[1:])
        return True


logging.getLogger("uvicorn.access").addFilter(NoVisitorAddress())

SHARE_CSP = "; ".join([
    "default-src 'none'",
    "img-src 'self'",
    "style-src 'unsafe-inline'",
    "form-action 'self'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
    # no allow-scripts: nothing on these pages can ever run script
    "sandbox allow-forms allow-same-origin allow-downloads",
])
ROBOTS_TAG = "noindex, nofollow, noarchive, nosnippet, noimageindex"
PAGE_SIZE = 200


def _headers(cache: str = "no-store") -> dict:
    return {
        "Content-Security-Policy": SHARE_CSP,
        "X-Robots-Tag": ROBOTS_TAG,
        "Referrer-Policy": "no-referrer",
        "Cache-Control": cache,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }


STYLE = """
body{font:15px/1.4 system-ui,-apple-system,sans-serif;margin:0;background:#111;color:#eee}
header{padding:16px 20px;border-bottom:1px solid #333}
h1{font-size:20px;margin:0}
.sub{color:#999;font-size:13px}
main{padding:16px;display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(220px,1fr))}
figure{margin:0;background:#1b1b1b;border-radius:6px;overflow:hidden}
figure img{width:100%;height:220px;object-fit:cover;display:block;background:#222}
figcaption{padding:6px 8px;font-size:12px;color:#bbb;word-break:break-all}
figcaption a{color:#8cf}
.box{max-width:360px;margin:15vh auto;padding:24px;background:#1b1b1b;border-radius:8px}
input{width:100%;padding:8px;margin:8px 0;box-sizing:border-box}
button{padding:8px 14px}
.err{color:#f88}
nav{padding:16px 20px}
nav a{color:#8cf;margin-right:16px}
.credit{display:block;color:#aaa;word-break:normal;margin-top:2px}
"""


def _credit_html(item) -> str:
    """F4: the item's credit under its name ('' when none is recorded)."""
    from .services.credits import output_text

    text = output_text(item)
    return f"<span class=\"credit\">{html.escape(text)}</span>" if text else ""


def _page(title: str, body: str, status: int = 200, extra_headers: dict | None = None):
    doc = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<meta name=\"robots\" content=\"noindex,nofollow\">"
        "<meta name=\"referrer\" content=\"no-referrer\">"
        f"<title>{html.escape(title)}</title><style>{STYLE}</style></head>"
        f"<body>{body}</body></html>"
    )
    return HTMLResponse(doc, status_code=status, headers={**_headers(), **(extra_headers or {})})


def _message(title: str, text: str, status: int):
    return _page(title, f"<div class=\"box\"><h1>{html.escape(title)}</h1>"
                        f"<p>{html.escape(text)}</p></div>", status)


def _not_found():
    return _message("Not found", "There is no shared collection at this address.", 404)


def _gone():
    return _message("Link no longer available",
                    "This shared link has expired or was turned off by its owner.", 410)


def _unlocked(request: Request, share) -> bool:
    if not share.password_hash:
        return True
    return shares_svc.cookie_valid(share, request.cookies.get("kist_share"))


def _password_form(share, error: str = "", status: int = 401):
    err = f"<p class=\"err\">{html.escape(error)}</p>" if error else ""
    slug = quote(share.slug, safe="")
    return _page(
        "Password required",
        "<div class=\"box\"><h1>Password required</h1>"
        f"<p>{html.escape(share.title or 'This collection')} is protected.</p>{err}"
        f"<form method=\"post\" action=\"/s/{slug}\">"
        "<input type=\"password\" name=\"password\" autocomplete=\"current-password\" "
        "maxlength=\"200\" required autofocus>"
        "<button type=\"submit\">Open</button></form></div>",
        status,
    )


def _resolve(session: Session, slug: str):
    """(share, error response)."""
    try:
        return shares_svc.by_slug(session, slug), None
    except shares_svc.NotFound:
        return None, _not_found()
    except shares_svc.Gone:
        return None, _gone()


@router.get("/robots.txt")
def robots():
    return PlainTextResponse("User-agent: *\nDisallow: /s/\n",
                             headers={"Cache-Control": "public, max-age=86400"})


@router.get("/s/{slug}")
def share_page(
    slug: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    page: int = 1,
):
    share, error = _resolve(session, slug)
    if error:
        return error
    if not _unlocked(request, share):
        return _password_form(share, status=401)
    page = max(1, min(int(page or 1), 10_000))
    total = shares_svc.count(session, share)
    rows = shares_svc.items(session, share, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    shares_svc.record_view(session, share)
    s = quote(share.slug, safe="")
    title = share.title or "Shared collection"
    figures = []
    for item in rows:
        name = html.escape(item.filename)
        base = f"/s/{s}/i/{item.id}"
        links = f"<a href=\"{base}/display\">{name}</a>"
        if share.allow_download:
            links += f" · <a href=\"{base}/file\" download>download</a>"
        links += _credit_html(item)  # F4
        figures.append(
            f"<figure><a href=\"{base}/display\"><img src=\"{base}/thumb\" alt=\"{name}\" "
            f"loading=\"lazy\"></a><figcaption>{links}</figcaption></figure>"
        )
    nav = ""
    if total > page * PAGE_SIZE or page > 1:
        parts = []
        if page > 1:
            parts.append(f"<a href=\"/s/{s}?page={page - 1}\">← previous</a>")
        if total > page * PAGE_SIZE:
            parts.append(f"<a href=\"/s/{s}?page={page + 1}\">next →</a>")
        nav = "<nav>" + "".join(parts) + "</nav>"
    count = f"{total} item{'s' if total != 1 else ''}"
    body = (
        f"<header><h1>{html.escape(title)}</h1><div class=\"sub\">{count} · shared from Kist"
        "</div></header>"
        f"<main>{''.join(figures)}</main>{nav}"
    )
    return _page(title, body)


@router.post("/s/{slug}")
async def share_unlock(
    slug: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
):
    share, error = _resolve(session, slug)
    if error:
        return error
    if not share.password_hash:
        return Response(status_code=303, headers={**_headers(), "Location": f"/s/{quote(slug)}"})
    wait = shares_svc.too_many_attempts(share.slug)
    if wait:
        resp = _message("Too many tries", "Too many wrong passwords. Try again later.", 429)
        resp.headers["Retry-After"] = str(wait)
        return resp
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > 4096:
        return _password_form(share, "That password is too long.", 413)
    try:
        form = await request.form(max_files=0, max_fields=4)
        password = str(form.get("password") or "")
    except Exception:
        password = ""
    if not shares_svc.check_password(share.password_hash, password):
        shares_svc.note_failure(share.slug)
        log.info("share %s: wrong password", share.id)
        return _password_form(share, "That password isn't right.", 401)
    value, max_age = shares_svc.make_cookie(share)
    resp = Response(status_code=303, headers={**_headers(), "Location": f"/s/{quote(slug)}"})
    resp.set_cookie(
        "kist_share", value, max_age=max_age, path=f"/s/{share.slug}", httponly=True,
        samesite="lax", secure=get_settings().public_url.startswith("https://")
        or request.url.scheme == "https",
    )
    return resp


VARIANTS = ("thumb", "display", "file")


@router.get("/s/{slug}/i/{item_id}/{variant}")
def share_file(
    slug: str,
    item_id: str,
    variant: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    download: bool = False,
):
    share, error = _resolve(session, slug)
    if error:
        return error
    if variant not in VARIANTS:
        return _not_found()
    if not _unlocked(request, share):
        return _message("Password required", "Open the collection first.", 401)
    item = shares_svc.item_in_share(session, share, item_id)
    if item is None:
        return _not_found()
    item_dir = get_settings().items_dir / item.id
    cache = "private, max-age=3600"
    if variant == "file":
        if not share.allow_download:
            return _message("Not allowed", "Downloads are turned off for this link.", 403)
        if share.allow_original:
            path = item_dir / f"original.{item.ext}"
            if not path.is_file():
                return _not_found()
            headers = _headers(cache)
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
            return FileResponse(
                path, media_type=item.mime, filename=item.filename, headers=headers,
                content_disposition_type="inline" if serve_inline(item.mime) and not download
                else "attachment",
            )
        # download allowed, originals not: the full-screen copy
        variant, download = "display", True
    names = (
        ["thumb_1024.webp", "thumb_256.webp"] if variant == "thumb"
        else [thumbs.SCREEN_FILE, "display.webp", "thumb_1024.webp", "thumb_256.webp"]
    )
    for name in names:
        path = item_dir / name
        if path.is_file():
            kwargs = {}
            if download and share.allow_download:
                stem = item.filename.rsplit(".", 1)[0] or "image"
                kwargs = {"filename": f"{stem}.webp", "content_disposition_type": "attachment"}
            return FileResponse(path, media_type="image/webp", headers=_headers(cache), **kwargs)
    return _not_found()
