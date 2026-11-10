"""Units for Milestone 2 that need no database: the face model maths
(YuNet decoding, NMS, Umeyama alignment), the ml service with faces off,
namespaces and the cleanup helpers."""

import io

import numpy as np
import pytest
from PIL import Image

from kist.ml import faces as mlfaces
from kist.services import tags as tags_svc
from kist.services import vocabulary


def test_yunet_decoding_puts_a_cell_where_it_belongs():
    outs = {}
    for stride in mlfaces.STRIDES:
        cells = (mlfaces.DET_SIZE // stride) ** 2
        outs[f"cls_{stride}"] = np.zeros((1, cells, 1), np.float32)
        outs[f"obj_{stride}"] = np.zeros((1, cells, 1), np.float32)
        outs[f"bbox_{stride}"] = np.zeros((1, cells, 4), np.float32)
        outs[f"kps_{stride}"] = np.zeros((1, cells, 10), np.float32)
    cols = mlfaces.DET_SIZE // 16
    idx = 5 * cols + 7  # row 5, column 7 at stride 16
    outs["cls_16"][0, idx, 0] = 0.81
    outs["obj_16"][0, idx, 0] = 1.0
    outs["bbox_16"][0, idx] = [0.5, 0.25, np.log(4.0), np.log(5.0)]
    outs["kps_16"][0, idx] = [0.1, 0.2] * 5
    dets = mlfaces.decode(outs)
    assert dets.shape == (1, 15)
    x, y, w, h = dets[0, :4]
    assert (w, h) == (64.0, 80.0)
    assert (x + w / 2, y + h / 2) == ((7 + 0.5) * 16, (5 + 0.25) * 16)
    assert dets[0, 4] == pytest.approx((7 + 0.1) * 16) and dets[0, 5] == pytest.approx(
        (5 + 0.2) * 16)
    assert dets[0, 14] == pytest.approx(0.9)  # sqrt(cls * obj)


def test_nms_keeps_the_best_of_overlapping_boxes():
    def row(x, y, s):
        return [x, y, 50, 50] + [0] * 10 + [s]

    dets = np.array([row(0, 0, 0.9), row(5, 5, 0.95), row(200, 200, 0.8)], np.float32)
    kept = mlfaces.nms(dets)
    assert [round(float(r[14]), 2) for r in kept] == [0.95, 0.8]


def test_similarity_transform_recovers_rotation_scale_and_shift():
    src = np.array(mlfaces.TEMPLATE)
    angle, scale, shift = 0.3, 2.5, np.array([40.0, -12.0])
    rot = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    moved = (scale * (rot @ src.T)).T + shift
    t = mlfaces.similarity_transform(moved, src)
    back = (t[:2, :2] @ moved.T).T + t[:2, 2]
    assert np.allclose(back, src, atol=1e-6)


def test_align_maps_landmarks_onto_the_template():
    im = Image.new("RGB", (400, 400), (0, 0, 0))
    # a white dot at a landmark lands on the template's spot
    lm = [(150, 160), (250, 160), (200, 220), (160, 280), (240, 280)]
    for x, y in lm[:1]:
        im.paste((255, 255, 255), (x - 2, y - 2, x + 3, y + 3))
    out = np.asarray(mlfaces.align(im, lm))
    ys, xs = np.nonzero(out[:, :, 0] > 100)
    tx, ty = mlfaces.TEMPLATE[0]
    assert abs(xs.mean() - tx) < 2.5 and abs(ys.mean() - ty) < 2.5


def test_letterbox_scales_and_pads():
    x, scale = mlfaces.letterbox(Image.new("RGB", (1280, 640), (10, 20, 30)))
    assert x.shape == (1, 3, 640, 640) and scale == 0.5
    assert x[0, :, 0, 0].tolist() == [30.0, 20.0, 10.0]  # BGR
    assert x[0, :, 639, 0].tolist() == [0.0, 0.0, 0.0]  # padding below


def test_ml_service_faces_are_off_by_default(monkeypatch):
    from fastapi.testclient import TestClient

    from kist.ml.server import app

    monkeypatch.setenv("KIST_ML_PRELOAD", "")
    monkeypatch.delenv("KIST_FACES", raising=False)
    buf = io.BytesIO()
    Image.new("RGB", (64, 64)).save(buf, "PNG")
    with TestClient(app) as c:
        assert c.get("/health").json()["faces"] is False
        res = c.post("/faces", content=buf.getvalue())
        assert res.status_code == 404 and "KIST_FACES" in res.text


def test_face_models_are_pinned():
    from kist.ml.models import FACE_MODELS

    for spec in FACE_MODELS.values():
        assert len(spec.sha256) == 64 and "/48b10fa505aeb51fbd127fc60a5c660040e823b7/" in spec.url


@pytest.mark.parametrize("name, expected", [
    ("brand:Rolex", ("brand", "Rolex")),
    ("Place:New York", ("place", "New York")),
    ("Rolex", (None, "Rolex")),
    ("https://x.com", (None, "https://x.com")),
    (":x", (None, ":x")),
])
def test_namespaces_parse(name, expected):
    assert vocabulary.split_namespace(name) == expected


def test_cleanup_helpers():
    assert tags_svc.levenshtein("stationery", "staitionery") == 1
    assert tags_svc.levenshtein("watches", "clocks") > 2
    assert tags_svc._stem("Restaurants") == tags_svc._stem("restaurant")
    assert tags_svc._stem("Accessories") == "accessory"
    assert tags_svc._clash_key("Dark-Mode") == tags_svc._clash_key("dark mode")
    assert vocabulary.is_wildcard("brand:*") == "brand" and vocabulary.is_wildcard("x") is None
