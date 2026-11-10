"""The importer framework: Entry, the runner, folders and reports."""

import json
import logging
import shutil
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..config import get_settings
from ..ids import new_ulid
from ..models import Folder, Item
from ..services import events, ingest, validation
from ..services import folders as folders_svc

log = logging.getLogger("kist.importers")

KINDS = ("pinterest", "arena", "raindrop", "takeout", "apple-photos", "sidecar", "eagle")
ROOT_FOLDER = "Imported"
MAX_ZIP_MEMBERS = 200_000
MAX_ZIP_BYTES = 64 * 1024**3
MAX_ERRORS_REPORTED = 200
MEDIA_EXTS = {
    "jpg", "jpeg", "png", "gif", "webp", "avif", "heic", "heif", "tif", "tiff", "bmp", "svg",
    "mp4", "mov", "m4v", "webm", "mkv", "avi", "pdf", "psd", "dng", "cr2", "cr3", "nef",
    "arw", "raf", "orf", "rw2", "jxl", "ico",
}


class ImportError_(validation.ValidationError):
    """The source can't be imported (bad path, wrong format): HTTP 422."""


@dataclass
class Entry:
    key: str  # the item's id in the source, for reports and provenance
    path: Path | None = None  # a local file
    url: str | None = None  # or an image/page to download
    filename: str | None = None
    folders: list[list[str]] = field(default_factory=list)  # paths under the run folder
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    rating: int = 0
    created_at: datetime | None = None
    modified_at: datetime | None = None
    deleted: bool = False
    provenance: dict = field(default_factory=dict)
    xmp_path: Path | None = None
    skip: str | None = None  # why this one isn't imported (a text block, ...)
    error: str | None = None  # a problem reading it from the source


class Importer:
    kind = ""

    def __init__(self, source: str, workdir: Path, *, transport=None):
        self.source = source
        self.workdir = workdir
        self.transport = transport

    def entries(self) -> Iterator[Entry]:  # pragma: no cover - interface
        raise NotImplementedError

    def after(self, session: Session, run: "Run") -> None:
        """Anything beyond items (Eagle's smart folders)."""


def get_importer(kind: str):
    from . import eagle, links, sidecar

    table = {
        "pinterest": links.PinterestImporter, "arena": links.ArenaImporter,
        "raindrop": links.RaindropImporter, "takeout": sidecar.TakeoutImporter,
        "apple-photos": sidecar.ApplePhotosImporter,
        "sidecar": sidecar.SidecarImporter, "eagle": eagle.EagleImporter,
    }
    if kind not in table:
        raise ImportError_(f"unknown import kind {kind!r}; expected one of {', '.join(KINDS)}")
    return table[kind]


# ---------- helpers for importers ----------


