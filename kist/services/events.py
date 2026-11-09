"""Item history: one event per item per change, and undo.

Every item mutation (create, field edits, tags, folders, trash, restore,
permanent delete, links, protect) writes an `item_events` row in the same
transaction as the change, and after the commit appends the same event as a
line to items/<id>/events.jsonl (a purge goes to library/.kist/
events-purged.jsonl, with the item's earlier lines, since its folder is
removed). The lines are the durable copy rebuild-index restores.

Who and why come from the context: the actor (a token's name, or cli /
worker / importer), the source (web, mcp, api, cli, worker, import) and a
batch id that groups what one request, MCP tool call or CLI command did.
Undo reverts a batch (or one event) newest first, as a new batch, and skips
an event when a later change outside the batch touched the same field.
"""

import contextlib
import contextvars
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session

from ..config import get_settings
from ..ids import new_ulid
from ..models import Item, ItemEvent

log = logging.getLogger("kist.events")

FILE = "events.jsonl"
PURGED_FILE = "events-purged.jsonl"
SOURCES = ("web", "mcp", "api", "cli", "worker", "import")
BATCH_MAX = 64
_BATCH_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

# fields an "edit" event carries; protected has its own kind
EDIT_FIELDS = ("filename", "rating", "notes", "source_url", "created_at")


class Kind:
    CREATE = "create"
    EDIT = "edit"
    TAGS = "tags"
    FOLDERS = "folders"
    TRASH = "trash"
    RESTORE = "restore"
    PURGE = "purge"
    LINK = "link"
    UNLINK = "unlink"
    PROTECT = "protect"


class NotFound(Exception):
    pass


# ---------- context ----------


@dataclass(frozen=True)
class Context:
    actor: str
    source: str
    batch_id: str


_ctx: contextvars.ContextVar[Context | None] = contextvars.ContextVar("kist_events", default=None)
_process_batch: str | None = None


def context() -> Context:
    """Who is acting. Outside a request, tool call or job it is the CLI,
    with one batch for the whole process (one command)."""
    global _process_batch
    ctx = _ctx.get()
    if ctx is not None:
        return ctx
    if _process_batch is None:
        _process_batch = new_ulid()
    return Context("cli", "cli", _process_batch)


def clean_batch_id(value: str | None) -> str | None:
    """A caller-supplied X-Kist-Batch, or None if it isn't usable."""
    value = (value or "").strip()
    return value if _BATCH_RE.match(value) else None


@contextlib.contextmanager
def acting(actor: str, source: str, batch_id: str | None = None):
    """Run a block as `actor` from `source`, in one batch (new if not given)."""
    ctx = Context(actor, source, batch_id or new_ulid())
    token = _ctx.set(ctx)
    try:
        yield ctx
    finally:
        _ctx.reset(token)


@contextlib.contextmanager
def new_batch():
    """Same actor and source, a fresh batch (undo runs in one)."""
    ctx = context()
    with acting(ctx.actor, ctx.source) as fresh:
        yield fresh


# ---------- recording ----------

_PENDING = "kist_events_pending"
_LINES = "kist_event_lines"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def state(item: Item) -> dict:
    """What events compare: an item's user-editable state."""
    return {
        "filename": item.filename,
        "rating": item.rating,
        "notes": item.notes,
        "source_url": item.source_url,
        "created_at": _iso(item.created_at),
        "protected": bool(item.protected),
        "tags": sorted(t.name for t in item.tags),
        "folder_ids": sorted(f.id for f in item.folders),
        "state": "trashed" if item.deleted_at is not None else "active",
    }


def record(session: Session, item_id: str, kind: str, before, after) -> ItemEvent:
    """Add an event to the current transaction (written to disk on commit)."""
    ctx = context()
    ev = ItemEvent(
        item_id=item_id,
        at=datetime.now(UTC),
        actor=ctx.actor,
        source=ctx.source,
        batch_id=ctx.batch_id,
        kind=kind,
        before=before,
        after=after,
    )
    session.add(ev)
    session.info.setdefault(_PENDING, []).append(ev)
    return ev


def _same_time(a: str | None, b: str | None) -> bool:
    if a == b:
        return True
    try:
        return a is not None and b is not None and (
            datetime.fromisoformat(a) == datetime.fromisoformat(b)
        )
    except ValueError:
        return False


