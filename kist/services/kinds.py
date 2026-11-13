"""Utility collections ("Kinds", Kist 2.0 F1): Screenshots, Documents & pages,
Drawings (Pencil & graphite, Ink & line art), Paintings, Photos, Low-res, and
the trade packs. Automatic, correctable, and learning from corrections.

How an item is sorted
---------------------
For every enabled kind (kinds_catalog.py) an item gets a score in 0..1:
- CLIP zero-shot on the embedding the item already has: the kind's prompts
  (averaged) against its alternatives, softmax at CLIP's temperature. A
  child kind (Pencil under Drawings) is scored against its siblings only,
  and only for items that are in the parent;
- plain rules on the file (rule kinds, or rules mixed with CLIP): PDF, OCR
  word count, camera EXIF, screen-sized PNGs, pixel size, provenance;
- once a kind has LEARN_MIN corrections, a small logistic regression on the
  CLIP embedding (numpy, Newton steps, L2) that starts from the zero-shot
  score as a fixed offset: corrections nudge the zero-shot call towards
  what this library means by "ink".
An item is in a kind automatically (`auto`) when its score reaches the
kind's threshold, which the per-kind strictness slider sets. The person's
verdict ("Belongs here" / "Not this") always wins over `auto`; the generated
column item_kinds.member is coalesce(verdict, auto).

What is durable where
---------------------
- verdicts: items/<id>/ai/kinds.json (rebuild-index loads them, verify
  checks them, rewrite-meta rewrites them);
- settings (packs, per-kind on/off and strictness, how many items before a
  kind shows in the sidebar, the low-res size): library/.kist/kinds.json;
- derived caches, safe to delete: .kist/kinds-prompts.json (the prompts'
  text vectors per model, so sorting works while the ml service is down)
  and .kist/kinds-classifiers.json (the learned weights; retrained from
  the verdicts and embeddings when missing);
- automatic calls: recomputed (item_kind_passes records the signature an
  item was sorted with, so `kist kinds-backfill` and the worker only do
  missing work).

Sorting never moves an item's modified_at, and neither does a correction
(it isn't an edit of the item; it is recorded in the item's history and
undoable).
"""

import hashlib
import json
import logging
import math
import re
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .. import __version__, access
from ..config import get_settings
from ..filetypes import kind_for
from ..lifecycle import JobStatus
from ..models import Item, ItemEmbedding, ItemEvent, ItemKind, ItemKindPass, ItemText, Job
from . import events, validation
from . import kinds_catalog as catalog
from .items import NotFound as _ItemNotFound

log = logging.getLogger("kist.kinds")

JOB_KIND = "kinds"
SETTINGS_FILE = "kinds.json"
PROMPTS_FILE = "kinds-prompts.json"
CLASSIFIERS_FILE = "kinds-classifiers.json"
VERDICT_FILE = "kinds.json"  # items/<id>/ai/kinds.json
EVENT_KIND = "kinds"
RULES_VERSION = 1
TEMPERATURE = 100.0
# rows below this score (and without a verdict) aren't kept
FLOOR = 0.12
# strictness 0..1 -> threshold
T_MIN, T_MAX = 0.1, 0.9
DEFAULT_MIN_ITEMS = 20
DEFAULT_LOW_RES_PX = 1200
LEARN_MIN = 10
L2 = 1.0
L2_SCALE = 3.0
OFFSET_CLIP = 8.0
CHUNK = 500
BATCH_PER_JOB = 2000
MAX_CORRECT = 1000
UNSURE_BAND = 0.15
DEFAULT_STRICTNESS = {"screenshots": 0.4, "drawings": 0.3, "paintings": 0.3,
                      "pencil": 0.8, "ink": 0.8}

# known screen sizes (portrait and landscape both match)
SCREEN_SIZES = frozenset({
    # iPhone
    (640, 1136), (750, 1334), (828, 1792), (1080, 1920), (1125, 2436), (1170, 2532),
    (1179, 2556), (1206, 2622), (1242, 2208), (1242, 2688), (1284, 2778), (1290, 2796),
    (1320, 2868), (1080, 2340),
    # Android
    (720, 1600), (1080, 2400), (1080, 2280), (1080, 2160), (1440, 3200), (1440, 3088),
    (1440, 3120), (1440, 2960), (1080, 2408), (1220, 2712), (1344, 2992),
    # iPad
    (1536, 2048), (1620, 2160), (1640, 2360), (1668, 2224), (1668, 2388), (2048, 2732),
    (1488, 2266), (2064, 2752), (1640, 2360),
    # desktop
    (1366, 768), (1440, 900), (1536, 864), (1680, 1050), (1920, 1080), (1920, 1200),
    (2560, 1440), (2560, 1600), (2880, 1800), (2940, 1912), (3024, 1964), (3456, 2234),
    (3420, 2224), (3840, 2160), (5120, 2880), (3440, 1440), (2736, 1824), (2256, 1504),
})
SCREEN_SIZES = frozenset(SCREEN_SIZES | {(h, w) for w, h in SCREEN_SIZES})
SCREENSHOT_NAME = re.compile(
    r"screen ?shot|screenshot|cleanshot|bildschirmfoto|capture d.{1,2}[ée]cran|schermata|"
    r"captura de pantalla|スクリーンショット|^scr_\d|^screen[-_ ]?\d", re.IGNORECASE)
COLOUR_CODE = re.compile(r"\bPANTONE\b|\bTCX\b|\bTPG\b|\bTPX\b|\b\d{2}-\d{4}\b|\bRAL ?\d{4}\b",
                         re.IGNORECASE)
FILM_ASPECTS = (2.39, 2.35, 2.0, 1.85, 16 / 9)
AI_SOURCE_TYPES = ("trainedalgorithmicmedia", "compositewithtrainedalgorithmicmedia",
                   "algorithmicmedia", "compositesynthetic")

# F4 print check: a function item -> (width_in, height_in) at 300 dpi, or None.
# F4 registers it with register_print_size(); without it, pixels decide.
_print_size: Callable[[Item], tuple[float, float] | None] | None = None


def register_print_size(fn: Callable[[Item], tuple[float, float] | None] | None) -> None:
    """Hook for F4's print-size data (Low-res reads it when present)."""
    global _print_size
    _print_size = fn


