"""Fixity, backups and archive health."""

import json
import stat
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from kist import cli


def corrupt(library, item):
    original = library / "items" / item["id"] / f"original.{item['ext']}"
    data = original.read_bytes()
    original.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    return original, data


# ---------- fixity ----------


def test_fixity_rehashes_a_share_a_day_and_flags_damage(client, upload, clean):
    from kist.db import SessionLocal
    from kist.services import fixity

    items = [upload()["item"] for _ in range(3)]
    original, data = corrupt(clean, items[1])
    with SessionLocal() as s:
        assert fixity.due(s) == 1  # ceil(3 / 30)
    # by hand: every original, oldest-checked first
    assert cli.fixity(limit=10) == 2

    lines = [json.loads(x) for x in
             (clean / "items" / items[1]["id"] / "fixity.jsonl").read_text().splitlines()]
    assert lines[-1]["ok"] is False and lines[-1]["expected"] == items[1]["sha256"]
    good = (clean / "items" / items[0]["id"] / "fixity.jsonl").read_text().splitlines()
    assert json.loads(good[-1])["ok"] is True

    assert client.get("/api/stats").json()["fixity_failed"] == 1
    failed = client.get("/api/items?fixity=failed").json()["items"]
    assert [i["id"] for i in failed] == [items[1]["id"]]
    deep = client.get("/api/health/deep").json()
    assert deep["checks"]["fixity"] == {"ok": False, "failed": 1}
    health = client.get("/api/archive-health").json()
    assert [f["id"] for f in health["fixity"]["failed"]] == [items[1]["id"]]
    assert health["fixity"]["checked_30d"] == 3 and health["fixity"]["total"] == 3

    # restored from a backup: the next check clears it
    original.write_bytes(data)
    assert cli.fixity(limit=10) == 0
    assert client.get("/api/stats").json()["fixity_failed"] == 0


def test_fixity_checks_are_not_edits(client, upload, clean):
    """A re-hash leaves modified_at alone: meta.json still matches (verify),
    "Recently edited" doesn't list checked items, and an editor's
    base_modified_at stays valid (no false 409)."""
    item = upload()["item"]
    before = client.get(f"/api/items/{item['id']}").json()["modified_at"]
    corrupt(clean, item)
    assert cli.fixity(limit=10) == 2  # a failed check (exit code 2)
    after = client.get(f"/api/items/{item['id']}").json()
    assert after["modified_at"] == before
    meta = json.loads((clean / "items" / item["id"] / "meta.json").read_text())
    assert datetime.fromisoformat(meta["modified_at"]) == datetime.fromisoformat(before)
    r = client.patch(f"/api/items/{item['id']}", json={"notes": "x", "base_modified_at": before})
    assert r.status_code == 200


def test_fixity_quota_spreads_the_work(client, upload):
    from kist.db import SessionLocal
    from kist.services import fixity

    for _ in range(3):
        upload()
    with SessionLocal() as s:
        assert fixity.run_some(s) == (1, 0)
        assert fixity.run_some(s) == (0, 0)  # today's share is done
        assert fixity.due(s, now=datetime.now(UTC) + timedelta(days=2)) == 1


def test_missing_original_fails_fixity(client, upload, clean):
    item = upload()["item"]
    (clean / "items" / item["id"] / f"original.{item['ext']}").unlink()
    assert cli.fixity(limit=10) == 2
    line = json.loads((clean / "items" / item["id"] / "fixity.jsonl").read_text())
    assert line["ok"] is False and line["sha256"] is None and "unreadable" in line["error"]


def test_rebuild_restores_fixity_dates(client, upload, clean):
    good, bad = upload()["item"], upload()["item"]
    corrupt(clean, bad)
    cli.fixity(limit=10)
    before = client.get("/api/archive-health").json()["fixity"]
    assert cli.rebuild_index(wipe=True) == 0
    after = client.get("/api/archive-health").json()["fixity"]
    assert after["checked_30d"] == before["checked_30d"] == 2
    assert [f["id"] for f in after["failed"]] == [bad["id"]]
    assert good["id"] not in json.dumps(after["failed"])


# ---------- backups ----------

FAKE_RESTIC = """#!/bin/sh
# a stand-in for restic: logs its calls, keeps a marker for "the repository"
echo "$RESTIC_REPOSITORY $*" >> "$FAKE_LOG"
case "$1" in
  cat) test -f "$RESTIC_REPOSITORY/config" ;;
  init) mkdir -p "$RESTIC_REPOSITORY" && touch "$RESTIC_REPOSITORY/config" ;;
  backup)
    echo '{"message_type":"status","percent_done":0.5}'
    printf '%s%s\n' '{"message_type":"summary","snapshot_id":"abc123",' \
      '"total_files_processed":7,"total_bytes_processed":4096}'
    ;;
esac
"""

FAKE_PG_DUMP = """#!/bin/sh
echo "-- dump of $PGDATABASE on $PGHOST"
echo "CREATE TABLE items ();"
"""


def _script(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def fakebin(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _script(bin_dir / "pg_dump", FAKE_PG_DUMP)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "restic.log"))
    monkeypatch.delenv("RESTIC_PASSWORD", raising=False)
    monkeypatch.delenv("RESTIC_PASSWORD_FILE", raising=False)
    return bin_dir