def record_changes(session: Session, item_id: str, before: dict, after: dict) -> list[ItemEvent]:
    """One event per kind of change between two state() snapshots."""
    out: list[ItemEvent] = []
    changed = [
        f for f in EDIT_FIELDS
        if not (
            _same_time(before.get(f), after.get(f)) if f == "created_at"
            else before.get(f) == after.get(f)
        )
    ]
    if changed:
        out.append(record(session, item_id, Kind.EDIT,
                          {f: before.get(f) for f in changed},
                          {f: after.get(f) for f in changed}))
    if before.get("protected") != after.get("protected"):
        out.append(record(session, item_id, Kind.PROTECT,
                          {"protected": before.get("protected")},
                          {"protected": after.get("protected")}))
    if [t.lower() for t in before["tags"]] != [t.lower() for t in after["tags"]]:
        out.append(record(session, item_id, Kind.TAGS,
                          {"tags": before["tags"]}, {"tags": after["tags"]}))
    if before["folder_ids"] != after["folder_ids"]:
        out.append(record(session, item_id, Kind.FOLDERS,
                          {"folder_ids": before["folder_ids"]},
                          {"folder_ids": after["folder_ids"]}))
    if before["state"] != after["state"]:
        kind = Kind.TRASH if after["state"] == "trashed" else Kind.RESTORE
        out.append(record(session, item_id, kind,
                          {"state": before["state"]}, {"state": after["state"]}))
    return out


def event_line(ev: ItemEvent) -> dict:
    return {
        "id": ev.id,
        "item_id": ev.item_id,
        "at": _iso(ev.at),
        "actor": ev.actor,
        "source": ev.source,
        "batch_id": ev.batch_id,
        "kind": ev.kind,
        "before": ev.before,
        "after": ev.after,
    }


@sa_event.listens_for(Session, "after_flush")
def _collect(session, flush_context):  # ids exist from here on
    pending = session.info.pop(_PENDING, None)
    if pending:
        session.info.setdefault(_LINES, []).extend(event_line(ev) for ev in pending)


@sa_event.listens_for(Session, "after_commit")
def _write(session):
    lines = session.info.pop(_LINES, None)
    if lines:
        write_lines(lines)


@sa_event.listens_for(Session, "after_rollback")
def _drop(session):
    session.info.pop(_PENDING, None)
    session.info.pop(_LINES, None)


def item_file(item_id: str) -> Path:
    return get_settings().items_dir / item_id / FILE


def purged_file() -> Path:
    return get_settings().state_dir / PURGED_FILE


