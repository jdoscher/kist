"""M4 REST routes: exports, export presets, imports, page snapshots and the
iOS Shortcut helper."""

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import access
from ..auth import require_scope
from ..config import get_settings
from ..db import get_session
from ..lifecycle import JobKind
from ..models import Item, Job
from ..services import export_images, exports, snapshots, validation
from ..services import items as items_svc
from ..services import jobs as jobs_svc

router = APIRouter()


def _who() -> str:
    p = access.current()
    return p.name if p is not None else "cli"


# ---------- portable export (BagIt) ----------


class ExportCreate(BaseModel):
    target: str = "all"  # folder | smart | board | items | all
    target_id: str | None = None
    item_ids: list[str] | None = None
    include: dict[str, bool] = {}
    format: str = "bagit-zip"


@router.post("/exports", status_code=202, tags=["exports"])
def create_export(body: ExportCreate, session: Annotated[Session, Depends(get_session)]):
    """Queue a portable BagIt export; poll GET /api/exports/{id}."""
    access.require(access.Action.EDIT)
    record = exports.create(session, target=body.target, target_id=body.target_id,
                            item_ids=body.item_ids, include=body.include,
                            format=body.format, created_by=_who())
    return exports.out(record)


@router.get("/exports", tags=["exports"])
def list_exports() -> list[dict[str, Any]]:
    return [exports.out(r) for r in exports.list_exports()]


def _export(export_id: str) -> dict:
    try:
        return exports.get(export_id)
    except exports.NotFound as exc:
        raise HTTPException(404, str(exc)) from None


@router.get("/exports/{export_id}", tags=["exports"])
def get_export(export_id: str, download: bool = False):
    """The export's status; with ?download=1 (or /download) the zip."""
    record = _export(export_id)
    if download:
        return download_export(export_id)
    return exports.out(record)


@router.get("/exports/{export_id}/download", tags=["exports"])
def download_export(export_id: str):
    record = _export(export_id)
    path = exports.zip_path(export_id)
    if record.get("status") != "done" or not path.is_file():
        raise HTTPException(409, f"export {export_id} is {record.get('status')}, not ready")
    return FileResponse(path, media_type="application/zip",
                        filename=f"kist-export-{export_id}.zip",
                        headers={"Cache-Control": "private, no-cache"})


@router.delete("/exports/{export_id}", tags=["exports"])
def delete_export(export_id: str):
    access.require(access.Action.EDIT)
    _export(export_id)
    exports.delete(export_id)
    return {"status": "deleted"}


# ---------- export presets ----------


class ImageExport(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=export_images.MAX_ITEMS)
    # a saved preset's name, or a preset object
    preset: str | dict[str, Any] = "web-2048"


@router.post("/items/export-images", tags=["exports"])
async def export_item_images(body: ImageExport,
                             session: Annotated[Session, Depends(get_session)]):
    """Resized/converted copies (a zip) or a contact sheet (a PDF)."""
    data, media_type, name = await run_in_threadpool(
        export_images.export, session, body.item_ids, body.preset)
    return Response(data, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="{name}"',
        "Cache-Control": "no-store",
    })


@router.get("/export-presets", tags=["exports"])
def list_presets() -> list[dict[str, Any]]:
    return list(export_images.list_presets().values())


@router.put("/export-presets/{name}", tags=["exports"])
def put_preset(name: str, body: dict[str, Any]):
    access.require(access.Action.EDIT)
    return export_images.put_preset(name, body)


@router.post("/export-presets", status_code=201, tags=["exports"])
def create_preset(body: dict[str, Any]):
    access.require(access.Action.EDIT)
    if not body.get("name"):
        raise validation.ValidationError("a preset needs a name")
    return export_images.put_preset(str(body["name"]), body)


@router.delete("/export-presets/{name}", tags=["exports"])
def delete_preset(name: str):
    access.require(access.Action.EDIT)
    export_images.delete_preset(name)
    return {"status": "deleted"}


# ---------- imports ----------


class ImportCreate(BaseModel):
    kind: str
    source: str = Field(min_length=1, max_length=4000)  # a path in an import dir, or a URL
    dry_run: bool = False
    folder: str | None = Field(default=None, max_length=validation.FOLDER_NAME_MAX)


