"""Smart folders: saved searches, shown in the sidebar like folders."""

from typing import Any

import sqlalchemy as sa
from pydantic import ValidationError as PydanticError
from sqlalchemy.orm import Session

from ..ids import new_ulid
from ..models import Folder, SmartFolder
from ..schemas import SmartFolderOut, SmartFolderQuery
from . import folders as folders_svc
from . import validation


class NotFound(Exception):
    def __init__(self, smart_id: str):
        super().__init__(f"smart folder {smart_id} not found")


def out(s: SmartFolder) -> SmartFolderOut:
    """Saved query with today's defaults filled in; a row saved before a
    rule existed is returned as stored rather than failing the list."""
    try:
        query = SmartFolderQuery.model_validate(s.query or {}).model_dump(mode="json")
    except PydanticError:
        query = dict(s.query or {})
    return SmartFolderOut(id=s.id, name=s.name, query=query, position=s.position)


def list_smart_folders(session: Session) -> list[SmartFolder]:
    return list(
        session.execute(
            sa.select(SmartFolder).order_by(SmartFolder.position, SmartFolder.name)
        ).scalars()
    )


def _stored(session: Session, query: SmartFolderQuery) -> dict[str, Any]:
    if query.folder not in (None, "", "none") and session.get(Folder, query.folder) is None:
        raise validation.UnknownFolder(query.folder)
    return query.model_dump(mode="json", exclude_none=True)


def create(session: Session, name: str, query: SmartFolderQuery) -> SmartFolder:
    s = SmartFolder(
        id=new_ulid(), name=validation.folder_name(name), query=_stored(session, query)
    )
    session.add(s)
    session.commit()
    folders_svc.snapshot(session)
    return s


def get(session: Session, smart_id: str) -> SmartFolder:
    s = session.get(SmartFolder, smart_id)
    if s is None:
        raise NotFound(smart_id)
    return s


def update(
    session: Session,
    smart_id: str,
    *,
    name: str | None = None,
    query: SmartFolderQuery | None = None,
    position: int | None = None,
) -> SmartFolder:
    s = get(session, smart_id)
    if name is not None:
        s.name = validation.folder_name(name)
    if query is not None:
        s.query = _stored(session, query)
    if position is not None:
        s.position = validation.position(position)
    session.commit()
    folders_svc.snapshot(session)
    return s


def delete(session: Session, smart_id: str) -> None:
    session.delete(get(session, smart_id))
    session.commit()
    folders_svc.snapshot(session)
