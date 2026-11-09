import json
from datetime import datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from ..config import get_settings
from ..db import get_session
from ..filetypes import serve_inline
from ..ids import new_ulid
from ..schemas import (
    FoldersPut,
    ItemCreateResponse,
    ItemOut,
    ItemPage,
    ItemPatch,
    MembershipPatch,
    TagsPut,
    UrlAdd,
)
from ..services import ingest, search, thumbs, validation
from ..services import items as items_svc
from ..services import jobs as jobs_svc

# Errors: items_svc.NotFound -> 404, validation.ValidationError -> 422,
# TransitionError -> 409, items_svc.Conflict -> 409 with the current item,
# search.BadCursor -> 400 (handlers in kist/app.py)
router = APIRouter(prefix="/items", tags=["items"])


@router.get("", response_model=ItemPage)
def list_items(
    session: Annotated[Session, Depends(get_session)],
    q: str | None = None,
    tags: Annotated[list[str] | None, Query(alias="tags[]")] = None,
    tags_csv: Annotated[str | None, Query(alias="tags")] = None,
    tags_any: str | None = None,  # csv, OR semantics
    untagged: bool = False,
    folder: str | None = None,
    type: str | None = None,
    rating: Annotated[int | None, Query(ge=0, le=5)] = None,
    deleted: bool = False,  # same as state=trashed (kept for older clients)
    state: str | None = None,  # active | trashed
    view: str | None = None,  # unfiled: no folder and no tags
    preview_status: str | None = None,  # pending | ready | failed | none
    order: str = "-created",
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: str | None = None,
):
    tag_names = list(tags or [])
    if tags_csv:
        tag_names += [t.strip() for t in tags_csv.split(",") if t.strip()]
    rows, next_cursor, total = search.search_items(
        session,
        q=q, tags=tag_names or None,
        tags_any=[t.strip() for t in tags_any.split(",") if t.strip()] if tags_any else None,
        untagged=untagged, folder=folder, kind=type,
        rating_min=rating, deleted=deleted, state=state, view=view,
        preview_status=preview_status, order=order,
        created_after=created_after, created_before=created_before,
        limit=limit, cursor=cursor,
    )
    return ItemPage(
        items=[ItemOut.from_item(i) for i in rows],
        next_cursor=next_cursor,
        total=total,
    )


@router.post("", response_model=ItemCreateResponse, status_code=201)
async def create_item(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
):
    """Upload a file (multipart form with 'file') or add from a URL (JSON {url})."""
    settings = get_settings()
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("multipart/"):
        limit = settings.max_upload_bytes
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit + 64 * 1024:  # + form overhead
            raise HTTPException(413, _too_large(limit))
        form = await request.form()
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise HTTPException(422, "multipart field 'file' is required")
        settings.staging_dir.mkdir(parents=True, exist_ok=True)
        staging = settings.staging_dir / f"up-{new_ulid()}"
        try:
            received = 0
            with staging.open("wb") as out:
                while chunk := await file.read(1024 * 1024):
                    received += len(chunk)
                    if received > limit:
                        raise HTTPException(413, _too_large(limit))
                    out.write(chunk)
            # hashing, ffmpeg/libvips/pdfium work: keep it off the event loop
            # so one slow item can't stall every other request
            result = await run_in_threadpool(
                ingest.ingest_file,
                session, staging,
                ingest.sanitize_filename(
                    str(form.get("filename") or "") or file.filename or "upload"
                ),
                folder_id=str(form.get("folder_id") or "") or None,
                folder_ids=_parse_tags(str(form.get("folder_ids") or "")),
                tag_names=_parse_tags(str(form.get("tags") or "")),
                notes=str(form.get("notes") or ""),
                source_url=str(form.get("source_url") or "") or None,
                rating=_parse_rating(form.get("rating")),
                created_at=_parse_dt(form.get("created_at")),
                modified_at=_parse_dt(form.get("modified_at")),
                deleted_at=_parse_dt(form.get("deleted_at")),
            )
        finally:
            staging.unlink(missing_ok=True)
    else:
        try:
            data = await request.json()
        except Exception:
            data = None
        if not isinstance(data, dict) or "url" not in data:
            raise HTTPException(422, "provide a multipart 'file' or a JSON body with 'url'")
        try:
            body = UrlAdd(**data)
        except PydanticValidationError as exc:
            raise RequestValidationError(exc.errors(include_url=False)) from None
        try:
            result = await run_in_threadpool(
                lambda: ingest.ingest_url(
                    session, body.url,
                    folder_id=body.folder_id, folder_ids=body.folder_ids,
                    tag_names=body.tags, notes=body.notes,
                    source_url=body.source_url, rating=body.rating,
                    created_at=body.created_at, modified_at=body.modified_at,
                    deleted_at=body.deleted_at,
                )
            )
        except (ValueError, httpx.HTTPError) as exc:
            described = ingest.describe_download_error(exc)
            if described is None:
                raise
            raise HTTPException(described[0], described[1]) from exc

    return ItemCreateResponse(item=ItemOut.from_item(result.item), created=result.created)


