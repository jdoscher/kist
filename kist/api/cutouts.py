"""F7 cutouts: REST (services/cutouts.py).

    GET  /api/cutouts/status                  on? which models, downloaded?
    POST /api/items/{id}/cutout               queue one (202, the job)
    POST /api/cutouts                         a selection {item_ids, ...}
    GET  /api/items/{id}/cutouts              cut-outs made from the item
    GET  /api/items/{id}/cutout-preview       transparent WebP for boards
    POST /api/boards/{id}/nodes/{node}/cutout swap the node to a cut-out
                                              (now, or when its job is done)
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import access
from ..db import get_session
from ..schemas import ItemOut, JobOut
from ..services import cutouts
from ..services import items as items_svc
from ..services import jobs as jobs_svc
from . import thumbfiles

router = APIRouter(tags=["cutouts"])


class CutoutIn(BaseModel):
    # isnet-general-use (fast) | birefnet-lite (fine edges); empty: the default
    model: str | None = None
    feather: float = Field(0.0, ge=0, le=cutouts.FEATHER_MAX)
    shadow: bool = False


class CutoutManyIn(CutoutIn):
    item_ids: list[str] = Field(min_length=1, max_length=500)


@router.get("/cutouts/status")
def cutout_status() -> dict:
    """Whether "Cut out subject" is on and each model's download state
    (missing: the first cut-out downloads it; size_bytes says how much)."""
    return cutouts.status()


@router.post("/items/{item_id}/cutout", response_model=JobOut, status_code=202)
def make_cutout(item_id: str, body: CutoutIn, session: Annotated[Session, Depends(get_session)]):
    """Cut the subject out of an image: a job whose result_item_id is the new
    transparent PNG item (derived_from this one; tags, folders and
    provenance copied; this item is never changed)."""
    access.require(access.Action.EDIT)
    job = cutouts.queue(session, item_id, model=body.model, feather=body.feather,
                        shadow=body.shadow)
    return jobs_svc.job_out(job)


@router.post("/cutouts", status_code=202)
def make_cutouts(body: CutoutManyIn, session: Annotated[Session, Depends(get_session)]) -> dict:
    """Cut out every item of a selection. Items that can't be cut out (not
    an image, no preview yet, in the trash) come back in skipped."""
    access.require(access.Action.EDIT)
    jobs, skipped = cutouts.queue_many(session, body.item_ids, model=body.model,
                                       feather=body.feather, shadow=body.shadow)
    return {"jobs": [jobs_svc.job_out(j).model_dump(mode="json") for j in jobs],
            "skipped": skipped}


@router.get("/items/{item_id}/cutouts", response_model=list[ItemOut])
def list_cutouts(item_id: str, session: Annotated[Session, Depends(get_session)]):
    return [ItemOut.from_item(i) for i in cutouts.cutouts_of(session, item_id)]


@router.get("/items/{item_id}/cutout-preview")
async def cutout_preview(item_id: str, size: int = 1024, v: int | None = None):
    """A transparent WebP of a cut-out (or any image with transparency), for
    boards and the grid's cut-out tiles (the other previews are flattened).

    Like /thumb (api/thumbfiles.py): when the requested copy exists and the
    caller sees every item, it is sent without a database round trip;
    otherwise the item is loaded (and the copy made if it's missing)."""
    directory = thumbfiles.item_dir(item_id)
    if access.restricted() is None:
        name = cutouts.ALPHA_FILE.format(size=1024 if size <= 1024 else 2048)
        if thumbfiles.regular_file(directory / name) is not None:
            return await thumbfiles.send_file(directory, [name], v)
    return await run_in_threadpool(_cutout_preview_db, item_id, size, v)


def _cutout_preview_db(item_id: str, size: int, v: int | None):
    from ..db import SessionLocal

    with SessionLocal() as session:
        item = items_svc.get_item(session, item_id)
        path = cutouts.alpha_preview(item, size)
        if path is None:
            raise HTTPException(404, "this item has no transparency")
        rev = item.preview_rev or 0
    exact = v is not None and v == rev
    return FileResponse(path, media_type="image/webp",
                        headers={"Cache-Control": thumbfiles.CACHE_FOREVER if exact
                                 else thumbfiles.CACHE_REVALIDATE})


@router.post("/boards/{board_id}/nodes/{node_id}/cutout", status_code=202)
def board_node_cutout(board_id: str, node_id: str, body: CutoutIn,
                      session: Annotated[Session, Depends(get_session)]) -> dict:
    """The board's "Cut out" on a placed image. {board, job}: board when an
    existing cut-out was swapped in right away, job when one is being made
    (the node switches to it when the job is done). Undo the request's
    batch to put the original back."""
    from ..services import boards as boards_svc

    out = cutouts.board_cutout(session, board_id, node_id, model=body.model,
                               feather=body.feather, shadow=body.shadow)
    return {
        "board": boards_svc.out(session, out["board"]) if out["board"] is not None else None,
        "job": jobs_svc.job_out(out["job"]).model_dump(mode="json") if out["job"] else None,
    }