class NotFound(_ItemNotFound):
    """An unknown kind (HTTP 404, like an unknown item)."""


# ---------- settings (library/.kist/kinds.json) ----------

_settings_cache: dict[str, Any] = {}
_cache_lock = threading.Lock()


def _state_path(name: str):
    return get_settings().state_dir / name


def _read_json_file(name: str) -> dict | None:
    """A .kist JSON file, re-read when its mtime changes."""
    path = _state_path(name)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    key = str(path)
    with _cache_lock:
        hit = _settings_cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("%s unreadable; using defaults", path)
        return None
    if not isinstance(data, dict):
        return None
    with _cache_lock:
        _settings_cache[key] = (mtime, data)
    return data


def _write_json_file(session: Session | None, name: str, data: dict) -> None:
    from . import meta as meta_svc

    if session is not None:
        meta_svc.advisory_lock(session, f"kinds:{name}")
    meta_svc.atomic_write_text(_state_path(name),
                               json.dumps(data, indent=2, ensure_ascii=False))
    with _cache_lock:
        _settings_cache.pop(str(_state_path(name)), None)


def settings() -> dict:
    """The kinds settings, defaults filled in."""
    raw = _read_json_file(SETTINGS_FILE) or {}
    packs = {p: (p == "base") for p in catalog.PACKS}
    for p, on in (raw.get("packs") or {}).items():
        if p in packs and p != "base":
            packs[p] = bool(on)
    kinds: dict[str, dict] = {}
    for k in catalog.KINDS:
        own = (raw.get("kinds") or {}).get(k.id) or {}
        enabled = own.get("enabled")
        strictness = own.get("strictness")
        if not isinstance(strictness, (int, float)) or not 0 <= strictness <= 1:
            strictness = DEFAULT_STRICTNESS.get(k.id, k.default_strictness)
        kinds[k.id] = {"enabled": enabled if isinstance(enabled, bool) else None,
                       "strictness": float(strictness)}
    min_items = raw.get("min_items")
    low_res = raw.get("low_res_px")
    return {
        "packs": packs,
        "kinds": kinds,
        "min_items": int(min_items) if isinstance(min_items, int) and min_items >= 0
        else DEFAULT_MIN_ITEMS,
        "low_res_px": int(low_res) if isinstance(low_res, int) and 16 <= low_res <= 100_000
        else DEFAULT_LOW_RES_PX,
    }


def is_enabled(kind_id: str, s: dict | None = None) -> bool:
    s = s or settings()
    k = catalog.BY_ID[kind_id]
    override = s["kinds"][kind_id]["enabled"]
    if override is not None:
        on = override
    else:
        on = not k.opt_in and any(s["packs"].get(p) for p in k.packs)
    if on and k.parent and not is_enabled(k.parent, s):
        return False  # no Pencil without Drawings
    return on


def enabled_kinds(s: dict | None = None) -> list[catalog.KindDef]:
    if not get_settings().kinds:
        return []
    s = s or settings()
    return [k for k in catalog.KINDS if is_enabled(k.id, s)]


def threshold(kind_id: str, s: dict | None = None) -> float:
    s = s or settings()
    return T_MIN + (T_MAX - T_MIN) * s["kinds"][kind_id]["strictness"]


def update_settings(session: Session, *, packs: dict | None = None,
                    min_items: int | None = None, low_res_px: int | None = None,
                    kinds: dict | None = None) -> dict:
    """Change settings (any subset). kinds: {id: {enabled: bool|None,
    strictness: 0..1}}. Strictness applies at once; anything that changes
    which items are in a kind queues a re-sort."""
    access.require(access.Action.EDIT)
    raw = dict(_read_json_file(SETTINGS_FILE) or {})
    raw.setdefault("version", 1)
    if packs:
        cur = dict(raw.get("packs") or {})
        for p, on in packs.items():
            if p not in catalog.PACKS:
                raise validation.ValidationError(
                    f"unknown pack {p!r}; expected one of {', '.join(catalog.PACKS)}")
            if p == "base":
                raise validation.ValidationError(
                    "the base kinds are always offered; switch single kinds off instead")
            cur[p] = bool(on)
        raw["packs"] = cur
    if min_items is not None:
        if not 0 <= int(min_items) <= 100_000:
            raise validation.ValidationError("min_items must be 0-100000")
        raw["min_items"] = int(min_items)
    if low_res_px is not None:
        if not 16 <= int(low_res_px) <= 100_000:
            raise validation.ValidationError("low_res_px must be 16-100000")
        raw["low_res_px"] = int(low_res_px)
    strict_changed: list[str] = []
    if kinds:
        cur = {k: dict(v) for k, v in (raw.get("kinds") or {}).items()}
        for kid, patch in kinds.items():
            if kid not in catalog.BY_ID:
                raise NotFound(f"unknown kind {kid!r}")
            entry = cur.setdefault(kid, {})
            if "enabled" in patch:
                v = patch["enabled"]
                if v is not None and not isinstance(v, bool):
                    raise validation.ValidationError("enabled must be true, false or null")
                entry["enabled"] = v
            if "strictness" in patch and patch["strictness"] is not None:
                v = float(patch["strictness"])
                if not 0 <= v <= 1 or math.isnan(v):
                    raise validation.ValidationError("strictness must be between 0 and 1")
                entry["strictness"] = round(v, 3)
                strict_changed.append(kid)
        raw["kinds"] = cur
    _write_json_file(session, SETTINGS_FILE, raw)
    s = settings()
    for kid in strict_changed:  # instant: the stored scores against the new line
        session.execute(sa.update(ItemKind).where(ItemKind.kind == kid, ItemKind.score.isnot(None))
                        .values(auto=ItemKind.score >= threshold(kid, s)))
    session.commit()
    _drop_disabled(session, s)
    queue_all(session)
    return s


def _drop_disabled(session: Session, s: dict) -> None:
    """A kind switched off keeps its verdicts (they are the person's), but
    its automatic calls go."""
    off = [k.id for k in catalog.KINDS if not is_enabled(k.id, s)]
    if not off:
        return
    session.execute(sa.delete(ItemKind).where(ItemKind.kind.in_(off),
                                              ItemKind.verdict.is_(None)))
    session.execute(sa.update(ItemKind).where(ItemKind.kind.in_(off))
                    .values(auto=False, score=None, why=None))
    session.commit()


