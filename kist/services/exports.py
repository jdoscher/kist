"""Portable export: a BagIt 1.0 zip that opens without Kist.

    POST /api/exports {target: folder|smart|board|items|all, target_id?,
                       item_ids?, include: {originals, sidecars, events},
                       format: "bagit-zip"}

runs as a worker job and writes library/exports/<id>.zip with

    bagit.txt, bag-info.txt              BagIt 1.0 (RFC 8493)
    manifest-sha256.txt                  every payload file's SHA-256
    tagmanifest-sha256.txt               the same for the files below
    data/<folder path>/<name>.<ext>      originals under readable names
                                         (the full-screen copy without
                                         originals)
    data/.../<name>.<ext>.json           meta.json + provenance (sidecars)
    data/.../<name>.xmp                  XMP for other photo tools (sidecars)
    data/.../<name>.<ext>.events.jsonl   item history (events)
    items.csv                            one row per item
    index.html + thumbs/<id>.webp        browse it offline from any disk

The status is library/exports/<id>.json. Finished exports are deleted
after KIST_EXPORT_RETENTION_DAYS (default 7).
"""

import csv
import hashlib
import html
import io
import json
import logging
import re
import shutil
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..filetypes import kind_for
from ..ids import new_ulid
from ..lifecycle import JobKind
from ..models import Item, Job
from . import meta as meta_svc
from . import provenance as provenance_svc
from . import selection, thumbs, validation, xmp

log = logging.getLogger("kist.exports")

FORMATS = ("bagit-zip",)
STATUSES = ("pending", "running", "done", "failed")
ID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
# already-compressed media are stored, text is deflated
DEFLATE_EXTS = {"json", "xmp", "jsonl", "csv", "html", "txt", "svg", "bmp", "tif", "tiff",
                "psd"}


class NotFound(Exception):
    def __init__(self, export_id: str):
        super().__init__(f"export {export_id} not found")


def exports_dir() -> Path:
    return get_settings().exports_dir


def status_path(export_id: str) -> Path:
    return exports_dir() / f"{export_id}.json"


def zip_path(export_id: str) -> Path:
    return exports_dir() / f"{export_id}.zip"


def _write_status(record: dict) -> None:
    meta_svc.atomic_write_text(status_path(record["id"]),
                               json.dumps(record, indent=2, default=str))


