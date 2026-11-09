import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..ids import new_ulid
from ..models import Folder, Item, ItemFolder
from . import meta as meta_svc
from . import validation

# every change to the folder tree's shape takes this lock, so two moves
# can't each pass the loop check against the other's old state
TREE_LOCK = "folder-tree"


class FolderError(Exception):
    """A folder change that isn't allowed (HTTP 400)."""


class FolderNotFound(FolderError):
    """(HTTP 404)"""

    def __init__(self, folder_id: str):
        super().__init__(f"folder {folder_id} not found")


def snapshot(session: Session) -> None:
    """Bring folders.json up to date (after a commit)."""
    meta_svc.sync_folders(session)


def subtree_counts(folders: list[Folder], memberships: list[tuple[str, str]]) -> dict[str, int]:
    """Distinct item count per folder, including items filed in any subfolder.

    An item filed in two folders of the same subtree counts once.
    """
    children: dict[str | None, list[str]] = {}
    for f in folders:
        children.setdefault(f.parent_id, []).append(f.id)
    direct: dict[str, set[str]] = {}
    for item_id, folder_id in memberships:
        direct.setdefault(folder_id, set()).add(item_id)

    cache: dict[str, set[str]] = {}

    def items_under(folder_id: str, seen: frozenset[str] = frozenset()) -> set[str]:
        if folder_id in cache:
            return cache[folder_id]
        if folder_id in seen:  # defensive: parent cycles are rejected on update
            return set()
        found = set(direct.get(folder_id, ()))
        for child in children.get(folder_id, ()):
            found |= items_under(child, seen | {folder_id})
        cache[folder_id] = found
        return found

    return {f.id: len(items_under(f.id)) for f in folders}


def folder_dict(f: Folder, item_count: int = 0) -> dict:
    return {
        "id": f.id,
        "name": f.name,
        "parent_id": f.parent_id,
        "position": f.position,
        "item_count": item_count,
    }


def list_folders(session: Session) -> list[dict]:
    folders = session.execute(
        sa.select(Folder).order_by(Folder.position, Folder.name)
    ).scalars().all()
    memberships = session.execute(
        sa.select(ItemFolder.item_id, ItemFolder.folder_id)
        .join(Item, Item.id == ItemFolder.item_id)
        .where(Item.deleted_at.is_(None))
    ).all()
    counts = subtree_counts(list(folders), [tuple(m) for m in memberships])
    return [folder_dict(f, counts.get(f.id, 0)) for f in folders]


def _subtree_ids(session: Session, folder_id: str) -> list[str]:
    from .search import folder_subtree

    return list(session.execute(sa.select(folder_subtree(folder_id).c.id)).scalars())


def _member_item_ids(session: Session, folder_ids: list[str]) -> list[str]:
    if not folder_ids:
        return []
    return sorted(
        set(
            session.execute(
                sa.select(ItemFolder.item_id).where(ItemFolder.folder_id.in_(folder_ids))
            ).scalars()
        )
    )


def _sync_items(session: Session, item_ids: list[str]) -> None:
    for item_id in item_ids:
        meta_svc.sync_item(session, item_id)


def create_folder(session: Session, name: str, parent_id: str | None = None) -> Folder:
    name = validation.folder_name(name)
    if parent_id is not None and session.get(Folder, parent_id) is None:
        raise validation.UnknownFolder(parent_id)
    folder = Folder(id=new_ulid(), name=name, parent_id=parent_id)
    session.add(folder)
    session.commit()
    snapshot(session)
    return folder


def update_folder(session: Session, folder_id: str, fields: dict) -> Folder:
    folder = session.get(Folder, folder_id)
    if folder is None:
        raise FolderNotFound(folder_id)
    reshaped = False
    if fields.get("name") is not None:
        name = validation.folder_name(fields["name"])
        reshaped = name != folder.name
        folder.name = name
    if "parent_id" in fields:
        meta_svc.advisory_lock(session, TREE_LOCK)
        new_parent = fields["parent_id"] or None
        if new_parent is not None:
            # read the tree after taking the lock: a move committed by
            # someone else a moment ago counts
            parents = dict(session.execute(sa.select(Folder.id, Folder.parent_id)).all())
            if new_parent not in parents:
                raise validation.UnknownFolder(new_parent)
            current: str | None = new_parent
            seen: set[str] = set()
            while current is not None and current not in seen:
                if current == folder_id:
                    raise FolderError("cannot move a folder into itself")
                seen.add(current)
                current = parents.get(current)
        reshaped = reshaped or new_parent != folder.parent_id
        folder.parent_id = new_parent
    if fields.get("position") is not None:
        folder.position = validation.position(fields["position"])
    # members' meta.json carries folder paths: rewrite them on rename/move
    affected = _member_item_ids(session, _subtree_ids(session, folder_id)) if reshaped else []
    session.commit()
    snapshot(session)
    _sync_items(session, affected)
    return folder


def delete_folder(session: Session, folder_id: str) -> None:
    """Delete a folder (and its subtree). Items are unfiled, not deleted, and
    their meta.json is rewritten so the durable copy matches."""
    folder = session.get(Folder, folder_id)
    if folder is None:
        raise FolderNotFound(folder_id)
    meta_svc.advisory_lock(session, TREE_LOCK)
    affected = _member_item_ids(session, _subtree_ids(session, folder_id))
    session.delete(folder)
    session.commit()
    snapshot(session)
    _sync_items(session, affected)
