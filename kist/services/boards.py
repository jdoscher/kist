"""Boards: a canvas of library items, notes, text, shapes, groups, links,
colour swatches and arrows (Milestone 3).

The document
------------
library/boards/<id>/board.json is the durable copy:

    {id, name, created_at, modified_at, version, deleted_at,
     canvas: {bg, grid},
     nodes: [{id, type, x, y, w, h, rot, z, item_id?, crop?, text?, style?,
              children?}],
     edges: [{id, from, to, label?, style?}]}

canvas.bg is a CSS colour, or "theme": draw the board on the app theme's
image canvas (each viewer's own theme). New boards get "theme"; a canvas
saved without a bg (boards from before Kist 2.0) keeps the old paper
default, so no existing board changes colour. Exports and the thumbnail
have no theme and use THEME_EXPORT_BG (board_render.py).

Postgres keeps the same document in boards.doc plus the columns a list
needs, and board_items(board_id, item_id) so "used in boards" is a query.
Nodes reference items (never copies); purging an item leaves its node in
place, drawn as a missing item. A thumb.webp snapshot is rendered next to
board.json after every save.

Saving
------
Every save bumps `version`. PUT (whole document) and PATCH (node ops) take
the version the client last saw as base_version; a save based on an older
version is refused with Conflict (HTTP 409, the current board in the body),
checked under a row lock so two saves can't both pass. As with meta.json,
the database commits first and the file is then written from the committed
row under an advisory lock, so the file always ends on the newest version.

History and undo
----------------
Board changes are `board_events` rows (create, update, trash, restore,
purge; before/after hold whole documents or the state), in the same batch
as the request's item events (X-Kist-Batch), and are appended to
library/boards/<id>/events.jsonl. events.UNDO_SOURCES makes
POST /api/batches/{id}/undo revert them too: an update is undone by saving
the earlier document as a new version, unless a later change outside the
batch touched the board.

Who sees a board
----------------
A folder-limited read token sees a board only when it shows at least one
item and every item node is an item that token can see; otherwise the board
doesn't exist for it (404), so a board can't leak what's outside its folders.
"""

import json
import logging
import math
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session

from .. import access
from ..config import get_settings
from ..ids import new_ulid
from ..lifecycle import TransitionError
from ..models import Board, BoardEvent, BoardItem, Item
from . import events, validation
from . import items as items_svc
from . import meta as meta_svc

log = logging.getLogger("kist.boards")

DIR = "boards"
FILE = "board.json"
THUMB = "thumb.webp"
EVENTS_FILE = "events.jsonl"
PURGED_EVENTS_FILE = "board-events-purged.jsonl"

NODE_TYPES = ("item", "note", "text", "shape", "group", "link", "color")
NAME_MAX = 200
NODES_MAX = 5000
EDGES_MAX = 10000
NODE_JSON_MAX = 20_000
DOC_JSON_MAX = 4_000_000
TEXT_MAX = 10_000
ID_MAX = 64
COORD_MAX = 1e7
THEME_BG = "theme"
# fills in a canvas saved without some key (the pre-2.0 default bg)
DEFAULT_CANVAS = {"bg": "#f5f5f4", "grid": 0}
# what a new board starts with: the theme's canvas (design pass B15)
NEW_CANVAS = {"bg": THEME_BG, "grid": 0}
CELL = 320  # grid layout cell
GAP = 20


class NotFound(Exception):
    """No board with that id the caller can see (HTTP 404)."""


class Conflict(Exception):
    """The board changed since base_version (HTTP 409)."""

    message = "The board changed since you opened it"

    def __init__(self, board: Board):
        super().__init__(self.message)
        self.board = board


class Kind:
    CREATE = "create"
    UPDATE = "update"
    TRASH = "trash"
    RESTORE = "restore"
    PURGE = "purge"


# ---------- paths ----------


def boards_dir() -> Path:
    return get_settings().library_dir / DIR


def board_dir(board_id: str) -> Path:
    return boards_dir() / board_id


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# ---------- the document ----------


def _num(value, name: str, default: float | None = None, *, positive: bool = False) -> float:
    if value is None:
        if default is None:
            raise validation.ValidationError(f"node {name} is required")
        return default
    if isinstance(value, bool):
        raise validation.ValidationError(f"node {name} must be a number")
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise validation.ValidationError(f"node {name} must be a number") from None
    if not math.isfinite(v) or abs(v) > COORD_MAX:
        raise validation.ValidationError(f"node {name} is out of range")
    if positive and v <= 0:
        raise validation.ValidationError(f"node {name} must be above 0")
    return v


def _id(value, what: str) -> str:
    if not isinstance(value, str) or not value or len(value) > ID_MAX:
        raise validation.ValidationError(f"{what} id must be a string of 1-{ID_MAX} characters")
    return value


def _size(obj, limit: int, what: str) -> None:
    if len(json.dumps(obj, default=str)) > limit:
        raise validation.ValidationError(f"{what} is too large")


