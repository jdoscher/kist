"""Cutouts (F7): the subject of an image as a new, transparent item.

"Cut out subject" (preview, info panel, board, a selection, MCP) queues a
`cutout` job. The worker runs it on the AI lane: it sends a preview-sized
copy to the ml service (POST /cutout, kist/ml/cutout.py), gets the
subject's mask back, scales it up to the original and builds a PNG from the
original's own pixels and colour profile:

- feather (0-20 px): softens the mask's edge, at the cut-out's size
- shadow (off/on): keeps the subject's cast shadow, re-made as a soft black
  layer so it works on any board colour
- model: "isnet-general-use" (fast) or "birefnet-lite" (fine edges, about
  six times slower); empty uses KIST_CUTOUT_MODEL / the ml service's default

The result is trimmed to the subject (plus a small margin) and ingested as
a NEW item: the original is never touched. It gets the original's tags,
folders and provenance (minus the C2PA flag: the new file carries no
manifest) and a derived_from link to it. What made it is stored in its
meta.json, under media.cutout:

    {source_item, model, feather, shadow, shadow_found,
     box: [x0, y0, x1, y1] (source px), size: [w, h], ms, total_ms, made_at}

so `kist rebuild-index` restores it with the item (and the link from both
items' meta.json). `kist verify` checks the link is still there.

Undo: the new item's create event and, for a board "Cut out", the board
update that swapped the node to it are recorded in the batch of the request
that asked for it, so undoing that batch trashes the cut-out and puts the
original back on the board.

Boards draw cut-outs from items/<id>/alpha_<size>.webp, a transparent copy
(previews are flattened onto a dark grey for the grid). It is derived from
original.png, made with the item and remade on demand when missing
(GET /api/items/{id}/cutout-preview).
"""

import io
import logging
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import httpx
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..config import get_settings
from ..filetypes import kind_for
from ..ids import new_ulid
from ..lifecycle import JobKind, JobStatus, PreviewStatus, TransitionError
from ..models import Item, ItemLink, Job
from . import events, validation
from . import items as items_svc

log = logging.getLogger("kist.cutouts")

MODELS = {
    # name -> (label, what it's for). The ml service holds the pins
    # (kist/ml/models.CUTOUT_MODELS)
    "isnet-general-use": ("Fast", "a few seconds a cut-out"),
    "birefnet-lite": ("Fine edges", "keeps thin parts and white-on-white subjects; "
                                     "about six times slower"),
}
FEATHER_MAX = 20.0
# what the ml service gets: the model works at 1024 px, so more is waste
ML_SIDE = 1536
ALPHA_SIZES = (1024, 2048)
ALPHA_FILE = "alpha_{size}.webp"
# Pillow opens these originals itself; anything else (HEIC, RAW, PSD, PDF
# ...) is cut out from its largest preview
PILLOW_MIMES = ("image/jpeg", "image/png", "image/webp", "image/tiff", "image/gif",
                "image/bmp")
MIN_SUBJECT = 0.001  # of the image's area: less is "no subject found"
MARGIN = 0.02  # trimmed to the subject plus this share of its long side


class CutoutsOff(TransitionError):
    """KIST_CUTOUTS=0 (HTTP 409 like any action that isn't available)."""


class MlDropped(RuntimeError):
    """The ml service took the request and the connection died before it
    answered: it crashed or was killed mid-cut-out (usually memory)."""


# a cut-out that crashes the ml service this many times fails instead of
# being retried for ever (each try would crash it again)
MAX_ML_DROPS = 3
DROPPED_RETRY = ("Kist's AI service stopped while making this cut-out (most likely it ran "
                 "out of memory); trying again")
DROPPED_FAILED = ("Kist's AI service stopped {n} times on this image, most likely out of "
                  "memory. Try the Fast model, or give the ml service more memory "
                  "(KIST_ML_MEM_LIMIT in .env)")
