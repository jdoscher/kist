"""Disaster recovery: `kist rebuild-index` must turn the library on disk
(items/*/meta.json + folders.json) back into the same archive.

This is the path the owner relies on after losing the database, and it is
only ever exercised for real on the worst day. Every field the owner can see
or edit must survive a wipe + rebuild, a damaged item must not stop the rest
from coming back, and nothing on disk may be silently left out.

Tests marked `xfail(strict=True)` with a "BUG:" reason document real defects
in the current code (they fail today; fix the app and they will XPASS, which
strict mode turns into a failure so the marker gets removed).
"""

import json
import shutil
from datetime import datetime

import pytest
import sqlalchemy as sa
from conftest import png_bytes

from kist import cli

# ---------- helpers ----------


def _norm(item: dict) -> dict:
    """An item as the owner sees it, comparable across a rebuild.

    Timestamps are compared as instants (the API renders them in the
    database session's time zone, which is not what we are testing).
    """
    out = dict(item)
    for key in ("created_at", "modified_at", "deleted_at"):
        if out.get(key):
            out[key] = datetime.fromisoformat(out[key])
    out["folder_ids"] = sorted(out["folder_ids"])
    out["tags"] = sorted(out["tags"])
    return out


def _all_items(client) -> dict[str, dict]:
    out = {}
    for state in ("active", "trashed"):
        res = client.get(f"/api/items?state={state}&limit=200").json()
        for it in res["items"]:
            out[it["id"]] = _norm(it)
    return out


def _folders(client) -> list[dict]:
    rows = client.get("/api/folders").json()
    return sorted(
        ({k: f[k] for k in ("id", "name", "parent_id", "position")} for f in rows),
        key=lambda f: f["id"],
    )


def _meta_path(clean, item_id):
    return clean / "items" / item_id / "meta.json"


def _read_meta(clean, item_id) -> dict:
    return json.loads(_meta_path(clean, item_id).read_text())


def _write_meta(clean, item_id, meta: dict) -> None:
    _meta_path(clean, item_id).write_text(json.dumps(meta))


def _drop_db_rows(*item_ids):
    """Simulate losing rows from Postgres while the disk is intact."""
    from kist.db import SessionLocal
    from kist.models import Item

    with SessionLocal() as s:
        s.execute(sa.delete(Item).where(Item.id.in_(item_ids)))
        s.commit()


def _library(client, upload):
    """A small but realistic library touching every restorable field."""
    top = client.post("/api/folders", json={"name": "Refs"}).json()
    mid = client.post("/api/folders", json={"name": "Type", "parent_id": top["id"]}).json()
    leaf = client.post("/api/folders", json={"name": "Serif", "parent_id": mid["id"]}).json()
    other = client.post("/api/folders", json={"name": "Moodboard ✨"}).json()
    client.patch(f"/api/folders/{other['id']}", json={"position": 7})
    client.post(
        "/api/smart-folders",
        json={"name": "Best reds", "query": {"tags": ["Red"], "rating": 4, "q": "poster"}},
    )

    a = upload(
        "poster.png", folder_ids=f"{leaf['id']},{other['id']}", tags="Red, print",
        notes="from the 1962 catalogue — ünïcode ok", source_url="https://example.com/p",
        rating="5", created_at="2018-03-04T05:06:07Z", modified_at="2019-01-02T03:04:05Z",
    )["item"]
    b = upload("trash.png", tags="print", deleted_at="2021-01-01T00:00:00Z")["item"]
    c = upload("notes.txt", b"plain text, no preview\n", "text/plain")["item"]
    d = upload("edited.png")["item"]
    # edits made after ingest must be in the durable copy too
    client.patch(f"/api/items/{d['id']}", json={
        "filename": "renamed.png", "rating": 2, "notes": "edited later",
        "folder_ids": [mid["id"]],
    })
    client.put(f"/api/items/{d['id']}/tags", json={"tags": ["later", "Red"]})
    return {"folders": (top, mid, leaf, other), "items": (a, b, c, d)}


# ---------- round trip ----------


