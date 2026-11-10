import re
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import Folder, Item, ItemEvent, ItemTag, SmartFolder, Tag, TagFeedback
from . import validation


def normalize(name: str) -> str:
    """Whitespace collapsed; used to match names (e.g. for removal)."""
    return " ".join(str(name).split())[: validation.TAG_MAX]


def get_or_create(
    session: Session, names: list[str], *, validate: bool = True, resolve: bool | None = None
) -> list[Tag]:
    """The Tag rows for `names`, creating missing ones.

    Safe against a concurrent writer creating the same tag: the insert is an
    upsert, so neither side gets a unique-key error. With validate=False
    (rebuild-index restoring old data) names are only cleaned, not refused.
    resolve (default: same as validate) rewrites aliases to their tag
    (services/vocabulary.py); undo and rebuild pass False to put back
    exactly what was there.
    """
    from . import vocabulary

    if resolve is None:
        resolve = validate
    out: list[Tag] = []
    seen: set[str] = set()
    for raw in names:
        if validate:
            name = validation.tag_name(raw)
        else:
            name = normalize(raw)
            if not name:
                continue
        if resolve:
            name = vocabulary.resolve(session, name)
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        session.execute(
            pg_insert(Tag).values(name=name).on_conflict_do_nothing(index_elements=["name"])
        )
        out.append(session.execute(sa.select(Tag).where(Tag.name == name)).scalar_one())
    return out


def tag_dict(tag_id: int, name: str, count: int) -> dict:
    from .vocabulary import split_namespace

    ns, value = split_namespace(name)
    return {"id": tag_id, "name": name, "count": count, "namespace": ns, "value": value}


def list_with_counts(session: Session, namespace: str | None = None) -> list[dict]:
    """Every tag with its live-item count. namespace: only tags in it
    ("brand" for brand:*), or "none" for tags outside every namespace.

    The live count is every use of the tag minus its uses on trashed items
    (each item_tags row names an existing item): two small aggregates,
    instead of joining all of item_tags to items to drop the trashed 1 %
    (dev0, 250k items: 297 -> 77 ms)."""
    uses = (sa.select(ItemTag.tag_id, sa.func.count().label("n"))
            .group_by(ItemTag.tag_id).subquery("uses"))
    trashed = (sa.select(ItemTag.tag_id, sa.func.count().label("n"))
               .join(Item, Item.id == ItemTag.item_id).where(Item.deleted_at.isnot(None))
               .group_by(ItemTag.tag_id).subquery("trashed_uses"))
    count = (sa.func.coalesce(uses.c.n, 0) - sa.func.coalesce(trashed.c.n, 0)).label("count")
    rows = session.execute(
        sa.select(Tag.id, Tag.name, count)
        .outerjoin(uses, uses.c.tag_id == Tag.id)
        .outerjoin(trashed, trashed.c.tag_id == Tag.id)
        .order_by(sa.text("count DESC"), Tag.name)
    ).all()
    out = [tag_dict(r.id, r.name, r.count) for r in rows]
    if namespace is not None:
        want = None if namespace.lower() in ("", "none") else namespace.lower()
        out = [t for t in out if t["namespace"] == want]
    return out


def prune_unused(session: Session) -> list[str]:
    """Delete tags that no item uses, trashed items included (so a restore keeps
    its tags). Returns the deleted names, sorted."""
    used = sa.select(ItemTag.tag_id)
    names = session.execute(
        sa.select(Tag.name).where(Tag.id.not_in(used)).order_by(Tag.name)
    ).scalars().all()
    if names:
        session.execute(sa.delete(Tag).where(Tag.id.not_in(used)))
        session.commit()
    return list(names)


# ---------- merge and rename ----------


def _items_with(session: Session, names: list[str]) -> list[str]:
    """Ids of items (trashed ones too) carrying any of these tags."""
    if not names:
        return []
    return list(session.execute(
        sa.select(ItemTag.item_id).join(Tag, Tag.id == ItemTag.tag_id)
        .where(Tag.name.in_(names)).distinct().order_by(ItemTag.item_id)
    ).scalars())


def _smart_folders_naming(session: Session, names: list[str]) -> list[dict]:
    want = {n.lower() for n in names}
    out = []
    for s in session.execute(sa.select(SmartFolder).order_by(SmartFolder.name)).scalars():
        q = s.query or {}
        used = [t for k in ("tags", "tags_any") for t in (q.get(k) or [])
                if isinstance(t, str) and t.lower() in want]
        if used:
            out.append({"id": s.id, "name": s.name, "tags": used})
    return out


