"""The `ml` service: a small FastAPI app on the compose network only.

    POST /embed/image   body: image bytes      -> {model, dim, vector}
    POST /embed/images  multipart, up to 32 files named "images"
                                                -> {model, dim, results: [{vector} | {error}]}
    POST /embed/text    {"text": "..."}         -> {model, dim, vector}
    POST /ocr           body: image bytes      -> {model, lang, text, boxes}
    POST /faces         body: image bytes      -> {model, width, height, faces:
                                                   [{box, landmarks, score, embedding}]}
                                                   (404 unless KIST_FACES=1)
    GET  /health                                -> {status, model, dim, device, loaded}

Faces (YuNet + SFace, kist/ml/faces.py) are opt-in: with KIST_FACES unset
or 0 the face models are never downloaded or loaded.

Vectors are L2-normalised float32 lists, so cosine distance is 1 - dot.
At most KIST_ML_CONCURRENCY requests run inference at once (the rest wait),
which keeps memory flat however many workers or helpers call it. Images
are decoded and resized before a request waits for that gate, so decoding
overlaps another request's inference. Run with `uvicorn kist.ml.server:app`.
"""

import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .engine import ClipEngine, EngineConfig, OcrEngine
from .faces import FaceEngine
from .models import ModelError

log = logging.getLogger("kist.ml.server")

MAX_BYTES = int(os.environ.get("KIST_ML_MAX_BYTES", str(64 * 1024 * 1024)))
MAX_BATCH = 32


class State:
    cfg: EngineConfig
    clip: ClipEngine
    ocr: OcrEngine
    faces: FaceEngine | None = None
    gate: threading.BoundedSemaphore
    stop = threading.Event()


state = State()


def _janitor() -> None:
    while not state.stop.wait(30):
        dropped = state.clip.unload_idle() + state.ocr.unload_idle()
        if state.faces is not None:
            dropped += state.faces.unload_idle()
        if dropped:
            log.info("unloaded idle: %s", ", ".join(dropped))


def _preload(parts: list[str]) -> None:
    for part in parts:
        try:
            if part == "visual":
                state.clip.visual.get(state.clip._load_visual)
            elif part == "textual":
                state.clip.textual.get(state.clip._load_textual)
            elif part == "ocr":
                state.ocr.tower.get(state.ocr._load_ocr)
            elif part == "faces" and state.faces is not None:
                state.faces.detector.get(state.faces._load_detector)
                state.faces.recognizer.get(state.faces._load_recognizer)
        except Exception:
            log.exception("preloading %s failed (it is retried on the first request)", part)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=os.environ.get("KIST_LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    state.cfg = EngineConfig.from_env()
    state.clip = ClipEngine(state.cfg)
    state.ocr = OcrEngine(state.cfg)
    state.faces = FaceEngine(state.cfg) if faces_enabled() else None
    state.gate = threading.BoundedSemaphore(int(os.environ.get("KIST_ML_CONCURRENCY", "1")))
    state.stop.clear()
    threading.Thread(target=_janitor, name="ml-janitor", daemon=True).start()
    # fetch (first start) and load the visual tower in the background, so the
    # first embedding job doesn't wait for a 350 MB download
    preload = [p.strip() for p in os.environ.get("KIST_ML_PRELOAD", "visual").split(",")
               if p.strip()]
    threading.Thread(target=_preload, args=(preload,), name="ml-preload", daemon=True).start()
    log.info("ml service: model %s, device %s, %d threads", state.cfg.model,
             state.cfg.device, state.cfg.threads)
    yield
    state.stop.set()


app = FastAPI(title="kist-ml", lifespan=lifespan)


def _call(fn, *args):
    try:
        return fn(*args)
    except ModelError as exc:
        raise HTTPException(503, str(exc)) from exc
    except (OSError, ValueError) as exc:  # PIL can't read it, a bad request
        raise HTTPException(422, f"can't process this input: {exc}") from exc


def faces_enabled() -> bool:
    return os.environ.get("KIST_FACES", "").strip().lower() in ("1", "true", "yes", "on")


def _run(fn, *args):
    with state.gate:
        t0 = time.perf_counter()
        out = _call(fn, *args)
        return out, (time.perf_counter() - t0) * 1000


def _embed_one(data: bytes):
    prepared = _call(state.clip.prepare, data)  # outside the gate
    return _run(state.clip.embed_prepared, prepared)


def _embed_batch(datas: list[bytes]):
    prepared = _call(state.clip.prepare_many, datas)  # outside the gate
    return _run(state.clip.embed_many, prepared)


async def _body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BYTES:
        raise HTTPException(413, "too large")
    data = await request.body()
    if not data:
        raise HTTPException(422, "send the image bytes as the request body")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "too large")
    return data


