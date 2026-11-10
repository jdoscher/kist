"""Stacks: several items shown as one in the grid (a RAW and its JPEG, a
burst, versions of one picture).

A stack has members (items.stack_id) and a cover, the item the grid shows
(GET /api/items?stacks=collapse). Every membership change is an item event
(kind "stack", before/after {stack_id, cover}), so it shows in history and
undoes; meta.json carries "stack": {id, cover} as the durable copy.

Suggestions (never applied by themselves):
- raw_jpeg: a RAW file and a JPEG/HEIC with the same base name, taken at the
  same moment when both carry an EXIF capture time
- burst: photos from the same camera (EXIF make, model) taken within
  BURST_SECONDS of each other
- version: items joined by version_of links
"""

import logging
import os
import warnings
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from .. import access
from ..config import get_settings
from ..ids import new_ulid
from ..models import Item, ItemFolder, ItemLink, Stack
from . import events, validation
from . import meta as meta_svc

log = logging.getLogger("kist.stacks")

BURST_SECONDS = 2.0
RAW_EXTS = frozenset({
    "cr2", "cr3", "crw", "nef", "nrw", "arw", "srf", "sr2", "raf", "dng", "orf", "rw2",
    "pef", "srw", "raw", "rwl", "3fr", "iiq", "x3f", "erf", "mef", "mos", "kdc",
})
PAIR_EXTS = frozenset({"jpg", "jpeg", "heic", "heif", "hif", "tif", "tiff", "png", "webp"})
SUGGEST_MAX_ITEMS = 50_000
EXIF_IFD, DATETIME_ORIGINAL, SUBSEC_ORIGINAL = 0x8769, 0x9003, 0x9291
MAKE, MODEL, DATETIME = 0x010F, 0x0110, 0x0132
STACK_KIND = "stack"


class NotFound(Exception):
    def __init__(self, stack_id: str):
        super().__init__(f"stack {stack_id} not found")


# ---------- reading ----------


def get(session: Session, stack_id: str) -> Stack:
    stack = session.get(Stack, stack_id)
    if stack is None:
        raise NotFound(stack_id)
    return stack


def _visible_cond(alias):
    """Live and, for a folder-limited reader, filed in its folders."""
    conds = [alias.deleted_at.is_(None)]
    reader = access.restricted()
    if reader is not None:
        conds.append(sa.exists().where(
            ItemFolder.item_id == alias.id,
            ItemFolder.folder_id.in_(sorted(reader.visible_folders) or [""]),
        ))
    return conds


def members(session: Session, stack_id: str, *, visible_only: bool = True) -> list[Item]:
    m = aliased(Item)
    stmt = sa.select(m).where(m.stack_id == stack_id)
    if visible_only:
        stmt = stmt.where(*_visible_cond(m))
    return list(session.execute(stmt.order_by(m.created_at, m.id)).scalars())


def stack_out(session: Session, stack: Stack) -> dict:
    """{id, cover_item_id, items} with only the members the caller sees.
    NotFound when it sees none of them."""
    from ..schemas import ItemOut

    items = members(session, stack.id)
    if not items:
        raise NotFound(stack.id)
    ids = {i.id for i in items}
    cover = stack.cover_item_id if stack.cover_item_id in ids else items[0].id
    return {"id": stack.id, "cover_item_id": cover, "count": len(items),
            "items": [ItemOut.from_item(i) for i in items]}


def representative_filter():
    """search.py's stacks=collapse: an item outside any stack, or its
    stack's cover, or (when the caller can't see the cover, or it's in the
    trash) the stack's first member the caller can see."""
    cover = aliased(Item, name="stack_cover")
    member = aliased(Item, name="stack_member")
    cover_id = (
        sa.select(Stack.cover_item_id)
        .join(cover, cover.id == Stack.cover_item_id)
        .where(Stack.id == Item.stack_id, *_visible_cond(cover))
        .scalar_subquery()
    )
    first_id = (
        sa.select(sa.func.min(member.id))
        .where(member.stack_id == Item.stack_id, *_visible_cond(member))
        .scalar_subquery()
    )
    return sa.or_(Item.stack_id.is_(None), Item.id == sa.func.coalesce(cover_id, first_id))


_COUNTS = "kist_stack_counts"


