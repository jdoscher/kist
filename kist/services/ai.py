"""AI results: image embeddings and text found in items (OCR, PDF text).

Who computes what
-----------------
The worker sends `embed` and `ocr` jobs to the ml service (KIST_ML_URL), a
few at a time on threads of their own, so an import never waits behind
them. Embed jobs go in batches (KIST_AI_EMBED_BATCH per request to POST
/embed/images). `caption` jobs go to the caption provider
(services/captions.py). A `kist helper` on another machine can claim the same jobs over HTTP
(POST /api/ai/jobs/claim) and report results back; the server checks and
stores them exactly as if the worker had computed them.

Budget
------
- AI jobs only start when no ingest or preview job is waiting.
- Jobs queued by `kist ai-backfill` only start inside KIST_AI_WINDOW
  ("01:00-06:00", local time of the container; empty = any time). Jobs for
  newly added items run straight away: one item costs well under a second.
- A worker runs at most KIST_AI_CONCURRENCY AI jobs at once; the ml service
  runs at most KIST_ML_CONCURRENCY inferences at once whoever asks.

Files are the truth
-------------------
Every result is written next to the item before its database row:

    items/<id>/ai/embed-<model>.f16    raw little-endian float16 vector
    items/<id>/ai/embed-<model>.json   {model, dim, created_at, kist_version}
    items/<id>/ai/ocr.json             {model, lang, text, boxes?, source,
                                        created_at, kist_version}

`kist rebuild-index` loads these and never recomputes anything; `kist
verify` reports rows whose file is missing and files not in the database.
"""

import json
import logging
import math
import os
import re
import struct
import tempfile
import threading
import time
from datetime import UTC, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..filetypes import kind_for
from ..lifecycle import AI_KINDS, DEFAULT_AI_KINDS, JobKind, JobStatus, PreviewStatus
from ..ml.models import MODELS
from ..models import AiHelper, HalfVec, Item, ItemEmbedding, ItemText, Job
from . import meta as meta_svc

log = logging.getLogger("kist.ai")

AI_DIR = "ai"
OCR_FILE = "ocr.json"
PDF_TEXT_MODEL = "pdf-text-layer"
EMBED_SOURCES = ("thumb_512.webp", "thumb_1024.webp", "thumb_256.webp")
# EXIF Make / Model: an image a camera took isn't OCR'd
EXIF_MAKE, EXIF_MODEL = 0x010F, 0x0110
MODEL_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
# item_text sources that are text read from the item itself (a caption is
# the third source, written about it: services/captions.py)
READ_SOURCES = ("ocr", "pdf")
MAX_TEXT = 1_000_000


class AiUnavailable(RuntimeError):
    """The ml service isn't configured or can't be reached (HTTP 503)."""


class ResultRejected(ValueError):
    """A helper's result doesn't fit the job (HTTP 422)."""


# ---------- settings helpers ----------


def active_model() -> str:
    model = get_settings().ml_model
    if not MODEL_NAME.match(model):
        raise ValueError(f"KIST_ML_MODEL {model!r} isn't a valid model name")
    return model


def model_dim(model: str) -> int | None:
    spec = MODELS.get(model)
    return spec.dim if spec else None


def parse_window(window: str) -> tuple[dtime, dtime] | None:
    """'01:00-06:00' -> (start, end); '' -> None (any time)."""
    window = (window or "").strip()
    if not window:
        return None
    try:
        a, b = (part.strip() for part in window.split("-", 1))
        start = datetime.strptime(a, "%H:%M").time()
        end = datetime.strptime(b, "%H:%M").time()
    except ValueError:
        raise ValueError(
            f"KIST_AI_WINDOW {window!r} isn't HH:MM-HH:MM (e.g. 01:00-06:00)"
        ) from None
    return start, end


def window_open(window: str, now: datetime | None = None) -> bool:
    parsed = parse_window(window)
    if parsed is None:
        return True
    start, end = parsed
    t = (now or datetime.now()).time()
    if start == end:
        return True
    if start < end:
        return start <= t < end
    return t >= start or t < end  # wraps past midnight


# ---------- the ml service ----------

_transport: httpx.BaseTransport | None = None  # tests put a fake ml service here
_health_cache: tuple[float, dict | None] = (0.0, None)
_health_lock = threading.Lock()
_query_cache: dict[tuple[str, str], list[float]] = {}


def set_transport(transport: httpx.BaseTransport | None) -> None:
    global _transport, _health_cache
    _transport = transport
    _health_cache = (0.0, None)
    _query_cache.clear()