def clean_node(raw, index: int) -> dict:
    if not isinstance(raw, dict):
        raise validation.ValidationError("every node must be an object")
    node = dict(raw)
    node["id"] = _id(node.get("id") or new_ulid(), "node")
    if node.get("type") not in NODE_TYPES:
        raise validation.ValidationError(
            f"unknown node type {node.get('type')!r}; expected one of {', '.join(NODE_TYPES)}"
        )
    node["x"] = _num(node.get("x"), "x", 0.0)
    node["y"] = _num(node.get("y"), "y", 0.0)
    node["w"] = _num(node.get("w"), "w", 200.0, positive=True)
    node["h"] = _num(node.get("h"), "h", 200.0, positive=True)
    node["rot"] = _num(node.get("rot"), "rot", 0.0)
    z = node.get("z", index)
    if isinstance(z, bool) or not isinstance(z, (int, float)) or not math.isfinite(z):
        raise validation.ValidationError("node z must be a number")
    node["z"] = int(z)
    if node["type"] == "item":
        node["item_id"] = _id(node.get("item_id"), "item")
    else:
        node.pop("item_id", None)
    crop = node.get("crop")
    if crop is not None:
        if not isinstance(crop, dict):
            raise validation.ValidationError("crop must be {x, y, w, h} (fractions 0-1)")
        c = {k: _num(crop.get(k), f"crop.{k}", d) for k, d in
             (("x", 0.0), ("y", 0.0), ("w", 1.0), ("h", 1.0))}
        if not (0 <= c["x"] < 1 and 0 <= c["y"] < 1 and 0 < c["w"] <= 1 and 0 < c["h"] <= 1
                and c["x"] + c["w"] <= 1 + 1e-9 and c["y"] + c["h"] <= 1 + 1e-9):
            raise validation.ValidationError("crop must lie inside the image (fractions 0-1)")
        node["crop"] = c
    if node.get("text") is not None:
        if not isinstance(node["text"], str) or len(node["text"]) > TEXT_MAX:
            raise validation.ValidationError(f"node text is a string of at most {TEXT_MAX}")
    if node.get("style") is not None and not isinstance(node["style"], dict):
        raise validation.ValidationError("node style must be an object")
    if node.get("children") is not None:
        if node["type"] != "group" or not isinstance(node["children"], list):
            raise validation.ValidationError("only a group has children (a list of node ids)")
        node["children"] = [_id(c, "child") for c in node["children"]]
    _size(node, NODE_JSON_MAX, "a node")
    return node


def clean_edge(raw, node_ids: set[str]) -> dict:
    if not isinstance(raw, dict):
        raise validation.ValidationError("every edge must be an object")
    edge = dict(raw)
    edge["id"] = _id(edge.get("id") or new_ulid(), "edge")
    for end in ("from", "to"):
        if edge.get(end) not in node_ids:
            raise validation.ValidationError(f"edge {edge['id']}: {end} is not a node on the board")
    if edge.get("label") is not None and (
        not isinstance(edge["label"], str) or len(edge["label"]) > 500
    ):
        raise validation.ValidationError("edge label is a string of at most 500 characters")
    if edge.get("style") is not None and not isinstance(edge["style"], dict):
        raise validation.ValidationError("edge style must be an object")
    _size(edge, NODE_JSON_MAX, "an edge")
    return edge


def clean_content(nodes, edges, canvas) -> tuple[list[dict], list[dict], dict]:
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise validation.ValidationError("nodes and edges must be lists")
    if len(nodes) > NODES_MAX:
        raise validation.ValidationError(f"a board holds at most {NODES_MAX} nodes")
    if len(edges) > EDGES_MAX:
        raise validation.ValidationError(f"a board holds at most {EDGES_MAX} edges")
    clean_nodes = [clean_node(n, i) for i, n in enumerate(nodes)]
    ids = [n["id"] for n in clean_nodes]
    if len(set(ids)) != len(ids):
        raise validation.ValidationError("node ids must be unique")
    node_ids = set(ids)
    for n in clean_nodes:
        for c in n.get("children") or []:
            if c not in node_ids or c == n["id"]:
                raise validation.ValidationError(f"group {n['id']}: child {c} is not on the board")
    clean_edges = [clean_edge(e, node_ids) for e in edges]
    eids = [e["id"] for e in clean_edges]
    if len(set(eids)) != len(eids):
        raise validation.ValidationError("edge ids must be unique")
    if canvas is None:
        canvas = dict(DEFAULT_CANVAS)
    if not isinstance(canvas, dict):
        raise validation.ValidationError("canvas must be an object")
    canvas = {**DEFAULT_CANVAS, **canvas}
    _size(canvas, NODE_JSON_MAX, "canvas")
    return clean_nodes, clean_edges, canvas


def item_ids_of(doc: dict) -> list[str]:
    return sorted({n["item_id"] for n in doc.get("nodes") or [] if n.get("type") == "item"})


