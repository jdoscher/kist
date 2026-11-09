import shutil
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .. import lifecycle
from ..lifecycle import JobKind, JobStatus
from ..models import Folder, Item, Job
from . import meta as meta_svc
from . import tags as tags_svc
from . import validation


class NotFound(Exception):
    """No item with that id (HTTP 404)."""

    def __init__(self, item_id: str):
        super().__init__(f"item {item_id} not found")
        self.item_id = item_id


class Conflict(Exception):
    """The item changed since the caller last saw it (HTTP 409)."""

    message = "Changed elsewhere since you opened it"

    def __init__(self, item: Item):
        super().__init__(self.message)
        self.item = item


def get_item(
    session: Session, item_id: str, *, with_deleted: bool = True, for_update: bool = False
) -> Item:
    item = session.get(
        Item, item_id, with_for_update=for_update or None, populate_existing=for_update
    )
    if item is None or (not with_deleted and item.deleted_at is not None):
        raise NotFound(item_id)
    return item


def save(session: Session, item: Item) -> Item:
    """Commit, then bring meta.json up to date. The meta write can't fail
    the call: the change is already safe in Postgres (see services.meta)."""
    session.commit()
    meta_svc.sync_item(session, item.id)
    return item


def touch_and_save(session: Session, item: Item) -> Item:
    item.modified_at = datetime.now(UTC)
    return save(session, item)


def resolve_folders(session: Session, folder_ids: list[str]) -> list[Folder]:
    """The folders for these ids, deduped. An id that doesn't exist (a folder
    deleted in another tab, a typo) is refused rather than dropped, so the
    caller never thinks something was filed when it wasn't."""
    seen: set[str] = set()
    out: list[Folder] = []
    for fid in folder_ids:
        if not fid or fid in seen:
            continue
        seen.add(fid)
        folder = session.get(Folder, fid)
        if folder is None:
            raise validation.UnknownFolder(fid)
        out.append(folder)
    return out


def _same_instant(a: datetime | None, b: datetime | None) -> bool:
    if a is None or b is None:
        return a is b
    if a.tzinfo is None:
        a = a.replace(tzinfo=UTC)
    if b.tzinfo is None:
        b = b.replace(tzinfo=UTC)
    return a == b


def update_item(
    session: Session,
    item_id: str,
    *,
    fields: dict,
    base_modified_at: datetime | None = None,
) -> Item:
    """Apply a partial update. `fields` only contains keys that were sent.

    With base_modified_at (the modified_at the caller last saw), the edit is
    refused with Conflict when someone else changed the item since. The row
    is locked while checking, so two such edits can't both pass.

    folder_id / folder_ids REPLACE every membership (REST PATCH semantics,
    kept for API clients); use set_folders(add=, remove=) to change one.
    """
    item = get_item(session, item_id, for_update=True)
    if base_modified_at is not None and not _same_instant(base_modified_at, item.modified_at):
        session.rollback()
        current = get_item(session, item_id)
        session.refresh(current)  # tags and folders too, for the 409 body
        raise Conflict(current)
    if fields.get("filename") is not None:
        item.filename = validation.filename(fields["filename"])
    if fields.get("rating") is not None:
        item.rating = validation.rating(fields["rating"])
    if fields.get("notes") is not None:
        item.notes = validation.notes(fields["notes"])
    if "source_url" in fields:
        item.source_url = validation.source_url(fields["source_url"])
    if fields.get("created_at") is not None:
        item.created_at = validation.timestamp(fields["created_at"], "created_at")
    if fields.get("folder_ids") is not None:
        item.folders = resolve_folders(session, list(fields["folder_ids"]))
    elif "folder_id" in fields:
        fid = fields["folder_id"]
        item.folders = resolve_folders(session, [fid]) if fid else []
    if fields.get("modified_at") is not None:
        # an explicit modified_at (e.g. an import) wins over the touch
        item.modified_at = validation.timestamp(fields["modified_at"], "modified_at")
        return save(session, item)
    return touch_and_save(session, item)


def set_folders(
    session: Session,
    item_id: str,
    *,
    replace: list[str] | None = None,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> Item:
    """Replace, or add/remove, folder memberships. Unknown ids to add or
    replace with are refused; removing a membership that isn't there (or a
    folder that no longer exists) is a no-op, so retries stay safe."""
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
    return touch_and_save(session, item)


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
    return touch_and_save(session, item)


def file_duplicate(
    session: Session,
    item: Item,
    *,
    folder_ids: list[str] | None = None,
    tag_names: list[str] | None = None,
    notes: str = "",
    source_url: str | None = None,
) -> Item:
    """A re-upload of an active item: apply what came with it, additively
    (add folders and tags, fill notes and source_url only if empty), so a
    file dropped on a folder ends up in that folder."""
    changed = False
    if folder_ids:
        have = {f.id for f in item.folders}
        new = [f for f in resolve_folders(session, folder_ids) if f.id not in have]
        if new:
            item.folders = [*item.folders, *new]
            changed = True
    if tag_names:
        have = {t.name.lower() for t in item.tags}
        new = [t for t in tags_svc.get_or_create(session, tag_names) if t.name.lower() not in have]
        if new:
            item.tags = [*item.tags, *new]
            changed = True
    if notes and not item.notes:
        item.notes = notes
        changed = True
    if source_url and not item.source_url:
        item.source_url = source_url
        changed = True
    if not changed:
        return item
    return touch_and_save(session, item)


def soft_delete(session: Session, item_id: str) -> Item:
    """active -> trashed (a no-op for an item already in the trash)."""
    item = get_item(session, item_id)
    if not lifecycle.trash(item):
        return item
    return touch_and_save(session, item)


def restore(session: Session, item_id: str) -> Item:
    """trashed -> active (a no-op for an active item)."""
    item = get_item(session, item_id)
    if not lifecycle.restore(item):
        return item
    return touch_and_save(session, item)


def hard_delete(session: Session, item_id: str) -> None:
    """trashed -> gone. Raises lifecycle.TransitionError for an active item.

    Holds the item's meta lock, so a meta.json write racing the purge can't
    re-create the folder afterwards, and drops queued preview jobs for it.
    """
    item = get_item(session, item_id)
    lifecycle.check_purge(item)
    meta_svc.advisory_lock(session, f"item:{item.id}")
    session.execute(
        sa.delete(Job).where(
            Job.kind == JobKind.REGEN_PREVIEW,
            Job.status == JobStatus.PENDING,
            Job.payload["item_id"].astext == item.id,
        )
    )
    d = meta_svc.item_dir(item.id)
    session.delete(item)
    session.commit()
    shutil.rmtree(d, ignore_errors=True)