def _import_out(job: Job) -> dict:
    payload = job.payload or {}
    return {
        "id": job.id, "status": job.status, "kind": payload.get("kind"),
        "source": payload.get("source"), "dry_run": bool(payload.get("dry_run")),
        "report": payload.get("report"), "last_error": job.last_error,
        "created_at": job.created_at, "finished_at": job.finished_at,
    }


@router.post("/imports", status_code=202, tags=["imports"],
             dependencies=[Depends(require_scope("write"))])
def create_import(body: ImportCreate, session: Annotated[Session, Depends(get_session)]):
    """Queue an importer run (a job); GET /api/imports/{id} shows progress."""
    from ..importers import base

    base.get_importer(body.kind)  # unknown kind -> 422
    base.check_api_source(body.source, body.kind)
    job = Job(kind=JobKind.IMPORT, payload={
        "kind": body.kind, "source": body.source, "dry_run": body.dry_run,
        "folder": body.folder, "filename": f"{body.kind} import",
    })
    session.add(job)
    session.commit()
    return _import_out(job)


@router.post("/imports/upload", tags=["imports"],
             dependencies=[Depends(require_scope("write"))])
async def upload_import(request: Request):
    """An export file (a zip, CSV or HTML) sent from the browser, kept in
    library/import/uploads/<id>/ so POST /api/imports can read it; answers
    {source}. Multipart field 'file'."""
    import re
    import shutil

    from ..ids import new_ulid

    settings = get_settings()
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > settings.max_upload_bytes:
        raise HTTPException(413, "that file is larger than Kist accepts")
    form = await request.form(max_files=1)
    file = form.get("file")
    if file is None or isinstance(file, str):
        raise HTTPException(422, "multipart field 'file' is required")
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", (file.filename or "upload").rsplit("/", 1)[-1]).strip(" .")
    name = (name or "upload")[:200]
    dest_dir = settings.library_dir / "import" / "uploads" / new_ulid()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name

    def _copy():
        with dest.open("wb") as out:
            shutil.copyfileobj(file.file, out, 1024 * 1024)

    await run_in_threadpool(_copy)
    return {"source": str(dest), "size_bytes": dest.stat().st_size, "filename": name}


@router.get("/imports/{job_id}", tags=["imports"])
def get_import(job_id: int, session: Annotated[Session, Depends(get_session)]):
    job = jobs_svc.get_job(session, job_id)
    if job.kind != JobKind.IMPORT:
        raise HTTPException(404, f"import {job_id} not found")
    return _import_out(job)


# ---------- page snapshots ----------


@router.post("/items/{item_id}/snapshot", tags=["items"])
async def save_snapshot(item_id: str, request: Request,
                        session: Annotated[Session, Depends(get_session)]):
    """Body: JSON {html, page_url?, title?, captured_at?} (the browser
    extension) or a raw text/html body. No body: the server fetches page_url
    once (KIST_SNAPSHOTS=1). Needs an edit scope, or an add-only token that
    added this item itself under SNAPSHOT_WINDOW ago."""
    _may_snapshot(session, item_id)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > snapshots.MAX_HTML_BYTES + 64 * 1024:
        raise HTTPException(413, "snapshots are at most 25 MB")
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > snapshots.MAX_HTML_BYTES + 64 * 1024:
            raise HTTPException(413, "snapshots are at most 25 MB")
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    html = None
    extra: dict = {}
    if raw.strip():
        if ctype in ("text/html", "application/xhtml+xml", "text/plain"):
            html = raw.decode("utf-8", errors="replace")
        else:
            try:
                data = json.loads(raw)
            except ValueError:
                raise HTTPException(422, "send JSON {html} or a text/html body") from None
            if isinstance(data, dict) and isinstance(data.get("html"), str):
                html = data["html"]
                extra = snapshots.client_fields(data)
            elif not (isinstance(data, dict) and data.get("html") is None):
                raise HTTPException(422, "html must be a string")
    if html is not None:
        if html is None or not html.strip():
            raise HTTPException(422, "the snapshot's html is empty")
        info = await run_in_threadpool(lambda: snapshots.save(
            session, item_id, html, source="browser", url=extra.get("page_url"),
            title=extra.get("title"), captured_at=extra.get("captured_at")))
    else:
        try:
            info = await run_in_threadpool(snapshots.fetch, session, item_id)
        except snapshots.SnapshotsOff as exc:
            raise HTTPException(409, str(exc)) from None
        except Exception as exc:
            from ..services import ingest

            described = ingest.describe_download_error(exc)
            if described is None:
                raise
            raise HTTPException(described[0], described[1]) from exc
    return info


