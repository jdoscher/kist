"""Trash retention: the worker empties the trash of old items.

Items trashed more than KIST_TRASH_RETENTION_DAYS ago (default 30; 0 keeps
the trash forever) are permanently deleted, protected items never. Each
purge is an item event by actor "worker", and the item's history is kept in
library/.kist/events-purged.jsonl.

The clock starts when retention is first switched on (library/.kist/
retention.json), so upgrading never empties an old trash at once: items
already there get the full retention period from that day.
"""

import json
import logging
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Item
from . import events
from . import items as items_svc
from . import meta as meta_svc

log = logging.getLogger("kist.retention")

STATE_FILE = "retention.json"
BATCH = 200


def started_at(now: datetime) -> datetime:
    """When retention was first on for this library (recorded the first time)."""
    path = get_settings().state_dir / STATE_FILE
    try:
        return datetime.fromisoformat(json.loads(path.read_text())["since"])
    except (OSError, ValueError, KeyError, TypeError):
        meta_svc.atomic_write_text(path, json.dumps({"since": now.isoformat()}))
        return now


def purge_expired(session: Session, days: int | None = None,
                  now: datetime | None = None) -> list[str]:
    """Permanently delete expired trash. Returns the purged item ids."""
    days = get_settings().trash_retention_days if days is None else days
    if days <= 0:
        return []
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=days)
    if started_at(now) > cutoff:
        return []
    ids = session.execute(
        sa.select(Item.id)
        .where(Item.deleted_at.isnot(None), Item.deleted_at < cutoff,
               Item.protected.is_(False))
        .order_by(Item.deleted_at, Item.id)
        .limit(BATCH)
    ).scalars().all()
    purged: list[str] = []
    with events.acting("worker", "worker"):
        for item_id in ids:
            try:
                items_svc.hard_delete(session, item_id)
                purged.append(item_id)
            except Exception:
                session.rollback()
                log.exception("retention: could not purge %s", item_id)
    if purged:
        log.info("retention: permanently deleted %d items trashed over %d days ago",
                 len(purged), days)
    return purged
