import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..ids import new_ulid
from ..models import Folder, Item, ItemFolder, SmartFolder
from . import meta as meta_svc


class FolderError(Exception):
    pass


def snapshot(session: Session) -> None:
    rows = session.execute(sa.select(Folder)).scalars().all()
    smart = session.execute(sa.select(SmartFolder)).scalars().all()
    meta_svc.write_folders_snapshot(
        [
            {"id": f.id, "name": f.name, "parent_id": f.parent_id, "position": f.position}
            for f in rows
        ],
        [
            {"id": s.id, "name": s.name, "query": s.query, "position": s.position}
            for s in smart
        ],
    )


def list_folders(session: Session) -> list[dict]:
    counts = dict(
        session.execute(
            sa.select(ItemFolder.folder_id, sa.func.count())
            .join(Item, Item.id == ItemFolder.item_id)
            .where(Item.deleted_at.is_(None))
            .group_by(ItemFolder.folder_id)
        ).all()
    )
    folders = session.execute(
        sa.select(Folder).order_by(Folder.position, Folder.name)
    ).scalars().all()
    return [
        {
            "id": f.id,
            "name": f.name,
            "parent_id": f.parent_id,
            "position": f.position,
            "item_count": counts.get(f.id, 0),
        }
        for f in folders
    ]


def create_folder(session: Session, name: str, parent_id: str | None = None) -> Folder:
    if parent_id is not None and session.get(Folder, parent_id) is None:
        raise FolderError(f"parent folder {parent_id} not found")
    folder = Folder(id=new_ulid(), name=name.strip(), parent_id=parent_id)
    session.add(folder)
    session.commit()
    snapshot(session)
    return folder


def _is_descendant(session: Session, candidate: str, ancestor: str) -> bool:
    current: str | None = candidate
    seen: set[str] = set()
    while current is not None and current not in seen:
        if current == ancestor:
            return True
        seen.add(current)
        folder = session.get(Folder, current)
        current = folder.parent_id if folder else None
    return False


def update_folder(session: Session, folder_id: str, fields: dict) -> Folder:
    folder = session.get(Folder, folder_id)
    if folder is None:
        raise FolderError(f"folder {folder_id} not found")
    if "name" in fields and fields["name"]:
        folder.name = fields["name"].strip()
    if "parent_id" in fields:
        new_parent = fields["parent_id"] or None
        if new_parent is not None:
            if new_parent == folder_id or _is_descendant(session, new_parent, folder_id):
                raise FolderError("cannot move a folder into itself")
            if session.get(Folder, new_parent) is None:
                raise FolderError(f"parent folder {new_parent} not found")
        folder.parent_id = new_parent
    if "position" in fields and fields["position"] is not None:
        folder.position = int(fields["position"])
    session.commit()
    snapshot(session)
    return folder


def delete_folder(session: Session, folder_id: str) -> None:
    """Delete a folder (and its subtree). Items are unfiled, not deleted."""
    folder = session.get(Folder, folder_id)
    if folder is None:
        raise FolderError(f"folder {folder_id} not found")
    session.delete(folder)
    session.commit()
    snapshot(session)