SNAPSHOT_WINDOW = timedelta(minutes=10)


def _may_snapshot(session: Session, item_id: str) -> None:
    """An edit scope may snapshot any item. An add-only token (the browser
    extension's recommended token) only an item it added itself in the
    last SNAPSHOT_WINDOW (items.added_by_token): enough to save the page
    right after the image, and nothing it could use to overwrite another
    item's snapshot. Anything else is 403, whether or not the item exists."""
    p = access.current()
    if p is None or p.can(access.Action.EDIT):
        return
    if p.can(access.Action.ADD) and p.token_id:
        item = session.get(Item, item_id)
        if (item is not None and item.deleted_at is None
                and item.added_by_token == p.token_id
                and item.created_at >= datetime.now(UTC) - SNAPSHOT_WINDOW):
            return
    raise access.Forbidden()


@router.get("/items/{item_id}/snapshot", tags=["items"])
def get_snapshot(item_id: str, session: Annotated[Session, Depends(get_session)]):
    """The saved page, sandboxed: nothing in it can run on Kist's origin."""
    items_svc.get_item(session, item_id)
    found = snapshots.read(item_id)
    if found is None:
        raise HTTPException(404, "no snapshot for this item")
    path, _info = found
    return FileResponse(path, media_type="text/html; charset=utf-8", headers={
        "Content-Security-Policy": snapshots.SNAPSHOT_CSP,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "private, no-cache",
    })


@router.get("/items/{item_id}/snapshot/text", tags=["items"])
def get_snapshot_text(item_id: str, session: Annotated[Session, Depends(get_session)]):
    items_svc.get_item(session, item_id)
    text = snapshots.text_of(item_id)
    if text is None:
        raise HTTPException(404, "no snapshot for this item")
    return PlainTextResponse(text)


# ---------- the iOS Shortcut ----------


@router.get("/shortcut", tags=["misc"], dependencies=[Depends(require_scope("admin"))])
def shortcut(request: Request):
    """How to build the "Save to Kist" share-sheet Shortcut for this server.

    A ready-made .shortcut file isn't served: iOS 15+ only opens Shortcuts
    signed by Apple's servers (`shortcuts sign` on a Mac), which a server
    can't do. The steps below are exact; docs/ios-shortcut.md has the long
    version."""
    base = get_settings().public_url.rstrip("/") or str(request.base_url).rstrip("/")
    endpoint = f"{base}/api/items"
    return {
        "file_available": False,
        "why": "iOS only opens .shortcut files signed by Apple (`shortcuts sign` on a "
               "Mac); build it once from these steps instead.",
        "endpoint": endpoint,
        "token": "make an add-only token: POST /api/tokens {name: 'iPhone', scopes: ['add']}",
        "steps": [
            "Shortcuts app → + → name it 'Save to Kist'; in its settings turn on "
            "'Show in Share Sheet' and accept Images, Media, Files, PDFs, URLs, Safari web "
            "pages and Text.",
            "If 'Shortcut Input' has no value: Stop and respond 'Nothing to save'.",
            "Add 'If Shortcut Input has any value', and inside it 'Get URLs from Shortcut "
            "Input'.",
            "If URLs has any value: 'Get Contents of URL' → URL " + endpoint
            + ", Method POST, Headers Authorization = 'Bearer <add-only token>' and "
            "Accept = 'text/plain', Request Body JSON: url = the URL, title = Shortcut "
            "Input's Name, text = Shortcut Input (as text).",
            "Otherwise: 'Get Contents of URL' → URL " + endpoint + ", Method POST, the "
            "same two headers, Request Body Form: file (File) = Shortcut Input.",
            "End with 'Show Notification' of 'Contents of URL' (Kist answers "
            "'Saved to Kist: <name>' or 'Already in Kist: <name>').",
        ],
    }
