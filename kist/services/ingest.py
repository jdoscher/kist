"""Ingest pipeline: hash → dedupe → sniff → store → thumbs → meta.json.

Used synchronously by the API (uploads, URL adds return the finished item)
and by the worker (inbox files, queued/retried jobs).
"""

import hashlib
import logging
import re
import shutil
import time
from datetime import datetime
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..db import SessionLocal
from ..filetypes import MIME_TO_EXT
from ..ids import new_ulid
from ..models import Item, Job
from . import meta as meta_svc
from . import tags as tags_svc
from . import thumbs


def items_folders_resolve(session: Session, folder_ids: list[str]):
    from . import items as items_svc

    return items_svc.resolve_folders(session, folder_ids)

log = logging.getLogger("kist.ingest")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sniff_mime(path: Path) -> str:
    import magic

    try:
        return magic.from_file(str(path), mime=True) or "application/octet-stream"
    except Exception:
        return "application/octet-stream"


def pick_ext(filename: str, mime: str) -> str:
    suffix = Path(filename).suffix.lstrip(".").lower()
    if suffix:
        return suffix
    return MIME_TO_EXT.get(mime, "bin")


class IngestResult:
    def __init__(self, item: Item, created: bool):
        self.item = item
        self.created = created


def ingest_file(
    session: Session,
    path: Path,
    filename: str,
    *,
    folder_id: str | None = None,
    folder_ids: list[str] | None = None,
    tag_names: list[str] | None = None,
    notes: str = "",
    source_url: str | None = None,
    rating: int = 0,
    created_at: datetime | None = None,
    modified_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> IngestResult:
    """Ingest the file at `path`, consuming it (moved into the item dir).

    Exact duplicates (sha256) are rejected: the existing item is returned
    and the source file is removed.
    """
    settings = get_settings()
    digest = sha256_file(path)

    existing = session.execute(
        sa.select(Item).where(Item.sha256 == digest)
    ).scalar_one_or_none()
    if existing is not None:
        path.unlink(missing_ok=True)
        return IngestResult(existing, created=False)

    mime = sniff_mime(path)
    ext = pick_ext(filename, mime)
    item_id = new_ulid()
    item_dir = settings.items_dir / item_id
    item_dir.mkdir(parents=True, exist_ok=True)
    original = item_dir / f"original.{ext}"

    try:
        try:
            path.replace(original)  # same filesystem: atomic
        except OSError:
            shutil.move(str(path), original)

        probe = thumbs.Probe()
        try:
            probe = thumbs.generate_previews(original, item_dir, mime, ext)
        except Exception:
            log.exception("preview generation failed for %s (%s)", filename, mime)
        phash, colors = thumbs.enrich(item_dir)

        item = Item(
            id=item_id,
            filename=filename or original.name,
            ext=ext,
            mime=mime,
            size_bytes=original.stat().st_size,
            width=probe.width,
            height=probe.height,
            duration_s=probe.duration_s,
            sha256=digest,
            phash=phash,
            dominant_colors=colors,
            rating=max(0, min(5, int(rating or 0))),
            notes=notes or "",
            source_url=source_url,
            deleted_at=deleted_at,
        )
        # imports preserve original timestamps; otherwise the DB defaults apply
        if created_at is not None:
            item.created_at = created_at
        if modified_at is not None:
            item.modified_at = modified_at
        session.add(item)
        all_folders = list(folder_ids or [])
        if folder_id:
            all_folders.append(folder_id)
        if all_folders:
            item.folders = items_folders_resolve(session, all_folders)
        if tag_names:
            item.tags = tags_svc.get_or_create(session, tag_names)
        try:
            session.commit()
        except IntegrityError:
            # lost a dedupe race — another process ingested the same bytes
            session.rollback()
            shutil.rmtree(item_dir, ignore_errors=True)
            existing = session.execute(
                sa.select(Item).where(Item.sha256 == digest)
            ).scalar_one()
            return IngestResult(existing, created=False)

        session.refresh(item)
        meta_svc.write_meta(item)
        return IngestResult(item, created=True)
    except Exception:
        shutil.rmtree(item_dir, ignore_errors=True)
        raise


SAFE_NAME = re.compile(r"[^\w.\- ()\[\]]+")


def sanitize_filename(name: str) -> str:
    name = SAFE_NAME.sub("_", name).strip() or "file"
    return name[:255]


def filename_from_url(url: str, response: httpx.Response) -> str:
    cd = response.headers.get("content-disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd, re.IGNORECASE)
    if m:
        return sanitize_filename(unquote(m.group(1)))
    path_name = Path(unquote(urlparse(url).path)).name
    if path_name:
        return sanitize_filename(path_name)
    return "download"


def ingest_url(
    session: Session,
    url: str,
    *,
    folder_id: str | None = None,
    folder_ids: list[str] | None = None,
    tag_names: list[str] | None = None,
    notes: str = "",
    source_url: str | None = None,
    rating: int = 0,
    created_at: datetime | None = None,
    modified_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> IngestResult:
    staging, filename, content_type = _download(url)
    if content_type in ("text/html", "application/xhtml+xml"):
        # a page link (Pinterest, Unsplash, a blog post…): take its preview image
        try:
            image_url = page_image_url(staging.read_text(errors="replace"), url)
        finally:
            staging.unlink(missing_ok=True)
        if not image_url:
            raise NotMediaError("that URL is a web page with no image to download")
        staging, filename, content_type = _download(image_url)
        if content_type.startswith("text/"):
            staging.unlink(missing_ok=True)
            raise NotMediaError("the page's preview image could not be downloaded")

    return ingest_file(
        session, staging, filename,
        folder_id=folder_id, folder_ids=folder_ids, tag_names=tag_names,
        notes=notes, source_url=source_url or url, rating=rating,
        created_at=created_at, modified_at=modified_at, deleted_at=deleted_at,
    )


class NotMediaError(ValueError):
    pass


DEFAULT_USER_AGENT = f"kist/{__version__} (self-hosted asset manager)"


def _download(url: str) -> tuple[Path, str, str]:
    """Stream url into staging. Returns (path, filename, bare content type)."""
    settings = get_settings()
    settings.staging_dir.mkdir(parents=True, exist_ok=True)
    staging = settings.staging_dir / f"dl-{new_ulid()}"
    max_bytes = settings.max_download_mb * 1024 * 1024

    with httpx.stream(
        "GET", url, follow_redirects=True, timeout=60,
        headers={
            "User-Agent": settings.user_agent or DEFAULT_USER_AGENT,
            "Accept": "image/avif,image/webp,image/*,video/*,*/*;q=0.8",
        },
    ) as resp:
        resp.raise_for_status()
        filename = filename_from_url(str(resp.url), resp)
        content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        received = 0
        with staging.open("wb") as f:
            for chunk in resp.iter_bytes(1024 * 256):
                received += len(chunk)
                if received > max_bytes:
                    f.close()
                    staging.unlink(missing_ok=True)
                    raise ValueError(
                        f"download exceeds max size ({settings.max_download_mb} MB)"
                    )
                f.write(chunk)
    return staging, filename, content_type


_META_TAG = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_IMAGE_PROPS = ("og:image:secure_url", "og:image", "og:image:url", "twitter:image",
                "twitter:image:src")


def page_image_url(html: str, page_url: str) -> str | None:
    """The og:image / twitter:image of an HTML page, resolved to absolute."""
    found: dict[str, str] = {}
    for tag in _META_TAG.findall(html[:500_000]):
        attrs = {k.lower(): (v1 or v2) for k, v1, v2 in _ATTR.findall(tag)}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key in _IMAGE_PROPS and attrs.get("content") and key not in found:
            found[key] = attrs["content"]
    for key in _IMAGE_PROPS:
        if key in found:
            return urljoin(page_url, unescape(found[key].strip()))
    return None


def run_ingest_job(session: Session, payload: dict) -> None:
    meta_kwargs = dict(
        folder_id=payload.get("folder_id"),
        folder_ids=payload.get("folder_ids"),
        tag_names=payload.get("tags"),
        notes=payload.get("notes", ""),
    )
    if payload.get("url"):
        ingest_url(session, payload["url"], **meta_kwargs)
        return
    path = Path(payload["path"])
    if not path.exists():
        log.warning("ingest job path vanished: %s", path)
        return
    ingest_file(
        session, path, payload.get("filename") or path.name,
        source_url=payload.get("source_url"), **meta_kwargs,
    )


def scan_inbox() -> None:
    """Move stable files out of library/inbox into staging and enqueue them."""
    settings = get_settings()
    inbox = settings.inbox_dir
    if not inbox.is_dir():
        return
    now = time.time()
    for entry in sorted(inbox.rglob("*")):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        try:
            stat = entry.stat()
        except FileNotFoundError:
            continue
        if now - stat.st_mtime < 3:  # still being written
            continue
        settings.staging_dir.mkdir(parents=True, exist_ok=True)
        staged = settings.staging_dir / f"inbox-{new_ulid()}-{entry.name}"
        try:
            entry.replace(staged)
        except OSError:
            log.exception("failed to stage inbox file %s", entry)
            continue
        with SessionLocal() as session:
            session.add(
                Job(kind="ingest", payload={"path": str(staged), "filename": entry.name})
            )
            session.commit()
        log.info("queued inbox file %s", entry.name)
