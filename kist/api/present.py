"""Present mode (Kist 2.0 F2): slides, saved shows, the PDF and the phone
remote. The services are services/present.py, present_pdf.py and
present_remote.py; the phone's relay (`relay_router`, no Kist token) is
mounted by app.py outside /api."""

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import access
from ..config import get_settings
from ..db import SessionLocal, get_session
from ..models import Item
from ..services import present as present_svc
from ..services import present_pdf
from ..services import present_remote as remote_svc
from ..services import search as search_svc
from .items import search_filters

router = APIRouter(tags=["present"])
relay_router = APIRouter(prefix="/present-relay", tags=["present"], include_in_schema=False)


class SlidesIn(BaseModel):
    source: dict[str, Any]
    compare: list[list[str]] | None = None
    pairs: str = "none"


class PdfIn(SlidesIn):
    show_id: str | None = None
    pdf: dict[str, Any] | None = None
    # the PDF's title and file name (a search has no name of its own)
    name: str | None = Field(default=None, max_length=200)


class ShowIn(BaseModel):
    name: str = Field(max_length=present_svc.NAME_MAX)
    source: dict[str, Any]
    options: dict[str, Any] | None = None
    compare: list[list[str]] | None = None
    # a search show is saved as the items it found
    item_ids: list[str] | None = None


class ShowPatch(BaseModel):
    name: str | None = Field(default=None, max_length=present_svc.NAME_MAX)
    options: dict[str, Any] | None = None
    compare: list[list[str]] | None = None
    item_ids: list[str] | None = None


class RemoteIn(BaseModel):
    name: str = Field(default="Show", max_length=200)
    item_ids: list[str] = Field(default_factory=list, max_length=remote_svc.MAX_ITEMS)


class CommandIn(BaseModel):
    cmd: str = Field(max_length=20)
    index: int | None = None


def _404(exc: Exception) -> HTTPException:
    return HTTPException(404, str(exc) or "Not Found")


def _search_runner(session: Session, filters: dict, semantic: str | None, similar: str | None):
    def run(cap: int) -> list[Item]:
        out: list[Item] = []
        cursor = None
        while len(out) < cap:
            res = search_svc.search(session, semantic=semantic, similar=similar,
                                    **{**filters, "limit": min(500, cap), "cursor": cursor})
            out.extend(res.items)
            cursor = res.next_cursor
            if not cursor or not res.items:
                break
        return out[:cap]
    return run


def _resolve(session, body: SlidesIn, filters, semantic, similar) -> dict:
    try:
        return present_svc.resolve(session, body.source, compare=body.compare, pairs=body.pairs,
                                   search_items=_search_runner(session, filters, semantic,
                                                               similar))
    except present_svc.NotFound as exc:
        raise _404(exc) from None


@router.post("/present/slides")
def slides(
    body: SlidesIn,
    session: Annotated[Session, Depends(get_session)],
    filters: Annotated[dict, Depends(search_filters)],
    semantic: Annotated[str | None, Query(max_length=1000)] = None,
    similar: Annotated[str | None, Query(max_length=26)] = None,
) -> dict[str, Any]:
    """The show for a folder, smart folder, board, chosen items or (with
    the grid's own query string) a search: its slides, in order."""
    return _resolve(session, body, filters, semantic, similar)


def _pdf_response(data: bytes, name: str) -> Response:
    safe = "".join(c if c.isalnum() or c in " -_." else "_" for c in name).strip() or "show"
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{safe[:80]}.pdf"'})


@router.post("/present/pdf")
def pdf(
    body: PdfIn,
    session: Annotated[Session, Depends(get_session)],
    filters: Annotated[dict, Depends(search_filters)],
    semantic: Annotated[str | None, Query(max_length=1000)] = None,
    similar: Annotated[str | None, Query(max_length=26)] = None,
):
    """The show as a PDF: one landscape page per slide with captions and
    credits (layout "slides"), or a contact sheet (layout "sheet")."""
    if body.show_id:
        try:
            resolved = present_svc.show_slides(session, body.show_id)
        except present_svc.NotFound as exc:
            raise _404(exc) from None
    else:
        resolved = _resolve(session, body, filters, semantic, similar)
    if body.name and body.name.strip():
        resolved["name"] = body.name.strip()
    return _pdf_response(present_pdf.render(session, resolved, body.pdf), resolved["name"])


# ---------- saved shows ----------


@router.get("/shows")
def list_shows(session: Annotated[Session, Depends(get_session)]) -> list[dict[str, Any]]:
    return present_svc.list_shows(session)


@router.post("/shows", status_code=201)
def create_show(body: ShowIn, session: Annotated[Session, Depends(get_session)]) -> dict:
    access.require(access.Action.EDIT)
    return present_svc.create_show(session, name=body.name, source=body.source,
                                   options=body.options, compare=body.compare,
                                   search_item_ids=body.item_ids)


