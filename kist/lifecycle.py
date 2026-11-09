"""Kist's state machines, named in one place.

Every surface (REST, MCP, the web UI, the CLI) uses these names and these
transitions instead of reading nullable columns or status strings itself.

Item lifecycle      active --trash--> trashed --restore--> active
                    trashed --purge--> (gone)
Preview status      pending -> ready | failed      none (no preview possible)
Job status          pending -> running -> done | failed (pending again on retry)
"""

from datetime import UTC, datetime
from enum import StrEnum


class TransitionError(Exception):
    """A transition that isn't allowed from the current state (HTTP 409)."""


# ---------- item lifecycle ----------


class ItemState(StrEnum):
    ACTIVE = "active"
    TRASHED = "trashed"


def item_state(item) -> ItemState:
    return ItemState.TRASHED if item.deleted_at is not None else ItemState.ACTIVE


def trash(item) -> bool:
    """active -> trashed. Trashing a trashed item is a no-op (returns False),
    so retries and bulk actions stay safe."""
    if item_state(item) is ItemState.TRASHED:
        return False
    item.deleted_at = datetime.now(UTC)
    return True


def restore(item) -> bool:
    """trashed -> active. Restoring an active item is a no-op (returns False)."""
    if item_state(item) is ItemState.ACTIVE:
        return False
    item.deleted_at = None
    return True


def check_purge(item) -> None:
    """Permanent deletion only happens from the trash."""
    if item_state(item) is not ItemState.TRASHED:
        raise TransitionError(
            f"item {item.id} is not in the trash; trash it before deleting it permanently"
        )


# ---------- preview status ----------


class PreviewStatus(StrEnum):
    PENDING = "pending"  # queued for (re)generation
    READY = "ready"  # thumbnails exist
    FAILED = "failed"  # a decoder failed or timed out; see preview_error
    NONE = "none"  # no preview possible for this kind of file


def preview_status_for(kind: str, width: int | None) -> PreviewStatus:
    """The status implied by today's data (used to backfill and rebuild)."""
    if width is not None:
        return PreviewStatus.READY
    if kind == "other":
        return PreviewStatus.NONE
    return PreviewStatus.FAILED


# ---------- jobs ----------


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class JobKind(StrEnum):
    INGEST = "ingest"  # a file dropped in the inbox (or an import dir)
    REGEN_PREVIEW = "regen_preview"  # retry an item's previews


MAX_ATTEMPTS = 3


def retry_delay_seconds(attempts: int) -> int:
    return 10 * attempts
