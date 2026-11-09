"""Kist maintenance CLI.

    kist rebuild-index [--wipe]   rebuild Postgres from the library on disk
    kist rewrite-meta             rewrite every meta.json (+ folders.json) from
                                   Postgres; safe while Kist is running
    kist regen-previews [--all]   (re)generate thumbnails; default only
                                   items that have none
    kist verify                   compare Postgres with the library on disk
                                   and report drift (changes nothing)
    kist reclassify [--dry-run]   give items Kist can't preview (e.g. a web
                                   page saved as .pdf) preview status "none"
    kist selftest                 run tiny generated files through every
                                   preview decoder; exit 1 if one fails

The library on disk (items/*/meta.json + folders.json) is the durable copy;
rebuild-index makes Postgres match it after a database loss or migration to
new hardware.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from .config import get_settings
from .db import SessionLocal
from .filetypes import kind_for
from .lifecycle import JobKind, PreviewStatus, preview_status_for
from .models import Folder, Item, ItemFolder, ItemTag, Job, SmartFolder, Tag


def parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def warn(message: str) -> None:
    print(f"  ! {message}", file=sys.stderr)


# ---------- rebuild-index ----------


def load_folders_file(path: Path) -> tuple[dict | None, str | None]:
    """(data, None), (None, None) when there is no file, or (None, why)."""
    if not path.is_file():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        for key in ("folders", "smart_folders"):
            rows = data.get(key, [])
            if not isinstance(rows, list) or not all(
                isinstance(r, dict) and isinstance(r.get("id"), str) and "name" in r
                for r in rows
            ):
                raise ValueError(f"'{key}' isn't a list of {{id, name, ...}} entries")
    except (OSError, ValueError) as exc:
        return None, str(exc)
    return data, None


def restore_folders(session, data: dict) -> tuple[set[str], int]:
    """Insert the folders in folders.json (parents first). A folder whose
    parent isn't anywhere, or that sits in a parent loop, is restored at
    the top level with a warning. Returns (folder ids, warnings)."""
    warnings = 0
    known = set(session.execute(sa.select(Folder.id)).scalars())
    pending = {f["id"]: dict(f) for f in data.get("folders", [])}
    for f in pending.values():
        parent = f.get("parent_id")
        if parent and parent not in pending and parent not in known:
            warn(f"folder {f['id']} ({f['name']}): parent {parent} is missing; "
                 "restored at the top level")
            f["parent_id"] = None
            warnings += 1
    restored: set[str] = set()
    while pending:
        ready = [
            f for f in pending.values()
            if not f.get("parent_id") or f["parent_id"] not in pending
        ]
        if not ready:
            # every folder left is in (or under) a parent loop: break it
            loop = min(f["id"] for f in pending.values())
            f = pending[loop]
            warn(f"folder {loop} ({f['name']}) is in a parent loop in folders.json; "
                 "restored at the top level")
            f["parent_id"] = None
            warnings += 1
            continue
        for f in ready:
            if session.get(Folder, f["id"]) is None:
                session.add(
                    Folder(
                        id=f["id"],
                        name=str(f["name"]),
                        parent_id=f.get("parent_id") or None,
                        position=int(f.get("position") or 0),
                    )
                )
            restored.add(f["id"])
            del pending[f["id"]]
        session.flush()
    session.commit()
    return restored | known, warnings


def item_from_meta(session, meta: dict, original: Path, folder_ids: set[str]) -> Item:
    from .services import tags as tags_svc

    # v2 meta has folder_ids; v1 had a single folder_id
    raw_folders = meta.get("folder_ids")
    if raw_folders is None:
        raw_folders = [meta["folder_id"]] if meta.get("folder_id") else []
    dropped = [f for f in raw_folders if f not in folder_ids]
    if dropped:
        print(f"  note: {meta['id']}: membership in missing folder(s) "
              f"{', '.join(dropped)} dropped")
    kind = kind_for(meta.get("mime", ""), meta["ext"])
    status = meta.get("preview_status")
    if status not in tuple(PreviewStatus) or status == PreviewStatus.PENDING:
        # no job survives a rebuild: say what the files on disk support
        status = preview_status_for(kind, meta.get("width"))
    item = Item(
        id=meta["id"],
        filename=meta["filename"],
        ext=meta["ext"],
        mime=meta.get("mime", "application/octet-stream"),
        size_bytes=meta.get("size_bytes")
        or (original.stat().st_size if original.is_file() else 0),
        width=meta.get("width"),
        height=meta.get("height"),
        duration_s=meta.get("duration_s"),
        sha256=meta["sha256"],
        phash=meta.get("phash"),
        dominant_colors=meta.get("dominant_colors"),
        rating=max(0, min(5, int(meta.get("rating") or 0))),
        notes=meta.get("notes") or "",
        source_url=meta.get("source_url"),
        deleted_at=parse_dt(meta.get("deleted_at")),
        preview_status=status,
        preview_error=meta.get("preview_error"),
        preview_rev=int(meta.get("preview_rev") or 0),
    )
    for key in ("created_at", "modified_at"):
        if meta.get(key):
            setattr(item, key, parse_dt(meta[key]))
    item.tags = tags_svc.get_or_create(session, meta.get("tags", []), validate=False)
    item.folders = [session.get(Folder, f) for f in raw_folders if f in folder_ids]
    return item


def recover_without_meta(session, item_dir: Path) -> str | None:
    """Restore an item folder that has an original but no meta.json from the
    file itself. Returns the restored filename, or None if it couldn't be."""
    from ulid import ULID

    from .services import ingest
    from .services import meta as meta_svc

    originals = sorted(p for p in item_dir.glob("original.*") if p.is_file())
    if not originals or len(item_dir.name) > 26:
        return None
    original = originals[0]
    ext = original.suffix.lstrip(".").lower() or "bin"
    digest = ingest.sha256_file(original)
    if session.execute(sa.select(Item.id).where(Item.sha256 == digest)).first():
        return None
    mime = ingest.sniff_mime(original)
    try:
        created = ULID.from_str(item_dir.name).datetime
    except (ValueError, TypeError):
        created = datetime.fromtimestamp(original.stat().st_mtime, UTC)
    kind = kind_for(mime, ext)
    item = Item(
        id=item_dir.name,
        filename=f"recovered-{item_dir.name}.{ext}",
        ext=ext,
        mime=mime,
        size_bytes=original.stat().st_size,
        sha256=digest,
        created_at=created,
        preview_status=PreviewStatus.NONE if kind == "other" else PreviewStatus.PENDING,
    )
    session.add(item)
    if kind != "other":  # the worker measures it and makes its previews
        session.add(Job(kind=JobKind.REGEN_PREVIEW,
                        payload={"item_id": item.id, "filename": item.filename}))
    session.commit()
    meta_svc.sync_item(session, item.id)
    return item.filename