# ---------- text vectors for the prompts ----------

_prompt_cache: dict[tuple[str, str], dict[str, Any]] = {}


def all_prompts(kinds: Iterable[catalog.KindDef]) -> list[str]:
    out: list[str] = []
    for k in kinds:
        out += list(k.prompts) + list(k.negatives)
        if k.prompts and not k.parent:
            out += list(catalog.GENERIC_NEGATIVES)
        if k.parent:  # a child is scored against its siblings, on or off
            for sib in catalog.children(k.parent):
                out += list(catalog.BY_ID[sib].prompts)
    return sorted(set(out))


def prompt_vectors(model: str, prompts: list[str], *, fetch: bool = True):
    """{prompt: unit vector} for `model`, from .kist/kinds-prompts.json or
    the ml service (then saved). None when some are missing and the ml
    service can't embed them now."""
    import numpy as np

    from . import ai

    path = _state_path(PROMPTS_FILE)
    key = (str(path), model)
    have: dict[str, Any] = dict(_prompt_cache.get(key) or {})
    if not all(p in have for p in prompts):
        data = _read_json_file(PROMPTS_FILE) or {}
        if data.get("model") == model:
            for p, v in (data.get("vectors") or {}).items():
                if p not in have:
                    arr = np.asarray(v, dtype=np.float32)
                    have[p] = arr / (np.linalg.norm(arr) + 1e-12)
    missing = [p for p in prompts if p not in have]
    if missing:
        if not fetch or not get_settings().ml_url:
            return None
        new: dict[str, list[float]] = {}
        try:
            for p in missing:
                new[p] = ai.ml_embed_text(p)
        except ai.AiUnavailable as exc:
            log.info("kinds: prompt vectors not embedded yet: %s", exc)
            return None
        for p, v in new.items():
            arr = np.asarray(v, dtype=np.float32)
            have[p] = arr / (np.linalg.norm(arr) + 1e-12)
        try:
            data = _read_json_file(PROMPTS_FILE) or {}
            vectors = dict(data.get("vectors") or {}) if data.get("model") == model else {}
            vectors.update(new)
            _write_json_file(None, PROMPTS_FILE, {"model": model, "vectors": vectors})
        except OSError:
            log.exception("kinds: prompt vectors not written to disk")
    _prompt_cache[key] = have
    return {p: have[p] for p in prompts}


class Scorer:
    """CLIP scoring for a set of kinds over a matrix of unit image vectors."""

    def __init__(self, kinds: list[catalog.KindDef], vectors: dict, classifiers: dict):
        import numpy as np

        self.kinds = [k for k in kinds if k.prompts]
        self.np = np
        self.centroid: dict[str, Any] = {}
        for k in self.kinds:
            c = np.mean([vectors[p] for p in k.prompts], axis=0)
            self.centroid[k.id] = c / (np.linalg.norm(c) + 1e-12)
        self.negs: dict[str, Any] = {}
        for k in self.kinds:
            if k.parent:
                sibs = [s.id for s in catalog.KINDS if s.parent == k.parent and s.id != k.id
                        and s.prompts]
                rows = [self._centroid_of(s, vectors) for s in sibs]
            else:
                names = list(dict.fromkeys([*k.negatives, *catalog.GENERIC_NEGATIVES]))
                rows = [vectors[n] for n in names]
            self.negs[k.id] = np.stack(rows) if rows else np.zeros((0, len(self.centroid[k.id])))
        self.classifiers = classifiers

    def _centroid_of(self, kid, vectors):
        np = self.np
        k = catalog.BY_ID[kid]
        c = np.mean([vectors[p] for p in k.prompts], axis=0)
        return c / (np.linalg.norm(c) + 1e-12)

    def logits(self, kid: str, X):
        """Zero-shot log-odds of `kid` against its alternatives, per row."""
        np = self.np
        lc = (X @ self.centroid[kid]) * TEMPERATURE
        negs = self.negs[kid]
        if not len(negs):
            return lc * 0
        ln = (X @ negs.T) * TEMPERATURE
        m = np.maximum(lc, ln.max(axis=1))
        num = np.exp(lc - m)
        den = np.exp(ln - m[:, None]).sum(axis=1)
        p = np.clip(num / (num + den), 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p))

    def probs(self, kid: str, X):
        np = self.np
        z = self.logits(kid, X)
        clf = self.classifiers.get(kid)
        if clf is not None:
            w = np.asarray(clf["w"], dtype=np.float32)
            if w.shape[0] == X.shape[1]:
                z = float(clf.get("a", 1.0)) * np.clip(z, -OFFSET_CLIP, OFFSET_CLIP) \
                    + X @ w + float(clf["b"])
        return 1 / (1 + np.exp(-z))


# ---------- learned classifiers ----------


def train_logreg(X, y, offset, l2: float = L2, iters: int = 25, l2_scale: float = L2_SCALE):
    """Logistic regression on top of the zero-shot log-odds `offset`:
    logit = a * offset + w . x + b, Newton steps, L2 on w and on (a - 1),
    so with few corrections it stays close to the zero-shot call, and when
    the corrections keep disagreeing it can turn a sure zero-shot call
    down. Returns (w, b, a)."""
    import numpy as np

    n, d = X.shape
    off = np.clip(np.asarray(offset, dtype=np.float64), -OFFSET_CLIP, OFFSET_CLIP)
    A = np.hstack([X.astype(np.float64), off[:, None], np.ones((n, 1))])
    prior = np.zeros(d + 2)
    prior[d] = 1.0
    theta = prior.copy()
    reg = np.full(d + 2, l2)
    reg[d] = l2_scale
    reg[-1] = 1e-3
    y = np.asarray(y, dtype=np.float64)
    for _ in range(iters):
        z = np.clip(A @ theta, -30, 30)
        p = 1 / (1 + np.exp(-z))
        g = A.T @ (p - y) + reg * (theta - prior)
        W = np.maximum(p * (1 - p), 1e-6)
        H = (A * W[:, None]).T @ A + np.diag(reg)
        try:
            step = np.linalg.lstsq(H, g, rcond=None)[0]
        except np.linalg.LinAlgError:
            break
        if not np.all(np.isfinite(step)):
            break
        big = np.abs(step).max()
        if big > 5:  # damped: separable corrections would run off
            step *= 5 / big
        theta -= step
        if big < 1e-6:
            break
    return theta[:d].astype(np.float32), float(theta[-1]), float(theta[d])


