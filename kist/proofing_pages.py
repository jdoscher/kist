"""F3 guest proofing on the public share pages: the pick / reject / heart
buttons and comments a guest sees when the link has "Let guests pick and
comment" on, the item page /s/<slug>/i/<item_id>, and the one POST endpoint
/s/<slug>/proof every guest action goes through.

Like the rest of /s/, these pages run no script at all (the sandbox CSP in
share_pages.py stays as it is): every action is a plain HTML form post
followed by a redirect back. A guest's id and display name live in the
signed, HttpOnly kist_guest cookie scoped to /s/<slug> (SameSite=Lax), and
every form carries a CSRF token tied to that guest id.
"""

import html
import logging
from datetime import datetime
from typing import Annotated
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from . import share_pages as sp
from .config import get_settings
from .db import get_session
from .services import proofing, validation
from .services import shares as shares_svc

log = logging.getLogger("kist.proofing_pages")

router = APIRouter(include_in_schema=False)

MAX_BODY = 16 * 1024

STYLE = """
.pf-bar{padding:12px 20px;border-bottom:1px solid #333;display:flex;flex-wrap:wrap;gap:8px 16px;
align-items:center;font-size:13px;color:#bbb}
.pf-bar form{display:flex;gap:6px;align-items:center;margin:0}
.pf-bar input{width:auto;margin:0;padding:5px 8px;min-height:28px;box-sizing:border-box}
.pf-acts{display:flex;gap:6px;padding:0 8px 8px;flex-wrap:wrap;align-items:center;margin:0}
.pf-acts button,.pf-bar button,.pf-c button,.pf-item form button{
min-height:28px;padding:4px 10px;border-radius:6px;
border:1px solid #555;background:#222;color:#eee;font:inherit;font-size:13px;cursor:pointer}
.pf-acts button[aria-pressed=true]{background:#3a3a3a;outline:1px solid #ddd}
.pf-acts a{color:#8cf;font-size:13px}
.pf-n{font-size:12px;color:#aaa}
.pf-item{max-width:1100px;margin:0 auto;padding:16px}
.pf-item img{max-width:100%;max-height:75vh;display:block;margin:0 auto;background:#222}
.pf-c{list-style:none;padding:0}
.pf-c li{background:#1b1b1b;border-radius:6px;padding:8px 10px;margin:8px 0;white-space:pre-wrap;
overflow-wrap:anywhere}
.pf-c .who{color:#aaa;font-size:12px;white-space:normal}
textarea{width:100%;min-height:90px;box-sizing:border-box;padding:8px;font:inherit;margin:6px 0}
.pf-closed{color:#fc9}
"""


class Guest:
    def __init__(self, guest_id: str, name: str, fresh: bool):
        self.id, self.name, self.fresh = guest_id, name, fresh


def _guest(request: Request, share) -> Guest:
    got = proofing.read_guest_cookie(share, request.cookies.get(proofing.GUEST_COOKIE))
    if got:
        return Guest(got[0], got[1], False)
    return Guest(proofing.new_guest_id(), "", True)


def _set_guest_cookie(resp: Response, request: Request, share, guest: Guest) -> None:
    value, max_age = proofing.make_guest_cookie(share, guest.id, guest.name)
    resp.set_cookie(
        proofing.GUEST_COOKIE, value, max_age=max_age, path=f"/s/{share.slug}", httponly=True,
        samesite="lax",
        secure=get_settings().public_url.startswith("https://") or request.url.scheme == "https",
    )


def _esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


def _when(value: datetime | None) -> str:
    return value.strftime("%d %b %Y, %H:%M UTC") if value else ""


