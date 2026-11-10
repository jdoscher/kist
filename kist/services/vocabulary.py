"""The tag vocabulary: aliases, implied tags and namespaces.

- An alias maps a variant to the tag it stands for ("Fashion" -> "Clothing").
  Every tag added through the API, MCP or an import is rewritten through the
  aliases on the way in, and adding an alias rewrites the items already
  carrying the variant (recorded as tag events, one batch, undoable). An
  alias has one target, and a target can't itself be an alias.
- An implication says a tag implies another ("Boots" -> "Shoes"). Implied
  tags are virtual: searching #shoes matches items tagged boots (search.py
  expands the tag filter at query time), and ItemOut.implied_tags lists
  them, but they are never written to items, so removing the rule cleans
  up everything. Loops are refused.
- A namespace is the part of a tag before its first colon ("brand:rolex"):
  parsed for display and GET /api/tags?namespace=, and `tags=brand:*`
  matches any tag in it.

library/vocabulary.json is the durable copy (rebuild-index restores it).

Every change to the rules (an alias added by a merge, a rule removed, the
smart folders a merge re-points) is a vocab event in the caller's batch
(`recorded`), so undoing the batch from Recent changes or MCP undo_batch
takes it back too, with the same "changed later" refusal as item undo.
library/vocabulary-events.jsonl is their durable copy.
"""

import contextlib
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import event as sa_event
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, object_session

from ..config import get_settings
from ..models import SmartFolder, TagAlias, TagImplies, VocabEvent
from . import events, validation
from . import meta as meta_svc

log = logging.getLogger("kist.vocabulary")

FILE = "vocabulary.json"
EVENTS_FILE = "vocabulary-events.jsonl"
KIND_RULES = "rules"  # the one vocab event kind: a before/after of what changed
VERSION = 1
NAMESPACE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]{0,31}):(\S.*)$")
_CACHE = "kist_vocabulary"


class VocabularyError(validation.ValidationError):
    """A rule that can't be added (an alias chain, an implication loop):
    HTTP 422."""


def split_namespace(name: str) -> tuple[str | None, str]:
    """'brand:Rolex' -> ('brand', 'Rolex'); 'Rolex' -> (None, 'Rolex').
    A URL-ish 'http://x' isn't a namespace (the value can't start with /)."""
    m = NAMESPACE.match(name or "")
    if not m or m.group(2).startswith("/"):
        return None, name
    return m.group(1).lower(), m.group(2)


def is_wildcard(name: str) -> str | None:
    """'brand:*' -> 'brand' (a whole-namespace tag filter), else None."""
    if name.endswith(":*") and NAMESPACE.match(name[:-1] + "x"):
        return name[:-2].lower()
    return None


# ---------- reading ----------


def _maps(session: Session) -> tuple[dict[str, str], dict[str, set[str]]]:
    """(alias -> tag, tag -> implied set), keys lower-case; cached on the
    session (vocabulary changes drop the cache)."""
    cached = session.info.get(_CACHE)
    if cached is not None:
        return cached
    aliases = {
        a.lower(): t for a, t in session.execute(sa.select(TagAlias.alias, TagAlias.tag)).all()
    }
    implies: dict[str, set[str]] = {}
    for t, i in session.execute(sa.select(TagImplies.tag, TagImplies.implied)).all():
        implies.setdefault(t.lower(), set()).add(i)
    session.info[_CACHE] = (aliases, implies)
    return aliases, implies


def forget(session: Session) -> None:
    session.info.pop(_CACHE, None)


def resolve(session: Session, name: str) -> str:
    """The canonical tag for a name (itself when it's no alias)."""
    aliases, _ = _maps(session)
    return aliases.get(name.lower(), name)


def implied_closure(session: Session, names) -> list[str]:
    """Every tag the given tags imply, directly or through others, minus
    the tags themselves; sorted."""
    _, implies = _maps(session)
    own = {n.lower() for n in names}
    seen: dict[str, str] = {}
    stack = list(own)
    while stack:
        for implied in implies.get(stack.pop(), ()):
            key = implied.lower()
            if key not in seen and key not in own:
                seen[key] = implied
                stack.append(key)
    return sorted(seen.values(), key=str.lower)


