from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_session
from ..schemas import FolderCreate, FolderOut, FolderPatch
from ..services import folders as folders_svc

# FolderNotFound -> 404, FolderError -> 400, ValidationError (bad name,
# unknown parent) -> 422: handlers in kist/app.py
router = APIRouter(prefix="/folders", tags=["folders"])


@router.get("", response_model=list[FolderOut])
def list_folders(session: Annotated[Session, Depends(get_session)]):
    return folders_svc.list_folders(session)


@router.post("", response_model=FolderOut, status_code=201)
def create_folder(body: FolderCreate, session: Annotated[Session, Depends(get_session)]):
    return folders_svc.folder_dict(folders_svc.create_folder(session, body.name, body.parent_id))


@router.patch("/{folder_id}", response_model=FolderOut)
def update_folder(
    folder_id: str,
    body: FolderPatch,
    session: Annotated[Session, Depends(get_session)],
):
    folder = folders_svc.update_folder(session, folder_id, body.model_dump(exclude_unset=True))
    return folders_svc.folder_dict(folder)


@router.delete("/{folder_id}")
def delete_folder(folder_id: str, session: Annotated[Session, Depends(get_session)]):
    folders_svc.delete_folder(session, folder_id)
    return {"status": "deleted"}
