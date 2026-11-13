"""Compare Postgres with the library on disk (changes nothing).

Used by `kist verify` and by the worker's daily run, whose last result
library/.kist/verify.json feeds GET /api/health/deep.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..config import get_settings
from ..lifecycle import PreviewStatus
from ..models import Item
from . import meta as meta_svc

log = logging.getLogger("kist.verify")

RESULT_FILE = "verify.json"
# compared as written; timestamps are compared as instants below
COMPARED = (
    "filename", "rating", "notes", "source_url", "tags", "folder_ids", "sha256",
    "preview_status", "preview_rev", "thumbhash", "protected", "links", "stack",
    "flag", "label",
    "media",  # Milestone 5
)
COMPARED_TIMES = ("created_at", "modified_at", "deleted_at")
# keys older meta.json files don't have yet, and what their absence means
MISSING_MEANS = {"protected": False, "links": [], "stack": None, "flag": None, "label": None,
                 "media": None}
FIXES = (
    "fixes: kist rewrite-meta (meta.json or folders.json out of date; safe while Kist "
    "runs, it re-reads each item as it writes it), kist rebuild-index (rows missing "
    "from the database), kist regen-previews (thumbnails)"
)


@dataclass
class Report:
    items_checked: int = 0
    problems: list[str] = field(default_factory=list)


def _instant(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


BATCH = 500


def _items_in_batches(session: Session):
    """Every item, a batch at a time (memory stays flat on big libraries)."""
    ids = session.execute(sa.select(Item.id).order_by(Item.id)).scalars().all()
    for start in range(0, len(ids), BATCH):
        chunk = ids[start:start + BATCH]
        yield from session.execute(
            sa.select(Item).where(Item.id.in_(chunk)).order_by(Item.id)
        ).scalars().all()
        session.expunge_all()


def check_items(session: Session, report: Report) -> None:
    settings = get_settings()
    db_ids: set[str] = set()
    for item in _items_in_batches(session):
        db_ids.add(item.id)
        item_dir = settings.items_dir / item.id
        if not (item_dir / f"original.{item.ext}").is_file():
            report.problems.append(f"{item.id}: original file missing ({item.filename})")
        meta_file = item_dir / "meta.json"
        if not meta_file.is_file():
            report.problems.append(f"{item.id}: meta.json missing")
        else:
            try:
                on_disk = meta_svc.read_json(meta_file)
            except ValueError:
                on_disk = None
                report.problems.append(f"{item.id}: meta.json unreadable")
            if isinstance(on_disk, dict):
                expected = meta_svc.to_meta(item)
                drift = [
                    k for k in COMPARED if on_disk.get(k, MISSING_MEANS.get(k)) != expected.get(k)
                ]
                drift += [
                    k for k in COMPARED_TIMES
                    if _instant(on_disk.get(k)) != _instant(expected.get(k))
                ]
                from .provenance import meta_drift

                drift += meta_drift(on_disk, expected)
                if drift:
                    report.problems.append(
                        f"{item.id}: meta.json differs in {', '.join(drift)}"
                    )
        if item.preview_status == PreviewStatus.READY and not (
            item_dir / "thumb_256.webp"
        ).is_file():
            report.problems.append(f"{item.id}: marked ready but has no thumbnails")
    report.items_checked = len(db_ids)
    if settings.items_dir.is_dir():
        for d in sorted(settings.items_dir.iterdir()):
            if d.is_dir() and not d.name.startswith(".") and d.name not in db_ids:
                report.problems.append(f"{d.name}: folder on disk with no database row")


def check_folders(session: Session, report: Report) -> None:
    path = get_settings().library_dir / "folders.json"
    folders, smart = meta_svc.folders_snapshot_rows(session)
    if not path.is_file():
        if folders or smart:
            report.problems.append("folders.json missing")
        return
    try:
        data = meta_svc.read_json(path)
        disk_folders = {f["id"]: f for f in data.get("folders", [])}
        disk_smart = {s["id"]: s for s in data.get("smart_folders", [])}
    except (ValueError, TypeError, KeyError, AttributeError):
        report.problems.append("folders.json unreadable")
        return
    for label, db_rows, disk in (
        ("folder", folders, disk_folders), ("smart folder", smart, disk_smart)
    ):
        db = {r["id"]: r for r in db_rows}
        for fid in sorted(db.keys() - disk.keys()):
            report.problems.append(f"folders.json: {label} {fid} ({db[fid]['name']}) missing")
        for fid in sorted(disk.keys() - db.keys()):
            report.problems.append(f"folders.json: {label} {fid} is not in the database")
        for fid in sorted(db.keys() & disk.keys()):
            if any(disk[fid].get(k) != v for k, v in db[fid].items()):
                report.problems.append(f"folders.json: {label} {fid} ({db[fid]['name']}) differs")


def check_ai(session: Session, report: Report) -> None:
    """AI rows whose sidecar file is missing (a rebuild would lose them),
    and sidecar files the database doesn't have (rebuild-index loads them)."""
    from ..models import ItemEmbedding, ItemText
    from . import ai

    settings = get_settings()
    rows: dict[str, set[str]] = {}
    for item_id, model in session.execute(
        sa.select(ItemEmbedding.item_id, ItemEmbedding.model)
    ).all():
        rows.setdefault(item_id, set()).add(model)
    texts = set(session.execute(
        sa.select(ItemText.item_id).where(ItemText.source.in_(ai.READ_SOURCES))
    ).scalars())
    captions = set(session.execute(
        sa.select(ItemText.item_id).where(ItemText.source == "caption")
    ).scalars())
    for item_id in sorted(captions):
        if not (ai.ai_dir(item_id) / "caption.json").is_file():
            report.problems.append(
                f"{item_id}: caption has no sidecar file ai/caption.json "
                "(kist ai-sidecars writes it from the database)"
            )
    for item_id, models in sorted(rows.items()):
        for model in sorted(models):
            f16, js = ai.embed_paths(item_id, model)
            if not f16.is_file() or not js.is_file():
                report.problems.append(
                    f"{item_id}: embedding ({model}) has no sidecar file "
                    "(kist ai-sidecars writes it from the database)"
                )
    for item_id in sorted(texts):
        if not (ai.ai_dir(item_id) / ai.OCR_FILE).is_file():
            report.problems.append(
                f"{item_id}: OCR/PDF text has no sidecar file ai/ocr.json "
                "(kist ai-sidecars writes it from the database)"
            )
    if not settings.items_dir.is_dir():
        return
    for ai_dir in sorted(settings.items_dir.glob(f"*/{ai.AI_DIR}")):
        item_id = ai_dir.parent.name
        for model in ai.embedding_sidecar_models(item_id):
            if model not in rows.get(item_id, ()):
                report.problems.append(
                    f"{item_id}: AI sidecar embed-{model} is not in the database "
                    "(kist rebuild-index loads it)"
                )
        if (ai_dir / ai.OCR_FILE).is_file() and item_id not in texts:
            report.problems.append(
                f"{item_id}: AI sidecar ocr.json is not in the database "
                "(kist rebuild-index loads it)"
            )
        if (ai_dir / "caption.json").is_file() and item_id not in captions:
            report.problems.append(
                f"{item_id}: AI sidecar caption.json is not in the database "
                "(kist rebuild-index loads it)"
            )