class PageContext:
    """What the gallery page needs to draw the guest's controls."""

    def __init__(self, session: Session, request: Request, share, settings, item_ids, page):
        self.share, self.settings, self.page = share, settings, page
        self.guest = _guest(request, share)
        self.request = request
        self.view = proofing.guest_view(session, share, settings, self.guest.id, item_ids)
        self.csrf = proofing.csrf_token(share, self.guest.id)
        self.slug = quote(share.slug, safe="")

    def _hidden(self, back: str, item_id: str | None = None) -> str:
        out = (f'<input type="hidden" name="csrf" value="{_esc(self.csrf)}">'
               f'<input type="hidden" name="back" value="{_esc(back)}">')
        if item_id:
            out += f'<input type="hidden" name="item_id" value="{_esc(item_id)}">'
        return out

    def header(self, back: str | None = None) -> str:
        s = self.settings
        who = ("Everyone with this link sees all feedback." if s.show_all
               else "Only you and the owner see your feedback.")
        if not s.round_open:
            state = f'<span class="pf-closed">Feedback for round {s.round} is closed.</span>'
            return f'<div class="pf-bar"><style>{STYLE}</style>{state}</div>'
        hidden = self._hidden(back or str(self.page))
        name = (f'<form method="post" action="/s/{self.slug}/proof">{hidden}'
                '<input type="hidden" name="action" value="name">'
                '<label for="pf-name">Your name</label>'
                f'<input id="pf-name" name="name" maxlength="{proofing.NAME_MAX}" '
                f'value="{_esc(self.guest.name)}" placeholder="optional" autocomplete="name">'
                '<button type="submit">Save</button></form>')
        return (f'<div class="pf-bar"><style>{STYLE}</style>'
                f'<span>Pick, heart or comment · round {s.round}. {who}</span>{name}</div>')

    def actions(self, item_id: str, back: str) -> str:
        v = self.view.get(item_id) or {}
        n_comments = len(v.get("comments") or [])
        link = (f'<a href="/s/{self.slug}/i/{quote(item_id, safe="")}#comments">'
                f'{"Comments" if self.settings.show_all else "Comment"}'
                f'{f" ({n_comments})" if n_comments else ""}</a>')
        counts = ""
        if self.settings.show_all and (v.get("picks") or v.get("hearts") or v.get("rejects")):
            counts = (f'<span class="pf-n">{v["picks"]} picked · {v["hearts"]} ♥'
                      f'{f" · {v["rejects"]} rejected" if v["rejects"] else ""}</span>')
        if not self.settings.round_open:
            mine = []
            if v.get("verdict"):
                mine.append("You picked this" if v["verdict"] == "pick" else "You rejected this")
            if v.get("heart"):
                mine.append("♥")
            return (f'<div class="pf-acts"><span class="pf-n">{_esc(" · ".join(mine))}</span>'
                    f'{counts}{link}</div>')

        def btn(action: str, label: str, pressed: bool, title: str) -> str:
            return (f'<button type="submit" name="action" value="{action}" '
                    f'aria-pressed="{"true" if pressed else "false"}" title="{_esc(title)}">'
                    f'{label}</button>')

        picked, rejected, heart = v.get("verdict") == "pick", v.get("verdict") == "reject", \
            bool(v.get("heart"))
        return (
            f'<form class="pf-acts" method="post" action="/s/{self.slug}/proof">'
            f'{self._hidden(back, item_id)}'
            + btn("pick", "✓ Picked" if picked else "Pick", picked,
                  "Pick this one (press again to undo)")
            + btn("heart", "♥" if heart else "♡", heart, "Heart (press again to undo)")
            + btn("reject", "✕ Rejected" if rejected else "Reject", rejected,
                  "Not this one (press again to undo)")
            + f"{counts}{link}</form>"
        )

    def finish(self, resp: Response) -> Response:
        if self.guest.fresh:
            _set_guest_cookie(resp, self.request, self.share, self.guest)
        return resp


def gallery_context(session: Session, request: Request, share, item_ids: list[str],
                    page: int) -> PageContext | None:
    """share_pages.share_page's hook: None unless guest feedback is on."""
    settings = proofing.enabled_for(session, share)
    if settings is None:
        return None
    return PageContext(session, request, share, settings, item_ids, page)


# ---------- the item page ----------


@router.get("/s/{slug}/i/{item_id}")
def item_page(slug: str, item_id: str, request: Request,
              session: Annotated[Session, Depends(get_session)]):
    share, error = sp._resolve(session, slug)
    if error:
        return error
    if not sp._unlocked(request, share):
        return sp._password_form(share, status=401)
    settings = proofing.enabled_for(session, share)
    item = shares_svc.item_in_share(session, share, item_id)
    if settings is None or item is None:
        return sp._not_found()
    ctx = PageContext(session, request, share, settings, [item.id], 1)
    s = ctx.slug
    v = ctx.view[item.id]
    comments = []
    for c in v["comments"]:
        delete = ""
        if c["mine"] and settings.round_open:
            delete = (f'<form method="post" action="/s/{s}/proof" style="display:inline">'
                      f'{ctx._hidden("item", item.id)}'
                      f'<input type="hidden" name="comment_id" value="{_esc(c["id"])}">'
                      '<button type="submit" name="action" value="delete_comment">Delete</button>'
                      '</form>')
        comments.append(f'<li><div class="who">{_esc(c["name"])}{" (you)" if c["mine"] else ""}'
                        f' · {_esc(_when(c["at"]))} {delete}</div>{_esc(c["text"])}</li>')
    form = ""
    if settings.round_open:
        form = (f'<form method="post" action="/s/{s}/proof">{ctx._hidden("item", item.id)}'
                '<input type="hidden" name="action" value="comment">'
                '<label for="pf-text">Add a comment</label>'
                f'<textarea id="pf-text" name="text" maxlength="{proofing.COMMENT_MAX}" required>'
                '</textarea><button type="submit">Send comment</button></form>')
    name = _esc(item.filename)
    title = share.title or "Shared collection"
    body = (
        f'<header><h1><a href="/s/{s}" style="color:inherit">{_esc(title)}</a></h1>'
        f'<div class="sub">{name}{sp._credit_html(item)}</div></header>{ctx.header("item")}'
        f'<div class="pf-item"><img src="/s/{s}/i/{item.id}/display" alt="{name}">'
        f'{ctx.actions(item.id, "item")}'
        f'<h2 id="comments" style="font-size:16px">Comments</h2>'
        f'<ul class="pf-c">{"".join(comments) or "<li>No comments yet.</li>"}</ul>{form}'
        f'<p><a href="/s/{s}" style="color:#8cf">← Back to all</a></p></div>'
    )
    return ctx.finish(sp._page(f"{item.filename} · {title}", body))