def merge(
    session: Session,
    from_names: list[str],
    into: str,
    *,
    dry_run: bool = False,
    keep_alias: bool = False,
) -> dict:
    """Move every item from the `from_names` tags to `into` (through its
    alias, if it is one), then point rules and smart folders at it and drop
    the old tags. Each item's change is a tag event in the caller's batch, so
    the whole merge undoes as one. dry_run counts what would change."""
    from ..services import events
    from . import folders as folders_svc
    from . import items as items_svc
    from . import vocabulary

    into = vocabulary.resolve(session, validation.tag_name(into))
    olds: list[str] = []
    for raw in from_names or []:
        name = validation.tag_name(raw)
        if name.lower() != into.lower() and name.lower() not in {o.lower() for o in olds}:
            olds.append(name)
    if not olds:
        raise validation.ValidationError("name at least one tag to merge that isn't the target")
    existing = {
        t.lower(): t for t in session.execute(sa.select(Tag.name).where(Tag.name.in_(olds)))
        .scalars()
    }
    item_ids = _items_with(session, olds)
    counts = {o: 0 for o in olds}
    for name, n in session.execute(
        sa.select(Tag.name, sa.func.count(ItemTag.item_id)).join(ItemTag, ItemTag.tag_id == Tag.id)
        .where(Tag.name.in_(olds)).group_by(Tag.name)
    ).all():
        for o in olds:
            if o.lower() == name.lower():
                counts[o] = n
    out = {
        "into": into,
        "from": olds,
        "items": len(item_ids),
        "counts": counts,
        "smart_folders": _smart_folders_naming(session, olds),
        "dry_run": dry_run,
        "batch_id": events.context().batch_id,
    }
    if dry_run:
        return out
    for item_id in item_ids:
        item = session.get(Item, item_id)
        have = [t.name for t in item.tags if t.name.lower() in {o.lower() for o in olds}]
        items_svc.set_tags(session, item_id, add=[into], remove=have, resolve=False)
    with vocabulary.recorded(session):  # in this batch: undo takes the alias back
        vocabulary.rename_in_rules(session, olds, into, keep_alias)
        vocabulary.rename_in_smart_folders(session, olds, into)
    session.execute(sa.delete(TagFeedback).where(TagFeedback.tag.in_(olds)))
    if existing:
        session.execute(sa.delete(Tag).where(
            Tag.name.in_(list(existing.values())),
            Tag.id.not_in(sa.select(ItemTag.tag_id)),
        ))
    session.commit()
    vocabulary.sync_file(session)
    folders_svc.snapshot(session)
    return out


def rename(session: Session, old: str, new: str, *, dry_run: bool = False) -> dict:
    """Rename a tag. Renaming onto an existing tag merges the two; a change
    of case only ("cpu" -> "CPU") renames in place."""
    from ..services import events
    from . import items as items_svc
    from . import meta as meta_svc
    from . import vocabulary

    old = validation.tag_name(old)
    new = validation.tag_name(new)
    row = session.execute(sa.select(Tag).where(Tag.name == old)).scalar_one_or_none()
    if row is None:
        raise validation.ValidationError(f"no tag named {old!r}")
    if old.lower() != new.lower():
        return {**merge(session, [row.name], new, dry_run=dry_run), "renamed": True}
    item_ids = _items_with(session, [row.name])
    out = {"into": new, "from": [row.name], "items": len(item_ids), "counts": {
        row.name: len(item_ids)}, "smart_folders": _smart_folders_naming(session, [row.name]),
        "dry_run": dry_run, "renamed": True, "batch_id": events.context().batch_id}
    if dry_run or row.name == new:
        return out
    befores = {}
    for item_id in item_ids:
        item = items_svc.get_item(session, item_id)
        befores[item_id] = events.state(item)
    previous = row.name
    row.name = new
    session.flush()
    for item_id in item_ids:
        item = session.get(Item, item_id)
        session.refresh(item, ["tags"])
        events.record_changes(session, item_id, befores[item_id], events.state(item))
        item.modified_at = datetime.now(UTC)
    with vocabulary.recorded(session):
        vocabulary.rename_in_rules(session, [previous], new, keep_alias=False)
        vocabulary.rename_in_smart_folders(session, [previous], new)
    session.commit()
    for item_id in item_ids:
        meta_svc.sync_item(session, item_id)
    vocabulary.sync_file(session)
    from . import folders as folders_svc

    folders_svc.snapshot(session)
    return out


