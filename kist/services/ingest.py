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
from ..lifecycle import JobKind, PreviewStatus
from ..models import Item, Job
from . import meta as meta_svc
from . import netguard, previews, thumbs, validation
from . import tags as tags_svc

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
    (it may be in the trash: check its state) and the source file is removed.
    An active duplicate still gets the folders and tags sent with it, and
    notes / source_url if it has none. If ingest fails, the source file is
    put back where it was, so a retry or the user still has it.

    Everything sent along is checked before any work is done: an unknown
    folder or a bad tag name raises validation.ValidationError and leaves
    the file where it was.
    """
    from . import items as items_svc

    settings = get_settings()
    all_folder_ids = list(folder_ids or []) + ([folder_id] if folder_id else [])
    items_svc.resolve_folders(session, all_folder_ids)  # unknown ids -> 422
    tag_names = [validation.tag_name(t) for t in tag_names or []]
    notes = validation.notes(notes or "")
    source_url = validation.source_url(source_url)
    rating = validation.rating(rating or 0)
    created_at = validation.timestamp(created_at, "created_at")
    modified_at = validation.timestamp(modified_at, "modified_at")
    digest = sha256_file(path)

    existing = session.execute(
        sa.select(Item).where(Item.sha256 == digest)
    ).scalar_one_or_none()
    if existing is not None:
        path.unlink(missing_ok=True)
        if existing.deleted_at is None:
            existing = items_svc.file_duplicate(
                session, existing, folder_ids=all_folder_ids, tag_names=tag_names,
                notes=notes, source_url=source_url,
            )
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
        preview_error = None
        try:
            probe = thumbs.generate_previews_isolated(original, item_dir, mime, ext)
            preview_status = previews.outcome(item_dir, mime, ext)
            if preview_status is PreviewStatus.FAILED:
                preview_error = "no thumbnail was produced"
        except Exception as exc:
            log.exception("preview generation failed for %s (%s)", filename, mime)
            preview_status = PreviewStatus.FAILED
            preview_error = previews.short_error(exc)
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
            rating=rating,
            notes=notes or "",
            source_url=source_url,
            deleted_at=deleted_at,
            preview_status=preview_status,
            preview_error=preview_error,
        )
        # imports preserve original timestamps; otherwise the DB defaults apply
        if created_at is not None:
            item.created_at = created_at
        if modified_at is not None:
            item.modified_at = modified_at
        session.add(item)
        if all_folder_ids:
            item.folders = items_svc.resolve_folders(session, all_folder_ids)
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
    except Exception:
        session.rollback()
        _put_back(original, path)
        shutil.rmtree(item_dir, ignore_errors=True)
        raise

    # committed: the item exists, so never remove its files from here on.
    # A failed meta.json write is logged and noted for `kist verify`.
    meta_svc.sync_item(session, item.id)
    session.refresh(item)
    return IngestResult(item, created=True)


def _put_back(original: Path, source: Path) -> None:
    """Return a half-ingested file to where it came from. Never delete it:
    for an inbox drop it may be the only copy."""
    if not original.is_file() or source.exists():
        return
    try:
        original.replace(source)
    except OSError:
        try:
            shutil.move(str(original), source)
        except OSError:
            log.exception("could not put %s back at %s", original, source)


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
    from . import items as items_svc

    # refuse bad input before downloading anything
    items_svc.resolve_folders(session, list(folder_ids or []) + ([folder_id] if folder_id else []))
    for name in tag_names or []:
        validation.tag_name(name)
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

    # URL adds keep only media: anything else (JSON, HTML, archives…) is more
    # likely an internal page or an error body than something worth keeping
    mime = sniff_mime(staging)
    if not is_media_mime(mime):
        staging.unlink(missing_ok=True)
        raise NotMediaError(
            f"that URL isn't an image, video or PDF ({mime}); "
            "download it and upload the file instead"
        )

    return ingest_file(
        session, staging, filename,
        folder_id=folder_id, folder_ids=folder_ids, tag_names=tag_names,
        notes=notes, source_url=source_url or url, rating=rating,
        created_at=created_at, modified_at=modified_at, deleted_at=deleted_at,
    )


class NotMediaError(ValueError):
    pass


class DownloadTooLarge(ValueError):
    pass


def describe_download_error(exc: Exception) -> tuple[int, str] | None:
    """(HTTP status, user-facing message) for a failed URL add, or None if
    exc isn't a download failure. Network errors are deliberately vague:
    detailed ones ("connection refused" vs "timed out") map the network."""
    if isinstance(exc, validation.ValidationError):
        return None  # not a download problem: the caller's input (422)
    if isinstance(exc, netguard.BlockedURLError):
        return 422, str(exc)
    if isinstance(exc, NotMediaError):
        return 422, str(exc)
    if isinstance(exc, netguard.DownloadTimeout):
        return 504, str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        return 502, f"download failed: the server answered {exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return 502, "download failed: could not fetch that URL"
    if isinstance(exc, DownloadTooLarge):
        return 413, str(exc)
    return None


def is_media_mime(mime: str) -> bool:
    return mime.startswith(("image/", "video/")) or mime == "application/pdf"


DEFAULT_USER_AGENT = f"kist/{__version__} (self-hosted asset manager)"


def _download(url: str, *, transport=None) -> tuple[Path, str, str]:
    """Stream url into staging through the network guard.

    Returns (path, filename, bare content type). Raises netguard.BlockedURLError
    for private/local targets, netguard.DownloadTimeout past the deadline,
    ValueError past the size cap, and httpx errors for network failures.
    """
    settings = get_settings()
    settings.staging_dir.mkdir(parents=True, exist_ok=True)
    staging = settings.staging_dir / f"dl-{new_ulid()}"
    max_bytes = settings.max_download_mb * 1024 * 1024
    headers = {
        "User-Agent": settings.user_agent or DEFAULT_USER_AGENT,
        "Accept": "image/avif,image/webp,image/*,video/*,*/*;q=0.8",
    }

    try:
        with netguard.guarded_get(url, headers, transport=transport) as (
            resp, final_url, deadline
        ):
            filename = filename_from_url(final_url, resp)
            content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            received = 0
            with staging.open("wb") as f:
                for chunk in resp.iter_bytes(1024 * 256):
                    received += len(chunk)
                    if received > max_bytes:
                        raise DownloadTooLarge(
                            f"download exceeds max size ({settings.max_download_mb} MB)"
                        )
                    if time.monotonic() > deadline:
                        raise netguard.DownloadTimeout(
                            "download took longer than "
                            f"{settings.download_timeout_seconds} s"
                        )
                    f.write(chunk)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
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


def resolve_import_path(file_path: str) -> Path:
    """The file an agent may import with add_item(file_path=...): it must
    resolve (symlinks and '..' included) to a file inside an import
    directory, never anywhere else on the host or in the library."""
    path = Path(file_path).resolve()
    allowed = [d.resolve() for d in get_settings().import_dirs]
    if not any(path.is_relative_to(d) for d in allowed):
        raise validation.ValidationError(
            "file_path must be inside an import directory: "
            + ", ".join(str(d) for d in allowed)
        )
    if not path.is_file():
        raise validation.ValidationError(f"no file at {path}")
    return path


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
        # never report success for a file we didn't ingest
        raise FileNotFoundError(f"the file to import is gone: {path.name}")
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
        try:
            with SessionLocal() as session:
                session.add(
                    Job(
                        kind=JobKind.INGEST,
                        payload={"path": str(staged), "filename": entry.name},
                    )
                )
                session.commit()
        except BaseException:
            # no job, no import: put it back in the inbox for the next scan
            # (if even that fails, the worker's staging sweep queues it)
            try:
                staged.replace(entry)
            except OSError:
                log.exception("could not put %s back in the inbox", staged.name)
            raise
        log.info("queued inbox file %s", entry.name)
