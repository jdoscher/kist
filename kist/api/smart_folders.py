from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_session
from ..schemas import SmartFolderCreate, SmartFolderOut, SmartFolderPatch
from ..services import smart_folders as smart_svc

router = APIRouter(prefix="/smart-folders", tags=["smart-folders"])


@router.get("", response_model=list[SmartFolderOut])
def list_smart_folders(session: Annotated[Session, Depends(get_session)]):
    return [smart_svc.out(s) for s in smart_svc.list_smart_folders(session)]


@router.post("", response_model=SmartFolderOut, status_code=201)
def create_smart_folder(
    body: SmartFolderCreate, session: Annotated[Session, Depends(get_session)]
):
    return smart_svc.out(smart_svc.create(session, body.name, body.query))


@router.patch("/{smart_id}", response_model=SmartFolderOut)
def update_smart_folder(
    smart_id: str,
    body: SmartFolderPatch,
    session: Annotated[Session, Depends(get_session)],
):
    return smart_svc.out(
        smart_svc.update(
            session, smart_id, name=body.name, query=body.query, position=body.position
        )
    )


@router.delete("/{smart_id}")
def delete_smart_folder(smart_id: str, session: Annotated[Session, Depends(get_session)]):
    smart_svc.delete(session, smart_id)
    return {"status": "deleted"}