ML_DOWN = "Kist's AI service isn't answering right now (it may be starting); waiting for it"


class NoSubject(RuntimeError):
    """The model found nothing to keep."""


# ---------- settings ----------


def enabled() -> bool:
    return bool(get_settings().cutouts)


def require_enabled() -> None:
    if not enabled():
        raise CutoutsOff("cut-outs are turned off on this server (KIST_CUTOUTS=0)")


def clean_options(model: str | None = None, feather: float | None = None,
                  shadow: bool | None = None) -> dict:
    model = (model or "").strip() or None
    if model is not None and model not in MODELS:
        raise validation.ValidationError(
            f"unknown cut-out model {model!r}; expected one of {', '.join(MODELS)}"
        )
    try:
        feather = float(feather or 0.0)
    except (TypeError, ValueError):
        raise validation.ValidationError("feather must be a number of pixels") from None
    if not 0.0 <= feather <= FEATHER_MAX:
        raise validation.ValidationError(f"feather must be between 0 and {FEATHER_MAX:g} px")
    return {"model": model or get_settings().cutout_model or None,
            "feather": round(feather, 1), "shadow": bool(shadow)}


def status() -> dict:
    """What the web UI needs before it offers "Cut out subject": whether
    it's on, and each model's download state (from the ml service)."""
    from . import ai

    settings = get_settings()
    health = ai.ml_health() if settings.cutouts and settings.ml_url else None
    ml = (health or {}).get("cutout") if health else None
    models = []
    for name, (label, note) in MODELS.items():
        st = ((ml or {}).get("models") or {}).get(name) or {}
        models.append({
            "name": name, "label": label, "note": note,
            "license": st.get("license"), "size_bytes": st.get("size_bytes"),
            "state": st.get("state") or "unknown",
            "downloaded_bytes": st.get("downloaded_bytes") or 0,
            "error": st.get("error"),
        })
    default = settings.cutout_model or (ml or {}).get("default") or "isnet-general-use"
    reason = None
    if not settings.cutouts:
        reason = "turned off (KIST_CUTOUTS=0)"
    elif not settings.ml_url:
        reason = "no ml service is configured (KIST_ML_URL)"
    elif health is None:
        reason = "Kist's AI service isn't answering right now"
    elif ml is None:
        reason = "the ml service has cut-outs turned off or is older than this server"
    return {"enabled": bool(settings.cutouts), "available": reason is None,
            "reason": reason, "default_model": default, "models": models,
            "feather_max": FEATHER_MAX}


# ---------- queueing ----------


def is_cutout(item: Item) -> bool:
    return bool((item.media or {}).get("cutout"))


def check_source(item: Item) -> None:
    if item.deleted_at is not None:
        raise TransitionError("the item is in the trash: restore it first")
    if kind_for(item.mime, item.ext) != "image" or item.mime == "image/svg+xml":
        raise TransitionError("only images can be cut out")
    if item.preview_status != PreviewStatus.READY:
        raise TransitionError("the item has no preview yet: cut it out once it has one")


def open_job(session: Session, item_id: str, options: dict,
             board: dict | None = None) -> Job | None:
    """A pending or running cut-out of this item with the same settings
    (asking twice doesn't make two)."""
    rows = session.execute(
        sa.select(Job).where(
            Job.kind == JobKind.CUTOUT,
            Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
            Job.payload["item_id"].astext == item_id,
        )
    ).scalars()
    for job in rows:
        p = job.payload or {}
        if all(p.get(k) == v for k, v in options.items()) and p.get("board") == board:
            return job
    return None