def count_for(item: Item) -> int | None:
    """How many members of the item's stack the caller sees (cached per
    session and caller); None outside a stack."""
    from sqlalchemy.orm import object_session

    if not item.stack_id:
        return None
    session = object_session(item)
    if session is None:
        return None
    key = (_COUNTS, access.current())
    cache = session.info.setdefault(key, {})
    if item.stack_id not in cache:
        m = aliased(Item)
        cache[item.stack_id] = session.execute(
            sa.select(sa.func.count()).select_from(m)
            .where(m.stack_id == item.stack_id, *_visible_cond(m))
        ).scalar_one()
    return cache[item.stack_id]


def forget_counts(session: Session) -> None:
    for key in [k for k in session.info if isinstance(k, tuple) and k[:1] == (_COUNTS,)]:
        session.info.pop(key, None)


def meta_block(item: Item) -> dict | None:
    """meta.json's "stack": {id, cover}."""
    from sqlalchemy.orm import object_session

    if not item.stack_id:
        return None
    session = object_session(item)
    stack = session.get(Stack, item.stack_id) if session is not None else None
    return {"id": item.stack_id, "cover": bool(stack and stack.cover_item_id == item.id)}


# ---------- changing ----------


def _state(session: Session, item: Item) -> dict:
    stack = session.get(Stack, item.stack_id) if item.stack_id else None
    return {"stack_id": item.stack_id, "cover": bool(stack and stack.cover_item_id == item.id)}


def _record(session: Session, item: Item, before: dict) -> bool:
    after = _state(session, item)
    if after == before:
        return False
    events.record(session, item.id, events.Kind.STACK, before, after)
    return True


def _finish(session: Session, item_ids) -> None:
    session.commit()
    forget_counts(session)
    for item_id in sorted(set(item_ids)):
        meta_svc.sync_item(session, item_id)


def _prune(session: Session, stack_ids, touched: set[str]) -> None:
    """Stacks left with fewer than two members dissolve."""
    for sid in set(stack_ids):
        stack = session.get(Stack, sid)
        if stack is None:
            continue
        left = list(session.execute(sa.select(Item).where(Item.stack_id == sid)).scalars())
        if len(left) >= 2:
            if stack.cover_item_id not in {i.id for i in left}:
                before = {i.id: _state(session, i) for i in left}
                stack.cover_item_id = left[0].id
                session.flush()
                for i in left:
                    if _record(session, i, before[i.id]):
                        touched.add(i.id)
            continue
        for i in left:
            before = _state(session, i)
            i.stack_id = None
            session.flush()
            _record(session, i, before)
            touched.add(i.id)
        session.delete(stack)
        session.flush()


def _items(session: Session, item_ids: list[str]) -> list[Item]:
    from . import items as items_svc

    out, seen = [], set()
    for item_id in item_ids or []:
        if item_id in seen:
            continue
        seen.add(item_id)
        out.append(items_svc.get_item(session, item_id))
    return out


def create(session: Session, item_ids: list[str], cover: str | None = None) -> Stack:
    """Stack these items (two or more), cover first in the grid (default:
    the first given). Items already in another stack move to this one."""
    items = _items(session, item_ids)
    if len(items) < 2:
        raise validation.ValidationError("a stack needs at least two items")
    cover = cover or items[0].id
    if cover not in {i.id for i in items}:
        raise validation.ValidationError("the cover must be one of the stacked items")
    old_stacks = {i.stack_id for i in items if i.stack_id}
    old_cover_items = {
        s.cover_item_id for s in session.execute(
            sa.select(Stack).where(Stack.id.in_(old_stacks))).scalars() if s.cover_item_id
    } if old_stacks else set()
    before = {i.id: _state(session, i) for i in items}
    stack = Stack(id=new_ulid(), cover_item_id=None)
    session.add(stack)
    session.flush()
    for i in items:
        i.stack_id = stack.id
    stack.cover_item_id = cover
    session.flush()
    touched = set()
    for i in items:
        if _record(session, i, before[i.id]):
            touched.add(i.id)
    _prune(session, old_stacks, touched)
    _finish(session, touched | old_cover_items)
    return stack