def _append(path: Path, lines: list[dict]) -> None:
    text = "".join(json.dumps(line, ensure_ascii=False, default=str) + "\n" for line in lines)
    with path.open("a", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def write_lines(lines: list[dict]) -> None:
    """Append committed events to their items' events.jsonl. Never raises:
    the events are safe in Postgres, and `kist rewrite-meta` rewrites the
    files from there (verify reports the gap meanwhile)."""
    from . import meta as meta_svc

    by_path: dict[Path, list[dict]] = {}
    for line in lines:
        if line["kind"] == Kind.PURGE:
            path = purged_file()
        else:
            path = item_file(line["item_id"])
            if not path.parent.is_dir():
                continue  # purged meanwhile; its purge line carries the story
        by_path.setdefault(path, []).append(line)
    for path, chunk in by_path.items():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _append(path, chunk)
        except OSError:
            log.exception("events not written to %s (kist rewrite-meta writes them)", path)
            meta_svc.note_pending(f"events:{chunk[0]['item_id']}")


def keep_purged_history(item_id: str) -> None:
    """Before an item's folder is removed, move its events.jsonl lines to
    library/.kist/events-purged.jsonl so rebuild-index still has them."""
    path = item_file(item_id)
    try:
        lines = path.read_text(encoding="utf-8")
    except OSError:
        return
    if not lines.strip():
        return
    try:
        target = purged_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            f.write(lines if lines.endswith("\n") else lines + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        log.exception("history of purged item %s not kept on disk", item_id)


def rewrite_item_file(session: Session, item_id: str) -> bool:
    """Rewrite items/<id>/events.jsonl from the database (rewrite-meta)."""
    from . import meta as meta_svc

    if not item_file(item_id).parent.is_dir():
        return False
    rows = session.execute(
        sa.select(ItemEvent).where(ItemEvent.item_id == item_id).order_by(ItemEvent.id)
    ).scalars().all()
    if not rows and not item_file(item_id).exists():
        return True
    meta_svc.atomic_write_text(
        item_file(item_id),
        "".join(json.dumps(event_line(e), ensure_ascii=False, default=str) + "\n" for e in rows),
    )
    return True


# ---------- reading ----------


def history(session: Session, item_id: str, limit: int = 200) -> list[ItemEvent]:
    return list(
        session.execute(
            sa.select(ItemEvent).where(ItemEvent.item_id == item_id)
            .order_by(ItemEvent.id.desc()).limit(limit)
        ).scalars()
    )


def recent_batches(session: Session, limit: int = 50, actor: str | None = None) -> list[dict]:
    stmt = (
        sa.select(
            ItemEvent.batch_id,
            sa.func.min(ItemEvent.actor).label("actor"),
            sa.func.min(ItemEvent.source).label("source"),
            sa.func.max(ItemEvent.at).label("at"),
            sa.func.count().label("count"),
            sa.func.array_agg(sa.distinct(ItemEvent.kind)).label("kinds"),
            sa.func.max(ItemEvent.id).label("last_id"),
        )
        .group_by(ItemEvent.batch_id)
        .order_by(sa.text("last_id DESC"))
        .limit(limit)
    )
    if actor:
        stmt = stmt.where(ItemEvent.actor == actor)
    return [
        {"batch_id": r.batch_id, "actor": r.actor, "source": r.source, "at": r.at,
         "count": r.count, "kinds": sorted(r.kinds)}
        for r in session.execute(stmt).all()
    ]


# ---------- undo ----------


def event_keys(kind: str, before, after) -> set[str]:
    """What an event touched, for conflict checks."""
    before, after = before or {}, after or {}
    if kind == Kind.EDIT:
        return {f"field:{f}" for f in set(before) | set(after)}
    if kind == Kind.PROTECT:
        return {"protected"}
    if kind == Kind.TAGS:
        a = {t.lower() for t in before.get("tags", [])}
        b = {t.lower() for t in after.get("tags", [])}
        return {f"tag:{t}" for t in a ^ b}
    if kind == Kind.FOLDERS:
        a, b = set(before.get("folder_ids", [])), set(after.get("folder_ids", []))
        return {f"folder:{f}" for f in a ^ b}
    if kind in (Kind.LINK, Kind.UNLINK):
        link = after.get("link") or before.get("link") or {}
        return {f"link:{link.get('item_id')}:{link.get('kind')}"}
    return {"state"}  # create, trash, restore, purge


def _describe(keys: set[str]) -> str:
    names = sorted(k.split(":", 1)[-1] if k.startswith("field:") else k for k in keys)
    return ", ".join(names)


def _apply_inverse(session: Session, ev: ItemEvent) -> str | None:
    """Revert one event through the services (which record the new events).
    Returns why it was skipped, or None when it was reverted."""
    from ..access import Forbidden
    from ..lifecycle import TransitionError
    from . import items as items_svc
    from . import validation

    before, after = ev.before or {}, ev.after or {}
    try:
        if ev.kind == Kind.EDIT:
            item = items_svc.get_item(session, ev.item_id)
            now = state(item)
            for f, value in after.items():
                same = _same_time(now.get(f), value) if f == "created_at" else now.get(f) == value
                if not same:
                    return f"{f} was changed later"
            fields = dict(before)
            if fields.get("created_at"):
                fields["created_at"] = datetime.fromisoformat(fields["created_at"])
            items_svc.update_item(session, ev.item_id, fields=fields)
        elif ev.kind == Kind.PROTECT:
            items_svc.update_item(
                session, ev.item_id, fields={"protected": bool(before.get("protected"))}
            )
        elif ev.kind == Kind.TAGS:
            old = {t.lower(): t for t in before.get("tags", [])}
            new = {t.lower(): t for t in after.get("tags", [])}
            items_svc.set_tags(
                session, ev.item_id,
                add=[old[k] for k in old.keys() - new.keys()],
                remove=[new[k] for k in new.keys() - old.keys()],
            )
        elif ev.kind == Kind.FOLDERS:
            old, new = set(before.get("folder_ids", [])), set(after.get("folder_ids", []))
            items_svc.set_folders(
                session, ev.item_id, add=sorted(old - new), remove=sorted(new - old)
            )
        elif ev.kind in (Kind.TRASH,):
            items_svc.restore(session, ev.item_id)
        elif ev.kind in (Kind.RESTORE, Kind.CREATE):
            items_svc.soft_delete(session, ev.item_id)
        elif ev.kind in (Kind.LINK, Kind.UNLINK):
            from . import links as links_svc

            link = (after if ev.kind == Kind.LINK else before).get("link") or {}
            src, dst = (
                (ev.item_id, link["item_id"]) if link.get("direction", "out") == "out"
                else (link["item_id"], ev.item_id)
            )
            if ev.kind == Kind.LINK:
                links_svc.unlink(session, src, dst, link["kind"])
            else:
                links_svc.link(session, src, dst, link["kind"])
        elif ev.kind == Kind.PURGE:
            return "it was permanently deleted; only a backup can bring it back"
        else:
            return f"events of kind {ev.kind!r} can't be undone"
    except items_svc.NotFound:
        session.rollback()
        return "the item no longer exists"
    except Forbidden as exc:
        session.rollback()
        return str(exc)
    except (TransitionError, validation.ValidationError) as exc:
        session.rollback()
        return str(exc)
    return None


def revert(session: Session, events: list[ItemEvent], exclude_batches: set[str]) -> dict:
    """Revert `events` newest first in a new batch. An event is skipped when
    a later event on the same item, outside `exclude_batches` and the new
    batch, touched the same thing."""
    undone = 0
    skipped: list[dict] = []
    with new_batch() as ctx:
        exclude = set(exclude_batches) | {ctx.batch_id}
        for ev in sorted(events, key=lambda e: e.id, reverse=True):
            keys = event_keys(ev.kind, ev.before, ev.after)
            later = session.execute(
                sa.select(ItemEvent).where(
                    ItemEvent.item_id == ev.item_id,
                    ItemEvent.id > ev.id,
                    ItemEvent.batch_id.not_in(sorted(exclude)),
                )
            ).scalars().all()
            touched = set().union(*(event_keys(e.kind, e.before, e.after) for e in later)) & keys
            if touched and ev.kind != Kind.PURGE:
                skipped.append({"item_id": ev.item_id, "event_id": ev.id,
                                "reason": f"changed later: {_describe(touched)}"})
                continue
            reason = _apply_inverse(session, ev)
            if reason:
                skipped.append({"item_id": ev.item_id, "event_id": ev.id, "reason": reason})
            else:
                undone += 1
    return {"undone": undone, "skipped": skipped, "batch_id": ctx.batch_id}


def undo_batch(session: Session, batch_id: str) -> dict:
    events = session.execute(
        sa.select(ItemEvent).where(ItemEvent.batch_id == batch_id)
    ).scalars().all()
    if not events:
        raise NotFound(f"batch {batch_id} not found")
    return revert(session, list(events), {batch_id})


def revert_event(session: Session, event_id: int) -> dict:
    ev = session.get(ItemEvent, event_id)
    if ev is None:
        raise NotFound(f"event {event_id} not found")
    return revert(session, [ev], set())


# ---------- the durable copy ----------


def read_file_lines(path: Path) -> tuple[list[dict], int]:
    """(events, unreadable lines) from an events.jsonl."""
    out: list[dict] = []
    bad = 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out, 0
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
            if not isinstance(line, dict) or not isinstance(line.get("id"), int):
                raise ValueError
            out.append(line)
        except ValueError:
            bad += 1
    return out, bad


def restore(session: Session) -> tuple[int, int]:
    """rebuild-index: insert every event in the events.jsonl files that the
    database lacks. Returns (restored, unreadable lines)."""
    settings = get_settings()
    paths = [purged_file()]
    if settings.items_dir.is_dir():
        paths += sorted(settings.items_dir.glob(f"*/{FILE}"))
    restored = bad = 0
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    for path in paths:
        lines, unreadable = read_file_lines(path)
        bad += unreadable
        rows = []
        for line in lines:
            try:
                rows.append({
                    "id": int(line["id"]),
                    "item_id": str(line["item_id"]),
                    "at": (datetime.fromisoformat(line["at"]) if line.get("at")
                           else datetime.now(UTC)),
                    "actor": str(line.get("actor") or "unknown"),
                    "source": str(line.get("source") or "api"),
                    "batch_id": str(line.get("batch_id") or line["id"]),
                    "kind": str(line["kind"]),
                    "before": line.get("before"),
                    "after": line.get("after"),
                })
            except (KeyError, TypeError, ValueError):
                bad += 1
        if rows:
            result = session.execute(
                pg_insert(ItemEvent).values(rows).on_conflict_do_nothing(index_elements=["id"])
            )
            restored += result.rowcount or 0
    # new events continue after the highest restored id
    session.execute(sa.text(
        "SELECT setval(pg_get_serial_sequence('item_events', 'id'), "
        "GREATEST((SELECT COALESCE(MAX(id), 0) FROM item_events), 1), "
        "(SELECT COUNT(*) > 0 FROM item_events))"
    ))
    session.commit()
    return restored, bad


def check_files(session: Session) -> list[str]:
    """verify: every item's events.jsonl has all of its events."""
    settings = get_settings()
    counts = dict(
        session.execute(
            sa.select(ItemEvent.item_id, sa.func.count())
            .join(Item, Item.id == ItemEvent.item_id)
            .group_by(ItemEvent.item_id)
        ).all()
    )
    problems = []
    for item_id, n in sorted(counts.items()):
        path = settings.items_dir / item_id / FILE
        if not path.parent.is_dir():
            continue  # reported as a missing folder elsewhere
        lines, bad = read_file_lines(path)
        ids = {line["id"] for line in lines}
        if len(ids) < n or bad:
            problems.append(
                f"{item_id}: events.jsonl has {len(ids)} of {n} events"
                + (f" and {bad} unreadable lines" if bad else "")
            )
    return problems