def get(export_id: str) -> dict:
    if not ID_RE.match(export_id or ""):
        raise NotFound(export_id)
    try:
        return json.loads(status_path(export_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise NotFound(export_id) from None


def list_exports() -> list[dict]:
    d = exports_dir()
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json"), reverse=True):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def out(record: dict) -> dict:
    data = dict(record)
    if data.get("status") == "done":
        data["download_url"] = f"/api/exports/{record['id']}/download"
    return data


def create(session: Session, *, target: str, target_id: str | None = None,
           item_ids: list[str] | None = None, include: dict | None = None,
           format: str = "bagit-zip", created_by: str = "owner") -> dict:
    if format not in FORMATS:
        raise validation.ValidationError(f"format must be one of {', '.join(FORMATS)}")
    target_id, item_ids = selection.check(session, target, target_id, item_ids)
    inc = {"originals": True, "sidecars": True, "events": False, **(include or {})}
    unknown = set(inc) - {"originals", "sidecars", "events"}
    if unknown:
        raise validation.ValidationError(f"unknown include options: {', '.join(sorted(unknown))}")
    record = {
        "id": new_ulid(), "status": "pending", "format": format, "target": target,
        "target_id": target_id, "item_ids": item_ids,
        "include": {k: bool(v) for k, v in inc.items()},
        "created_by": created_by, "created_at": datetime.now(UTC).isoformat(),
        "finished_at": None, "error": None, "item_count": None, "size_bytes": None,
    }
    exports_dir().mkdir(parents=True, exist_ok=True)
    _write_status(record)
    session.add(Job(kind=JobKind.EXPORT, payload={"export_id": record["id"],
                                                  "filename": f"export {record['id']}"}))
    session.commit()
    return record


def delete(export_id: str) -> None:
    get(export_id)
    zip_path(export_id).unlink(missing_ok=True)
    status_path(export_id).unlink(missing_ok=True)


def purge_expired(now: datetime | None = None) -> int:
    """Delete exports older than KIST_EXPORT_RETENTION_DAYS (0 keeps them)."""
    days = get_settings().export_retention_days
    if days <= 0:
        return 0
    now = now or datetime.now(UTC)
    removed = 0
    for record in list_exports():
        when = record.get("finished_at") or record.get("created_at")
        try:
            at = datetime.fromisoformat(when)
        except (TypeError, ValueError):
            continue
        if record.get("status") in ("pending", "running"):
            continue
        if now - at > timedelta(days=days):
            zip_path(record["id"]).unlink(missing_ok=True)
            status_path(record["id"]).unlink(missing_ok=True)
            removed += 1
    # a crash mid-export can leave a partial file behind
    for tmp in exports_dir().glob(".*.zip.part") if exports_dir().is_dir() else []:
        try:
            if now.timestamp() - tmp.stat().st_mtime > 86400:
                tmp.unlink()
        except OSError:
            pass
    return removed


def touch_job(job_id: int | None) -> None:
    """Keep a long job's lease fresh so the reaper doesn't re-queue it."""
    if job_id is None:
        return
    from ..db import SessionLocal

    try:
        with SessionLocal() as s:
            s.execute(sa.update(Job).where(Job.id == job_id).values(started_at=datetime.now(UTC)))
            s.commit()
    except Exception:
        log.exception("could not renew the lease of job %s", job_id)


# ---------- names ----------

_BAD = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]+")


def safe_component(name: str, fallback: str = "untitled") -> str:
    """A file or folder name that is safe on every disk: no separators,
    no control characters, never '.' or '..'."""
    name = _BAD.sub("_", str(name or "")).strip().strip(".")
    name = " ".join(name.split())
    if not name:
        name = fallback
    return name[:150]


class Names:
    """Unique readable names per directory (case-insensitive, so a zip
    unpacked on macOS or Windows doesn't overwrite anything)."""

    def __init__(self):
        self.taken: set[str] = set()

    def claim(self, directory: str, stem: str, ext: str) -> str:
        n = 1
        while True:
            candidate = stem if n == 1 else f"{stem} ({n})"
            key = f"{directory}/{candidate}".lower()
            if key not in self.taken:
                self.taken.add(key)
                return candidate
            n += 1


def item_directory(item: Item) -> str:
    paths = sorted(meta_svc.folder_path(f) for f in item.folders)
    if not paths:
        return "Unfiled"
    return "/".join(safe_component(p) for p in paths[0])


def bagit_path(path: str) -> str:
    """RFC 8493 2.1.3: CR, LF and % are percent-encoded in manifests."""
    return path.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


# ---------- building the bag ----------


class _Bag:
    def __init__(self, zf: zipfile.ZipFile):
        self.zf = zf
        self.payload: list[tuple[str, str]] = []
        self.tags: list[tuple[str, str]] = []
        self.payload_bytes = 0

    def _compress(self, name: str) -> int:
        ext = name.rsplit(".", 1)[-1].lower()
        return zipfile.ZIP_DEFLATED if ext in DEFLATE_EXTS else zipfile.ZIP_STORED

    def add_file(self, arcname: str, src: Path, *, tag: bool = False) -> None:
        h = hashlib.sha256()
        size = 0
        info = zipfile.ZipInfo(arcname, date_time=_zip_time(src))
        info.compress_type = self._compress(arcname)
        with src.open("rb") as f, self.zf.open(info, "w", force_zip64=True) as out_f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
                size += len(chunk)
                out_f.write(chunk)
        self._note(arcname, h.hexdigest(), size, tag)

    def add_bytes(self, arcname: str, data: bytes, *, tag: bool = False) -> None:
        info = zipfile.ZipInfo(arcname, date_time=datetime.now(UTC).timetuple()[:6])
        info.compress_type = self._compress(arcname)
        self.zf.writestr(info, data)
        self._note(arcname, hashlib.sha256(data).hexdigest(), len(data), tag)

    def _note(self, arcname: str, digest: str, size: int, tag: bool) -> None:
        if tag:
            self.tags.append((digest, arcname))
        else:
            self.payload.append((digest, arcname))
            self.payload_bytes += size


def _zip_time(path: Path):
    try:
        ts = max(path.stat().st_mtime, 315532800)  # zip can't store before 1980
    except OSError:
        ts = datetime.now(UTC).timestamp()
    return datetime.fromtimestamp(ts, UTC).timetuple()[:6]


CSV_FIELDS = ("id", "path", "filename", "folders", "tags", "rating", "notes", "page_url",
              "image_url", "creator", "credit", "license", "ai_generated", "captured_at",
              "created_at", "modified_at", "mime", "size_bytes", "width", "height", "sha256")


def run(session: Session, export_id: str, job_id: int | None = None) -> dict:
    """Build the zip for an export (the worker's export job)."""
    record = get(export_id)
    record.update(status="running", error=None)
    _write_status(record)
    final = zip_path(export_id)
    part = exports_dir() / f".{export_id}.zip.part"
    try:
        n, size = _build(session, record, part, job_id)
        part.replace(final)
        record.update(status="done", item_count=n, size_bytes=final.stat().st_size,
                      payload_bytes=size, finished_at=datetime.now(UTC).isoformat())
    except Exception as exc:
        part.unlink(missing_ok=True)
        record.update(status="failed", error=str(exc)[:1000],
                      finished_at=datetime.now(UTC).isoformat())
        _write_status(record)
        raise
    _write_status(record)
    return record


def _build(session: Session, record: dict, dest: Path, job_id: int | None) -> tuple[int, int]:
    settings = get_settings()
    inc = record.get("include") or {}
    ids = selection.ids(session, record["target"], record.get("target_id"),
                        record.get("item_ids"))
    names = Names()
    rows: list[dict] = []
    cards: list[str] = []
    with zipfile.ZipFile(dest, "w", allowZip64=True) as zf:
        bag = _Bag(zf)
        for n, item_id in enumerate(ids):
            if n and n % 50 == 0:
                touch_job(job_id)
                session.expunge_all()
            item = session.get(Item, item_id)
            if item is None or item.deleted_at is not None:
                continue
            item_dir = settings.items_dir / item.id
            directory = "data/" + item_directory(item)
            if inc.get("originals", True):
                src = item_dir / f"original.{item.ext}"
                ext = item.ext
            else:
                src = next((item_dir / f for f in (thumbs.SCREEN_FILE, "display.webp",
                                                   "thumb_1024.webp") if (item_dir / f).is_file()),
                           item_dir / f"original.{item.ext}")
                ext = src.suffix.lstrip(".") or item.ext
            if not src.is_file():
                log.warning("export %s: %s has no file on disk; skipped", record["id"], item.id)
                continue
            raw_stem = item.filename.rsplit(".", 1)[0] if "." in item.filename else item.filename
            stem = names.claim(directory, safe_component(raw_stem, item.id), ext)
            rel = f"{directory}/{stem}.{safe_component(ext, 'bin')}"
            bag.add_file(rel, src)
            meta = meta_svc.to_meta(item)
            if inc.get("sidecars", True):
                bag.add_bytes(f"{rel}.json", json.dumps(meta, indent=2, ensure_ascii=False,
                                                         default=str).encode("utf-8"))
                existing = item_dir / xmp.SIDECAR_NAME
                packet = xmp.build(xmp.item_fields(item),
                                   existing.read_bytes() if existing.is_file() else None)
                bag.add_bytes(f"{directory}/{stem}.xmp", packet)
            if inc.get("events"):
                events_file = item_dir / "events.jsonl"
                if events_file.is_file():
                    bag.add_file(f"{rel}.events.jsonl", events_file)
            thumb = item_dir / "thumb_256.webp"
            thumb_rel = None
            if thumb.is_file():
                thumb_rel = f"thumbs/{item.id}.webp"
                bag.add_file(thumb_rel, thumb, tag=True)
            prov = provenance_svc.out(item)
            row = {
                "id": item.id, "path": rel, "filename": item.filename,
                "folders": "; ".join("/".join(p) for p in meta["folder_paths"]),
                "tags": "; ".join(meta["tags"]), "rating": item.rating, "notes": item.notes,
                "page_url": prov.get("page_url", ""), "image_url": prov.get("image_url", ""),
                "creator": prov.get("creator", ""), "credit": prov.get("credit", ""),
                "license": prov.get("license", ""),
                "ai_generated": "" if prov.get("ai_generated") is None else prov["ai_generated"],
                "captured_at": prov.get("captured_at", ""),
                "created_at": meta["created_at"], "modified_at": meta["modified_at"],
                "mime": item.mime, "size_bytes": item.size_bytes, "width": item.width or "",
                "height": item.height or "", "sha256": item.sha256,
            }
            rows.append(row)
            cards.append(_card(row, thumb_rel, kind_for(item.mime, item.ext)))

        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
        w.writeheader()
        for row in rows:
            w.writerow(row)
        bag.add_bytes("items.csv", buf.getvalue().encode("utf-8"), tag=True)
        bag.add_bytes("index.html", _index_html(record, cards).encode("utf-8"), tag=True)

        manifest = "".join(f"{d}  {bagit_path(p)}\n" for d, p in bag.payload)
        bag.add_bytes("manifest-sha256.txt", manifest.encode("utf-8"), tag=True)
        bag.add_bytes("bagit.txt",
                      b"BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n", tag=True)
        target = record["target"] + (f" {record['target_id']}" if record.get("target_id") else "")
        info = (
            "Source-Organization: Kist (self-hosted)\n"
            f"Bagging-Date: {datetime.now(UTC).date().isoformat()}\n"
            f"Bag-Software-Agent: kist/{__version__}\n"
            f"Payload-Oxum: {bag.payload_bytes}.{len(bag.payload)}\n"
            f"External-Identifier: {record['id']}\n"
            f"External-Description: Kist export of {target}: {len(rows)} items\n"
        )
        bag.add_bytes("bag-info.txt", info.encode("utf-8"), tag=True)
        # tag manifest: every tag file except the tag manifest itself
        tagmanifest = "".join(f"{d}  {bagit_path(p)}\n" for d, p in bag.tags)
        info_ = zipfile.ZipInfo("tagmanifest-sha256.txt",
                                date_time=datetime.now(UTC).timetuple()[:6])
        info_.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info_, tagmanifest)
    return len(rows), bag.payload_bytes