def _client(timeout: float | None = None) -> httpx.Client:
    settings = get_settings()
    if not settings.ml_url:
        raise AiUnavailable("no ml service is configured (KIST_ML_URL)")
    return httpx.Client(
        base_url=settings.ml_url.rstrip("/"),
        timeout=timeout or settings.ml_timeout_seconds,
        transport=_transport,
    )


def _post(path: str, **kwargs) -> dict:
    try:
        with _client() as c:
            r = c.post(path, **kwargs)
    except httpx.HTTPError as exc:
        raise AiUnavailable(f"the ml service can't be reached: {type(exc).__name__}") from exc
    if r.status_code >= 500:
        raise AiUnavailable(f"the ml service answered {r.status_code}: {r.text[:300]}")
    if r.status_code >= 400:
        raise ValueError(f"the ml service refused this input ({r.status_code}): {r.text[:300]}")
    return r.json()


def _checked_vector(out: dict) -> list[float]:
    model = active_model()
    if out.get("model") != model:
        raise AiUnavailable(
            f"the ml service runs {out.get('model')!r} but KIST_ML_MODEL is {model!r}; "
            "set the same KIST_ML_MODEL for the ml service, app and worker"
        )
    return out["vector"]


def ml_embed_image(data: bytes) -> list[float]:
    return _checked_vector(
        _post("/embed/image", content=data, headers={"Content-Type": "application/octet-stream"})
    )


def ml_embed_images(datas: list[bytes]) -> list[list[float] | str]:
    """Vectors for several images in one request (POST /embed/images); an
    image the ml service can't read gives its error message instead. An
    older ml service without the batch endpoint gets one request each."""
    if not datas:
        return []
    files = [("images", (f"{i}.img", d, "application/octet-stream"))
             for i, d in enumerate(datas)]
    try:
        with _client() as c:
            r = c.post("/embed/images", files=files)
    except httpx.HTTPError as exc:
        raise AiUnavailable(f"the ml service can't be reached: {type(exc).__name__}") from exc
    if r.status_code in (404, 405):
        out: list[list[float] | str] = []
        for d in datas:
            try:
                out.append(ml_embed_image(d))
            except ValueError as exc:
                out.append(str(exc))
        return out
    if r.status_code >= 500:
        raise AiUnavailable(f"the ml service answered {r.status_code}: {r.text[:300]}")
    if r.status_code >= 400:
        raise ValueError(f"the ml service refused this batch ({r.status_code}): {r.text[:300]}")
    body = r.json()
    _checked_vector({"model": body.get("model"), "vector": []})  # same model as ours?
    results = body.get("results") or []
    if len(results) != len(datas):
        raise AiUnavailable("the ml service answered for a different number of images")
    return [res["vector"] if "vector" in res else str(res.get("error") or "failed")
            for res in results]


def ml_embed_text(text: str) -> list[float]:
    return _checked_vector(_post("/embed/text", json={"text": text}))


def ml_ocr(data: bytes) -> dict:
    return _post("/ocr", content=data, headers={"Content-Type": "application/octet-stream"})


def ml_health(max_age: float = 15.0) -> dict | None:
    """The ml service's /health, cached briefly; None when it's unreachable."""
    global _health_cache
    if not get_settings().ml_url:
        return None
    with _health_lock:
        at, cached = _health_cache
        if time.monotonic() - at < max_age:
            return cached
    try:
        with _client(timeout=2.0) as c:
            r = c.get("/health")
        result = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        result = None
    with _health_lock:
        _health_cache = (time.monotonic(), result)
    return result


def has_embeddings(session: Session) -> bool:
    """Any vectors for the active model? (Semantic ordering is skipped, and
    the ml service not asked, until there are.)"""
    return session.execute(
        sa.select(ItemEmbedding.item_id).where(ItemEmbedding.model == active_model()).limit(1)
    ).first() is not None


def item_vector(session: Session, item_id: str, model: str) -> list[float]:
    """The stored embedding of an item the caller may see (more like this).
    ValidationError (422) when it has none yet."""
    from . import items as items_svc
    from .validation import ValidationError

    items_svc.get_item(session, item_id)  # NotFound for items the caller can't see
    vec = session.execute(
        sa.select(ItemEmbedding.vector).where(ItemEmbedding.item_id == item_id,
                                              ItemEmbedding.model == model)
    ).scalar_one_or_none()
    if vec is None:
        raise ValidationError(
            f"item {item_id} has no embedding yet, so there's nothing to compare it with"
        )
    return vec


def query_vector(text: str) -> list[float]:
    """A search phrase as a vector (the last few are remembered)."""
    key = (active_model(), text.strip().lower())
    if key in _query_cache:
        return _query_cache[key]
    vec = ml_embed_text(text.strip())
    if len(_query_cache) > 256:
        _query_cache.clear()
    _query_cache[key] = vec
    return vec


