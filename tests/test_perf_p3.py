"""Perf P3: lighter thumbnails (WebP Q80), the encoder marker and the
`kist regen-previews --thumbs-only` backfill."""

import json
import subprocess

import pytest
from conftest import png_bytes
from media_fixtures import jpeg, pdf_bytes


def item_dir(clean, item):
    return clean / "items" / item["id"]


def make_old(d):
    """Turn an item's thumbnails into what Kist <= cd27cb7 made: WebP Q86,
    no encoder marker."""
    import pyvips

    for name in ("thumb_256.webp", "thumb_512.webp", "thumb_1024.webp"):
        img = pyvips.Image.new_from_file(str(d / name))
        data = img.webpsave_buffer(Q=86, smart_subsample=True)
        (d / name).write_bytes(data)
    (d / "thumbs.v").unlink(missing_ok=True)


def run_backfill(capsys, every=1):
    from kist import cli

    code = cli.regen_thumbs(every)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_chosen_encode_is_the_default():
    from kist.services import thumbs

    assert thumbs.THUMB_WEBP == {"Q": 80, "smart_subsample": True}
    assert thumbs.THUMB_ENCODER == 2
    assert thumbs.ENCODER_FILE in thumbs.PREVIEW_FILES  # remove_previews clears it


def test_new_uploads_get_the_new_encoder_and_its_marker(client, upload, clean):
    item = upload("big.png", png_bytes(size=(1600, 1200)))["item"]
    d = item_dir(clean, item)
    assert (d / "thumbs.v").read_text().strip() == "2"
    from PIL import Image

    assert Image.open(d / "thumb_512.webp").size == (683, 512)
    assert max(Image.open(d / "thumb_1024.webp").size) == 1024
    assert not list(d.glob(".*.tmp"))  # temporary names are renamed away


def test_backfill_reencodes_old_thumbnails_once(client, upload, clean, capsys):
    from kist.db import SessionLocal
    from kist.models import Item

    items = [upload(f"p{i}.jpg", jpeg(color=(30 * i, 90, 200 - 20 * i), size=(1800, 1200)),
                    mime="image/jpeg")["item"] for i in range(5)]
    items.append(upload("doc.pdf", data=pdf_bytes(), mime="application/pdf")["item"])
    for it in items:
        make_old(item_dir(clean, it))
    with SessionLocal() as s:
        before = {i.id: (i.preview_rev, i.modified_at, i.width, i.height, i.phash,
                         i.dominant_colors) for i in s.query(Item)}
    old_bytes = {it["id"]: (item_dir(clean, it) / "thumb_1024.webp").read_bytes()
                 for it in items}

    code, out, err = run_backfill(capsys, every=2)
    assert code == 0, err
    progress = [line for line in out.splitlines() if line.startswith("thumbnails: ")]
    assert len(progress) >= 4  # every 2 items, and the last line
    assert "6/6 looked at, 6 re-encoded" in out
    assert "5 from original" in out and "1 from previews" in out
    with SessionLocal() as s:
        after = {i.id: (i.preview_rev, i.modified_at, i.width, i.height, i.phash,
                        i.dominant_colors) for i in s.query(Item)}
    for it in items:
        d = item_dir(clean, it)
        assert (d / "thumbs.v").read_text().strip() == "2"
        assert (d / "thumb_1024.webp").read_bytes() != old_bytes[it["id"]]
        b, a = before[it["id"]], after[it["id"]]
        assert a[0] == b[0] + 1  # new URLs, so browsers fetch the new files
        assert a[1] == b[1]  # modified_at never moves
        assert a[2:4] == b[2:4]  # nor the dimensions
        meta = json.loads((d / "meta.json").read_text())
        assert meta["preview_rev"] == a[0]
        if it["mime"] == "image/jpeg":
            assert a[4:] == b[4:]  # duplicates and palettes untouched
    # the grid sees the new rev and file
    page = client.get("/api/items").json()["items"]
    shown = next(i for i in page if i["id"] == items[0]["id"])
    r = client.get(f"/api/items/{shown['id']}/thumb?size=1024&v={shown['preview_rev']}")
    assert r.content == (item_dir(clean, items[0]) / "thumb_1024.webp").read_bytes()

    # a second run does nothing
    code, out, _ = run_backfill(capsys, every=100)
    assert code == 0 and "6/6 looked at, 0 re-encoded, 6 already current" in out
    with SessionLocal() as s:
        assert {i.id: i.preview_rev for i in s.query(Item)} == {k: v[0] for k, v in after.items()}


def test_the_new_files_are_smaller(client, upload, clean, capsys):
    item = upload("photo.jpg", jpeg(size=(2400, 1600)), mime="image/jpeg")["item"]
    d = item_dir(clean, item)
    make_old(d)
    old = sum((d / f"thumb_{s}.webp").stat().st_size for s in (256, 512, 1024))
    assert run_backfill(capsys)[0] == 0
    new = sum((d / f"thumb_{s}.webp").stat().st_size for s in (256, 512, 1024))
    assert new < old