def queue(session: Session, item_id: str, *, model: str | None = None,
          feather: float | None = None, shadow: bool | None = None,
          board: dict | None = None) -> Job:
    """Queue a cut-out of one item. `board` {board_id, node_id}: put the
    cut-out in that node's place when it's done."""
    require_enabled()
    options = clean_options(model, feather, shadow)
    item = items_svc.get_item(session, item_id)
    check_source(item)
    existing = open_job(session, item.id, options, board)
    if existing is not None:
        return existing
    ctx = events.context()
    job = Job(kind=JobKind.CUTOUT, payload={
        "item_id": item.id, "filename": item.filename, **options,
        **({"board": board} if board else {}),
        "actor": ctx.actor, "source": ctx.source, "batch_id": ctx.batch_id,
    })
    session.add(job)
    session.commit()
    return job


def queue_many(session: Session, item_ids: list[str], **options) -> tuple[list[Job], list[dict]]:
    """A selection: (jobs, skipped [{item_id, reason}])."""
    require_enabled()
    clean_options(options.get("model"), options.get("feather"), options.get("shadow"))
    if not item_ids:
        raise validation.ValidationError("item_ids is empty")
    if len(item_ids) > 500:
        raise validation.ValidationError("at most 500 items at once")
    jobs: list[Job] = []
    skipped: list[dict] = []
    for item_id in dict.fromkeys(item_ids):
        try:
            jobs.append(queue(session, item_id, **options))
        except items_svc.NotFound:
            session.rollback()
            skipped.append({"item_id": item_id, "reason": "not found"})
        except TransitionError as exc:
            session.rollback()
            skipped.append({"item_id": item_id, "reason": str(exc)})
    return jobs, skipped


def cutouts_of(session: Session, item_id: str) -> list[Item]:
    """The live cut-outs made from an item (newest first)."""
    items_svc.get_item(session, item_id)
    rows = session.execute(
        sa.select(Item).join(ItemLink, ItemLink.src_id == Item.id).where(
            ItemLink.dst_id == item_id, ItemLink.kind == "derived_from",
            Item.deleted_at.is_(None), Item.media["cutout"].isnot(None),
        ).order_by(Item.created_at.desc())
    ).scalars()
    return [i for i in rows if items_svc.visible(i)]


# ---------- the worker's side ----------


def _ml_mask(data: bytes, model: str | None) -> tuple[bytes, str, float]:
    """POST /cutout. AiUnavailable (put the job back without using an
    attempt) when the ml service is down or still downloading the model."""
    from . import ai

    params = {"model": model} if model else None
    try:
        with ai._client() as c:
            r = c.post("/cutout", content=data, params=params,
                       headers={"Content-Type": "application/octet-stream"})
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        # never got in: the service is down or restarting (no attempt used)
        raise ai.AiUnavailable(f"{ML_DOWN} ({type(exc).__name__})") from exc
    except httpx.HTTPError as exc:
        # got in, then lost: it died while working on this image
        raise MlDropped(type(exc).__name__) from exc
    if r.status_code == 404:
        raise RuntimeError("the ml service has cut-outs turned off (KIST_CUTOUTS) or is "
                           "older than this server")
    if r.status_code >= 500:
        try:
            detail = str(r.json().get("detail") or r.text)
        except ValueError:
            detail = r.text
        raise ai.AiUnavailable(f"the ml service answered {r.status_code}: {detail[:300]}")
    if r.status_code >= 400:
        raise ValueError(f"the ml service refused this image ({r.status_code}): {r.text[:300]}")
    return (r.content, r.headers.get("x-kist-model") or (model or "unknown"),
            float(r.headers.get("x-kist-ms") or 0))