def _check_new_items(session: Session, ids: set[str], before: set[str]) -> None:
    """Items newly placed on a board must exist and be visible to the
    caller; nodes already there may point at purged items (missing)."""
    for item_id in sorted(ids - before):
        items_svc.get_item(session, item_id)  # NotFound -> 404


def _name(value) -> str:
    name = str(value or "").strip()
    if not name:
        raise validation.ValidationError("a board needs a name")
    if len(name) > NAME_MAX:
        raise validation.ValidationError(f"a board name is at most {NAME_MAX} characters")
    return name


# ---------- reading ----------


def visible(session: Session, board: Board) -> bool:
    p = access.restricted()
    if p is None:
        return True
    if board.deleted_at is not None:
        return False
    ids = item_ids_of(board.doc)
    if not ids:
        return False
    rows = session.execute(sa.select(Item).where(Item.id.in_(ids))).scalars().all()
    return len(rows) == len(ids) and all(items_svc.visible(i) for i in rows)


def get_board(session: Session, board_id: str, *, for_update: bool = False) -> Board:
    board = session.get(Board, board_id, with_for_update=for_update or None,
                        populate_existing=for_update)
    if board is None or not visible(session, board):
        raise NotFound(f"board {board_id} not found")
    return board


def list_boards(session: Session, *, state: str = "active", q: str | None = None,
                limit: int = 200) -> list[Board]:
    if state not in ("active", "trashed"):
        raise validation.ValidationError("state is active or trashed")
    stmt = sa.select(Board).where(
        Board.deleted_at.is_(None) if state == "active" else Board.deleted_at.isnot(None)
    )
    if q:
        stmt = stmt.where(Board.name.ilike(f"%{q}%"))
    rows = session.execute(stmt.order_by(Board.modified_at.desc(), Board.id.desc())
                           .limit(limit)).scalars().all()
    return [b for b in rows if visible(session, b)]


def boards_of_item(session: Session, item_id: str) -> list[Board]:
    items_svc.get_item(session, item_id)
    rows = session.execute(
        sa.select(Board).join(BoardItem, BoardItem.board_id == Board.id)
        .where(BoardItem.item_id == item_id, Board.deleted_at.is_(None))
        .order_by(Board.modified_at.desc())
    ).scalars().all()
    return [b for b in rows if visible(session, b)]


def missing_items(session: Session, doc: dict) -> list[str]:
    ids = item_ids_of(doc)
    if not ids:
        return []
    have = set(session.execute(sa.select(Item.id).where(Item.id.in_(ids))).scalars())
    return [i for i in ids if i not in have]


def share_item_ids(session: Session, board_id: str) -> list[str]:
    """The live items on a board, in reading order (top to bottom, then left
    to right), for share links and exports (services/selection.py). Raises
    NotFound for an unknown or trashed board; a deleted item is left out."""
    board = session.get(Board, board_id)
    if board is None or board.deleted_at is not None:
        raise NotFound(f"board {board_id} not found")
    nodes = [n for n in board.doc.get("nodes") or []
             if n.get("type") == "item" and n.get("item_id")]
    nodes.sort(key=lambda n: (float(n.get("y") or 0), float(n.get("x") or 0)))
    ordered = list(dict.fromkeys(n["item_id"] for n in nodes))
    live = set(session.execute(
        sa.select(Item.id).where(Item.id.in_(ordered or [""]), Item.deleted_at.is_(None))
    ).scalars())
    return [i for i in ordered if i in live]


def summary(board: Board) -> dict:
    return {
        "id": board.id, "name": board.name, "version": board.version,
        "item_count": board.item_count, "created_at": board.created_at,
        "modified_at": board.modified_at, "deleted_at": board.deleted_at,
        "thumb_url": f"/api/boards/{board.id}/thumb?v={board.version}",
    }


def out(session: Session, board: Board) -> dict:
    """The document as the API returns it: board.json plus state,
    item_count, missing_item_ids and thumb_url. Items a folder-limited
    reader can't see never get this far (see visible)."""
    doc = dict(board.doc)
    doc["state"] = "trashed" if board.deleted_at is not None else "active"
    doc["item_count"] = board.item_count
    doc["missing_item_ids"] = missing_items(session, board.doc)
    doc["thumb_url"] = f"/api/boards/{board.id}/thumb?v={board.version}"
    return doc


# ---------- writing ----------


def _set_items(session: Session, board: Board) -> None:
    ids = item_ids_of(board.doc)
    session.execute(sa.delete(BoardItem).where(BoardItem.board_id == board.id))
    if ids:
        existing = set(session.execute(sa.select(Item.id).where(Item.id.in_(ids))).scalars())
        for item_id in ids:
            if item_id in existing:
                session.add(BoardItem(board_id=board.id, item_id=item_id))
    board.item_count = len(ids)


