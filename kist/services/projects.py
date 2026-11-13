"""Projects, pinned sidebar entries and per-project custom fields (Kist 2.0 F6).

Projects
--------
A project is a light record for one job: name, client, job number, phase,
status (active | archived) and notes. It groups things that already exist
BY REFERENCE: folders, smart folders, boards, share links and (once Present
lands) shows. A member is {kind, id}; the kinds are a registry
(`MEMBER_KINDS`), so another feature adds one with `register_member_kind`
(Present: "show") without touching this module. A member whose target is
gone (a folder deleted later) stays listed as missing until it is removed.

The project's items are the items filed in its folders (subfolders
included): the `project=` filter, the project page's recent items and
where its custom fields apply. Smart folders, boards and shares are listed
on the project page but don't widen that set.

Pins
----
A global list (the owner's sidebar) of {kind, ref}: a project, folder,
smart folder, board, person, tag (ref = the tag name) or a saved search
(ref = an id, plus a name and the search as URL params). Reading drops
pins whose target is gone and projects that are archived (unarchiving
brings the pin back). A folder-limited read token sees only its folders'
and visible boards' pins.

Custom fields
-------------
A project defines fields: text, number, date (YYYY-MM-DD), choice (one of
a list) or url. They apply to items in the project's folders. Values live
on the item (items.custom_fields, {field id: value}) and in its meta.json,
so rebuild-index restores them. Setting values is an item event of kind
"fields" (undoable through batches like any edit). Deleting a field or a
project clears its values from the items, as events in the same batch, so
nothing invisible is left behind. A field's type is fixed once made; to
change it, make a new field.

Filters: `field.<key>=<expr>` (search.build_filters custom_fields={key:
expr}). The key is the field name in lower case with non-letters as "_"
(a field id works too); a key shared by several projects matches any of
them. expr: `*` any value, `none` no value; text/url: a case-insensitive
substring; choice: one value or several separated by "|"; number and date:
an exact value, `>=x`, `<=x`, `>x`, `<x` or `a..b` (inclusive). A key no
project has matches nothing (a saved search keeps working, empty).

Durability: everything but the values lives in library/.kist/projects.json
(atomic write under an advisory lock); rebuild-index has nothing to
restore from the database, and verify checks the file.
"""

import json
import math
import re
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .. import access
from ..config import get_settings
from ..ids import new_ulid
from ..models import Board, Folder, Item, ItemEvent, ItemFolder, Person, Share, SmartFolder, Tag
from . import events, validation
from . import meta as meta_svc

FILE = "projects.json"
VERSION = 1
STATUSES = ("active", "archived")
FIELD_TYPES = ("text", "number", "date", "choice", "url")
PIN_KINDS = ("project", "folder", "smart", "board", "person", "tag", "search")
PROJECTS_MAX = 1000
MEMBERS_MAX = 500
FIELDS_MAX = 50
CHOICES_MAX = 200
PINS_MAX = 100
TEXT_MAX = 200  # name, client, job number, phase
NOTES_MAX = 20_000
VALUE_MAX = 2000
SEARCH_MAX = 2000
EVENT_KIND = "fields"
_KEY_RE = re.compile(r"[^a-z0-9]+")
_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")


class NotFound(Exception):
    """No project / field / pin with that id (HTTP 404)."""


# ---------- the file ----------

_cache: dict[str, Any] = {"key": None, "data": None}
_cache_lock = threading.Lock()


def path():
    return get_settings().state_dir / FILE


def _empty() -> dict:
    return {"version": VERSION, "projects": [], "pins": []}


def load() -> dict:
    """The parsed file ({version, projects, pins}); empty when absent.
    Raises ValueError when it is unreadable."""
    p = path()
    try:
        st = p.stat()
    except FileNotFoundError:
        return _empty()
    key = (str(p), st.st_mtime_ns, st.st_size)
    with _cache_lock:
        if _cache["key"] == key:
            return json.loads(json.dumps(_cache["data"]))  # a private copy
    data = meta_svc.read_json(p)
    if not isinstance(data, dict) or not isinstance(data.get("projects", []), list) \
            or not isinstance(data.get("pins", []), list):
        raise ValueError("projects.json has no projects/pins lists")
    data.setdefault("projects", [])
    data.setdefault("pins", [])
    with _cache_lock:
        _cache["key"], _cache["data"] = key, data
    return json.loads(json.dumps(data))


def _save(data: dict) -> None:
    data["version"] = VERSION
    meta_svc.atomic_write_text(path(), json.dumps(data, indent=2, ensure_ascii=False))


def _locked(session: Session) -> dict:
    """Take the file's lock (until the transaction ends) and read it."""
    meta_svc.advisory_lock(session, FILE)
    return load()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _find(data: dict, project_id: str) -> dict:
    for p in data["projects"]:
        if p.get("id") == project_id:
            return p
    raise NotFound(f"project {project_id} not found")


