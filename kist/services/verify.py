"""Compare Postgres with the library on disk (changes nothing).

Used by `kist verify` and by the worker's daily run, whose last result
library/.kist/verify.json feeds GET /api/health/deep.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..config import get_settings
from ..lifecycle import PreviewStatus
from ..models import Item
from . import meta as meta_svc

log = logging.getLogger("kist.verify")

RESULT_FILE = "verify.json"
# compared as written; timestamps are compared as instants below
COMPARED = (
    "filename", "rating", "notes", "source_url", "tags", "folder_ids", "sha256",
    "preview_status", "preview_rev",
)
COMPARED_TIMES = ("created_at", "modified_at", "deleted_at")
FIXES = (
    "fixes: kist rewrite-meta (meta.json or folders.json out of date; safe while Kist "
    "runs, it re-reads each item as it writes it), kist rebuild-index (rows missing "
    "from the database), kist regen-previews (thumbnails)"
)


@dataclass
class Report:
    items_checked: int = 0
    problems: list[str] = field(default_factory=list)


def _instant(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


BATCH = 500


def _items_in_batches(session: Session):
    """Every item, a batch at a time (memory stays flat on big libraries)."""
    ids = session.execute(sa.select(Item.id).order_by(Item.id)).scalars().all()
    for start in range(0, len(ids), BATCH):
        chunk = ids[start:start + BATCH]
        yield from session.execute(
            sa.select(Item).where(Item.id.in_(chunk)).order_by(Item.id)
        ).scalars().all()
        session.expunge_all()


def check_items(session: Session, report: Report) -> None:
    settings = get_settings()
    db_ids: set[str] = set()
    for item in _items_in_batches(session):
        db_ids.add(item.id)
        item_dir = settings.items_dir / item.id
        if not (item_dir / f"original.{item.ext}").is_file():
            report.problems.append(f"{item.id}: original file missing ({item.filename})")
        meta_file = item_dir / "meta.json"
        if not meta_file.is_file():
            report.problems.append(f"{item.id}: meta.json missing")
        else:
            try:
                on_disk = meta_svc.read_json(meta_file)
            except ValueError:
                on_disk = None
                report.problems.append(f"{item.id}: meta.json unreadable")
            if isinstance(on_disk, dict):
                expected = meta_svc.to_meta(item)
                drift = [k for k in COMPARED if on_disk.get(k) != expected.get(k)]
                drift += [
                    k for k in COMPARED_TIMES
                    if _instant(on_disk.get(k)) != _instant(expected.get(k))
                ]
                if drift:
                    report.problems.append(
                        f"{item.id}: meta.json differs in {', '.join(drift)}"
                    )
        if item.preview_status == PreviewStatus.READY and not (
            item_dir / "thumb_256.webp"
        ).is_file():
            report.problems.append(f"{item.id}: marked ready but has no thumbnails")
    report.items_checked = len(db_ids)
    if settings.items_dir.is_dir():
        for d in sorted(settings.items_dir.iterdir()):
            if d.is_dir() and not d.name.startswith(".") and d.name not in db_ids:
                report.problems.append(f"{d.name}: folder on disk with no database row")


def check_folders(session: Session, report: Report) -> None:
    path = get_settings().library_dir / "folders.json"
    folders, smart = meta_svc.folders_snapshot_rows(session)
    if not path.is_file():
        if folders or smart:
            report.problems.append("folders.json missing")
        return
    try:
        data = meta_svc.read_json(path)
        disk_folders = {f["id"]: f for f in data.get("folders", [])}
        disk_smart = {s["id"]: s for s in data.get("smart_folders", [])}
    except (ValueError, TypeError, KeyError, AttributeError):
        report.problems.append("folders.json unreadable")
        return
    for label, db_rows, disk in (
        ("folder", folders, disk_folders), ("smart folder", smart, disk_smart)
    ):
        db = {r["id"]: r for r in db_rows}
        for fid in sorted(db.keys() - disk.keys()):
            report.problems.append(f"folders.json: {label} {fid} ({db[fid]['name']}) missing")
        for fid in sorted(disk.keys() - db.keys()):
            report.problems.append(f"folders.json: {label} {fid} is not in the database")
        for fid in sorted(db.keys() & disk.keys()):
            if any(disk[fid].get(k) != v for k, v in db[fid].items()):
                report.problems.append(f"folders.json: {label} {fid} ({db[fid]['name']}) differs")


def run(session: Session) -> Report:
    report = Report()
    check_items(session, report)
    check_folders(session, report)
    for key in meta_svc.pending():
        report.problems.append(f"{key}: an earlier write to disk failed")
    return report


def save_result(report: Report) -> None:
    """Keep the last result for deep health (best effort)."""
    try:
        meta_svc.atomic_write_text(
            get_settings().state_dir / RESULT_FILE,
            json.dumps(
                {
                    "last_run": datetime.now(UTC).isoformat(),
                    "items_checked": report.items_checked,
                    "drift": len(report.problems),
                    "problems": report.problems[:50],
                },
                indent=2,
            ),
        )
    except OSError:
        log.exception("could not save the verify result")


def last_result() -> dict | None:
    try:
        return meta_svc.read_json(get_settings().state_dir / RESULT_FILE)
    except (OSError, ValueError):
        return None
