"""Command palette (Milestone 3, the web's Cmd+K): one query, mixed results.

GET /api/palette?q= answers folders, smart folders, tags, boards and items
(by filename, top 5 each), best matches first (exact, then prefix, then
contains), plus the static actions below matched by title. The web may add
its own actions; these are the ones it can always count on.
"""

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..models import Folder, Item, SmartFolder, Tag
from . import boards as boards_svc
from . import search

PER_KIND = 5
ACTIONS = (
    ("upload", "Add files", "Upload or drop files"),
    ("add-url", "Add from URL", "Save an image from a link"),
    ("new-folder", "New folder", ""),
    ("new-board", "New board", "Start a moodboard"),
    ("triage", "Triage Unfiled", "Rate, tag and file the inbox one by one"),
    ("compare", "Compare selected", "Side by side, wipe or overlay"),
    ("rename", "Rename selected", "Bulk rename with a pattern"),
    ("apply-template", "Apply template", "Add tags, folders, credit and more"),
    ("history", "History", "Recent changes and undo"),
    ("trash", "Trash", ""),
    ("settings", "Settings", ""),
)


def _rank(title: str, q: str) -> tuple[int, int, str]:
    t, ql = title.lower(), q.lower()
    return (0 if t == ql else 1 if t.startswith(ql) else 2, len(title), t)


def palette(session: Session, q: str, limit: int = PER_KIND) -> list[dict]:
    from .meta import folder_path

    q = (q or "").strip()
    out: list[dict] = []
    pattern = f"%{q}%"
    if not q:
        return [{"kind": "action", "id": a, "title": t, "subtitle": s} for a, t, s in ACTIONS]

    folders = session.execute(
        sa.select(Folder).where(Folder.name.ilike(pattern)).limit(50)
    ).scalars().all()
    from .. import access

    visible = set(access.visible_folder_filter(f.id for f in folders))
    folders = sorted((f for f in folders if f.id in visible), key=lambda f: _rank(f.name, q))
    out += [{"kind": "folder", "id": f.id, "title": f.name,
             "subtitle": " / ".join(folder_path(f)[:-1])} for f in folders[:limit]]

    if access.restricted() is None:
        smart = session.execute(
            sa.select(SmartFolder).where(SmartFolder.name.ilike(pattern)).limit(50)
        ).scalars().all()
        out += [{"kind": "smart_folder", "id": s.id, "title": s.name, "subtitle": "Smart folder"}
                for s in sorted(smart, key=lambda s: _rank(s.name, q))[:limit]]
        tags = session.execute(
            sa.select(Tag).where(Tag.name.ilike(pattern)).limit(50)
        ).scalars().all()
        out += [{"kind": "tag", "id": str(t.id), "title": t.name, "subtitle": "Tag"}
                for t in sorted(tags, key=lambda t: _rank(t.name, q))[:limit]]

    boards = boards_svc.list_boards(session, q=q, limit=50)
    out += [{"kind": "board", "id": b.id, "title": b.name,
             "subtitle": f"Board · {b.item_count} items"}
            for b in sorted(boards, key=lambda b: _rank(b.name, q))[:limit]]

    filters = search.build_filters()
    items = session.execute(
        sa.select(Item).where(*filters, Item.filename.ilike(pattern))
        .order_by(Item.modified_at.desc()).limit(50)
    ).scalars().all()
    out += [{"kind": "item", "id": i.id, "title": i.filename,
             "subtitle": f"{i.ext.upper()}" + (f" · {i.width}×{i.height}" if i.width else "")}
            for i in sorted(items, key=lambda i: _rank(i.filename, q))[:limit]]

    out += [{"kind": "action", "id": a, "title": t, "subtitle": s} for a, t, s in ACTIONS
            if q.lower() in t.lower()]
    return out