# ---------- sidecar files ----------


def ai_dir(item_id: str) -> Path:
    return get_settings().items_dir / item_id / AI_DIR


def embed_paths(item_id: str, model: str) -> tuple[Path, Path]:
    base = ai_dir(item_id) / f"embed-{model}"
    return base.with_name(base.name + ".f16"), base.with_name(base.name + ".json")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def pack_f16(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}e", *vec)


def unpack_f16(data: bytes) -> list[float]:
    if len(data) % 2:
        raise ValueError("not a float16 vector (odd length)")
    return list(struct.unpack(f"<{len(data) // 2}e", data))


def write_embedding_sidecar(item_id: str, model: str, vec: list[float], created_at: datetime):
    f16, js = embed_paths(item_id, model)
    _atomic_write_bytes(f16, pack_f16(vec))
    meta_svc.atomic_write_text(js, json.dumps({
        "model": model, "dim": len(vec), "created_at": created_at.isoformat(),
        "kist_version": __version__,
    }, indent=2))


def read_embedding_sidecar(item_id: str, model: str) -> tuple[list[float], dict] | None:
    f16, js = embed_paths(item_id, model)
    if not f16.is_file():
        return None
    vec = unpack_f16(f16.read_bytes())
    info = meta_svc.read_json(js) if js.is_file() else {}
    if info.get("dim") not in (None, len(vec)):
        raise ValueError(f"{f16.name}: {len(vec)} values but its .json says dim {info['dim']}")
    return vec, info


def embedding_sidecar_models(item_id: str) -> list[str]:
    d = ai_dir(item_id)
    if not d.is_dir():
        return []
    return sorted(p.name[len("embed-"):-len(".f16")] for p in d.glob("embed-*.f16"))


def write_ocr_sidecar(item_id: str, record: dict) -> None:
    meta_svc.atomic_write_text(
        ai_dir(item_id) / OCR_FILE, json.dumps(record, indent=2, ensure_ascii=False)
    )


def read_ocr_sidecar(item_id: str) -> dict | None:
    path = ai_dir(item_id) / OCR_FILE
    if not path.is_file():
        return None
    data = meta_svc.read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        raise ValueError(f"{path.name} has no text")
    return data


# ---------- storing results (file first, then the row) ----------


def _item_exists_on_disk(item_id: str) -> bool:
    return (get_settings().items_dir / item_id).is_dir()


def clean_vector(vec: Any, dim: int | None) -> list[float]:
    if not isinstance(vec, list) or not vec:
        raise ResultRejected("vector must be a non-empty list of numbers")
    try:
        out = [float(x) for x in vec]
    except (TypeError, ValueError):
        raise ResultRejected("vector must be a list of numbers") from None
    if dim is not None and len(out) != dim:
        raise ResultRejected(f"vector has {len(out)} values; this model's have {dim}")
    if not all(math.isfinite(x) for x in out):
        raise ResultRejected("vector has non-finite values")
    norm = math.sqrt(sum(x * x for x in out))
    if norm == 0:
        raise ResultRejected("vector is all zeros")
    return [x / norm for x in out]


def _upsert_embedding(session: Session, item_id: str, model: str, vec: list[float],
                      created_at: datetime) -> None:
    session.execute(
        sa.text(
            "INSERT INTO item_embeddings (item_id, model, dim, vector, created_at) "
            "VALUES (:item_id, :model, :dim, CAST(:vector AS halfvec), :created_at) "
            "ON CONFLICT (item_id, model) DO UPDATE SET dim = EXCLUDED.dim, "
            "vector = EXCLUDED.vector, created_at = EXCLUDED.created_at"
        ).bindparams(sa.bindparam("vector", type_=HalfVec())),
        {"item_id": item_id, "model": model, "dim": len(vec), "vector": vec,
         "created_at": created_at},
    )


def store_embedding(session: Session, item_id: str, model: str, vec: list[float]) -> bool:
    """Sidecar, then row. False when the item is gone (purged meanwhile)."""
    vec = clean_vector(vec, model_dim(model))
    if session.get(Item, item_id) is None or not _item_exists_on_disk(item_id):
        return False
    now = datetime.now(UTC)
    write_embedding_sidecar(item_id, model, vec, now)
    _upsert_embedding(session, item_id, model, vec, now)
    session.commit()
    return True


def _clean_text(text: str) -> str:
    return (text or "").replace("\x00", "")[:MAX_TEXT]


