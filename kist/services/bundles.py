"""Asset bundles (Milestone 5): a zip kept whole as one item.

A model with its textures, a texture set, a font family, a mockup kit:
the zip is stored as is (the original), and Kist lists what's inside
(items/<id>/bundle.json), previews it from the best picture inside, and
serves single files out of it.

Safety:
- Entry names are only ever looked up in the zip's own directory; nothing
  is extracted to disk, so a name like ../../etc/passwd can't go anywhere.
  get_entry() still refuses absolute paths, backslashes, NUL bytes, drive
  letters and '..' segments outright.
- Zip bombs: the entry count, the total declared size and each entry's
  size are capped (KIST_BUNDLE_MAX_*), and reads stop at the cap even if
  the declared size lies.
- Files served from a bundle are attachments unless they are raster
  images, always with nosniff and a sandbox CSP.
"""

import json
import mimetypes
import posixpath
import re
import zipfile
from pathlib import Path, PurePosixPath

from ..config import get_settings

IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif", "tif", "tiff", "bmp", "tga", "exr", "hdr"}
PREVIEWABLE = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tif", "tiff"}
MODEL_EXTS = {"glb", "gltf", "obj", "fbx", "stl", "ply", "usdz", "usd", "usda", "usdc", "blend",
              "dae", "3ds", "abc", "c4d", "max", "ma", "mb"}
FONT_EXTS = {"ttf", "otf", "woff", "woff2", "ttc"}
DOC_NAMES = re.compile(r"(^|/)(licen[cs]e|copying|readme|credits?)(\.[a-z]+)?$", re.IGNORECASE)
COVER_HINTS = ("preview", "thumbnail", "thumb", "cover", "render", "poster")
ALBEDO_HINTS = ("albedo", "basecolor", "base_color", "diffuse", "color", "col")
INLINE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


class BundleError(ValueError):
    pass


def _limits() -> tuple[int, int, int]:
    s = get_settings()
    return (s.bundle_max_entries, s.bundle_max_total_mb * 1024 * 1024,
            s.bundle_max_entry_mb * 1024 * 1024)


def _category(name: str) -> str:
    ext = PurePosixPath(name).suffix.lstrip(".").lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in MODEL_EXTS:
        return "model"
    if ext in FONT_EXTS:
        return "font"
    if DOC_NAMES.search(name):
        return "doc"
    if ext in ("mp4", "mov", "webm", "mkv"):
        return "video"
    if ext in ("mp3", "wav", "flac", "ogg", "m4a", "aiff"):
        return "audio"
    return "other"


def manifest(path: Path) -> dict:
    """What's in the zip (raises BundleError for a zip that breaks the caps)."""
    max_entries, max_total, max_entry = _limits()
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise BundleError(f"not a readable zip file: {exc}") from None
    with zf:
        infos = zf.infolist()
        if len(infos) > max_entries:
            raise BundleError(f"the zip has more than {max_entries} entries")
        entries = []
        total = 0
        for info in infos:
            if info.is_dir():
                continue
            name = info.filename
            total += info.file_size
            entry = {
                "path": name,
                "size": info.file_size,
                "compressed": info.compress_size,
                "kind": _category(name),
                "safe": safe_name(name),
            }
            if info.file_size > max_entry:
                entry["too_large"] = True
            entries.append(entry)
        if total > max_total:
            raise BundleError("the zip expands past the bundle size limit")
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    out = {
        "entries": entries,
        "count": len(entries),
        "total_size": total,
        "kinds": counts,
        "cover": pick_cover(entries),
        "model": next((e["path"] for e in entries if e["kind"] == "model" and e["safe"]), None),
        "docs": [e["path"] for e in entries if e["kind"] == "doc" and e["safe"]][:10],
    }
    return out


def pick_cover(entries: list[dict]) -> str | None:
    images = [e for e in entries if e["kind"] == "image" and e["safe"] and not e.get("too_large")
              and PurePosixPath(e["path"]).suffix.lstrip(".").lower() in PREVIEWABLE
              and not PurePosixPath(e["path"]).name.startswith(".")
              and "__MACOSX" not in e["path"]]
    if not images:
        return None

    def score(e: dict) -> tuple:
        stem = PurePosixPath(e["path"]).stem.lower()
        hint = 0 if any(h in stem for h in COVER_HINTS) else (
            1 if any(h in stem for h in ALBEDO_HINTS) else 2)
        return (hint, e["path"].count("/"), e["path"].lower())

    return sorted(images, key=score)[0]["path"]


_DRIVE = re.compile(r"^[A-Za-z]:")


def safe_name(name: str) -> bool:
    if not name or "\x00" in name or "\\" in name or name.startswith("/") or _DRIVE.match(name):
        return False
    parts = name.split("/")
    return all(p not in ("..",) for p in parts) and posixpath.normpath(name) == name.rstrip("/")


def check_path(requested: str) -> str:
    """A requested entry path, refused if it tries to leave the bundle."""
    if not safe_name(requested):
        raise BundleError("that path isn't allowed")
    return requested


def read_entry(path: Path, name: str) -> tuple[bytes, str]:
    """(bytes, MIME type) of one entry, capped at KIST_BUNDLE_MAX_ENTRY_MB."""
    check_path(name)
    _n, _t, max_entry = _limits()
    with zipfile.ZipFile(path) as zf:
        try:
            info = zf.getinfo(name)
        except KeyError:
            raise FileNotFoundError(name) from None
        if info.is_dir():
            raise FileNotFoundError(name)
        if info.file_size > max_entry:
            raise BundleError("that file is larger than the bundle entry limit")
        with zf.open(info) as f:
            data = f.read(max_entry + 1)
        if len(data) > max_entry:
            raise BundleError("that file is larger than the bundle entry limit")
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return data, mime


def write_manifest(item_dir: Path, data: dict) -> None:
    (item_dir / "bundle.json").write_text(json.dumps(data, separators=(",", ":")))


def load_manifest(item_dir: Path, original: Path) -> dict:
    try:
        return json.loads((item_dir / "bundle.json").read_text())
    except (OSError, ValueError):
        data = manifest(original)
        try:
            write_manifest(item_dir, data)
        except OSError:
            pass
        return data


def summary(data: dict) -> dict:
    """The part of the manifest kept in items.media (meta.json)."""
    return {k: data[k] for k in ("count", "total_size", "kinds", "cover", "model", "docs")
            if k in data}