def implied_for_item(item) -> list[str]:
    session = object_session(item)
    if session is None or not item.tags:
        return []
    try:
        return implied_closure(session, [t.name for t in item.tags])
    except Exception:  # never fail an item read over the vocabulary
        log.exception("implied tags for %s", item.id)
        return []


def get(session: Session) -> dict:
    from . import tags as tags_svc

    aliases = session.execute(
        sa.select(TagAlias.alias, TagAlias.tag).order_by(TagAlias.tag, TagAlias.alias)
    ).all()
    implies = session.execute(
        sa.select(TagImplies.tag, TagImplies.implied).order_by(TagImplies.tag, TagImplies.implied)
    ).all()
    namespaces: dict[str, int] = {}
    for t in tags_svc.list_with_counts(session):
        ns = t["namespace"]
        if ns:
            namespaces[ns] = namespaces.get(ns, 0) + 1
    return {
        "aliases": [{"alias": a, "tag": t} for a, t in aliases],
        "implies": [{"tag": t, "implied": i} for t, i in implies],
        "namespaces": [{"namespace": n, "tags": c} for n, c in sorted(namespaces.items())],
    }


# ---------- query-time expansion (search.py's tag filter) ----------

_cte_seq = 0


def matching_names(name: str):
    """A SELECT of every tag name that satisfies a filter on `name`: the
    name (through its alias, if it is one) plus every tag that implies it,
    at any depth. A recursive CTE, so it costs one query however deep the
    rules go, and a loop (which can't be added) couldn't hang it."""
    global _cte_seq
    _cte_seq = (_cte_seq + 1) % 1_000_000
    start = sa.cast(sa.literal(name), CITEXT)
    canonical = sa.func.coalesce(
        sa.select(TagAlias.tag).where(TagAlias.alias == start).scalar_subquery(), start
    )
    base = sa.select(canonical.label("name")).cte(f"tag_closure_{_cte_seq}", recursive=True)
    step = sa.select(TagImplies.tag).where(TagImplies.implied == base.c.name)
    closure = base.union(step)
    return sa.select(closure.c.name)


# ---------- changing the rules ----------


def _name(value: str, what: str) -> str:
    try:
        return validation.tag_name(value)
    except validation.ValidationError as exc:
        raise VocabularyError(f"{what}: {exc}") from None


def add_alias(session: Session, alias: str, tag: str) -> dict:
    """alias -> tag, then rewrite every item carrying the alias (one batch:
    the request's) and every rule and smart folder naming it."""
    from . import tags as tags_svc

    alias = _name(alias, "alias")
    tag = _name(tag, "tag")
    if alias.lower() == tag.lower():
        raise VocabularyError("an alias can't point at itself")
    aliases, _ = _maps(session)
    if tag.lower() in aliases:
        raise VocabularyError(
            f"{tag!r} is itself an alias of {aliases[tag.lower()]!r}; "
            f"point {alias!r} at {aliases[tag.lower()]!r} instead"
        )
    current = aliases.get(alias.lower())
    if current is not None and current.lower() != tag.lower():
        raise VocabularyError(
            f"{alias!r} is already an alias of {current!r}; remove that alias first"
        )
    pointing = sorted(a for a, t in aliases.items() if t.lower() == alias.lower())
    if pointing:
        raise VocabularyError(
            f"other aliases point at {alias!r} ({', '.join(pointing)}); "
            f"merge {alias!r} into {tag!r} instead (POST /api/tags/merge with keep_alias)"
        )
    result = tags_svc.merge(session, [alias], tag, keep_alias=True)
    return {"alias": alias, "tag": result["into"], "items": result["items"]}


def remove_alias(session: Session, alias: str) -> bool:
    with recorded(session):
        n = session.execute(sa.delete(TagAlias).where(TagAlias.alias == alias)).rowcount
    session.commit()
    forget(session)
    if n:
        sync_file(session)
    return bool(n)


def _reaches(implies: dict[str, set[str]], start: str, goal: str) -> bool:
    stack, seen = [start.lower()], set()
    while stack:
        cur = stack.pop()
        if cur == goal.lower():
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(i.lower() for i in implies.get(cur, ()))
    return False