def _doc(board: Board, *, name: str, canvas: dict, nodes: list, edges: list) -> dict:
    return {
        "id": board.id,
        "name": name,
        "created_at": _iso(board.created_at),
        "modified_at": _iso(board.modified_at),
        "version": board.version,
        "deleted_at": _iso(board.deleted_at),
        "canvas": canvas,
        "nodes": nodes,
        "edges": edges,
    }


def create_board(session: Session, name: str, *, canvas: dict | None = None,
                 nodes: list | None = None, edges: list | None = None,
                 item_ids: list[str] | None = None, layout: str = "grid",
                 board_id: str | None = None) -> Board:
    access.require(access.Action.EDIT)
    name = _name(name)
    if canvas is None or isinstance(canvas, dict):
        # a new board follows the theme unless it says otherwise
        canvas = {**NEW_CANVAS, **(canvas or {})}
    nodes, edges, canvas = clean_content(nodes or [], edges or [], canvas)
    if item_ids:
        nodes = nodes + layout_nodes(session, item_ids, layout, nodes)
    _check_new_items(session, {n["item_id"] for n in nodes if n["type"] == "item"}, set())
    _size(nodes, DOC_JSON_MAX, "the board")
    now = datetime.now(UTC)
    board = Board(id=board_id or new_ulid(), name=name, created_at=now, modified_at=now,
                  version=1, item_count=0, deleted_at=None)
    board.doc = _doc(board, name=name, canvas=canvas, nodes=nodes, edges=edges)
    session.add(board)
    session.flush()
    _set_items(session, board)
    record(session, board.id, Kind.CREATE, None, board.doc)
    return _commit(session, board)


def _check_base(session: Session, board: Board, base_version: int | None) -> None:
    if board.deleted_at is not None:
        session.rollback()
        raise TransitionError("The board is in the trash: restore it first")
    if base_version is not None and base_version != board.version:
        session.rollback()
        raise Conflict(get_board(session, board.id))


def _replace(session: Session, board: Board, *, name: str, canvas: dict, nodes: list,
             edges: list) -> Board:
    before = board.doc
    _check_new_items(session, {n["item_id"] for n in nodes if n["type"] == "item"},
                     set(item_ids_of(before)))
    candidate = {"name": name, "canvas": canvas, "nodes": nodes, "edges": edges}
    if all(before.get(k) == v for k, v in candidate.items()):
        session.rollback()  # nothing changed: no new version
        return get_board(session, board.id)
    _size(nodes, DOC_JSON_MAX, "the board")
    board.version += 1
    board.modified_at = datetime.now(UTC)
    board.name = name
    board.doc = _doc(board, name=name, canvas=canvas, nodes=nodes, edges=edges)
    _set_items(session, board)
    record(session, board.id, Kind.UPDATE, before, board.doc)
    return _commit(session, board)


def save_board(session: Session, board_id: str, *, base_version: int, name: str,
               canvas: dict | None, nodes: list, edges: list) -> Board:
    """PUT: replace the whole document."""
    access.require(access.Action.EDIT)
    board = get_board(session, board_id, for_update=True)
    _check_base(session, board, base_version)
    nodes, edges, canvas = clean_content(nodes, edges, canvas if canvas is not None
                                         else board.doc.get("canvas"))
    return _replace(session, board, name=_name(name), canvas=canvas, nodes=nodes, edges=edges)


def _merge(target: dict, patch: dict) -> dict:
    """JSON merge patch (RFC 7386): null removes a key, objects merge."""
    out = dict(target)
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def patch_board(session: Session, board_id: str, ops: dict) -> Board:
    """PATCH: node and edge ops against the current document."""
    access.require(access.Action.EDIT)
    board = get_board(session, board_id, for_update=True)
    _check_base(session, board, ops.get("base_version"))
    doc = board.doc
    nodes = {n["id"]: dict(n) for n in doc.get("nodes") or []}
    order = [n["id"] for n in doc.get("nodes") or []]
    edges = {e["id"]: dict(e) for e in doc.get("edges") or []}
    eorder = [e["id"] for e in doc.get("edges") or []]

    removed = set(ops.get("remove") or [])
    for nid in removed:
        if nid not in nodes:
            raise validation.ValidationError(f"no node {nid} on the board")
        del nodes[nid]
    order = [i for i in order if i in nodes]
    for n in nodes.values():
        if n.get("children"):
            n["children"] = [c for c in n["children"] if c not in removed]
    for eid in [e for e, v in edges.items() if v["from"] in removed or v["to"] in removed]:
        del edges[eid]
    for upd in ops.get("update") or []:
        if not isinstance(upd, dict) or upd.get("id") not in nodes:
            raise validation.ValidationError(f"no node {(upd or {}).get('id')} on the board")
        nodes[upd["id"]] = _merge(nodes[upd["id"]], {k: v for k, v in upd.items() if k != "id"})
    top = max((n.get("z", 0) for n in nodes.values()), default=0)
    for i, raw in enumerate(ops.get("add") or []):
        if not isinstance(raw, dict):
            raise validation.ValidationError("every node must be an object")
        node = dict(raw)
        node.setdefault("id", new_ulid())
        node.setdefault("z", top + 1 + i)
        if node["id"] in nodes:
            raise validation.ValidationError(f"node {node['id']} is already on the board")
        nodes[node["id"]] = node
        order.append(node["id"])
    for eid in ops.get("edges_remove") or []:
        edges.pop(eid, None)
    eorder = [e for e in eorder if e in edges]
    for upd in ops.get("edges_update") or []:
        if not isinstance(upd, dict) or upd.get("id") not in edges:
            raise validation.ValidationError(f"no edge {(upd or {}).get('id')} on the board")
        edges[upd["id"]] = _merge(edges[upd["id"]], {k: v for k, v in upd.items() if k != "id"})
    for raw in ops.get("edges_add") or []:
        edge = dict(raw) if isinstance(raw, dict) else raw
        if isinstance(edge, dict):
            edge.setdefault("id", new_ulid())
            if edge["id"] in edges:
                raise validation.ValidationError(f"edge {edge['id']} is already on the board")
            edges[edge["id"]] = edge
            eorder.append(edge["id"])
        else:
            raise validation.ValidationError("every edge must be an object")
    canvas = doc.get("canvas")
    if ops.get("canvas") is not None:
        canvas = _merge(canvas or {}, ops["canvas"])
    new_nodes, new_edges, canvas = clean_content(
        [nodes[i] for i in order], [edges[e] for e in eorder], canvas
    )
    name = _name(ops["name"]) if ops.get("name") is not None else board.name
    return _replace(session, board, name=name, canvas=canvas, nodes=new_nodes, edges=new_edges)