def update(session: Session, stack_id: str, *, cover: str | None = None,
           add: list[str] | None = None, remove: list[str] | None = None) -> Stack:
    """Change the cover and/or add and remove members. A stack left with
    one member dissolves (get then answers 404)."""
    stack = get(session, stack_id)
    touched: set[str] = set()
    old_stacks = set()
    for i in _items(session, add or []):
        if i.stack_id == stack.id:
            continue
        before = _state(session, i)
        if i.stack_id:
            old_stacks.add(i.stack_id)
        i.stack_id = stack.id
        session.flush()
        if _record(session, i, before):
            touched.add(i.id)
    for i in _items(session, remove or []):
        if i.stack_id != stack.id:
            continue
        before = _state(session, i)
        if stack.cover_item_id == i.id:
            stack.cover_item_id = None
        i.stack_id = None
        session.flush()
        if _record(session, i, before):
            touched.add(i.id)
    if cover is not None:
        target = _items(session, [cover])[0]
        if target.stack_id != stack.id:
            raise validation.ValidationError("the cover must be a member of the stack")
        current = list(session.execute(sa.select(Item).where(Item.stack_id == stack.id)).scalars())
        before = {i.id: _state(session, i) for i in current}
        stack.cover_item_id = target.id
        session.flush()
        for i in current:
            if _record(session, i, before[i.id]):
                touched.add(i.id)
    _prune(session, old_stacks | {stack.id}, touched)
    _finish(session, touched)
    return stack


def unstack(session: Session, stack_id: str) -> int:
    """Dissolve a stack; its items stay as they are. Returns how many."""
    stack = get(session, stack_id)
    items = list(session.execute(sa.select(Item).where(Item.stack_id == stack.id)).scalars())
    before = {i.id: _state(session, i) for i in items}
    for i in items:
        i.stack_id = None
    stack.cover_item_id = None
    session.flush()
    for i in items:
        _record(session, i, before[i.id])
    session.delete(stack)
    _finish(session, [i.id for i in items])
    return len(items)


def restore_membership(session: Session, item_id: str, state: dict) -> None:
    """Undo: put one item back in (or out of) a stack as `state` says,
    re-creating the stack row if it was dissolved meanwhile."""
    from . import items as items_svc

    item = items_svc.get_item(session, item_id)
    before = _state(session, item)
    old = item.stack_id
    sid = (state or {}).get("stack_id")
    if sid:
        stack = session.get(Stack, sid)
        if stack is None:
            stack = Stack(id=sid, cover_item_id=None)
            session.add(stack)
            session.flush()
        item.stack_id = sid
        if state.get("cover"):
            stack.cover_item_id = item.id
        elif stack.cover_item_id == item.id:
            stack.cover_item_id = None
    else:
        item.stack_id = None
        if old:
            stack = session.get(Stack, old)
            if stack is not None and stack.cover_item_id == item.id:
                stack.cover_item_id = None
    session.flush()
    touched = {item.id} if _record(session, item, before) else set()
    if old and old != sid:
        stack = session.get(Stack, old)
        left = session.execute(
            sa.select(sa.func.count()).select_from(Item).where(Item.stack_id == old)
        ).scalar_one()
        if stack is not None and left == 0:
            session.delete(stack)
    _finish(session, touched or {item.id})


def restore_from_meta(session: Session, item_id: str, block) -> bool:
    """rebuild-index: meta.json's stack block back into the database."""
    if not isinstance(block, dict) or not isinstance(block.get("id"), str):
        return False
    item = session.get(Item, item_id)
    if item is None:
        return False
    stack = session.get(Stack, block["id"])
    if stack is None:
        stack = Stack(id=block["id"], cover_item_id=None)
        session.add(stack)
        session.flush()
    # a core UPDATE that keeps modified_at: putting the item back in its
    # stack isn't an edit, and meta.json's modified_at must still match
    session.execute(
        sa.update(Item).where(Item.id == item.id)
        .values(stack_id=stack.id, modified_at=Item.modified_at)
        .execution_options(synchronize_session=False)
    )
    session.expire(item, ["stack_id", "modified_at"])
    if block.get("cover"):
        stack.cover_item_id = item.id
    session.flush()
    return True


# ---------- suggestions ----------

_exif_cache: dict[tuple[str, str], tuple[str | None, datetime | None]] = {}


