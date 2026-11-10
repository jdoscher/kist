"""Preview status transitions for an item (lifecycle.PreviewStatus).

Ingest records the outcome of the first attempt; regenerate() retries it
(from the regen_preview job, or `kist regen-previews`).
"""

import logging
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..config import get_settings
from ..filetypes import kind_for
from ..lifecycle import PreviewStatus
from ..models import Item
from . import meta as meta_svc
from . import thumbs

log = logging.getLogger("kist.previews")

ERROR_MAX = 500


def outcome(item_dir: Path, mime: str, ext: str) -> PreviewStatus:
    """Status after a generation attempt that didn't raise."""
    if kind_for(mime, ext) == "other":
        return PreviewStatus.NONE
    if (item_dir / "thumb_256.webp").is_file():
        return PreviewStatus.READY
    from .media import NO_PREVIEW_OK

    if kind_for(mime, ext) in NO_PREVIEW_OK:  # a font, a .fig with no thumbnail
        return PreviewStatus.NONE
    return PreviewStatus.FAILED


def short_error(exc: BaseException) -> str:
    """One readable line for people and agents. The preview child reports a
    traceback tail; its last line is the actual error."""
    text = str(exc).strip() or type(exc).__name__
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    line = lines[-1] if lines else text
    if line.startswith("^"):  # a caret marker, not the message
        line = lines[-2] if len(lines) > 1 else text
    return line[-ERROR_MAX:]


def regenerate(session: Session, item: Item) -> PreviewStatus:
    """Retry an item's previews. Keeps the old previews if this attempt fails."""
    item_dir = get_settings().items_dir / item.id
    original = item_dir / f"original.{item.ext}"
    if kind_for(item.mime, item.ext) == "other":
        item.preview_status, item.preview_error = PreviewStatus.NONE, None
    elif not original.is_file():
        item.preview_status = PreviewStatus.FAILED
        item.preview_error = "original file missing on disk"
    else:
        try:
            from . import media

            cover = (item.media or {}).get("cover_time")
            probe = thumbs.generate_previews_isolated(
                original, item_dir, item.mime, item.ext, cleanup=False,
                options={"cover_time": cover} if cover is not None else None,
            )
            phash, colors = thumbs.enrich(item_dir, kind_for(item.mime, item.ext))
            if probe.width is not None:
                item.width, item.height = probe.width, probe.height
            if probe.duration_s is not None:
                item.duration_s = probe.duration_s
            media.apply(item, media.merge(item.media, probe.media))
            if phash is not None:
                item.phash = phash
            if colors is not None:
                item.dominant_colors = colors
            placeholder = thumbs.thumbhash_for(item_dir)
            if placeholder is not None:
                item.thumbhash = placeholder
            item.preview_status = outcome(item_dir, item.mime, item.ext)
            item.preview_error = (
                None if item.preview_status is not PreviewStatus.FAILED
                else "no thumbnail was produced"
            )
        except Exception as exc:
            log.warning("preview retry failed for %s: %s", item.id, exc)
            # old thumbnails are kept on a failed retry: still viewable
            kept = item.width is not None and (item_dir / "thumb_256.webp").is_file()
            item.preview_status = PreviewStatus.READY if kept else PreviewStatus.FAILED
            item.preview_error = short_error(exc)
    status = PreviewStatus(item.preview_status)
    # in SQL, so two processes regenerating one item both move it on
    item.preview_rev = Item.preview_rev + 1
    session.commit()
    if not meta_svc.sync_item(session, item.id):  # (it refreshes the item)
        try:
            session.refresh(item)
        except Exception:  # purged meanwhile
            session.rollback()
    return status


def add_screen_copy(session: Session, item: Item) -> bool:
    """Make only the 2048 full-screen copy (items ingested before it existed).
    Returns False when the item doesn't need one or it already exists."""
    item_dir = get_settings().items_dir / item.id
    if (
        kind_for(item.mime, item.ext) != "image"
        or item.preview_status != PreviewStatus.READY
        or not thumbs.needs_screen_copy(item.width, item.height)
        or (item_dir / thumbs.SCREEN_FILE).is_file()
    ):
        return False
    thumbs.screen_copy_isolated(item_dir / f"original.{item.ext}", item_dir, item.ext)
    item.preview_rev = Item.preview_rev + 1
    session.commit()
    meta_svc.sync_item(session, item.id)
    return True


def add_thumbhash(session: Session, item: Item) -> bool:
    """Compute a missing ThumbHash from the existing preview (older items).
    One that can't be made is stored as "" so the backfill moves on."""
    if item.thumbhash is not None:
        return False
    value = thumbs.thumbhash_for(get_settings().items_dir / item.id) or ""
    # a placeholder isn't an edit: keep modified_at (or the upgrade backfill
    # would make every item "recently edited"; models.BOOKKEEPING_COLUMNS)
    session.execute(
        sa.update(Item).where(Item.id == item.id).values(
            thumbhash=value, modified_at=Item.modified_at
        )
    )
    session.commit()
    meta_svc.sync_item(session, item.id)
    # `item` still holds the old value (the update bypassed the ORM object)
    return bool(value)


def recompute_colors(session: Session, item: Item) -> bool:
    """Dominant colours again from the existing preview (after the palette
    method changed); item_colors follows by trigger. Not an edit: modified_at
    stays. Returns whether they changed."""
    small = get_settings().items_dir / item.id / "thumb_256.webp"
    if ((small.parent / "preview_icon").exists()
            or kind_for(item.mime, item.ext) in thumbs.DRAWN_PREVIEW_KINDS):
        colors = None  # an icon, waveform or specimen has no palette (thumbs.enrich)
    elif not small.is_file():
        return False
    else:
        colors = thumbs.dominant_colors(small)
    if colors == item.dominant_colors:
        return False
    session.execute(
        sa.update(Item).where(Item.id == item.id).values(
            dominant_colors=colors, modified_at=Item.modified_at
        )
    )
    session.commit()
    meta_svc.sync_item(session, item.id)
    return True


THUMBHASH_BATCH = 50


def backfill_thumbhashes(session: Session, limit: int = THUMBHASH_BATCH) -> int:
    """Give up to `limit` ready items without a ThumbHash one (the worker
    runs this in the background). Returns how many were looked at."""
    import sqlalchemy as sa

    ids = session.execute(
        sa.select(Item.id)
        .where(Item.thumbhash.is_(None), Item.preview_status == PreviewStatus.READY)
        .order_by(Item.id)
        .limit(limit)
    ).scalars().all()
    for item_id in ids:
        item = session.get(Item, item_id)
        if item is not None:
            add_thumbhash(session, item)
    return len(ids)


def reclassify(session: Session, item: Item) -> bool:
    """Make an item's preview state agree with its kind (from its sniffed
    MIME type). An item that can't have a preview (e.g. an HTML page named
    .pdf, which older versions called a PDF) loses any previews it was given
    and becomes preview_status none. Returns True if anything changed."""
    if kind_for(item.mime, item.ext) != "other" or item.preview_status == PreviewStatus.NONE:
        return False
    thumbs.remove_previews(get_settings().items_dir / item.id)
    item.preview_status, item.preview_error = PreviewStatus.NONE, None
    item.width = item.height = item.duration_s = None
    item.phash, item.dominant_colors, item.thumbhash = None, None, None
    item.preview_rev = Item.preview_rev + 1
    session.commit()
    meta_svc.sync_item(session, item.id)
    return True
