"""Kist maintenance CLI.

    kist rebuild-index [--wipe]   rebuild Postgres from the library on disk
    kist rewrite-meta             rewrite every meta.json (+ folders.json) from
                                   Postgres; safe while Kist is running
    kist regen-previews [--all]   (re)generate thumbnails; default only
                                   items that have none. --thumbhash-only
                                   just adds missing ThumbHash placeholders
    kist verify                   compare Postgres with the library on disk
                                   and report drift (changes nothing)
    kist reclassify [--dry-run]   give items Kist can't preview (e.g. a web
                                   page saved as .pdf) preview status "none"
    kist selftest                 run tiny generated files through every
                                   preview decoder; exit 1 if one fails
    kist backup                   dump the database to library/.kist and
                                   back the library up with restic
                                   (KIST_BACKUP_REPO); exit 2 if skipped
    kist fixity [--limit N]       re-hash N originals now (the worker does
                                   about 1/30 of them every day)
    kist ai-backfill [--kind embed|ocr] [--limit N]
                                  queue AI jobs for items without results
                                   (they run inside KIST_AI_WINDOW)
    kist ai-sidecars              write AI sidecar files missing on disk
                                   from the database (verify reports them)
    kist helper --url URL         run AI jobs on this machine for a Kist
                                   server (token: $KIST_HELPER_TOKEN)

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
from .models import (
    ApiToken,
    Folder,
    Item,
    ItemEvent,
    ItemFolder,
    ItemLink,
    ItemTag,
    Job,
    SmartFolder,
    Tag,
)


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
                        description=str(f.get("description") or ""),
                    )
                )
            restored.add(f["id"])
            del pending[f["id"]]
        session.flush()
    session.commit()
    return restored | known, warnings


def item_from_meta(session, meta: dict, original: Path, folder_ids: set[str]) -> Item:
    from .services import fixity as fixity_svc
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
        thumbhash=meta.get("thumbhash") if isinstance(meta.get("thumbhash"), str) else None,
        rating=max(0, min(5, int(meta.get("rating") or 0))),
        protected=bool(meta.get("protected")),
        flag=_known(meta.get("flag"), "flag"),
        label=_known(meta.get("label"), "label"),
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
    item.fixity_checked_at, item.fixity_failed_at = fixity_svc.from_log(original.parent)
    item.tags = tags_svc.get_or_create(session, meta.get("tags", []), validate=False)
    item.folders = [session.get(Folder, f) for f in raw_folders if f in folder_ids]
    return item


def _known(value, field: str) -> str | None:
    """A flag/label from meta.json, or None if it isn't one Kist knows."""
    from .services import validation

    try:
        return validation.flag(value) if field == "flag" else validation.label(value)
    except validation.ValidationError:
        return None


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
    from .services import ai
    from .services import events as events_svc
    from .services import folders as folders_svc
    from .services import links as links_svc
    from .services import tokens as tokens_svc

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
            # Milestone 3: back from board.json / annotations.json below
            from .models import Annotation, Board, BoardEvent

            session.execute(sa.delete(BoardEvent))
            session.execute(sa.delete(Board))  # board_items cascade
            session.execute(sa.delete(Annotation))
            session.execute(sa.delete(ItemEvent))  # back from events.jsonl below
            session.execute(sa.delete(ItemLink))
            session.execute(sa.delete(ItemTag))
            session.execute(sa.delete(ItemFolder))
            session.execute(sa.delete(Item))
            session.execute(sa.delete(Tag))
            session.execute(sa.delete(SmartFolder))
            session.execute(sa.delete(Folder))
            if tokens_svc.path().is_file():
                # tokens come back from tokens.json just below
                session.execute(sa.delete(ApiToken))
            session.commit()

        restored_tokens, token_problem = tokens_svc.restore(session)
        if token_problem:
            warn(f"tokens.json unreadable: {token_problem}")
            warnings += 1
        elif restored_tokens:
            print(f"tokens: {restored_tokens} restored")

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

        # links need both ends: a second pass once every item is back
        links = 0
        for meta_file in sorted(items_dir.glob("*/meta.json")):
            try:
                meta = json.loads(meta_file.read_text())
                if meta.get("links") and session.get(Item, meta.get("id")) is not None:
                    links += links_svc.restore_from_meta(session, meta["id"], meta["links"])
                    session.commit()
            except Exception as exc:
                session.rollback()
                warn(f"{meta_file}: links not restored: {exc}")
                warnings += 1
        if links:
            print(f"links: {links} restored")
        restored_events, bad_lines = events_svc.restore(session)
        print(f"history: {restored_events} events restored")
        if bad_lines:
            warn(f"{bad_lines} unreadable lines in events.jsonl files were skipped")
            warnings += 1
        if recovered:
            print(f"items without meta.json restored from their files: {recovered}")

        # AI results come back from their sidecar files (never recomputed),
        # for restored items and for present items whose rows are missing
        emb = txt = 0
        for item_id in session.execute(sa.select(Item.id).order_by(Item.id)).scalars().all():
            try:
                e, t = ai.load_sidecars(session, item_id)
                emb, txt = emb + e, txt + t
            except Exception as exc:
                session.rollback()
                warn(f"{item_id}: AI files unreadable: {exc}")
                warnings += 1
        print(f"AI results loaded from sidecars: {emb} embeddings, {txt} texts")
        warnings += restore_creative(session)

        # keep snapshots in sync with what we just restored
        folders_svc.snapshot(session)
    if errors or warnings or recovered:
        print("finished with warnings (see the lines marked !)", file=sys.stderr)
        return 2
    return 0


