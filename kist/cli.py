"""Kist maintenance CLI.

    kist rebuild-index [--wipe]   rebuild Postgres from the library on disk
    kist rewrite-meta             rewrite every meta.json from Postgres
    kist regen-previews [--all]   (re)generate thumbnails; default only
                                   items that have none
    kist verify                   compare Postgres with the library on disk
                                   and report drift (changes nothing)

The library on disk (items/*/meta.json + folders.json) is the durable copy;
rebuild-index makes Postgres match it after a database loss or migration to
new hardware.
"""

import argparse
import json
import sys
from datetime import datetime

import sqlalchemy as sa

from .config import get_settings
from .db import SessionLocal
from .filetypes import kind_for
from .lifecycle import PreviewStatus, preview_status_for
from .models import Folder, Item, ItemFolder, ItemTag, SmartFolder, Tag


def parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def rebuild_index(wipe: bool) -> int:
    from .services import tags as tags_svc

    settings = get_settings()
    items_dir = settings.items_dir
    if not items_dir.is_dir():
        print(f"no items directory at {items_dir}", file=sys.stderr)
        return 1

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

        # folders first (items reference them)
        folders_file = settings.library_dir / "folders.json"
        folder_ids: set[str] = set()
        if folders_file.is_file():
            data = json.loads(folders_file.read_text())
            rows = data.get("folders", [])
            # parents before children so FKs resolve
            pending = {f["id"]: f for f in rows}
            inserted = True
            while pending and inserted:
                inserted = False
                for fid, f in list(pending.items()):
                    parent = f.get("parent_id")
                    if parent and parent in pending:
                        continue
                    if session.get(Folder, fid) is None:
                        session.add(
                            Folder(
                                id=fid,
                                name=f["name"],
                                parent_id=parent if parent not in pending else None,
                                position=f.get("position", 0),
                            )
                        )
                    folder_ids.add(fid)
                    del pending[fid]
                    inserted = True
            session.commit()
            print(f"folders: {len(folder_ids)} restored")
            smart_restored = 0
            for s in data.get("smart_folders", []):
                if session.get(SmartFolder, s["id"]) is None:
                    session.add(
                        SmartFolder(
                            id=s["id"],
                            name=s["name"],
                            query=s.get("query", {}),
                            position=s.get("position", 0),
                        )
                    )
                    smart_restored += 1
            session.commit()
            print(f"smart folders: {smart_restored} restored")
        else:
            folder_ids = set(session.execute(sa.select(Folder.id)).scalars())
            print("no folders.json found; keeping existing folders table")

        ok = skipped = errors = 0
        for meta_file in sorted(items_dir.glob("*/meta.json")):
            try:
                meta = json.loads(meta_file.read_text())
                item_id = meta["id"]
                if meta_file.parent.name != item_id:
                    print(f"  ! {meta_file}: id mismatch, skipping", file=sys.stderr)
                    errors += 1
                    continue
                original = meta_file.parent / f"original.{meta['ext']}"
                if not original.is_file():
                    print(f"  ! {item_id}: original file missing", file=sys.stderr)

                existing = session.get(Item, item_id)
                if existing is not None:
                    skipped += 1
                    continue
                dupe = session.execute(
                    sa.select(Item.id).where(Item.sha256 == meta["sha256"])
                ).scalar_one_or_none()
                if dupe:
                    print(f"  ! {item_id}: sha256 collides with {dupe}, skipping",
                          file=sys.stderr)
                    errors += 1
                    continue

                # v2 meta has folder_ids; v1 had a single folder_id
                raw_folders = meta.get("folder_ids")
                if raw_folders is None:
                    raw_folders = [meta["folder_id"]] if meta.get("folder_id") else []
                item_folder_ids = [f for f in raw_folders if f in folder_ids]

                item = Item(
                    id=item_id,
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
                    rating=meta.get("rating", 0),
                    notes=meta.get("notes", ""),
                    source_url=meta.get("source_url"),
                    created_at=parse_dt(meta.get("created_at")),
                    modified_at=parse_dt(meta.get("modified_at")),
                    deleted_at=parse_dt(meta.get("deleted_at")),
                    preview_status=meta.get("preview_status")
                    or preview_status_for(
                        kind_for(meta.get("mime", ""), meta["ext"]), meta.get("width")
                    ),
                    preview_error=meta.get("preview_error"),
                )
                item.tags = tags_svc.get_or_create(session, meta.get("tags", []))
                if item_folder_ids:
                    item.folders = [
                        session.get(Folder, f) for f in item_folder_ids
                    ]
                session.add(item)
                session.commit()
                ok += 1
            except Exception as exc:
                session.rollback()
                print(f"  ! {meta_file}: {exc}", file=sys.stderr)
                errors += 1

        print(f"items: {ok} restored, {skipped} already present, {errors} errors")

        # keep snapshots in sync with what we just restored
        from .services import folders as folders_svc

        folders_svc.snapshot(session)
    return 0 if errors == 0 else 2