def test_wipe_and_rebuild_restores_the_library_exactly(client, upload, clean, capsys):
    lib = _library(client, upload)
    before_items = _all_items(client)
    before_folders = _folders(client)
    before_smart = client.get("/api/smart-folders").json()
    assert len(before_items) == 4

    assert cli.rebuild_index(wipe=True) == 0

    after = _all_items(client)
    assert set(after) == set(before_items)
    for item_id, want in before_items.items():
        got = after[item_id]
        # preview_rev is checked by its own (xfail) test below
        want = {k: v for k, v in want.items() if k not in ("preview_rev", "thumb_url")}
        got = {k: v for k, v in got.items() if k not in ("preview_rev", "thumb_url")}
        assert got == want, item_id
    assert _folders(client) == before_folders
    assert client.get("/api/smart-folders").json() == before_smart

    # subfolder membership still rolls up after the rebuild
    top = lib["folders"][0]
    in_top = client.get(f"/api/items?folder={top['id']}").json()
    assert in_top["total"] == 2
    # trashed stays trashed, and the files are where they were
    b = lib["items"][1]
    assert after[b["id"]]["state"] == "trashed"
    for it in lib["items"]:
        assert (clean / "items" / it["id"] / f"original.{it['ext']}").is_file()

    # and the database now agrees with the disk
    capsys.readouterr()
    assert cli.verify() == 0, capsys.readouterr().out


def test_rebuild_is_idempotent(client, upload, capsys):
    _library(client, upload)
    before = _all_items(client)
    assert cli.rebuild_index(wipe=False) == 0
    assert cli.rebuild_index(wipe=False) == 0
    out = capsys.readouterr().out
    assert "items: 0 restored, 4 already present, 0 errors" in out
    assert _all_items(client) == before
    assert client.get("/api/stats").json()["total_items"] == 3  # 1 of 4 is trashed


def test_partial_loss_restores_only_the_missing_rows(client, upload, capsys):
    lib = _library(client, upload)
    a, _, _, d = lib["items"]
    before = _all_items(client)

    _drop_db_rows(a["id"])
    assert client.get(f"/api/items/{a['id']}").status_code == 404

    assert cli.rebuild_index(wipe=False) == 0
    assert "items: 1 restored, 3 already present, 0 errors" in capsys.readouterr().out
    after = _all_items(client)
    assert set(after) == set(before)
    for key in ("tags", "folder_ids", "notes", "rating", "source_url", "filename",
                "created_at", "modified_at", "sha256", "state", "preview_status"):
        assert after[a["id"]][key] == before[a["id"]][key], key
    # rows that were never lost are not touched
    assert after[d["id"]] == before[d["id"]]


def test_purged_items_do_not_come_back(client, upload):
    keep = upload()["item"]
    gone = upload()["item"]
    client.delete(f"/api/items/{gone['id']}")
    client.delete(f"/api/items/{gone['id']}?hard=true")

    assert cli.rebuild_index(wipe=True) == 0
    assert client.get(f"/api/items/{gone['id']}").status_code == 404
    assert client.get(f"/api/items/{keep['id']}").status_code == 200


# ---------- damage on disk ----------


def test_one_damaged_item_does_not_stop_the_rest(client, upload, clean, capsys):
    good = upload()["item"]
    corrupt = upload()["item"]
    moved = upload()["item"]
    copied = upload()["item"]

    _meta_path(clean, corrupt["id"]).write_text('{"id": "trunc')  # half-written
    # a meta.json whose id does not match its folder (e.g. a folder renamed by hand)
    meta = _read_meta(clean, moved["id"])
    meta["id"] = "01AAAAAAAAAAAAAAAAAAAAAAAA"
    _write_meta(clean, moved["id"], meta)
    # a hand copy of an item folder: same bytes under a second id
    dup_id = "0ZZZZZZZZZZZZZZZZZZZZZZZZZ"  # sorts after every real ULID
    shutil.copytree(clean / "items" / copied["id"], clean / "items" / dup_id)
    meta = _read_meta(clean, dup_id)
    meta["id"] = dup_id
    _write_meta(clean, dup_id, meta)

    assert cli.rebuild_index(wipe=True) == 2  # non-zero: the owner must look
    captured = capsys.readouterr()
    assert "items: 2 restored, 0 already present, 3 errors" in captured.out
    assert corrupt["id"] in captured.err
    assert "id mismatch" in captured.err
    assert f"sha256 collides with {copied['id']}" in captured.err

    assert client.get(f"/api/items/{good['id']}").status_code == 200
    assert client.get(f"/api/items/{copied['id']}").status_code == 200
    assert client.get(f"/api/items/{dup_id}").status_code == 404
    # the damaged meta.json files were reported, not rewritten or deleted
    assert _meta_path(clean, corrupt["id"]).read_text() == '{"id": "trunc'
    assert (clean / "items" / corrupt["id"] / "original.png").is_file()


