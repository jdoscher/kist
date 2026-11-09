"""Preview status transitions for an item (lifecycle.PreviewStatus).

Ingest records the outcome of the first attempt; regenerate() retries it
(from the regen_preview job, or `kist regen-previews`).
"""

import logging
from pathlib import Path

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
            probe = thumbs.generate_previews_isolated(
                original, item_dir, item.mime, item.ext, cleanup=False
            )
            phash, colors = thumbs.enrich(item_dir)
            if probe.width is not None:
                item.width, item.height = probe.width, probe.height
            if probe.duration_s is not None:
                item.duration_s = probe.duration_s
            if phash is not None:
                item.phash = phash
            if colors is not None:
                item.dominant_colors = colors
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
    item.phash, item.dominant_colors = None, None
    item.preview_rev = Item.preview_rev + 1
    session.commit()
    meta_svc.sync_item(session, item.id)
    return True
