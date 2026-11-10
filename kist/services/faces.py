"""Faces and people (opt-in: KIST_FACES=1 on the app, worker and ml service).

The worker sends each image's 2048 px screen copy (or its largest
thumbnail) to the ml service's /faces (YuNet + SFace, kist/ml/faces.py) as
a `faces` job. Results go to disk first, then the database:

    items/<id>/ai/faces.json   {model, faces: [{box: {x, y, w, h},
                                landmarks, score, embedding}], ...}
                               boxes and landmarks as fractions of the image

A face's `idx` is its position in that file, so "<item id>:<idx>" names a
face across rebuilds. People live in library/.kist/people.json: each with
an id, a name (or none) and the faces that belong to them; rebuild-index
reads both files and recomputes nothing.

Who is who:
- a new face joins the person of its nearest named-or-not neighbour when
  they are close enough (ASSIGN_COSINE);
- `cluster_unassigned` (the worker, every few minutes while faces arrive)
  groups the faces still without a person into new, unnamed people
  (connected components at CLUSTER_COSINE, MIN_FACES or more faces).
The thresholds sit above OpenCV's published same-person cosine for SFace
(0.363) so that chains of look-alikes don't merge two people.
"""

import io
import json
import logging
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .. import __version__, access
from ..config import get_settings
from ..ids import new_ulid
from ..models import Face, HalfVec, Item, ItemFolder, Person
from . import meta as meta_svc
from . import validation

log = logging.getLogger("kist.faces")

FILE = "faces.json"
PEOPLE_FILE = "people.json"
ASSIGN_COSINE = 0.42
CLUSTER_COSINE = 0.48
MIN_FACES = 2
NAME_MAX = 200
DIM = 128


class NotFound(Exception):
    def __init__(self, what: str):
        super().__init__(f"{what} not found")


def enabled() -> bool:
    return bool(get_settings().faces)


# ---------- the ml service ----------


def ml_faces(data: bytes) -> dict:
    from .ai import _post

    return _post("/faces", content=data, headers={"Content-Type": "application/octet-stream"})


def source(item: Item):
    """The image to look for faces in: the 2048 px screen copy, else the
    largest thumbnail. None for items without image previews."""
    from ..filetypes import kind_for
    from ..lifecycle import PreviewStatus
    from .thumbs import SCREEN_FILE

    if kind_for(item.mime, item.ext) != "image" or item.preview_status != PreviewStatus.READY:
        return None
    d = get_settings().items_dir / item.id
    for name in (SCREEN_FILE, "thumb_1024.webp", "thumb_512.webp"):
        if (d / name).is_file():
            return d / name
    return None


# ---------- sidecar ----------


def sidecar_path(item_id: str):
    from .ai import ai_dir

    return ai_dir(item_id) / FILE


def _clean_face(f: dict, width: float, height: float) -> dict:
    x, y, w, h = (float(v) for v in f["box"])
    emb = [float(v) for v in f["embedding"]]
    if len(emb) != DIM:
        raise ValueError(f"a face embedding has {len(emb)} values, not {DIM}")
    return {
        "box": {"x": round(x / width, 5), "y": round(y / height, 5),
                "w": round(w / width, 5), "h": round(h / height, 5)},
        "landmarks": [[round(float(px) / width, 5), round(float(py) / height, 5)]
                      for px, py in f.get("landmarks") or []],
        "score": round(float(f.get("score") or 0.0), 4),
        "embedding": emb,
    }


def read_sidecar(item_id: str) -> dict | None:
    path = sidecar_path(item_id)
    if not path.is_file():
        return None
    data = meta_svc.read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("faces"), list):
        raise ValueError(f"{FILE} has no faces list")
    return data


def _iou(a: dict, b: dict) -> float:
    ax2, ay2, bx2, by2 = a["x"] + a["w"], a["y"] + a["h"], b["x"] + b["w"], b["y"] + b["h"]
    iw = max(0.0, min(ax2, bx2) - max(a["x"], b["x"]))
    ih = max(0.0, min(ay2, by2) - max(a["y"], b["y"]))
    inter = iw * ih
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def _insert_rows(session: Session, item_id: str, data: dict,
                 people: dict[int, str | None] | None = None) -> list[int]:
    from .suggestions import halfvec

    ids = []
    for idx, f in enumerate(data["faces"]):
        box = {**f["box"], "landmarks": f.get("landmarks") or []}
        face = Face(item_id=item_id, idx=idx, box=box, score=float(f.get("score") or 0),
                    model=str(data.get("model") or "faces"),
                    embedding=halfvec(f["embedding"]),
                    person_id=(people or {}).get(idx))
        session.add(face)
        session.flush()
        ids.append(face.id)
    return ids