def source_image(item: Item):
    """(PIL image, icc profile) at full size (capped at KIST_CUTOUT_MAX_SIDE):
    the original when Pillow reads it, else the largest preview."""
    from PIL import Image, ImageOps

    settings = get_settings()
    item_dir = settings.items_dir / item.id
    cap = max(256, int(settings.cutout_max_side))
    candidates: list[Path] = []
    if item.mime in PILLOW_MIMES:
        candidates.append(item_dir / f"original.{item.ext}")
    from . import thumbs

    candidates += [item_dir / "display.webp", item_dir / thumbs.SCREEN_FILE,
                   item_dir / "thumb_1024.webp"]
    last: Exception | None = None
    for path in candidates:
        if not path.is_file():
            continue
        try:
            im = Image.open(path)
            if getattr(im, "n_frames", 1) > 1:
                im.seek(0)
            if im.format == "JPEG":
                im.draft("RGB", (cap, cap))
            icc = im.info.get("icc_profile")
            im = ImageOps.exif_transpose(im)
            im.load()
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB")
            if max(im.size) > cap:
                im.thumbnail((cap, cap), Image.Resampling.LANCZOS)
            return im, icc
        except Exception as exc:  # a file Pillow can't read: try the next
            last = exc
            log.info("cutout: can't read %s (%s); trying a preview", path.name, exc)
    raise RuntimeError(f"no readable image for this item ({last or 'no previews'})")


def _ml_copy(im) -> bytes:
    from PIL import Image

    small = im.copy()
    small.thumbnail((ML_SIDE, ML_SIDE), Image.Resampling.LANCZOS)
    if small.mode == "RGBA":
        bg = Image.new("RGBA", small.size, (255, 255, 255, 255))
        small = Image.alpha_composite(bg, small)
    buf = io.BytesIO()
    small.convert("RGB").save(buf, "JPEG", quality=95)
    return buf.getvalue()


def compose(im, mask, *, feather: float = 0.0, shadow: bool = False):
    """(RGBA cut-out, its trim box in the source's pixels, whether a shadow
    was kept): `mask` is the model's greyscale mask at any size."""
    import numpy as np
    from PIL import Image, ImageFilter

    w, h = im.size
    m = mask.convert("L")
    if m.size != (w, h):
        m = m.resize((w, h), Image.Resampling.BILINEAR)
    if feather > 0:
        m = m.filter(ImageFilter.GaussianBlur(feather))
    a = np.asarray(m, dtype=np.float32) / 255.0
    rgb = np.asarray(im.convert("RGB"), dtype=np.float32)
    if im.mode == "RGBA":  # an already transparent original stays so
        a = a * (np.asarray(im.getchannel("A"), dtype=np.float32) / 255.0)
    if float((a > 0.5).mean()) < MIN_SUBJECT:
        raise NoSubject("the model found no subject to cut out in this image")
    kept = False
    if shadow:
        s = shadow_alpha(rgb, a)
        kept = bool((s > 0.05).any())
        out_a = a + s * (1.0 - a)
        # the subject over a black shadow layer
        rgb = rgb * (a / np.maximum(out_a, 1e-6))[..., None]
        a = out_a
    alpha = np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out = np.dstack([np.clip(rgb + 0.5, 0, 255).astype(np.uint8), alpha])
    ys, xs = np.nonzero(alpha > 2)
    pad = round(MARGIN * max(xs.max() - xs.min(), ys.max() - ys.min()))
    box = (max(0, int(xs.min()) - pad), max(0, int(ys.min()) - pad),
           min(w, int(xs.max()) + 1 + pad), min(h, int(ys.max()) + 1 + pad))
    return Image.fromarray(out, "RGBA").crop(box), box, kept


SHADOW_WORK_SIDE = 512  # the shadow is soft: it is found at this size
# a background busier than this (fine detail against its own mean
# brightness: patterned carpet, foliage) has no shadow worth keeping; plain
# studio paper and table tops measure under 0.02
SHADOW_MAX_TEXTURE = 0.05


def _blur(arr, radius: float):
    """A float array in 0..1 through Pillow's Gaussian blur (8-bit is
    plenty for masks and soft light)."""
    import numpy as np
    from PIL import Image, ImageFilter

    im = Image.fromarray(np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8), "L")
    return np.asarray(im.filter(ImageFilter.GaussianBlur(radius)), np.float32) / 255.0


