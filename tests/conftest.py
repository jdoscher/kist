"""Test setup.

Unit tests run anywhere. Integration tests need a throwaway Postgres 16:

    KIST_TEST_DATABASE_URL=postgresql+psycopg://kist:kist@localhost:5432/kist_test pytest

The database is wiped between tests — never point this at a real library.
"""

import io
import os
import shutil
import tempfile
from pathlib import Path

import pytest

TOKEN = "test-token-0123456789abcdef"
LIBRARY = Path(tempfile.mkdtemp(prefix="kist-test-library-"))
TEST_DB = os.environ.get("KIST_TEST_DATABASE_URL")

# must happen before kist is imported: settings and the engine are module-level
os.environ["KIST_TOKEN"] = TOKEN
os.environ["KIST_LIBRARY_DIR"] = str(LIBRARY)
if TEST_DB:
    os.environ["KIST_DATABASE_URL"] = TEST_DB

TABLES = "item_tags, item_folders, items, tags, smart_folders, folders, jobs"


@pytest.fixture(scope="session")
def database():
    if not TEST_DB:
        pytest.skip("set KIST_TEST_DATABASE_URL to run integration tests")
    from kist import migrate

    migrate.main()
    yield


@pytest.fixture
def clean(database):
    import sqlalchemy as sa

    from kist.db import engine

    with engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    for child in LIBRARY.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    yield LIBRARY


@pytest.fixture(scope="session")
def app_client(database):
    from fastapi.testclient import TestClient

    from kist.app import app

    # one client for the whole run: the MCP session manager can only be
    # started once per process
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        yield c


@pytest.fixture
def client(app_client, clean):
    app_client.cookies.clear()
    return app_client


def png_bytes(color=(200, 40, 40), size=(640, 480)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def upload(client):
    """upload(name, data=None, **form) -> response JSON (asserts 201)."""
    counter = iter(range(1, 10_000))

    def _upload(name="photo.png", data=None, mime="image/png", **form):
        if data is None:
            n = next(counter)
            data = png_bytes(color=(n * 37 % 256, n * 91 % 256, n * 13 % 256))
        res = client.post("/api/items", files={"file": (name, data, mime)}, data=form)
        assert res.status_code == 201, res.text
        return res.json()

    return _upload