def classifiers(model: str) -> dict:
    data = _read_json_file(CLASSIFIERS_FILE) or {}
    if data.get("model") != model:
        return {}
    return {k: v for k, v in (data.get("kinds") or {}).items() if isinstance(v, dict)}


def _verdict_stamp(session: Session, kid: str) -> tuple[int, str]:
    n, last = session.execute(
        sa.select(sa.func.count(), sa.func.max(ItemKind.verdict_at))
        .where(ItemKind.kind == kid, ItemKind.verdict.isnot(None))
    ).one()
    return int(n or 0), last.isoformat() if last else ""


def retrain(session: Session, kids: list[str] | None = None, *, fetch: bool = True) -> list[str]:
    """Retrain learnable kinds whose corrections changed since their last
    training (or `kids`). Returns the kinds retrained (or dropped)."""
    import numpy as np

    from . import ai

    model = ai.active_model()
    data = _read_json_file(CLASSIFIERS_FILE) or {}
    current = dict(data.get("kinds") or {}) if data.get("model") == model else {}
    changed: list[str] = []
    for k in catalog.KINDS:
        if not k.learnable or not k.prompts or (kids is not None and k.id not in kids):
            continue
        n, last = _verdict_stamp(session, k.id)
        old = current.get(k.id)
        if old and old.get("n") == n and old.get("last") == last:
            continue
        if n < LEARN_MIN:
            if k.id in current:
                current.pop(k.id)
                changed.append(k.id)
            continue
        rows = session.execute(
            sa.select(ItemKind.item_id, ItemKind.verdict, ItemEmbedding.vector)
            .join(ItemEmbedding, sa.and_(ItemEmbedding.item_id == ItemKind.item_id,
                                         ItemEmbedding.model == model))
            .where(ItemKind.kind == k.id, ItemKind.verdict.isnot(None))
        ).all()
        if len(rows) < LEARN_MIN:
            continue
        kinds = [k] + ([catalog.BY_ID[s] for s in catalog.children(k.parent)
                        if s != k.id] if k.parent else [])
        vectors = prompt_vectors(model, all_prompts(kinds), fetch=fetch)
        if vectors is None:
            continue
        X = np.asarray([r[2] for r in rows], dtype=np.float32)
        X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-12
        y = np.asarray([1.0 if r[1] else 0.0 for r in rows])
        scorer = Scorer(kinds, vectors, {})
        w, b, a = train_logreg(X, y, scorer.logits(k.id, X))
        current[k.id] = {"w": [round(float(x), 6) for x in w], "b": round(b, 6),
                         "a": round(a, 6), "n": n,
                         "pos": int(y.sum()), "neg": int(len(y) - y.sum()), "last": last,
                         "trained_at": datetime.now(UTC).isoformat()}
        changed.append(k.id)
    if changed:
        _write_json_file(session, CLASSIFIERS_FILE, {"model": model, "kinds": current})
        session.commit()
    return changed


# ---------- rules ----------


def _exif(item: Item) -> dict:
    return ((item.media or {}).get("exif") or {}) if isinstance(item.media, dict) else {}


def _has_camera(item: Item) -> str | None:
    ex = _exif(item)
    return item.camera or ex.get("camera") or ex.get("make") or ex.get("model") or None


def _is_image(item: Item) -> bool:
    return kind_for(item.mime or "", item.ext or "") == "image"


def _screen_size(item: Item) -> bool:
    return bool(item.width and item.height and (item.width, item.height) in SCREEN_SIZES)


def _rule(kind: catalog.KindDef, item: Item, words: int, text: str, clip: float | None,
          s: dict) -> tuple[float, str] | None:
    """(score, why) for a rule kind or a rule mixed with CLIP. None: no
    opinion (CLIP alone decides)."""
    r = kind.rule
    if r == "camera":
        cam = _has_camera(item)
        if _is_image(item) and cam and not SCREENSHOT_NAME.search(item.filename or ""):
            return 1.0, f"Camera: {cam}"
        return 0.0, ""
    if r == "low_res":
        if not _is_image(item) or item.mime == "image/svg+xml" or not item.width:
            return 0.0, ""
        limit = s["low_res_px"]
        size = _print_size(item) if _print_size else None
        if size:
            long_in = max(size)
            if long_in * 300 < limit:
                return 1.0, f"{long_in:.1f} in at 300 dpi (under {limit / 300:.1f} in)"
            return 0.0, ""
        long_px = max(item.width or 0, item.height or 0)
        if long_px < limit:
            return 1.0, f"{item.width} × {item.height} px: under {limit:,} px on the long side"
        return 0.0, ""
    if r == "ai_made":
        prov = item.provenance or {}
        if prov.get("ai_generated") is True:
            why = "C2PA says AI-made" if prov.get("c2pa") else "Provenance says AI-made"
            return 1.0, why
        dst = str(prov.get("digital_source_type") or "").lower()
        if any(t in dst for t in AI_SOURCE_TYPES):
            return 1.0, f"Digital source type: {dst.rsplit('/', 1)[-1]}"
        return 0.0, ""
    if r == "screenshot":
        if not _is_image(item):
            return 0.0, ""
        cam = _has_camera(item)
        if cam:
            return 0.0, ""  # a photo of a screen is a photo
        if SCREENSHOT_NAME.search(item.filename or ""):
            return 0.97, "File name says screenshot"
        png = item.mime == "image/png"
        if png and _screen_size(item):
            return 0.92, f"PNG, no camera, {item.width} × {item.height} = a screen's size"
        if _screen_size(item) and (clip or 0) >= 0.3:
            return 0.8, f"No camera, {item.width} × {item.height} = a screen's size"
        return None
    if r == "document":
        if (item.mime or "") == "application/pdf":
            return 0.95, "PDF"
        if words >= 60:
            return max(0.8, clip or 0), f"OCR found {words} words"
        if words >= 25 and (clip or 0) >= 0.25:
            return 0.85, f"OCR found {words} words"
        return None
    if r == "colour_card":
        if text and COLOUR_CODE.search(text):
            return max(0.85, clip or 0), "OCR found a colour code"
        return None
    if r == "film_still":
        if clip is None or not item.width or not item.height:
            return None
        a = max(item.width, item.height) / max(1, min(item.width, item.height))
        filmish = any(abs(a - f) / f < 0.02 for f in FILM_ASPECTS) and not _has_camera(item)
        if not filmish:
            return clip * 0.6, ""
        return None
    if r == "panorama":
        if (item.ext or "").lower() in ("hdr", "exr"):
            return 0.95, "HDR file"
        if item.width and item.height and abs(item.width / max(1, item.height) - 2) < 0.02 \
                and item.width >= 2000:
            return max(0.85, clip or 0), f"2:1 panorama, {item.width} × {item.height}"
        if clip is not None:
            return clip * 0.5, ""
        return None
    return None