def rebuild_index(wipe: bool) -> int:
    from .services import folders as folders_svc

    settings = get_settings()
    items_dir = settings.items_dir
    if not items_dir.is_dir():
        print(f"no items directory at {items_dir}", file=sys.stderr)
        return 1

    warnings = 0
    # read folders.json before touching the database: a bad file must not
    # leave an emptied library behind
    data, problem = load_folders_file(settings.library_dir / "folders.json")
    if problem:
        warn(f"folders.json unreadable: {problem}")
        if wipe:
            print("refusing to --wipe with an unreadable folders.json: fix it, move it "
                  "away, or run without --wipe to keep the existing folders",
                  file=sys.stderr)
            return 1
        warnings += 1

    with SessionLocal() as session:
        if wipe:
            print("wiping items/tags/folders tables…")
            session.execute(sa.delete(ItemTag))
            session.execute(sa.delete(ItemFolder))
            session.execute(sa.delete(Item))
            session.execute(sa.delete(Tag))
            session.execute(sa.delete(SmartFolder))
            session.execute(sa.delete(Folder))
            session.commit()

        if data is not None:
            folder_ids, folder_warnings = restore_folders(session, data)
            warnings += folder_warnings
            print(f"folders: {len(folder_ids)} restored")
            smart_restored = 0
            for s in data.get("smart_folders", []):
                if session.get(SmartFolder, s["id"]) is None:
                    session.add(
                        SmartFolder(
                            id=s["id"],
                            name=s["name"],
                            query=s.get("query") or {},
                            position=s.get("position", 0),
                        )
                    )
                    smart_restored += 1
            session.commit()
            print(f"smart folders: {smart_restored} restored")
        else:
            folder_ids = set(session.execute(sa.select(Folder.id)).scalars())
            if not problem:
                print("no folders.json found; keeping existing folders table")
            else:
                print("keeping existing folders table")

        ok = skipped = errors = recovered = 0
        for item_dir in sorted(items_dir.iterdir()):
            if not item_dir.is_dir() or item_dir.name.startswith("."):
                continue
            meta_file = item_dir / "meta.json"
            if not meta_file.is_file():
                if session.get(Item, item_dir.name) is not None:
                    skipped += 1  # in the database; rewrite-meta restores the file
                    continue
                try:
                    name = recover_without_meta(session, item_dir)
                except Exception as exc:
                    session.rollback()
                    name = None
                    warn(f"{item_dir.name}: {exc}")
                if name:
                    recovered += 1
                    warn(f"{item_dir.name}: no meta.json; restored from its original file "
                         f"as {name} (its tags, folders and notes were only in meta.json)")
                else:
                    errors += 1
                    warn(f"{item_dir.name}: no meta.json and no usable original file; skipped")
                continue
            try:
                meta = json.loads(meta_file.read_text())
                item_id = meta["id"]
                if item_dir.name != item_id:
                    warn(f"{meta_file}: id mismatch, skipping")
                    errors += 1
                    continue
                original = item_dir / f"original.{meta['ext']}"
                if not original.is_file():
                    warn(f"{item_id}: original file missing")
                    warnings += 1

                if session.get(Item, item_id) is not None:
                    skipped += 1
                    continue
                dupe = session.execute(
                    sa.select(Item.id).where(Item.sha256 == meta["sha256"])
                ).scalar_one_or_none()
                if dupe:
                    warn(f"{item_id}: sha256 collides with {dupe}, skipping")
                    errors += 1
                    continue
                session.add(item_from_meta(session, meta, original, folder_ids))
                session.commit()
                ok += 1
            except Exception as exc:
                session.rollback()
                warn(f"{meta_file}: {exc}")
                errors += 1

        print(f"items: {ok} restored, {skipped} already present, {errors} errors")
        if recovered:
            print(f"items without meta.json restored from their files: {recovered}")

        # keep snapshots in sync with what we just restored
        folders_svc.snapshot(session)
    if errors or warnings or recovered:
        print("finished with warnings (see the lines marked !)", file=sys.stderr)
        return 2
    return 0


