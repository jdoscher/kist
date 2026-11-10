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
"""

import json
import logging
import re
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, object_session

from ..config import get_settings
from ..models import SmartFolder, TagAlias, TagImplies
from . import meta as meta_svc
from . import validation

log = logging.getLogger("kist.vocabulary")

FILE = "vocabulary.json"
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
    session.execute(
        pg_insert(TagImplies).values(tag=tag, implied=implied).on_conflict_do_nothing()
    )
    session.commit()
    forget(session)
    sync_file(session)
    return {"tag": tag, "implied": implied}


def remove_implies(session: Session, tag: str, implied: str) -> bool:
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
