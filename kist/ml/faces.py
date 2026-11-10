"""Faces: OpenCV-zoo YuNet (detection) and SFace (recognition) on ONNX Runtime.

Opt-in (KIST_FACES=1 on the ml service); nothing here is loaded otherwise.
OpenCV isn't needed: the pre- and post-processing OpenCV's FaceDetectorYN /
FaceRecognizerSF do is reimplemented with numpy and Pillow:

- YuNet (2023mar) takes a fixed 640x640 BGR float image (0-255, no mean or
  scaling). The image is scaled to fit and padded bottom/right. Its twelve
  outputs are cls/obj/bbox/kps for strides 8, 16 and 32; a cell's score is
  sqrt(cls * obj), its box centre (c + dx, r + dy) * stride and its size
  exp(dw|dh) * stride, its five landmarks (c + kx, r + ky) * stride (right
  eye, left eye, nose tip, right and left mouth corner). Greedy NMS at IoU
  0.3 follows.
- SFace takes a 112x112 RGB face (0-255) aligned by a similarity transform
  (Umeyama) from the five landmarks onto ArcFace's template, and returns a
  128-d feature; two faces are the same person at cosine >= 0.363 (the
  threshold OpenCV publishes for this model).
"""

import io
import logging

from .models import FACE_MODELS, ensure_file

log = logging.getLogger("kist.ml.faces")

DET_SIZE = 640
STRIDES = (8, 16, 32)
SCORE_THRESHOLD = 0.75
NMS_IOU = 0.3
MIN_FACE_PX = 16  # in the original image; smaller detections are noise
MAX_FACES = 64
# ArcFace's 112x112 landmark template (OpenCV face_recognize.cpp)
TEMPLATE = (
    (38.2946, 51.6963),
    (73.5318, 51.5014),
    (56.0252, 71.7366),
    (41.5493, 92.3655),
    (70.7299, 92.2041),
)
SAME_PERSON_COSINE = 0.363


