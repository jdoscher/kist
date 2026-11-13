"""Cutouts (F7): background removal on ONNX Runtime.

    POST /cutout   body: image bytes  -> image/png, a greyscale mask the size
                                         of the image sent (255 = subject)

The ml service only answers "which pixels are the subject". The worker
(kist/services/cutouts.py) sends a preview-sized copy, scales the mask up
to the original and makes the transparent PNG from the original's own
pixels, so nothing here needs the full-size file.

The model is downloaded on the first cut-out someone asks for (never at
start), in a background thread: until it is there, requests get 503 with
the progress, and the worker puts the job back without using up an
attempt. KIST_CUTOUTS=0 turns the endpoint off (404); KIST_CUTOUT_MODEL
picks the model (kist/ml/models.CUTOUT_MODELS).
"""

import io
import logging
import os
import threading

from .models import (
    CUTOUT_MODELS,
    DEFAULT_CUTOUT_MODEL,
    CutoutSpec,
    ModelError,
    cutout_path,
    cutout_ready,
    ensure_cutout,
)

log = logging.getLogger("kist.ml.cutout")


class Downloading(RuntimeError):
    """The model is still on its way (HTTP 503 with the progress)."""


def enabled() -> bool:
    return os.environ.get("KIST_CUTOUTS", "1").strip().lower() not in ("0", "false", "no", "off")


def spec_from_env() -> CutoutSpec:
    name = (os.environ.get("KIST_CUTOUT_MODEL") or DEFAULT_CUTOUT_MODEL).strip()
    try:
        return CUTOUT_MODELS[name]
    except KeyError:
        raise ModelError(
            f"unknown cutout model {name!r}; Kist knows {', '.join(sorted(CUTOUT_MODELS))}"
        ) from None


def preprocess(im, spec: CutoutSpec):
    """A PIL RGB image -> the model's 1x3xSxS float32 input."""
    import numpy as np
    from PIL import Image

    x = np.asarray(im.resize((spec.side, spec.side), Image.Resampling.BILINEAR),
                   dtype=np.float32)
    if spec.kind == "isnet":
        x = x / max(float(x.max()), 1e-6) - 0.5
    else:
        x = x / 255.0
        x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array(
            [0.229, 0.224, 0.225], np.float32)
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None].astype(np.float32))


def postprocess(out, spec: CutoutSpec):
    """The model's first output -> an SxS float32 mask in 0..1."""
    import numpy as np

    pred = np.asarray(out, dtype=np.float32).reshape(spec.side, spec.side)
    if spec.kind == "isnet":
        lo, hi = float(pred.min()), float(pred.max())
        return (pred - lo) / (hi - lo) if hi - lo > 1e-6 else np.zeros_like(pred)
    return 1.0 / (1.0 + np.exp(-pred))


def flatten(im):
    """Any PIL image -> RGB; transparent pixels on white (what a cut-out
    model expects of an already transparent PNG)."""
    from PIL import Image, ImageOps

    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P", "PA"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    return im.convert("RGB")


class _Model:
    """One cutout model: its file, its download and its loaded session."""

    def __init__(self, spec: CutoutSpec, cfg):
        from .engine import _Tower

        self.spec = spec
        self.cfg = cfg
        self.tower = _Tower(cfg.idle_seconds)
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.got = 0
        self.error: str | None = None

    def status(self) -> dict:
        ready = cutout_ready(self.spec, self.cfg.cache_dir)
        with self.lock:
            downloading = self.thread is not None and self.thread.is_alive()
            state = "ready" if ready else "downloading" if downloading else "missing"
            return {
                "model": self.spec.name,
                "license": self.spec.license,
                "size_bytes": self.spec.size,
                "state": state,
                "downloaded_bytes": self.spec.size if ready else self.got,
                "error": None if ready or downloading else self.error,
                "loaded": self.tower.loaded is not None,
            }

    def _fetch(self) -> None:
        def progress(got: int) -> None:
            self.got = got

        try:
            ensure_cutout(self.spec, self.cfg.cache_dir, progress=progress)
            self.error = None
        except Exception as exc:  # reported by status() and the next request
            log.exception("downloading the cutout model %s failed", self.spec.name)
            self.error = f"{type(exc).__name__}: {exc}"[:500]

    def ensure(self) -> None:
        """Return when the model file is there; otherwise start (or keep)
        the download and raise Downloading."""
        if cutout_ready(self.spec, self.cfg.cache_dir):
            return
        path = cutout_path(self.spec, self.cfg.cache_dir)
        if path.is_file() and path.stat().st_size == self.spec.size:
            ensure_cutout(self.spec, self.cfg.cache_dir)  # hashes it once, no download
            return
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.got = 0
                self.thread = threading.Thread(target=self._fetch, name="cutout-download",
                                               daemon=True)
                self.thread.start()
                log.info("downloading the cutout model %s (%.0f MB)", self.spec.name,
                         self.spec.size / 1e6)
            got = self.got
        raise Downloading(
            f"downloading the cut-out model {self.spec.name} "
            f"({got / 1e6:.0f} of {self.spec.size / 1e6:.0f} MB); try again shortly"
        )

    def load(self):
        from .engine import _session

        return _session(ensure_cutout(self.spec, self.cfg.cache_dir), self.cfg)


class CutoutEngine:
    """Every model in CUTOUT_MODELS, each downloaded on its first request,
    loaded on use and dropped when idle. The download runs in its own
    thread so a request never holds the inference gate while it waits.
    KIST_CUTOUT_MODEL is the model a request gets when it names none."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.default = spec_from_env().name
        self.models = {name: _Model(spec, cfg) for name, spec in CUTOUT_MODELS.items()}

    @property
    def model(self) -> str:
        return self.default

    def get(self, name: str | None) -> _Model:
        try:
            return self.models[name or self.default]
        except KeyError:
            raise ValueError(
                f"unknown cutout model {name!r}; this ml service knows "
                f"{', '.join(sorted(self.models))}"
            ) from None

    def status(self) -> dict:
        return {"default": self.default,
                "models": {n: m.status() for n, m in self.models.items()}}

    def prepare(self, data: bytes, name: str | None = None):
        """Decode and preprocess (outside the inference gate). Returns
        (model, input array, (width, height))."""
        from PIL import Image

        model = self.get(name)
        im = flatten(Image.open(io.BytesIO(data)))
        return model, preprocess(im, model.spec), im.size

    def mask_prepared(self, prepared) -> bytes:
        """Run the model; a PNG mask the size of the image sent."""
        import numpy as np
        from PIL import Image

        model, x, size = prepared
        sess = model.tower.get(model.load)
        out = sess.run(None, {sess.get_inputs()[0].name: x})[0]
        mask = postprocess(out, model.spec)
        im = Image.fromarray(np.clip(mask * 255.0 + 0.5, 0, 255).astype(np.uint8), "L")
        if im.size != size:
            im = im.resize(size, Image.Resampling.BILINEAR)
        buf = io.BytesIO()
        im.save(buf, format="PNG", compress_level=3)
        return buf.getvalue()

    def unload_idle(self) -> list[str]:
        import time

        now = time.monotonic()
        return [f"cutout:{n}" for n, m in self.models.items() if m.tower.unload_if_idle(now)]