# ---------- sorting ----------


def signature(s: dict, kinds: list[catalog.KindDef], model: str | None,
              clfs: dict) -> str:
    """What an item's calls depend on, besides the item itself."""
    blob = json.dumps({
        "v": RULES_VERSION,
        "kinds": [k.id for k in kinds],
        "strict": {k.id: s["kinds"][k.id]["strictness"] for k in kinds},
        "low_res": s["low_res_px"],
        "model": model,
        "prompts": hashlib.sha1("|".join(all_prompts(kinds)).encode()).hexdigest()[:10]
        if model else None,
        "clf": {k: v.get("trained_at") for k, v in sorted(clfs.items())
                if k in {x.id for x in kinds}} if model else {},
    }, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


class _Context:
    """Everything one sorting pass needs, loaded once."""

    def __init__(self, session: Session, *, fetch: bool = True):
        from . import ai

        self.s = settings()
        self.kinds = enabled_kinds(self.s)
        self.model = ai.active_model() if get_settings().ml_url or _has_any_embedding(session) \
            else None
        self.scorer: Scorer | None = None
        self.clfs: dict = {}
        clip_kinds = [k for k in self.kinds if k.prompts]
        if self.model and clip_kinds:
            vectors = prompt_vectors(self.model, all_prompts(clip_kinds), fetch=fetch)
            if vectors is not None:
                self.clfs = classifiers(self.model)
                self.scorer = Scorer(clip_kinds, vectors, self.clfs)
        self.sig_full = signature(self.s, self.kinds, self.model, self.clfs) \
            if self.scorer else None
        self.sig_rules = signature(self.s, self.kinds, None, {})

    def expected_sig(self, has_embedding: bool) -> str:
        return self.sig_full if (has_embedding and self.sig_full) else self.sig_rules


def _has_any_embedding(session: Session) -> bool:
    return session.execute(sa.select(sa.exists().where(ItemEmbedding.item_id.isnot(None)))
                           ).scalar_one()


def _in_parent(row: ItemKind | None, call: tuple[float, str] | None, t: float) -> bool:
    if row is not None and row.verdict is not None:
        return row.verdict
    if call is not None:
        return call[0] >= t
    return bool(row is not None and row.auto)


def assign(session: Session, item_ids: list[str], ctx: _Context | None = None) -> int:
    """Sort these items into the enabled kinds now. Returns how many items
    were sorted. Never touches modified_at. Without CLIP (no embedding, or
    the prompts can't be embedded now) only the rules run, and earlier CLIP
    calls are left as they were."""
    import numpy as np

    if not item_ids:
        return 0
    ctx = ctx or _Context(session)
    done = 0
    for start in range(0, len(item_ids), CHUNK):
        ids = item_ids[start:start + CHUNK]
        items = {i.id: i for i in session.execute(
            sa.select(Item).where(Item.id.in_(ids))).scalars()}
        if not items:
            continue
        vecs: dict[str, list[float]] = {}
        if ctx.scorer is not None:
            vecs = dict(session.execute(
                sa.select(ItemEmbedding.item_id, ItemEmbedding.vector)
                .where(ItemEmbedding.item_id.in_(list(items)),
                       ItemEmbedding.model == ctx.model)).all())
        texts: dict[str, str] = {}
        for iid, text in session.execute(
            sa.select(ItemText.item_id, ItemText.text)
            .where(ItemText.item_id.in_(list(items)), ItemText.source.in_(("ocr", "pdf")))
        ).all():
            texts[iid] = (texts.get(iid, "") + " " + (text or "")).strip()
        existing: dict[tuple[str, str], ItemKind] = {
            (r.item_id, r.kind): r for r in session.execute(
                sa.select(ItemKind).where(ItemKind.item_id.in_(list(items)))).scalars()}
        with_vec = [i for i in items if i in vecs]
        probs: dict[str, dict[str, float]] = {}
        if with_vec and ctx.scorer is not None:
            X = np.asarray([vecs[i] for i in with_vec], dtype=np.float32)
            X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-12
            for k in ctx.scorer.kinds:
                p = ctx.scorer.probs(k.id, X)
                probs[k.id] = {iid: float(p[n]) for n, iid in enumerate(with_vec)}
        for iid, item in items.items():
            text = texts.get(iid, "")
            words = len(re.findall(r"\w{2,}", text))
            has_clip = iid in vecs and ctx.scorer is not None
            # (score, why), or None: no call possible now, keep the last one
            calls: dict[str, tuple[float, str] | None] = {}
            # parents before children
            for k in sorted(ctx.kinds, key=lambda k: k.parent is not None):
                clip = probs.get(k.id, {}).get(iid) if has_clip else None
                if k.parent:
                    if clip is None:
                        calls[k.id] = None
                        continue
                    if not _in_parent(existing.get((iid, k.parent)), calls.get(k.parent),
                                      threshold(k.parent, ctx.s)):
                        calls[k.id] = (0.0, "")
                        continue
                rule = _rule(k, item, words, text, clip, ctx.s) if k.rule else None
                if rule is not None:
                    calls[k.id] = rule
                elif clip is not None:
                    why = (f"Learned from your corrections, {clip:.2f}" if k.id in ctx.clfs
                           else f"Looks like: {k.prompts[0]} {clip:.2f}")
                    calls[k.id] = (clip, why)
                elif k.prompts:
                    calls[k.id] = None
                else:
                    calls[k.id] = (0.0, "")
            for kid, call in calls.items():
                if call is None:
                    continue
                score, why = call
                row = existing.get((iid, kid))
                auto = score >= threshold(kid, ctx.s)
                if score < FLOOR and (row is None or row.verdict is None):
                    if row is not None:
                        session.delete(row)
                    continue
                if row is None:
                    session.add(ItemKind(item_id=iid, kind=kid, score=round(score, 4),
                                         auto=auto, why=why or None))
                else:
                    row.score, row.auto, row.why = round(score, 4), auto, why or None
            sig = ctx.expected_sig(iid in vecs)
            session.execute(
                pg_insert(ItemKindPass).values(item_id=iid, sig=sig, at=datetime.now(UTC))
                .on_conflict_do_update(index_elements=["item_id"],
                                       set_={"sig": sig, "at": datetime.now(UTC)}))
            done += 1
        session.commit()
    return done


def stale_ids(session: Session, ctx: _Context, limit: int | None = None,
              all_items: bool = False) -> list[str]:
    """Active items whose calls are missing or out of date."""
    has_emb = sa.exists().where(ItemEmbedding.item_id == Item.id,
                                ItemEmbedding.model == (ctx.model or ""))
    expected = sa.case((has_emb, ctx.sig_full or ctx.sig_rules), else_=ctx.sig_rules)
    stmt = (sa.select(Item.id).outerjoin(ItemKindPass, ItemKindPass.item_id == Item.id)
            .where(Item.deleted_at.is_(None)).order_by(Item.id))
    if not all_items:
        stmt = stmt.where(ItemKindPass.sig.is_distinct_from(expected))
    if limit:
        stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars())


