import json
from datetime import datetime
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from .. import access
from ..config import get_settings
from ..db import get_session
from ..filetypes import serve_inline
from ..ids import new_ulid
from ..schemas import (
    CaptionJob,
    EventOut,
    FoldersPut,
    ItemCreateResponse,
    ItemLite,
    ItemOut,
    ItemPage,
    ItemPatch,
    LinkCreate,
    LinkOut,
    MembershipPatch,
    PaletteColour,
    TagsPut,
    UrlAdd,
)
from ..services import captions, colour, ingest, search, thumbs, validation
from ..services import events as events_svc
from ..services import items as items_svc
from ..services import jobs as jobs_svc
from ..services import links as links_svc
from ..services import provenance as provenance_svc

# Errors: items_svc.NotFound -> 404, validation.ValidationError -> 422,
# TransitionError -> 409, items_svc.Conflict -> 409 with the current item,
# search.BadCursor -> 400 (handlers in kist/app.py)
router = APIRouter(prefix="/items", tags=["items"])


def search_filters(
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
    fixity: str | None = None,  # failed: originals whose fixity check failed
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: str | None = None,
    # only items whose OCR found text (true) or found none (false)
    has_text: bool | None = None,
    # colour search: a hex colour, tolerance 0-100 (default 30)
    color: Annotated[str | None, Query(max_length=9)] = None,
    color_tol: Annotated[int | None, Query(ge=0, le=100)] = None,
    # only matches inside images and PDFs (OCR / PDF text), ranked
    text: Annotated[str | None, Query(max_length=500)] = None,
    # collapse: one entry per stack (its cover), with stack_count
    stacks: Literal["collapse"] | None = None,
    person: str | None = None,  # items showing this person (faces)
    # pick | reject | none ; a colour | none
    flag: str | None = None,
    label: str | None = None,
    # M4: true | false | unknown
    ai_generated: str | None = None,
    license: Annotated[str | None, Query(max_length=200)] = None,
    # M4 link-rot watch: ok | gone | moved | error | unchecked
    source_status: str | None = None,
    # Milestone 5 media filters (services/media.filters); order=-taken sorts
    # by date taken
    taken_after: datetime | None = None,
    taken_before: datetime | None = None,
    has_gps: bool | None = None,
    camera: Annotated[str | None, Query(max_length=200)] = None,
    lens: Annotated[str | None, Query(max_length=200)] = None,
    duration_min: Annotated[float | None, Query(ge=0)] = None,
    duration_max: Annotated[float | None, Query(ge=0)] = None,
    vertical: bool | None = None,
    has_audio: bool | None = None,
    place: Annotated[str | None, Query(max_length=200)] = None,
    # the timeline's date (taken, else added) and a map area w,s,e,n
    date_after: datetime | None = None,
    date_before: datetime | None = None,
    bbox: Annotated[str | None, Query(max_length=200)] = None,
    # F4 (services/credits.filters): ref_only|comp|licensed|cleared|none|
    # uncleared; no credit or creator; prints at least / not this size at
    # 300 dpi ('8in', '20cm', 'A4')
    rights_status: Annotated[str | None, Query(max_length=100)] = None,
    needs_credit: bool | None = None,
    print_min: Annotated[str | None, Query(max_length=20)] = None,
    print_max: Annotated[str | None, Query(max_length=20)] = None,
) -> dict:
    """The filters GET /api/items and POST /api/search/image share, as
    services.search.search() keyword arguments."""
    tag_names = list(tags or [])
    if tags_csv:
        tag_names += [t.strip() for t in tags_csv.split(",") if t.strip()]
    return dict(
        q=q, tags=tag_names or None,
        tags_any=[t.strip() for t in tags_any.split(",") if t.strip()] if tags_any else None,
        untagged=untagged, folder=folder, kind=type,
        rating_min=rating, deleted=deleted, state=state, view=view,
        preview_status=preview_status, order=order,
        created_after=created_after, created_before=created_before, fixity=fixity,
        limit=limit, cursor=cursor, has_text=has_text, color=color, color_tol=color_tol,
        text=text, stacks=stacks, person=person, flag=flag, label=label,
        ai_generated=ai_generated, license=license, source_status=source_status,
        media=dict(taken_after=taken_after, taken_before=taken_before, has_gps=has_gps,
                   camera=camera, lens=lens, duration_min=duration_min,
                   duration_max=duration_max, vertical=vertical, has_audio=has_audio,
                   place=place, date_after=date_after, date_before=date_before, bbox=bbox),
        credits={k: v for k, v in dict(rights_status=rights_status, needs_credit=needs_credit,
                                       print_min=print_min, print_max=print_max).items()
                 if v is not None} or None,
    )