# ---------- member kinds (a registry, so Present can add "show") ----------

# kind -> fn(session, ids) -> {id: {"name": str, ...extra}} for the ids that exist
Resolver = Callable[[Session, list[str]], dict[str, dict]]
MEMBER_KINDS: dict[str, Resolver] = {}


def register_member_kind(kind: str, resolver: Resolver) -> None:
    """Let projects hold another kind of thing (F2 Present: "show")."""
    MEMBER_KINDS[kind] = resolver


def _folders(session: Session, ids: list[str]) -> dict[str, dict]:
    rows = session.execute(sa.select(Folder.id, Folder.name).where(Folder.id.in_(ids))).all()
    return {r.id: {"name": r.name} for r in rows}


def _smart(session: Session, ids: list[str]) -> dict[str, dict]:
    rows = session.execute(
        sa.select(SmartFolder.id, SmartFolder.name, SmartFolder.query)
        .where(SmartFolder.id.in_(ids))).all()
    return {r.id: {"name": r.name, "query": r.query or {}} for r in rows}


def _boards(session: Session, ids: list[str]) -> dict[str, dict]:
    from . import boards as boards_svc

    rows = session.execute(sa.select(Board).where(Board.id.in_(ids))).scalars().all()
    return {b.id: {"name": b.name, "trashed": b.deleted_at is not None}
            for b in rows if boards_svc.visible(session, b)}


def _shares(session: Session, ids: list[str]) -> dict[str, dict]:
    rows = session.execute(sa.select(Share).where(Share.id.in_(ids))).scalars().all()
    now = datetime.now(UTC)
    out = {}
    for s in rows:
        live = s.revoked_at is None and (s.expires_at is None or s.expires_at > now)
        out[s.id] = {"name": s.title or f"Share of a {s.target_kind}", "url": f"/s/{s.slug}",
                     "live": live, "target_kind": s.target_kind, "target_id": s.target_id}
    return out


register_member_kind("folder", _folders)
register_member_kind("smart", _smart)
register_member_kind("board", _boards)
register_member_kind("share", _shares)


def _shows(session: Session, ids: list[str]) -> dict[str, dict]:
    """F2 Present's saved shows (library/.kist/shows.json), the ones this
    token may see."""
    from . import present as present_svc

    try:
        shows = present_svc.list_shows(session)
    except validation.ValidationError:  # an unreadable shows.json: verify says so
        return {}
    want = set(ids)
    return {s["id"]: {"name": s.get("name") or "Show", "web_url": f"/?present={s['id']}",
                      "source_kind": (s.get("source") or {}).get("kind")}
            for s in shows if s.get("id") in want}


register_member_kind("show", _shows)  # F2 Present (integration)


# ---------- cleaning input ----------


