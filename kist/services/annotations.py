"""Annotations: notes pinned to a point, a box or a time range of an item.

Postgres holds them in `annotations` (searched by `q`); the durable copy is
items/<id>/annotations.json, rewritten from the committed rows after every
change under the item's own lock (the same pattern as meta.json), so
rebuild-index restores them.

Coordinates are fractions of the image (0-1, origin top left), so they stay
right at any preview size; times are seconds into a video. Every change is
an item event of kind "annotation" (before/after: the annotation, or null),
so it shows in the item's history and undo reverts it.
"""

import json
import logging
import math
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..ids import new_ulid
from ..models import Annotation, Item, ItemEvent
from . import events, validation
from . import items as items_svc
from . import meta as meta_svc

log = logging.getLogger("kist.annotations")

FILE = "annotations.json"
KINDS = ("point", "box", "range")
TEXT_MAX = 4000
PER_ITEM_MAX = 500
COORDS = ("x", "y", "w", "h", "t_start", "t_end")


class NotFound(Exception):
    """No annotation with that id on that item (HTTP 404)."""


def as_dict(a: Annotation) -> dict:
    return {
        "id": a.id,
        "item_id": a.item_id,
        "kind": a.kind,
        "x": a.x, "y": a.y, "w": a.w, "h": a.h,
        "t_start": a.t_start, "t_end": a.t_end,
        "text": a.text,
        "author": a.author,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "resolved": bool(a.resolved),
    }


def _num(value, name: str) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise validation.ValidationError(f"{name} must be a number") from None
    if not math.isfinite(v):
        raise validation.ValidationError(f"{name} must be a finite number")
    return v


def check(kind: str, data: dict) -> dict:
    """Validate the geometry for `kind`; returns the cleaned coordinates
    (the ones a kind doesn't use are None)."""
    if kind not in KINDS:
        raise validation.ValidationError(f"unknown kind {kind!r}; expected point, box or range")
    c = {k: _num(data.get(k), k) for k in COORDS}
    out = dict.fromkeys(COORDS)
    if kind in ("point", "box"):
        for k in ("x", "y") + (("w", "h") if kind == "box" else ()):
            if c[k] is None:
                raise validation.ValidationError(f"a {kind} annotation needs {k}")
            if not 0.0 <= c[k] <= 1.0:
                raise validation.ValidationError(
                    f"{k} must be from 0 to 1 (a fraction of the image)"
                )
            out[k] = c[k]
        if kind == "box":
            if out["w"] <= 0 or out["h"] <= 0:
                raise validation.ValidationError("a box needs a width and height above 0")
            if out["x"] + out["w"] > 1.0 + 1e-9 or out["y"] + out["h"] > 1.0 + 1e-9:
                raise validation.ValidationError("the box reaches outside the image")
    # a time range (video); a point or box may carry one too (a frame)
    if kind == "range" and c["t_start"] is None:
        raise validation.ValidationError("a range annotation needs t_start")
    if c["t_start"] is not None:
        if c["t_start"] < 0:
            raise validation.ValidationError("t_start can't be negative")
        end = c["t_end"] if c["t_end"] is not None else c["t_start"]
        if end < c["t_start"]:
            raise validation.ValidationError("t_end is before t_start")
        out["t_start"], out["t_end"] = c["t_start"], end
    elif c["t_end"] is not None:
        raise validation.ValidationError("t_end needs t_start")
    return out


def _text(value) -> str:
    text = str(value or "")
    if len(text) > TEXT_MAX:
        raise validation.ValidationError(f"annotation text is at most {TEXT_MAX} characters")
    return text


def list_for(session: Session, item_id: str) -> list[Annotation]:
    items_svc.get_item(session, item_id)  # 404 / visibility
    return _rows(session, item_id)


def _rows(session: Session, item_id: str) -> list[Annotation]:
    return list(session.execute(
        sa.select(Annotation).where(Annotation.item_id == item_id)
        .order_by(Annotation.created_at, Annotation.id)
    ).scalars())