def _upsert_text(session: Session, item_id: str, source: str, text: str, lang: str | None,
                 model: str, created_at: datetime) -> None:
    # one read-text row per item (a PDF's text layer or its OCR); a caption
    # is kept
    session.execute(sa.delete(ItemText).where(ItemText.item_id == item_id,
                                              ItemText.source.in_(READ_SOURCES)))
    session.add(ItemText(item_id=item_id, source=source, text=text, lang=lang, model=model,
                         created_at=created_at))


def store_text(session: Session, item_id: str, *, source: str, text: str,
               model: str, lang: str | None = None, boxes: list | None = None) -> bool:
    if source not in ("ocr", "pdf"):
        raise ResultRejected("source must be ocr or pdf")
    if session.get(Item, item_id) is None or not _item_exists_on_disk(item_id):
        return False
    now = datetime.now(UTC)
    text = _clean_text(text)
    record = {"model": model, "lang": lang, "text": text, "source": source,
              "created_at": now.isoformat(), "kist_version": __version__}
    if boxes:
        record["boxes"] = boxes
    write_ocr_sidecar(item_id, record)
    _upsert_text(session, item_id, source, text, lang, model, now)
    session.commit()
    return True


def load_sidecars(session: Session, item_id: str) -> tuple[int, int]:
    """Database rows from an item's AI files, for rows that are missing
    (rebuild-index). Returns (embeddings, texts) loaded. Never recomputes."""
    if not ai_dir(item_id).is_dir():
        return 0, 0
    have = set(session.execute(
        sa.select(ItemEmbedding.model).where(ItemEmbedding.item_id == item_id)
    ).scalars())
    emb = txt = 0
    for model in embedding_sidecar_models(item_id):
        if model in have or not MODEL_NAME.match(model):
            continue
        found = read_embedding_sidecar(item_id, model)
        if found is None:
            continue
        vec, info = found
        at = _parse_dt(info.get("created_at")) or datetime.now(UTC)
        _upsert_embedding(session, item_id, model, vec, at)
        emb += 1
    if session.get(ItemText, (item_id, "ocr")) is None and session.get(
        ItemText, (item_id, "pdf")
    ) is None:
        rec = read_ocr_sidecar(item_id)
        if rec is not None:
            source = rec.get("source") if rec.get("source") in ("ocr", "pdf") else "ocr"
            _upsert_text(session, item_id, source, _clean_text(rec["text"]), rec.get("lang"),
                         str(rec.get("model") or "unknown"),
                         _parse_dt(rec.get("created_at")) or datetime.now(UTC))
            txt += 1
    if emb or txt:
        session.commit()
    from . import captions

    txt += captions.load_sidecar(session, item_id)
    return emb, txt


def rewrite_missing_sidecars(session: Session) -> tuple[int, int]:
    """Write sidecar files for database rows that have none (`kist
    ai-sidecars`). Returns (embeddings, texts) written."""
    emb = txt = 0
    rows = session.execute(
        sa.select(ItemEmbedding.item_id, ItemEmbedding.model, ItemEmbedding.created_at,
                  ItemEmbedding.vector)
    )
    for item_id, model, created_at, vec in rows:
        f16, js = embed_paths(item_id, model)
        if (f16.is_file() and js.is_file()) or not _item_exists_on_disk(item_id):
            continue
        write_embedding_sidecar(item_id, model, vec, created_at)
        emb += 1
    for t in session.execute(
        sa.select(ItemText).where(ItemText.source.in_(READ_SOURCES))
    ).scalars():
        if (ai_dir(t.item_id) / OCR_FILE).is_file() or not _item_exists_on_disk(t.item_id):
            continue
        write_ocr_sidecar(t.item_id, {
            "model": t.model, "lang": t.lang, "text": t.text, "source": t.source,
            "created_at": t.created_at.isoformat(), "kist_version": __version__,
        })
        txt += 1
    from . import captions

    txt += captions.rewrite_missing_sidecars(session)
    return emb, txt


def _parse_dt(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def ensure_vector_index(session: Session, model: str) -> None:
    """The partial HNSW index for one model (0101 makes the default's)."""
    dim = model_dim(model)
    if dim is None or not MODEL_NAME.match(model):
        return
    name = "ix_item_embeddings_hnsw_" + re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
    session.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS {name[:63]} ON item_embeddings "
        f"USING hnsw ((vector::halfvec({dim})) halfvec_cosine_ops) WHERE model = '{model}'"
    ))
    session.commit()


# ---------- what an item needs ----------


def embed_source(item: Item) -> Path | None:
    if item.preview_status != PreviewStatus.READY:
        return None
    d = get_settings().items_dir / item.id
    for name in EMBED_SOURCES:
        if (d / name).is_file():
            return d / name
    return None