def shadow_alpha(rgb, a):
    """Where the subject's shadow is, as an alpha for a black layer: pixels
    outside the subject, near it, and darker than the background around
    them (each spot against its own neighbourhood, so a vignette or a
    gradient isn't a shadow). A busy background gives none."""
    import numpy as np
    from PIL import Image

    h, w = a.shape
    scale = min(1.0, SHADOW_WORK_SIDE / max(w, h))
    sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
    lum = (rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)) / 255.0
    lum_s = np.asarray(Image.fromarray(np.clip(lum * 255, 0, 255).astype(np.uint8), "L")
                       .resize((sw, sh), Image.Resampling.BOX), np.float32) / 255.0
    a_s = np.asarray(Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8), "L")
                     .resize((sw, sh), Image.Resampling.BOX), np.float32) / 255.0
    bg = (a_s < 0.1).astype(np.float32)
    if bg.mean() < 0.02:
        return np.zeros_like(a)
    side = max(sw, sh)
    # the background's own light, from background pixels only
    wide = side * 0.1
    bg_local = _blur(lum_s * bg, wide) / np.maximum(_blur(bg, wide), 1e-3)
    if float(np.median(bg_local[bg > 0])) < 0.1:
        return np.zeros_like(a)  # a dark background has no visible shadow to keep
    # texture: fine detail left after a small blur, on clearly-background pixels
    fine = lum_s - _blur(lum_s, max(1.0, side * 0.01))
    far = (_blur(a_s, side * 0.02) < 0.02) & (bg > 0)
    if far.sum() > 50 and float(np.std(fine[far])) / max(float(np.mean(lum_s[far])), 0.05) \
            > SHADOW_MAX_TEXTURE:
        return np.zeros_like(a)
    dark = np.clip((bg_local - _blur(lum_s, max(1.0, side * 0.004))) / np.maximum(bg_local, 0.05),
                   0.0, 1.0)
    dark = np.clip((dark - 0.06) / 0.6, 0.0, 1.0)  # paper grain and noise stay out
    # near the subject: its mask spread to ~8% of the long side
    near = np.clip(_blur(a_s, side * 0.04) * 4.0, 0.0, 1.0)
    small = dark * near * (1.0 - a_s) * 0.9
    full = np.asarray(Image.fromarray(np.clip(small * 255, 0, 255).astype(np.uint8), "L")
                      .resize((w, h), Image.Resampling.BILINEAR), np.float32) / 255.0
    return (full * (1.0 - a)).astype(np.float32)


def write_alpha_previews(item_dir: Path, png: Path | None = None) -> list[Path]:
    """items/<id>/alpha_<size>.webp: transparent copies for boards."""
    from PIL import Image

    src = png or next(iter(sorted(item_dir.glob("original.*"))), None)
    if src is None or not src.is_file():
        return []
    written = []
    with Image.open(src) as im:
        im = im.convert("RGBA")
        for size in ALPHA_SIZES:
            copy = im.copy()
            copy.thumbnail((size, size), Image.Resampling.LANCZOS)
            path = item_dir / ALPHA_FILE.format(size=size)
            tmp = path.with_name(f".{path.name}.{new_ulid()}.tmp")
            copy.save(tmp, "WEBP", quality=88, exact=False)
            tmp.replace(path)
            written.append(path)
            if max(im.size) <= size:
                break
    return written


def alpha_preview(item: Item, size: int) -> Path | None:
    """The transparent preview for a board, made now if it's missing. None
    for an item without transparency."""
    item_dir = get_settings().items_dir / item.id
    size = 1024 if size <= 1024 else 2048
    for s in (size, 1024):
        path = item_dir / ALPHA_FILE.format(size=s)
        if path.is_file():
            return path
    if not is_cutout(item) and not has_alpha(item):
        return None
    write_alpha_previews(item_dir)
    for s in (size, 1024):
        path = item_dir / ALPHA_FILE.format(size=s)
        if path.is_file():
            return path
    return None


