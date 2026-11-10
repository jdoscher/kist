"""Fixity: proof that originals haven't changed since ingest.

The worker re-hashes about 1/30 of the originals every day, oldest-checked
first, so every file is checked about once a month at a low, steady load.
Each check appends {at, sha256, ok} to items/<id>/fixity.jsonl (the record
rebuild-index restores the timestamps from) and sets fixity_checked_at; a
mismatch or a missing original sets fixity_failed_at, which deep health,
/api/stats (Needs attention) and /api/archive-health report until a later
check passes (after the file is restored from a backup).
"""

import json
import logging
import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Item

log = logging.getLogger("kist.fixity")

FILE = "fixity.jsonl"
CYCLE_DAYS = 30
CHUNK = 25  # checks per worker pass, so the loop keeps moving


def log_path(item_id: str) -> Path:
    return get_settings().items_dir / item_id / FILE


def daily_quota(total: int) -> int:
    return math.ceil(total / CYCLE_DAYS) if total else 0


def due(session: Session, now: datetime | None = None) -> int:
    """How many more checks today's share calls for."""
    now = now or datetime.now(UTC)
    total, recent = session.execute(
        sa.select(
            sa.func.count(),
            sa.func.count().filter(Item.fixity_checked_at > now - timedelta(days=1)),
        ).select_from(Item)
    ).one()
    return max(0, daily_quota(total) - recent)


def check_item(session: Session, item: Item, now: datetime | None = None) -> bool:
    """Re-hash one original against its recorded sha256. Returns ok."""
    from .ingest import sha256_file

    now = now or datetime.now(UTC)
    original = get_settings().items_dir / item.id / f"original.{item.ext}"
    try:
        digest = sha256_file(original)
        error = None
    except OSError as exc:
        digest, error = None, f"unreadable: {type(exc).__name__}"
    ok = digest == item.sha256
    line = {"at": now.isoformat(), "sha256": digest, "ok": ok}
    if not ok:
        line["expected"] = item.sha256
        if error:
            line["error"] = error
    # a check isn't an edit: modified_at stays (models.BOOKKEEPING_COLUMNS;
    # spelled out so the intent survives a move to the ORM)
    session.execute(
        sa.update(Item).where(Item.id == item.id).values(
            fixity_checked_at=now,
            fixity_failed_at=None if ok else now,
            modified_at=Item.modified_at,
        )
    )
    session.commit()
    path = log_path(item.id)
    if path.parent.is_dir():
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            log.exception("fixity line not written for %s", item.id)
    if not ok:
        log.error("fixity check FAILED for %s (%s): expected %s, found %s",
                  item.id, item.filename, item.sha256, digest or error)
    return ok


def run_some(session: Session, limit: int = CHUNK) -> tuple[int, int]:
    """Check up to `limit` of today's share. Returns (checked, failed)."""
    n = min(limit, due(session))
    if n <= 0:
        return 0, 0
    ids = session.execute(
        sa.select(Item.id)
        .order_by(Item.fixity_checked_at.asc().nulls_first(), Item.id)
        .limit(n)
    ).scalars().all()
    checked = failed = 0
    for item_id in ids:
        item = session.get(Item, item_id, populate_existing=True)
        if item is None:
            continue
        checked += 1
        if not check_item(session, item):
            failed += 1
    return checked, failed


def failed_items(session: Session, limit: int = 50) -> list[Item]:
    return list(
        session.execute(
            sa.select(Item).where(Item.fixity_failed_at.isnot(None))
            .order_by(Item.fixity_failed_at.desc()).limit(limit)
        ).scalars()
    )


def from_log(item_dir: Path) -> tuple[datetime | None, datetime | None]:
    """(checked_at, failed_at) from an item's fixity.jsonl (rebuild-index)."""
    try:
        lines = (item_dir / FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, None
    for raw in reversed(lines):
        try:
            last = json.loads(raw)
            at = datetime.fromisoformat(last["at"])
        except (ValueError, KeyError, TypeError):
            continue
        return at, (None if last.get("ok") else at)
    return None, None
