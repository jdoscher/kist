"""Data integrity (audit 2026-11-09): the durable copy, races, validation."""

import json
import os
import threading
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from conftest import png_bytes

from kist import cli

# ---------- meta.json / folders.json writes ----------


def test_concurrent_meta_writes_never_leave_a_torn_file(client, upload, clean):
    from kist.db import SessionLocal
    from kist.services import items as items_svc
    from kist.services import meta as meta_svc

    item = upload()["item"]
    with SessionLocal() as s:
        row = items_svc.get_item(s, item["id"])
        row.notes = "x" * 20_000
        errors = []

        def write():
            try:
                for _ in range(50):
                    meta_svc.write_meta(row)
            except Exception as exc:  # pragma: no cover - the failure we look for
                errors.append(exc)

        threads = [threading.Thread(target=write) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert errors == []
    path = clean / "items" / item["id"] / "meta.json"
    assert json.loads(path.read_text())["notes"] == "x" * 20_000
    assert [p.name for p in path.parent.glob(".*tmp")] == []


def test_a_failed_meta_write_does_not_fail_the_edit(client, upload, monkeypatch, capsys):
    from kist.services import meta as meta_svc

    item = upload()["item"]

    def broken(_item):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(meta_svc, "write_meta", broken)
    res = client.patch(f"/api/items/{item['id']}", json={"notes": "saved in the db"})
    assert res.status_code == 200 and res.json()["notes"] == "saved in the db"
    assert meta_svc.pending() == [item["id"]]
    monkeypatch.undo()

    assert cli.verify() == 2
    out = capsys.readouterr().out
    assert f"{item['id']}: an earlier write to disk failed" in out
    assert "differs in notes" in out
    assert cli.rewrite_meta() == 0
    assert meta_svc.pending() == []
    assert cli.verify() == 0


def test_meta_follows_the_newest_commit_not_the_last_writer(client, upload, clean):
    """A writer that committed first but reaches the disk last must not put
    its older state back (it re-reads the row under the item's lock)."""
    from kist.db import SessionLocal
    from kist.services import items as items_svc
    from kist.services import meta as meta_svc

    item = upload()["item"]
    with SessionLocal() as slow:
        row = items_svc.get_item(slow, item["id"])
        row.notes = "older"
        slow.commit()  # committed, meta.json not written yet...
        client.patch(f"/api/items/{item['id']}", json={"notes": "newer"})
        meta_svc.sync_item(slow, item["id"])  # ...and now it is
    meta = json.loads((clean / "items" / item["id"] / "meta.json").read_text())
    assert meta["notes"] == "newer"


def test_folder_rename_rewrites_folder_paths(client, upload, clean):
    folder = client.post("/api/folders", json={"name": "Old"}).json()
    item = upload(folder_id=folder["id"])["item"]
    client.patch(f"/api/folders/{folder['id']}", json={"name": "New"})
    meta = json.loads((clean / "items" / item["id"] / "meta.json").read_text())
    assert meta["folder_paths"] == [["New"]]


def test_verify_checks_folders_json(client, clean, capsys):
    folder = client.post("/api/folders", json={"name": "Kept"}).json()
    client.post("/api/smart-folders", json={"name": "Saved", "query": {"q": "x"}})
    (clean / "items").mkdir(exist_ok=True)
    assert cli.verify() == 0
    path = clean / "folders.json"
    data = json.loads(path.read_text())
    data["folders"][0]["name"] = "Renamed by hand"
    data["smart_folders"] = []
    path.write_text(json.dumps(data))
    assert cli.verify() == 2
    out = capsys.readouterr().out
    assert f"folder {folder['id']} (Kept) differs" in out
    assert "smart folder" in out and "missing" in out
    assert "safe while Kist" in out
    path.write_text("{oops")
    assert cli.verify() == 2
    assert "folders.json unreadable" in capsys.readouterr().out


def test_preview_rev_is_in_meta_and_goes_up_in_sql(client, upload, clean):
    """Two processes regenerating the same item both move preview_rev on,
    even when one of them holds an old copy of the row."""
    from kist.db import SessionLocal
    from kist.services import items as items_svc
    from kist.services import previews

    item = upload()["item"]
    with SessionLocal() as stale, SessionLocal() as other:
        old = items_svc.get_item(stale, item["id"])  # rev 0, never refreshed
        previews.regenerate(other, items_svc.get_item(other, item["id"]))
        previews.regenerate(stale, old)
    got = client.get(f"/api/items/{item['id']}").json()
    assert got["preview_rev"] == item["preview_rev"] + 2
    meta = json.loads((clean / "items" / item["id"] / "meta.json").read_text())
    assert meta["preview_rev"] == got["preview_rev"]


# ---------- concurrency ----------


def test_stale_edit_gets_409_with_the_current_item(client, upload):
    item = upload()["item"]
    seen = item["modified_at"]
    ok = client.patch(f"/api/items/{item['id']}",
                      json={"notes": "agent", "base_modified_at": seen})
    assert ok.status_code == 200
    stale = client.patch(f"/api/items/{item['id']}",
                         json={"notes": "old tab", "base_modified_at": seen})
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["message"] == "Changed elsewhere since you opened it"
    assert detail["item"]["notes"] == "agent"
    assert client.get(f"/api/items/{item['id']}").json()["notes"] == "agent"
    # with the new base it goes through; without one it always does
    fresh = client.patch(f"/api/items/{item['id']}", json={
        "notes": "mine", "base_modified_at": detail["item"]["modified_at"]})
    assert fresh.status_code == 200
    assert client.patch(f"/api/items/{item['id']}", json={"rating": 3}).status_code == 200


def test_concurrent_new_tag_does_not_fail(clean):
    from kist.db import SessionLocal
    from kist.services import tags as tags_svc

    barrier = threading.Barrier(2)
    errors = []

    def create():
        try:
            with SessionLocal() as s:
                barrier.wait()
                tags_svc.get_or_create(s, ["brand-new"])
                s.commit()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=create) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_concurrent_folder_moves_cannot_make_a_loop(client):
    from kist.db import SessionLocal
    from kist.services import folders as folders_svc

    x = client.post("/api/folders", json={"name": "X"}).json()["id"]
    y = client.post("/api/folders", json={"name": "Y"}).json()["id"]
    barrier = threading.Barrier(2)
    results = []

    def move(folder, parent):
        with SessionLocal() as s:
            barrier.wait()
            try:
                folders_svc.update_folder(s, folder, {"parent_id": parent})
                results.append("ok")
            except folders_svc.FolderError:
                results.append("refused")

    threads = [threading.Thread(target=move, args=a) for a in ((x, y), (y, x))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["ok", "refused"]
    parents = {f["id"]: f["parent_id"] for f in client.get("/api/folders").json()}
    assert None in (parents[x], parents[y])


# ---------- validation (contract item 2) ----------


def test_unknown_folder_ids_are_refused(client, upload):
    item = upload()["item"]
    ghost = "01GHOSTGHOSTGHOSTGHOSTGHOS"
    for res in (
        client.patch(f"/api/items/{item['id']}", json={"folder_ids": [ghost]}),
        client.patch(f"/api/items/{item['id']}", json={"folder_id": ghost}),
        client.patch(f"/api/items/{item['id']}/folders", json={"add": [ghost]}),
        client.put(f"/api/items/{item['id']}/folders", json={"folder_ids": [ghost]}),
        client.post("/api/items", files={"file": ("n.png", png_bytes((3, 3, 3)), "image/png")},
                    data={"folder_id": ghost}),
        client.post("/api/items", json={"url": "https://e.test/a.png", "folder_id": ghost}),
        client.post("/api/folders", json={"name": "Child", "parent_id": ghost}),
        client.post("/api/smart-folders", json={"name": "S", "query": {"folder": ghost}}),
    ):
        assert res.status_code == 422, res.text
        assert res.json() == {"detail": f"Unknown folder: {ghost}"}
    # removing a membership that isn't there is fine (a retry, a stale tab)
    assert client.patch(f"/api/items/{item['id']}/folders",
                        json={"remove": [ghost]}).status_code == 200
    assert client.get("/api/items").json()["total"] == 1  # the upload never happened


@pytest.mark.parametrize("body", [
    {"filename": "a/b.png"}, {"filename": "nul\u0000.png"}, {"filename": "   "},
    {"filename": "x" * 256}, {"notes": "n" * 100_001}, {"rating": 6}, {"rating": -1},
    {"created_at": (datetime.now(UTC) + timedelta(days=30)).isoformat()},
])
def test_item_fields_are_checked(client, upload, body):
    item = upload()["item"]
    assert client.patch(f"/api/items/{item['id']}", json=body).status_code == 422


def test_names_and_tags_are_checked(client, upload):
    item = upload()["item"]
    assert client.put(f"/api/items/{item['id']}/tags", json={"tags": ["red, white"]}
                      ).status_code == 422
    assert client.patch(f"/api/items/{item['id']}/tags", json={"add": ["t" * 101]}
                        ).status_code == 422
    assert client.patch(f"/api/items/{item['id']}/tags", json={"add": ["  "]}
                        ).status_code == 422
    ok = client.patch(f"/api/items/{item['id']}/tags", json={"add": ["  two   words "]})
    assert ok.json()["tags"] == ["two words"]
    assert client.post("/api/folders", json={"name": "   "}).status_code == 422
    assert client.post("/api/folders", json={"name": "f" * 201}).status_code == 422
    made = client.post("/api/folders", json={"name": "  Trimmed  "}).json()
    assert made["name"] == "Trimmed"
    assert client.patch(f"/api/folders/{made['id']}", json={"name": " "}).status_code == 422
    assert client.patch(f"/api/folders/{made['id']}",
                        json={"position": 2**40}).status_code == 422
    assert client.post("/api/smart-folders", json={"name": "  "}).status_code == 422


def test_upload_rating_is_checked(client):
    res = client.post("/api/items", files={"file": ("r.png", png_bytes((4, 4, 4)), "image/png")},
                      data={"rating": "7"})
    assert res.status_code == 422


def test_bad_cursor_is_a_400(client, upload):
    upload()
    for cursor in ("nope", "WzFd", "e30"):  # junk, [1], {}
        assert client.get(f"/api/items?cursor={cursor}").status_code == 400, cursor


def test_unknown_type_and_order_are_refused(client):
    assert client.get("/api/items?type=pdff").status_code == 422
    assert client.get("/api/items?order=sideways").status_code == 422


def test_smart_folders_keep_preview_and_trash_filters(client):
    made = client.post("/api/smart-folders", json={
        "name": "Broken previews", "query": {"preview": "failed", "deleted": True},
    })
    assert made.status_code == 201, made.text
    listed = client.get("/api/smart-folders").json()[0]["query"]
    assert listed["preview"] == "failed" and listed["deleted"] is True
    # the search param's own name is accepted too
    alias = client.post("/api/smart-folders", json={
        "name": "Pending", "query": {"preview_status": "pending"}}).json()
    assert alias["query"]["preview"] == "pending"
    for bad in ({"type": "gif"}, {"order": "random"}, {"view": "everything"},
                {"preview": "broken"}):
        res = client.post("/api/smart-folders", json={"name": "Bad", "query": bad})
        assert res.status_code == 422, bad


# ---------- the rest of the audit ----------


def test_a_duplicate_upload_is_still_filed_where_it_was_dropped(client, upload):
    data = png_bytes((8, 8, 8))
    first = upload("a.png", data, notes="")["item"]
    folder = client.post("/api/folders", json={"name": "Kitchen"}).json()
    again = upload("a.png", data, folder_id=folder["id"], tags="blue", notes="from the drop")
    assert not again["created"]
    item = again["item"]
    assert item["id"] == first["id"]
    assert item["folder_ids"] == [folder["id"]]
    assert item["tags"] == ["blue"] and item["notes"] == "from the drop"


def test_hard_delete_drops_queued_preview_jobs(client, upload):
    from kist import worker

    item = upload()["item"]
    client.post(f"/api/items/{item['id']}/preview")
    client.delete(f"/api/items/{item['id']}")
    client.delete(f"/api/items/{item['id']}?hard=true")
    assert client.get("/api/jobs").json() == []
    assert worker.run_one() is False


def test_a_preview_job_for_a_purged_item_just_finishes(client, upload, clean):
    from kist import worker
    from kist.db import SessionLocal
    from kist.models import Job

    with SessionLocal() as s:
        s.add(Job(kind="regen_preview", payload={"item_id": "01GONEGONEGONEGONEGONEGONE"}))
        s.commit()
    assert worker.run_one() is True
    assert client.get("/api/jobs?status=done").json()[0]["last_error"] is None
    assert not (clean / "items" / "01GONEGONEGONEGONEGONEGONE").exists()


def _old(path):
    old = datetime.now().timestamp() - 600
    os.utime(path, (old, old))


def test_files_stranded_in_staging_are_queued_again(client, clean):
    from kist import worker
    from kist.config import get_settings
    from kist.services import jobs

    staging = get_settings().staging_dir
    staging.mkdir(parents=True, exist_ok=True)
    stranded = staging / "inbox-01HZZZZZZZZZZZZZZZZZZZZZZZ-holiday photo.png"
    stranded.write_bytes(png_bytes((7, 7, 7)))
    fresh = staging / "inbox-01HYYYYYYYYYYYYYYYYYYYYYYY-new.png"
    fresh.write_bytes(png_bytes((7, 7, 8)))
    _old(stranded)
    upload_temp = staging / "up-01HXXXXXXXXXXXXXXXXXXXXXXX"
    upload_temp.write_bytes(b"in flight")
    _old(upload_temp)

    assert jobs.recover_staging() == 1  # not the fresh one, not upload temps
    assert jobs.recover_staging() == 0  # it has a job now
    assert worker.run_one() is True
    found = client.get("/api/items").json()["items"]
    assert [i["filename"] for i in found] == ["holiday photo.png"]
    assert fresh.exists() and upload_temp.exists()


def test_inbox_file_goes_back_when_the_job_cant_be_queued(clean, monkeypatch):
    from kist.config import get_settings
    from kist.services import ingest

    inbox = get_settings().inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)
    dropped = inbox / "drop.png"
    dropped.write_bytes(png_bytes((6, 6, 6)))
    _old(dropped)

    def db_down():
        raise sa.exc.OperationalError("SELECT 1", {}, Exception("the database is restarting"))

    monkeypatch.setattr(ingest, "SessionLocal", db_down)
    with pytest.raises(sa.exc.OperationalError):
        ingest.scan_inbox()
    assert dropped.is_file()
    assert list(get_settings().staging_dir.glob("inbox-*")) == []


