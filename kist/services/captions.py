"""Captions: a sentence or two describing an image, written by a vision model.

Off by default. KIST_CAPTION_PROVIDER picks who writes them:

    ollama     a local Ollama (KIST_CAPTION_URL, e.g. http://ollama:11434, and
               KIST_CAPTION_MODEL, e.g. qwen2.5vl:3b). Nothing leaves the house.
    openai     OpenAI's chat completions API (or any server that speaks it,
               via KIST_CAPTION_URL), KIST_CAPTION_API_KEY + KIST_CAPTION_MODEL
    anthropic  Anthropic's messages API, KIST_CAPTION_API_KEY + _MODEL
    gemini     Google's Gemini API, KIST_CAPTION_API_KEY + _MODEL

The three cloud providers are bring-your-own-key: each image is sent to that
company to be described. Status and docs say so plainly.

Who runs the jobs
-----------------
`caption` jobs are queued on demand (POST /api/items/{id}/caption) or by
`kist ai-backfill --kind caption`, and for new items only with
KIST_CAPTION_AUTO=true. The worker runs them when the provider is usable
from the server (a cloud key, or an Ollama URL). With provider ollama and
no URL, they wait for a `kist helper --kinds caption` on a Mac that runs
Ollama itself.

Files are the truth
-------------------
    items/<id>/ai/caption.json   {text, model, provider, created_at,
                                  edited, edited_at?, kist_version}

The caption is also an item_text row (source "caption"), so keyword search
finds it, and ItemOut.caption. Writing a caption (generated or edited) is an
item event of kind "caption", so it shows in the history and can be undone.
"""

import base64
import io
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx
import sqlalchemy as sa
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..lifecycle import JobKind, JobStatus, PreviewStatus
from ..models import Item, ItemText, Job
from . import events
from . import meta as meta_svc
from .validation import ValidationError

log = logging.getLogger("kist.captions")

CAPTION_FILE = "caption.json"
SOURCE = "caption"
PROVIDERS = ("ollama", "openai", "anthropic", "gemini")
CLOUD = ("openai", "anthropic", "gemini")
CAPTION_MAX = 2000
IMAGE_SIDE = 1024
DEFAULT_PROMPT = (
    "Describe this image in one or two plain sentences, for alt text and search: "
    "the main subject, the setting, notable colours and materials, and any clearly "
    "legible words. No preamble, no speculation."
)
LEAVES_HOUSE = (
    "Captions use {provider}: every image captioned is sent to {company} to be "
    "described. Use ollama to keep images at home."
)
COMPANIES = {"openai": "OpenAI", "anthropic": "Anthropic", "gemini": "Google"}


class CaptionError(RuntimeError):
    """The provider refused or failed (the job is retried, then fails)."""


class CaptionsOff(ValidationError):
    """No caption provider is configured (HTTP 422)."""


# ---------- providers ----------


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str
    url: str = ""
    api_key: str = ""
    prompt: str = DEFAULT_PROMPT
    timeout: float = 180.0
    max_tokens: int = 200


class Provider(Protocol):
    name: str

    def caption(self, image: bytes, mime: str) -> str: ...


_transport: httpx.BaseTransport | None = None  # tests may put a fake here


def set_transport(transport: httpx.BaseTransport | None) -> None:
    global _transport
    _transport = transport


class _HttpProvider:
    name = ""
    default_url = ""

    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg
        self.base = (cfg.url or self.default_url).rstrip("/")
        if not self.base:
            raise CaptionsOff(f"KIST_CAPTION_URL is needed for {self.name}")
        if not cfg.model:
            raise CaptionsOff("KIST_CAPTION_MODEL is needed (e.g. qwen2.5vl:3b)")

    def _post(self, path: str, body: dict, headers: dict | None = None) -> dict:
        try:
            with httpx.Client(base_url=self.base, timeout=self.cfg.timeout,
                              transport=_transport) as c:
                r = c.post(path, json=body, headers=headers or {})
        except httpx.HTTPError as exc:
            raise CaptionError(
                f"{self.name} can't be reached at {self.base}: {type(exc).__name__}"
            ) from exc
        if r.status_code >= 400:
            raise CaptionError(f"{self.name} answered {r.status_code}: {_error_text(r)}")
        try:
            return r.json()
        except ValueError:
            raise CaptionError(f"{self.name} sent something that isn't JSON") from None


def _error_text(r: httpx.Response) -> str:
    try:
        data = r.json()
    except ValueError:
        return r.text[:300]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)[:300]
    return str(err or data)[:300]