def _text(value, what: str, limit: int = TEXT_MAX, required: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise validation.ValidationError(f"a project needs a {what}")
    if len(text) > limit:
        raise validation.ValidationError(f"{what} is at most {limit} characters")
    return text


def _notes(value) -> str:
    text = str(value or "").strip()
    if len(text) > NOTES_MAX:
        raise validation.ValidationError(f"notes are at most {NOTES_MAX} characters")
    return text


def _status(value) -> str:
    if value not in STATUSES:
        raise validation.ValidationError("status is active or archived")
    return value


def field_key(name: str) -> str:
    """The filter key of a field name: 'Look number' -> 'look_number'."""
    return _KEY_RE.sub("_", str(name).lower()).strip("_")[:40]


# ---------- projects ----------

PROJECT_KEYS = ("name", "client", "job_number", "phase", "notes", "status")


def _clean_project(body: dict, *, partial: bool) -> dict:
    out: dict = {}
    for key in PROJECT_KEYS:
        if key not in body or (partial and body[key] is None):
            continue
        value = body[key]
        if key == "name":
            out[key] = _text(value, "name", required=True)
        elif key == "notes":
            out[key] = _notes(value)
        elif key == "status":
            out[key] = _status(value)
        else:
            out[key] = _text(value, key.replace("_", " "))
    if not partial and "name" not in out:
        raise validation.ValidationError("a project needs a name")
    return out


def list_projects(session: Session, status: str = "active") -> list[dict]:
    """Projects (newest change first), each with member and field counts."""
    if status not in (*STATUSES, "all"):
        raise validation.ValidationError("status is active, archived or all")
    _hide_from_restricted()
    rows = [p for p in load()["projects"] if status == "all" or p.get("status") == status]
    rows.sort(key=lambda p: p.get("modified_at") or "", reverse=True)
    return [summary(p) for p in rows]


def summary(p: dict) -> dict:
    out = {k: p.get(k, "") for k in ("id", *PROJECT_KEYS, "created_at", "modified_at")}
    out["status"] = p.get("status") or "active"
    counts: dict[str, int] = {}
    for m in p.get("members") or []:
        counts[m["kind"]] = counts.get(m["kind"], 0) + 1
    out["member_counts"] = counts
    out["fields"] = list(p.get("fields") or [])
    return out


def get(session: Session, project_id: str) -> dict:
    """A project with its members resolved: {kind, id, name, missing, ...}."""
    _hide_from_restricted()
    p = _find(load(), project_id)
    out = summary(p)
    out["members"] = resolve_members(session, p.get("members") or [])
    return out


def resolve_members(session: Session, members: list[dict]) -> list[dict]:
    by_kind: dict[str, list[str]] = {}
    for m in members:
        by_kind.setdefault(m["kind"], []).append(m["id"])
    found: dict[str, dict[str, dict]] = {}
    for kind, ids in by_kind.items():
        resolver = MEMBER_KINDS.get(kind)
        found[kind] = resolver(session, ids) if resolver else {}
    out = []
    for m in members:
        info = found.get(m["kind"], {}).get(m["id"])
        row = {"kind": m["kind"], "id": m["id"], "missing": info is None,
               "name": (info or {}).get("name") or ""}
        row.update({k: v for k, v in (info or {}).items() if k != "name"})
        out.append(row)
    return out


def create(session: Session, body: dict) -> dict:
    access.require(access.Action.EDIT)
    clean = _clean_project(body, partial=False)
    now = _now()
    p = {"id": new_ulid(), "name": clean["name"], "client": clean.get("client", ""),
         "job_number": clean.get("job_number", ""), "phase": clean.get("phase", ""),
         "notes": clean.get("notes", ""), "status": clean.get("status", "active"),
         "created_at": now, "modified_at": now, "members": [], "fields": []}
    data = _locked(session)
    if len(data["projects"]) >= PROJECTS_MAX:
        session.rollback()
        raise validation.ValidationError(f"at most {PROJECTS_MAX} projects")
    data["projects"].append(p)
    _save(data)
    session.commit()
    return get(session, p["id"])


def update(session: Session, project_id: str, body: dict) -> dict:
    access.require(access.Action.EDIT)
    clean = _clean_project(body, partial=True)
    data = _locked(session)
    p = _find_or_rollback(session, data, project_id)
    p.update(clean)
    p["modified_at"] = _now()
    _save(data)
    session.commit()
    return get(session, project_id)


def delete(session: Session, project_id: str) -> dict:
    """Remove the project and its pins; its fields' values leave the items
    (item events in this batch)."""
    access.require(access.Action.EDIT)
    data = _locked(session)
    p = _find_or_rollback(session, data, project_id)
    field_ids = [f["id"] for f in p.get("fields") or []]
    data["projects"] = [x for x in data["projects"] if x.get("id") != project_id]
    data["pins"] = [x for x in data["pins"]
                    if not (x.get("kind") == "project" and x.get("ref") == project_id)]
    _save(data)
    session.commit()
    cleared = _clear_values(session, field_ids)
    return {"deleted": project_id, "values_cleared": cleared}


def _find_or_rollback(session: Session, data: dict, project_id: str) -> dict:
    try:
        return _find(data, project_id)
    except NotFound:
        session.rollback()
        raise


# ---------- members ----------


def add_member(session: Session, project_id: str, kind: str, member_id: str) -> dict:
    access.require(access.Action.EDIT)
    if kind not in MEMBER_KINDS:
        raise validation.ValidationError(
            f"unknown kind {kind!r}; expected one of {', '.join(sorted(MEMBER_KINDS))}")
    member_id = str(member_id or "")
    if not _ID_RE.match(member_id) or not MEMBER_KINDS[kind](session, [member_id]):
        raise validation.ValidationError(f"no {kind} {member_id!r}")
    data = _locked(session)
    p = _find_or_rollback(session, data, project_id)
    members = p.setdefault("members", [])
    if not any(m["kind"] == kind and m["id"] == member_id for m in members):
        if len(members) >= MEMBERS_MAX:
            session.rollback()
            raise validation.ValidationError(f"a project holds at most {MEMBERS_MAX} things")
        members.append({"kind": kind, "id": member_id})
        p["modified_at"] = _now()
        _save(data)
    session.commit()
    return get(session, project_id)


def remove_member(session: Session, project_id: str, kind: str, member_id: str) -> dict:
    """Removing a member that isn't there is a no-op (retries stay safe)."""
    access.require(access.Action.EDIT)
    data = _locked(session)
    p = _find_or_rollback(session, data, project_id)
    before = p.get("members") or []
    p["members"] = [m for m in before if not (m["kind"] == kind and m["id"] == member_id)]
    if len(p["members"]) != len(before):
        p["modified_at"] = _now()
        _save(data)
    session.commit()
    return get(session, project_id)


def projects_of(session: Session, kind: str, member_id: str) -> list[dict]:
    """The projects holding this folder / board / share ... (summaries)."""
    _hide_from_restricted()
    return [summary(p) for p in load()["projects"]
            if any(m["kind"] == kind and m["id"] == member_id for m in p.get("members") or [])]


# ---------- which items a project covers ----------


def _parents(session: Session) -> dict[str, str | None]:
    return {r.id: r.parent_id for r in session.execute(sa.select(Folder.id, Folder.parent_id))}


def project_folder_ids(p: dict) -> list[str]:
    return [m["id"] for m in p.get("members") or [] if m["kind"] == "folder"]


def project_condition(project_id: str):
    """SQL: the item is filed in the project's folders (or below)."""
    try:
        p = _find(load(), project_id)
    except (NotFound, ValueError):
        return sa.false()
    folder_ids = project_folder_ids(p)
    if not folder_ids:
        return sa.false()
    tree = (sa.select(Folder.id).where(Folder.id.in_(folder_ids))
            .cte(name="project_folders", recursive=True))
    tree = tree.union(sa.select(Folder.id).where(Folder.parent_id == tree.c.id))
    return sa.exists().where(ItemFolder.item_id == Item.id,
                             ItemFolder.folder_id.in_(sa.select(tree.c.id)))


def projects_for_item(session: Session, item: Item, data: dict | None = None) -> list[dict]:
    """The projects whose folders hold the item (subfolders count)."""
    data = data if data is not None else load()
    if not item.folders:
        return []
    parents = _parents(session)
    ancestors: set[str] = set()
    for f in item.folders:
        cur: str | None = f.id
        while cur and cur not in ancestors:
            ancestors.add(cur)
            cur = parents.get(cur)
    return [p for p in data["projects"] if ancestors & set(project_folder_ids(p))]


# ---------- field definitions ----------


def _clean_choices(value) -> list[str]:
    if not isinstance(value, list):
        raise validation.ValidationError("choices is a list of names")
    out: list[str] = []
    for c in value:
        name = _text(c, "a choice", limit=VALUE_MAX)
        if name and name.lower() not in {x.lower() for x in out}:
            out.append(name)
    if not out:
        raise validation.ValidationError("a choice field needs at least one choice")
    if len(out) > CHOICES_MAX:
        raise validation.ValidationError(f"at most {CHOICES_MAX} choices")
    return out


def add_field(session: Session, project_id: str, body: dict) -> dict:
    access.require(access.Action.EDIT)
    name = _text(body.get("name"), "field name", required=True)
    key = field_key(name)
    if not key:
        raise validation.ValidationError("a field name needs a letter or a digit")
    ftype = body.get("type") or "text"
    if ftype not in FIELD_TYPES:
        raise validation.ValidationError(f"type is one of {', '.join(FIELD_TYPES)}")
    field = {"id": new_ulid(), "name": name, "key": key, "type": ftype}
    if ftype == "choice":
        field["choices"] = _clean_choices(body.get("choices") or [])
    data = _locked(session)
    p = _find_or_rollback(session, data, project_id)
    fields = p.setdefault("fields", [])
    if any(f["key"] == key for f in fields):
        session.rollback()
        raise validation.ValidationError(f"this project already has a field called {name!r}")
    if len(fields) >= FIELDS_MAX:
        session.rollback()
        raise validation.ValidationError(f"a project has at most {FIELDS_MAX} fields")
    fields.append(field)
    p["modified_at"] = _now()
    _save(data)
    session.commit()
    return get(session, project_id)


def update_field(session: Session, project_id: str, field_id: str, body: dict) -> dict:
    """Rename, change the choices or move (position) a field. The type is
    fixed. A choice that's removed keeps its existing values (they show
    as they are and can be changed)."""
    access.require(access.Action.EDIT)
    data = _locked(session)
    p = _find_or_rollback(session, data, project_id)
    fields = p.get("fields") or []
    field = next((f for f in fields if f["id"] == field_id), None)
    if field is None:
        session.rollback()
        raise NotFound(f"field {field_id} not found")
    if body.get("type") not in (None, field["type"]):
        session.rollback()
        raise validation.ValidationError("a field's type can't change; make a new field")
    if body.get("name") is not None:
        name = _text(body["name"], "field name", required=True)
        key = field_key(name)
        if not key or any(f["key"] == key and f["id"] != field_id for f in fields):
            session.rollback()
            raise validation.ValidationError(f"this project already has a field called {name!r}")
        field["name"], field["key"] = name, key
    if body.get("choices") is not None:
        if field["type"] != "choice":
            session.rollback()
            raise validation.ValidationError("only a choice field has choices")
        field["choices"] = _clean_choices(body["choices"])
    if body.get("position") is not None:
        pos = max(0, min(len(fields) - 1, int(body["position"])))
        fields.remove(field)
        fields.insert(pos, field)
    p["modified_at"] = _now()
    _save(data)
    session.commit()
    return get(session, project_id)


def delete_field(session: Session, project_id: str, field_id: str) -> dict:
    access.require(access.Action.EDIT)
    data = _locked(session)
    p = _find_or_rollback(session, data, project_id)
    fields = p.get("fields") or []
    if not any(f["id"] == field_id for f in fields):
        session.rollback()
        raise NotFound(f"field {field_id} not found")
    p["fields"] = [f for f in fields if f["id"] != field_id]
    p["modified_at"] = _now()
    _save(data)
    session.commit()
    _clear_values(session, [field_id])
    return get(session, project_id)


def all_fields(data: dict | None = None) -> dict[str, tuple[dict, dict]]:
    """field id -> (field, project)."""
    data = data if data is not None else load()
    return {f["id"]: (f, p) for p in data["projects"] for f in p.get("fields") or []}


def fields_index(session: Session) -> list[dict]:
    """Every field key in use, for filter pickers: {key, name, type, choices,
    projects: [{id, name}]}; one entry per key."""
    _hide_from_restricted()
    by_key: dict[str, dict] = {}
    for p in load()["projects"]:
        for f in p.get("fields") or []:
            row = by_key.setdefault(f["key"], {"key": f["key"], "name": f["name"],
                                               "type": f["type"], "choices": [],
                                               "projects": []})
            for c in f.get("choices") or []:
                if c not in row["choices"]:
                    row["choices"].append(c)
            row["projects"].append({"id": p["id"], "name": p["name"],
                                    "status": p.get("status", "active")})
    return sorted(by_key.values(), key=lambda r: r["name"].lower())


# ---------- values ----------


def clean_value(field: dict, value) -> Any:
    """A value as stored, or None (no value)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    ftype = field["type"]
    if ftype == "number":
        if isinstance(value, bool):
            raise validation.ValidationError(f"{field['name']} takes a number")
        try:
            n = float(str(value).strip()) if isinstance(value, str) else float(value)
        except (TypeError, ValueError):
            raise validation.ValidationError(f"{field['name']} takes a number") from None
        if not math.isfinite(n):
            raise validation.ValidationError(f"{field['name']} takes a number")
        return int(n) if n.is_integer() and abs(n) < 2**53 else n
    text = str(value).strip()
    if len(text) > VALUE_MAX:
        raise validation.ValidationError(f"{field['name']} is at most {VALUE_MAX} characters")
    if ftype == "date":
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            raise validation.ValidationError(
                f"{field['name']} takes a date like 2026-11-12") from None
    if ftype == "choice":
        for c in field.get("choices") or []:
            if c.lower() == text.lower():
                return c
        raise validation.ValidationError(
            f"{field['name']} is one of: {', '.join(field.get('choices') or [])}")
    if ftype == "url":
        parts = urlsplit(text)
        if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
            raise validation.ValidationError(
                f"{field['name']} takes an http(s) link, e.g. https://example.com")
        return text
    return " ".join(text.split()) if "\n" not in text else text


def item_fields(session: Session, item_id: str) -> list[dict]:
    """[{project: {id, name, status}, fields: [{...field, value}]}] for the
    projects whose folders hold the item."""
    from . import items as items_svc

    _hide_from_restricted()
    item = items_svc.get_item(session, item_id)
    values = item.custom_fields or {}
    out = []
    for p in projects_for_item(session, item):
        out.append({
            "project": {"id": p["id"], "name": p["name"], "status": p.get("status", "active")},
            "fields": [{**f, "value": values.get(f["id"])} for f in p.get("fields") or []],
        })
    return out


def set_values(session: Session, item_id: str, values: dict, *, check_scope: bool = True,
               commit: bool = True) -> Item:
    """Set (or with None/"" clear) field values on one item, as one
    "fields" event. Each field must belong to a project whose folders hold
    the item (check_scope=False is undo's way back)."""
    from . import items as items_svc

    access.require(access.Action.EDIT)
    if not isinstance(values, dict) or not values:
        raise validation.ValidationError("values is {field id: value}")
    item = items_svc.get_item(session, item_id, for_update=True)
    data = load()
    known = all_fields(data)
    allowed: set[str] | None = None
    if check_scope:
        allowed = {f["id"] for p in projects_for_item(session, item, data)
                   for f in p.get("fields") or []}
    current = dict(item.custom_fields or {})
    new = dict(current)
    for fid, raw in values.items():
        if fid not in known:
            if check_scope or raw is not None:
                raise validation.ValidationError(f"no field {fid!r}")
            new.pop(fid, None)  # undo of a value whose field is gone: drop it
            continue
        field, project = known[fid]
        if allowed is not None and fid not in allowed:
            raise validation.ValidationError(
                f"{field['name']} belongs to project {project['name']!r}, and this item "
                "isn't in that project's folders")
        value = clean_value(field, raw)
        if value is None:
            new.pop(fid, None)
        else:
            new[fid] = value
    changed = sorted(k for k in set(current) | set(new) if current.get(k) != new.get(k))
    if not changed:
        return item
    events.record(session, item.id, EVENT_KIND,
                  {"fields": {k: current.get(k) for k in changed}},
                  {"fields": {k: new.get(k) for k in changed}})
    item.custom_fields = new
    if not commit:
        return item
    return items_svc.touch_and_save(session, item)


def bulk_set(session: Session, item_ids: list[str], values: dict) -> dict:
    """set_values on many items in the caller's batch. An item outside a
    field's project is skipped (reported), never an error for the rest."""
    from . import items as items_svc

    access.require(access.Action.EDIT)
    if not item_ids:
        raise validation.ValidationError("item_ids is empty")
    if len(item_ids) > 5000:
        raise validation.ValidationError("at most 5000 items at a time")
    known = all_fields()
    for fid, raw in (values or {}).items():  # bad input fails before anything changes
        if fid not in known:
            raise validation.ValidationError(f"no field {fid!r}")
        clean_value(known[fid][0], raw)
    updated, skipped = 0, []
    for item_id in dict.fromkeys(item_ids):
        try:
            item = items_svc.get_item(session, item_id)
            scope = {f["id"] for p in projects_for_item(session, item)
                     for f in p.get("fields") or []}
            mine = {k: v for k, v in values.items() if k in scope}
            if not mine:
                skipped.append({"id": item_id, "reason": "not in the project's folders"})
                continue
            before = dict(item.custom_fields or {})
            after = set_values(session, item_id, mine).custom_fields or {}
            updated += int(before != after)
        except items_svc.NotFound:
            session.rollback()
            skipped.append({"id": item_id, "reason": "not found"})
    return {"updated": updated, "skipped": skipped, "batch_id": events.context().batch_id}


def _clear_values(session: Session, field_ids: list[str]) -> int:
    """Remove these fields' values from every item (one event each)."""
    if not field_ids:
        return 0
    cond = sa.or_(*(Item.custom_fields.has_key(f) for f in field_ids))  # noqa: W601
    ids = session.execute(sa.select(Item.id).where(cond)).scalars().all()
    n = 0
    for item_id in ids:
        item = session.get(Item, item_id)
        current = dict(item.custom_fields or {})
        gone = {f: current.pop(f) for f in field_ids if f in current}
        if not gone:
            continue
        events.record(session, item.id, EVENT_KIND, {"fields": gone},
                      {"fields": dict.fromkeys(gone)})
        item.custom_fields = current
        session.commit()
        meta_svc.sync_item(session, item.id)
        n += 1
    return n


class _FieldsKind(events.ExtraKind):
    def keys(self, before, after) -> set[str]:
        names = set((before or {}).get("fields") or {}) | set((after or {}).get("fields") or {})
        return {f"field:custom:{k}" for k in names}

    def inverse(self, session: Session, ev: ItemEvent) -> str | None:
        from . import items as items_svc

        item = items_svc.get_item(session, ev.item_id)
        now = item.custom_fields or {}
        after = (ev.after or {}).get("fields") or {}
        for k, v in after.items():
            if now.get(k) != v:
                return "a field was changed later"
        before = (ev.before or {}).get("fields") or {}
        known = all_fields()
        restore = {k: v for k, v in before.items() if k in known or v is None}
        if not restore and before:
            return "its fields no longer exist"
        if restore:
            set_values(session, ev.item_id, restore, check_scope=False)
        return None


events.EXTRA_KINDS[EVENT_KIND] = _FieldsKind()


def meta_block(item: Item) -> dict:
    return dict(item.custom_fields or {})


def restore_from_meta(item: Item, meta: dict) -> None:
    """rebuild-index: the values from meta.json (anything malformed is dropped)."""
    raw = meta.get("custom_fields")
    if isinstance(raw, dict):
        item.custom_fields = {str(k): v for k, v in raw.items()
                              if isinstance(v, (str, int, float)) and not isinstance(v, bool)}
    else:
        item.custom_fields = {}


# ---------- filters ----------

_CMP = re.compile(r"^(>=|<=|>|<)\s*(.+)$")


def _number(text: str, name: str) -> float:
    try:
        n = float(text)
    except ValueError:
        raise validation.ValidationError(f"field.{name} takes a number, e.g. 3 or >=3") from None
    if not math.isfinite(n):
        raise validation.ValidationError(f"field.{name} takes a number")
    return n


def _date(text: str, name: str) -> str:
    try:
        return date.fromisoformat(text.strip()[:10]).isoformat()
    except ValueError:
        raise validation.ValidationError(
            f"field.{name} takes a date like 2026-11-12, or a range a..b") from None


def _range_cond(col, expr: str, conv, name: str):
    expr = expr.strip()
    if ".." in expr:
        lo, hi = expr.split("..", 1)
        conds = []
        if lo.strip():
            conds.append(col >= conv(lo, name))
        if hi.strip():
            conds.append(col <= conv(hi, name))
        return sa.and_(*conds) if conds else sa.true()
    m = _CMP.match(expr)
    if m:
        op, v = m.group(1), conv(m.group(2), name)
        return {">=": col >= v, "<=": col <= v, ">": col > v, "<": col < v}[op]
    return col == conv(expr, name)


def _one_field(field: dict, expr: str, name: str):
    fid = field["id"]
    present = Item.custom_fields.has_key(fid)  # noqa: W601
    if expr == "*":
        return present
    raw = Item.custom_fields[fid]
    text = Item.custom_fields[fid].astext
    ftype = field["type"]
    if ftype == "number":
        num = sa.case((sa.func.jsonb_typeof(raw) == "number",
                       sa.cast(text, sa.Numeric)), else_=None)
        return _range_cond(num, expr, _number, name)
    if ftype == "date":
        return sa.and_(present, _range_cond(text, expr, _date, name))
    if ftype == "choice":
        wanted = [c.strip().lower() for c in expr.split("|") if c.strip()]
        return sa.func.lower(text).in_(wanted)
    escaped = expr.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return text.ilike(f"%{escaped}%", escape="\\")


def filters(custom_fields: dict | None = None, project: str | None = None) -> list:
    """build_filters' part: field.<key>=<expr> and project=<id>."""
    out: list = []
    if project:
        out.append(project_condition(project))
    if not custom_fields:
        return out
    try:
        known = all_fields()
    except ValueError:
        known = {}
    for name, expr in custom_fields.items():
        if expr is None:
            continue
        expr = str(expr).strip()
        if not expr:
            continue
        key = field_key(name)
        matching = [f for fid, (f, _p) in known.items() if f["key"] == key or fid == name]
        if not matching:
            out.append(sa.false())  # nothing has a field by that name
            continue
        if expr.lower() == "none":
            out.append(sa.not_(sa.or_(*(Item.custom_fields.has_key(f["id"])  # noqa: W601
                                        for f in matching))))
            continue
        out.append(sa.or_(*(_one_field(f, expr, key) for f in matching)))
    return out


def fields_from_params(params) -> dict[str, str]:
    """field.<key>=<expr> query parameters -> {key: expr}."""
    out: dict[str, str] = {}
    for k, v in params.items():
        if k.startswith("field.") and len(k) > 6 and v is not None:
            out[k[6:][:64]] = str(v)[:VALUE_MAX]
    return out


# ---------- pins ----------


def _hide_from_restricted() -> None:
    """Projects can name things outside a folder-limited reader's folders."""
    if access.restricted() is not None:
        raise NotFound("projects aren't available to this token")


def _clean_pin(session: Session, body: dict) -> dict:
    kind = body.get("kind")
    if kind not in PIN_KINDS:
        raise validation.ValidationError(f"kind is one of {', '.join(PIN_KINDS)}")
    ref = str(body.get("ref") or "").strip()
    pin: dict = {"kind": kind}
    if kind == "search":
        name = _text(body.get("name"), "name", required=True)
        query = str(body.get("query") or "").strip().lstrip("?")
        if not query or len(query) > SEARCH_MAX:
            raise validation.ValidationError(
                f"a saved search needs its search (URL parameters, at most {SEARCH_MAX})")
        pin.update(ref=ref if _ID_RE.match(ref) else new_ulid(), name=name, query=query)
        return pin
    if not ref or len(ref) > 500:
        raise validation.ValidationError("a pin needs ref: the id (or the tag name)")
    found = _names(session, kind, [ref], load())
    if not found:
        raise validation.ValidationError(f"no {kind} {ref!r}")
    # a tag pin keeps the tag's own spelling
    pin["ref"] = found[ref]["name"] if kind == "tag" else ref
    return pin


def _names(session: Session, kind: str, refs: list[str], data: dict) -> dict[str, dict]:
    """ref -> {name, ...} for the pins of one kind that still exist."""
    if not refs:
        return {}
    if kind == "project":
        return {p["id"]: {"name": p["name"], "status": p.get("status", "active")}
                for p in data["projects"] if p["id"] in refs}
    if kind == "folder":
        return _folders(session, refs)
    if kind == "smart":
        return _smart(session, refs)
    if kind == "board":
        return {k: v for k, v in _boards(session, refs).items() if not v.get("trashed")}
    if kind == "person":
        rows = session.execute(sa.select(Person.id, Person.name)
                               .where(Person.id.in_(refs))).all()
        return {r.id: {"name": r.name or "Unnamed person"} for r in rows}
    if kind == "tag":
        rows = session.execute(sa.select(Tag.name).where(
            sa.func.lower(Tag.name).in_([r.lower() for r in refs]))).scalars().all()
        lower = {n.lower(): n for n in rows}
        return {r: {"name": lower[r.lower()]} for r in refs if r.lower() in lower}
    return {}


def list_pins(session: Session) -> list[dict]:
    """The sidebar's pins in order, names resolved; gone targets and
    archived projects left out. A folder-limited reader sees only pins of
    its folders and boards it can see."""
    data = load()
    pins = data["pins"]
    reader = access.restricted()
    found: dict[str, dict[str, dict]] = {}
    for kind in PIN_KINDS:
        refs = [p["ref"] for p in pins if p.get("kind") == kind]
        if kind != "search":
            found[kind] = _names(session, kind, refs, data)
    out = []
    for pin in pins:
        kind = pin.get("kind")
        if reader is not None:
            if kind == "folder" and pin["ref"] not in reader.visible_folders:
                continue
            if kind not in ("folder", "board"):
                continue
        if kind == "search":
            out.append({"kind": kind, "ref": pin["ref"], "name": pin.get("name", ""),
                        "query": pin.get("query", "")})
            continue
        info = found.get(kind, {}).get(pin.get("ref"))
        if info is None or info.get("status") == "archived":
            continue
        row = {"kind": kind, "ref": pin["ref"], "name": info["name"]}
        if kind == "smart":
            row["query"] = info.get("query") or {}
        out.append(row)
    return out


def add_pin(session: Session, body: dict) -> list[dict]:
    access.require(access.Action.EDIT)
    pin = _clean_pin(session, body)
    data = _locked(session)
    if not any(p.get("kind") == pin["kind"] and p.get("ref") == pin["ref"]
               for p in data["pins"]):
        if len(data["pins"]) >= PINS_MAX:
            session.rollback()
            raise validation.ValidationError(f"at most {PINS_MAX} pins")
        data["pins"].append(pin)
        _save(data)
    session.commit()
    return list_pins(session)


def remove_pin(session: Session, kind: str, ref: str) -> list[dict]:
    """Unpinning something that isn't pinned is a no-op."""
    access.require(access.Action.EDIT)
    data = _locked(session)
    keep = [p for p in data["pins"] if not (p.get("kind") == kind and p.get("ref") == ref)]
    if len(keep) != len(data["pins"]):
        data["pins"] = keep
        _save(data)
    session.commit()
    return list_pins(session)


def reorder_pins(session: Session, order: list[dict]) -> list[dict]:
    """Put the listed pins first, in that order; pins not listed (hidden
    ones: archived projects) keep their relative order after them."""
    access.require(access.Action.EDIT)
    if not isinstance(order, list):
        raise validation.ValidationError("pins is a list of {kind, ref}")
    data = _locked(session)
    by_key = {(p.get("kind"), p.get("ref")): p for p in data["pins"]}
    first, seen = [], set()
    for o in order:
        key = (o.get("kind"), o.get("ref")) if isinstance(o, dict) else None
        if key in by_key and key not in seen:
            first.append(by_key[key])
            seen.add(key)
    data["pins"] = first + [p for p in data["pins"]
                            if (p.get("kind"), p.get("ref")) not in seen]
    _save(data)
    session.commit()
    return list_pins(session)


# ---------- verify ----------


def check_file(session: Session) -> list[str]:
    """verify: projects.json is readable and well formed."""
    try:
        data = load()
    except (ValueError, OSError):
        return ["projects.json unreadable"]
    problems: list[str] = []
    ids: set[str] = set()
    for p in data["projects"]:
        if not isinstance(p, dict) or not p.get("id") or not p.get("name"):
            problems.append("projects.json: a project without id or name")
            continue
        if p["id"] in ids:
            problems.append(f"projects.json: project id {p['id']} appears twice")
        ids.add(p["id"])
        if p.get("status", "active") not in STATUSES:
            problems.append(f"projects.json: project {p['id']} has status {p.get('status')!r}")
        keys: set[str] = set()
        for f in p.get("fields") or []:
            if not isinstance(f, dict) or not f.get("id") or f.get("type") not in FIELD_TYPES:
                problems.append(f"projects.json: project {p['id']} has a malformed field")
                continue
            if f.get("key") in keys:
                problems.append(f"projects.json: project {p['id']} has two fields "
                                f"called {f.get('name')!r}")
            keys.add(f.get("key"))
        for m in p.get("members") or []:
            if not isinstance(m, dict) or not m.get("kind") or not m.get("id"):
                problems.append(f"projects.json: project {p['id']} has a malformed member")
    for pin in data["pins"]:
        if not isinstance(pin, dict) or pin.get("kind") not in PIN_KINDS or not pin.get("ref"):
            problems.append("projects.json: a malformed pin")
    return problems