def store(session: Session, item_id: str, result: dict) -> bool:
    """A /faces result for an item: sidecar, then rows. A re-run keeps each
    face's person when the new box sits where an old one was. False when
    the item is gone."""
    from .ai import _item_exists_on_disk

    if session.get(Item, item_id) is None or not _item_exists_on_disk(item_id):
        return False
    width, height = float(result.get("width") or 0), float(result.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("a faces result needs the image's width and height")
    faces = [_clean_face(f, width, height) for f in result.get("faces") or []]
    data = {"model": str(result.get("model") or "faces"), "width": int(width),
            "height": int(height), "faces": faces,
            "created_at": datetime.now(UTC).isoformat(), "kist_version": __version__}
    meta_svc.atomic_write_text(sidecar_path(item_id), json.dumps(data, ensure_ascii=False))
    old = session.execute(sa.select(Face).where(Face.item_id == item_id)).scalars().all()
    keep: dict[int, str | None] = {}
    for idx, f in enumerate(faces):
        best = max(old, key=lambda o: _iou(o.box, f["box"]), default=None)
        if best is not None and best.person_id and _iou(best.box, f["box"]) >= 0.5:
            keep[idx] = best.person_id
    for o in old:
        session.delete(o)
    session.flush()
    new_ids = _insert_rows(session, item_id, data, keep)
    session.commit()
    assign(session, [i for i, idx in zip(new_ids, range(len(faces)), strict=True)
                     if idx not in keep])
    sync_people_file(session)
    return True


def load_sidecar(session: Session, item_id: str) -> int:
    """rebuild-index: rows from ai/faces.json when the item has none."""
    if session.execute(sa.select(Face.id).where(Face.item_id == item_id).limit(1)).first():
        return 0
    data = read_sidecar(item_id)
    if data is None or not data["faces"]:
        return 0
    n = len(_insert_rows(session, item_id, data))
    session.commit()
    return n


# ---------- people ----------


def _nearest(session: Session, vec, exclude_item: str | None = None, k: int = 5):
    distance = Face.embedding.op("<=>", return_type=sa.Float)(
        sa.cast(sa.bindparam("q", vec, type_=HalfVec()), HalfVec(DIM)))
    stmt = (sa.select(Face.person_id, distance).where(Face.person_id.isnot(None))
            .order_by(distance).limit(k))
    if exclude_item:
        stmt = stmt.where(Face.item_id != exclude_item)
    return [(pid, 1.0 - float(d)) for pid, d in session.execute(stmt).all()]


def assign(session: Session, face_ids: list[int]) -> int:
    """Give new faces the person of their nearest close-enough face.
    Two faces in one picture are never the same person."""
    n = 0
    for fid in face_ids:
        face = session.get(Face, fid)
        if face is None or face.person_id:
            continue
        vec = session.execute(sa.select(Face.embedding).where(Face.id == fid)).scalar_one()
        taken = set(session.execute(
            sa.select(Face.person_id).where(Face.item_id == face.item_id,
                                            Face.person_id.isnot(None))
        ).scalars())
        for pid, sim in _nearest(session, vec, exclude_item=face.item_id):
            if sim < ASSIGN_COSINE:
                break
            if pid not in taken:
                face.person_id = pid
                n += 1
                break
    session.commit()
    return n


def cluster_unassigned(session: Session) -> int:
    """New unnamed people from faces without one: connected components of
    the faces at CLUSTER_COSINE or closer, with MIN_FACES or more faces.
    Returns how many people were made."""
    import numpy as np

    rows = session.execute(
        sa.select(Face.id, Face.item_id, Face.embedding).where(Face.person_id.is_(None))
        .order_by(Face.id)
    ).all()
    if len(rows) < MIN_FACES:
        return 0
    ids = [r[0] for r in rows]
    items = [r[1] for r in rows]
    x = np.asarray([r[2] for r in rows], dtype=np.float32)
    x /= np.linalg.norm(x, axis=1, keepdims=True) + 1e-12
    parent = list(range(len(ids)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for start in range(0, len(ids), 2048):
        sims = x[start:start + 2048] @ x.T
        for i, j in zip(*np.nonzero(sims >= CLUSTER_COSINE), strict=True):
            gi = start + int(i)
            j = int(j)
            if gi < j and items[gi] != items[j]:
                parent[find(gi)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(ids)):
        groups.setdefault(find(i), []).append(i)
    made = 0
    for members in groups.values():
        if len(members) < MIN_FACES:
            continue
        # one face per picture: the best-scoring if a group holds two
        person = Person(id=new_ulid(), name=None)
        session.add(person)
        session.flush()
        seen_items: set[str] = set()
        for i in members:
            if items[i] in seen_items:
                continue
            seen_items.add(items[i])
            session.get(Face, ids[i]).person_id = person.id
        if len(seen_items) < MIN_FACES:
            session.delete(person)
            for i in members:
                session.get(Face, ids[i]).person_id = None
            continue
        person.cover_face_id = _best_face(session, person.id)
        made += 1
    session.commit()
    if made:
        sync_people_file(session)
    return made


def _best_face(session: Session, person_id: str) -> int | None:
    rows = session.execute(
        sa.select(Face.id, Face.box, Face.score).where(Face.person_id == person_id)
    ).all()
    if not rows:
        return None
    return max(rows, key=lambda r: (r.score * (r.box.get("w", 0) * r.box.get("h", 0)) ** 0.25,
                                    -r.id)).id


def _visible_faces_stmt():
    stmt = sa.select(Face).join(Item, Item.id == Face.item_id).where(Item.deleted_at.is_(None))
    reader = access.restricted()
    if reader is not None:
        stmt = stmt.where(sa.exists().where(
            ItemFolder.item_id == Item.id,
            ItemFolder.folder_id.in_(sorted(reader.visible_folders) or [""]),
        ))
    return stmt


def face_out(face: Face) -> dict:
    return {"id": face.id, "item_id": face.item_id, "idx": face.idx,
            "box": {k: face.box.get(k) for k in ("x", "y", "w", "h")},
            "score": face.score, "person_id": face.person_id,
            "thumb_url": f"/api/faces/{face.id}/thumb"}


def list_people(session: Session) -> list[dict]:
    """People with at least one face on an item the caller can see; counts
    and the cover count only those faces. Named people first."""
    faces = session.execute(_visible_faces_stmt().where(Face.person_id.isnot(None))).scalars()
    by: dict[str, list[Face]] = {}
    for f in faces:
        by.setdefault(f.person_id, []).append(f)
    out = []
    for p in session.execute(sa.select(Person).where(Person.id.in_(list(by)))).scalars():
        fs = by[p.id]
        cover = next((f for f in fs if f.id == p.cover_face_id), None) or max(
            fs, key=lambda f: (f.score, -f.id))
        out.append({"id": p.id, "name": p.name, "face_count": len(fs),
                    "item_count": len({f.item_id for f in fs}), "cover": face_out(cover)})
    out.sort(key=lambda p: (p["name"] is None, (p["name"] or "").lower(), -p["face_count"]))
    return out


def get_person(session: Session, person_id: str) -> Person:
    p = session.get(Person, person_id)
    if p is None:
        raise NotFound(f"person {person_id}")
    return p


def update_person(session: Session, person_id: str, *, name=..., cover_face_id=None) -> dict:
    p = get_person(session, person_id)
    if name is not ...:
        name = " ".join(str(name or "").split())
        if len(name) > NAME_MAX:
            raise validation.ValidationError(f"names are at most {NAME_MAX} characters")
        p.name = name or None
    if cover_face_id is not None:
        face = session.get(Face, cover_face_id)
        if face is None or face.person_id != p.id:
            raise validation.ValidationError("the cover must be one of this person's faces")
        p.cover_face_id = face.id
    session.commit()
    sync_people_file(session)
    return person_dict(session, p)


def person_dict(session: Session, p: Person) -> dict:
    found = [x for x in list_people(session) if x["id"] == p.id]
    if not found:
        raise NotFound(f"person {p.id}")
    return found[0]


def merge_people(session: Session, from_ids: list[str], into: str) -> dict:
    target = get_person(session, into)
    sources = [get_person(session, pid) for pid in dict.fromkeys(from_ids or []) if pid != into]
    if not sources:
        raise validation.ValidationError("name at least one other person to merge")
    for s in sources:
        session.execute(sa.update(Face).where(Face.person_id == s.id)
                        .values(person_id=target.id))
        if not target.name and s.name:
            target.name = s.name
        session.delete(s)
    session.flush()
    if target.cover_face_id is None:
        target.cover_face_id = _best_face(session, target.id)
    session.commit()
    sync_people_file(session)
    return person_dict(session, target)


def set_face_person(session: Session, face_id: int, person_id: str | None) -> dict:
    """Correct one face: move it to another person, or to nobody."""
    face = session.get(Face, face_id)
    if face is None:
        raise NotFound(f"face {face_id}")
    if person_id:
        get_person(session, person_id)
    face.person_id = person_id or None
    session.commit()
    sync_people_file(session)
    return face_out(face)


def item_faces(session: Session, item_id: str) -> list[dict]:
    from . import items as items_svc

    item = items_svc.get_item(session, item_id)
    faces = session.execute(
        sa.select(Face).where(Face.item_id == item.id).order_by(Face.idx)).scalars()
    names = dict(session.execute(sa.select(Person.id, Person.name)).all())
    return [{**face_out(f), "person_name": names.get(f.person_id)} for f in faces]


def face_thumb(session: Session, face_id: int, size: int = 160) -> bytes:
    """A square crop around a face (webp) from the item's previews."""
    from PIL import Image

    from . import items as items_svc

    face = session.get(Face, face_id)
    if face is None:
        raise NotFound(f"face {face_id}")
    item = items_svc.get_item(session, face.item_id)  # visibility
    src = source(item)
    if src is None:
        raise NotFound(f"a preview for face {face_id}")
    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        b = face.box
        cx, cy = (b["x"] + b["w"] / 2) * w, (b["y"] + b["h"] / 2) * h
        half = max(b["w"] * w, b["h"] * h) * 0.8
        crop = im.crop((round(cx - half), round(cy - half), round(cx + half), round(cy + half)))
        crop = crop.resize((size, size), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        crop.save(buf, "WEBP", quality=85)
    return buf.getvalue()


# ---------- people.json ----------


def people_path():
    return get_settings().state_dir / PEOPLE_FILE


def people_snapshot(session: Session) -> dict:
    faces = session.execute(
        sa.select(Face.person_id, Face.item_id, Face.idx, Face.id)
        .where(Face.person_id.isnot(None)).order_by(Face.item_id, Face.idx)
    ).all()
    key_of = {fid: f"{item}:{idx}" for _, item, idx, fid in faces}
    by: dict[str, list[str]] = {}
    for pid, item, idx, _ in faces:
        by.setdefault(pid, []).append(f"{item}:{idx}")
    people = []
    for p in session.execute(sa.select(Person).order_by(Person.id)).scalars():
        people.append({"id": p.id, "name": p.name, "cover": key_of.get(p.cover_face_id),
                       "faces": by.get(p.id, [])})
    return {"version": 1, "people": people}


def sync_people_file(session: Session) -> bool:
    try:
        meta_svc.advisory_lock(session, PEOPLE_FILE)
        data = people_snapshot(session)
        if data["people"] or people_path().is_file():
            meta_svc.atomic_write_text(people_path(), json.dumps(data, ensure_ascii=False))
        session.commit()
        return True
    except Exception:
        session.rollback()
        log.exception("people.json not written (kist rewrite-meta writes it)")
        meta_svc.note_pending(PEOPLE_FILE)
        return False


def restore_people(session: Session) -> tuple[int, str | None]:
    """rebuild-index (after the faces): people and who is who, from
    people.json. Returns (people restored, problem)."""
    path = people_path()
    if not path.is_file():
        return 0, None
    try:
        data = meta_svc.read_json(path)
        people = data.get("people")
        if not isinstance(people, list):
            raise ValueError("people must be a list")
    except (OSError, ValueError, AttributeError) as exc:
        return 0, str(exc)
    face_id = {f"{item}:{idx}": fid for fid, item, idx in session.execute(
        sa.select(Face.id, Face.item_id, Face.idx)).all()}
    n = 0
    for p in people:
        if not isinstance(p, dict) or not isinstance(p.get("id"), str):
            continue
        person = session.get(Person, p["id"])
        if person is None:
            person = Person(id=p["id"], name=p.get("name") or None)
            session.add(person)
            session.flush()
            n += 1
        for key in p.get("faces") or []:
            if key in face_id:
                session.get(Face, face_id[key]).person_id = person.id
        person.cover_face_id = face_id.get(p.get("cover") or "")
    session.commit()
    return n, None


def check(session: Session) -> list[str]:
    """verify: face rows match their sidecars, people.json matches."""
    problems = []
    counts = dict(session.execute(
        sa.select(Face.item_id, sa.func.count()).group_by(Face.item_id)).all())
    for item_id, n in sorted(counts.items()):
        try:
            data = read_sidecar(item_id)
        except (OSError, ValueError):
            data = None
        if data is None or len(data["faces"]) != n:
            problems.append(f"{item_id}: {n} faces in the database but ai/{FILE} "
                            "doesn't list them")
    settings = get_settings()
    if settings.items_dir.is_dir():
        for path in sorted(settings.items_dir.glob(f"*/ai/{FILE}")):
            item_id = path.parent.parent.name
            try:
                data = read_sidecar(item_id)
            except (OSError, ValueError):
                problems.append(f"{item_id}: ai/{FILE} unreadable")
                continue
            if data and data["faces"] and item_id not in counts:
                problems.append(f"{item_id}: ai/{FILE} is not in the database "
                                "(kist rebuild-index loads it)")
    snap = people_snapshot(session)
    if snap["people"] or people_path().is_file():
        try:
            disk = meta_svc.read_json(people_path())
        except (OSError, ValueError):
            disk = None
        if disk is None:
            problems.append(f"{PEOPLE_FILE} missing or unreadable")
        elif sorted(map(json.dumps, disk.get("people") or [])) != sorted(
                map(json.dumps, snap["people"])):
            problems.append(f"{PEOPLE_FILE} differs from the database")
    return problems