def add_implies(session: Session, tag: str, implied: str) -> dict:
    """tag implies implied. Both go through the aliases first. A rule that
    would close a loop (shoes -> boots when boots -> shoes) is refused."""
    tag = resolve(session, _name(tag, "tag"))
    implied = resolve(session, _name(implied, "implied"))
    if tag.lower() == implied.lower():
        raise VocabularyError("a tag can't imply itself")
    _, implies = _maps(session)
    if _reaches(implies, implied, tag):
        raise VocabularyError(
            f"{implied!r} already implies {tag!r} (directly or through other tags); "
            "this rule would make a loop"
        )
    with recorded(session):
        session.execute(
            pg_insert(TagImplies).values(tag=tag, implied=implied).on_conflict_do_nothing()
        )
    session.commit()
    forget(session)
    sync_file(session)
    return {"tag": tag, "implied": implied}


def remove_implies(session: Session, tag: str, implied: str) -> bool:
    with recorded(session):
        n = session.execute(
            sa.delete(TagImplies).where(TagImplies.tag == tag, TagImplies.implied == implied)
        ).rowcount
    session.commit()
    forget(session)
    if n:
        sync_file(session)
    return bool(n)


def rename_in_rules(session: Session, old_names: list[str], new: str, keep_alias: bool) -> None:
    """After tags were merged into `new`: rules naming the old tags name the
    new one (and, with keep_alias, the old names become its aliases).
    Doesn't commit."""
    olds = [o for o in old_names if o.lower() != new.lower()]
    if not olds:
        return
    old_set = {o.lower() for o in olds}
    session.execute(sa.update(TagAlias).where(TagAlias.tag.in_(olds)).values(tag=new))
    session.execute(sa.delete(TagAlias).where(TagAlias.alias == sa.cast(new, CITEXT)))
    rows = session.execute(sa.select(TagImplies.tag, TagImplies.implied)).all()
    rewritten = set()
    for t, i in rows:
        nt = new if t.lower() in old_set else t
        ni = new if i.lower() in old_set else i
        if (nt, ni) != (t, i):
            session.execute(
                sa.delete(TagImplies).where(TagImplies.tag == t, TagImplies.implied == i)
            )
            if nt.lower() != ni.lower():
                rewritten.add((nt, ni))
    for nt, ni in sorted(rewritten):
        session.execute(
            pg_insert(TagImplies).values(tag=nt, implied=ni).on_conflict_do_nothing()
        )
    if keep_alias:
        for o in olds:
            session.execute(
                pg_insert(TagAlias).values(alias=o, tag=new)
                .on_conflict_do_update(index_elements=["alias"], set_={"tag": new})
            )
    forget(session)


def rename_in_smart_folders(session: Session, old_names: list[str], new: str) -> int:
    """Smart folders filtering on the old tags filter on `new`. Doesn't commit."""
    old_set = {o.lower() for o in old_names}
    changed = 0
    for s in session.execute(sa.select(SmartFolder)).scalars():
        q = dict(s.query or {})
        touched = False
        for key in ("tags", "tags_any"):
            vals = q.get(key)
            if not isinstance(vals, list):
                continue
            out: list[str] = []
            for v in vals:
                v2 = new if str(v).lower() in old_set else v
                if str(v2).lower() not in {x.lower() for x in out}:
                    out.append(v2)
                touched = touched or v2 != v
            q[key] = out
        if touched:
            s.query = q
            changed += 1
    return changed


# ---------- history (vocab events, batched with item events) ----------


def _state(session: Session) -> dict:
    """Every rule and smart-folder filter, keyed for diffing."""
    aliases = {a: t for a, t in session.execute(sa.select(TagAlias.alias, TagAlias.tag)).all()}
    implies = {(t, i) for t, i in session.execute(sa.select(TagImplies.tag, TagImplies.implied))}
    smart = {sid: q for sid, q in session.execute(sa.select(SmartFolder.id, SmartFolder.query))}
    return {"aliases": aliases, "implies": implies, "smart": smart}