# ---------- the rest ----------


def rewrite_meta() -> int:
    from .services import meta as meta_svc

    written = failed = 0
    with SessionLocal() as session:
        ids = session.execute(sa.select(Item.id).order_by(Item.id)).scalars().all()
        for item_id in ids:
            # re-read under the item's lock as it's written: an edit made
            # meanwhile is never overwritten with older data
            if meta_svc.sync_item(session, item_id):
                written += 1
            elif session.get(Item, item_id) is not None and meta_svc.item_dir(item_id).is_dir():
                failed += 1
        folders_ok = meta_svc.sync_folders(session)
    if not failed and folders_ok:
        meta_svc.clear_pending()
    print(f"rewrote meta.json for {written} items (+ folders.json)"
          + (f"; {failed} failed (see the log)" if failed else ""))
    return 0 if not failed and folders_ok else 2


def regen_previews(do_all: bool) -> int:
    from .services import previews

    settings = get_settings()
    done = skipped = failed = screen = 0
    with SessionLocal() as session:
        ids = session.execute(sa.select(Item.id).order_by(Item.id)).scalars().all()
        for item_id in ids:
            # fresh every time: the worker may have changed it meanwhile
            item = session.get(Item, item_id, populate_existing=True)
            if item is None:
                continue
            if kind_for(item.mime, item.ext) == "other":
                if previews.reclassify(session, item):
                    print(f"  - {item.id} {item.filename}: no preview possible")
                skipped += 1  # no preview possible for these
                continue
            item_dir = settings.items_dir / item.id
            wanted = [f"thumb_{s}.webp" for s in (256, 512, 1024)]
            if item.ext.lower() in {"heic", "heif"}:
                wanted.append("display.webp")
            complete = all((item_dir / name).exists() for name in wanted)
            if (
                not do_all and complete and item.width is not None
                and item.preview_status == PreviewStatus.READY
            ):
                # older items only lack the 2048 full-screen copy: make just that
                try:
                    if previews.add_screen_copy(session, item):
                        screen += 1
                        print(f"  + {item.id} {item.filename} (full-screen copy)")
                    else:
                        skipped += 1
                except Exception as exc:
                    session.rollback()
                    failed += 1
                    print(f"  ! {item.id} {item.filename}: {str(exc)[-200:]}", file=sys.stderr)
                continue
            try:
                status = previews.regenerate(session, item)
            except Exception as exc:
                session.rollback()
                failed += 1
                print(f"  ! {item.id} {item.filename}: {str(exc)[:200]}", file=sys.stderr)
                continue
            if status == PreviewStatus.FAILED or item.preview_error:
                failed += 1
                print(f"  ! {item.id} {item.filename}: {item.preview_error}", file=sys.stderr)
            else:
                done += 1
                print(f"  ✓ {item.id} {item.filename}")
    print(
        f"previews: {done} regenerated, {screen} full-screen copies added, "
        f"{skipped} already fine, {failed} failed"
    )
    return 0 if failed == 0 else 2


