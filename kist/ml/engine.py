"""ONNX Runtime engines: CLIP image/text towers and RapidOCR.

Thread-safe: ONNX Runtime sessions may be run from several threads at once.
Each tower loads on first use and is dropped again after `idle_seconds`
without a request (`unload_idle()`), so a quiet library costs no memory
for the text tower or OCR.

Devices (KIST_ML_DEVICE):
    cpu           ONNX Runtime CPU (the default; the NAS)
    auto          CoreML on macOS when ONNX Runtime has it, else CPU
    coreml        CoreML on the Neural Engine (Apple silicon), CPU for
                  anything it can't run. Barely faster than the CPU for
                  ViT-B-32 on an M6 and slow to start (it compiles the model
                  for 40-75 s), so the helper defaults to cpu
    openvino-gpu  Intel GPU through OpenVINO (an Iris Xe on the NAS); needs
                  the onnxruntime-openvino build of ONNX Runtime and /dev/dri
A device that isn't available falls back to CPU with a warning, so a bad
setting slows things down but never stops them.
"""

import io
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .models import DEFAULT_BASE_URL, ModelSpec, ensure_files, spec_for

log = logging.getLogger("kist.ml.engine")

DEVICES = ("cpu", "auto", "coreml", "openvino-gpu")
OCR_MAX_SIDE = 2048


@dataclass
class EngineConfig:
    model: str = "ViT-B-32__openai"
    cache_dir: Path = Path("/models")
    base_url: str = DEFAULT_BASE_URL
    threads: int = 4
    device: str = "cpu"
    idle_seconds: float = 300.0

    @classmethod
    def from_env(cls, **defaults) -> "EngineConfig":
        env = os.environ
        cfg = cls(**defaults)
        cfg.model = env.get("KIST_ML_MODEL") or cfg.model
        if env.get("KIST_ML_CACHE"):
            cfg.cache_dir = Path(env["KIST_ML_CACHE"])
        cfg.base_url = env.get("KIST_ML_BASE_URL") or cfg.base_url
        if env.get("KIST_ML_THREADS"):
            cfg.threads = int(env["KIST_ML_THREADS"])
        cfg.device = (env.get("KIST_ML_DEVICE") or cfg.device).strip().lower()
        if env.get("KIST_ML_IDLE_SECONDS"):
            cfg.idle_seconds = float(env["KIST_ML_IDLE_SECONDS"])
        return cfg


def providers_for(device: str, cache_dir: Path) -> list:
    """The ONNX Runtime execution providers for a device setting."""
    import onnxruntime as ort

    available = set(ort.get_available_providers())
    device = device if device in DEVICES else "cpu"
    if device in ("auto", "coreml") and "CoreMLExecutionProvider" in available:
        coreml_cache = cache_dir / "coreml-cache"
        coreml_cache.mkdir(parents=True, exist_ok=True)
        return [
            ("CoreMLExecutionProvider", {
                "ModelFormat": "MLProgram",
                # measured on an M6 (ViT-B-32): Neural Engine 7.8 ms, CPU 8.8 ms,
                # ALL/GPU 19 ms; every first load compiles for 40-75 s
                "MLComputeUnits": "CPUAndNeuralEngine",
                "ModelCacheDirectory": str(coreml_cache),
            }),
            "CPUExecutionProvider",
        ]
    if device == "openvino-gpu":
        if "OpenVINOExecutionProvider" in available:
            ov_cache = cache_dir / "openvino-cache"
            ov_cache.mkdir(parents=True, exist_ok=True)
            return [
                ("OpenVINOExecutionProvider", {"device_type": "GPU", "cache_dir": str(ov_cache)}),
                "CPUExecutionProvider",
            ]
        log.warning("KIST_ML_DEVICE=openvino-gpu but this ONNX Runtime has no OpenVINO "
                    "provider (build the ml image with OPENVINO=1); using the CPU")
    elif device == "coreml":
        log.warning("KIST_ML_DEVICE=coreml but CoreML isn't available here; using the CPU")
    return ["CPUExecutionProvider"]


def _session(path: Path, cfg: EngineConfig):
    import onnxruntime as ort

    so = ort.SessionOptions()
    if cfg.threads > 0:
        so.intra_op_num_threads = cfg.threads
    so.inter_op_num_threads = 1
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.log_severity_level = 3
    providers = providers_for(cfg.device, cfg.cache_dir)
    try:
        return ort.InferenceSession(str(path), so, providers=providers)
    except Exception as exc:
        if providers == ["CPUExecutionProvider"]:
            raise
        log.warning("could not start %s (%s); using the CPU", providers[0], exc)
        return ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])


