"""Milestone 3 endpoints that hang off items, plus templates, triage and
the command palette (boards are in api/boards.py)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from ..config import get_settings
from ..db import get_session
from ..ids import new_ulid
from ..schemas import (
    AnnotationIn,
    AnnotationOut,
    AnnotationPatch,
    BoardSummary,
    ItemCreateResponse,
    ItemOut,
    PaletteResult,
    RenameIn,
    RenameOut,
    TemplateApply,
    TemplateIn,
    TemplateOut,
    TriageOut,
    VersionsOut,
)
from ..services import annotations as ann_svc
from ..services import boards as boards_svc
from ..services import events as events_svc
from ..services import ingest, palette, triage
from ..services import templates as tpl_svc
from ..services import versions as versions_svc

router = APIRouter(tags=["creative"])


# ---------- annotations ----------


@router.get("/items/{item_id}/annotations", response_model=list[AnnotationOut])
def list_annotations(item_id: str, session: Annotated[Session, Depends(get_session)]):
    return [ann_svc.as_dict(a) for a in ann_svc.list_for(session, item_id)]


@router.post("/items/{item_id}/annotations", response_model=AnnotationOut, status_code=201)
def add_annotation(item_id: str, body: AnnotationIn,
                   session: Annotated[Session, Depends(get_session)]):
    return ann_svc.as_dict(ann_svc.create(session, item_id, body.model_dump()))


@router.patch("/items/{item_id}/annotations/{ann_id}", response_model=AnnotationOut)
def patch_annotation(item_id: str, ann_id: str, body: AnnotationPatch,
                     session: Annotated[Session, Depends(get_session)]):
    return ann_svc.as_dict(
        ann_svc.update(session, item_id, ann_id, body.model_dump(exclude_unset=True))
    )


@router.delete("/items/{item_id}/annotations/{ann_id}")
def delete_annotation(item_id: str, ann_id: str,
                      session: Annotated[Session, Depends(get_session)]):
    ann_svc.delete(session, item_id, ann_id)
    return {"status": "deleted"}


# ---------- versions ----------


@router.get("/items/{item_id}/versions", response_model=VersionsOut)
def list_versions(item_id: str, session: Annotated[Session, Depends(get_session)]):
    chain = versions_svc.chain(session, item_id)
    live = [i for i in chain if i.deleted_at is None]
    return VersionsOut(items=[ItemOut.from_item(i) for i in chain],
                       current_id=(live or chain)[-1].id if chain else item_id)


@router.post("/items/{item_id}/versions", response_model=ItemCreateResponse, status_code=201)
async def add_version(item_id: str, request: Request,
                      session: Annotated[Session, Depends(get_session)]):
    """Upload a new version (multipart 'file'): linked version_of the
    current head of the chain, with its folders and tags."""
    settings = get_settings()
    if not request.headers.get("content-type", "").startswith("multipart/"):
        raise HTTPException(422, "send the file as multipart field 'file'")
    await run_in_threadpool(versions_svc.chain, session, item_id)  # 404 before reading
    limit = settings.max_upload_bytes
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
                    raise HTTPException(413, "the file is larger than this server accepts")
                out.write(chunk)
        result = await run_in_threadpool(
            versions_svc.add_version, session, item_id, staging,
            ingest.sanitize_filename(file.filename or "upload"),
        )
    finally:
        staging.unlink(missing_ok=True)
    return ItemCreateResponse(item=ItemOut.from_item(result.item), created=result.created)


# ---------- boards an item is on ----------


@router.get("/items/{item_id}/boards", response_model=list[BoardSummary])
def item_boards(item_id: str, session: Annotated[Session, Depends(get_session)]):
    return [boards_svc.summary(b) for b in boards_svc.boards_of_item(session, item_id)]


# ---------- bulk rename ----------


@router.post("/items/rename", response_model=RenameOut)
def rename_items(body: RenameIn, session: Annotated[Session, Depends(get_session)]):
    """Rename items from a pattern ({name} {ext} {n:3} {date:%Y%m%d} {w}
    {h} {folder} {tag:ns}). dry_run (the default) only shows before/after.
    The renames are one batch: undo it with POST /api/batches/{batch_id}/undo."""
    rows = tpl_svc.rename(session, body.item_ids, body.pattern, dry_run=body.dry_run,
                          start=body.start)
    return RenameOut(dry_run=body.dry_run, items=rows,
                     batch_id=None if body.dry_run else events_svc.context().batch_id)


# ---------- templates ----------


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(session: Annotated[Session, Depends(get_session)]):
    return tpl_svc.list_templates(session)


@router.post("/templates", response_model=TemplateOut, status_code=201)
def create_template(body: TemplateIn, session: Annotated[Session, Depends(get_session)]):
    return tpl_svc.create(session, body.name, body.fields.model_dump(exclude_none=True))


@router.put("/templates/{template_id}", response_model=TemplateOut)
def update_template(template_id: str, body: TemplateIn,
                    session: Annotated[Session, Depends(get_session)]):
    return tpl_svc.update(session, template_id, body.name,
                          body.fields.model_dump(exclude_none=True))


@router.delete("/templates/{template_id}")
def delete_template(template_id: str, session: Annotated[Session, Depends(get_session)]):
    tpl_svc.delete(session, template_id)
    return {"status": "deleted"}


@router.post("/templates/{template_id}/apply")
def apply_template(template_id: str, body: TemplateApply,
                   session: Annotated[Session, Depends(get_session)]):
    """Apply to every item as one batch (undo: POST /api/batches/{batch_id}/undo)."""
    items = tpl_svc.apply(session, template_id, body.item_ids)
    return {"items": [ItemOut.from_item(i).model_dump(mode="json") for i in items],
            "batch_id": events_svc.context().batch_id}


# ---------- triage and palette ----------


@router.get("/triage/next", response_model=TriageOut)
def triage_next(
    session: Annotated[Session, Depends(get_session)],
    view: str | None = "unfiled",
    after: str | None = None,
    order: str = "-created",
    prefetch: Annotated[int, Query(ge=0, le=20)] = 5,
    folder: str | None = None,
    flag: str | None = None,
    label: str | None = None,
    undecided: bool | None = None,
):
    """The next item to triage in a view (default Unfiled) after `after`,
    the ids to prefetch, and how many are left. The Unfiled queue skips items
    already decided on (flagged, labelled or rated); undecided=true/false
    overrides that for any view."""
    out = triage.next_item(session, view=view, after=after, order=order, prefetch=prefetch,
                           folder=folder, flag=flag, label=label, undecided=undecided)
    return TriageOut(item=ItemOut.from_item(out["item"]) if out["item"] else None,
                     prefetch_ids=out["prefetch_ids"], remaining=out["remaining"])


@router.get("/palette", response_model=list[PaletteResult])
def command_palette(session: Annotated[Session, Depends(get_session)],
                    q: Annotated[str, Query(max_length=200)] = ""):
    """Folders, smart folders, tags, boards, items (top 5 each) and actions."""
    return palette.palette(session, q)