def _get(session: Session, item_id: str, ann_id: str) -> Annotation:
    a = session.get(Annotation, ann_id)
    if a is None or a.item_id != item_id:
        raise NotFound(f"annotation {ann_id} not found on item {item_id}")
    return a


def create(session: Session, item_id: str, data: dict, *, ann_id: str | None = None,
           created_at: datetime | None = None, author: str | None = None) -> Annotation:
    items_svc.get_item(session, item_id)
    count = session.execute(
        sa.select(sa.func.count()).select_from(Annotation).where(Annotation.item_id == item_id)
    ).scalar_one()
    if count >= PER_ITEM_MAX:
        raise validation.ValidationError(f"an item can have at most {PER_ITEM_MAX} annotations")
    kind = data.get("kind")
    coords = check(kind, data)
    a = Annotation(
        id=ann_id or new_ulid(), item_id=item_id, kind=kind, **coords,
        text=_text(data.get("text")),
        author=author if author is not None else events.context().actor,
        created_at=created_at or datetime.now(UTC),
        resolved=bool(data.get("resolved", False)),
    )
    session.add(a)
    session.flush()
    events.record(session, item_id, events.Kind.ANNOTATION, None, {"annotation": as_dict(a)})
    _commit(session, item_id)
    return a


def update(session: Session, item_id: str, ann_id: str, fields: dict) -> Annotation:
    items_svc.get_item(session, item_id)
    a = _get(session, item_id, ann_id)
    before = as_dict(a)
    merged = {k: fields[k] if fields.get(k) is not None else getattr(a, k) for k in COORDS}
    coords = check(a.kind, merged)
    for k, v in coords.items():
        setattr(a, k, v)
    if fields.get("text") is not None:
        a.text = _text(fields["text"])
    if fields.get("resolved") is not None:
        a.resolved = bool(fields["resolved"])
    after = as_dict(a)
    if after != before:
        events.record(session, item_id, events.Kind.ANNOTATION,
                      {"annotation": before}, {"annotation": after})
        _commit(session, item_id)
    return a


def delete(session: Session, item_id: str, ann_id: str) -> None:
    items_svc.get_item(session, item_id)
    a = _get(session, item_id, ann_id)
    events.record(session, item_id, events.Kind.ANNOTATION, {"annotation": as_dict(a)}, None)
    session.delete(a)
    _commit(session, item_id)


def _commit(session: Session, item_id: str) -> None:
    session.commit()
    sync_file(session, item_id)


# ---------- the durable copy ----------


def path_for(item_id: str):
    return meta_svc.item_dir(item_id) / FILE


def file_rows(session: Session, item_id: str) -> list[dict]:
    return [as_dict(a) for a in _rows(session, item_id)]


def sync_file(session: Session, item_id: str) -> bool:
    """Write items/<id>/annotations.json from the committed rows (after
    commit; never raises: verify reports a failed write)."""
    try:
        meta_svc.advisory_lock(session, f"annotations:{item_id}")
        if session.get(Item, item_id) is None or not meta_svc.item_dir(item_id).is_dir():
            session.commit()
            return False
        rows = file_rows(session, item_id)
        path = path_for(item_id)
        if rows or path.exists():
            meta_svc.atomic_write_text(
                path, json.dumps({"version": 1, "annotations": rows}, indent=2, ensure_ascii=False)
            )
        session.commit()
        return True
    except Exception:
        session.rollback()
        log.exception("annotations.json for %s not written (kist rewrite-meta writes it)", item_id)
        meta_svc.note_pending(f"annotations:{item_id}")
        return False


def read_file(item_id: str) -> list[dict] | None:
    path = path_for(item_id)
    if not path.is_file():
        return None
    data = meta_svc.read_json(path)
    rows = data.get("annotations") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("no annotations list")
    return rows