def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(t) for t in parsed]
    except json.JSONDecodeError:
        pass
    return [t.strip() for t in raw.split(",") if t.strip()]


def _too_large(limit: int) -> str:
    return f"the file is larger than this server accepts ({limit / 1024**3:.1f} GiB)"


def _parse_rating(raw) -> int:
    if raw is None or not str(raw).strip():
        return 0
    return validation.rating(str(raw).strip())


def _parse_dt(raw) -> datetime | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, f"invalid ISO 8601 timestamp: {raw!r}") from None


@router.get("/{item_id}", response_model=ItemOut)
def get_item(item_id: str, session: Annotated[Session, Depends(get_session)]):
    return ItemOut.from_item(items_svc.get_item(session, item_id))


@router.patch("/{item_id}", response_model=ItemOut)
def patch_item(
    item_id: str,
    patch: ItemPatch,
    session: Annotated[Session, Depends(get_session)],
):
    """Change fields. Send base_modified_at (the item's modified_at as you
    last saw it) to get a 409 instead of overwriting someone else's edit.
    folder_id / folder_ids replace every membership; to add or remove one,
    use PATCH /api/items/{id}/folders."""
    fields = patch.model_dump(exclude_unset=True)
    base = fields.pop("base_modified_at", None)
    item = items_svc.update_item(session, item_id, fields=fields, base_modified_at=base)
    return ItemOut.from_item(item)


@router.delete("/{item_id}")
def delete_item(
    item_id: str,
    session: Annotated[Session, Depends(get_session)],
    hard: bool = False,
):
    if hard:
        items_svc.hard_delete(session, item_id)
        return {"status": "deleted", "hard": True}
    item = items_svc.soft_delete(session, item_id)
    return {"status": "trashed", "item": ItemOut.from_item(item).model_dump(mode="json")}


@router.post("/{item_id}/restore", response_model=ItemOut)
def restore_item(item_id: str, session: Annotated[Session, Depends(get_session)]):
    return ItemOut.from_item(items_svc.restore(session, item_id))


@router.post("/{item_id}/preview", response_model=ItemOut, status_code=202)
def retry_preview(item_id: str, session: Annotated[Session, Depends(get_session)]):
    """Queue a fresh attempt at the item's previews (preview_status -> pending)."""
    return ItemOut.from_item(jobs_svc.retry_preview(session, item_id))


@router.put("/{item_id}/tags", response_model=ItemOut)
def put_tags(
    item_id: str,
    body: TagsPut,
    session: Annotated[Session, Depends(get_session)],
):
    return ItemOut.from_item(items_svc.set_tags(session, item_id, replace=body.tags))


@router.patch("/{item_id}/tags", response_model=ItemOut)
def patch_tags(
    item_id: str,
    body: MembershipPatch,
    session: Annotated[Session, Depends(get_session)],
):
    """Add/remove tags against what the server has (safe with other writers)."""
    return ItemOut.from_item(
        items_svc.set_tags(session, item_id, add=body.add, remove=body.remove)
    )


