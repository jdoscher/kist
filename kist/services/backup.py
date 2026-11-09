"""`kist backup`: a database dump plus a restic snapshot of the library.

1. pg_dump writes library/.kist/db.sql.gz (the library is the source of
   truth, but a dump restores faster and keeps the job history).
2. restic backs the whole library up to KIST_BACKUP_REPO, with the password
   in RESTIC_PASSWORD or RESTIC_PASSWORD_FILE. The repository is created on
   first use.
3. The outcome goes to library/.kist/backup.json, which archive health and
   the worker's schedule (KIST_BACKUP_INTERVAL_HOURS) read.

A missing tool or setting is skipped with a message saying what to do; the
result then says not ok.
"""

import gzip
import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from ..config import get_settings
from . import meta as meta_svc

log = logging.getLogger("kist.backup")

STATE_FILE = "backup.json"
DUMP_FILE = "db.sql.gz"
DUMP_TIMEOUT = 3600
BACKUP_TIMEOUT = 24 * 3600


def state_path() -> Path:
    return get_settings().state_dir / STATE_FILE


def last_result() -> dict | None:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def redact(repo: str) -> str:
    """A repository location without any password in it."""
    return re.sub(r"(://[^:/@]+:)[^@]*@", r"\1***@", repo)


def _pg_env() -> dict[str, str]:
    url = make_url(get_settings().database_url)
    env = dict(os.environ)
    host = url.host or url.query.get("host")
    if isinstance(host, tuple):
        host = host[0]
    port = url.port or url.query.get("port")
    if isinstance(port, tuple):
        port = port[0]
    for key, value in (("PGHOST", host), ("PGPORT", port), ("PGUSER", url.username),
                       ("PGPASSWORD", url.password), ("PGDATABASE", url.database)):
        if value:
            env[key] = str(value)
    return env


def dump_database() -> dict:
    """pg_dump to library/.kist/db.sql.gz (via a temp file + rename)."""
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        return {"ok": False, "error": "pg_dump isn't installed here; database dump skipped"}
    state = get_settings().state_dir
    state.mkdir(parents=True, exist_ok=True)
    final = state / DUMP_FILE
    tmp = state / f".{DUMP_FILE}.tmp"
    try:
        with gzip.open(tmp, "wb") as out:
            proc = subprocess.Popen(
                [pg_dump, "--no-owner", "--no-privileges"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_pg_env(),
            )
            assert proc.stdout is not None
            for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b""):
                out.write(chunk)
            _, err = proc.communicate(timeout=DUMP_TIMEOUT)
        if proc.returncode != 0:
            tmp.unlink(missing_ok=True)
            message = err.decode(errors="replace").strip().splitlines()
            return {"ok": False, "error": f"pg_dump failed: {(message or ['?'])[-1][:300]}"}
        os.replace(tmp, final)
        return {"ok": True, "path": str(final), "bytes": final.stat().st_size}
    except (OSError, subprocess.SubprocessError) as exc:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": f"database dump failed: {exc}"}


def _restic(args: list[str], env: dict, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["restic", *args], capture_output=True, text=True, env=env, timeout=timeout
    )


def run_restic() -> dict:
    settings = get_settings()
    repo = settings.backup_repo.strip()
    if not repo:
        return {"ok": False, "skipped": True,
                "error": "KIST_BACKUP_REPO isn't set; no backup was made"}
    if not shutil.which("restic"):
        return {"ok": False, "skipped": True,
                "error": "restic isn't installed here; no backup was made "
                         "(it is in the Kist image: run `docker compose exec worker kist backup`)"}
    if not (os.environ.get("RESTIC_PASSWORD") or os.environ.get("RESTIC_PASSWORD_FILE")):
        return {"ok": False, "skipped": True,
                "error": "set RESTIC_PASSWORD or RESTIC_PASSWORD_FILE for the backup repository"}
    env = dict(os.environ, RESTIC_REPOSITORY=repo)
    # make the repository on first use
    if _restic(["cat", "config"], env, 300).returncode != 0:
        made = _restic(["init"], env, 600)
        if made.returncode != 0:
            return {"ok": False, "error": "restic init failed: "
                    + (made.stderr.strip().splitlines() or ["?"])[-1][:300]}
    library = str(settings.library_dir)
    proc = _restic(
        ["backup", "--json", "--exclude", str(settings.staging_dir),
         "--exclude", str(settings.state_dir / "worker-heartbeat"), library],
        env, BACKUP_TIMEOUT,
    )
    summary: dict = {}
    for line in proc.stdout.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if isinstance(msg, dict) and msg.get("message_type") == "summary":
            summary = msg
    if proc.returncode not in (0, 3) or not summary:  # 3: some files unreadable
        return {"ok": False, "error": "restic backup failed: "
                + (proc.stderr.strip().splitlines() or ["no summary"])[-1][:300]}
    out = {
        "ok": proc.returncode == 0,
        "snapshot_id": summary.get("snapshot_id"),
        "files": summary.get("total_files_processed"),
        "bytes": summary.get("total_bytes_processed"),
    }
    if proc.returncode == 3:
        out["error"] = "some files couldn't be read; see the worker log"
    return out


def run() -> dict:
    """Dump, back up, record. Returns the recorded result."""
    started = time.monotonic()
    db = dump_database()
    restic = run_restic()
    result = {
        "last_at": datetime.now(UTC).isoformat(),
        "ok": bool(restic.get("ok")),
        "repo": redact(get_settings().backup_repo) or None,
        "snapshot_id": restic.get("snapshot_id"),
        "files": restic.get("files"),
        "bytes": restic.get("bytes"),
        "skipped": bool(restic.get("skipped")),
        "error": restic.get("error"),
        "db_dump": db,
        "duration_s": round(time.monotonic() - started, 1),
    }
    previous = last_result() or {}
    # "last backed up" means the last good one, even after a failed run
    result["last_ok_at"] = result["last_at"] if result["ok"] else previous.get("last_ok_at")
    try:
        meta_svc.atomic_write_text(state_path(), json.dumps(result, indent=2))
    except OSError:
        log.exception("could not record the backup result")
    return result


def due(now: datetime | None = None) -> bool:
    settings = get_settings()
    if settings.backup_interval_hours <= 0 or not settings.backup_repo:
        return False
    last = (last_result() or {}).get("last_at")
    if not last:
        return True
    try:
        age = (now or datetime.now(UTC)) - datetime.fromisoformat(last)
    except ValueError:
        return True
    return age.total_seconds() >= settings.backup_interval_hours * 3600
