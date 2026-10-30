from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_session
from ..ids import new_ulid
from ..models import SmartFolder
from ..schemas import SmartFolderCreate, SmartFolderOut, SmartFolderPatch
from ..services import folders as folders_svc

router = APIRouter(prefix="/smart-folders", tags=["smart-folders"])


def _out(s: SmartFolder) -> SmartFolderOut:
    return SmartFolderOut(id=s.id, name=s.name, query=s.query, position=s.position)


@router.get("", response_model=list[SmartFolderOut])
def list_smart_folders(session: Annotated[Session, Depends(get_session)]):
    rows = session.execute(
        sa.select(SmartFolder).order_by(SmartFolder.position, SmartFolder.name)
    ).scalars().all()
    return [_out(s) for s in rows]


@router.post("", response_model=SmartFolderOut, status_code=201)
def create_smart_folder(
    body: SmartFolderCreate, session: Annotated[Session, Depends(get_session)]
):
    s = SmartFolder(
        id=new_ulid(),
        name=body.name.strip(),
        query=body.query.model_dump(mode="json", exclude_none=True),
    )
    session.add(s)
    session.commit()
    folders_svc.snapshot(session)
    return _out(s)


@router.patch("/{smart_id}", response_model=SmartFolderOut)
def update_smart_folder(
    smart_id: str,
    body: SmartFolderPatch,
    session: Annotated[Session, Depends(get_session)],
):
    s = session.get(SmartFolder, smart_id)
    if s is None:
        raise HTTPException(404, f"smart folder {smart_id} not found")
    if body.name is not None:
        s.name = body.name.strip()
    if body.query is not None:
        s.query = body.query.model_dump(mode="json", exclude_none=True)
    if body.position is not None:
        s.position = body.position
    session.commit()
    folders_svc.snapshot(session)
    return _out(s)


@router.delete("/{smart_id}")
def delete_smart_folder(
    smart_id: str, session: Annotated[Session, Depends(get_session)]
):
    s = session.get(SmartFolder, smart_id)
    if s is None:
        raise HTTPException(404, f"smart folder {smart_id} not found")
    session.delete(s)
    session.commit()
    folders_svc.snapshot(session)
    return {"status": "deleted"}