def _diff(old: dict, new: dict) -> tuple[dict, dict] | None:
    """(before, after) of what changed between two _state()s, or None.
    aliases: {alias: tag or null}; implies: the pairs only one side has;
    smart_folders: {id: query} of re-pointed filters (gone ones left out)."""
    lo = {a.lower(): (a, t) for a, t in old["aliases"].items()}
    ln = {a.lower(): (a, t) for a, t in new["aliases"].items()}
    before: dict = {"aliases": {}, "implies": [], "smart_folders": {}}
    after: dict = {"aliases": {}, "implies": [], "smart_folders": {}}
    for key in sorted(lo.keys() | ln.keys()):
        o, n = lo.get(key), ln.get(key)
        if o != n:
            name = (o or n)[0]
            before["aliases"][name] = o[1] if o else None
            after["aliases"][name] = n[1] if n else None
    before["implies"] = sorted([t, i] for t, i in old["implies"] - new["implies"])
    after["implies"] = sorted([t, i] for t, i in new["implies"] - old["implies"])
    for sid in sorted(old["smart"].keys() & new["smart"].keys()):
        if old["smart"][sid] != new["smart"][sid]:
            before["smart_folders"][sid] = old["smart"][sid]
            after["smart_folders"][sid] = new["smart"][sid]
    if not any(before.values()) and not any(after.values()):
        return None
    return before, after


def record(session: Session, before: dict, after: dict) -> VocabEvent:
    """A vocab event in the current batch (written to disk on commit)."""
    ctx = events.context()
    ev = VocabEvent(at=datetime.now(UTC), actor=ctx.actor, source=ctx.source,
                    batch_id=ctx.batch_id, kind=KIND_RULES, before=before, after=after)
    session.add(ev)
    session.info.setdefault(_PENDING, []).append(ev)
    return ev


@contextlib.contextmanager
def recorded(session: Session):
    """Record whatever the block changes in the rules (and smart-folder
    filters) as one vocab event in the caller's batch. Doesn't commit."""
    session.flush()
    old = _state(session)
    yield
    session.flush()
    change = _diff(old, _state(session))
    if change:
        record(session, *change)
    forget(session)


def event_keys(before, after) -> set[str]:
    """What a vocab event touched, for conflict checks."""
    keys: set[str] = set()
    for part in (before or {}, after or {}):
        keys |= {f"alias:{a.lower()}" for a in part.get("aliases") or {}}
        keys |= {f"implies:{t.lower()}>{i.lower()}" for t, i in part.get("implies") or []}
        keys |= {f"smart_folder:{sid}" for sid in part.get("smart_folders") or {}}
    return keys


def _drift(session: Session, after: dict) -> str | None:
    """Why the rules no longer look like `after` (changed outside history,
    e.g. before history existed), or None."""
    now = _state(session)
    aliases = {a.lower(): t.lower() for a, t in now["aliases"].items()}
    for a, t in (after.get("aliases") or {}).items():
        if aliases.get(a.lower()) != (t.lower() if t else None):
            return f"changed later: alias:{a}"
    have = {(t.lower(), i.lower()) for t, i in now["implies"]}
    for t, i in after.get("implies") or []:
        if (t.lower(), i.lower()) not in have:
            return f"changed later: implies:{t}>{i}"
    for sid, q in (after.get("smart_folders") or {}).items():
        if sid in now["smart"] and now["smart"][sid] != q:
            return f"changed later: smart_folder:{sid}"
    return None


def revert_event(session: Session, ev: VocabEvent) -> None:
    """Put the rules back to ev.before (recorded as a new vocab event in the
    current batch). Doesn't check conflicts; _VocabUndo does."""
    before, after = ev.before or {}, ev.after or {}
    with recorded(session):
        for alias, tag in (before.get("aliases") or {}).items():
            session.execute(sa.delete(TagAlias).where(TagAlias.alias == alias))
            if tag:
                session.execute(pg_insert(TagAlias).values(alias=alias, tag=tag))
        for t, i in after.get("implies") or []:
            session.execute(sa.delete(TagImplies).where(TagImplies.tag == t,
                                                        TagImplies.implied == i))
        for t, i in before.get("implies") or []:
            session.execute(
                pg_insert(TagImplies).values(tag=t, implied=i).on_conflict_do_nothing())
        for sid, q in (before.get("smart_folders") or {}).items():
            row = session.get(SmartFolder, sid)
            if row is not None:
                row.query = q
    session.commit()
    sync_file(session)
    if before.get("smart_folders"):
        from . import folders as folders_svc

        folders_svc.snapshot(session)