# ---------- cleanup suggestions ----------

CLEANUP_MIN_LEN = 5
CLEANUP_MAX_TAGS = 5000


def _clash_key(name: str) -> str:
    return re.sub(r"[\s_\-.]+", "", name.lower())


def _stem(name: str) -> str:
    """A crude English stem: enough to pair Restaurant/Restaurants,
    Scent/Scents, Wedding/Weddings, Accessory/Accessories."""
    s = name.lower().strip()
    for suffix, repl in (("ies", "y"), ("sses", "ss"), ("es", ""), ("s", ""),
                         ("ing", ""), ("ed", "")):
        if s.endswith(suffix) and len(s) - len(suffix) >= 3:
            if suffix == "es" and not s.endswith(("ches", "shes", "xes", "zes", "sses")):
                continue
            return s[: -len(suffix)] + repl
    return s


def edit_distance(a: str, b: str, limit: int = 2) -> int:
    """Edit distance counting a swap of two neighbouring letters as one
    edit (Staitionery, Jewlery), or limit + 1 as soon as it must be bigger."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def _near_spelling(a: str, b: str) -> bool:
    """Whether two tag values look like one word misspelt: the same first
    letter (typos rarely hit it; Interiors/Exteriors are different words),
    and one edit apart, or two for words of 8+ letters (Shoes/Skies and
    Bags/Bugs are real words two edits apart, not typos)."""
    a, b = a.lower(), b.lower()
    if not a or not b or a[0] != b[0]:
        return False
    limit = 1 if min(len(a), len(b)) < 8 else 2
    return edit_distance(a, b, limit) <= limit


def levenshtein(a: str, b: str, limit: int = 2) -> int:
    """Edit distance, or limit + 1 as soon as it must be bigger."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            best = min(best, cur[j])
        if best > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def last_used(session: Session) -> dict[str, datetime]:
    """lower(tag) -> the last time an item carrying it was created or had
    its tags changed (item history), else that item's created_at."""
    tag = sa.func.jsonb_array_elements_text(ItemEvent.after["tags"]).table_valued("value")
    rows = session.execute(
        sa.select(sa.func.lower(tag.c.value), sa.func.max(ItemEvent.at))
        .select_from(ItemEvent).join(tag, sa.true())
        .where(ItemEvent.kind.in_(("create", "tags")))
        .group_by(sa.func.lower(tag.c.value))
    ).all()
    out = {k: v for k, v in rows}
    for name, at in session.execute(
        sa.select(Tag.name, sa.func.max(Item.created_at))
        .join(ItemTag, ItemTag.tag_id == Tag.id).join(Item, Item.id == ItemTag.item_id)
        .group_by(Tag.name)
    ).all():
        key = name.lower()
        if at is not None and (key not in out or out[key] < at):
            out[key] = at
    return out


def _merge_proposal(group: list[dict]) -> dict:
    """Merge the smaller tags into the most used one."""
    ordered = sorted(group, key=lambda t: (-t["count"], t["name"].lower()))
    return {"action": "merge", "into": ordered[0]["name"],
            "from": [t["name"] for t in ordered[1:]]}