@app.get("/health")
def health() -> dict:
    import onnxruntime as ort

    return {
        "status": "ok",
        "model": state.clip.model,
        "dim": state.clip.dim,
        "device": state.cfg.device,
        "threads": state.cfg.threads,
        "providers": ort.get_available_providers(),
        "loaded": {
            "visual": state.clip.visual.loaded is not None,
            "textual": state.clip.textual.loaded is not None,
            "ocr": state.ocr.tower.loaded is not None,
            "faces": state.faces is not None and state.faces.recognizer.loaded is not None,
        },
        "ocr_model": state.ocr.model,
        "faces": state.faces is not None,
        "faces_model": state.faces.model if state.faces is not None else None,
    }


@app.post("/embed/image")
async def embed_image(request: Request) -> dict:
    data = await _body(request)
    vec, ms = await run_in_threadpool(_embed_one, data)
    return {"model": state.clip.model, "dim": len(vec), "vector": vec, "ms": round(ms, 1)}


@app.post("/embed/images")
async def embed_images(request: Request) -> dict:
    """Several images in one request (multipart, each part named "images").
    Results come back in the same order; an image that can't be read gets
    {"error": ...} and the others still get their vectors."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BYTES:
        raise HTTPException(413, "too large")
    form = await request.form(max_files=MAX_BATCH + 1, max_fields=10)
    files = [f for f in form.getlist("images") if hasattr(f, "read")]
    if not files:
        raise HTTPException(422, "send the images as multipart parts named 'images'")
    if len(files) > MAX_BATCH:
        raise HTTPException(413, f"at most {MAX_BATCH} images per request")
    datas = [await f.read() for f in files]
    if sum(len(d) for d in datas) > MAX_BYTES:
        raise HTTPException(413, "too large")
    out, ms = await run_in_threadpool(_embed_batch, datas)
    results = [{"vector": r} if not isinstance(r, str) else {"error": r} for r in out]
    return {"model": state.clip.model, "dim": state.clip.dim, "results": results,
            "ms": round(ms, 1)}


class TextIn(BaseModel):
    text: str


@app.post("/embed/text")
async def embed_text(body: TextIn) -> dict:
    if not body.text.strip():
        raise HTTPException(422, "text is empty")
    vec, ms = await run_in_threadpool(_run, state.clip.embed_text, body.text[:2000])
    return {"model": state.clip.model, "dim": len(vec), "vector": vec, "ms": round(ms, 1)}


@app.post("/ocr")
async def ocr(request: Request) -> dict:
    data = await _body(request)
    out, ms = await run_in_threadpool(_run, state.ocr.ocr, data)
    return {**out, "ms": round(ms, 1)}


@app.post("/faces")
async def faces(request: Request) -> dict:
    if state.faces is None:
        raise HTTPException(404, "faces are off on this ml service (KIST_FACES=1 turns them on)")
    data = await _body(request)
    out, ms = await run_in_threadpool(_run, state.faces.faces, data)
    return {**out, "ms": round(ms, 1)}
