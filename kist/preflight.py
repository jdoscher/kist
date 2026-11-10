"""First-start check: can Kist write to its library? (Milestone 6)

A fresh `docker compose up` used to fail deep inside the app or worker with
a PermissionError traceback when Docker had created ./library as root while
Kist runs as uid 1000. Now the entrypoint (`python -m kist.preflight`), the
app's startup and the worker's start all check first and stop with one line
that says what to run.

KIST_LIBRARY (the host path docker-compose mounts at /library) is passed
into the containers only so this message can name it.
"""

import logging
import os
import sys
import tempfile
from pathlib import Path

log = logging.getLogger("kist.preflight")


def _nearest_existing(path: Path) -> Path:
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def _writable(path: Path) -> bool:
    if not os.access(path, os.W_OK | os.X_OK):
        return False
    try:  # os.access can say yes on a read-only mount or a squashed NFS share
        with tempfile.NamedTemporaryFile(dir=path, prefix=".kist-write-check-"):
            pass
    except OSError:
        return False
    return True


def library_problem(library_dir: Path, subdirs: tuple[Path, ...] = (),
                    host_path: str | None = None, uid: int | None = None,
                    gid: int | None = None) -> str | None:
    """None when Kist can write everywhere it needs to, else the one line to
    log. A directory that doesn't exist yet only needs a writable parent."""
    uid = os.getuid() if uid is None else uid
    gid = os.getgid() if gid is None else gid
    host = host_path or os.environ.get("KIST_LIBRARY") or "<host library path>"
    for path in (library_dir, *subdirs):
        target = _nearest_existing(Path(path))
        if not target.is_dir() or _writable(target):
            continue
        try:
            owner = target.stat().st_uid
        except OSError:
            owner = "?"
        return (f"Kist can't write to {target} (owned by uid {owner}, Kist runs as uid {uid}): "
                f"run  sudo chown -R {uid}:{gid} {host}  or set KIST_UID/KIST_GID")
    return None


def settings_problem() -> str | None:
    from .config import get_settings

    s = get_settings()
    return library_problem(
        s.library_dir, (s.items_dir, s.inbox_dir, s.staging_dir, s.state_dir, *s.import_dirs))


def require_writable_library() -> None:
    """Log the problem and exit (status 1) when the library isn't writable."""
    problem = settings_problem()
    if problem:
        log.error(problem)
        raise SystemExit(1)


def main() -> int:
    problem = settings_problem()
    if problem:
        print(problem, file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