def verify() -> int:
    """Report where Postgres and the library on disk disagree."""
    from .services import verify as verify_svc

    with SessionLocal() as session:
        report = verify_svc.run(session)
    verify_svc.save_result(report)
    for p in report.problems:
        print(f"  ! {p}")
    print(f"verify: {report.items_checked} items checked, {len(report.problems)} problems")
    if report.problems:
        print(f"  {verify_svc.FIXES}")
    return 0 if not report.problems else 2


def reclassify(dry_run: bool) -> int:
    """Items whose sniffed type allows no preview but that still claim one
    (older versions trusted a .pdf name): preview status none, previews
    removed. The original file and its name are left alone."""
    from .services import previews
    from .services.search import KIND_FILTERS

    changed = 0
    with SessionLocal() as session:
        mislabelled = session.execute(
            sa.select(Item.id, Item.filename, Item.mime).where(
                sa.func.lower(Item.ext) == "pdf", Item.mime != "application/pdf"
            ).order_by(Item.id)
        ).all()
        for row in mislabelled:
            print(f"  {row.id} {row.filename}: named .pdf but is {row.mime}")
        stale = session.execute(
            sa.select(Item.id).where(
                KIND_FILTERS["other"], Item.preview_status != PreviewStatus.NONE
            ).order_by(Item.id)
        ).scalars().all()
        for item_id in stale:
            item = session.get(Item, item_id, populate_existing=True)
            if item is None:
                continue
            print(f"  {'would fix' if dry_run else 'fixed'} {item.id} {item.filename} "
                  f"({item.mime}): preview {item.preview_status} -> none")
            if not dry_run and previews.reclassify(session, item):
                changed += 1
            elif dry_run:
                changed += 1
    print(f"reclassify: {len(mislabelled)} items named .pdf that aren't PDFs; "
          f"{changed} items {'to change' if dry_run else 'changed'}")
    return 0


# ---------- selftest ----------


