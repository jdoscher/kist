"""Metadata templates and bulk rename (Milestone 3, batch edit).

Templates
---------
A template is a named set of edits applied to many items at once:
{id, name, fields: {tags_add, folder_add, notes_append, source_url, rating,
label, flag, credit, license}}. They live only in
library/.kist/templates.json (plain file, written atomically under an
advisory lock); there is no table, so rebuild-index has nothing to restore
and verify checks the file is readable and its folders still exist.

credit and license become namespaced tags (`credit:<value>`,
`license:<value>`), so they are searchable, filterable, and usable in a
rename pattern as {tag:credit}.

Applying one goes through the item services, so every change is an item
event in the caller's batch: one request (or one MCP call) is one batch and
POST /api/batches/{id}/undo takes it all back.

Bulk rename
-----------
`rename(item_ids, pattern, dry_run)` builds each new filename from a
pattern: {name} (filename without extension), {ext}, {n} / {n:3} (counter,
zero-padded), {date} / {date:%Y%m%d} (created_at, UTC), {w} and {h}
(pixels), {folder} (the item's first folder by name), {tag:ns} (the value of
the item's first `ns:value` tag). Without {ext} in the pattern the item's
extension is appended. Names that collide, with each other or with another
item's filename, get " (2)", " (3)" ... before the extension.
"""

import json
import re
from datetime import UTC

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .. import access
from ..config import get_settings
from ..ids import new_ulid
from ..models import Folder, Item
from . import events, validation
from . import items as items_svc
from . import meta as meta_svc

FILE = "templates.json"
TEMPLATES_MAX = 500
FIELDS = ("tags_add", "folder_add", "notes_append", "source_url", "rating", "label",
          "flag", "credit", "license")


class NotFound(Exception):
    """No template with that id (HTTP 404)."""


def path():
    return get_settings().state_dir / FILE


def load() -> list[dict]:
    p = path()
    if not p.is_file():
        return []
    data = meta_svc.read_json(p)
    rows = data.get("templates") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("templates.json has no templates list")
    return rows


def _save(rows: list[dict]) -> None:
    meta_svc.atomic_write_text(
        path(), json.dumps({"version": 1, "templates": rows}, indent=2, ensure_ascii=False)
    )


def clean_fields(session: Session, fields: dict) -> dict:
    out: dict = {}
    tags = [validation.tag_name(t) for t in fields.get("tags_add") or []]
    if tags:
        out["tags_add"] = tags
    folders = [f for f in fields.get("folder_add") or [] if f]
    if folders:
        items_svc.resolve_folders(session, folders)  # unknown -> 422
        out["folder_add"] = folders
    if fields.get("notes_append"):
        out["notes_append"] = validation.notes(fields["notes_append"])
    if fields.get("source_url"):
        out["source_url"] = validation.source_url(fields["source_url"])
    if fields.get("rating") is not None:
        out["rating"] = validation.rating(fields["rating"])
    for key, check in (("label", validation.label), ("flag", validation.flag)):
        if fields.get(key) is not None:
            out[key] = check(fields[key]) or "none"  # "none" clears on apply
    for key in ("credit", "license"):
        if fields.get(key):
            validation.tag_name(f"{key}:{fields[key]}")
            out[key] = str(fields[key]).strip()
    return out


def list_templates(session: Session) -> list[dict]:
    return load()


def get(session: Session, template_id: str) -> dict:
    for t in load():
        if t.get("id") == template_id:
            return t
    raise NotFound(f"template {template_id} not found")


def _locked(session: Session):
    meta_svc.advisory_lock(session, FILE)


def create(session: Session, name: str, fields: dict) -> dict:
    access.require(access.Action.EDIT)
    tpl = {"id": new_ulid(), "name": _name(name), "fields": clean_fields(session, fields)}
    _locked(session)
    rows = load()
    if len(rows) >= TEMPLATES_MAX:
        session.rollback()
        raise validation.ValidationError(f"at most {TEMPLATES_MAX} templates")
    rows.append(tpl)
    _save(rows)
    session.commit()
    return tpl


def update(session: Session, template_id: str, name: str, fields: dict) -> dict:
    access.require(access.Action.EDIT)
    cleaned = clean_fields(session, fields)
    _locked(session)
    rows = load()
    for t in rows:
        if t.get("id") == template_id:
            t["name"], t["fields"] = _name(name), cleaned
            _save(rows)
            session.commit()
            return t
    session.rollback()
    raise NotFound(f"template {template_id} not found")


def delete(session: Session, template_id: str) -> None:
    access.require(access.Action.EDIT)
    _locked(session)
    rows = load()
    keep = [t for t in rows if t.get("id") != template_id]
    if len(keep) == len(rows):
        session.rollback()
        raise NotFound(f"template {template_id} not found")
    _save(keep)
    session.commit()


def _name(value) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 200:
        raise validation.ValidationError("a template name is 1-200 characters")
    return name