def test_a_failed_park_move_leaves_the_job_pointing_at_staging(client, clean, monkeypatch):
    from kist.config import get_settings
    from kist.db import SessionLocal
    from kist.models import Job
    from kist.services import jobs

    staging = get_settings().staging_dir
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / "inbox-01HWWWWWWWWWWWWWWWWWWWWWWW-x.png"
    staged.write_bytes(b"x")
    with SessionLocal() as s:
        job = Job(kind="ingest", payload={"path": str(staged), "filename": "x.png"},
                  status="running", attempts=3)
        s.add(job)
        s.commit()
        job_id = job.id

    def no_move(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(jobs.shutil, "move", no_move)
    with SessionLocal() as s:
        jobs.finish(s, job_id, "boom")
    got = client.get("/api/jobs?status=failed").json()[0]
    assert got["id"] == job_id
    with SessionLocal() as s:
        payload = s.get(Job, job_id).payload
    assert "failed_path" not in payload and payload["path"] == str(staged)
    assert staged.is_file()


KIND_CASES = [
    ("image/png", "png"), ("image/svg+xml", "svg"), ("application/pdf", "pdf"),
    ("text/html", "pdf"), ("application/octet-stream", "pdf"), ("video/mp4", "mp4"),
    ("application/octet-stream", "mp4"), ("application/octet-stream", "MOV"),
    ("text/html", "mp4"), ("text/plain", "txt"), ("audio/mpeg", "mp3"),
]


def test_type_filter_and_stats_agree_with_item_kind(client, clean):
    """search.KIND_FILTERS is filetypes.kind_for in SQL."""
    from kist.db import SessionLocal
    from kist.filetypes import kind_for
    from kist.models import Item

    with SessionLocal() as s:
        for n, (mime, ext) in enumerate(KIND_CASES):
            s.add(Item(id=f"01KIND{n:020d}", filename=f"f.{ext}", ext=ext, mime=mime,
                       size_bytes=1, sha256=f"{n:064d}", preview_status="none"))
        s.commit()
    for kind in ("image", "video", "pdf", "other"):
        got = {i["id"] for i in client.get(f"/api/items?type={kind}&limit=100").json()["items"]}
        want = {f"01KIND{n:020d}" for n, (m, e) in enumerate(KIND_CASES) if kind_for(m, e) == kind}
        assert got == want, kind
        assert client.get("/api/stats").json()["by_kind"][kind] == len(want)
    items = client.get("/api/items?limit=100").json()["items"]
    assert all(i["kind"] == kind_for(i["mime"], i["ext"]) for i in items)


def test_rating_is_constrained_in_the_database(clean):
    from sqlalchemy.exc import IntegrityError

    from kist.db import SessionLocal

    with SessionLocal() as s, pytest.raises(IntegrityError):
        s.execute(sa.text(
            "INSERT INTO items (id, filename, ext, mime, size_bytes, sha256, rating) "
            "VALUES ('01RATE', 'a', 'png', 'image/png', 1, 'r', 9)"
        ))
        s.commit()


def test_migrations_go_down_and_up_again(database):
    from alembic import command
    from alembic.config import Config

    from kist.config import get_settings
    from kist.services.health import MIGRATIONS_DIR

    cfg = Config(str(MIGRATIONS_DIR.parent / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    command.downgrade(cfg, "0004")
    command.upgrade(cfg, "head")