def add_items(session: Session, board_id: str, item_ids: list[str], layout: str = "grid",
              base_version: int | None = None) -> Board:
    """Lay items out below what is on the board (the MCP add_to_board)."""
    board = get_board(session, board_id)
    nodes = layout_nodes(session, item_ids, layout, board.doc.get("nodes") or [])
    return patch_board(session, board_id, {"base_version": base_version, "add": nodes})


def duplicate_board(session: Session, board_id: str, name: str | None = None) -> Board:
    src = get_board(session, board_id)
    return create_board(
        session, name or f"{src.name} copy"[:NAME_MAX],
        canvas={**DEFAULT_CANVAS, **(src.doc.get("canvas") or {})},  # the copy keeps its colour
        nodes=src.doc.get("nodes") or [], edges=src.doc.get("edges") or [],
    )


def trash_board(session: Session, board_id: str) -> Board:
    access.require(access.Action.EDIT)
    board = get_board(session, board_id, for_update=True)
    if board.deleted_at is not None:
        session.rollback()
        return board
    board.deleted_at = datetime.now(UTC)
    board.doc = {**board.doc, "deleted_at": _iso(board.deleted_at)}
    record(session, board.id, Kind.TRASH, {"state": "active"}, {"state": "trashed"})
    return _commit(session, board)


def restore_board(session: Session, board_id: str) -> Board:
    access.require(access.Action.EDIT)
    board = get_board(session, board_id, for_update=True)
    if board.deleted_at is None:
        session.rollback()
        return board
    board.deleted_at = None
    board.doc = {**board.doc, "deleted_at": None}
    record(session, board.id, Kind.RESTORE, {"state": "trashed"}, {"state": "active"})
    return _commit(session, board)


def purge_board(session: Session, board_id: str) -> None:
    """trashed -> gone (the board's history moves to .kist/board-events-purged.jsonl)."""
    board = get_board(session, board_id)
    if board.deleted_at is None:
        raise TransitionError("Only a board in the trash can be deleted permanently")
    meta_svc.advisory_lock(session, f"board:{board.id}")
    record(session, board.id, Kind.PURGE, board.doc, None)
    session.delete(board)
    session.commit()
    _keep_purged_history(board_id)
    shutil.rmtree(board_dir(board_id), ignore_errors=True)


def purge_expired(session: Session, days: int | None = None,
                  now: datetime | None = None) -> list[str]:
    """Boards trashed longer than KIST_TRASH_RETENTION_DAYS ago (the same
    clock as items, services/retention.py)."""
    from . import retention

    days = get_settings().trash_retention_days if days is None else days
    if days <= 0:
        return []
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=days)
    if retention.started_at(now) > cutoff:
        return []
    ids = session.execute(
        sa.select(Board.id).where(Board.deleted_at.isnot(None), Board.deleted_at < cutoff)
        .order_by(Board.deleted_at).limit(200)
    ).scalars().all()
    purged = []
    with events.acting("worker", "worker"):
        for board_id in ids:
            try:
                purge_board(session, board_id)
                purged.append(board_id)
            except Exception:
                session.rollback()
                log.exception("retention: could not purge board %s", board_id)
    return purged


def _commit(session: Session, board: Board) -> Board:
    board_id = board.id
    session.commit()
    sync_file(session, board_id)
    return get_board(session, board_id)


# ---------- layout ----------


