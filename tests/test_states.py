"""Kist's state machines (kist/lifecycle.py): items, previews, jobs, views."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import png_bytes
from test_mcp import init, rpc

BROKEN_PNG = png_bytes((3, 3, 3))[:60]  # a real PNG header, cut off: won't decode


# ---------- item lifecycle ----------


def test_items_report_their_state(client, upload):
    item = upload()["item"]
    assert item["state"] == "active"
    trashed = client.delete(f"/api/items/{item['id']}").json()["item"]
    assert trashed["state"] == "trashed"


def test_trash_and_restore_are_idempotent(client, upload):
    item = upload()["item"]
    first = client.delete(f"/api/items/{item['id']}").json()["item"]
    again = client.delete(f"/api/items/{item['id']}").json()["item"]
    assert again["deleted_at"] == first["deleted_at"]
    assert again["modified_at"] == first["modified_at"]
    restored = client.post(f"/api/items/{item['id']}/restore").json()
    assert restored["state"] == "active"
    still = client.post(f"/api/items/{item['id']}/restore").json()
    assert still["modified_at"] == restored["modified_at"]


def test_reupload_of_a_trashed_item_says_it_is_in_the_trash(client, upload):
    data = png_bytes((9, 9, 9))
    item = upload("a.png", data)["item"]
    client.delete(f"/api/items/{item['id']}")
    again = upload("b.png", data)
    assert again["created"] is False
    assert again["item"]["id"] == item["id"]
    assert again["item"]["state"] == "trashed"


# ---------- views ----------


def test_unfiled_view_and_state_filter(client, upload):
    inbox = upload()["item"]
    tagged = upload(tags="x")["item"]
    folder = client.post("/api/folders", json={"name": "F"}).json()
    filed = upload(folder_ids=folder["id"])["item"]
    gone = upload()["item"]
    client.delete(f"/api/items/{gone['id']}")

    ids = lambda r: {i["id"] for i in r.json()["items"]}  # noqa: E731
    assert ids(client.get("/api/items?view=unfiled")) == {inbox["id"]}
    # folder=none still means "no folder", tagged or not
    assert ids(client.get("/api/items?folder=none")) == {inbox["id"], tagged["id"]}
    assert ids(client.get("/api/items?state=trashed")) == {gone["id"]}
    assert ids(client.get("/api/items?deleted=true")) == {gone["id"]}
    assert filed["id"] in ids(client.get("/api/items?state=active"))
    assert client.get("/api/items?view=nope").status_code == 422
    assert client.get("/api/items?state=nope").status_code == 422


def test_smart_folders_keep_the_unfiled_view(client):
    made = client.post(
        "/api/smart-folders", json={"name": "Inbox", "query": {"view": "unfiled"}}
    )
    assert made.status_code in (200, 201), made.text
    assert made.json()["query"]["view"] == "unfiled"


# ---------- ingest failures never lose the file ----------


def test_failed_ingest_puts_the_file_back(clean, monkeypatch):
    from kist.config import get_settings
    from kist.db import SessionLocal
    from kist.services import ingest
    from kist.services import tags as tags_svc

    def boom(*a, **k):
        raise RuntimeError("database went away")

    monkeypatch.setattr(tags_svc, "get_or_create", boom)
    staging = get_settings().staging_dir
    staging.mkdir(parents=True, exist_ok=True)
    src = staging / "inbox-keep.png"
    src.write_bytes(png_bytes((5, 6, 7)))
    with SessionLocal() as s, pytest.raises(RuntimeError):
        ingest.ingest_file(s, src, "keep.png", tag_names=["t"])
    assert src.is_file(), "the source file must survive a failed ingest"
    assert list((clean / "items").iterdir()) == []


def test_ingest_job_with_a_missing_file_fails(clean):
    from kist.db import SessionLocal
    from kist.services import ingest

    with SessionLocal() as s, pytest.raises(FileNotFoundError):
        ingest.run_ingest_job(s, {"path": str(clean / "nope.png"), "filename": "nope.png"})


# ---------- jobs ----------


def _queue_inbox_file(name: str, data: bytes):
    from kist.config import get_settings
    from kist.services import ingest

    inbox = get_settings().inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)
    f = inbox / name
    f.write_bytes(data)
    old = datetime.now().timestamp() - 60
    import os

    os.utime(f, (old, old))
    ingest.scan_inbox()


def _run_due_jobs(times=1):
    import sqlalchemy as sa

    from kist import worker
    from kist.db import SessionLocal
    from kist.models import Job

    for _ in range(times):
        with SessionLocal() as s:  # skip the retry backoff
            s.execute(sa.update(Job).values(run_after=datetime.now(UTC) - timedelta(seconds=1)))
            s.commit()
        worker.run_one()


def test_inbox_job_fails_visibly_and_can_be_retried(client, clean, monkeypatch):
    from kist.services import ingest
    from kist.services import tags as tags_svc

    # every attempt fails inside ingest, after the file has moved into the
    # item folder (the step that used to delete it)
    real = tags_svc.get_or_create
    monkeypatch.setattr(tags_svc, "get_or_create", lambda *a, **k: 1 / 0)
    orig_ingest = ingest.ingest_file

    def failing(session, path, filename, **kw):
        kw["tag_names"] = ["boom"]
        return orig_ingest(session, path, filename, **kw)

    monkeypatch.setattr(ingest, "ingest_file", failing)
    _queue_inbox_file("holiday.png", png_bytes((1, 1, 2)))
    _run_due_jobs(3)

    failed = client.get("/api/jobs?status=failed").json()
    assert len(failed) == 1
    job = failed[0]
    assert job["filename"] == "holiday.png" and job["attempts"] == 3
    assert "division by zero" in job["last_error"]
    parked = clean / "failed" / "holiday.png"
    assert parked.is_file(), "a failed inbox file is kept under its own name"
    assert client.get("/api/stats").json()["failed_jobs"] == 1

    # fix the cause, retry: the file goes back and is ingested
    monkeypatch.setattr(tags_svc, "get_or_create", real)
    monkeypatch.setattr(ingest, "ingest_file", orig_ingest)
    retried = client.post(f"/api/jobs/{job['id']}/retry").json()
    assert retried["status"] == "pending" and retried["attempts"] == 0
    assert not parked.exists()
    _run_due_jobs(1)
    assert client.get("/api/jobs?status=done").json()[0]["id"] == job["id"]
    assert client.get("/api/items?view=unfiled").json()["total"] == 1
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 409
    assert client.delete(f"/api/jobs/{job['id']}").json()["status"] == "dismissed"
    assert client.get("/api/jobs").json() == []


def test_jobs_left_running_by_a_dead_worker_are_requeued(clean):
    from kist.db import SessionLocal
    from kist.models import Job
    from kist.services import jobs

    with SessionLocal() as s:
        stale = Job(kind="ingest", payload={}, status="running", attempts=1,
                    started_at=datetime.now(UTC) - timedelta(hours=2))
        fresh = Job(kind="ingest", payload={}, status="running", attempts=1,
                    started_at=datetime.now(UTC))
        s.add_all([stale, fresh])
        s.commit()
        assert jobs.reap_stale(s, lease_seconds=900) == 1
        s.refresh(stale)
        s.refresh(fresh)
        assert stale.status == "pending" and "worker stopped" in stale.last_error
        assert fresh.status == "running"


def test_job_status_is_constrained(clean):
    import sqlalchemy as sa
    from sqlalchemy.exc import IntegrityError

    from kist.db import SessionLocal

    with SessionLocal() as s, pytest.raises(IntegrityError):
        s.execute(sa.text("INSERT INTO jobs (kind, payload, status) VALUES ('x', '{}', 'weird')"))
        s.commit()


# ---------- preview status ----------


def test_preview_statuses(client, upload, clean, monkeypatch):
    from kist.services import thumbs

    ok = upload()["item"]
    assert ok["preview_status"] == "ready" and ok["has_thumb"]
    other = upload("notes.xyz", b"just some bytes", mime="application/octet-stream")["item"]
    assert other["preview_status"] == "none" and not other["has_thumb"]
    assert client.post(f"/api/items/{other['id']}/preview").status_code == 409

    monkeypatch.setattr(
        thumbs, "generate_previews_isolated",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad PNG data")),
    )
    broken = upload("broken.png", BROKEN_PNG)["item"]
    assert broken["preview_status"] == "failed"
    assert "bad PNG data" in broken["preview_error"]
    assert not broken["has_thumb"]
    assert client.get("/api/stats").json()["preview_failed"] == 1

    queued = client.post(f"/api/items/{broken['id']}/preview")
    assert queued.status_code == 202 and queued.json()["preview_status"] == "pending"
    # asking twice doesn't queue two jobs
    client.post(f"/api/items/{broken['id']}/preview")
    assert len(client.get("/api/jobs?status=pending").json()) == 1
    _run_due_jobs(1)
    after = client.get(f"/api/items/{broken['id']}").json()
    assert after["preview_status"] == "failed"  # still broken; the job itself is done
    assert client.get("/api/jobs?status=done").json()[0]["item_id"] == broken["id"]


def test_preview_status_is_in_meta(client, upload, clean, monkeypatch):
    from kist.services import thumbs

    monkeypatch.setattr(
        thumbs, "generate_previews_isolated",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad PNG data")),
    )
    broken = upload("broken.png", BROKEN_PNG)["item"]
    meta = json.loads((clean / "items" / broken["id"] / "meta.json").read_text())
    assert meta["preview_status"] == "failed"


def test_preview_status_for_backfill():
    from kist.lifecycle import preview_status_for

    assert preview_status_for("image", 100) == "ready"
    assert preview_status_for("other", None) == "none"
    assert preview_status_for("pdf", None) == "failed"


# ---------- verify ----------


def test_verify_reports_drift(client, upload, clean, capsys):
    from kist import cli

    item = upload()["item"]
    assert cli.verify() == 0
    (clean / "items" / item["id"] / "meta.json").unlink()
    assert cli.verify() == 2
    assert "meta.json missing" in capsys.readouterr().out


# ---------- MCP ----------


def test_mcp_speaks_the_same_states(client, upload):
    inbox = upload()["item"]
    upload(tags="t")
    init(client)

    def call(name, args, i):
        res = rpc(client, "tools/call", {"name": name, "arguments": args}, id_=i)["result"]
        assert not res.get("isError"), res
        return json.loads(res["content"][0]["text"])

    found = call("search_items", {"view": "unfiled"}, 2)
    assert [i["id"] for i in found["items"]] == [inbox["id"]]
    assert found["items"][0]["web_url"] == f"/all?item={inbox['id']}"

    trashed = call("trash_item", {"id": inbox["id"]}, 3)
    assert trashed["state"] == "trashed" and trashed["web_url"].startswith("/trash?")
    in_trash = call("search_items", {"state": "trashed"}, 4)
    assert [i["id"] for i in in_trash["items"]] == [inbox["id"]]
    assert call("restore_item", {"id": inbox["id"]}, 5)["state"] == "active"
    tools = {t["name"] for t in rpc(client, "tools/list", id_=6)["result"]["tools"]}
    assert {"restore_item", "retry_preview", "list_jobs", "retry_job"} <= tools