def _normalize(vec):
    import numpy as np

    v = np.asarray(vec, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


class _Tower:
    """Lazily loaded, idle-unloaded state shared by the three engines."""

    def __init__(self, idle_seconds: float):
        self.idle_seconds = idle_seconds
        self.lock = threading.Lock()
        self.loaded = None
        self.last_used = 0.0

    def get(self, loader):
        with self.lock:
            if self.loaded is None:
                t0 = time.monotonic()
                self.loaded = loader()
                log.info("loaded %s in %.1f s", getattr(loader, "__name__", "model"),
                         time.monotonic() - t0)
            self.last_used = time.monotonic()
            return self.loaded

    def unload_if_idle(self, now: float) -> bool:
        with self.lock:
            if self.loaded is not None and self.idle_seconds > 0 and (
                now - self.last_used > self.idle_seconds
            ):
                self.loaded = None
                return True
        return False


class ClipEngine:
    def __init__(self, cfg: EngineConfig):
        self.cfg = cfg
        self.spec: ModelSpec = spec_for(cfg.model)
        self.visual = _Tower(cfg.idle_seconds)
        self.textual = _Tower(cfg.idle_seconds)

    @property
    def model(self) -> str:
        return self.spec.name

    @property
    def dim(self) -> int:
        return self.spec.dim

    # ---------- images ----------

    def _load_visual(self):
        root = ensure_files(self.spec, "visual", self.cfg.cache_dir, self.cfg.base_url)
        pre = json.loads((root / "visual" / "preprocess_cfg.json").read_text())
        sess = _session(root / "visual" / "model.onnx", self.cfg)
        return sess, pre

    def preprocess(self, data: bytes, pre: dict):
        import numpy as np
        from PIL import Image, ImageOps

        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                im = Image.alpha_composite(bg, im)
            im = im.convert("RGB")
        w_out, h_out = pre.get("size", [224, 224])
        resample = Image.Resampling.BICUBIC
        if pre.get("resize_mode") == "squash":
            im = im.resize((w_out, h_out), resample)
        else:  # "shortest": shorter side to size, then a centre crop
            w, h = im.size
            s = max(w_out / w, h_out / h)
            im = im.resize((max(w_out, round(w * s)), max(h_out, round(h * s))), resample)
            w, h = im.size
            left, top = (w - w_out) // 2, (h - h_out) // 2
            im = im.crop((left, top, left + w_out, top + h_out))
        a = np.asarray(im, dtype=np.float32) / 255.0
        mean = np.array(pre.get("mean", [0.5] * 3), dtype=np.float32)
        std = np.array(pre.get("std", [0.5] * 3), dtype=np.float32)
        return ((a - mean) / std).transpose(2, 0, 1)[None].astype(np.float32)

    def embed_image(self, data: bytes) -> list[float]:
        return self.embed_prepared(self.prepare(data))

    def prepare(self, data: bytes):
        """Decode and preprocess one image (no inference: callers run this
        outside the inference gate, so decoding overlaps other requests)."""
        _sess, pre = self.visual.get(self._load_visual)
        return self.preprocess(data, pre)

    def embed_prepared(self, x) -> list[float]:
        sess, _pre = self.visual.get(self._load_visual)
        out = sess.run(None, {sess.get_inputs()[0].name: x})[0]
        return _normalize(out).tolist()

    def prepare_many(self, datas: list[bytes]) -> list:
        """prepare() for several images on a few threads; an image that
        can't be read gives its error message instead of an array."""
        from concurrent.futures import ThreadPoolExecutor

        def one(data: bytes):
            try:
                return self.prepare(data)
            except (OSError, ValueError) as exc:  # PIL can't read it
                return f"can't process this image: {exc}"

        if len(datas) <= 1:
            return [one(d) for d in datas]
        with ThreadPoolExecutor(max_workers=min(4, len(datas)),
                                thread_name_prefix="ml-decode") as pool:
            return list(pool.map(one, datas))

    def embed_many(self, prepared: list) -> list[list[float] | str]:
        """Vectors for prepare_many()'s output. The ONNX exports take one
        image per run (their batch dimension is fixed at 1), so this loops;
        the saving is in one request, one load check and decoding in
        parallel. Errors pass through as strings."""
        sess, _pre = self.visual.get(self._load_visual)
        name = sess.get_inputs()[0].name
        out: list[list[float] | str] = []
        for x in prepared:
            if isinstance(x, str):
                out.append(x)
                continue
            out.append(_normalize(sess.run(None, {name: x})[0]).tolist())
        return out

    # ---------- text ----------

    def _load_textual(self):
        from tokenizers import Tokenizer

        root = ensure_files(self.spec, "textual", self.cfg.cache_dir, self.cfg.base_url)
        model_cfg = json.loads((root / "config.json").read_text())
        tok_cfg = json.loads((root / "textual" / "tokenizer_config.json").read_text())
        context = int(model_cfg.get("text_cfg", {}).get("context_length", 77))
        pad = tok_cfg.get("pad_token")
        if isinstance(pad, dict):
            pad = pad.get("content")
        tok = Tokenizer.from_file(str(root / "textual" / "tokenizer.json"))
        pad_id = tok.token_to_id(pad) if pad else 0
        tok.enable_padding(length=context, pad_token=pad or "", pad_id=pad_id or 0)
        tok.enable_truncation(max_length=context)
        canonicalize = (
            model_cfg.get("text_cfg", {}).get("tokenizer_kwargs", {}).get("clean")
            == "canonicalize"
        )
        sess = _session(root / "textual" / "model.onnx", self.cfg)
        return sess, tok, canonicalize

    def embed_text(self, text: str) -> list[float]:
        import string

        import numpy as np

        sess, tok, canonicalize = self.textual.get(self._load_textual)
        text = " ".join(text.split())
        if canonicalize:
            text = text.translate(str.maketrans("", "", string.punctuation)).lower()
        ids = tok.encode(text).ids
        inp = sess.get_inputs()[0]
        dtype = np.int64 if "int64" in inp.type else np.int32
        out = sess.run(None, {inp.name: np.array([ids], dtype=dtype)})[0]
        return _normalize(out).tolist()

    def unload_idle(self) -> list[str]:
        now = time.monotonic()
        return [n for n, t in (("visual", self.visual), ("textual", self.textual))
                if t.unload_if_idle(now)]


class OcrEngine:
    """RapidOCR (PP-OCRv4 detection + recognition, Chinese/English models)."""

    def __init__(self, cfg: EngineConfig):
        self.cfg = cfg
        self.tower = _Tower(cfg.idle_seconds)

    @property
    def model(self) -> str:
        try:
            from importlib.metadata import version

            return f"rapidocr-onnxruntime-{version('rapidocr-onnxruntime')}"
        except Exception:
            return "rapidocr-onnxruntime"

    def _load_ocr(self):
        from rapidocr_onnxruntime import RapidOCR

        return RapidOCR(intra_op_num_threads=max(self.cfg.threads, 1), inter_op_num_threads=1)

    def ocr(self, data: bytes) -> dict:
        import numpy as np
        from PIL import Image, ImageOps

        engine = self.tower.get(self._load_ocr)
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        scale = 1.0
        if max(im.size) > OCR_MAX_SIDE:
            scale = OCR_MAX_SIDE / max(im.size)
            im = im.resize((round(im.width * scale), round(im.height * scale)),
                           Image.Resampling.LANCZOS)
        # RapidOCR expects OpenCV's BGR order
        result, _ = engine(np.asarray(im)[:, :, ::-1].copy())
        boxes = []
        for box, text, score in result or []:
            boxes.append({
                "box": [[round(x / scale), round(y / scale)] for x, y in box],
                "text": text,
                "score": round(float(score), 3),
            })
        return {"model": self.model, "lang": None, "text": _reading_order(boxes),
                "boxes": boxes}

    def unload_idle(self) -> list[str]:
        return ["ocr"] if self.tower.unload_if_idle(time.monotonic()) else []


def _reading_order(boxes: list[dict]) -> str:
    """Lines top to bottom, left to right within a line."""
    if not boxes:
        return ""
    rows: list[list[dict]] = []
    for b in sorted(boxes, key=lambda b: min(p[1] for p in b["box"])):
        ys = [p[1] for p in b["box"]]
        top, bottom = min(ys), max(ys)
        mid = (top + bottom) / 2
        if rows:
            last = rows[-1][0]
            lys = [p[1] for p in last["box"]]
            if min(lys) <= mid <= max(lys):
                rows[-1].append(b)
                continue
        rows.append([b])
    lines = [" ".join(b["text"] for b in sorted(r, key=lambda b: min(p[0] for p in b["box"])))
             for r in rows]
    return "\n".join(lines)