def capture_info(item: Item) -> tuple[str | None, datetime | None]:
    """(camera "Make Model", capture time) from the original's EXIF, or
    Nones. Only the header is read; cached by item and sha256."""
    key = (item.id, item.sha256)
    if key in _exif_cache:
        return _exif_cache[key]
    camera = when = None
    path = get_settings().items_dir / item.id / f"original.{item.ext}"
    try:
        from PIL import Image

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with Image.open(path) as im:
                exif = im.getexif()
                make = str(exif.get(MAKE) or "").strip("\x00 ")
                model = str(exif.get(MODEL) or "").strip("\x00 ")
                camera = f"{make} {model}".strip() or None
                sub = exif.get_ifd(EXIF_IFD)
                raw = sub.get(DATETIME_ORIGINAL) or exif.get(DATETIME)
                if raw:
                    when = datetime.strptime(str(raw).strip("\x00 ")[:19], "%Y:%m:%d %H:%M:%S")
                    frac = str(sub.get(SUBSEC_ORIGINAL) or "").strip("\x00 ")
                    if frac.isdigit():
                        when = when.replace(microsecond=int(frac.ljust(6, "0")[:6]))
    except Exception:
        pass
    if len(_exif_cache) > 200_000:
        _exif_cache.clear()
    _exif_cache[key] = (camera, when)
    return camera, when


def _stem(filename: str) -> str:
    return os.path.splitext(os.path.basename(filename or ""))[0].lower()


def suggestions(session: Session, limit: int = 200) -> list[dict]:
    """Stacks worth making, never applied: [{kind, item_ids, cover_item_id,
    reason}]. Only active items outside any stack are considered."""
    items = list(session.execute(
        sa.select(Item).where(Item.deleted_at.is_(None), Item.stack_id.is_(None))
        .order_by(Item.id).limit(SUGGEST_MAX_ITEMS)
    ).scalars())
    out: list[dict] = []
    used: set[str] = set()

    # RAW + JPEG pairs
    by_stem: dict[str, list[Item]] = {}
    for i in items:
        if i.ext.lower() in RAW_EXTS or i.ext.lower() in PAIR_EXTS:
            by_stem.setdefault(_stem(i.filename), []).append(i)
    for stem, group in sorted(by_stem.items()):
        raws = [i for i in group if i.ext.lower() in RAW_EXTS]
        pairs = [i for i in group if i.ext.lower() in PAIR_EXTS]
        if not stem or not raws or not pairs:
            continue
        times = {capture_info(i)[1] for i in raws + pairs} - {None}
        if len(times) > 1:
            continue  # same name, different moments: a reused file name
        members_ = sorted(raws + pairs, key=lambda i: i.id)
        cover = next((i for i in pairs if i.preview_status == "ready"), pairs[0])
        out.append({"kind": "raw_jpeg", "item_ids": [i.id for i in members_],
                    "cover_item_id": cover.id,
                    "reason": f"RAW and {cover.ext.upper()} named {stem}"})
        used.update(i.id for i in members_)

    # bursts: same camera, within BURST_SECONDS
    shots = []
    for i in items:
        if i.id in used or not i.mime.startswith("image/"):
            continue
        camera, when = capture_info(i)
        if camera and when:
            shots.append((camera, when, i))
    shots.sort(key=lambda s: (s[0], s[1], s[2].id))
    run: list = []

    def flush():
        if len(run) >= 2:
            out.append({
                "kind": "burst", "item_ids": [s[2].id for s in run],
                "cover_item_id": max(run, key=lambda s: (s[2].rating, -len(s[2].id)))[2].id,
                "reason": f"{len(run)} shots from {run[0][0]} within "
                          f"{(run[-1][1] - run[0][1]).total_seconds():.1f} s",
            })
            used.update(s[2].id for s in run)

    for s in shots:
        if run and (s[0] != run[-1][0] or (s[1] - run[-1][1]).total_seconds() > BURST_SECONDS):
            flush()
            run = []
        run.append(s)
    flush()

    # version_of links
    free = {i.id for i in items} - used
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for src, dst in session.execute(
        sa.select(ItemLink.src_id, ItemLink.dst_id).where(ItemLink.kind == "version_of")
    ).all():
        if src in free and dst in free:
            parent[find(src)] = find(dst)
    groups: dict[str, list[str]] = {}
    for x in list(parent):
        groups.setdefault(find(x), []).append(x)
    for root, ids in sorted(groups.items()):
        if len(ids) >= 2:
            out.append({"kind": "version", "item_ids": sorted(ids), "cover_item_id": root,
                        "reason": f"{len(ids)} versions linked with version_of"})
    return out[:limit]
