import json
from datetime import datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from ..config import get_settings
from ..db import get_session
from ..filetypes import kind_for
from ..ids import new_ulid
from ..schemas import (
    FoldersPut,
    ItemCreateResponse,
    ItemOut,
    ItemPage,
    ItemPatch,
    TagsPut,
    UrlAdd,
)
from ..services import ingest, search
from ..services import items as items_svc

router = APIRouter(prefix="/items", tags=["items"])


def _get_or_404(session: Session, item_id: str):
    try:
        return items_svc.get_item(session, item_id)
    except items_svc.NotFound:
        raise HTTPException(404, f"item {item_id} not found") from None


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
    deleted: bool = False,
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
        rating_min=rating, deleted=deleted, order=order,
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
        form = await request.form()
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise HTTPException(422, "multipart field 'file' is required")
        settings.staging_dir.mkdir(parents=True, exist_ok=True)
        staging = settings.staging_dir / f"up-{new_ulid()}"
        try:
            with staging.open("wb") as out:
                while chunk := await file.read(1024 * 1024):
                    out.write(chunk)
            result = ingest.ingest_file(
                session, staging,
                ingest.sanitize_filename(
                    str(form.get("filename") or "") or file.filename or "upload"
                ),
                folder_id=str(form.get("folder_id") or "") or None,
                folder_ids=_parse_tags(str(form.get("folder_ids") or "")),
                tag_names=_parse_tags(str(form.get("tags") or "")),
                notes=str(form.get("notes") or ""),
                source_url=str(form.get("source_url") or "") or None,
                rating=_parse_int(form.get("rating")),
                created_at=_parse_dt(form.get("created_at")),
                modified_at=_parse_dt(form.get("modified_at")),
                deleted_at=_parse_dt(form.get("deleted_at")),
            )
        finally:
            staging.unlink(missing_ok=True)
    else:
        try:
            body = UrlAdd(**await request.json())
        except Exception:
            raise HTTPException(
                422, "provide a multipart 'file' or a JSON body with 'url'"
            ) from None
        try:
            result = ingest.ingest_url(
                session, body.url,
                folder_id=body.folder_id, folder_ids=body.folder_ids,
                tag_names=body.tags, notes=body.notes,
                source_url=body.source_url, rating=body.rating,
                created_at=body.created_at, modified_at=body.modified_at,
                deleted_at=body.deleted_at,
            )
        except ingest.NotMediaError as exc:
            raise HTTPException(422, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(413, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"download failed: {exc}") from exc

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


def _parse_int(raw) -> int:
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return 0


def _parse_dt(raw) -> datetime | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, f"invalid ISO 8601 timestamp: {raw!r}") from None


@router.get("/{item_id}", response_model=ItemOut)
def get_item(item_id: str, session: Annotated[Session, Depends(get_session)]):
    return ItemOut.from_item(_get_or_404(session, item_id))


@router.patch("/{item_id}", response_model=ItemOut)
def patch_item(
    item_id: str,
    patch: ItemPatch,
    session: Annotated[Session, Depends(get_session)],
):
    _get_or_404(session, item_id)
    fields = patch.model_dump(exclude_unset=True)
    item = items_svc.update_item(session, item_id, fields=fields)
    return ItemOut.from_item(item)


@router.delete("/{item_id}")
def delete_item(
    item_id: str,
    session: Annotated[Session, Depends(get_session)],
    hard: bool = False,
):
    _get_or_404(session, item_id)
    if hard:
        items_svc.hard_delete(session, item_id)
        return {"status": "deleted", "hard": True}
    item = items_svc.soft_delete(session, item_id)
    return {"status": "trashed", "item": ItemOut.from_item(item).model_dump(mode="json")}


@router.post("/{item_id}/restore", response_model=ItemOut)
def restore_item(item_id: str, session: Annotated[Session, Depends(get_session)]):
    _get_or_404(session, item_id)
    return ItemOut.from_item(items_svc.restore(session, item_id))


@router.put("/{item_id}/tags", response_model=ItemOut)
def put_tags(
    item_id: str,
    body: TagsPut,
    session: Annotated[Session, Depends(get_session)],
):
    _get_or_404(session, item_id)
    return ItemOut.from_item(items_svc.set_tags(session, item_id, replace=body.tags))


@router.put("/{item_id}/folders", response_model=ItemOut)
def put_folders(
    item_id: str,
    body: FoldersPut,
    session: Annotated[Session, Depends(get_session)],
):
    _get_or_404(session, item_id)
    return ItemOut.from_item(
        items_svc.set_folders(session, item_id, replace=body.folder_ids)
    )


@router.get("/{item_id}/file")
def get_file(item_id: str, session: Annotated[Session, Depends(get_session)]):
    item = _get_or_404(session, item_id)
    path = get_settings().items_dir / item.id / f"original.{item.ext}"
    if not path.is_file():
        raise HTTPException(404, "original file missing on disk")
    # originals can come from anywhere (URL downloads); never let one run
    # script on this origin, where it could read the API token
    headers = {"X-Content-Type-Options": "nosniff"}
    kind = kind_for(item.mime, item.ext)
    inline = kind in ("image", "video", "pdf")
    if item.ext == "svg" or "svg" in item.mime:
        headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox"
        )
    return FileResponse(
        path,
        media_type=item.mime,
        filename=item.filename,
        content_disposition_type="inline" if inline else "attachment",
        headers=headers,
    )


@router.get("/{item_id}/thumb")
def get_thumb(
    item_id: str,
    session: Annotated[Session, Depends(get_session)],
    size: int = 256,
):
    item = _get_or_404(session, item_id)
    item_dir = get_settings().items_dir / item.id
    if size > 1024:  # full-res display copy (HEIC); falls back to 1024
        path = item_dir / "display.webp"
        if not path.is_file():
            path = item_dir / "thumb_1024.webp"
        if path.is_file():
            return FileResponse(path, media_type="image/webp")
    size = 256 if size <= 256 else 512 if size <= 512 else 1024
    path = item_dir / f"thumb_{size}.webp"
    if not path.is_file() and size == 512:
        path = item_dir / "thumb_1024.webp"  # not yet backfilled
    if not path.is_file():
        raise HTTPException(404, "no thumbnail for this item")
    return FileResponse(path, media_type="image/webp")