# ---------- the one write endpoint ----------


def _cross_site(request: Request) -> bool:
    """Refuse forms posted from another site. Browsers send Sec-Fetch-Site;
    Origin is "null" on these no-referrer pages, so only a real foreign
    origin counts against a request."""
    if request.headers.get("sec-fetch-site", "") == "cross-site":
        return True
    origin = request.headers.get("origin", "")
    if origin and origin != "null":
        host = request.headers.get("host", "")
        if urlsplit(origin).netloc.lower() != host.lower():
            public = urlsplit(get_settings().public_url).netloc.lower()
            return not public or urlsplit(origin).netloc.lower() != public
    return False


def _back(share, back: str, item_id: str | None) -> str:
    s = quote(share.slug, safe="")
    if back == "item" and item_id and shares_svc.ITEM_ID_RE.match(item_id):
        return f"/s/{s}/i/{item_id}#comments"
    page = int(back) if back.isdigit() and 0 < int(back) <= 10_000 else 1
    anchor = f"#i-{item_id}" if item_id and shares_svc.ITEM_ID_RE.match(item_id) else ""
    return f"/s/{s}" + (f"?page={page}" if page > 1 else "") + anchor


def _refuse(title: str, text: str, status: int, share=None):
    if share is not None:
        text_html = (f'<div class="box"><h1>{_esc(title)}</h1><p>{_esc(text)}</p>'
                     f'<p><a href="/s/{quote(share.slug, safe="")}" style="color:#8cf">'
                     'Back to the collection</a></p></div>')
        return sp._page(title, text_html, status)
    return sp._message(title, text, status)


@router.post("/s/{slug}/proof")
async def proof_action(slug: str, request: Request,
                       session: Annotated[Session, Depends(get_session)]):
    share, error = sp._resolve(session, slug)
    if error:
        return error
    if not sp._unlocked(request, share):
        return sp._message("Password required", "Open the collection first.", 401)
    if proofing.enabled_for(session, share) is None:
        return _refuse("Feedback is off", "Feedback is turned off for this link.", 403, share)
    if _cross_site(request):
        log.info("share %s: cross-site feedback refused", share.id)
        return _refuse("Not allowed", "Feedback has to come from the page itself.", 403, share)
    declared = request.headers.get("content-length", "")
    if not declared.isdigit() or int(declared) > MAX_BODY:
        return _refuse("Too long", "That was too long to send.", 413, share)
    try:
        form = await request.form(max_files=0, max_fields=12, max_part_size=MAX_BODY)
    except Exception:
        return _refuse("Not understood", "That form couldn't be read.", 400, share)
    got = proofing.read_guest_cookie(share, request.cookies.get(proofing.GUEST_COOKIE))
    if got is None or not proofing.csrf_ok(share, got[0], str(form.get("csrf") or "")):
        return _refuse("Page expired", "Reload the page and try again.", 403, share)
    guest = Guest(got[0], got[1], False)
    try:
        proofing.rate_check(request.client.host if request.client else None, share.id)
    except proofing.TooMany as exc:
        resp = _refuse("Slow down", str(exc), 429, share)
        resp.headers["Retry-After"] = str(exc.wait)
        return resp
    action = str(form.get("action") or "")
    back = str(form.get("back") or "1")[:12]
    item_id = str(form.get("item_id") or "")[:40] or None
    try:
        if action == "name":
            guest.name = proofing.clean_name(form.get("name"))
            proofing.rename_guest(session, share, guest.id, guest.name)
            resp = Response(status_code=303, headers={**sp._headers(),
                                                      "Location": _back(share, back, None)})
            _set_guest_cookie(resp, request, share, guest)
            return resp
        if action == "delete_comment":
            proofing.guest_delete_comment(session, share, guest.id,
                                          str(form.get("comment_id") or "")[:40])
        else:
            item = shares_svc.item_in_share(session, share, item_id or "")
            if item is None:
                return sp._not_found()
            if action == "comment":
                proofing.add_comment(session, share, item, guest.id, guest.name,
                                     str(form.get("text") or ""))
            elif action in proofing.MARK_ACTIONS:
                proofing.set_mark(session, share, item, guest.id, guest.name, action)
            else:
                return _refuse("Not understood", "Unknown action.", 400, share)
    except proofing.Closed as exc:
        return _refuse("Feedback closed", str(exc), 403, share)
    except proofing.TooMany as exc:
        resp = _refuse("That's plenty", str(exc), 429, share)
        resp.headers["Retry-After"] = str(exc.wait)
        return resp
    except proofing.NotFound:
        return sp._not_found()
    except validation.ValidationError as exc:
        return _refuse("Not saved", str(exc), 400, share)
    return Response(status_code=303,
                    headers={**sp._headers(), "Location": _back(share, back, item_id)})