def cleanup(session: Session, unused_since: datetime | None = None) -> list[dict]:
    """Suggestions for a tidier vocabulary, each {kind, tags, counts,
    proposal}. Kinds: one_off (used by one item; not bookkeeping tags like
    credit:), clash (same name but for case, spaces, hyphens or
    underscores), near (the same stem, or a likely misspelling within a
    namespace: same first letter, one edit apart, two for 8+ letters),
    unused (no item tagged since `unused_since`, default a year ago;
    includes tags with no live items; old bookkeeping tags stay),
    smart_folder (a smart folder names a tag or folder that doesn't exist)."""
    from . import vocabulary

    tags = list_with_counts(session)[:CLEANUP_MAX_TAGS]
    by_lower = {t["name"].lower(): t for t in tags}
    aliases, _ = vocabulary._maps(session)
    out: list[dict] = []

    def suggestion(kind, group, proposal):
        return {"kind": kind, "tags": [t["name"] for t in group],
                "counts": {t["name"]: t["count"] for t in group}, "proposal": proposal}

    # clashes: same key once case and separators are ignored
    clash: dict[str, list[dict]] = {}
    for t in tags:
        clash.setdefault(_clash_key(t["name"]), []).append(t)
    clashed: set[str] = set()
    for group in clash.values():
        if len(group) > 1:
            out.append(suggestion("clash", group, _merge_proposal(group)))
            clashed.update(t["name"].lower() for t in group)

    # near-misspellings and shared stems (within a namespace)
    paired: set[frozenset] = set()
    parent = {t["name"].lower(): t["name"].lower() for t in tags}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    stems: dict[tuple, list[dict]] = {}
    for t in tags:
        ns, value = vocabulary.split_namespace(t["name"])
        stems.setdefault((ns, _stem(value)), []).append(t)
    for group in stems.values():
        for t in group[1:]:
            parent[find(t["name"].lower())] = find(group[0]["name"].lower())
    # misspellings compare values within a namespace (format:reel and
    # format:gif share a prefix, not a spelling)
    split = {t["name"]: vocabulary.split_namespace(t["name"]) for t in tags}
    long = sorted((t for t in tags if len(split[t["name"]][1]) >= CLEANUP_MIN_LEN),
                  key=lambda t: len(split[t["name"]][1]))
    for i, a in enumerate(long):
        al = a["name"].lower()
        a_ns, av = split[a["name"]]
        for b in long[i + 1:]:
            bl = b["name"].lower()
            b_ns, bv = split[b["name"]]
            if len(bv) - len(av) > 2:
                break
            if a_ns != b_ns:
                continue
            if _clash_key(al) == _clash_key(bl):
                continue
            if any(c.isdigit() for c in al + bl) and re.sub(r"\d", "", al) == re.sub(r"\d", "", bl):
                continue  # demo1 / demo2, v1 / v2: numbered on purpose
            if _near_spelling(av, bv):
                parent[find(al)] = find(bl)
    groups: dict[str, list[dict]] = {}
    for t in tags:
        groups.setdefault(find(t["name"].lower()), []).append(t)
    for group in groups.values():
        names = frozenset(t["name"].lower() for t in group)
        if len(group) > 1 and not names <= clashed and names not in paired:
            paired.add(names)
            out.append(suggestion("near", group, _merge_proposal(group)))

    # one-offs: a tag on a single item. Bookkeeping tags (credit:,
    # license:, source: ...) record where one item came from, so one use is
    # normal for them, not clutter
    from .suggestions import BOOKKEEPING_NAMESPACES

    def bookkeeping(t: dict) -> bool:
        return (vocabulary.split_namespace(t["name"])[0] or "") in BOOKKEEPING_NAMESPACES

    for t in tags:
        if t["count"] == 1 and not bookkeeping(t):
            out.append(suggestion("one_off", [t], {"action": "review"}))

    # unused since a date (no live items counts as unused)
    since = unused_since or (datetime.now(UTC) - timedelta(days=365))
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    used = last_used(session)
    for t in tags:
        at = used.get(t["name"].lower())
        if t["count"] == 0 or ((at is None or at < since) and not bookkeeping(t)):
            s = suggestion("unused", [t], {"action": "delete" if t["count"] == 0 else "review"})
            s["last_used"] = at.isoformat() if at else None
            out.append(s)

    # smart folders naming tags or folders that don't exist
    folder_ids = set(session.execute(sa.select(Folder.id)).scalars())
    for s in session.execute(sa.select(SmartFolder).order_by(SmartFolder.name)).scalars():
        q = s.query or {}
        missing_tags = [
            x for k in ("tags", "tags_any") for x in (q.get(k) or [])
            if isinstance(x, str) and not vocabulary.is_wildcard(x)
            and x.lower() not in by_lower and x.lower() not in aliases
        ]
        folder = q.get("folder")
        missing_folder = folder if folder not in (None, "", "none") and folder not in folder_ids \
            else None
        if missing_tags or missing_folder:
            out.append({
                "kind": "smart_folder", "tags": missing_tags,
                "counts": {t: 0 for t in missing_tags},
                "proposal": {"action": "edit_smart_folder", "smart_folder_id": s.id,
                             "smart_folder": s.name, "missing_tags": missing_tags,
                             "missing_folder": missing_folder},
            })
    return out
