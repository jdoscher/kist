from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from .. import access
from ..db import get_session
from ..schemas import BoardAddItems, BoardCreate, BoardPatch, BoardPut, BoardSummary
from ..services import boards as boards_svc

# boards_svc.NotFound -> 404, boards_svc.Conflict -> 409 with the current
# board, TransitionError (editing a trashed board) -> 409 (kist/app.py)
router = APIRouter(prefix="/boards", tags=["boards"])


@router.get("", response_model=list[BoardSummary])
def list_boards(
    session: Annotated[Session, Depends(get_session)],
    state: Literal["active", "trashed"] = "active",
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
):
    return [boards_svc.summary(b) for b in boards_svc.list_boards(session, state=state, q=q,
                                                                  limit=limit)]


@router.post("", status_code=201)
def create_board(body: BoardCreate, session: Annotated[Session, Depends(get_session)]):
    """A new board. item_ids lays those items out (grid or masonry) after
    any nodes you send."""
    board = boards_svc.create_board(
        session, body.name, canvas=body.canvas, nodes=body.nodes, edges=body.edges,
        item_ids=body.item_ids, layout=body.layout,
    )
    return boards_svc.out(session, board)


@router.get("/{board_id}")
def get_board(board_id: str, session: Annotated[Session, Depends(get_session)]):
    return boards_svc.out(session, boards_svc.get_board(session, board_id))


@router.put("/{board_id}")
def put_board(board_id: str, body: BoardPut, session: Annotated[Session, Depends(get_session)]):
    """Save the whole document. 409 when base_version isn't the current version."""
    board = boards_svc.save_board(
        session, board_id, base_version=body.base_version, name=body.name,
        canvas=body.canvas, nodes=body.nodes, edges=body.edges,
    )
    return boards_svc.out(session, board)


@router.patch("/{board_id}")
def patch_board(board_id: str, body: BoardPatch,
                session: Annotated[Session, Depends(get_session)]):
    """Node and edge ops (add, update as JSON merge, remove)."""
    board = boards_svc.patch_board(session, board_id, body.model_dump(exclude_unset=True))
    return boards_svc.out(session, board)


@router.post("/{board_id}/items")
def add_items(board_id: str, body: BoardAddItems,
              session: Annotated[Session, Depends(get_session)]):
    """Lay items out below what is on the board."""
    board = boards_svc.add_items(session, board_id, body.item_ids, body.layout,
                                 body.base_version)
    return boards_svc.out(session, board)


@router.delete("/{board_id}")
def delete_board(board_id: str, session: Annotated[Session, Depends(get_session)],
                 hard: bool = False):
    """Move the board to the trash (restorable for the trash retention
    period); hard=true deletes a trashed board for good."""
    if hard:
        access.require(access.Action.HARD_DELETE)
        boards_svc.purge_board(session, board_id)
        return {"status": "deleted", "hard": True}
    board = boards_svc.trash_board(session, board_id)
    return {"status": "trashed", "board": boards_svc.summary(board)}


@router.post("/{board_id}/restore")
def restore_board(board_id: str, session: Annotated[Session, Depends(get_session)]):
    return boards_svc.out(session, boards_svc.restore_board(session, board_id))


@router.post("/{board_id}/duplicate", status_code=201)
def duplicate_board(board_id: str, session: Annotated[Session, Depends(get_session)],
                    name: str | None = None):
    return boards_svc.out(session, boards_svc.duplicate_board(session, board_id, name))


@router.get("/{board_id}/thumb")
def board_thumb(board_id: str, session: Annotated[Session, Depends(get_session)]):
    path = boards_svc.thumb_path(session, board_id)
    if not path.is_file():
        return Response(status_code=404)
    return FileResponse(path, media_type="image/webp",
                        headers={"Cache-Control": "private, no-cache"})


@router.get("/{board_id}/export")
def export_board(
    board_id: str,
    session: Annotated[Session, Depends(get_session)],
    format: Literal["png", "pdf", "html"] = "png",
    scale: Annotated[float, Query(ge=0.05, le=8)] = 1.0,
    max_pixels: Annotated[int | None, Query(ge=10_000)] = None,
):
    """PNG or PDF rendered at `scale` (scaled down to stay under
    max_pixels, at most 40 MP), or one self-contained HTML file."""
    body, media, name = boards_svc.export(session, board_id, format, scale, max_pixels)
    headers = {"Content-Disposition": f'attachment; filename="{name}"',
               "Cache-Control": "private, no-cache"}
    if format == "html":
        # opened from this origin it must never run script (it embeds user text)
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src data:; " \
                                             "style-src 'unsafe-inline'"
    return Response(body, media_type=media, headers=headers)
