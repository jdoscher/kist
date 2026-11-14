"""Re-encode existing thumbnails with the current encoder (perf P3).

`kist regen-previews --thumbs-only` runs this over the library. Every item
whose directory says its thumbnails are older than thumbs.THUMB_ENCODER
(thumbs.encoder_version) gets its three sizes made again:

- from the original, for the raster images libvips reads (JPEG, PNG, WebP,
  GIF, BMP, TIFF: 97 % of Jay's library). Measured 2026-11-14 on 40 of his
  originals (scripts/bench/thumb_encode.py --screen-source): making them
  from the 2048 px screen copy instead is barely faster (an N100 spends
  ~0.15 s on the copy against ~0.2-0.35 s on a 2-7 MB original) but
  compresses twice, and it shows on gradients and product edges (worst-1 %
  SSIM 0.86 against 0.89; gradient wallpapers 0.81 against 0.89);
- from display.webp, the full-resolution copy Kist keeps of a HEIC (the
  Debian libvips can't open current iPhone HEICs, see thumbs.heif_poster);
- through the whole preview pipeline (previews.regenerate) for everything
  else: PDFs, videos, fonts, designs, models, RAW files, SVGs.

The decoders handle files from anywhere, so thumbnails are made in a child
process, BATCH items per child (a child per item would double the time on
an N100); if a child dies, its unfinished items are retried one per child,
so one bad file can only fail itself. New files replace the old ones
atomically (thumbs._save_webp), then preview_rev goes up by one, so
browsers fetch the new files instead of their cached copies, and meta.json
follows. modified_at never moves (thumbnails are bookkeeping). The version
marker is written last, so an interrupted run redoes at most the items it
was on, and a re-run skips everything already done.
"""

import json
import logging
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..config import get_settings
from ..filetypes import kind_for
from ..lifecycle import PreviewStatus
from ..models import Item
from . import meta as meta_svc
from . import thumbs

log = logging.getLogger("kist.thumb_backfill")

# originals libvips reads directly (what ingest made their thumbnails from)
VIPS_ORIGINALS = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp",
                  "image/tiff"}
BATCH = 16


def needs_reencode(item_dir: Path) -> bool:
    return ((item_dir / "thumb_256.webp").is_file()
            and thumbs.encoder_version(item_dir) < thumbs.THUMB_ENCODER)


def source_for(item: Item, item_dir: Path) -> Path | None:
    """What to make the thumbnails from (None: run the preview pipeline)."""
    original = item_dir / f"original.{item.ext}"
    if kind_for(item.mime, item.ext) != "image":
        return None
    if item.mime in VIPS_ORIGINALS and original.is_file():
        return original
    if (item_dir / "display.webp").is_file():  # HEIC/HEIF
        return item_dir / "display.webp"
    return None


def thumbs_batch(pairs: list[tuple[Path, Path]]) -> list[str | None]:
    """Make the thumbnails of each (source, item_dir) in child processes.
    Returns an error message (or None) per pair."""
    if not pairs:
        return []
    limit = get_settings().preview_timeout_seconds
    results: dict[int, str | None] = {}
    stderr = ""
    try:
        out = subprocess.run(
            [sys.executable, "-m", "kist.services.preview_child", "--thumbs-batch"],
            input="\n".join(json.dumps([str(s), str(d)]) for s, d in pairs),
            capture_output=True, text=True, timeout=limit + 5 * len(pairs),
        )
        stderr = out.stderr
        for line in out.stdout.splitlines():
            try:
                r = json.loads(line)
                results[int(r["i"])] = r.get("error")
            except (ValueError, KeyError, TypeError):
                continue
    except subprocess.TimeoutExpired:
        stderr = "timed out"
    if len(pairs) > 1:  # the child died or hung: the rest one at a time
        for i, pair in enumerate(pairs):
            if i not in results:
                results[i] = thumbs_batch([pair])[0]
    why = f"thumbnails failed (the decoder crashed or hung): {stderr[-300:]}"
    return [results.get(i, why) for i in range(len(pairs))]


def bump_rev(session: Session, item_id: str) -> None:
    """preview_rev + 1 without touching modified_at (bookkeeping)."""
    session.execute(
        sa.update(Item).where(Item.id == item_id).values(
            preview_rev=Item.preview_rev + 1, modified_at=Item.modified_at
        )
    )
    session.commit()
    meta_svc.sync_item(session, item_id)


@dataclass
class Totals:
    total: int = 0
    seen: int = 0
    done: int = 0
    skipped: int = 0
    failed: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)

    def line(self) -> str:
        secs = time.monotonic() - self.started
        rate = self.done / secs if secs > 0 else 0.0
        left = self.total - self.seen
        mins = left / rate / 60 if rate > 0 else 0
        eta = (f", about {mins:.0f} min left" if mins >= 1 else ", under a minute left") \
            if rate > 0 and left else ""
        return (f"thumbnails: {self.seen}/{self.total} looked at, {self.done} re-encoded, "
                f"{self.skipped} already current, {self.failed} failed "
                f"({rate:.1f}/s{eta})")


def run(session: Session, ids: Iterable[str] | None = None, *, every: int = 200,
        say: Callable[[str], None] = print, warn: Callable[[str], None] | None = None,
        batch: int = BATCH) -> Totals:
    """Re-encode every ready item's older thumbnails (or only `ids`), with a
    progress line every `every` items looked at (not after the last one:
    the caller prints the totals)."""
    from . import previews

    warn = warn or say
    items_dir = get_settings().items_dir
    if ids is None:
        ids = session.execute(
            sa.select(Item.id).where(Item.preview_status == PreviewStatus.READY)
            .order_by(Item.id)
        ).scalars().all()
    ids = list(ids)
    t = Totals(total=len(ids))
    pending: list[tuple[str, Path, Path]] = []  # (item id, source, item dir)

    def flush() -> None:
        errors = thumbs_batch([(s, d) for _, s, d in pending])
        for (item_id, src, d), err in zip(pending, errors, strict=True):
            if err:
                t.failed += 1
                warn(f"  ! {item_id}: {err[-300:]}")
                continue
            bump_rev(session, item_id)
            thumbs.mark_encoder(d)
            t.done += 1
            how = "display.webp" if src.name == "display.webp" else "original"
            t.by_source[how] = t.by_source.get(how, 0) + 1
        pending.clear()

    for n, item_id in enumerate(ids, 1):
        t.seen = n
        item_dir = items_dir / item_id
        if not needs_reencode(item_dir):
            t.skipped += 1
        else:
            item = session.get(Item, item_id, populate_existing=True)
            if item is None or item.preview_status != PreviewStatus.READY:
                t.skipped += 1
            elif (src := source_for(item, item_dir)) is not None:
                pending.append((item_id, src, item_dir))
                if len(pending) >= batch:
                    flush()
            else:
                try:
                    status = previews.regenerate(session, item)  # bumps preview_rev
                    error = item.preview_error
                except Exception as exc:
                    session.rollback()
                    status, error = PreviewStatus.FAILED, str(exc)
                if status == PreviewStatus.FAILED or error:
                    t.failed += 1
                    warn(f"  ! {item_id}: {error}")
                else:
                    thumbs.mark_encoder(item_dir)
                    t.done += 1
                    t.by_source["previews"] = t.by_source.get("previews", 0) + 1
        if every and n % every == 0 and n < t.total:  # (the caller prints the last)
            flush()
            say(t.line())
    flush()
    t.seen = t.total
    return t