def test_missing_original_is_restored_but_flagged(client, upload, clean, capsys):
    item = upload()["item"]
    (clean / "items" / item["id"] / "original.png").unlink()

    cli.rebuild_index(wipe=True)
    assert f"{item['id']}: original file missing" in capsys.readouterr().err
    # the metadata (tags, notes…) is still worth having back
    assert client.get(f"/api/items/{item['id']}").status_code == 200
    assert cli.verify() == 2
    assert "original file missing" in capsys.readouterr().out


def test_v1_meta_with_a_single_folder_id_is_upgraded(client, upload, clean):
    folder = client.post("/api/folders", json={"name": "Old"}).json()
    item = upload(folder_id=folder["id"])["item"]
    meta = _read_meta(clean, item["id"])
    meta["version"] = 1
    meta["folder_id"] = meta.pop("folder_ids")[0]
    for key in ("folder_paths", "preview_status", "preview_error"):
        meta.pop(key)
    _write_meta(clean, item["id"], meta)

    assert cli.rebuild_index(wipe=True) == 0
    got = client.get(f"/api/items/{item['id']}").json()
    assert got["folder_ids"] == [folder["id"]]
    assert got["preview_status"] == "ready"  # derived: it has dimensions
    assert got["has_thumb"]


def test_membership_in_a_deleted_folder_is_dropped(client, upload):
    keep = client.post("/api/folders", json={"name": "Keep"}).json()
    doomed = client.post("/api/folders", json={"name": "Doomed"}).json()
    item = upload(folder_ids=f"{keep['id']},{doomed['id']}")["item"]
    client.delete(f"/api/folders/{doomed['id']}")

    assert cli.rebuild_index(wipe=True) == 0
    assert client.get(f"/api/items/{item['id']}").json()["folder_ids"] == [keep["id"]]


def test_children_listed_before_parents_still_nest(client, clean):
    a = client.post("/api/folders", json={"name": "A"}).json()
    b = client.post("/api/folders", json={"name": "B", "parent_id": a["id"]}).json()
    c = client.post("/api/folders", json={"name": "C", "parent_id": b["id"]}).json()
    before = _folders(client)
    path = clean / "folders.json"
    data = json.loads(path.read_text())
    data["folders"].sort(key=lambda f: f["id"], reverse=True)  # C, B, A
    assert [f["id"] for f in data["folders"]] == [c["id"], b["id"], a["id"]]
    path.write_text(json.dumps(data))
    (clean / "items").mkdir()  # rebuild-index refuses a library without one

    assert cli.rebuild_index(wipe=True) == 0
    assert _folders(client) == before


def test_without_folders_json_existing_folders_are_kept(client, upload, clean, capsys):
    folder = client.post("/api/folders", json={"name": "Kept"}).json()
    item = upload(folder_id=folder["id"])["item"]
    (clean / "folders.json").unlink()
    _drop_db_rows(item["id"])

    assert cli.rebuild_index(wipe=False) == 0
    assert "keeping existing folders table" in capsys.readouterr().out
    assert client.get(f"/api/items/{item['id']}").json()["folder_ids"] == [folder["id"]]
    assert (clean / "folders.json").is_file(), "the snapshot is written again"


def test_no_items_directory_is_an_error(clean, capsys):
    assert not (clean / "items").exists()
    assert cli.rebuild_index(wipe=True) == 1
    assert "no items directory" in capsys.readouterr().err


