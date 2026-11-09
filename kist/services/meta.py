"""The library's durable copy: per-item meta.json and library/folders.json.

Postgres is the query index; these files are the denormalized copy every
change is written to, so the whole library can be rebuilt from disk alone
(`kist rebuild-index`).

How a write stays correct with several writers (API threads, MCP, the
worker, the CLI):

- Files are written to a unique temp file, fsynced, then renamed over the
  old one, so a reader or a crash only ever sees a whole file.
- The database is committed first. Then, under a Postgres advisory lock for
  that one file, the current row is read again and written out. Whoever
  writes last read last, so the file always ends on the newest state, even
  when two writers commit in one order and reach the disk in the other.
- A write that fails after the commit never fails the request: the change is
  safe in Postgres. It is logged, noted in library/.kist/meta-pending, and
  `kist verify` reports it until `kist rewrite-meta` writes it again.
"""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Folder, Item, SmartFolder

log = logging.getLogger("kist.meta")

META_VERSION = 2
PENDING_FILE = "meta-pending"
FOLDERS_KEY = "folders.json"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def item_dir(item_id: str) -> Path:
    return get_settings().items_dir / item_id


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def to_meta(item: Item) -> dict:
    return {
        "version": META_VERSION,
        "id": item.id,
        "filename": item.filename,
        "ext": item.ext,
        "mime": item.mime,
        "size_bytes": item.size_bytes,
        "width": item.width,
        "height": item.height,
        "duration_s": item.duration_s,
        "sha256": item.sha256,
        "phash": item.phash,
        "dominant_colors": item.dominant_colors,
        "folder_ids": sorted(f.id for f in item.folders),
        "folder_paths": [folder_path(f) for f in item.folders],
        "rating": item.rating,
        "notes": item.notes,
        "source_url": item.source_url,
        "tags": sorted(t.name for t in item.tags),
        "created_at": _iso(item.created_at),
        "modified_at": _iso(item.modified_at),
        "deleted_at": _iso(item.deleted_at),
        "preview_status": item.preview_status,
        "preview_error": item.preview_error,
        "preview_rev": item.preview_rev or 0,
    }


def folder_path(folder) -> list[str]:
    names: list[str] = []
    seen = set()
    while folder is not None and folder.id not in seen:
        seen.add(folder.id)
        names.append(folder.name)
        folder = folder.parent
    return list(reversed(names))


# ---------- files ----------


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a unique temp file + fsync + rename (never a torn file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_for(path):
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        try:  # make the rename itself durable
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass


def write_meta(item: Item) -> None:
    atomic_write_text(
        item_dir(item.id) / "meta.json",
        json.dumps(to_meta(item), indent=2, ensure_ascii=False),
    )


def write_folders_snapshot(
    folders: list[dict], smart_folders: list[dict] | None = None
) -> None:
    """library/folders.json: lets rebuild-index restore folders + smart folders."""
    atomic_write_text(
        get_settings().library_dir / "folders.json",
        json.dumps(
            {"version": META_VERSION, "folders": folders, "smart_folders": smart_folders or []},
            indent=2,
            default=str,
        ),
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------- writes that follow a commit ----------


def advisory_lock(session: Session, key: str) -> None:
    """Hold a Postgres lock named `key` until this transaction ends."""
    session.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"kist:{key}"}
    )


def note_pending(key: str) -> None:
    """Remember a durable-copy write that failed, for verify / rewrite-meta."""
    try:
        state = get_settings().state_dir
        state.mkdir(parents=True, exist_ok=True)
        with (state / PENDING_FILE).open("a", encoding="utf-8") as f:
            f.write(key + "\n")
    except OSError:
        log.exception("could not note the failed write of %s either", key)


def pending() -> list[str]:
    path = get_settings().state_dir / PENDING_FILE
    try:
        return sorted({ln.strip() for ln in path.read_text().splitlines() if ln.strip()})
    except FileNotFoundError:
        return []


def clear_pending() -> None:
    (get_settings().state_dir / PENDING_FILE).unlink(missing_ok=True)


def sync_item(session: Session, item_id: str) -> bool:
    """Bring items/<id>/meta.json up to the committed row. Call after commit.

    Returns False when nothing was written: the item no longer exists (a
    purge won the race; its folder is gone and must stay gone) or the write
    failed (logged and noted, never raised).
    """
    try:
        advisory_lock(session, f"item:{item_id}")
        item = session.get(Item, item_id)
        if item is None:
            session.commit()
            return False
        session.refresh(item)
        if not item_dir(item_id).is_dir():
            # purged between the commit and now: don't re-create its folder
            session.commit()
            return False
        write_meta(item)
        session.commit()
        return True
    except Exception:
        session.rollback()
        log.exception("meta.json for %s not written (kist rewrite-meta writes it)", item_id)
        note_pending(item_id)
        return False


def folders_snapshot_rows(session: Session) -> tuple[list[dict], list[dict]]:
    rows = session.execute(sa.select(Folder).order_by(Folder.id)).scalars().all()
    smart = session.execute(sa.select(SmartFolder).order_by(SmartFolder.id)).scalars().all()
    return (
        [
            {"id": f.id, "name": f.name, "parent_id": f.parent_id, "position": f.position}
            for f in rows
        ],
        [{"id": s.id, "name": s.name, "query": s.query, "position": s.position} for s in smart],
    )


def sync_folders(session: Session) -> bool:
    """Bring folders.json up to the committed folders. Call after commit."""
    try:
        advisory_lock(session, FOLDERS_KEY)
        folders, smart = folders_snapshot_rows(session)
        write_folders_snapshot(folders, smart)
        session.commit()
        return True
    except Exception:
        session.rollback()
        log.exception("folders.json not written (kist rewrite-meta writes it)")
        note_pending(FOLDERS_KEY)
        return False