def backfill(session: Session, *, limit: int | None = None, all_items: bool = False,
             fetch: bool = True) -> int:
    """Sort the items that need it (`kist kinds-backfill`)."""
    if not get_settings().kinds:
        return 0
    retrain(session, fetch=fetch)
    ctx = _Context(session, fetch=fetch)
    ids = stale_ids(session, ctx, limit, all_items)
    return assign(session, ids, ctx)


# ---------- jobs ----------


def _open_job(session: Session, *, all_items: bool) -> bool:
    q = sa.select(Job.id).where(Job.kind == JOB_KIND, Job.status == JobStatus.PENDING)
    if all_items:
        q = q.where(Job.payload["all"].astext == "true")
    return session.execute(q.limit(1)).first() is not None


def sort_now(session: Session, item_ids: list[str], *, fetch: bool = True) -> None:
    """After a new item, an embedding or OCR text: sort these items right
    away, in the job (or request) that produced the data. A few queries per
    item; never fails the caller (the worker's upkeep catches up)."""
    if not get_settings().kinds or not item_ids:
        return
    try:
        assign(session, [i for i in item_ids if i], _Context(session, fetch=fetch))
    except Exception:
        session.rollback()
        log.exception("kinds: sorting %s failed (the worker retries)", item_ids[:3])


def queue_all(session: Session) -> None:
    """After settings or learning changed: re-sort what is out of date."""
    if not get_settings().kinds or _open_job(session, all_items=True):
        return
    session.add(Job(kind=JOB_KIND, payload={"all": True}))
    session.commit()


def run_job(session: Session, job: Job) -> None:
    payload = job.payload or {}
    if payload.get("all"):
        retrain(session)
        ctx = _Context(session)
        ids = stale_ids(session, ctx, BATCH_PER_JOB + 1)
        more = len(ids) > BATCH_PER_JOB
        assign(session, ids[:BATCH_PER_JOB], ctx)
        if more:  # the next chunk as its own job, so imports go in between
            session.add(Job(kind=JOB_KIND, payload={"all": True}))
            session.commit()
        return
    ids = [i for i in (payload.get("item_ids") or []) if isinstance(i, str)]
    assign(session, ids)


def upkeep(session: Session) -> int:
    """Worker idle tick: a chunk of out-of-date items (after a rebuild, a
    model change, or the ml service coming back)."""
    if not get_settings().kinds:
        return 0
    ctx = _Context(session)
    ids = stale_ids(session, ctx, BATCH_PER_JOB)
    return assign(session, ids, ctx)


def on_new_item(session: Session, item: Item) -> None:
    """The rules can run as soon as an item exists (no AI needed)."""
    sort_now(session, [item.id], fetch=False)


# ---------- filters ----------

FILTER_RE = re.compile(r"^(-)?([a-z0-9-]{1,40})(?::(auto|confirmed|unsure|rejected))?$")


def member_condition(kid: str):
    """SQL: the item is in kind `kid` (verdict wins; a parent includes items
    confirmed into a child; a parent's \"not this\" removes its children)."""
    k = catalog.BY_ID[kid]
    own = sa.exists().where(ItemKind.item_id == Item.id, ItemKind.kind == kid, ItemKind.member)
    kids = catalog.children(kid)
    if kids:
        own = sa.or_(own, sa.exists().where(ItemKind.item_id == Item.id,
                                            ItemKind.kind.in_(kids),
                                            ItemKind.verdict.is_(True)))
        return sa.and_(own, ~_rejected(kid))
    if k.parent:
        return sa.and_(own, ~_rejected(k.parent))
    return own


def _rejected(kid: str):
    return sa.exists().where(ItemKind.item_id == Item.id, ItemKind.kind == kid,
                             ItemKind.verdict.is_(False))


def filters(auto_kind: str | None) -> list:
    """search.build_filters' auto_kind: `drawings` (in it), `-screenshots`
    (not in it), `drawings:auto` (in it automatically, not yet confirmed),
    `drawings:confirmed`, `drawings:rejected` (said \"not this\") and
    `drawings:unsure` (no verdict, score near the line: the review queue)."""
    if auto_kind is None or not str(auto_kind).strip():
        return []
    out = []
    for part in str(auto_kind).split(","):
        part = part.strip().lower()
        if not part:
            continue
        m = FILTER_RE.match(part)
        if not m or m.group(2) not in catalog.BY_ID:
            raise validation.ValidationError(
                f"unknown kind {part!r}; expected one of {', '.join(catalog.BY_ID)} "
                "(optionally -kind, or kind:auto|confirmed|unsure|rejected)")
        neg, kid, mode = m.groups()
        if mode is None:
            cond = member_condition(kid)
        elif mode == "auto":
            cond = sa.and_(member_condition(kid), sa.exists().where(
                ItemKind.item_id == Item.id, ItemKind.kind == kid, ItemKind.verdict.is_(None),
                ItemKind.auto))
        elif mode == "confirmed":
            cond = sa.exists().where(ItemKind.item_id == Item.id, ItemKind.kind == kid,
                                     ItemKind.verdict.is_(True))
        elif mode == "rejected":
            cond = _rejected(kid)
        else:  # unsure
            t = threshold(kid)
            cond = sa.exists().where(
                ItemKind.item_id == Item.id, ItemKind.kind == kid, ItemKind.verdict.is_(None),
                ItemKind.score.between(t - UNSURE_BAND, t + UNSURE_BAND))
        out.append(~cond if neg else cond)
    return out