def has_alpha(item: Item) -> bool:
    if item.mime not in ("image/png", "image/webp", "image/gif", "image/tiff"):
        return False
    from PIL import Image

    try:
        with Image.open(get_settings().items_dir / item.id / f"original.{item.ext}") as im:
            return "A" in im.getbands() or "transparency" in im.info
    except Exception:
        return False


def _copied_provenance(item: Item) -> dict:
    from . import provenance as provenance_svc

    data = provenance_svc.out(item)
    data.pop("c2pa", None)  # the new file has no manifest
    data.pop("page_url", None)  # passed as source_url
    return data


def run_job(session: Session, job: Job) -> None:
    from PIL import Image

    from . import ingest, links, media

    p = dict(job.payload or {})
    item = session.get(Item, p.get("item_id"))
    if item is None:
        raise RuntimeError("the source item no longer exists")
    check_source(item)
    t0 = time.perf_counter()
    im, icc = source_image(item)
    try:
        mask_png, model, ms = _ml_mask(_ml_copy(im), p.get("model"))
    except MlDropped as exc:
        from . import ai
        from .jobs import MAX_ATTEMPTS

        drops = int(p.get("ml_drops") or 0) + 1
        job.payload = {**p, "ml_drops": drops}
        if drops >= MAX_ML_DROPS:
            job.attempts = MAX_ATTEMPTS  # fail now: every retry would crash it again
        session.commit()  # kept across the worker's rollback and release
        log.warning("cutout job %s: the ml service dropped the request (%s), %d of %d",
                    job.id, exc, drops, MAX_ML_DROPS)
        if drops >= MAX_ML_DROPS:
            raise RuntimeError(DROPPED_FAILED.format(n=drops)) from exc
        raise ai.AiUnavailable(f"{DROPPED_RETRY} ({drops} of {MAX_ML_DROPS})") from exc
    cut, box, kept = compose(im, Image.open(io.BytesIO(mask_png)),
                       feather=float(p.get("feather") or 0), shadow=bool(p.get("shadow")))
    settings = get_settings()
    settings.staging_dir.mkdir(parents=True, exist_ok=True)
    staging = settings.staging_dir / f"cutout-{new_ulid()}.png"
    try:
        cut.save(staging, "PNG", icc_profile=icc, compress_level=6)
        stem = PurePosixPath(item.filename).stem
        name = ingest.sanitize_filename(f"{stem} cutout.png")
        with events.acting(p.get("actor") or "worker", p.get("source") or "worker",
                           p.get("batch_id")):
            result = ingest.ingest_file(
                session, staging, name, tag_names=[t.name for t in item.tags],
                folder_ids=[f.id for f in item.folders],
                source_url=item.source_url, provenance=_copied_provenance(item),
            )
            new = result.item
            if not result.created and new.deleted_at is not None:
                # the same cut-out was made before and is in the trash (an
                # undo): asking again brings it back
                new = items_svc.restore(session, new.id)
            if result.created:
                info = {"source_item": item.id, "model": model,
                        "feather": float(p.get("feather") or 0), "shadow": bool(p.get("shadow")),
                        # "keep shadow" on a busy background finds none
                        "shadow_found": kept, "box": list(box), "size": list(cut.size),
                        "ms": round(ms, 1),
                        "total_ms": round((time.perf_counter() - t0) * 1000, 1),
                        "made_at": datetime.now(UTC).isoformat()}
                media.update_media(session, new.id, {"source_item": item.id, "cutout": info})
                try:
                    write_alpha_previews(settings.items_dir / new.id)
                except Exception:  # remade on demand
                    log.exception("cutout %s: the transparent preview failed", new.id)
            if new.id != item.id:
                links.link(session, new.id, item.id, "derived_from")
            if p.get("board"):
                place_on_board(session, p["board"], item.id, new)
    finally:
        staging.unlink(missing_ok=True)
    job.payload = {**p, "result_item_id": new.id}
    session.commit()