@router.get("/shows/{show_id}")
def get_show(show_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    try:
        return present_svc.get_show(session, show_id)
    except present_svc.NotFound as exc:
        raise _404(exc) from None


@router.patch("/shows/{show_id}")
def patch_show(show_id: str, body: ShowPatch,
               session: Annotated[Session, Depends(get_session)]) -> dict:
    access.require(access.Action.EDIT)
    try:
        return present_svc.update_show(session, show_id, body.model_dump(exclude_unset=True))
    except present_svc.NotFound as exc:
        raise _404(exc) from None


@router.delete("/shows/{show_id}", status_code=204)
def delete_show(show_id: str, session: Annotated[Session, Depends(get_session)]) -> Response:
    access.require(access.Action.EDIT)
    try:
        present_svc.delete_show(session, show_id)
    except present_svc.NotFound as exc:
        raise _404(exc) from None
    return Response(status_code=204)


@router.get("/shows/{show_id}/slides")
def show_slides(show_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    try:
        return present_svc.show_slides(session, show_id)
    except present_svc.NotFound as exc:
        raise _404(exc) from None


@router.get("/shows/{show_id}/pdf")
def show_pdf(
    show_id: str,
    session: Annotated[Session, Depends(get_session)],
    layout: str = "slides",
    page: str = "a4",
    notes: bool = False,
    captions: bool | None = None,
    filenames: bool | None = None,
):
    try:
        resolved = present_svc.show_slides(session, show_id)
    except present_svc.NotFound as exc:
        raise _404(exc) from None
    opts = resolved["options"]
    return _pdf_response(present_pdf.render(session, resolved, {
        "layout": layout, "page": page, "notes": notes,
        "captions": opts["captions"] if captions is None else captions,
        "filenames": opts["filenames"] if filenames is None else filenames,
    }), resolved["name"])


# ---------- phone remote: the show window's side ----------


def _who() -> str:
    p = access.current()
    return p.name if p is not None else "cli"


def _remote(remote_id: str) -> remote_svc.Session:
    try:
        return remote_svc.get(remote_id)
    except remote_svc.NotFound as exc:
        raise _404(exc) from None


@router.post("/present/remote", status_code=201)
def create_remote(body: RemoteIn) -> dict:
    """Pair a phone: a session for this show, and the token the QR code
    carries (shown once; the server keeps only its hash)."""
    access.require(access.Action.EDIT)
    s, token = remote_svc.create(name=body.name, item_ids=body.item_ids, owner=_who())
    return {**remote_svc.info(s), "token": token,
            "path": f"/present/remote/{s.id}#{token}"}


@router.get("/present/remote/{remote_id}/poll")
async def remote_poll(remote_id: str, after: int = 0,
                      wait: Annotated[float, Query(ge=0, le=25)] = 20) -> dict:
    """Commands the phone sent after `after` (waits up to `wait` seconds)."""
    access.require(access.Action.EDIT)
    return await remote_svc.wait_commands(_remote(remote_id), after, wait)


@router.post("/present/remote/{remote_id}/state")
async def remote_state(remote_id: str, body: dict[str, Any]) -> dict:
    """What the phone shows: slide n of m, title, credit, notes, pick."""
    access.require(access.Action.EDIT)
    return remote_svc.set_state(_remote(remote_id), body)


@router.delete("/present/remote/{remote_id}", status_code=204)
async def remote_revoke(remote_id: str) -> Response:
    access.require(access.Action.EDIT)
    try:
        remote_svc.revoke(remote_id)
    except remote_svc.NotFound as exc:
        raise _404(exc) from None
    return Response(status_code=204)


# ---------- phone remote: the phone's side (no Kist token) ----------

NOT_FOUND = {"detail": "Not Found"}
NO_STORE = {"Cache-Control": "no-store"}


def _phone(remote_id: str, token: str | None) -> remote_svc.Session | None:
    try:
        return remote_svc.check_token(remote_id, token)
    except remote_svc.NotFound:
        return None


@relay_router.get("/{remote_id}/state")
async def relay_state(remote_id: str, after: int = 0,
                      wait: Annotated[float, Query(ge=0, le=25)] = 20,
                      x_kist_remote: Annotated[str | None, Header()] = None):
    s = _phone(remote_id, x_kist_remote)
    if s is None:
        return JSONResponse(NOT_FOUND, status_code=404, headers=NO_STORE)
    return JSONResponse(await remote_svc.wait_state(s, after, wait), headers=NO_STORE)


@relay_router.post("/{remote_id}/cmd")
async def relay_cmd(remote_id: str, request: Request,
                    x_kist_remote: Annotated[str | None, Header()] = None):
    s = _phone(remote_id, x_kist_remote)
    if s is None:
        return JSONResponse(NOT_FOUND, status_code=404, headers=NO_STORE)
    raw = await request.body()
    if len(raw) > 512:
        return JSONResponse({"detail": "too large"}, status_code=413, headers=NO_STORE)
    try:
        body = CommandIn.model_validate_json(raw or b"{}")
    except ValueError:
        return JSONResponse({"detail": "cmd is required"}, status_code=422, headers=NO_STORE)
    try:
        event = remote_svc.send_command(s, body.cmd, body.index)
    except remote_svc.TooMany:
        return JSONResponse({"detail": "slow down"}, status_code=429,
                            headers={**NO_STORE, "Retry-After": "1"})
    return JSONResponse(event, headers=NO_STORE)


@relay_router.get("/{remote_id}/thumb/{item_id}")
def relay_thumb(remote_id: str, item_id: str,
                x_kist_remote: Annotated[str | None, Header()] = None):
    """A thumbnail of one of this show's items (and nothing else)."""
    s = _phone(remote_id, x_kist_remote)
    if s is None or item_id not in s.item_ids:
        return JSONResponse(NOT_FOUND, status_code=404, headers=NO_STORE)
    with SessionLocal() as session:
        item = session.get(Item, item_id)
        if item is None or item.deleted_at is not None:
            return JSONResponse(NOT_FOUND, status_code=404, headers=NO_STORE)
    d: Path = get_settings().items_dir / item_id
    for name in ("thumb_1024.webp", "thumb_512.webp", "thumb_256.webp"):
        if (d / name).is_file():
            return FileResponse(d / name, media_type="image/webp",
                                headers={"Cache-Control": "private, max-age=600"})
    return JSONResponse(NOT_FOUND, status_code=404, headers=NO_STORE)