# ---------- listing ----------


def overview(session: Session) -> dict:
    """GET /api/kinds: every kind with its settings and how many items (the
    caller can see) are in it."""
    from . import ai, search

    s = settings()
    model = ai.active_model()
    clfs = classifiers(model)
    base = search.build_filters(session=session)
    on_ids = [k.id for k in catalog.KINDS if is_enabled(k.id, s)] if get_settings().kinds else []

    def grouped(cond) -> dict[str, int]:
        if not on_ids:
            return {}
        return dict(session.execute(
            sa.select(ItemKind.kind, sa.func.count()).join(Item, Item.id == ItemKind.item_id)
            .where(*base, ItemKind.kind.in_(on_ids), cond).group_by(ItemKind.kind)).all())

    # one grouped count each; kinds with a parent or children follow the
    # parent's verdicts (member_condition), so those are counted on their own
    counts = grouped(ItemKind.member)
    confirmed_n = grouped(ItemKind.verdict.is_(True))
    rejected_n = grouped(ItemKind.verdict.is_(False))
    for kid in on_ids:
        k = catalog.BY_ID[kid]
        if k.parent or catalog.children(kid):
            counts[kid] = session.execute(sa.select(sa.func.count()).select_from(Item)
                                          .where(*base, member_condition(kid))).scalar_one()
    kinds_out = []
    for k in catalog.KINDS:
        on = k.id in on_ids
        count, confirmed, rejected = (counts.get(k.id, 0), confirmed_n.get(k.id, 0),
                                      rejected_n.get(k.id, 0)) if on else (0, 0, 0)
        clf = clfs.get(k.id)
        kinds_out.append({
            "id": k.id, "name": k.name, "packs": list(k.packs), "parent": k.parent,
            "enabled": bool(on), "enabled_override": s["kinds"][k.id]["enabled"],
            "opt_in": k.opt_in, "rule": k.rule, "clip": bool(k.prompts),
            "learnable": k.learnable and bool(k.prompts),
            "strictness": s["kinds"][k.id]["strictness"],
            "threshold": round(threshold(k.id, s), 3),
            "count": count, "confirmed": confirmed, "rejected": rejected,
            "visible": bool(on) and count >= s["min_items"],
            "learned": ({"corrections": clf.get("n"), "pos": clf.get("pos"),
                         "neg": clf.get("neg"), "trained_at": clf.get("trained_at")}
                        if clf else None),
            "learn_min": LEARN_MIN,
            "how": k.how,
        })
    pending = 0
    if get_settings().kinds:
        try:
            pending = session.execute(sa.select(sa.func.count()).select_from(Job).where(
                Job.kind == JOB_KIND, Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)))
            ).scalar_one()
        except Exception:
            session.rollback()
    return {
        "enabled": bool(get_settings().kinds),
        "kinds": kinds_out,
        "packs": [{"id": p, "name": n, "enabled": bool(s["packs"].get(p))}
                  for p, n in catalog.PACKS.items()],
        "min_items": s["min_items"],
        "low_res_px": s["low_res_px"],
        "print_size_source": "print check" if _print_size else "pixels",
        "pending_jobs": pending,
    }


def item_kinds(session: Session, item_id: str) -> list[dict]:
    """An item's kinds for the info panel: members (auto or confirmed) and
    the ones the person ruled out, with the score and why."""
    from . import items as items_svc

    item = items_svc.get_item(session, item_id)  # 404 and reader scope
    s = settings()
    rows = {r.kind: r for r in session.execute(
        sa.select(ItemKind).where(ItemKind.item_id == item.id)).scalars()}
    out = []
    for k in catalog.KINDS:
        r = rows.get(k.id)
        if r is None:
            continue
        member = session.execute(sa.select(sa.exists().where(
            Item.id == item.id, member_condition(k.id)))).scalar_one()
        if not member and r.verdict is not False:
            continue
        if not is_enabled(k.id, s) and r.verdict is None:
            continue
        out.append({
            "kind": k.id, "name": k.name, "parent": k.parent,
            "source": "human" if r.verdict is not None else "auto",
            "member": bool(member), "verdict": r.verdict,
            "score": r.score, "why": r.why,
            "enabled": is_enabled(k.id, s),
        })
    return out


# ---------- corrections ----------


def correct(session: Session, item_ids: list[str], kind_id: str,
            verdict: bool | None) -> dict:
    """\"Belongs here\" (True), \"Not this\" (False) or back to automatic
    (None), on one item or a selection: one event per changed item in the
    current batch (undo puts the old verdicts back)."""
    access.require(access.Action.EDIT)
    from . import items as items_svc

    if kind_id not in catalog.BY_ID:
        raise NotFound(f"unknown kind {kind_id!r}")
    ids = list(dict.fromkeys(i for i in item_ids if isinstance(i, str)))
    if not ids:
        raise validation.ValidationError("item_ids is empty")
    if len(ids) > MAX_CORRECT:
        raise validation.ValidationError(f"at most {MAX_CORRECT} items at once")
    for iid in ids:
        items_svc.get_item(session, iid)  # 404 (and reader scope)
    rows = {r.item_id: r for r in session.execute(
        sa.select(ItemKind).where(ItemKind.item_id.in_(ids), ItemKind.kind == kind_id)
        .with_for_update()).scalars()}
    now = datetime.now(UTC)
    changed: list[str] = []
    for iid in ids:
        r = rows.get(iid)
        before = r.verdict if r is not None else None
        if before == verdict:
            continue
        if r is None:
            r = ItemKind(item_id=iid, kind=kind_id, score=None, auto=False, why=None)
            session.add(r)
        r.verdict, r.verdict_at = verdict, (now if verdict is not None else None)
        events.record(session, iid, EVENT_KIND, {"kind": kind_id, "verdict": before},
                      {"kind": kind_id, "verdict": verdict})
        changed.append(iid)
    ctx = events.context()
    session.commit()
    for iid in changed:
        write_verdict_sidecar(session, iid)
    if changed:
        k = catalog.BY_ID[kind_id]
        kids = catalog.children(kind_id)
        if kids or k.parent:  # children follow the parent's call: re-sort now
            try:
                assign(session, changed, _Context(session, fetch=False))
            except Exception:
                session.rollback()
                log.exception("kinds: re-sort after a correction failed")
        if k.learnable and k.prompts:
            n, _ = _verdict_stamp(session, kind_id)
            if n >= LEARN_MIN or kind_id in classifiers(_model()):
                queue_all(session)  # retrains, then re-sorts what changed
    return {"kind": kind_id, "verdict": verdict, "changed": len(changed),
            "item_ids": changed, "batch_id": ctx.batch_id}