def has_camera_exif(path: Path) -> bool:
    import warnings

    from PIL import Image

    # only the header is read (no pixels decoded), so a very large original
    # isn't a decompression bomb here: don't warn about it
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        with Image.open(path) as im:
            exif = im.getexif()
            return bool(exif.get(EXIF_MAKE) or exif.get(EXIF_MODEL))


def ocr_kind(item: Item) -> str | None:
    """'pdf', 'image' (a screenshot-like image: no camera EXIF) or None."""
    if item.mime == "application/pdf":
        return "pdf"
    if kind_for(item.mime, item.ext) != "image" or item.preview_status != PreviewStatus.READY:
        return None
    original = get_settings().items_dir / item.id / f"original.{item.ext}"
    try:
        return None if has_camera_exif(original) else "image"
    except Exception:
        return None  # a format PIL can't read (HEIC, SVG): photos, or nothing to read


def ocr_image_source(item: Item) -> Path | None:
    from .thumbs import SCREEN_FILE

    d = get_settings().items_dir / item.id
    for p in (d / SCREEN_FILE, d / f"original.{item.ext}", d / "thumb_1024.webp"):
        if p.is_file():
            return p
    return None


# ---------- queueing ----------


def _open_jobs(session: Session, kind: str, item_ids: list[str]) -> set[str]:
    if not item_ids:
        return set()
    rows = session.execute(
        sa.select(Job.payload["item_id"].astext).where(
            Job.kind == kind,
            Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
            Job.payload["item_id"].astext.in_(item_ids),
        )
    ).scalars()
    return set(rows)


def enqueue_for_item(session: Session, item: Item, *, backfill: bool = False,
                     kinds: tuple[str, ...] = DEFAULT_AI_KINDS) -> list[str]:
    """Queue what an item still needs. Returns the kinds queued."""
    queued: list[str] = []
    model = active_model()
    if JobKind.EMBED in kinds and embed_source(item) is not None:
        done = session.get(ItemEmbedding, (item.id, model)) is not None
        if not done and not _open_jobs(session, JobKind.EMBED, [item.id]):
            session.add(Job(kind=JobKind.EMBED, payload=_payload(item, backfill)))
            queued.append(JobKind.EMBED)
    if JobKind.OCR in kinds:
        which = ocr_kind(item)
        done = session.execute(
            sa.select(ItemText.item_id).where(ItemText.item_id == item.id,
                                              ItemText.source.in_(READ_SOURCES)).limit(1)
        ).first() is not None
        if which and not done and not _open_jobs(session, JobKind.OCR, [item.id]):
            session.add(Job(kind=JobKind.OCR,
                            payload={**_payload(item, backfill), "pdf": which == "pdf"}))
            queued.append(JobKind.OCR)
    if queued:
        session.commit()
    return queued


def _payload(item: Item, backfill: bool) -> dict:
    return {"item_id": item.id, "filename": item.filename, "backfill": backfill}


def on_new_item(session: Session, item: Item) -> None:
    """Called after an item is created or its previews are redone. Never
    fails the caller: AI is extra."""
    if not get_settings().ai_enabled:
        return
    try:
        enqueue_for_item(session, item)
        from . import captions

        captions.on_new_item(session, item)  # only with KIST_CAPTION_AUTO
    except Exception:
        session.rollback()
        log.exception("could not queue AI jobs for %s", item.id)


def backfill(session: Session, kinds: tuple[str, ...] = DEFAULT_AI_KINDS,
             limit: int | None = None) -> dict[str, int]:
    """Queue jobs for every active item without a result for the active
    model. They wait for KIST_AI_WINDOW."""
    counts = {k: 0 for k in kinds}
    model = active_model()
    for kind in kinds:
        if kind == JobKind.CAPTION:
            from . import captions

            counts[kind] = captions.backfill(session, limit)
            continue
        if kind == JobKind.EMBED:
            missing = sa.select(Item.id).where(
                Item.deleted_at.is_(None),
                Item.preview_status == PreviewStatus.READY,
                ~sa.exists().where(ItemEmbedding.item_id == Item.id,
                                   ItemEmbedding.model == model),
            )
        else:
            missing = sa.select(Item.id).where(
                Item.deleted_at.is_(None),
                sa.or_(Item.mime == "application/pdf",
                       sa.and_(Item.mime.like("image/%"),
                               Item.preview_status == PreviewStatus.READY)),
                ~sa.exists().where(ItemText.item_id == Item.id,
                                   ItemText.source.in_(READ_SOURCES)),
            )
        ids = list(session.execute(missing.order_by(Item.id)).scalars())
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            busy = _open_jobs(session, kind, chunk)
            for item in session.execute(sa.select(Item).where(Item.id.in_(chunk))).scalars():
                if item.id in busy:
                    continue
                if limit is not None and counts[kind] >= limit:
                    break
                if kind == JobKind.EMBED:
                    if embed_source(item) is None:
                        continue
                    session.add(Job(kind=kind, payload=_payload(item, True)))
                else:
                    which = ocr_kind(item)
                    if which is None:
                        continue
                    session.add(Job(kind=kind,
                                    payload={**_payload(item, True), "pdf": which == "pdf"}))
                counts[kind] += 1
            session.commit()
            if limit is not None and counts[kind] >= limit:
                break
    return counts


