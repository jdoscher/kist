"""Paging counts (first page only) and ThumbHash placeholders."""

import json

import sqlalchemy as sa

from kist import cli


def test_total_only_on_the_first_page(client, upload):
    for _ in range(3):
        upload()
    first = client.get("/api/items?limit=2").json()
    assert first["total"] == 3 and first["next_cursor"]
    second = client.get(f"/api/items?limit=2&cursor={first['next_cursor']}").json()
    assert second["total"] is None and len(second["items"]) == 1


def test_uploads_get_a_thumbhash_kept_in_meta_and_rebuild(client, upload, clean):
    item = upload("th.png")["item"]
    assert item["thumbhash"] and isinstance(item["thumbhash"], str)
    meta = json.loads((clean / "items" / item["id"] / "meta.json").read_text())
    assert meta["thumbhash"] == item["thumbhash"]
    assert cli.rebuild_index(wipe=True) == 0
    assert client.get(f"/api/items/{item['id']}").json()["thumbhash"] == item["thumbhash"]


def test_thumbhash_backfill_for_older_items(client, upload, clean):
    from kist.db import SessionLocal
    from kist.models import Item
    from kist.services import previews

    item = upload()["item"]
    with SessionLocal() as s:
        s.execute(sa.update(Item).values(thumbhash=None))
        s.commit()
    assert client.get(f"/api/items/{item['id']}").json()["thumbhash"] is None
    assert cli.regen_previews(False, thumbhash_only=True) == 0
    assert client.get(f"/api/items/{item['id']}").json()["thumbhash"] == item["thumbhash"]

    # the worker's backfill filling it in between the CLI's list and its
    # visit is a success, not "preview unreadable" (seen on the 2.0 upgrade)
    from unittest import mock

    with SessionLocal() as s:
        s.execute(sa.update(Item).values(thumbhash=None))
        s.commit()
    real_get = sa.orm.Session.get

    def get_after_worker(self, cls, ident, **kw):
        if cls is Item:
            with SessionLocal() as other:
                other.execute(sa.update(Item).where(Item.id == ident).values(thumbhash="x"))
                other.commit()
        return real_get(self, cls, ident, **kw)

    with mock.patch.object(sa.orm.Session, "get", get_after_worker):
        assert cli.regen_previews(False, thumbhash_only=True) == 0

    with SessionLocal() as s:
        s.execute(sa.update(Item).values(thumbhash=None))
        s.commit()
    edited = client.get(f"/api/items/{item['id']}").json()["modified_at"]
    with SessionLocal() as s:
        assert previews.backfill_thumbhashes(s) == 1
        assert previews.backfill_thumbhashes(s) == 0
    got = client.get(f"/api/items/{item['id']}").json()
    assert got["thumbhash"] == item["thumbhash"]
    # a placeholder isn't an edit: "Recently edited" doesn't change
    assert got["modified_at"] == edited
    meta = json.loads((clean / "items" / item["id"] / "meta.json").read_text())
    assert meta["thumbhash"] == item["thumbhash"]


def test_non_images_have_no_thumbhash(upload):
    assert upload("notes.txt", b"plain text", "text/plain")["item"]["thumbhash"] is None