def _model() -> str:
    from . import ai

    return ai.active_model()


class _KindsEvent(events.ExtraKind):
    """Undo for kinds verdicts (registered in events.EXTRA_KINDS)."""

    def keys(self, before, after) -> set[str]:
        return {f"kinds:{(after or before or {}).get('kind')}"}

    def inverse(self, session: Session, ev: ItemEvent) -> str | None:
        kid = (ev.before or {}).get("kind") or (ev.after or {}).get("kind")
        if kid not in catalog.BY_ID:
            return f"unknown kind {kid!r}"
        row = session.get(ItemKind, (ev.item_id, kid))
        now = row.verdict if row is not None else None
        if now != (ev.after or {}).get("verdict"):
            return "the kind was corrected again later"
        correct(session, [ev.item_id], kid, (ev.before or {}).get("verdict"))
        return None


def _register_event_kind() -> None:
    events.EXTRA_KINDS[EVENT_KIND] = _KindsEvent()


_register_event_kind()


# ---------- durable copy of the verdicts ----------


def _verdict_path(item_id: str):
    from .ai import ai_dir

    return ai_dir(item_id) / VERDICT_FILE


def write_verdict_sidecar(session: Session, item_id: str) -> bool:
    from . import meta as meta_svc

    rows = session.execute(
        sa.select(ItemKind).where(ItemKind.item_id == item_id, ItemKind.verdict.isnot(None))
        .order_by(ItemKind.kind)).scalars().all()
    path = _verdict_path(item_id)
    try:
        if not rows:
            path.unlink(missing_ok=True)
            return True
        if not path.parent.parent.is_dir():
            return False  # purged meanwhile
        meta_svc.atomic_write_text(path, json.dumps({
            "verdicts": [{"kind": r.kind, "verdict": r.verdict,
                          "at": r.verdict_at.isoformat() if r.verdict_at else None}
                         for r in rows],
            "kist_version": __version__,
        }, indent=2, ensure_ascii=False))
        return True
    except OSError:
        log.exception("kinds verdicts for %s not written to disk", item_id)
        meta_svc.note_pending(f"kinds:{item_id}")
        return False


def load_verdict_sidecar(session: Session, item_id: str) -> int:
    """rebuild-index: verdict rows from items/<id>/ai/kinds.json."""
    from . import meta as meta_svc

    path = _verdict_path(item_id)
    if not path.is_file():
        return 0
    n = 0
    for row in (meta_svc.read_json(path).get("verdicts") or []):
        try:
            kid = str(row["kind"])
            verdict = row["verdict"]
            if kid not in catalog.BY_ID or not isinstance(verdict, bool):
                continue
            at = datetime.fromisoformat(row["at"]) if row.get("at") else datetime.now(UTC)
            session.execute(
                pg_insert(ItemKind).values(item_id=item_id, kind=kid, verdict=verdict,
                                           verdict_at=at, auto=False)
                .on_conflict_do_update(index_elements=["item_id", "kind"],
                                       set_={"verdict": verdict, "verdict_at": at}))
            n += 1
        except (KeyError, TypeError, ValueError):
            continue
    session.commit()
    return n


def rewrite_sidecars(session: Session) -> int:
    """rewrite-meta: every item with verdicts gets its file. Returns failures."""
    failed = 0
    for iid in session.execute(sa.select(ItemKind.item_id).where(ItemKind.verdict.isnot(None))
                               .distinct().order_by(ItemKind.item_id)).scalars().all():
        if not write_verdict_sidecar(session, iid):
            failed += 1
    return failed


def check(session: Session) -> list[str]:
    """verify: the verdict files match the rows, and kinds.json is readable."""
    from . import meta as meta_svc

    problems = []
    counts = dict(session.execute(
        sa.select(ItemKind.item_id, sa.func.count()).where(ItemKind.verdict.isnot(None))
        .group_by(ItemKind.item_id)).all())
    for item_id, n in sorted(counts.items()):
        try:
            have = len(meta_svc.read_json(_verdict_path(item_id)).get("verdicts") or [])
        except (OSError, ValueError, AttributeError):
            have = -1
        if have != n:
            problems.append(f"{item_id}: ai/{VERDICT_FILE} doesn't match its {n} kind verdicts")
    path = _state_path(SETTINGS_FILE)
    if path.is_file():
        try:
            if not isinstance(json.loads(path.read_text(encoding="utf-8")), dict):
                raise ValueError("not an object")
        except (OSError, ValueError) as exc:
            problems.append(f".kist/{SETTINGS_FILE}: unreadable ({exc})")
    return problems


def restore(session: Session) -> tuple[int, int]:
    """rebuild-index: verdicts from their files, then the automatic calls
    (from cached prompt vectors when the ml service is down; whatever can't
    be done now is left to the worker). Returns (verdicts, items sorted)."""
    verdicts = 0
    for iid in session.execute(sa.select(Item.id).order_by(Item.id)).scalars().all():
        verdicts += load_verdict_sidecar(session, iid)
    sorted_n = 0
    if get_settings().kinds:
        session.execute(sa.delete(ItemKindPass))
        session.commit()
        sorted_n = backfill(session, fetch=False)
    return verdicts, sorted_n