def test_backup_without_restic_is_skipped_with_a_reason(client, clean, fakebin, monkeypatch,
                                                         tmp_path, capsys):
    import gzip

    from sqlalchemy.engine import make_url

    from kist.config import get_settings

    monkeypatch.setattr(get_settings(), "backup_repo", str(tmp_path / "repo"))
    assert cli.backup() == 2
    err = capsys.readouterr().err
    assert "restic isn't installed here" in err
    result = json.loads((clean / ".kist" / "backup.json").read_text())
    assert result["ok"] is False and result["skipped"] is True
    # the database dump was still made
    dump = gzip.decompress((clean / ".kist" / "db.sql.gz").read_bytes()).decode()
    assert "CREATE TABLE" in dump
    assert dump.startswith(f"-- dump of {make_url(get_settings().database_url).database} on ")


def test_backup_needs_a_repo_and_a_password(client, clean, fakebin, monkeypatch, tmp_path):
    from kist.config import get_settings

    _script(fakebin / "restic", FAKE_RESTIC)
    monkeypatch.setattr(get_settings(), "backup_repo", "")
    assert cli.backup() == 2
    assert "KIST_BACKUP_REPO" in json.loads((clean / ".kist" / "backup.json").read_text())["error"]
    monkeypatch.setattr(get_settings(), "backup_repo", str(tmp_path / "repo"))
    assert cli.backup() == 2
    assert "RESTIC_PASSWORD" in json.loads((clean / ".kist" / "backup.json").read_text())["error"]


def test_backup_with_restic(client, clean, fakebin, monkeypatch, tmp_path):
    from kist.config import get_settings
    from kist.services import backup

    _script(fakebin / "restic", FAKE_RESTIC)
    repo = tmp_path / "repo"
    monkeypatch.setattr(get_settings(), "backup_repo", str(repo))
    monkeypatch.setenv("RESTIC_PASSWORD", "pw")
    assert cli.backup() == 0
    result = json.loads((clean / ".kist" / "backup.json").read_text())
    assert result["ok"] and result["snapshot_id"] == "abc123" and result["files"] == 7
    assert result["last_ok_at"] == result["last_at"] and result["db_dump"]["ok"]
    calls = (tmp_path / "restic.log").read_text().splitlines()
    assert [c.split()[1] for c in calls] == ["cat", "init", "backup"]
    assert str(clean) in calls[-1] and "--exclude" in calls[-1]
    # the repository exists now: no second init
    assert cli.backup() == 0
    assert [c.split()[1] for c in (tmp_path / "restic.log").read_text().splitlines()][3:] == [
        "cat", "backup"]

    health = client.get("/api/archive-health").json()
    assert health["backup"]["ok"] and health["backup"]["repo_set"]
    assert health["backup"]["db_dump_ok"] is True and health["backup"]["db_dump_error"] is None
    assert health["backup"]["age_h"] is not None and health["backup"]["age_h"] < 1
    done = {c["id"]: c["done"] for c in health["checklist"]}
    assert done["repo"] and done["recent"] and not done["offsite"]

    # the worker's schedule
    monkeypatch.setattr(get_settings(), "backup_interval_hours", 0)
    assert not backup.due()
    monkeypatch.setattr(get_settings(), "backup_interval_hours", 24)
    assert not backup.due()
    assert backup.due(now=datetime.now(UTC) + timedelta(hours=25))


def test_failed_backup_keeps_the_last_good_one(client, clean, fakebin, monkeypatch, tmp_path):
    from kist.config import get_settings

    _script(fakebin / "restic", FAKE_RESTIC)
    monkeypatch.setattr(get_settings(), "backup_repo", str(tmp_path / "repo"))
    monkeypatch.setenv("RESTIC_PASSWORD", "pw")
    assert cli.backup() == 0
    good = json.loads((clean / ".kist" / "backup.json").read_text())["last_ok_at"]
    _script(fakebin / "restic", "#!/bin/sh\necho 'Fatal: repository is locked' >&2\nexit 1\n")
    assert cli.backup() == 1
    result = json.loads((clean / ".kist" / "backup.json").read_text())
    assert not result["ok"] and result["last_ok_at"] == good
    assert "locked" in result["error"] or "init failed" in result["error"]


def test_redacts_passwords_in_repo_urls():
    from kist.services.backup import redact

    assert redact("rest:https://u:secret@host:8000/kist") == "rest:https://u:***@host:8000/kist"
    assert redact("/mnt/usb/kist") == "/mnt/usb/kist"


# ---------- archive health ----------


def test_archive_health_shape_and_checklist(client, clean):
    health = client.get("/api/archive-health").json()
    assert set(health) >= {"fixity", "verify", "backup", "disk", "checklist"}
    assert health["disk"]["free_bytes"] > 0
    assert health["backup"] == {**health["backup"], "repo_set": False, "ok": False}
    assert [c["id"] for c in health["checklist"]] == ["repo", "recent", "offsite", "verify"]

    res = client.post("/api/archive-health/checklist", json={"id": "offsite", "done": True})
    assert res.status_code == 200
    assert {c["id"]: c["done"] for c in res.json()["checklist"]}["offsite"] is True
    assert json.loads((clean / ".kist" / "checklist.json").read_text())["offsite"]["done"]
    res = client.post("/api/archive-health/checklist", json={"id": "recent", "done": True})
    assert res.status_code == 422 and "measured by Kist" in res.text

    from kist.db import SessionLocal
    from kist.services import verify

    with SessionLocal() as s:
        verify.save_result(verify.run(s))
    done = {c["id"]: c["done"] for c in client.get("/api/archive-health").json()["checklist"]}
    assert done["verify"] is True


def test_archive_health_scopes(client):
    reader = client.post("/api/tokens", json={"name": "r", "scopes": ["read"]}).json()["token"]
    r = TestClient(client.app, headers={"Authorization": f"Bearer {reader}"})
    assert r.get("/api/archive-health").status_code == 200
    assert r.post("/api/archive-health/checklist",
                  json={"id": "offsite", "done": True}).status_code == 403