def restore(session: Session) -> tuple[int, int]:
    """rebuild-index: insert annotations from annotations.json files that
    the database lacks. Returns (restored, unreadable files)."""
    from ..config import get_settings

    restored = bad = 0
    items_dir = get_settings().items_dir
    if not items_dir.is_dir():
        return 0, 0
    for path in sorted(items_dir.glob(f"*/{FILE}")):
        item_id = path.parent.name
        if session.get(Item, item_id) is None:
            continue
        try:
            rows = read_file(item_id) or []
            for r in rows:
                if session.get(Annotation, r["id"]) is not None:
                    continue
                coords = check(r["kind"], r)
                session.add(Annotation(
                    id=r["id"], item_id=item_id, kind=r["kind"], **coords,
                    text=str(r.get("text") or ""), author=str(r.get("author") or ""),
                    created_at=(datetime.fromisoformat(r["created_at"]) if r.get("created_at")
                                else datetime.now(UTC)),
                    resolved=bool(r.get("resolved")),
                ))
                session.flush()
                restored += 1
            session.commit()
        except Exception:
            session.rollback()
            log.exception("%s unreadable", path)
            bad += 1
    return restored, bad


def check_files(session: Session) -> list[str]:
    """verify: every item's annotations.json matches its rows."""
    problems: list[str] = []
    by_item: dict[str, list[dict]] = {}
    for a in session.execute(sa.select(Annotation).order_by(Annotation.item_id)).scalars():
        by_item.setdefault(a.item_id, []).append(as_dict(a))
    from ..config import get_settings

    items_dir = get_settings().items_dir
    on_disk = {p.parent.name for p in items_dir.glob(f"*/{FILE}")} if items_dir.is_dir() else set()
    for item_id in sorted(set(by_item) | on_disk):
        if not meta_svc.item_dir(item_id).is_dir():
            continue
        try:
            disk = read_file(item_id) or []
        except (ValueError, OSError):
            problems.append(f"{item_id}: annotations.json unreadable")
            continue
        db = {r["id"]: r for r in by_item.get(item_id, [])}
        dk = {r.get("id"): r for r in disk if isinstance(r, dict)}
        if db.keys() != dk.keys():
            problems.append(
                f"{item_id}: annotations.json has {len(dk)} annotations, the database {len(db)}"
            )
            continue
        for aid, row in db.items():
            other = dk[aid]
            if any(other.get(k) != row[k] for k in row if k != "created_at") or (
                _instant(other.get("created_at")) != _instant(row["created_at"])
            ):
                problems.append(f"{item_id}: annotation {aid} differs in annotations.json")
    return problems


def _instant(value):
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


# ---------- undo (events.EXTRA_KINDS) ----------


class _AnnotationKind(events.ExtraKind):
    def keys(self, before, after) -> set[str]:
        a = (after or {}).get("annotation") or (before or {}).get("annotation") or {}
        return {f"annotation:{a.get('id')}"}

    def inverse(self, session: Session, ev: ItemEvent) -> str | None:
        before = (ev.before or {}).get("annotation")
        after = (ev.after or {}).get("annotation")
        try:
            if before is None and after is not None:  # created: delete it
                delete(session, ev.item_id, after["id"])
            elif after is None and before is not None:  # deleted: put it back
                if session.get(Annotation, before["id"]) is not None:
                    return "it exists again"
                create(session, ev.item_id, before, ann_id=before["id"],
                       created_at=_instant(before.get("created_at")),
                       author=before.get("author") or "")
            elif before is not None:
                update(session, ev.item_id, before["id"],
                       {**{k: before.get(k) for k in COORDS},
                        "text": before.get("text", ""), "resolved": before.get("resolved")})
        except NotFound:
            session.rollback()
            return "the annotation no longer exists"
        return None


events.EXTRA_KINDS[events.Kind.ANNOTATION] = _AnnotationKind()