def _href(path: str) -> str:
    from urllib.parse import quote

    return quote(path, safe="/")


def _card(row: dict, thumb_rel: str | None, kind: str) -> str:
    e = html.escape
    img = (f'<img src="{e(_href(thumb_rel))}" alt="" loading="lazy">' if thumb_rel
           else f'<div class="ph">{e(kind)}</div>')
    source = ""
    if row["page_url"] and str(row["page_url"]).startswith(("http://", "https://")):
        source = f'<a class="src" href="{e(row["page_url"])}" rel="noreferrer">source</a>'
    tags = " ".join(f"<span>{e(t)}</span>" for t in row["tags"].split("; ") if t)
    stars = "★" * int(row["rating"] or 0)
    notes = f'<p class="notes">{e(row["notes"][:500])}</p>' if row["notes"] else ""
    credit = " · ".join(e(str(row[k])) for k in ("creator", "credit", "license") if row[k])
    search = " ".join(str(row[k]) for k in ("filename", "tags", "notes", "folders", "creator"))
    return (
        f'<figure data-s="{e(search.lower())}"><a href="{e(_href(row["path"]))}">{img}</a>'
        f'<figcaption><a href="{e(_href(row["path"]))}">{e(row["filename"])}</a>'
        f'<div class="meta">{e(row["folders"])} {stars}</div>'
        f'<div class="tags">{tags}</div>{notes}'
        f'<div class="meta">{credit} {source}</div></figcaption></figure>'
    )