def _fit(item: Item, box: float) -> tuple[float, float]:
    w, h = item.width or 1, item.height or 1
    s = box / max(w, h)
    return max(1.0, w * s), max(1.0, h * s)


def layout_nodes(session: Session, item_ids: list[str], layout: str,
                 existing: list[dict]) -> list[dict]:
    """Item nodes for `item_ids`, laid out in a grid or masonry columns
    below the existing nodes."""
    if layout not in ("grid", "masonry"):
        raise validation.ValidationError("layout is grid or masonry")
    if len(item_ids) > 1000:
        raise validation.ValidationError("at most 1000 items at once")
    items = [items_svc.get_item(session, i) for i in dict.fromkeys(item_ids)]
    top = max((n["y"] + n["h"] for n in existing), default=-GAP) + GAP * 2
    left = min((n["x"] for n in existing), default=0.0)
    z0 = max((n.get("z", 0) for n in existing), default=0) + 1
    cols = max(1, math.ceil(math.sqrt(len(items))))
    box = CELL - GAP
    out: list[dict] = []
    if layout == "grid":
        for i, item in enumerate(items):
            w, h = _fit(item, box)
            r, c = divmod(i, cols)
            out.append({
                "id": new_ulid(), "type": "item", "item_id": item.id,
                "x": left + c * CELL + (box - w) / 2, "y": top + r * CELL + (box - h) / 2,
                "w": w, "h": h, "rot": 0.0, "z": z0 + i,
            })
    else:
        heights = [top] * cols
        for i, item in enumerate(items):
            iw, ih = item.width or 1, item.height or 1
            w, h = float(box), box * ih / iw
            c = heights.index(min(heights))
            out.append({
                "id": new_ulid(), "type": "item", "item_id": item.id,
                "x": left + c * CELL, "y": heights[c], "w": w, "h": max(1.0, h),
                "rot": 0.0, "z": z0 + i,
            })
            heights[c] += max(1.0, h) + GAP
    return out


# ---------- export ----------


def _exists(session: Session):
    cache: dict[str, bool] = {}

    def exists(item_id: str) -> bool:
        if item_id not in cache:
            cache[item_id] = bool(item_id) and session.get(Item, item_id) is not None
        return cache[item_id]

    return exists


def export(session: Session, board_id: str, fmt: str, scale: float = 1.0,
           max_pixels: int | None = None) -> tuple[bytes, str, str]:
    """(body, media type, file name) for png | pdf | html."""
    from . import board_render as render

    board = get_board(session, board_id)
    doc = board.doc
    exists = _exists(session)
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in board.name).strip() or "board"
    if not (0.05 <= scale <= 8):
        raise validation.ValidationError("scale is from 0.05 to 8")
    limit = min(max_pixels or render.MAX_PIXELS, render.MAX_PIXELS)
    from . import credits as credits_svc  # F4: a credits band/footer

    if fmt == "png":
        img = render.render(doc, scale, exists, limit)
        img = credits_svc.board_footer(session, doc, img, scale)
        return render.png_bytes(img), "image/png", f"{safe}.png"
    if fmt == "pdf":
        img = render.render(doc, scale, exists, limit)
        img = credits_svc.board_footer(session, doc, img, scale)
        return render.pdf_bytes(img, render.fit_scale(doc, scale, limit)), "application/pdf", (
            f"{safe}.pdf")
    if fmt == "html":
        page = render.render_html(doc, exists, board.name).replace(
            "</body>", credits_svc.board_html_credits(session, doc) + "</body>", 1)
        return (page.encode(), "text/html; charset=utf-8", f"{safe}.html")
    raise validation.ValidationError("format is png, pdf or html")


# ---------- the durable copy ----------


def sync_file(session: Session, board_id: str) -> bool:
    """Write board.json (and thumb.webp) from the committed row. Call after
    commit; never raises (verify reports a failed write)."""
    from . import board_render as render

    try:
        meta_svc.advisory_lock(session, f"board:{board_id}")
        board = session.get(Board, board_id, populate_existing=True)
        if board is None:
            session.commit()
            return False
        doc = board.doc
        d = board_dir(board_id)
        d.mkdir(parents=True, exist_ok=True)
        meta_svc.atomic_write_text(d / FILE, json.dumps(doc, indent=2, ensure_ascii=False))
        try:
            img = render.snapshot(doc, _exists(session))
            tmp = d / f".{THUMB}.tmp"
            img.save(tmp, "WEBP", quality=80)
            os.replace(tmp, d / THUMB)
        except Exception:
            log.exception("board %s: thumb.webp not rendered", board_id)
        session.commit()
        return True
    except Exception:
        session.rollback()
        log.exception("board.json for %s not written (kist rewrite-meta writes it)", board_id)
        meta_svc.note_pending(f"board:{board_id}")
        return False


def thumb_path(session: Session, board_id: str) -> Path:
    board = get_board(session, board_id)
    path = board_dir(board.id) / THUMB
    if not path.is_file():
        sync_file(session, board.id)
    return path