def restore_creative(session) -> int:
    """Milestone 3: annotations, boards and board history from their files.
    Returns the number of warnings."""
    from .services import annotations as annotations_svc
    from .services import boards as boards_svc

    warnings = 0
    restored, bad = annotations_svc.restore(session)
    print(f"annotations: {restored} restored")
    if bad:
        warn(f"{bad} annotations.json files unreadable (see the log)")
        warnings += 1
    boards, board_events, problems = boards_svc.restore(session)
    print(f"boards: {boards} restored, {board_events} board events")
    if problems:
        warn(f"{problems} board files or event lines unreadable (see the log)")
        warnings += 1
    return warnings


# ---------- the rest ----------


def rewrite_meta() -> int:
    from .services import events as events_svc
    from .services import meta as meta_svc
    from .services import tokens as tokens_svc

    written = failed = 0
    with SessionLocal() as session:
        ids = session.execute(sa.select(Item.id).order_by(Item.id)).scalars().all()
        for item_id in ids:
            # re-read under the item's lock as it's written: an edit made
            # meanwhile is never overwritten with older data
            if meta_svc.sync_item(session, item_id):
                written += 1
                if not events_svc.rewrite_item_file(session, item_id):
                    failed += 1
            elif session.get(Item, item_id) is not None and meta_svc.item_dir(item_id).is_dir():
                failed += 1
        folders_ok = meta_svc.sync_folders(session)
        folders_ok = tokens_svc.sync_file(session) and folders_ok
        failed += rewrite_creative(session)
    if not failed and folders_ok:
        meta_svc.clear_pending()
    print(f"rewrote meta.json for {written} items (+ folders.json)"
          + (f"; {failed} failed (see the log)" if failed else ""))
    return 0 if not failed and folders_ok else 2


def rewrite_creative(session) -> int:
    """Milestone 3: annotations.json and board files from the database.
    Returns how many failed."""
    from .models import Annotation
    from .services import annotations as annotations_svc
    from .services import boards as boards_svc

    failed = 0
    for item_id in session.execute(sa.select(Annotation.item_id).distinct()).scalars().all():
        if not annotations_svc.sync_file(session, item_id):
            failed += 1
    _, board_failed = boards_svc.rewrite_files(session)
    return failed + board_failed


def regen_thumbhashes() -> int:
    """Compute the ThumbHash placeholder for items that have previews but no
    ThumbHash yet (the worker also does this in the background)."""
    from .services import previews

    done = failed = 0
    with SessionLocal() as session:
        ids = session.execute(
            sa.select(Item.id).where(
                Item.thumbhash.is_(None), Item.preview_status == PreviewStatus.READY
            ).order_by(Item.id)
        ).scalars().all()
        for item_id in ids:
            item = session.get(Item, item_id, populate_existing=True)
            if item is None:
                continue
            if item.thumbhash is not None:
                # the worker's background backfill got there first
                done += 1
                continue
            if previews.add_thumbhash(session, item):
                done += 1
            else:
                failed += 1
                print(f"  ! {item.id} {item.filename}: no ThumbHash (preview unreadable)",
                      file=sys.stderr)
    print(f"thumbhash: {done} added, {failed} couldn't be made")
    return 0 if not failed else 2


def regen_previews(do_all: bool, thumbhash_only: bool = False) -> int:
    from .services import previews

    if thumbhash_only:
        return regen_thumbhashes()
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