# ---------- boards ----------


def place_on_board(session: Session, board: dict, original_id: str, cut: Item) -> bool:
    """Swap a board node from the original to its cut-out (kept centred, at
    the node's width). Skipped when the node is gone or shows something else
    by now. Recorded in the current batch, so undo puts the original back."""
    from . import boards as boards_svc

    try:
        b = boards_svc.get_board(session, board.get("board_id") or "")
    except boards_svc.NotFound:
        return False
    node = next((n for n in b.doc.get("nodes") or [] if n.get("id") == board.get("node_id")),
                None)
    if node is None or node.get("type") != "item" or node.get("item_id") != original_id:
        log.info("cutout: board node %s no longer shows %s; left alone",
                 board.get("node_id"), original_id)
        return False
    upd = swap_update(node, cut)
    try:
        boards_svc.patch_board(session, b.id, {"update": [upd]})
    except (boards_svc.Conflict, TransitionError, validation.ValidationError) as exc:
        session.rollback()
        log.info("cutout: could not put %s on board %s: %s", cut.id, b.id, exc)
        return False
    return True


def swap_update(node: dict, cut: Item) -> dict:
    """The node update that shows the cut-out instead: same width and
    centre, the cut-out's own proportions, no crop."""
    w = float(node.get("w") or 200)
    cw, ch = cut.width or 1, cut.height or 1
    h = max(1.0, w * ch / cw)
    cy = float(node.get("y") or 0) + float(node.get("h") or h) / 2
    return {"id": node["id"], "item_id": cut.id, "crop": None,
            "x": float(node.get("x") or 0), "y": round(cy - h / 2, 2), "w": w, "h": round(h, 2)}


def board_cutout(session: Session, board_id: str, node_id: str, *, model: str | None = None,
                 feather: float | None = None, shadow: bool | None = None) -> dict:
    """The board's "Cut out" on a placed image: swap now when a cut-out of
    that item with these settings exists, else queue one that swaps when
    it's done. {board, job}: one of them is None."""
    from .. import access
    from . import boards as boards_svc

    require_enabled()
    access.require(access.Action.EDIT)
    options = clean_options(model, feather, shadow)
    b = boards_svc.get_board(session, board_id)
    if b.deleted_at is not None:
        raise TransitionError("The board is in the trash: restore it first")
    node = next((n for n in b.doc.get("nodes") or [] if n.get("id") == node_id), None)
    if node is None or node.get("type") != "item" or not node.get("item_id"):
        raise validation.ValidationError(f"no image node {node_id} on the board")
    item = items_svc.get_item(session, node["item_id"])
    if is_cutout(item):
        raise TransitionError("this is already a cut-out")
    for cut in cutouts_of(session, item.id):
        info = (cut.media or {}).get("cutout") or {}
        same = (info.get("feather") == options["feather"] and info.get("shadow")
                == options["shadow"] and (options["model"] is None
                                          or info.get("model") == options["model"]))
        if same:
            board = boards_svc.patch_board(session, b.id, {"update": [swap_update(node, cut)]})
            return {"board": board, "job": None}
    job = queue(session, item.id, **options, board={"board_id": b.id, "node_id": node_id})
    return {"board": None, "job": job}


# ---------- verify ----------


def check(session: Session) -> list[str]:
    """Cut-outs whose derived_from link to a still-existing source is gone
    (meta.json and the database disagree about where it came from)."""
    problems = []
    rows = session.execute(
        sa.select(Item.id, Item.media["cutout"]["source_item"].astext)
        .where(Item.media["cutout"].isnot(None))
    ).all()
    for item_id, source in rows:
        if not source or session.get(Item, source) is None:
            continue
        if session.get(ItemLink, (item_id, source, "derived_from")) is None:
            problems.append(f"{item_id}: a cut-out of {source} without its derived_from link "
                            "(kist rebuild-index restores links from meta.json)")
    return problems