# ---------- known bugs (strict xfail: they fail today) ----------


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "BUG: an item folder with an original but no meta.json (ingest tolerates a "
    "failed meta write and says 'run kist rewrite-meta') is silently skipped by "
    "rebuild-index: not restored, not reported, exit code 0"
))
def test_item_folder_without_meta_json_is_reported(client, upload, clean, capsys):
    upload()
    orphan = upload()["item"]
    _meta_path(clean, orphan["id"]).unlink()

    code = cli.rebuild_index(wipe=True)
    captured = capsys.readouterr()
    assert orphan["id"] in captured.err and code != 0


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "BUG: rebuild-index --wipe empties the database before reading "
    "folders.json; an unreadable folders.json then crashes it and leaves an "
    "empty library"
))
def test_unreadable_folders_json_does_not_leave_an_empty_library(client, upload, clean):
    item = upload()["item"]
    (clean / "folders.json").write_text("{not json")

    try:
        cli.rebuild_index(wipe=True)
    except Exception:
        pass
    assert client.get(f"/api/items/{item['id']}").status_code == 200


@pytest.mark.xfail(strict=True, raises=sa.exc.IntegrityError, reason=(
    "BUG: a folder in folders.json whose parent is not in the file is inserted "
    "with a dangling parent_id; the FK violation aborts the whole rebuild "
    "(after --wipe has already emptied the database)"
))
def test_folder_with_a_missing_parent_is_restored_at_top_level(client, upload, clean):
    a = client.post("/api/folders", json={"name": "A"}).json()
    b = client.post("/api/folders", json={"name": "B", "parent_id": a["id"]}).json()
    item = upload(folder_id=b["id"])["item"]
    path = clean / "folders.json"
    data = json.loads(path.read_text())
    data["folders"] = [f for f in data["folders"] if f["id"] != a["id"]]
    path.write_text(json.dumps(data))

    cli.rebuild_index(wipe=True)
    assert client.get(f"/api/items/{item['id']}").json()["folder_ids"] == [b["id"]]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "BUG: folders in a parent cycle in folders.json are dropped without a "
    "word, and so are their items' memberships; rebuild-index still exits 0"
))
def test_folder_cycle_in_folders_json_is_reported(client, upload, clean, capsys):
    a = client.post("/api/folders", json={"name": "A"}).json()
    b = client.post("/api/folders", json={"name": "B", "parent_id": a["id"]}).json()
    upload(folder_id=b["id"])
    path = clean / "folders.json"
    data = json.loads(path.read_text())
    for f in data["folders"]:
        if f["id"] == a["id"]:
            f["parent_id"] = b["id"]
    path.write_text(json.dumps(data))

    code = cli.rebuild_index(wipe=True)
    names = {f["name"] for f in client.get("/api/folders").json()}
    assert names == {"A", "B"} or (code != 0 and a["id"] in capsys.readouterr().err)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "BUG: preview_rev is not in meta.json, so rebuild-index resets it to 0; "
    "thumbnails are served 'immutable' per ?v=rev, so browsers keep showing "
    "the stale cached preview of any item whose preview was regenerated"
))
def test_preview_version_survives_a_rebuild(client, upload):
    from kist.db import SessionLocal
    from kist.services import items as items_svc
    from kist.services import previews

    item = upload("big.png", png_bytes(size=(1200, 900)))["item"]
    with SessionLocal() as s:
        previews.regenerate(s, items_svc.get_item(s, item["id"]))
    rev = client.get(f"/api/items/{item['id']}").json()["preview_rev"]
    assert rev > item["preview_rev"]

    assert cli.rebuild_index(wipe=True) == 0
    assert client.get(f"/api/items/{item['id']}").json()["preview_rev"] == rev


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "BUG: deleting a folder does not rewrite its items' meta.json, so the "
    "durable copy keeps the dead folder id and `kist verify` reports drift "
    "after an ordinary folder delete"
))
def test_verify_is_clean_after_a_folder_delete(client, upload, capsys):
    folder = client.post("/api/folders", json={"name": "Temp"}).json()
    upload(folder_id=folder["id"])
    client.delete(f"/api/folders/{folder['id']}")
    code = cli.verify()
    assert code == 0, capsys.readouterr().out
