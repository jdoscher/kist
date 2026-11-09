"""GET /api/archive-health: is the collection intact, and does a second
copy exist? Fixity, the last verify, the last backup, free disk, and a
3-2-1 checklist (most items are measured; "offsite" is the user's word,
stored in library/.kist/checklist.json)."""

import json
import shutil
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Item
from . import backup as backup_svc
from . import fixity as fixity_svc
from . import meta as meta_svc
from . import validation
from . import verify as verify_svc

CHECKLIST_FILE = "checklist.json"
RECENT_BACKUP_DAYS = 7
# what the user can tick; the rest is measured
USER_ITEMS = ("offsite",)


def _checklist_path():
    return get_settings().state_dir / CHECKLIST_FILE


def user_checklist() -> dict:
    try:
        data = json.loads(_checklist_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def set_checklist(item_id: str, done: bool) -> dict:
    if item_id not in USER_ITEMS:
        raise validation.ValidationError(
            f"{item_id!r} is measured by Kist; only {', '.join(USER_ITEMS)} can be set"
        )
    data = user_checklist()
    data[item_id] = {"done": bool(done), "at": datetime.now(UTC).isoformat()}
    meta_svc.atomic_write_text(_checklist_path(), json.dumps(data, indent=2))
    return data


def _hours_since(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        return round((now - datetime.fromisoformat(value)).total_seconds() / 3600, 1)
    except ValueError:
        return None


def health(session: Session) -> dict:
    settings = get_settings()
    now = datetime.now(UTC)
    total, checked = session.execute(
        sa.select(
            sa.func.count(),
            sa.func.count().filter(Item.fixity_checked_at > now - timedelta(days=30)),
        ).select_from(Item)
    ).one()
    failed = [
        {"id": i.id, "filename": i.filename, "failed_at": i.fixity_failed_at,
         "expected_sha256": i.sha256}
        for i in fixity_svc.failed_items(session)
    ]
    last_verify = verify_svc.last_result() or {}
    verify = {
        "last_run": last_verify.get("last_run"),
        "items_checked": last_verify.get("items_checked"),
        "drift": last_verify.get("drift"),
        "ok": last_verify.get("last_run") is not None and not last_verify.get("drift"),
    }
    last_backup = backup_svc.last_result() or {}
    age_h = _hours_since(last_backup.get("last_ok_at"), now)
    backup = {
        "last_at": last_backup.get("last_at"),
        "last_ok_at": last_backup.get("last_ok_at"),
        "ok": bool(last_backup.get("ok")),
        "repo_set": bool(settings.backup_repo.strip()),
        "age_h": age_h,
        "error": last_backup.get("error"),
        "snapshot_id": last_backup.get("snapshot_id"),
        # the library snapshot is the backup; a failed database dump only
        # makes restores slower, so it's a warning, not a failure
        "db_dump_ok": (last_backup.get("db_dump") or {}).get("ok"),
        "db_dump_error": (last_backup.get("db_dump") or {}).get("error"),
        "interval_hours": settings.backup_interval_hours,
    }
    try:
        free = shutil.disk_usage(settings.library_dir).free
    except OSError:
        free = None
    ticked = user_checklist()
    checklist = [
        {"id": "repo", "label": "A backup repository is configured",
         "done": backup["repo_set"]},
        {"id": "recent", "label": f"Backed up within {RECENT_BACKUP_DAYS} days",
         "done": age_h is not None and age_h <= RECENT_BACKUP_DAYS * 24},
        {"id": "offsite", "label": "A copy is kept somewhere else (offsite)",
         "done": bool((ticked.get("offsite") or {}).get("done"))},
        {"id": "verify", "label": "The last verify found no problems", "done": verify["ok"]},
    ]
    return {
        "fixity": {"checked_30d": checked, "total": total, "failed": failed},
        "verify": verify,
        "backup": backup,
        "disk": {"free_bytes": free},
        "trash_retention_days": settings.trash_retention_days,
        "checklist": checklist,
    }
