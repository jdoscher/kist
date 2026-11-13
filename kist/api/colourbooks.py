"""F5 colour books (services/colourbooks.py): import, list, enable/disable,
rename, delete; nearest code for a colour or an item's palette; code lookup.

    GET    /api/colourbooks                         the books
    POST   /api/colourbooks                         import (multipart file, name?, format?)
    GET    /api/colourbooks/nearest?hex=..&hex=..   nearest code per enabled book
    GET    /api/colourbooks/lookup?q=..             swatches by code or name
    GET    /api/colourbooks/{id}                    one book with its swatches
    PATCH  /api/colourbooks/{id}                    {name?, enabled?}
    DELETE /api/colourbooks/{id}                    remove it (owner/write tokens)
    GET    /api/items/{id}/palette/codes            the palette with nearest codes

Reading needs the read scope (a folder-limited reader reaches only
palette/codes for items it can see, and lookup); importing and editing need
edit; deleting needs a write token (agents can't delete books).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import access
from ..db import get_session
from ..services import colourbooks
from ..services import items as items_svc
from ..services import meta as meta_svc

router = APIRouter(tags=["colour books"])

LOCK = "colourbooks"


def _not_found(exc: colourbooks.NotFound) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


@router.get("/colourbooks")
def list_books() -> list[dict]:
    return [b.summary() for b in colourbooks.list_books()]


@router.post("/colourbooks", status_code=201)
async def import_book(
    session: Annotated[Session, Depends(get_session)],
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form(max_length=colourbooks.NAME_MAX)] = None,
    format: Annotated[str | None, Form()] = None,  # ase | csv | gpl (default: from the file)
) -> dict:
    access.require(access.Action.EDIT)
    data = await file.read(colourbooks.MAX_FILE_BYTES + 1)

    def run():
        meta_svc.advisory_lock(session, LOCK)
        try:
            return colourbooks.import_book(data, file.filename, name, format)
        finally:
            session.commit()

    book, report = await run_in_threadpool(run)
    return {**book.summary(), **report}


@router.get("/colourbooks/nearest")
def nearest(hex: Annotated[list[str], Query(min_length=1, max_length=32)],
            book: str | None = None) -> list[dict]:
    """[{hex, matches: [{book_id, book, code, name, hex, de}]}] per colour."""
    try:
        books = [colourbooks.get_book(book)] if book else None
    except colourbooks.NotFound as exc:
        raise _not_found(exc) from None
    return [{"hex": h, "matches": colourbooks.nearest(h, books)} for h in hex]


@router.get("/colourbooks/lookup")
def lookup(q: Annotated[str, Query(min_length=1, max_length=200)],
           limit: Annotated[int, Query(ge=1, le=100)] = 20,
           book: str | None = None) -> list[dict]:
    """Swatches whose code or name matches (each with ref, the colour
    search value: book:<book>/<code>). q may itself be a ref."""
    try:
        if colourbooks.REF_RE.match(q.strip()):
            b, sw = colourbooks.resolve(q)
            ref = colourbooks.ref_for(b, sw)
            return [{"book_id": b.id, "book": b.name, **sw.out(), "ref": ref}]
        return colourbooks.lookup(q, limit, book)
    except colourbooks.NotFound:
        return []


@router.get("/colourbooks/{book_id}")
def get_book(book_id: str) -> dict:
    try:
        b = colourbooks.get_book(book_id)
    except colourbooks.NotFound as exc:
        raise _not_found(exc) from None
    return {**b.summary(), "swatches": [s.out() for s in b.swatches]}


class BookPatch(BaseModel):
    name: str | None = Field(default=None, max_length=colourbooks.NAME_MAX)
    enabled: bool | None = None


@router.patch("/colourbooks/{book_id}")
def patch_book(book_id: str, body: BookPatch,
               session: Annotated[Session, Depends(get_session)]) -> dict:
    access.require(access.Action.EDIT)
    meta_svc.advisory_lock(session, LOCK)
    try:
        return colourbooks.update_book(book_id, name=body.name, enabled=body.enabled).summary()
    except colourbooks.NotFound as exc:
        raise _not_found(exc) from None
    finally:
        session.commit()


@router.delete("/colourbooks/{book_id}", status_code=204)
def delete_book(book_id: str, session: Annotated[Session, Depends(get_session)]) -> None:
    # destructive: owner and write tokens, never an agent token
    access.require(access.Action.HARD_DELETE)
    meta_svc.advisory_lock(session, LOCK)
    try:
        colourbooks.delete_book(book_id)
    except colourbooks.NotFound as exc:
        raise _not_found(exc) from None
    finally:
        session.commit()


@router.get("/items/{item_id}/palette/codes")
def palette_codes(item_id: str, session: Annotated[Session, Depends(get_session)]) -> list[dict]:
    """The item's palette ({hex, pct, name}) with `matches`: the nearest
    code in each enabled colour book, closest first."""
    item = items_svc.get_item(session, item_id)
    return colourbooks.palette_codes(item.dominant_colors)