def apply(session: Session, template_id: str, item_ids: list[str]) -> list[Item]:
    """Apply the template to every item, in the caller's batch."""
    access.require(access.Action.EDIT)
    fields = get(session, template_id).get("fields") or {}
    # check everything first, so a bad id changes nothing
    targets = [items_svc.get_item(session, i) for i in dict.fromkeys(item_ids)]
    if fields.get("folder_add"):
        items_svc.resolve_folders(session, fields["folder_add"])
    tags = list(fields.get("tags_add") or [])
    for key in ("credit", "license"):
        if fields.get(key):
            tags.append(f"{key}:{fields[key]}")
    out = []
    for item in targets:
        item_id = item.id
        if tags:
            items_svc.set_tags(session, item_id, add=tags)
        if fields.get("folder_add"):
            items_svc.set_folders(session, item_id, add=fields["folder_add"])
        edit: dict = {}
        current = items_svc.get_item(session, item_id)
        note = fields.get("notes_append")
        if note and note not in (current.notes or ""):
            joined = f"{current.notes.rstrip()}\n{note}" if current.notes else note
            edit["notes"] = joined
        if fields.get("source_url"):
            edit["source_url"] = fields["source_url"]
        if fields.get("rating") is not None:
            edit["rating"] = fields["rating"]
        for key in ("label", "flag"):
            if fields.get(key) is not None:
                edit[key] = None if fields[key] == "none" else fields[key]
        if edit:
            items_svc.update_item(session, item_id, fields=edit)
        out.append(items_svc.get_item(session, item_id))
    return out


# ---------- bulk rename ----------

_TOKEN = re.compile(r"\{([a-z]+)(?::([^}]*))?\}")


def _stem_ext(filename: str) -> tuple[str, str]:
    if "." in filename.strip(".") and not filename.startswith("."):
        stem, ext = filename.rsplit(".", 1)
        return stem, ext
    return filename, ""


def _folder_name(item: Item) -> str:
    names = sorted(f.name for f in item.folders if f.id in set(
        access.visible_folder_filter(g.id for g in item.folders)))
    return names[0] if names else ""


def _tag_value(item: Item, ns: str) -> str:
    prefix = f"{ns.lower()}:"
    for name in sorted(t.name for t in item.tags):
        if name.lower().startswith(prefix):
            return name[len(prefix):].strip()
    return ""


def _safe(text: str) -> str:
    return text.replace("/", "-").replace("\x00", "")


def render_name(pattern: str, item: Item, n: int) -> str:
    def sub(m: re.Match) -> str:
        key, arg = m.group(1), m.group(2)
        if key == "name":
            return _stem_ext(item.filename)[0]
        if key == "ext":
            return item.ext
        if key == "n":
            width = 0
            if arg:
                if not arg.isdigit() or int(arg) > 12:
                    raise validation.ValidationError("{n:width} takes a width from 0 to 12")
                width = int(arg)
            return str(n).zfill(width)
        if key == "date":
            fmt = arg or "%Y%m%d"
            try:
                return item.created_at.astimezone(UTC).strftime(fmt)
            except ValueError:
                raise validation.ValidationError(f"bad date format {fmt!r}") from None
        if key == "w":
            return str(item.width or 0)
        if key == "h":
            return str(item.height or 0)
        if key == "folder":
            return _folder_name(item)
        if key == "tag":
            if not arg:
                raise validation.ValidationError("{tag:ns} needs a namespace, e.g. {tag:client}")
            return _tag_value(item, arg)
        raise validation.ValidationError(
            f"unknown token {{{key}}}; use {{name}}, {{ext}}, {{n:3}}, {{date:%Y%m%d}}, "
            "{w}, {h}, {folder} or {tag:ns}"
        )

    out = _safe(_TOKEN.sub(sub, pattern)).strip()
    if not out.strip(" ."):
        raise validation.ValidationError(f"the pattern gives {item.filename!r} an empty name")
    if "{ext}" not in pattern and item.ext:
        out = f"{out}.{item.ext}"
    return out


def _with_suffix(name: str, k: int) -> str:
    stem, ext = _stem_ext(name)
    return f"{stem} ({k}).{ext}" if ext else f"{name} ({k})"


def rename(session: Session, item_ids: list[str], pattern: str, *, dry_run: bool,
           start: int = 1) -> list[dict]:
    """[{id, before, after, changed}] in the order given. With dry_run
    nothing changes; otherwise each rename is an item edit event in the
    caller's batch."""
    if not dry_run:
        access.require(access.Action.EDIT)
    ids = list(dict.fromkeys(item_ids))
    items = [items_svc.get_item(session, i) for i in ids]
    ids_set = set(ids)
    rows = []
    taken: set[str] = set()
    for k, item in enumerate(items):
        wanted = render_name(pattern, item, start + k)
        validation.filename(wanted)
        candidate, n = wanted, 1
        while True:
            lower = candidate.lower()
            clash = lower in taken or session.execute(
                sa.select(Item.id).where(sa.func.lower(Item.filename) == lower,
                                         Item.id.not_in(ids_set),
                                         Item.deleted_at.is_(None)).limit(1)
            ).first() is not None
            if not clash:
                break
            n += 1
            candidate = _with_suffix(wanted, n)
        validation.filename(candidate)
        taken.add(candidate.lower())
        rows.append({"id": item.id, "before": item.filename, "after": candidate,
                     "changed": candidate != item.filename})
    if not dry_run:
        for row in rows:
            if row["changed"]:
                items_svc.update_item(session, row["id"], fields={"filename": row["after"]})
    return rows


def batch_id() -> str:
    return events.context().batch_id


def check_file(session: Session) -> list[str]:
    """verify: templates.json is readable and its folders exist."""
    try:
        rows = load()
    except (ValueError, OSError):
        return ["templates.json unreadable"]
    problems = []
    folder_ids = set(session.execute(sa.select(Folder.id)).scalars())
    for t in rows:
        if not isinstance(t, dict) or not t.get("id") or not t.get("name"):
            problems.append("templates.json: a template without id or name")
            continue
        for f in (t.get("fields") or {}).get("folder_add") or []:
            if f not in folder_ids:
                problems.append(f"templates.json: template {t['id']} ({t['name']}) "
                                f"files into folder {f}, which no longer exists")
    return problems