@router.patch("/{item_id}/folders", response_model=ItemOut)
def patch_folders(
    item_id: str,
    body: MembershipPatch,
    session: Annotated[Session, Depends(get_session)],
):
    """Add/remove folder memberships against what the server has. Unknown
    folder ids to add are refused (422); removing one is a no-op."""
    return ItemOut.from_item(
        items_svc.set_folders(session, item_id, add=body.add, remove=body.remove)
    )


@router.put("/{item_id}/folders", response_model=ItemOut)
def put_folders(
    item_id: str,
    body: FoldersPut,
    session: Annotated[Session, Depends(get_session)],
):
    return ItemOut.from_item(
        items_svc.set_folders(session, item_id, replace=body.folder_ids)
    )


@router.get("/{item_id}/file")
def get_file(item_id: str, session: Annotated[Session, Depends(get_session)]):
    item = items_svc.get_item(session, item_id)
    path = get_settings().items_dir / item.id / f"original.{item.ext}"
    if not path.is_file():
        raise HTTPException(404, "original file missing on disk")
    # originals can come from anywhere (URL downloads, mail attachments);
    # never let one run script on this origin, where it could read the API
    # token. What's shown inline is decided by the MIME type libmagic
    # sniffed from the bytes, never by the file's name: an HTML page named
    # "invoice.pdf" is a sandboxed download.
    headers = {"X-Content-Type-Options": "nosniff"}
    inline = serve_inline(item.mime)
    if "svg" in item.mime:
        headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox"
        )
    elif item.mime == "application/pdf":
        # Chromium-based browsers refuse to render a PDF in a sandboxed
        # document, which would break the preview and "open original". The
        # browsers' PDF viewers already run PDF script isolated from this
        # origin. Only framing is restricted (the preview frames it itself);
        # a stricter policy can block the viewer's own embed.
        headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    else:
        # opened on its own, an original is a sandboxed document: it can never
        # run script with this origin's storage (where the token lives)
        headers["Content-Security-Policy"] = "sandbox"
    headers["Cache-Control"] = CACHE_FOREVER  # an item's original never changes
    return FileResponse(
        path,
        media_type=item.mime,
        filename=item.filename,
        content_disposition_type="inline" if inline else "attachment",
        headers=headers,
    )


# Previews change only when preview_rev does, and every URL the UI builds
# carries ?v=<preview_rev>, so a versioned request can be cached for good.
# Originals never change at all. Both are private: they sit behind the token.
CACHE_FOREVER = "private, max-age=31536000, immutable"
CACHE_REVALIDATE = "private, no-cache"


@router.get("/{item_id}/thumb")
def get_thumb(
    item_id: str,
    session: Annotated[Session, Depends(get_session)],
    size: int = 256,
    v: int | None = None,
):
    """size 256 | 512 | 1024 thumbnails; 2048 is the full-screen copy and
    4096 the full-resolution HEIC copy. A missing copy falls back to the next
    smaller one, and that answer is never cached for good."""
    item = items_svc.get_item(session, item_id)
    item_dir = get_settings().items_dir / item.id
    if size > 2048:
        wanted = ["display.webp", thumbs.SCREEN_FILE, "thumb_1024.webp"]
    elif size > 1024:
        wanted = [thumbs.SCREEN_FILE, "display.webp", "thumb_1024.webp"]
    else:
        size = 256 if size <= 256 else 512 if size <= 512 else 1024
        wanted = [f"thumb_{size}.webp"] + (["thumb_1024.webp"] if size == 512 else [])
    for n, name in enumerate(wanted):
        path = item_dir / name
        if path.is_file():
            exact = n == 0 and v is not None and v == (item.preview_rev or 0)
            return FileResponse(
                path,
                media_type="image/webp",
                headers={"Cache-Control": CACHE_FOREVER if exact else CACHE_REVALIDATE},
            )
    raise HTTPException(404, "no thumbnail for this item")