def letterbox(im, size: int = DET_SIZE):
    """(BGR float32 NCHW 1x3xSxS, scale): the image scaled to fit, padded."""
    import numpy as np
    from PIL import Image

    w, h = im.size
    scale = min(size / w, size / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    canvas.paste(im.resize((nw, nh), Image.Resampling.BILINEAR), (0, 0))
    a = np.asarray(canvas, dtype=np.float32)[:, :, ::-1]  # RGB -> BGR
    return np.ascontiguousarray(a.transpose(2, 0, 1)[None]), scale


def decode(outputs: dict, size: int = DET_SIZE, threshold: float = SCORE_THRESHOLD):
    """YuNet's raw outputs -> array of rows [x, y, w, h, 10 landmarks, score]
    in the letterboxed image's pixels, before NMS."""
    import numpy as np

    rows_out = []
    for stride in STRIDES:
        cols = size // stride
        rows = size // stride
        cls = np.clip(outputs[f"cls_{stride}"].reshape(-1), 0, 1)
        obj = np.clip(outputs[f"obj_{stride}"].reshape(-1), 0, 1)
        bbox = outputs[f"bbox_{stride}"].reshape(-1, 4)
        kps = outputs[f"kps_{stride}"].reshape(-1, 10)
        score = np.sqrt(cls * obj)
        keep = np.nonzero(score >= threshold)[0]
        if keep.size == 0:
            continue
        r, c = np.divmod(keep, cols)
        assert rows * cols == cls.size
        b = bbox[keep]
        cx = (c + b[:, 0]) * stride
        cy = (r + b[:, 1]) * stride
        w = np.exp(b[:, 2]) * stride
        h = np.exp(b[:, 3]) * stride
        k = kps[keep].copy()
        k[:, 0::2] = (k[:, 0::2] + c[:, None]) * stride
        k[:, 1::2] = (k[:, 1::2] + r[:, None]) * stride
        rows_out.append(np.column_stack([cx - w / 2, cy - h / 2, w, h, k, score[keep]]))
    if not rows_out:
        return np.zeros((0, 15), dtype=np.float32)
    return np.concatenate(rows_out).astype(np.float32)


def nms(dets, iou: float = NMS_IOU):
    """Greedy non-maximum suppression; returns the kept rows, best first."""
    import numpy as np

    if len(dets) == 0:
        return dets
    order = np.argsort(-dets[:, 14])
    x1, y1 = dets[:, 0], dets[:, 1]
    x2, y2 = x1 + dets[:, 2], y1 + dets[:, 3]
    area = dets[:, 2] * dets[:, 3]
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        rest = order[1:]
        ix1 = np.maximum(x1[i], x1[rest])
        iy1 = np.maximum(y1[i], y1[rest])
        ix2 = np.minimum(x2[i], x2[rest])
        iy2 = np.minimum(y2[i], y2[rest])
        inter = np.clip(ix2 - ix1, 0, None) * np.clip(iy2 - iy1, 0, None)
        overlap = inter / (area[i] + area[rest] - inter + 1e-9)
        order = rest[overlap <= iou]
    return dets[keep]


def similarity_transform(src, dst):
    """Umeyama's least-squares similarity (rotation, uniform scale,
    translation) mapping src points onto dst; a 3x3 matrix."""
    import numpy as np

    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    n = src.shape[0]
    src_mean, dst_mean = src.mean(0), dst.mean(0)
    src_d, dst_d = src - src_mean, dst - dst_mean
    a = dst_d.T @ src_d / n
    d = np.ones(2)
    if np.linalg.det(a) < 0:
        d[1] = -1
    u, s, vt = np.linalg.svd(a)
    t = np.eye(3)
    t[:2, :2] = u @ np.diag(d) @ vt
    var = src_d.var(0).sum()
    scale = (s @ d) / var if var > 0 else 1.0
    t[:2, 2] = dst_mean - scale * (t[:2, :2] @ src_mean)
    t[:2, :2] *= scale
    return t


def align(im, landmarks, size: int = 112):
    """The face warped onto the 112x112 template (a PIL RGB image)."""
    import numpy as np
    from PIL import Image

    t = similarity_transform(landmarks, TEMPLATE)
    inv = np.linalg.inv(t)  # PIL maps output pixels back to input pixels
    coeffs = (inv[0, 0], inv[0, 1], inv[0, 2], inv[1, 0], inv[1, 1], inv[1, 2])
    return im.transform((size, size), Image.Transform.AFFINE, coeffs,
                        resample=Image.Resampling.BILINEAR)


class FaceEngine:
    """YuNet + SFace, each loaded on first use and dropped when idle."""

    def __init__(self, cfg):
        from .engine import _Tower

        self.cfg = cfg
        self.detector = _Tower(cfg.idle_seconds)
        self.recognizer = _Tower(cfg.idle_seconds)

    @property
    def model(self) -> str:
        d, r = FACE_MODELS["detector"], FACE_MODELS["recognizer"]
        return f"{d.name}+{r.name}"

    def _load_detector(self):
        from .engine import _session

        return _session(ensure_file(FACE_MODELS["detector"], self.cfg.cache_dir), self.cfg)

    def _load_recognizer(self):
        from .engine import _session

        return _session(ensure_file(FACE_MODELS["recognizer"], self.cfg.cache_dir), self.cfg)

    def detect(self, im) -> list[dict]:
        """Faces in a PIL RGB image: [{box: [x, y, w, h], landmarks, score}]
        in the image's own pixels, best first."""
        sess = self.detector.get(self._load_detector)
        x, scale = letterbox(im)
        names = [o.name for o in sess.get_outputs()]
        outs = dict(zip(names, sess.run(None, {sess.get_inputs()[0].name: x}), strict=True))
        dets = nms(decode(outs))
        faces = []
        w, h = im.size
        for row in dets[:MAX_FACES]:
            bx, by, bw, bh = (float(v) / scale for v in row[:4])
            if min(bw, bh) < MIN_FACE_PX:
                continue
            x0, y0 = max(0.0, bx), max(0.0, by)
            x1, y1 = min(float(w), bx + bw), min(float(h), by + bh)
            if x1 - x0 < MIN_FACE_PX / 2 or y1 - y0 < MIN_FACE_PX / 2:
                continue
            lm = [[float(row[4 + 2 * i]) / scale, float(row[5 + 2 * i]) / scale]
                  for i in range(5)]
            faces.append({"box": [x0, y0, x1 - x0, y1 - y0], "landmarks": lm,
                          "score": float(row[14])})
        return faces

    def embed(self, im, landmarks) -> list[float]:
        import numpy as np

        sess = self.recognizer.get(self._load_recognizer)
        face = np.asarray(align(im, landmarks), dtype=np.float32)  # RGB, 0-255
        x = np.ascontiguousarray(face.transpose(2, 0, 1)[None])
        out = sess.run(None, {sess.get_inputs()[0].name: x})[0].reshape(-1)
        n = float(np.linalg.norm(out))
        return (out / n if n > 0 else out).tolist()

    def faces(self, data: bytes) -> dict:
        """Detect and embed every face in an image. Boxes and landmarks are
        returned in pixels of the image as sent, plus its size."""
        from PIL import Image, ImageOps

        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        found = self.detect(im)
        for f in found:
            f["embedding"] = self.embed(im, f["landmarks"])
        return {"model": self.model, "width": im.width, "height": im.height, "faces": found}

    def unload_idle(self) -> list[str]:
        import time

        now = time.monotonic()
        return [n for n, t in (("face-detector", self.detector),
                               ("face-recognizer", self.recognizer))
                if t.unload_if_idle(now)]
