"""Run Alembic migrations under a Postgres advisory lock.

Both the api and worker containers run this at startup; the lock keeps
concurrent upgrades from racing. Retries while the database is starting up.
"""

import sys
import time
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from .config import get_settings

LOCK_KEY = 7342190  # arbitrary app-wide advisory lock id


def main() -> None:
    settings = get_settings()
    engine = sa.create_engine(settings.database_url)

    deadline = time.monotonic() + 120
    while True:
        try:
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
            break
        except Exception as exc:
            if time.monotonic() > deadline:
                print(f"database never became ready: {exc}", file=sys.stderr)
                sys.exit(1)
            time.sleep(1)

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)

    with engine.connect() as conn:
        conn.execute(sa.text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_KEY})
        try:
            command.upgrade(cfg, "head")
        finally:
            conn.execute(sa.text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
    print("migrations up to date")


if __name__ == "__main__":
    main()
