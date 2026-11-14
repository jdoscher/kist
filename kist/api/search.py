"""Search by image: POST /api/search/image.

    multipart field "file": an image (not stored)
    query params: the same filters as GET /api/items (q, tags, folder, type,
                  color, text, ...), limit and cursor

The file is embedded by the ml service and thrown away; the result is the
same page as GET /api/items?similar=..., each item with match.kind
"similar". Needs only the read scope (it changes nothing).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from ..db import get_session
from ..schemas import SearchPage
from ..services import ai, search
from .items import Fields, grid_page_response, page_out, search_filters

router = APIRouter(prefix="/search", tags=["search"])

MAX_IMAGE_BYTES = 32 * 1024 * 1024


@router.post("/image", response_model=SearchPage)
async def search_by_image(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    filters: Annotated[dict, Depends(search_filters)],
    fields: Fields = None,  # grid: slim items, as GET /api/items?fields=grid
):
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_IMAGE_BYTES + 64 * 1024:
        raise HTTPException(413, "the image is larger than 32 MB")
    form = await request.form()
    file = form.get("file")
    if not isinstance(file, UploadFile):
        raise HTTPException(422, "multipart field 'file' (an image) is required")
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "the image is larger than 32 MB")
    if not data:
        raise HTTPException(422, "the file is empty")
    try:
        vec = await run_in_threadpool(ai.ml_embed_image, data)  # AiUnavailable -> 503
    except ai.AiUnavailable:
        raise
    except ValueError as exc:
        raise HTTPException(422, f"that file can't be read as an image: {exc}") from None
    result = await run_in_threadpool(
        lambda: search.search(session, image_vector=vec, **filters)
    )
    if fields == "grid":
        return grid_page_response(result)
    page = page_out(result)
    return SearchPage(items=page.items, next_cursor=page.next_cursor, total=page.total)
