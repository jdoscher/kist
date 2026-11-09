"""The `ml` service: a small FastAPI app on the compose network only.

    POST /embed/image   body: image bytes      -> {model, dim, vector}
    POST /embed/text    {"text": "..."}         -> {model, dim, vector}
    POST /ocr           body: image bytes      -> {model, lang, text, boxes}
    GET  /health                                -> {status, model, dim, device, loaded}

Vectors are L2-normalised float32 lists, so cosine distance is 1 - dot.
At most KIST_ML_CONCURRENCY requests run inference at once (the rest wait),
which keeps memory flat however many workers or helpers call it. Run with
`uvicorn kist.ml.server:app`.
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
from .models import ModelError

log = logging.getLogger("kist.ml.server")

MAX_BYTES = int(os.environ.get("KIST_ML_MAX_BYTES", str(64 * 1024 * 1024)))


class State:
    cfg: EngineConfig
    clip: ClipEngine
    ocr: OcrEngine
    gate: threading.BoundedSemaphore
    stop = threading.Event()


state = State()


def _janitor() -> None:
    while not state.stop.wait(30):
        dropped = state.clip.unload_idle() + state.ocr.unload_idle()
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
        except Exception:
            log.exception("preloading %s failed (it is retried on the first request)", part)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=os.environ.get("KIST_LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    state.cfg = EngineConfig.from_env()
    state.clip = ClipEngine(state.cfg)
    state.ocr = OcrEngine(state.cfg)
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


def _run(fn, *args):
    with state.gate:
        t0 = time.perf_counter()
        try:
            out = fn(*args)
        except ModelError as exc:
            raise HTTPException(503, str(exc)) from exc
        except (OSError, ValueError) as exc:  # PIL can't read it, a bad request
            raise HTTPException(422, f"can't process this input: {exc}") from exc
        return out, (time.perf_counter() - t0) * 1000


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
        },
        "ocr_model": state.ocr.model,
    }


@app.post("/embed/image")
async def embed_image(request: Request) -> dict:
    data = await _body(request)
    vec, ms = await run_in_threadpool(_run, state.clip.embed_image, data)
    return {"model": state.clip.model, "dim": len(vec), "vector": vec, "ms": round(ms, 1)}


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