def backup() -> int:
    from .services import backup as backup_svc

    result = backup_svc.run()
    db = result["db_dump"]
    print(f"database dump: {'written to ' + db['path'] if db.get('ok') else db.get('error')}")
    if result["ok"]:
        print(f"backup: snapshot {result['snapshot_id']} in {result['repo']} "
              f"({result['files']} files, {result['bytes']} bytes)")
        return 0
    print(f"backup: {result['error']}", file=sys.stderr)
    return 2 if result.get("skipped") else 1


def fixity(limit: int) -> int:
    from .services import fixity as fixity_svc

    checked = failed = 0
    with SessionLocal() as session:
        ids = session.execute(
            sa.select(Item.id)
            .order_by(Item.fixity_checked_at.asc().nulls_first(), Item.id)
            .limit(limit)
        ).scalars().all()
        for item_id in ids:
            item = session.get(Item, item_id, populate_existing=True)
            if item is None:
                continue
            checked += 1
            if not fixity_svc.check_item(session, item):
                failed += 1
                print(f"  ! {item.id} {item.filename}: changed or unreadable (expected "
                      f"sha256 {item.sha256}); restore it from a backup", file=sys.stderr)
    print(f"fixity: {checked} originals checked, {failed} failed")
    return 0 if not failed else 2


def ai_sidecars() -> int:
    from .services import ai

    with SessionLocal() as session:
        emb, txt = ai.rewrite_missing_sidecars(session)
    print(f"AI sidecars written: {emb} embeddings, {txt} texts")
    return 0


def ai_backfill(kind: str | None, limit: int | None) -> int:
    from .lifecycle import AI_KINDS
    from .services import ai

    kinds = (kind,) if kind else AI_KINDS
    with SessionLocal() as session:
        counts = ai.backfill(session, kinds, limit)
    settings = get_settings()
    print("queued: " + ", ".join(f"{n} {k}" for k, n in counts.items()))
    print(f"they run {'inside ' + settings.ai_window if settings.ai_window else 'now'}"
          + ("" if settings.ml_url else
             " once the ml service is configured (KIST_ML_URL) or a helper claims them"))
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
    p_regen.add_argument(
        "--thumbhash-only", action="store_true",
        help="only compute missing ThumbHash placeholders from existing previews",
    )
    sub.add_parser("verify", help="report drift between Postgres and the library on disk")
    p_reclass = sub.add_parser(
        "reclassify", help="mark items Kist can't preview (e.g. HTML named .pdf) as such"
    )
    p_reclass.add_argument("--dry-run", action="store_true", help="only list what would change")
    sub.add_parser("selftest", help="check every preview decoder with tiny sample files")
    sub.add_parser("backup", help="dump the database and back the library up with restic")
    p_fix = sub.add_parser("fixity", help="re-hash originals now, oldest-checked first")
    p_fix.add_argument("--limit", type=int, default=100,
                       help="how many originals to check (default 100)")
    p_backfill = sub.add_parser("ai-backfill", help="queue AI jobs for items without results")
    p_backfill.add_argument("--kind", choices=["embed", "ocr"], default=None)
    p_backfill.add_argument("--limit", type=int, default=None,
                            help="at most this many jobs per kind")
    sub.add_parser("ai-sidecars", help="write missing AI sidecar files from the database")
    sub.add_parser("helper", help="run AI jobs here for a Kist server (see kist helper -h)",
                   add_help=False)

    if len(sys.argv) > 1 and sys.argv[1] == "helper":
        from . import helper

        sys.exit(helper.main(sys.argv[2:]))
    args = parser.parse_args()
    if args.cmd == "rebuild-index":
        sys.exit(rebuild_index(args.wipe))
    if args.cmd == "rewrite-meta":
        sys.exit(rewrite_meta())
    if args.cmd == "regen-previews":
        sys.exit(regen_previews(args.all, args.thumbhash_only))
    if args.cmd == "verify":
        sys.exit(verify())
    if args.cmd == "reclassify":
        sys.exit(reclassify(args.dry_run))
    if args.cmd == "selftest":
        sys.exit(selftest())
    if args.cmd == "backup":
        sys.exit(backup())
    if args.cmd == "fixity":
        sys.exit(fixity(args.limit))
    if args.cmd == "ai-backfill":
        sys.exit(ai_backfill(args.kind, args.limit))
    if args.cmd == "ai-sidecars":
        sys.exit(ai_sidecars())


if __name__ == "__main__":
    main()
