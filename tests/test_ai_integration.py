"""The real models end to end (opt-in: downloads ~600 MB on first run).

    KIST_ML_INTEGRATION=1 KIST_ML_CACHE=~/.cache/kist-ml pytest tests/test_ai_integration.py

The ml app runs in-process (the same code as the ml container) and the
worker's AI jobs go to it, so this covers the ONNX towers, the tokenizer,
RapidOCR, the sidecars and semantic ordering together.
"""

import io
import os

import httpx
import pytest
import sqlalchemy as sa
from PIL import Image, ImageDraw, ImageFont

from kist.config import get_settings
from kist.db import SessionLocal
from kist.models import ItemText
from kist.services import ai

pytestmark = pytest.mark.skipif(
    os.environ.get("KIST_ML_INTEGRATION") != "1",
    reason="set KIST_ML_INTEGRATION=1 to run the real models",
)


def png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def text_image(text: str) -> bytes:
    im = Image.new("RGB", (900, 300), "white")
    d = ImageDraw.Draw(im)
    d.text((40, 100), text, fill="black", font=ImageFont.load_default(size=72))
    return png(im)


@pytest.fixture
def real_ml(clean, monkeypatch):
    from fastapi.testclient import TestClient

    from kist.ml.server import app as ml_app

    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ml_url", "http://ml.test")
    monkeypatch.setenv("KIST_ML_PRELOAD", "")
    with TestClient(ml_app) as ml:
        def forward(request: httpx.Request) -> httpx.Response:
            r = ml.request(request.method, request.url.path, content=request.content,
                           headers=dict(request.headers))
            return httpx.Response(r.status_code, headers=r.headers, content=r.content)

        ai.set_transport(httpx.MockTransport(forward))
        yield ml
    ai.set_transport(None)


def test_real_models(client, upload, real_ml):
    from test_ai import drain

    ids = {}
    for name, rgb in (("red.png", (220, 20, 20)), ("green.png", (20, 200, 40)),
                      ("blue.png", (20, 40, 220))):
        ids[name] = upload(name, data=png(Image.new("RGB", (400, 300), rgb)))["item"]["id"]
    doc = upload("invoice.png", data=text_image("INVOICE 4471 Orvis"))["item"]["id"]
    assert drain() == 8  # 4 embeddings + 4 OCR runs

    health = real_ml.get("/health").json()
    assert health["loaded"]["visual"] and health["loaded"]["ocr"]

    for word, expected in (("a red square", "red.png"), ("a blue square", "blue.png"),
                           ("green", "green.png")):
        page = client.get("/api/items", params={"semantic": word}).json()
        assert page["items"][0]["id"] == ids[expected], word
    page = client.get("/api/items", params={"semantic": "a page of text"}).json()
    assert page["items"][0]["id"] == doc

    with SessionLocal() as s:
        text = s.get(ItemText, (doc, "ocr")).text
    assert "INVOICE" in text.upper() and "4471" in text

    # text in images, with the hit marked
    page = client.get("/api/items", params={"text": "invoice"}).json()
    assert [i["id"] for i in page["items"]] == [doc]
    assert "<<INVOICE>>" in page["items"][0]["match"]["snippet"].upper()


def test_real_batched_embeddings_match_single_ones(client, upload, real_ml):
    from kist import worker
    from kist.models import ItemEmbedding

    ids = [upload(f"{n}.png", data=png(Image.new("RGB", (300, 200), rgb)))["item"]["id"]
           for n, rgb in (("a", (200, 30, 30)), ("b", (30, 200, 30)), ("c", (30, 30, 200)))]
    with SessionLocal() as s:
        jobs = ai.claim_batch(s, runner="worker", kinds=("embed",), batch=8)
    assert len(jobs) == 3
    assert worker.run_ai_batch([j.id for j in jobs]) == "done"
    thumbs = get_settings().items_dir
    for item_id in ids:
        single = ai.ml_embed_image((thumbs / item_id / "thumb_512.webp").read_bytes())
        with SessionLocal() as s:
            stored = s.execute(sa.select(ItemEmbedding.vector).where(
                ItemEmbedding.item_id == item_id)).scalar_one()
        assert sum(a * b for a, b in zip(single, stored, strict=True)) > 0.999