def test_a_bad_original_fails_alone_and_is_retried_next_run(client, upload, clean, capsys):
    good = [upload(f"g{i}.png", png_bytes(color=(10 * i, 20, 30), size=(900, 700)))["item"]
            for i in range(3)]
    bad = upload("bad.png", png_bytes(color=(1, 2, 3), size=(900, 700)))["item"]
    for it in good + [bad]:
        make_old(item_dir(clean, it))
    (item_dir(clean, bad) / "original.png").write_bytes(b"\x89PNG\r\n\x1a\n not a png")
    code, out, err = run_backfill(capsys, every=100)
    assert code == 2
    assert "3 re-encoded" in out and "1 failed" in out and bad["id"] in err
    for it in good:
        assert (item_dir(clean, it) / "thumbs.v").is_file()
    assert not (item_dir(clean, bad) / "thumbs.v").exists()
    # its old thumbnails are still there and still served
    assert client.get(f"/api/items/{bad['id']}/thumb").status_code == 200
    code, out, _ = run_backfill(capsys, every=100)
    assert "1 failed" in out and "3 already current" in out  # only the bad one again


def test_a_crashing_batch_child_retries_its_items_one_by_one(client, upload, clean, monkeypatch):
    from kist.services import thumb_backfill

    items = [upload(f"c{i}.png", png_bytes(color=(5, 9 * i, 40), size=(800, 600)))["item"]
             for i in range(3)]
    pairs = [(item_dir(clean, it) / "original.png", item_dir(clean, it)) for it in items]
    real = subprocess.run
    calls = []

    def flaky(cmd, **kw):
        calls.append(kw.get("input", "").count("\n") + 1)
        if calls[-1] > 1:  # the batch: answers for the first item, then "segfaults"
            real(cmd, **{**kw, "input": kw["input"].splitlines()[0]})
            return subprocess.CompletedProcess(cmd, -11, json.dumps({"i": 0, "error": None}),
                                               "Segmentation fault")
        return real(cmd, **kw)

    monkeypatch.setattr(thumb_backfill.subprocess, "run", flaky)
    assert thumb_backfill.thumbs_batch(pairs) == [None, None, None]
    assert calls == [3, 1, 1]  # the two unanswered ones, one child each


def test_sources(client, upload, clean):
    from kist.db import SessionLocal
    from kist.models import Item
    from kist.services import thumb_backfill

    photo = upload("a.jpg", jpeg(size=(1500, 1000)), mime="image/jpeg")["item"]
    pdf = upload("d.pdf", data=pdf_bytes(), mime="application/pdf")["item"]
    with SessionLocal() as s:
        p, d = s.get(Item, photo["id"]), s.get(Item, pdf["id"])
        assert thumb_backfill.source_for(p, item_dir(clean, photo)).name == "original.jpg"
        assert thumb_backfill.source_for(d, item_dir(clean, pdf)) is None  # preview pipeline
        # a HEIC (Debian's libvips can't open iPhone ones): its display.webp
        p.mime, p.ext = "image/heic", "heic"
        (item_dir(clean, photo) / "display.webp").write_bytes(b"x")
        assert thumb_backfill.source_for(p, item_dir(clean, photo)).name == "display.webp"
        s.rollback()


def test_trashed_and_pending_items(client, upload, clean, capsys):
    from kist.db import SessionLocal
    from kist.models import Item

    trashed = upload("t.png", png_bytes(size=(700, 500)), deleted_at="2026-01-01T00:00:00Z")["item"]
    pending = upload("p.png", png_bytes(color=(9, 9, 9), size=(700, 500)))["item"]
    for it in (trashed, pending):
        make_old(item_dir(clean, it))
    with SessionLocal() as s:
        s.get(Item, pending["id"]).preview_status = "pending"
        s.commit()
    code, out, _ = run_backfill(capsys, every=100)
    assert code == 0
    assert (item_dir(clean, trashed) / "thumbs.v").is_file()  # the trash shows thumbnails too
    assert not (item_dir(clean, pending) / "thumbs.v").exists()  # the worker will make it


def test_the_marker_survives_rebuild_index(client, upload, clean, capsys):
    from kist import cli
    from kist.db import SessionLocal
    from kist.models import Item

    item = upload("r.png", png_bytes(size=(1300, 900)))["item"]
    make_old(item_dir(clean, item))
    assert run_backfill(capsys)[0] == 0
    with SessionLocal() as s:
        rev = s.get(Item, item["id"]).preview_rev
    assert cli.rebuild_index(wipe=True) == 0
    with SessionLocal() as s:
        assert s.get(Item, item["id"]).preview_rev == rev
    code, out, _ = run_backfill(capsys, every=100)
    assert "0 re-encoded, 1 already current" in out


def test_cli_flag(monkeypatch):
    from kist import cli

    seen = {}
    monkeypatch.setattr(cli, "regen_thumbs", lambda every: seen.setdefault("every", every) and 0)
    monkeypatch.setattr("sys.argv", ["kist", "regen-previews", "--thumbs-only",
                                     "--progress-every", "50"])
    with pytest.raises(SystemExit):
        cli.main()
    assert seen == {"every": 50}


def test_encoder_version_reads_garbage_as_old(tmp_path):
    from kist.services import thumbs

    assert thumbs.encoder_version(tmp_path) == 1
    (tmp_path / thumbs.ENCODER_FILE).write_text("junk")
    assert thumbs.encoder_version(tmp_path) == 1
    thumbs.mark_encoder(tmp_path)
    assert thumbs.encoder_version(tmp_path) == thumbs.THUMB_ENCODER
