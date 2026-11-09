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
    session.commit()
    session.refresh(item)
    meta_svc.write_meta(item)
    return PreviewStatus(item.preview_status)