# ---------- claiming (the worker and helpers) ----------


def light_jobs_waiting(session: Session) -> bool:
    return session.execute(
        sa.select(Job.id).where(
            Job.status == JobStatus.PENDING,
            Job.kind.notin_(AI_KINDS),
            Job.run_after <= sa.func.now(),
        ).limit(1)
    ).first() is not None


def claim(session: Session, *, runner: str, kinds: tuple[str, ...] = AI_KINDS,
          limit: int = 1, include_pdf: bool = True) -> list[Job]:
    """Take up to `limit` AI jobs (pending -> running), oldest first, under
    the budget rules. `runner` is recorded in the payload: "worker" or
    "helper:<name>"; a helper may only report on its own jobs."""
    kinds = tuple(k for k in kinds if k in AI_KINDS)
    if not kinds or limit < 1 or light_jobs_waiting(session):
        return []
    conds = [
        Job.status == JobStatus.PENDING,
        Job.kind.in_(kinds),
        Job.run_after <= sa.func.now(),
    ]
    if not window_open(get_settings().ai_window):
        conds.append(sa.func.coalesce(Job.payload["backfill"].astext, "false") != "true")
    if not include_pdf:
        conds.append(sa.func.coalesce(Job.payload["pdf"].astext, "false") != "true")
    jobs = list(session.execute(
        sa.select(Job).where(*conds).order_by(Job.id).limit(limit)
        .with_for_update(skip_locked=True)
    ).scalars())
    now = datetime.now(UTC)
    for job in jobs:
        job.status = JobStatus.RUNNING
        job.attempts += 1
        job.started_at = now
        job.finished_at = None
        job.payload = {**(job.payload or {}), "runner": runner}
    session.commit()
    return jobs


def claim_batch(session: Session, *, runner: str, kinds: tuple[str, ...] = AI_KINDS,
                batch: int = 1) -> list[Job]:
    """What one worker thread takes: the oldest job it may run and, when
    that is an embed job, up to batch-1 more embed jobs to send with it."""
    jobs = claim(session, runner=runner, kinds=kinds, limit=1)
    if jobs and jobs[0].kind == JobKind.EMBED and batch > 1:
        jobs += claim(session, runner=runner, kinds=(JobKind.EMBED,), limit=batch - 1)
    return jobs


def worker_kinds() -> tuple[str, ...]:
    """The AI job kinds this server's worker can run itself."""
    from . import captions

    kinds: list[str] = []
    if get_settings().ml_url:
        kinds += [JobKind.EMBED, JobKind.OCR]
    if captions.worker_can_run():
        kinds.append(JobKind.CAPTION)
    return tuple(kinds)


def run_embed_batch(session: Session, jobs: list[Job]) -> dict[int, str | None]:
    """Embed several items in one ml request. Returns {job id: error or
    None}. AiUnavailable (the ml service is down) is raised for the caller
    to put every job back."""
    results: dict[int, str | None] = {}
    todo: list[tuple[Job, Item, bytes]] = []
    for job in jobs:
        item = session.get(Item, (job.payload or {}).get("item_id"))
        if item is None or not _item_exists_on_disk(item.id):
            results[job.id] = None  # purged meanwhile: nothing to do
            continue
        src = embed_source(item)
        if src is None:
            results[job.id] = "the item has no thumbnail to embed"
            continue
        try:
            todo.append((job, item, src.read_bytes()))
        except OSError as exc:
            results[job.id] = f"can't read {src.name}: {exc}"
    if not todo:
        return results
    model = active_model()
    vectors = ml_embed_images([data for _, _, data in todo])
    dim = model_dim(model)
    now = datetime.now(UTC)
    ready: list[tuple[Job, Item, list[float]]] = []
    for (job, item, _), vec in zip(todo, vectors, strict=True):
        if isinstance(vec, str):
            results[job.id] = vec
            continue
        try:
            ready.append((job, item, clean_vector(vec, dim)))
        except ResultRejected as exc:
            results[job.id] = str(exc)

    # sidecars first, written side by side: each waits for its fsync, and
    # on a NAS disk those waits overlap instead of adding up
    def write(entry):
        _job, item, vec = entry
        write_embedding_sidecar(item.id, model, vec, now)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(8, len(ready)) or 1,
                            thread_name_prefix="sidecar") as pool:
        written = list(zip(ready, pool.map(_capture(write), ready), strict=True))
    stored = 0
    for (job, item, vec), error in written:
        if error is not None:
            log.warning("job %s: writing the sidecar failed: %s", job.id, error)
            results[job.id] = error
            continue
        try:  # then the rows, one commit for the batch
            with session.begin_nested():
                _upsert_embedding(session, item.id, model, vec, now)
            results[job.id] = None
            stored += 1
        except Exception as exc:  # e.g. the item was purged meanwhile
            log.exception("job %s: storing the embedding failed", job.id)
            results[job.id] = str(exc) or type(exc).__name__
    if stored:
        session.commit()
    return results