def _index_html(record: dict, cards: list[str]) -> str:
    title = f"Kist export {record['id']}"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{font:14px/1.4 system-ui,sans-serif;margin:0;background:#fafafa;color:#222}}
header{{padding:16px;border-bottom:1px solid #ddd;position:sticky;top:0;background:#fff}}
h1{{font-size:18px;margin:0 0 8px}}
input{{width:100%;max-width:420px;padding:6px}}
main{{display:grid;gap:12px;padding:16px;grid-template-columns:repeat(auto-fill,minmax(200px,1fr))}}
figure{{margin:0;background:#fff;border:1px solid #e3e3e3;border-radius:6px;overflow:hidden}}
figure img,.ph{{width:100%;height:180px;object-fit:cover;display:block;background:#eee}}
.ph{{display:flex;align-items:center;justify-content:center;color:#888}}
figcaption{{padding:6px 8px;word-break:break-word}}
.meta{{color:#777;font-size:12px}}
.tags span{{display:inline-block;background:#eef;border-radius:3px;padding:0 4px;margin:2px;
font-size:11px}}
.notes{{font-size:12px;color:#444;margin:4px 0}}
</style></head><body>
<header><h1>{html.escape(title)}</h1>
<div class="meta">{len(cards)} items · made {html.escape(record["created_at"][:10])} ·
originals are in data/, metadata in items.csv and the .json/.xmp files beside each file.
manifest-sha256.txt lets any BagIt tool check every file.</div>
<input id="q" type="search" placeholder="Filter by name, tag, note, folder…"></header>
<main id="grid">
{chr(10).join(cards)}
</main>
<script>
document.getElementById('q').addEventListener('input', function (e) {{
  var q = e.target.value.toLowerCase();
  document.querySelectorAll('#grid figure').forEach(function (f) {{
    f.style.display = f.getAttribute('data-s').indexOf(q) === -1 ? 'none' : '';
  }});
}});
</script>
</body></html>
"""


def run_job(session: Session, job: Job) -> None:
    export_id = (job.payload or {}).get("export_id")
    run(session, export_id, job.id)


def copy_to(export_id: str, dest: Path) -> Path:
    """CLI: copy a finished export somewhere else."""
    src = zip_path(export_id)
    shutil.copyfile(src, dest)
    return dest