def page_out(result: search.SearchResult) -> ItemPage:
    return ItemPage(
        items=[ItemOut.from_item(i, result.matches.get(i.id)) for i in result.items],
        next_cursor=result.next_cursor,
        total=result.total,
    )


@router.get("", response_model=ItemPage)
def list_items(
    session: Annotated[Session, Depends(get_session)],
    filters: Annotated[dict, Depends(search_filters)],
    # rank by meaning: nearest to this text first (needs the ml service)
    semantic: Annotated[str | None, Query(max_length=1000)] = None,
    # more like this: nearest to that item's embedding
    similar: Annotated[str | None, Query(max_length=26)] = None,
    # compare: these items, in this order; every other parameter is ignored
    ids: Annotated[str | None, Query(max_length=3000)] = None,
):
    """Search. q, tags, folder, type and the rest filter; semantic, similar,
    color and text rank (services/ranking.py), and each ranked item carries
    `match` {kind, score, snippet?}. ids=a,b,c fetches those items instead."""
    if ids is not None:
        return _items_by_ids(session, ids)
    return page_out(search.search(session, semantic=semantic, similar=similar, **filters))


def _items_by_ids(session: Session, raw: str) -> ItemPage:
    """GET /api/items?ids=a,b,c: those items in that order (any state);
    ids that don't exist or the caller can't see are left out."""
    wanted = list(dict.fromkeys(i.strip() for i in raw.split(",") if i.strip()))
    if len(wanted) > 100:
        raise HTTPException(422, "at most 100 ids")
    out = []
    for item_id in wanted:
        try:
            out.append(ItemOut.from_item(items_svc.get_item(session, item_id)))
        except items_svc.NotFound:
            continue
    return ItemPage(items=out, next_cursor=None, total=len(out))


@router.post("", response_model=ItemCreateResponse, status_code=201)
async def create_item(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
):
    """Upload a file (multipart form with 'file') or add from a URL (JSON {url}).

    Provenance comes as a `provenance` object (JSON, or a JSON string form
    field) and/or flat fields: page_url, image_url, title, captured_at,
    method (extension | bookmarklet | ...), creator/author, credit, site,
    license, rights_url/license_url (services/provenance.from_flat)."""
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
                # flat fields (the browser extension), then a provenance object
                provenance={**provenance_svc.from_flat(form),
                            **(_parse_provenance(form.get("provenance")) or {})},
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
        # flat keys (the extension, the /add page), then a provenance object
        prov = {**provenance_svc.from_flat(data), **(body.provenance or {})}
        notes = body.notes
        if body.title and not prov.get("page_title"):
            prov["page_title"] = " ".join(body.title.split())[:2000]
        if body.text and body.text.strip() and body.text.strip() != body.url:
            notes = (notes + "\n\n" if notes else "") + body.text.strip()
            notes = validation.notes(notes)
        try:
            result = await run_in_threadpool(
                lambda: ingest.ingest_url(
                    session, body.url,
                    folder_id=body.folder_id, folder_ids=body.folder_ids,
                    tag_names=body.tags, notes=notes,
                    source_url=body.source_url, rating=body.rating,
                    created_at=body.created_at, modified_at=body.modified_at,
                    deleted_at=body.deleted_at, provenance=prov,
                )
            )
        except (ValueError, httpx.HTTPError) as exc:
            described = ingest.describe_download_error(exc)
            if described is None:
                raise
            raise HTTPException(described[0], described[1]) from exc

    if _wants_plain_text(request):
        # an iOS Shortcut shows this as its result banner
        verb = "Saved to Kist" if result.created else "Already in Kist"
        return PlainTextResponse(
            f"{verb}: {result.item.filename}", status_code=201 if result.created else 200
        )
    return ItemCreateResponse(item=ItemOut.from_item(result.item), created=result.created)


