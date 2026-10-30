from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_session
from ..schemas import FolderCreate, FolderOut, FolderPatch
from ..services import folders as folders_svc

router = APIRouter(prefix="/folders", tags=["folders"])


@router.get("", response_model=list[FolderOut])
def list_folders(session: Annotated[Session, Depends(get_session)]):
    return folders_svc.list_folders(session)


@router.post("", response_model=FolderOut, status_code=201)
def create_folder(
    body: FolderCreate, session: Annotated[Session, Depends(get_session)]
):
    try:
        folder = folders_svc.create_folder(session, body.name, body.parent_id)
    except folders_svc.FolderError as exc:
        raise HTTPException(400, str(exc)) from exc
    return FolderOut(
        id=folder.id, name=folder.name, parent_id=folder.parent_id,
        position=folder.position,
    )


@router.patch("/{folder_id}", response_model=FolderOut)
def update_folder(
    folder_id: str,
    body: FolderPatch,
    session: Annotated[Session, Depends(get_session)],
):
    try:
        folder = folders_svc.update_folder(
            session, folder_id, body.model_dump(exclude_unset=True)
        )
    except folders_svc.FolderError as exc:
        raise HTTPException(400, str(exc)) from exc
    return FolderOut(
        id=folder.id, name=folder.name, parent_id=folder.parent_id,
        position=folder.position,
    )


@router.delete("/{folder_id}")
def delete_folder(folder_id: str, session: Annotated[Session, Depends(get_session)]):
    try:
        folders_svc.delete_folder(session, folder_id)
    except folders_svc.FolderError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"status": "deleted"}