def read_board_file(path: Path) -> dict:
    doc = meta_svc.read_json(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("id"), str):
        raise ValueError("not a board document")
    return doc


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def restore(session: Session) -> tuple[int, int, int]:
    """rebuild-index: boards from boards/*/board.json the database lacks,
    then their history from events.jsonl. Returns (boards, events, problems)."""
    restored = problems = 0
    root = boards_dir()
    paths = sorted(root.glob(f"*/{FILE}")) if root.is_dir() else []
    for path in paths:
        try:
            doc = read_board_file(path)
            if doc["id"] != path.parent.name:
                raise ValueError("id doesn't match its folder")
            if session.get(Board, doc["id"]) is not None:
                continue
            nodes, edges, canvas = clean_content(doc.get("nodes") or [], doc.get("edges") or [],
                                                 doc.get("canvas"))
            board = Board(
                id=doc["id"], name=_name(doc.get("name") or "Board"),
                created_at=_parse_dt(doc.get("created_at")) or datetime.now(UTC),
                modified_at=_parse_dt(doc.get("modified_at")) or datetime.now(UTC),
                version=int(doc.get("version") or 1),
                deleted_at=_parse_dt(doc.get("deleted_at")),
            )
            board.doc = {**doc, "nodes": nodes, "edges": edges, "canvas": canvas}
            session.add(board)
            session.flush()
            _set_items(session, board)
            session.commit()
            restored += 1
        except Exception as exc:
            session.rollback()
            log.warning("%s: board not restored: %s", path, exc)
            problems += 1
    n_events, bad = restore_events(session)
    return restored, n_events, problems + bad


# ---------- events ----------

_PENDING = "kist_board_events_pending"
_LINES = "kist_board_event_lines"


def record(session: Session, board_id: str, kind: str, before, after) -> BoardEvent:
    ctx = events.context()
    ev = BoardEvent(board_id=board_id, at=datetime.now(UTC), actor=ctx.actor,
                    source=ctx.source, batch_id=ctx.batch_id, kind=kind,
                    before=before, after=after)
    session.add(ev)
    session.info.setdefault(_PENDING, []).append(ev)
    return ev