def _wants_plain_text(request: Request) -> bool:
    accept = request.headers.get("accept", "").lower()
    return accept.startswith("text/plain")


def _parse_provenance(raw) -> dict | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        data = json.loads(str(raw))
    except json.JSONDecodeError:
        raise HTTPException(422, "provenance must be a JSON object") from None
    if not isinstance(data, dict):
        raise HTTPException(422, "provenance must be a JSON object")
    return data


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


@router.get("/{item_id}/palette", response_model=list[PaletteColour])
def get_palette(item_id: str, session: Annotated[Session, Depends(get_session)]):
    """The item's dominant colours, largest share first, each with the
    nearest everyday colour name. Search one with ?color=<hex>."""
    return colour.palette(items_svc.get_item(session, item_id).dominant_colors)


@router.post("/{item_id}/caption", response_model=CaptionJob, status_code=202)
def request_caption(item_id: str, session: Annotated[Session, Depends(get_session)]):
    """Queue a caption for this item now (KIST_CAPTION_PROVIDER). It
    replaces the current caption; PATCH {caption} edits it instead."""
    job = captions.request(session, item_id)
    return CaptionJob(job_id=job.id, status=job.status,
                      provider=get_settings().caption_provider or None,
                      warning=captions.warning())


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
        access.require(access.Action.HARD_DELETE)
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
    if item.mime.startswith("font/"):
        # @font-face specimens load fonts from here: same origin only
        headers["Cross-Origin-Resource-Policy"] = "same-origin"
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


@router.get("/{item_id}/history", response_model=list[EventOut])
def item_history(
    item_id: str,
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    """Every change to the item, newest first (create, edits, tags, folders,
    trash, restore, links, protect). Revert one with POST /api/events/{id}/revert."""
    items_svc.get_item(session, item_id)
    return events_svc.history(session, item_id, limit)


@router.get("/{item_id}/links", response_model=list[LinkOut])
def item_links(item_id: str, session: Annotated[Session, Depends(get_session)]):
    items_svc.get_item(session, item_id)
    return [
        LinkOut(item=ItemLite.from_item(other), kind=kind, direction=direction)
        for other, kind, direction in links_svc.links_of(session, item_id)
    ]


@router.post("/{item_id}/links", response_model=list[LinkOut], status_code=201)
def add_link(
    item_id: str, body: LinkCreate, session: Annotated[Session, Depends(get_session)]
):
    """Link this item to dst_id (kind: derived_from, alternate, related,
    not_duplicate, version_of). Linking twice is a no-op. Returns the links."""
    links_svc.link(session, item_id, body.dst_id, body.kind)
    return item_links(item_id, session)


@router.delete("/{item_id}/links/{dst_id}")
def remove_link(
    item_id: str,
    dst_id: str,
    session: Annotated[Session, Depends(get_session)],
    kind: str | None = None,
):
    """Remove this item's links to dst_id (only those of `kind`, if given)."""
    return {"removed": links_svc.unlink(session, item_id, dst_id, kind)}


# ---------- Milestone 2: tag suggestions and faces ----------


class TagFeedbackIn(BaseModel):
    tag: str
    accepted: bool


@router.get("/{item_id}/tag-suggestions")
def tag_suggestions(
    item_id: str,
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[dict]:
    """[{tag, score, why}]: why is "similar items" (tags of the nearest
    look-alikes) or "matches the image" (zero-shot over the tag names).
    Never applied by itself; empty until the item has an embedding."""
    from ..services import suggestions

    return suggestions.suggest(session, item_id, limit)


@router.post("/{item_id}/tag-suggestions/feedback")
def tag_suggestion_feedback(
    item_id: str, body: TagFeedbackIn, session: Annotated[Session, Depends(get_session)]
) -> dict:
    """Remember that a suggestion was accepted or rejected for this item
    (it isn't suggested for it again). Accepting doesn't add the tag: PATCH
    /api/items/{id}/tags does."""
    from ..services import suggestions

    return suggestions.feedback(session, item_id, body.tag, body.accepted)


@router.get("/{item_id}/faces")
def item_faces(item_id: str, session: Annotated[Session, Depends(get_session)]) -> list[dict]:
    """Faces found in the item (boxes as fractions of the image) and who
    they are."""
    from ..services import faces

    return faces.item_faces(session, item_id)