def _samples(work: Path) -> list[tuple[str, Path]]:
    """Tiny files in every format Kist previews. (name, path)"""
    from PIL import Image

    img = Image.new("RGB", (64, 48), (200, 60, 30))
    out: list[tuple[str, Path]] = []
    for ext, fmt in (("png", "PNG"), ("jpg", "JPEG"), ("gif", "GIF"), ("webp", "WEBP")):
        p = work / f"sample.{ext}"
        img.save(p, fmt)
        out.append((ext, p))
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
        p = work / "sample.heic"
        img.save(p, "HEIF")
        out.append(("heic", p))
    except Exception as exc:  # no HEIF encoder in this build: decoding isn't tested
        print(f"  - heic: skipped (can't write a sample here: {type(exc).__name__})")
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    pdf.new_page(200, 150)
    p = work / "sample.pdf"
    pdf.save(str(p))
    pdf.close()
    out.append(("pdf", p))
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        p = work / "sample.mp4"
        made = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
             "testsrc=size=64x48:rate=5", "-t", "1", "-pix_fmt", "yuv420p", str(p)],
            capture_output=True, text=True, timeout=60,
        )
        if made.returncode == 0:
            out.append(("mp4", p))
        else:
            print(f"  ✗ mp4: ffmpeg couldn't make a sample: {made.stderr[-200:]}")
            out.append(("mp4", work / "missing.mp4"))
    else:
        print("  - mp4: skipped (ffmpeg not installed)")
    return out


def selftest() -> int:
    """Push tiny generated files through the real preview pipeline (the
    child process, the decoders, the time limit) in a temp dir."""
    from .services import ingest, thumbs

    failures = 0
    with tempfile.TemporaryDirectory(prefix="kist-selftest-") as td:
        work = Path(td)
        for name, path in _samples(work):
            out = work / f"out-{name}"
            out.mkdir()
            try:
                if not path.is_file():
                    raise RuntimeError("no sample file")
                mime = ingest.sniff_mime(path)
                ext = path.suffix.lstrip(".")
                kind = kind_for(mime, ext)
                if kind == "other":
                    raise RuntimeError(f"sniffed as {mime}, which Kist can't preview")
                probe = thumbs.generate_previews_isolated(path, out, mime, ext)
                if not (out / "thumb_256.webp").is_file():
                    raise RuntimeError("no thumbnail was produced")
                print(f"  ✓ {name}: {mime}, {probe.width}x{probe.height}")
            except Exception as exc:
                failures += 1
                lines = [ln for ln in str(exc).splitlines() if ln.strip()]
                print(f"  ✗ {name}: {lines[-1] if lines else type(exc).__name__}")
    print("selftest: " + ("all decoders work" if not failures else f"{failures} failed"))
    return 0 if not failures else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="kist", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_rebuild = sub.add_parser(
        "rebuild-index", help="rebuild Postgres from meta.json files on disk"
    )
    p_rebuild.add_argument(
        "--wipe", action="store_true",
        help="clear items/tags/folders tables first (full rebuild)",
    )
    sub.add_parser("rewrite-meta", help="rewrite all meta.json files from Postgres")
    p_regen = sub.add_parser(
        "regen-previews", help="(re)generate thumbnails/probe data"
    )
    p_regen.add_argument(
        "--all", action="store_true",
        help="regenerate for every item, not just items without previews",
    )
    sub.add_parser("verify", help="report drift between Postgres and the library on disk")
    p_reclass = sub.add_parser(
        "reclassify", help="mark items Kist can't preview (e.g. HTML named .pdf) as such"
    )
    p_reclass.add_argument("--dry-run", action="store_true", help="only list what would change")
    sub.add_parser("selftest", help="check every preview decoder with tiny sample files")

    args = parser.parse_args()
    if args.cmd == "rebuild-index":
        sys.exit(rebuild_index(args.wipe))
    if args.cmd == "rewrite-meta":
        sys.exit(rewrite_meta())
    if args.cmd == "regen-previews":
        sys.exit(regen_previews(args.all))
    if args.cmd == "verify":
        sys.exit(verify())
    if args.cmd == "reclassify":
        sys.exit(reclassify(args.dry_run))
    if args.cmd == "selftest":
        sys.exit(selftest())


if __name__ == "__main__":
    main()