class OllamaProvider(_HttpProvider):
    name = "ollama"

    def caption(self, image: bytes, mime: str) -> str:
        out = self._post("/api/chat", {
            "model": self.cfg.model,
            "stream": False,
            "messages": [{"role": "user", "content": self.cfg.prompt,
                          "images": [base64.b64encode(image).decode()]}],
            "options": {"num_predict": self.cfg.max_tokens},
        })
        return _text((out.get("message") or {}).get("content"), self.name)


class OpenAIProvider(_HttpProvider):
    name = "openai"
    default_url = "https://api.openai.com/v1"

    def caption(self, image: bytes, mime: str) -> str:
        data_url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
        out = self._post("/chat/completions", {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": self.cfg.prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]}],
        }, {"Authorization": f"Bearer {self.cfg.api_key}"})
        choices = out.get("choices") or [{}]
        return _text((choices[0].get("message") or {}).get("content"), self.name)


class AnthropicProvider(_HttpProvider):
    name = "anthropic"
    default_url = "https://api.anthropic.com/v1"

    def caption(self, image: bytes, mime: str) -> str:
        out = self._post("/messages", {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime,
                                             "data": base64.b64encode(image).decode()}},
                {"type": "text", "text": self.cfg.prompt},
            ]}],
        }, {"x-api-key": self.cfg.api_key, "anthropic-version": "2023-06-01"})
        parts = [p.get("text", "") for p in out.get("content") or [] if p.get("type") == "text"]
        return _text(" ".join(parts), self.name)


class GeminiProvider(_HttpProvider):
    name = "gemini"
    default_url = "https://generativelanguage.googleapis.com/v1beta"

    def caption(self, image: bytes, mime: str) -> str:
        out = self._post(f"/models/{self.cfg.model}:generateContent", {
            "contents": [{"parts": [
                {"inline_data": {"mime_type": mime, "data": base64.b64encode(image).decode()}},
                {"text": self.cfg.prompt},
            ]}],
            "generationConfig": {"maxOutputTokens": self.cfg.max_tokens},
        }, {"x-goog-api-key": self.cfg.api_key})
        candidates = out.get("candidates") or [{}]
        parts = (candidates[0].get("content") or {}).get("parts") or []
        return _text(" ".join(p.get("text", "") for p in parts), self.name)


PROVIDER_CLASSES: dict[str, type[_HttpProvider]] = {
    "ollama": OllamaProvider, "openai": OpenAIProvider,
    "anthropic": AnthropicProvider, "gemini": GeminiProvider,
}