def event_line(ev: BoardEvent) -> dict:
    return {"id": ev.id, "board_id": ev.board_id, "at": _iso(ev.at), "actor": ev.actor,
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


def purged_file() -> Path:
    return get_settings().state_dir / PURGED_EVENTS_FILE


def events_file(board_id: str) -> Path:
    return board_dir(board_id) / EVENTS_FILE


def _append(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write("".join(json.dumps(x, ensure_ascii=False, default=str) + "\n" for x in lines))
        f.flush()
        os.fsync(f.fileno())


def write_lines(lines: list[dict]) -> None:
    by_path: dict[Path, list[dict]] = {}
    for line in lines:
        path = purged_file() if line["kind"] == Kind.PURGE else events_file(line["board_id"])
        by_path.setdefault(path, []).append(line)
    for path, chunk in by_path.items():
        try:
            _append(path, chunk)
        except OSError:
            log.exception("board events not written to %s (kist rewrite-meta writes them)", path)
            meta_svc.note_pending(f"board-events:{chunk[0]['board_id']}")


def _keep_purged_history(board_id: str) -> None:
    path = events_file(board_id)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    if text.strip():
        try:
            target = purged_file()
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as f:
                f.write(text if text.endswith("\n") else text + "\n")
        except OSError:
            log.exception("history of purged board %s not kept on disk", board_id)


def rewrite_files(session: Session) -> tuple[int, int]:
    """rewrite-meta: every board.json, thumb.webp and events.jsonl from the
    database. Returns (written, failed)."""
    written = failed = 0
    for board_id in session.execute(sa.select(Board.id).order_by(Board.id)).scalars().all():
        if not sync_file(session, board_id):
            failed += 1
            continue
        rows = session.execute(sa.select(BoardEvent).where(BoardEvent.board_id == board_id)
                               .order_by(BoardEvent.id)).scalars().all()
        meta_svc.atomic_write_text(events_file(board_id), "".join(
            json.dumps(event_line(e), ensure_ascii=False, default=str) + "\n" for e in rows))
        written += 1
    return written, failed


def restore_events(session: Session) -> tuple[int, int]:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    root = boards_dir()
    paths = [purged_file()] + (sorted(root.glob(f"*/{EVENTS_FILE}")) if root.is_dir() else [])
    restored = bad = 0
    for path in paths:
        lines, unreadable = events.read_file_lines(path)
        bad += unreadable
        rows = []
        for line in lines:
            try:
                rows.append({
                    "id": int(line["id"]), "board_id": str(line["board_id"]),
                    "at": (datetime.fromisoformat(line["at"]) if line.get("at")
                           else datetime.now(UTC)),
                    "actor": str(line.get("actor") or "unknown"),
                    "source": str(line.get("source") or "api"),
                    "batch_id": str(line.get("batch_id") or line["id"]),
                    "kind": str(line["kind"]), "before": line.get("before"),
                    "after": line.get("after"),
                })
            except (KeyError, TypeError, ValueError):
                bad += 1
        if rows:
            result = session.execute(
                pg_insert(BoardEvent).values(rows).on_conflict_do_nothing(index_elements=["id"])
            )
            restored += result.rowcount or 0
    session.execute(sa.text(
        "SELECT setval(pg_get_serial_sequence('board_events', 'id'), "
        "GREATEST((SELECT COALESCE(MAX(id), 0) FROM board_events), 1), "
        "(SELECT COUNT(*) > 0 FROM board_events))"
    ))
    session.commit()
    return restored, bad


def check_files(session: Session) -> list[str]:
    """verify: board.json matches each board row; events.jsonl has every
    event; a board folder has a row."""
    problems: list[str] = []
    ids = set()
    for board in session.execute(sa.select(Board).order_by(Board.id)).scalars():
        ids.add(board.id)
        path = board_dir(board.id) / FILE
        if not path.is_file():
            problems.append(f"board {board.id}: board.json missing")
            continue
        try:
            disk = read_board_file(path)
        except (ValueError, OSError):
            problems.append(f"board {board.id}: board.json unreadable")
            continue
        if json.loads(json.dumps(board.doc)) != disk:
            problems.append(f"board {board.id}: board.json differs from the database "
                            f"(version {disk.get('version')} on disk, "
                            f"{board.version} in the database)")
        expected = set(session.execute(sa.select(BoardItem.item_id)
                                       .where(BoardItem.board_id == board.id)).scalars())
        have = set(session.execute(sa.select(Item.id).where(Item.id.in_(item_ids_of(board.doc))))
                   .scalars()) if item_ids_of(board.doc) else set()
        if expected != have:
            problems.append(f"board {board.id}: board_items out of step with its nodes")
    counts = dict(session.execute(
        sa.select(BoardEvent.board_id, sa.func.count()).join(Board, Board.id == BoardEvent.board_id)
        .group_by(BoardEvent.board_id)).all())
    for board_id, n in sorted(counts.items()):
        lines, bad = events.read_file_lines(events_file(board_id))
        if len({line["id"] for line in lines}) < n or bad:
            problems.append(f"board {board_id}: events.jsonl has {len(lines)} of {n} events")
    root = boards_dir()
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir() and not d.name.startswith(".") and d.name not in ids:
                problems.append(f"board {d.name}: folder on disk with no database row "
                                "(kist rebuild-index restores it)")
    return problems


# ---------- undo (events.UNDO_SOURCES) ----------


class _BoardUndo(events.UndoSource):
    def find(self, session: Session, batch_id: str) -> list:
        return list(session.execute(
            sa.select(BoardEvent).where(BoardEvent.batch_id == batch_id)
        ).scalars())

    def batches_select(self):
        return sa.select(BoardEvent.batch_id, BoardEvent.actor, BoardEvent.source,
                         BoardEvent.at, sa.literal("board_").concat(BoardEvent.kind))

    def revert(self, session: Session, evs: list, exclude: set[str]) -> tuple[int, list[dict]]:
        undone = 0
        skipped: list[dict] = []
        for ev in sorted(evs, key=lambda e: e.id, reverse=True):
            later = session.execute(
                sa.select(sa.func.count()).select_from(BoardEvent).where(
                    BoardEvent.board_id == ev.board_id, BoardEvent.id > ev.id,
                    BoardEvent.batch_id.not_in(sorted(exclude)),
                )
            ).scalar_one()

            def skip(reason: str, ev=ev) -> None:
                skipped.append({"item_id": ev.board_id, "event_id": ev.id, "reason": reason,
                                "target": "board"})

            if ev.kind == Kind.PURGE:
                skip("it was permanently deleted; only a backup can bring it back")
                continue
            if later:
                skip("the board was changed later")
                continue
            try:
                if ev.kind == Kind.CREATE or ev.kind == Kind.RESTORE:
                    trash_board(session, ev.board_id)
                elif ev.kind == Kind.TRASH:
                    restore_board(session, ev.board_id)
                elif ev.kind == Kind.UPDATE:
                    before = ev.before or {}
                    board = get_board(session, ev.board_id)
                    save_board(session, ev.board_id, base_version=board.version,
                               name=before.get("name") or board.name,
                               canvas=before.get("canvas"), nodes=before.get("nodes") or [],
                               edges=before.get("edges") or [])
                else:
                    skip(f"board events of kind {ev.kind!r} can't be undone")
                    continue
            except NotFound:
                session.rollback()
                skip("the board no longer exists")
                continue
            except (TransitionError, Conflict, validation.ValidationError,
                    items_svc.NotFound, access.Forbidden) as exc:
                session.rollback()
                skip(str(exc))
                continue
            undone += 1
        return undone, skipped


events.UNDO_SOURCES.append(_BoardUndo())