def _capture(fn):
    """fn(x) -> None, or the error message it raised."""
    def run(x) -> str | None:
        try:
            fn(x)
            return None
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"
    return run


def run_job(session: Session, job: Job) -> None:
    """Compute one AI job through the ml service or the caption provider
    (the worker's side)."""
    payload = job.payload or {}
    item = session.get(Item, payload.get("item_id"))
    if item is None or not _item_exists_on_disk(item.id):
        log.info("job %s: item %s no longer exists", job.id, payload.get("item_id"))
        return
    if job.kind == JobKind.EMBED:
        src = embed_source(item)
        if src is None:
            raise RuntimeError("the item has no thumbnail to embed")
        store_embedding(session, item.id, active_model(), ml_embed_image(src.read_bytes()))
    elif job.kind == JobKind.OCR:
        if item.mime == "application/pdf":
            _ocr_pdf(session, item)
        else:
            src = ocr_image_source(item)
            if src is None:
                raise RuntimeError("the item has no image to read")
            out = ml_ocr(src.read_bytes())
            store_text(session, item.id, source="ocr", text=out.get("text") or "",
                       model=str(out.get("model") or "ocr"), lang=out.get("lang"),
                       boxes=out.get("boxes"))
    elif job.kind == JobKind.CAPTION:
        from . import captions

        captions.run_job(session, item)
    else:
        raise ValueError(f"not an AI job: {job.kind}")


def _ocr_pdf(session: Session, item: Item) -> None:
    from .pdftext import extract_isolated

    settings = get_settings()
    original = settings.items_dir / item.id / f"original.{item.ext}"
    if not original.is_file():
        raise RuntimeError("original file missing on disk")
    with tempfile.TemporaryDirectory(prefix="kist-pdf-") as td:
        got = extract_isolated(original, settings.ai_ocr_pdf_pages, Path(td),
                               settings.preview_timeout_seconds)
        if got["text"]:
            store_text(session, item.id, source="pdf", text=got["text"], model=PDF_TEXT_MODEL)
            return
        texts, model = [], "ocr"
        for page in got["images"]:
            out = ml_ocr(Path(page).read_bytes())
            model = str(out.get("model") or model)
            if out.get("text"):
                texts.append(out["text"])
        store_text(session, item.id, source="ocr", text="\n\n".join(texts), model=model)


# ---------- helpers (kist helper) ----------


def touch_helper(session: Session, name: str, done: int = 0, info: dict | None = None) -> None:
    helper = session.get(AiHelper, name)
    if helper is None:
        helper = AiHelper(name=name, jobs_done=0, info=info or {})
        session.add(helper)
    helper.last_seen = datetime.now(UTC)
    if done:
        helper.jobs_done = (helper.jobs_done or 0) + done
    if info:
        helper.info = info
    session.commit()


def job_for_helper(job: Job) -> dict:
    payload = job.payload or {}
    item_id = payload.get("item_id")
    out = {
        "id": job.id,
        "kind": job.kind,
        "item_id": item_id,
        "model": active_model() if job.kind == JobKind.EMBED else None,
        # under /api/ai/jobs, the only place a helper token may go
        "image_url": f"/api/ai/jobs/{job.id}/image",
        "lease_seconds": get_settings().job_lease_seconds,
    }
    if job.kind == JobKind.CAPTION:
        from .captions import DEFAULT_PROMPT

        settings = get_settings()
        # what the server would use; the helper may run its own model
        out["model"] = settings.caption_model or None
        out["prompt"] = settings.caption_prompt or DEFAULT_PROMPT
    return out


def helper_claim(session: Session, name: str, kinds: list[str], limit: int,
                 info: dict | None = None) -> list[dict]:
    touch_helper(session, name, info=info)
    wanted = tuple(k for k in kinds if k in AI_KINDS) or DEFAULT_AI_KINDS
    # scanned PDFs are read by the server (it has the file and pdfium)
    jobs = claim(session, runner=f"helper:{name}", kinds=wanted, limit=limit,
                 include_pdf=False)
    return [job_for_helper(j) for j in jobs]


