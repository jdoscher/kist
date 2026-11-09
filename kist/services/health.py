"""GET /api/health/deep: is every part of Kist working, not just the DB?"""

import logging
import shutil
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..lifecycle import JobStatus, PreviewStatus
from ..models import Item, Job
from . import verify as verify_svc

log = logging.getLogger("kist.health")

HEARTBEAT_FILE = "worker-heartbeat"
# the worker beats every 15 s; this allows a few missed beats
WORKER_STALE_SECONDS = 90
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


def heartbeat_path() -> Path:
    return get_settings().state_dir / HEARTBEAT_FILE


def heartbeat_age() -> float | None:
    try:
        return max(0.0, time.time() - heartbeat_path().stat().st_mtime)
    except OSError:
        return None


@lru_cache
def migration_head() -> str | None:
    from alembic.script import ScriptDirectory

    try:
        return ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()
    except Exception:
        log.exception("could not read the migration scripts")
        return None


def version_info() -> dict:
    return {"version": __version__, "commit": get_settings().git_commit or "unknown"}


def _guard(fn):
    try:
        return fn()
    except Exception as exc:
        log.warning("health check %s failed: %s", fn.__name__, exc)
        return {"ok": False, "error": type(exc).__name__}


def deep(session: Session) -> dict:
    settings = get_settings()
    now = datetime.now(UTC)
    checks: dict[str, dict] = {}

    def db():
        started = time.monotonic()
        session.execute(sa.text("SELECT 1"))
        return {"ok": True, "latency_ms": round((time.monotonic() - started) * 1000, 1)}

    checks["db"] = _guard(db)
    if not checks["db"]["ok"]:
        session.rollback()

    def migrations():
        current = session.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        head = migration_head()
        return {"ok": current is not None and current == head, "current": current, "head": head}

    def worker():
        age = heartbeat_age()
        return {
            "ok": age is not None and age < WORKER_STALE_SECONDS,
            "heartbeat_age_s": None if age is None else round(age, 1),
        }

    def library():
        state = settings.state_dir
        state.mkdir(parents=True, exist_ok=True)
        probe = state / f".write-test-{time.monotonic_ns()}"
        try:
            probe.write_text("ok")
            writable = True
        except OSError:
            writable = False
        finally:
            probe.unlink(missing_ok=True)
        usage = shutil.disk_usage(settings.library_dir)
        mounted = settings.items_dir.is_dir()
        return {
            "ok": writable and mounted,
            "writable": writable,
            "items_dir": mounted,
            "free_bytes": usage.free,
        }

    def jobs():
        lease_cutoff = now - timedelta(seconds=settings.job_lease_seconds)
        failed, stuck, oldest = session.execute(
            sa.select(
                sa.func.count().filter(Job.status == JobStatus.FAILED),
                sa.func.count().filter(
                    Job.status == JobStatus.RUNNING, Job.started_at < lease_cutoff
                ),
                sa.func.min(Job.created_at).filter(Job.status == JobStatus.PENDING),
            )
        ).one()
        return {
            "ok": stuck == 0,
            "failed": failed,
            "stuck": stuck,
            "oldest_pending_age_s": (
                None if oldest is None else round((now - oldest).total_seconds(), 1)
            ),
        }

    def previews():
        failed = session.execute(
            sa.select(sa.func.count()).where(
                Item.deleted_at.is_(None),
                Item.preview_status == PreviewStatus.FAILED,
                Item.modified_at >= now - timedelta(hours=24),
            )
        ).scalar_one()
        return {"ok": True, "failed_24h": failed}

    def verify():
        last = verify_svc.last_result() or {}
        drift = last.get("drift")
        return {"ok": not drift, "last_run": last.get("last_run"), "drift": drift}

    for fn in (migrations, worker, library, jobs, previews, verify):
        checks[fn.__name__] = _guard(fn)
        if fn in (migrations, jobs, previews) and not checks[fn.__name__].get("ok", True):
            session.rollback()
    # what has to work for Kist to work; jobs/previews/verify are reported
    # for a person to judge
    essential = ("db", "migrations", "worker", "library")
    return {
        "ok": all(checks[name].get("ok") for name in essential),
        "checks": checks,
        **version_info(),
    }