def _text(value, provider: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise CaptionError(f"{provider} returned no caption")
    return text[:CAPTION_MAX]


def config_from_settings(**overrides) -> ProviderConfig | None:
    s = get_settings()
    provider = (overrides.get("provider", s.caption_provider) or "").strip().lower()
    if not provider:
        return None
    if provider not in PROVIDERS:
        raise CaptionsOff(
            f"KIST_CAPTION_PROVIDER {provider!r} isn't one of {', '.join(PROVIDERS)}"
        )
    return ProviderConfig(
        provider=provider,
        model=overrides.get("model", s.caption_model),
        url=overrides.get("url", s.caption_url),
        api_key=overrides.get("api_key", s.caption_api_key),
        prompt=s.caption_prompt or DEFAULT_PROMPT,
        timeout=s.caption_timeout_seconds,
    )


def make_provider(cfg: ProviderConfig) -> Provider:
    if cfg.provider in CLOUD and not cfg.api_key:
        raise CaptionsOff(f"KIST_CAPTION_API_KEY is needed for {cfg.provider}")
    return PROVIDER_CLASSES[cfg.provider](cfg)


def enabled() -> bool:
    """Captions can be queued (a provider is chosen)."""
    try:
        return config_from_settings() is not None
    except CaptionsOff:
        return False


def worker_can_run() -> bool:
    """The server itself can reach the provider (else jobs wait for a helper)."""
    try:
        cfg = config_from_settings()
        if cfg is None:
            return False
        make_provider(cfg)
        return True
    except CaptionsOff:
        return False


def warning(provider: str | None = None) -> str | None:
    provider = provider if provider is not None else (get_settings().caption_provider or "")
    provider = provider.strip().lower()
    if provider in CLOUD:
        return LEAVES_HOUSE.format(provider=provider, company=COMPANIES[provider])
    return None


def status() -> dict:
    s = get_settings()
    provider = (s.caption_provider or "").strip().lower() or None
    return {
        "provider": provider,
        "model": s.caption_model or None,
        "auto": s.caption_auto,
        "runs_on": None if provider is None else ("server" if worker_can_run() else "helper"),
        "leaves_house": provider in CLOUD,
        "warning": warning(provider or ""),
    }


# ---------- the image sent ----------


def image_for(item: Item) -> bytes:
    """The item's 1024 px preview as JPEG (every provider reads JPEG)."""

    d = get_settings().items_dir / item.id
    for name in ("thumb_1024.webp", "thumb_512.webp", "thumb_256.webp"):
        if (d / name).is_file():
            return to_jpeg((d / name).read_bytes())
    raise RuntimeError("the item has no preview to caption")


def to_jpeg(data: bytes) -> bytes:
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((IMAGE_SIDE, IMAGE_SIDE))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return buf.getvalue()


def can_caption(item: Item) -> bool:
    return item.preview_status == PreviewStatus.READY and item.deleted_at is None


# ---------- sidecar + row + event ----------


def sidecar_path(item_id: str) -> Path:
    return get_settings().items_dir / item_id / "ai" / CAPTION_FILE


def read_sidecar(item_id: str) -> dict | None:
    path = sidecar_path(item_id)
    if not path.is_file():
        return None
    data = meta_svc.read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        raise ValueError(f"{path.name} has no text")
    return data


def _write_sidecar(item_id: str, record: dict) -> None:
    meta_svc.atomic_write_text(sidecar_path(item_id),
                               json.dumps(record, indent=2, ensure_ascii=False))


def _clean(text: str | None) -> str:
    text = " ".join((text or "").replace("\x00", "").split())
    if len(text) > CAPTION_MAX:
        raise ValidationError(f"a caption is at most {CAPTION_MAX} characters")
    return text


def current(session: Session, item_id: str) -> str | None:
    text = session.execute(
        sa.select(ItemText.text).where(ItemText.item_id == item_id, ItemText.source == SOURCE)
    ).scalar_one_or_none()
    return text or None


def _put(session: Session, item_id: str, record: dict | None) -> None:
    """Write the sidecar (or remove it), then the row."""
    if record is None:
        sidecar_path(item_id).unlink(missing_ok=True)
        session.execute(
            sa.delete(ItemText).where(ItemText.item_id == item_id, ItemText.source == SOURCE)
            .execution_options(synchronize_session=False)
        )
        return
    _write_sidecar(item_id, record)
    _upsert_row(session, item_id, record)


def _upsert_row(session: Session, item_id: str, record: dict) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    values = {"item_id": item_id, "source": SOURCE, "text": record["text"][:CAPTION_MAX],
              "lang": None, "model": str(record.get("model") or "unknown")[:200],
              "created_at": _dt(record.get("created_at")) or datetime.now(UTC)}
    stmt = pg_insert(ItemText).values(**values)
    session.execute(stmt.on_conflict_do_update(
        index_elements=["item_id", "source"],
        set_={"text": stmt.excluded.text, "model": stmt.excluded.model,
              "created_at": stmt.excluded.created_at},
    ))


def store_generated(session: Session, item_id: str, text: str, *, model: str,
                    provider: str) -> bool:
    """A provider's caption: sidecar, row and a `caption` event. False when
    the item is gone."""
    item = session.get(Item, item_id)
    if item is None or not (get_settings().items_dir / item_id).is_dir():
        return False
    text = _clean(text)[:CAPTION_MAX]
    before = current(session, item_id)
    now = datetime.now(UTC)
    _put(session, item_id, {
        "text": text, "model": model, "provider": provider, "created_at": now.isoformat(),
        "edited": False, "kist_version": __version__,
    } if text else None)
    if before != (text or None):
        events.record(session, item_id, events.Kind.CAPTION, {"caption": before},
                      {"caption": text or None})
    session.commit()
    return True


def set_caption(session: Session, item_id: str, text: str | None) -> Item:
    """A person's (or agent's) edit: an empty caption removes it. Recorded
    as a `caption` event; the item's modified_at moves like any edit."""
    from . import items as items_svc

    return items_svc.update_item(session, item_id, fields={"caption": text or ""})


def apply_caption(session: Session, item: Item, text: str | None) -> bool:
    """items.update_item's part of a caption edit (no commit). Returns
    whether it changed."""
    item_id = item.id
    text = _clean(text)
    before = current(session, item_id)
    if (text or None) == before:
        return False
    old = None
    try:
        old = read_sidecar(item_id)
    except (OSError, ValueError):
        old = None
    now = datetime.now(UTC)
    record = None
    if text:
        record = {
            "text": text,
            "model": (old or {}).get("model") or "edited",
            "provider": (old or {}).get("provider") or "edited",
            "created_at": (old or {}).get("created_at") or now.isoformat(),
            "edited": True, "edited_at": now.isoformat(), "kist_version": __version__,
        }
    _put(session, item_id, record)
    events.record(session, item_id, events.Kind.CAPTION, {"caption": before},
                  {"caption": text or None})
    # the item's caption_row was loaded before: show the new one
    session.flush()
    session.expire(item, ["caption_row"])
    return True


def load_sidecar(session: Session, item_id: str) -> int:
    """rebuild-index: the caption row from caption.json when it's missing."""
    if current(session, item_id) is not None:
        return 0
    rec = read_sidecar(item_id)
    if rec is None or not rec["text"].strip():
        return 0
    _upsert_row(session, item_id, rec)
    session.commit()
    return 1


def rewrite_missing_sidecars(session: Session) -> int:
    n = 0
    for t in session.execute(sa.select(ItemText).where(ItemText.source == SOURCE)).scalars():
        if sidecar_path(t.item_id).is_file() or not sidecar_path(t.item_id).parent.parent.is_dir():
            continue
        _write_sidecar(t.item_id, {"text": t.text, "model": t.model, "provider": "unknown",
                                   "created_at": t.created_at.isoformat(), "edited": False,
                                   "kist_version": __version__})
        n += 1
    return n


def _dt(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


# ---------- jobs ----------


def _open_job(session: Session, item_id: str) -> Job | None:
    return session.execute(
        sa.select(Job).where(
            Job.kind == JobKind.CAPTION,
            Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
            Job.payload["item_id"].astext == item_id,
        ).limit(1)
    ).scalar_one_or_none()


def request(session: Session, item_id: str) -> Job:
    """POST /api/items/{id}/caption: queue a caption now (not a backfill,
    so it doesn't wait for KIST_AI_WINDOW). An open job is reused."""
    from . import items as items_svc

    if not enabled():
        config_from_settings()  # raises the specific reason
        raise CaptionsOff("captions are off: set KIST_CAPTION_PROVIDER (see the README)")
    item = items_svc.get_item(session, item_id, with_deleted=False)
    if not can_caption(item):
        raise ValidationError("this item has no preview to caption yet")
    job = _open_job(session, item_id)
    if job is None:
        job = Job(kind=JobKind.CAPTION,
                  payload={"item_id": item.id, "filename": item.filename, "backfill": False})
        session.add(job)
        session.commit()
    return job


def on_new_item(session: Session, item: Item) -> None:
    """KIST_CAPTION_AUTO: queue a caption for each new item with a preview."""
    if not get_settings().caption_auto or not enabled() or not can_caption(item):
        return
    if current(session, item.id) is None and _open_job(session, item.id) is None:
        session.add(Job(kind=JobKind.CAPTION,
                        payload={"item_id": item.id, "filename": item.filename,
                                 "backfill": False}))
        session.commit()


def backfill(session: Session, limit: int | None = None) -> int:
    """Queue captions for active items with a preview and no caption. They
    wait for KIST_AI_WINDOW."""
    if not enabled():
        config_from_settings()
        raise CaptionsOff("captions are off: set KIST_CAPTION_PROVIDER (see the README)")
    open_ids = set(session.execute(
        sa.select(Job.payload["item_id"].astext).where(
            Job.kind == JobKind.CAPTION, Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)))
    ).scalars())
    stmt = sa.select(Item.id, Item.filename).where(
        Item.deleted_at.is_(None),
        Item.preview_status == PreviewStatus.READY,
        ~sa.exists().where(ItemText.item_id == Item.id, ItemText.source == SOURCE),
    ).order_by(Item.id)
    n = 0
    for item_id, filename in session.execute(stmt).all():
        if item_id in open_ids:
            continue
        if limit is not None and n >= limit:
            break
        session.add(Job(kind=JobKind.CAPTION,
                        payload={"item_id": item_id, "filename": filename, "backfill": True}))
        n += 1
    session.commit()
    return n


def run_job(session: Session, item: Item) -> None:
    """The worker's side of a caption job."""
    cfg = config_from_settings()
    if cfg is None:
        raise CaptionError("captions are off (KIST_CAPTION_PROVIDER is empty)")
    provider = make_provider(cfg)
    text = provider.caption(image_for(item), "image/jpeg")
    with events.acting("worker", "worker"):
        store_generated(session, item.id, text, model=cfg.model, provider=cfg.provider)