def rewrite_meta() -> int:
    from .services import folders as folders_svc
    from .services import meta as meta_svc

    with SessionLocal() as session:
        count = 0
        for item in session.execute(sa.select(Item)).scalars():
            meta_svc.write_meta(item)
            count += 1
        folders_svc.snapshot(session)
    print(f"rewrote meta.json for {count} items (+ folders.json)")
    return 0


def regen_previews(do_all: bool) -> int:
    from .services import previews

    settings = get_settings()
    done = skipped = failed = 0
    with SessionLocal() as session:
        items = session.execute(sa.select(Item)).scalars().all()
        for item in items:
            if kind_for(item.mime, item.ext) == "other":
                if item.preview_status != PreviewStatus.NONE:
                    item.preview_status, item.preview_error = PreviewStatus.NONE, None
                    session.commit()
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
                skipped += 1
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
    print(f"previews: {done} regenerated, {skipped} already fine, {failed} failed")
    return 0 if failed == 0 else 2


def verify() -> int:
    """Report where Postgres and the library on disk disagree."""
    from .services import meta as meta_svc

    settings = get_settings()
    problems: list[str] = []
    compared = ("filename", "rating", "notes", "source_url", "tags", "folder_ids",
                "deleted_at", "sha256")
    with SessionLocal() as session:
        items = session.execute(sa.select(Item)).scalars().all()
        db_ids = {i.id for i in items}
        for item in items:
            item_dir = settings.items_dir / item.id
            if not (item_dir / f"original.{item.ext}").is_file():
                problems.append(f"{item.id}: original file missing ({item.filename})")
            meta_file = item_dir / "meta.json"
            if not meta_file.is_file():
                problems.append(f"{item.id}: meta.json missing")
                continue
            try:
                on_disk = json.loads(meta_file.read_text())
            except ValueError:
                problems.append(f"{item.id}: meta.json unreadable")
                continue
            expected = meta_svc.to_meta(item)
            drift = [k for k in compared if on_disk.get(k) != expected.get(k)]
            if drift:
                problems.append(f"{item.id}: meta.json differs in {', '.join(drift)}")
            if item.preview_status == PreviewStatus.READY and not (
                item_dir / "thumb_256.webp"
            ).is_file():
                problems.append(f"{item.id}: marked ready but has no thumbnails")
    if settings.items_dir.is_dir():
        for d in sorted(settings.items_dir.iterdir()):
            if d.is_dir() and d.name not in db_ids:
                problems.append(f"{d.name}: folder on disk with no database row")
    for p in problems:
        print(f"  ! {p}")
    print(f"verify: {len(db_ids)} items checked, {len(problems)} problems")
    if problems:
        print("  fixes: kist rewrite-meta (meta drift), kist rebuild-index (rows "
              "missing), kist regen-previews (thumbnails)")
    return 0 if not problems else 2


def main() -> None:
    parser = argparse.ArgumentParser(prog="kist", description=__doc__)
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

    args = parser.parse_args()
    if args.cmd == "rebuild-index":
        sys.exit(rebuild_index(args.wipe))
    if args.cmd == "rewrite-meta":
        sys.exit(rewrite_meta())
    if args.cmd == "regen-previews":
        sys.exit(regen_previews(args.all))
    if args.cmd == "verify":
        sys.exit(verify())


if __name__ == "__main__":
    main()