def check_creative(session: Session, report: Report) -> None:
    """Milestone 3: annotations.json, boards/*/board.json + events.jsonl,
    and .kist/templates.json."""
    from . import annotations, boards, templates

    report.problems.extend(annotations.check_files(session))
    report.problems.extend(boards.check_files(session))
    report.problems.extend(templates.check_file(session))


def check_m4(session: Session, report: Report) -> None:
    """Share links (shares.json), page snapshot text (page/text.txt) and
    the export presets file."""
    from . import export_images, shares, snapshots

    report.problems.extend(shares.check_file(session))
    report.problems.extend(snapshots.check(session))
    report.problems.extend(export_images.check_file())
    from . import credits

    report.problems.extend(credits.check_file())  # F4: library/.kist/credits.json


def run(session: Session) -> Report:
    from . import events as events_svc
    from . import tokens as tokens_svc

    report = Report()
    check_items(session, report)
    check_folders(session, report)
    report.problems.extend(tokens_svc.check_file(session))
    report.problems.extend(events_svc.check_files(session))
    check_ai(session, report)
    from . import faces as faces_svc
    from . import suggestions as suggestions_svc
    from . import vocabulary

    report.problems.extend(vocabulary.check_file(session))
    report.problems.extend(vocabulary.check_events_file(session))
    report.problems.extend(faces_svc.check(session))
    report.problems.extend(suggestions_svc.check_feedback_files(session))
    from . import kinds as kinds_svc

    report.problems.extend(kinds_svc.check(session))  # F1
    check_creative(session, report)
    check_m4(session, report)
    from . import colourbooks  # F5

    report.problems.extend(colourbooks.check_files())
    for key in meta_svc.pending():
        report.problems.append(f"{key}: an earlier write to disk failed")
    return report


def save_result(report: Report) -> None:
    """Keep the last result for deep health (best effort)."""
    try:
        meta_svc.atomic_write_text(
            get_settings().state_dir / RESULT_FILE,
            json.dumps(
                {
                    "last_run": datetime.now(UTC).isoformat(),
                    "items_checked": report.items_checked,
                    "drift": len(report.problems),
                    "problems": report.problems[:50],
                },
                indent=2,
            ),
        )
    except OSError:
        log.exception("could not save the verify result")


def last_result() -> dict | None:
    try:
        return meta_svc.read_json(get_settings().state_dir / RESULT_FILE)
    except (OSError, ValueError):
        return None
