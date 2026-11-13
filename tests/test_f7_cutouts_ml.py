"""F7 cutouts: the ml side (no models needed; real-model checks are opt-in
with KIST_ML_INTEGRATION=1 and KIST_CUTOUT_CACHE)."""

import io
import os
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from kist.ml import cutout as mlcut
from kist.ml.models import CUTOUT_MODELS, DEFAULT_CUTOUT_MODEL


def _png(size=(96, 64), color=(200, 30, 30), mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, "PNG")
    return buf.getvalue()


def test_cutout_models_are_pinned_and_open_licensed():
    assert DEFAULT_CUTOUT_MODEL in CUTOUT_MODELS
    for spec in CUTOUT_MODELS.values():
        assert re.fullmatch(r"[0-9a-f]{64}", spec.sha256)
        assert re.search(r"/resolve/[0-9a-f]{40}/", spec.url), spec.url
        assert spec.license in ("MIT", "Apache-2.0")
        assert spec.size > 1_000_000 and spec.side == 1024


def test_preprocess_and_postprocess():
    im = Image.new("RGB", (300, 200), (255, 128, 0))
    isnet = CUTOUT_MODELS["isnet-general-use"]
    x = mlcut.preprocess(im, isnet)
    assert x.shape == (1, 3, 1024, 1024) and x.dtype == np.float32
    assert abs(float(x[0, 0, 0, 0]) - 0.5) < 1e-5  # 255/255 - 0.5
    bi = CUTOUT_MODELS["birefnet-lite"]
    y = mlcut.preprocess(im, bi)
    assert abs(float(y[0, 0, 0, 0]) - (1 - 0.485) / 0.229) < 1e-4
    # birefnet gives logits; isnet a probability that is min-max stretched
    logits = np.zeros((1, 1, 1024, 1024), np.float32)
    assert float(mlcut.postprocess(logits, bi).mean()) == pytest.approx(0.5)
    p = np.full((1, 1, 1024, 1024), 0.2, np.float32)
    p[..., :10, :10] = 0.6
    m = mlcut.postprocess(p, isnet)
    assert float(m.max()) == pytest.approx(1.0) and float(m.min()) == pytest.approx(0.0)


def test_flatten_puts_transparent_pixels_on_white():
    im = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    assert mlcut.flatten(im).getpixel((0, 0)) == (255, 255, 255)


class _FakeSession:
    class _In:
        name = "input_image"

    def get_inputs(self):
        return [self._In()]

    def run(self, _outs, feeds):
        x = next(iter(feeds.values()))
        assert x.shape == (1, 3, 1024, 1024)
        out = np.full((1, 1, 1024, 1024), -8.0, np.float32)
        out[..., 256:768, 256:768] = 8.0  # the subject: the middle
        return [out]


@pytest.fixture
def ml_app(monkeypatch, tmp_path):
    monkeypatch.setenv("KIST_ML_PRELOAD", "")
    monkeypatch.setenv("KIST_ML_CACHE", str(tmp_path))
    monkeypatch.delenv("KIST_CUTOUTS", raising=False)
    monkeypatch.delenv("KIST_CUTOUT_MODEL", raising=False)
    from fastapi.testclient import TestClient

    from kist.ml.server import app

    with TestClient(app) as c:
        yield c


def test_ml_cutout_returns_a_mask_the_size_of_the_image(ml_app, monkeypatch):
    monkeypatch.setattr(mlcut._Model, "ensure", lambda self: None)
    monkeypatch.setattr(mlcut._Model, "load", lambda self: _FakeSession())
    res = ml_app.post("/cutout?model=birefnet-lite", content=_png((200, 100)))
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "image/png"
    assert res.headers["x-kist-model"] == "birefnet-lite"
    mask = Image.open(io.BytesIO(res.content))
    assert mask.mode == "L" and mask.size == (200, 100)
    assert mask.getpixel((100, 50)) > 250 and mask.getpixel((2, 2)) < 5
    health = ml_app.get("/health").json()["cutout"]
    assert health["default"] == "isnet-general-use"
    # the heavy model is dropped right after its cut-out (memory, Nov 2026);
    # the fast one stays loaded until idle
    assert health["models"]["birefnet-lite"]["loaded"] is False
    assert ml_app.post("/cutout", content=_png((200, 100))).status_code == 200
    assert ml_app.get("/health").json()["cutout"]["models"]["isnet-general-use"]["loaded"] is True
    assert ml_app.post("/cutout?model=nope", content=_png()).status_code == 422