class _VocabUndo(events.UndoSource):
    def find(self, session: Session, batch_id: str) -> list:
        return list(session.execute(
            sa.select(VocabEvent).where(VocabEvent.batch_id == batch_id)
        ).scalars())

    def batches_select(self):
        return sa.select(VocabEvent.batch_id, VocabEvent.actor, VocabEvent.source,
                         VocabEvent.at, sa.literal("vocab_").concat(VocabEvent.kind))

    def revert(self, session: Session, evs: list, exclude: set[str]) -> tuple[int, list[dict]]:
        undone = 0
        skipped: list[dict] = []
        for ev in sorted(evs, key=lambda e: e.id, reverse=True):
            keys = event_keys(ev.before, ev.after)
            later = session.execute(
                sa.select(VocabEvent).where(VocabEvent.id > ev.id,
                                            VocabEvent.batch_id.not_in(sorted(exclude)))
            ).scalars().all()
            touched = set().union(*(event_keys(e.before, e.after) for e in later)) & keys
            reason = (f"changed later: {', '.join(sorted(touched))}" if touched
                      else _drift(session, ev.after or {}))
            if reason:
                skipped.append({"item_id": "vocabulary", "event_id": ev.id,
                                "reason": reason, "target": "vocabulary"})
                continue
            try:
                revert_event(session, ev)
            except Exception as exc:  # a rule that can't go back (e.g. a clash)
                session.rollback()
                forget(session)
                log.warning("vocab event %s not undone: %s", ev.id, exc)
                skipped.append({"item_id": "vocabulary", "event_id": ev.id,
                                "reason": f"the tag rules couldn't be put back: {exc}",
                                "target": "vocabulary"})
                continue
            undone += 1
        return undone, skipped


events.UNDO_SOURCES.append(_VocabUndo())


# ---------- vocab events on disk ----------

_PENDING = "kist_vocab_events_pending"
_LINES = "kist_vocab_event_lines"


def event_line(ev: VocabEvent) -> dict:
    return {"id": ev.id, "at": ev.at.isoformat() if ev.at else None, "actor": ev.actor,
            "source": ev.source, "batch_id": ev.batch_id, "kind": ev.kind,
            "before": ev.before, "after": ev.after}


@sa_event.listens_for(Session, "after_flush")
def _collect(session, flush_context):
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


def events_path() -> Path:
    return get_settings().library_dir / EVENTS_FILE


def write_lines(lines: list[dict]) -> None:
    p = events_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write("".join(json.dumps(x, ensure_ascii=False, default=str) + "\n"
                            for x in lines))
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        log.exception("vocabulary events not written (kist rewrite-meta writes them)")
        meta_svc.note_pending(EVENTS_FILE)


def rewrite_events_file(session: Session) -> bool:
    """rewrite-meta: vocabulary-events.jsonl from the database."""
    rows = session.execute(sa.select(VocabEvent).order_by(VocabEvent.id)).scalars().all()
    if not rows and not events_path().exists():
        return True
    try:
        meta_svc.atomic_write_text(events_path(), "".join(
            json.dumps(event_line(e), ensure_ascii=False, default=str) + "\n" for e in rows))
        return True
    except OSError:
        log.exception("vocabulary-events.jsonl not written")
        return False


def restore_events(session: Session) -> tuple[int, int]:
    """rebuild-index: vocab events from vocabulary-events.jsonl the database
    lacks, so a merge stays undoable. Returns (restored, unreadable lines)."""
    lines, bad = events.read_file_lines(events_path())
    rows = []
    for line in lines:
        try:
            rows.append({
                "id": int(line["id"]),
                "at": datetime.fromisoformat(line["at"]) if line.get("at") else datetime.now(UTC),
                "actor": str(line.get("actor") or "unknown"),
                "source": str(line.get("source") or "api"),
                "batch_id": str(line.get("batch_id") or line["id"]),
                "kind": str(line.get("kind") or KIND_RULES),
                "before": line.get("before"), "after": line.get("after"),
            })
        except (KeyError, TypeError, ValueError):
            bad += 1
    restored = 0
    if rows:
        restored = session.execute(
            pg_insert(VocabEvent).values(rows).on_conflict_do_nothing(index_elements=["id"])
        ).rowcount or 0
    session.execute(sa.text(
        "SELECT setval(pg_get_serial_sequence('vocab_events', 'id'), "
        "GREATEST((SELECT COALESCE(MAX(id), 0) FROM vocab_events), 1), "
        "(SELECT COUNT(*) > 0 FROM vocab_events))"
    ))
    session.commit()
    return restored, bad