def parse_time(value) -> datetime | None:
    """ISO 8601, epoch seconds/milliseconds, or exiftool's 'YYYY:MM:DD HH:MM:SS'."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)) or str(value).strip().lstrip("-").isdigit():
            n = float(value)
            if n > 1e11:  # milliseconds
                n /= 1000
            return datetime.fromtimestamp(n, UTC)
        text = str(value).strip()
        if len(text) >= 19 and text[4] == ":" and text[7] == ":":
            text = text[:4] + "-" + text[5:7] + "-" + text[8:]
        text = text.replace("Z", "+00:00").replace(" UTC", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, OverflowError, OSError):
        return None


def clean_tags(values) -> list[str]:
    out: list[str] = []
    if isinstance(values, str):
        values = [v for v in values.replace(";", ",").split(",")]
    for v in values or []:
        try:
            name = validation.tag_name(str(v))
        except validation.ValidationError:
            continue
        if name.lower() not in {t.lower() for t in out}:
            out.append(name)
    return out


def is_media(path: Path) -> bool:
    return path.suffix.lstrip(".").lower() in MEDIA_EXTS


def safe_url(value) -> str | None:
    try:
        return validation.source_url(value)
    except validation.ValidationError:
        return None


def resolve_source(source: str, *, allow_url: bool = False) -> Path | str:
    """A local path, checked: the API may only import from the import
    directories (library/import, library/inbox); the CLI anywhere."""
    if allow_url and source.startswith(("http://", "https://", "arena:")):
        return source
    path = Path(source).expanduser()
    if not path.exists():
        raise ImportError_(f"nothing at {source}")
    return path.resolve()


def check_api_source(source: str, kind: str) -> None:
    """POST /api/imports: a path must sit inside an import directory."""
    if kind == "arena" and source.startswith(("http://", "https://", "arena:")):
        return
    path = Path(source).resolve()
    allowed = [d.resolve() for d in get_settings().import_dirs]
    if not any(path == d or path.is_relative_to(d) for d in allowed):
        raise ImportError_(
            "source must be inside an import directory: " + ", ".join(str(d) for d in allowed))
    if not path.exists():
        raise ImportError_(f"nothing at {source}")


def unpack(path: Path, workdir: Path) -> Path:
    """A zip, unpacked safely into workdir; a directory as it is."""
    if path.is_dir():
        return path
    if not zipfile.is_zipfile(path):
        return path
    dest = workdir / "unzipped"
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with zipfile.ZipFile(path) as zf:
        members = zf.infolist()
        if len(members) > MAX_ZIP_MEMBERS:
            raise ImportError_("that zip has too many files")
        if sum(m.file_size for m in members) > MAX_ZIP_BYTES:
            raise ImportError_("that zip unpacks to more than Kist will take at once")
        for m in members:
            name = m.filename.replace("\\", "/")
            if m.is_dir() or name.startswith("__MACOSX/"):
                continue
            target = (dest / name).resolve()
            if not target.is_relative_to(root) or name.startswith("/") or ".." in name.split("/"):
                raise ImportError_(f"refusing a zip entry outside the archive: {m.filename!r}")
            if (m.external_attr >> 16) & 0o170000 == 0o120000:
                continue  # symlinks are never followed out of the archive
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(m) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
    return dest


# ---------- running ----------


@dataclass
class Run:
    kind: str
    source: str
    dry_run: bool
    folder_name: str
    report: dict
    run_folder_id: str | None = None
    folder_ids: dict = field(default_factory=dict)
    seen_hashes: dict = field(default_factory=dict)


def _top_folder(session: Session, name: str, parent_id: str | None) -> Folder | None:
    return session.execute(
        sa.select(Folder).where(Folder.name == name, Folder.parent_id.is_(parent_id)
                                if parent_id is None else Folder.parent_id == parent_id)
        .order_by(Folder.id).limit(1)
    ).scalar_one_or_none()


def _make_run_folder(session: Session, run: Run) -> str:
    root = _top_folder(session, ROOT_FOLDER, None)
    if root is None:
        root = folders_svc.create_folder(session, ROOT_FOLDER)
    name, n = run.folder_name, 1
    while _top_folder(session, name, root.id) is not None:
        n += 1
        name = f"{run.folder_name} ({n})"
    folder = folders_svc.create_folder(session, name, parent_id=root.id)
    run.report["folder_path"] = f"{ROOT_FOLDER}/{name}"
    return folder.id


def _folder_for(session: Session, run: Run, path: list[str]) -> str:
    if run.run_folder_id is None:
        run.run_folder_id = _make_run_folder(session, run)
        run.report["folder_id"] = run.run_folder_id
    parent = run.run_folder_id
    key: tuple = ()
    for part in path:
        part = " ".join(str(part).split())[: validation.FOLDER_NAME_MAX] or "Untitled"
        key = (*key, part)
        if key not in run.folder_ids:
            existing = _top_folder(session, part, parent)
            run.folder_ids[key] = (existing or folders_svc.create_folder(
                session, part, parent_id=parent)).id
        parent = run.folder_ids[key]
    return parent


def _note_error(report: dict, key: str, error: str) -> None:
    report["errors_count"] += 1
    if len(report["errors"]) < MAX_ERRORS_REPORTED:
        report["errors"].append({"key": key, "error": error[:500]})


def _ingest_entry(session: Session, run: Run, entry: Entry) -> None:
    report = run.report
    prov = {**entry.provenance, "method": "import", "imported_from": run.kind,
            "original_id": str(entry.key)[:500]}
    prov = {k: v for k, v in prov.items() if v not in (None, "")}
    deleted_at = None
    if entry.deleted:
        deleted_at = entry.modified_at or entry.created_at or datetime.now(UTC)
    if run.dry_run:
        if entry.path is not None:
            digest = ingest.sha256_file(entry.path)
            existing = session.execute(
                sa.select(Item.id).where(Item.sha256 == digest)).scalar_one_or_none()
            if existing or digest in run.seen_hashes:
                report["duplicates"] += 1
            else:
                report["imported"] += 1
            run.seen_hashes.setdefault(digest, entry.key)
        else:
            report["to_download"] += 1
        return
    folder_ids = [_folder_for(session, run, p) for p in (entry.folders or [[]])]
    kwargs = dict(
        folder_ids=folder_ids, tag_names=entry.tags, notes=entry.notes,
        rating=entry.rating, created_at=entry.created_at, modified_at=entry.modified_at,
        deleted_at=deleted_at, provenance=prov,
    )
    page_url = prov.pop("page_url", None)
    if entry.path is not None:
        staging = get_settings().staging_dir
        staging.mkdir(parents=True, exist_ok=True)
        copy = staging / f"import-{new_ulid()}"  # never consume the user's source
        shutil.copyfile(entry.path, copy)
        try:
            result = ingest.ingest_file(
                session, copy, ingest.sanitize_filename(entry.filename or entry.path.name),
                source_url=page_url, xmp_path=entry.xmp_path, **kwargs)
        finally:
            copy.unlink(missing_ok=True)
    else:
        result = ingest.ingest_url(session, entry.url, source_url=page_url, **kwargs)
    report["imported" if result.created else "duplicates"] += 1


def run_import(
    session: Session,
    kind: str,
    source: str,
    *,
    dry_run: bool = False,
    folder: str | None = None,
    progress: Callable[[dict], None] | None = None,
    transport=None,
) -> dict:
    """Run an importer. Returns the report: {kind, source, dry_run, found,
    imported, duplicates, skipped, to_download, errors_count, errors[],
    folder_id, folder_path}."""
    cls = get_importer(kind)
    folder_name = " ".join((folder or f"{kind} {date.today().isoformat()}").split())
    validation.folder_name(folder_name)
    report = {
        "kind": kind, "source": source, "dry_run": dry_run, "found": 0, "imported": 0,
        "duplicates": 0, "skipped": 0, "to_download": 0, "errors_count": 0, "errors": [],
        "folder_id": None, "folder_path": None, "started_at": datetime.now(UTC).isoformat(),
        "finished_at": None, "batch_id": None,
    }
    workdir = get_settings().staging_dir / f"importwork-{new_ulid()}"
    workdir.mkdir(parents=True, exist_ok=True)
    run = Run(kind=kind, source=source, dry_run=dry_run, folder_name=folder_name, report=report)
    try:
        with events.acting("importer", "import") as ctx:
            report["batch_id"] = None if dry_run else ctx.batch_id
            importer = cls(source, workdir, transport=transport)
            for entry in importer.entries():
                report["found"] += 1
                if entry.error:
                    _note_error(report, entry.key, entry.error)
                elif entry.skip:
                    report["skipped"] += 1
                else:
                    try:
                        _ingest_entry(session, run, entry)
                    except Exception as exc:  # one bad file never stops the run
                        session.rollback()
                        described = ingest.describe_download_error(exc) if isinstance(
                            exc, Exception) else None
                        _note_error(report, entry.key, described[1] if described else
                                    (str(exc) or type(exc).__name__))
                if progress and report["found"] % 10 == 0:
                    progress(report)
            if not dry_run:
                importer.after(session, run)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    report["finished_at"] = datetime.now(UTC).isoformat()
    if progress:
        progress(report)
    return report


# ---------- the worker job ----------


def run_job(session: Session, job) -> None:
    from ..models import Job
    from ..services.exports import touch_job

    payload = dict(job.payload or {})

    def progress(report: dict) -> None:
        from ..db import SessionLocal

        touch_job(job.id)
        with SessionLocal() as s:
            row = s.get(Job, job.id)
            if row is not None:
                row.payload = {**(row.payload or {}), "report": json.loads(json.dumps(report))}
                s.commit()

    report = run_import(session, payload["kind"], payload["source"],
                        dry_run=bool(payload.get("dry_run")), folder=payload.get("folder"),
                        progress=progress)
    progress(report)