def test_cutout_sessions_turn_off_the_cpu_arena(monkeypatch, tmp_path):
    import onnxruntime as ort

    from kist.ml import engine

    seen = []
    def fake(path, so, providers):
        seen.append(so)
        return object()

    monkeypatch.setattr(ort, "InferenceSession", fake)
    cfg = engine.EngineConfig(cache_dir=tmp_path, device="cpu")
    engine._session(tmp_path / "m.onnx", cfg, low_memory=True)
    engine._session(tmp_path / "m.onnx", cfg)
    assert seen[0].enable_cpu_mem_arena is False and seen[0].enable_mem_pattern is False
    assert seen[1].enable_cpu_mem_arena is True


def test_a_model_too_big_for_the_container_is_refused_before_it_runs(ml_app, monkeypatch):
    monkeypatch.setattr(mlcut, "memory_limit", lambda: 4 * 1024**3)
    loads = []
    monkeypatch.setattr(mlcut._Model, "ensure", lambda self: None)
    monkeypatch.setattr(mlcut._Model, "load", lambda self: loads.append(1) or _FakeSession())
    models = ml_app.get("/health").json()["cutout"]["models"]
    assert "KIST_ML_MEM_LIMIT=8g" in models["birefnet-lite"]["memory_problem"]
    assert models["isnet-general-use"]["memory_problem"] is None
    res = ml_app.post("/cutout?model=birefnet-lite", content=_png())
    assert res.status_code == 507 and "needs about 8 GB" in res.text and loads == []
    assert ml_app.post("/cutout", content=_png((200, 100))).status_code == 200
    monkeypatch.setattr(mlcut, "memory_limit", lambda: None)  # no limit: offered
    models = ml_app.get("/health").json()["cutout"]["models"]
    assert models["birefnet-lite"]["memory_problem"] is None


def test_ml_cutout_says_so_while_the_model_downloads(ml_app, monkeypatch):
    started = []

    def fetch(self):
        started.append(self.spec.name)

    monkeypatch.setattr(mlcut._Model, "_fetch", fetch)
    res = ml_app.post("/cutout", content=_png())
    assert res.status_code == 503 and "downloading the cut-out model" in res.text
    assert res.headers.get("retry-after") == "30"
    assert started == ["isnet-general-use"]
    st = ml_app.get("/health").json()["cutout"]["models"]["isnet-general-use"]
    assert st["state"] in ("downloading", "missing") and st["size_bytes"] == 178648008


def test_ml_cutouts_can_be_turned_off(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from kist.ml.server import app

    monkeypatch.setenv("KIST_ML_PRELOAD", "")
    monkeypatch.setenv("KIST_CUTOUTS", "0")
    with TestClient(app) as c:
        assert c.get("/health").json()["cutout"] is None
        res = c.post("/cutout", content=_png())
        assert res.status_code == 404 and "KIST_CUTOUTS" in res.text


@pytest.mark.skipif(not (os.environ.get("KIST_ML_INTEGRATION") and os.environ.get(
    "KIST_CUTOUT_CACHE")), reason="real cutout models: KIST_ML_INTEGRATION=1 KIST_CUTOUT_CACHE")
@pytest.mark.parametrize("name", sorted(CUTOUT_MODELS))
def test_real_cutout_model_finds_a_square_on_a_plain_background(name, monkeypatch):
    from kist.ml.engine import EngineConfig

    monkeypatch.setenv("KIST_CUTOUT_MODEL", name)
    eng = mlcut.CutoutEngine(EngineConfig(cache_dir=Path(os.environ["KIST_CUTOUT_CACHE"]),
                                          threads=4))
    im = Image.new("RGB", (640, 480), (235, 235, 235))
    im.paste((20, 60, 160), (220, 140, 420, 340))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    mask = Image.open(io.BytesIO(eng.mask_prepared(eng.prepare(buf.getvalue(), name))))
    assert mask.size == (640, 480)
    assert mask.getpixel((320, 240)) > 200 and mask.getpixel((20, 20)) < 50