def check_events_file(session: Session) -> list[str]:
    """verify: vocabulary-events.jsonl has every vocab event."""
    n = session.execute(sa.select(sa.func.count()).select_from(VocabEvent)).scalar_one()
    if not n:
        return []
    lines, bad = events.read_file_lines(events_path())
    have = len({line["id"] for line in lines})
    if have < n or bad:
        return [f"{EVENTS_FILE} has {have} of {n} vocabulary events"
                + (f" and {bad} unreadable lines" if bad else "")]
    return []


# ---------- the durable copy ----------


def path():
    return get_settings().library_dir / FILE


def snapshot(session: Session) -> dict:
    aliases = session.execute(
        sa.select(TagAlias.alias, TagAlias.tag).order_by(sa.func.lower(TagAlias.alias))
    ).all()
    implies = session.execute(
        sa.select(TagImplies.tag, TagImplies.implied)
        .order_by(sa.func.lower(TagImplies.tag), sa.func.lower(TagImplies.implied))
    ).all()
    return {
        "version": VERSION,
        "aliases": {a: t for a, t in aliases},
        "implies": [[t, i] for t, i in implies],
    }


def sync_file(session: Session) -> bool:
    """Bring vocabulary.json up to the committed rules. Never raises."""
    try:
        meta_svc.advisory_lock(session, FILE)
        data = snapshot(session)
        data["written_at"] = datetime.now(UTC).isoformat()
        meta_svc.atomic_write_text(path(), json.dumps(data, indent=2, ensure_ascii=False))
        session.commit()
        return True
    except Exception:
        session.rollback()
        log.exception("vocabulary.json not written (kist rewrite-meta writes it)")
        meta_svc.note_pending(FILE)
        return False


def restore(session: Session) -> tuple[int, str | None]:
    """rebuild-index: rules from vocabulary.json the database lacks.
    Returns (rules restored, problem)."""
    p = path()
    if not p.is_file():
        return 0, None
    try:
        data = meta_svc.read_json(p)
        aliases = data.get("aliases") or {}
        implies = data.get("implies") or []
        if not isinstance(aliases, dict) or not isinstance(implies, list):
            raise ValueError("aliases must be an object and implies a list")
    except (OSError, ValueError, AttributeError) as exc:
        return 0, str(exc)
    n = 0
    for alias, tag in aliases.items():
        if not isinstance(tag, str) or not alias.strip() or alias.lower() == tag.lower():
            continue
        n += session.execute(
            pg_insert(TagAlias).values(alias=alias, tag=tag).on_conflict_do_nothing()
        ).rowcount or 0
    for pair in implies:
        if (not isinstance(pair, list) or len(pair) != 2
                or not all(isinstance(x, str) for x in pair) or pair[0].lower() == pair[1].lower()):
            continue
        n += session.execute(
            pg_insert(TagImplies).values(tag=pair[0], implied=pair[1]).on_conflict_do_nothing()
        ).rowcount or 0
    session.commit()
    forget(session)
    return n, None


def check_file(session: Session) -> list[str]:
    """verify: vocabulary.json matches the tables."""
    data = snapshot(session)
    p = path()
    if not p.is_file():
        return ["vocabulary.json missing"] if data["aliases"] or data["implies"] else []
    try:
        disk = meta_svc.read_json(p)
    except (OSError, ValueError):
        return ["vocabulary.json unreadable"]
    problems = []
    disk_aliases = {k.lower(): str(v).lower() for k, v in (disk.get("aliases") or {}).items()}
    db_aliases = {k.lower(): v.lower() for k, v in data["aliases"].items()}
    if disk_aliases != db_aliases:
        problems.append("vocabulary.json: aliases differ from the database")
    disk_imp = {(a.lower(), b.lower()) for a, b in (disk.get("implies") or [])}
    db_imp = {(a.lower(), b.lower()) for a, b in data["implies"]}
    if disk_imp != db_imp:
        problems.append("vocabulary.json: implied tags differ from the database")
    return problems
