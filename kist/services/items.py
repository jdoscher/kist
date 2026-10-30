import shutil
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..models import Folder, Item
from . import meta as meta_svc
from . import tags as tags_svc


class NotFound(Exception):
    pass


def get_item(session: Session, item_id: str, *, with_deleted: bool = True) -> Item:
    item = session.get(Item, item_id)
    if item is None or (not with_deleted and item.deleted_at is not None):
        raise NotFound(item_id)
    return item


def touch_and_snapshot(session: Session, item: Item) -> Item:
    item.modified_at = datetime.now(UTC)
    session.commit()
    session.refresh(item)
    meta_svc.write_meta(item)
    return item


def resolve_folders(session: Session, folder_ids: list[str]) -> list[Folder]:
    """Dedupe and keep only folders that exist."""
    seen: set[str] = set()
    out: list[Folder] = []
    for fid in folder_ids:
        if not fid or fid in seen:
            continue
        seen.add(fid)
        folder = session.get(Folder, fid)
        if folder is not None:
            out.append(folder)
    return out


def update_item(
    session: Session,
    item_id: str,
    *,
    fields: dict,
) -> Item:
    """Apply a partial update. `fields` only contains keys that were sent."""
    item = get_item(session, item_id)
    if "filename" in fields and fields["filename"]:
        item.filename = str(fields["filename"])[:255]
    if "rating" in fields and fields["rating"] is not None:
        item.rating = max(0, min(5, int(fields["rating"])))
    if "notes" in fields and fields["notes"] is not None:
        item.notes = str(fields["notes"])
    if "source_url" in fields:
        item.source_url = fields["source_url"] or None
    if "created_at" in fields and fields["created_at"] is not None:
        item.created_at = fields["created_at"]
    if "folder_ids" in fields and fields["folder_ids"] is not None:
        item.folders = resolve_folders(session, list(fields["folder_ids"]))
    elif "folder_id" in fields:
        fid = fields["folder_id"]
        item.folders = resolve_folders(session, [fid]) if fid else []
    if "modified_at" in fields and fields["modified_at"] is not None:
        # explicit modified_at (e.g. an import) wins over the touch below
        session.flush()
        item.modified_at = fields["modified_at"]
        session.commit()
        session.refresh(item)
        meta_svc.write_meta(item)
        return item
    return touch_and_snapshot(session, item)


def set_folders(
    session: Session,
    item_id: str,
    *,
    replace: list[str] | None = None,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> Item:
    item = get_item(session, item_id)
    if replace is not None:
        item.folders = resolve_folders(session, replace)
    else:
        current = {f.id: f for f in item.folders}
        for folder in resolve_folders(session, add or []):
            current.setdefault(folder.id, folder)
        for fid in remove or []:
            current.pop(fid, None)
        item.folders = list(current.values())
    return touch_and_snapshot(session, item)


def set_tags(
    session: Session,
    item_id: str,
    *,
    replace: list[str] | None = None,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> Item:
    item = get_item(session, item_id)
    if replace is not None:
        item.tags = tags_svc.get_or_create(session, replace)
    else:
        current = {t.name.lower(): t for t in item.tags}
        for tag in tags_svc.get_or_create(session, add or []):
            current.setdefault(tag.name.lower(), tag)
        for name in remove or []:
            current.pop(tags_svc.normalize(name).lower(), None)
        item.tags = list(current.values())
    return touch_and_snapshot(session, item)


def soft_delete(session: Session, item_id: str) -> Item:
    item = get_item(session, item_id)
    if item.deleted_at is None:
        item.deleted_at = datetime.now(UTC)
    return touch_and_snapshot(session, item)


def restore(session: Session, item_id: str) -> Item:
    item = get_item(session, item_id)
    item.deleted_at = None
    return touch_and_snapshot(session, item)


def hard_delete(session: Session, item_id: str) -> None:
    item = get_item(session, item_id)
    d = meta_svc.item_dir(item.id)
    session.delete(item)
    session.commit()
    shutil.rmtree(d, ignore_errors=True)


def empty_trash(session: Session) -> int:
    ids = session.execute(
        sa.select(Item.id).where(Item.deleted_at.isnot(None))
    ).scalars().all()
    for item_id in ids:
        hard_delete(session, item_id)
    return len(ids)