def _helper_job(session: Session, job_id: int, name: str) -> Job:
    from ..lifecycle import TransitionError
    from . import jobs as jobs_svc

    job = jobs_svc.get_job(session, job_id)
    if job.kind not in AI_KINDS:
        raise TransitionError(f"job {job_id} isn't an AI job")
    if job.status != JobStatus.RUNNING or (job.payload or {}).get("runner") != f"helper:{name}":
        raise TransitionError(
            f"job {job_id} isn't running for helper {name!r} (its lease may have run out)"
        )
    return job


def helper_job_image(session: Session, job_id: int, name: str) -> tuple[str, int]:
    """(item_id, thumbnail size) for a job this helper holds."""
    job = _helper_job(session, job_id, name)
    size = {JobKind.EMBED: 512, JobKind.CAPTION: 1024}.get(job.kind, 2048)
    return (job.payload or {}).get("item_id"), size


def helper_result(session: Session, job_id: int, name: str, body: dict) -> Job:
    from . import jobs as jobs_svc

    job = _helper_job(session, job_id, name)
    error = body.get("error")
    if error:
        touch_helper(session, name)
        return jobs_svc.finish(session, job_id, f"helper {name}: {str(error)[:3000]}")
    item_id = (job.payload or {}).get("item_id")
    if job.kind == JobKind.EMBED:
        model = body.get("model") or active_model()
        if model != active_model():
            raise ResultRejected(
                f"the helper used {model!r} but this server's model is {active_model()!r}"
            )
        store_embedding(session, item_id, model, body.get("vector"))
    elif job.kind == JobKind.CAPTION:
        from . import captions, events

        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ResultRejected("a caption result needs text")
        with events.acting(f"helper:{name}"[:100], "worker"):
            captions.store_generated(session, item_id, text[:captions.CAPTION_MAX],
                                     model=str(body.get("model") or "unknown")[:200],
                                     provider=f"helper:{name}"[:100])
    else:
        text = body.get("text")
        if not isinstance(text, str):
            raise ResultRejected("an ocr result needs text (a string, maybe empty)")
        boxes = body.get("boxes") if isinstance(body.get("boxes"), list) else None
        store_text(session, item_id, source="ocr", text=text,
                   model=str(body.get("model") or "ocr")[:200],
                   lang=body.get("lang") if isinstance(body.get("lang"), str) else None,
                   boxes=boxes)
    touch_helper(session, name, done=1)
    return jobs_svc.finish(session, job_id, None)


# ---------- status ----------


def status(session: Session) -> dict:
    settings = get_settings()
    model = active_model()
    health = ml_health()
    embedded = session.execute(
        sa.select(sa.func.count()).select_from(ItemEmbedding).where(ItemEmbedding.model == model)
    ).scalar_one()
    ocr_done = session.execute(
        sa.select(sa.func.count()).select_from(ItemText).where(ItemText.source.in_(READ_SOURCES))
    ).scalar_one()
    rows = session.execute(
        sa.select(Job.kind, Job.status, sa.func.count())
        .where(Job.kind.in_(AI_KINDS))
        .group_by(Job.kind, Job.status)
    ).all()
    by = {(k, s): n for k, s, n in rows}
    helpers = session.execute(sa.select(AiHelper).order_by(AiHelper.last_seen.desc())).scalars()
    try:
        win_open = window_open(settings.ai_window)
    except ValueError:
        win_open = False
    from . import captions

    captioned = session.execute(
        sa.select(sa.func.count()).select_from(ItemText).where(ItemText.source == "caption")
    ).scalar_one()
    return {
        "enabled": settings.ai_enabled,
        "model": model,
        "ml_url_set": bool(settings.ml_url),
        "ml_ok": health is not None,
        "ml": health,
        "embedded": embedded,
        "ocr_done": ocr_done,
        "pending": sum(n for (k, s), n in by.items() if s == JobStatus.PENDING),
        "jobs": {
            str(kind): {str(s): by.get((kind, s), 0) for s in JobStatus} for kind in AI_KINDS
        },
        "window": settings.ai_window or None,
        "window_open": win_open,
        "concurrency": settings.ai_concurrency,
        "embed_batch": settings.ai_embed_batch,
        "captioned": captioned,
        "captions": captions.status(),
        "helpers": [
            {"name": h.name, "last_seen": h.last_seen, "jobs_done": h.jobs_done, "info": h.info}
            for h in helpers
        ],
    }
