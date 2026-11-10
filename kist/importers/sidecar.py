"""Generic sidecars, Google Photos Takeout and Apple Photos (osxphotos).

sidecar       any folder of files with `<name>.json` / `<name.ext>.json`
              and/or `.xmp` sidecars beside them. JSON keys understood:
              title, description|notes|caption, tags|keywords, rating,
              url|source_url|page_url, image_url, created_at|date|taken_at,
              creator|author|artist, credit, license, ai_generated.
takeout       Google Takeout's "Google Photos" folders. Each photo has a
              JSON sidecar: `IMG.jpg.json`, or since late 2024
              `IMG.jpg.supplemental-metadata.json` (cut short to fit a name
              limit: `IMG.jpg.supplemental-meta.json` …); a numbered copy
              `IMG(1).jpg` pairs with `IMG.jpg(1).json`. Read: title,
              description, photoTakenTime, geoData, favorited, trashed.
              Albums become folders; "Photos from 2021" year folders don't.
apple-photos  a folder made by `osxphotos export DEST --sidecar json`
              (exiftool-style JSON, `photo.jpg.json` or with
              --sidecar-drop-ext `photo.json`): XMP:Title, XMP:Description,
              XMP:Subject / IPTC:Keywords / XMP:TagsList, XMP:PersonInImage,
              XMP:Rating, EXIF:DateTimeOriginal (+ OffsetTimeOriginal) and
              GPS. Sub-folders (e.g. --directory "{album}") become folders.
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path

from ..services import xmp
from .base import (
    Entry,
    Importer,
    ImportError_,
    clean_tags,
    is_media,
    parse_time,
    safe_url,
    unpack,
)


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and is_media(p)
                  and not any(part.startswith(".") for part in p.relative_to(root).parts))


def _folder_of(root: Path, path: Path) -> list[list[str]]:
    parts = list(path.parent.relative_to(root).parts)
    return [parts] if parts else []


def _root(source: str, workdir: Path) -> Path:
    root = unpack(Path(source), workdir)
    if not root.is_dir():
        raise ImportError_("expected a folder or a zip")
    return root


def _json(path: Path):
    try:
        if path.stat().st_size > 5 * 1024 * 1024:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class SidecarImporter(Importer):
    kind = "sidecar"

    def entries(self) -> Iterator[Entry]:
        root = _root(self.source, self.workdir)
        for f in _files(root):
            data = None
            for cand in (f.parent / f"{f.name}.json", f.with_suffix(".json")):
                if cand.is_file() and cand != f:
                    data = _json(cand)
                    if data is None:
                        yield Entry(key=str(f.relative_to(root)), error=f"{cand.name} unreadable")
                    break
            entry = Entry(key=str(f.relative_to(root)), path=f, filename=f.name,
                          folders=_folder_of(root, f), xmp_path=xmp.neighbour(f))
            if isinstance(data, list) and data and isinstance(data[0], dict):
                data = data[0]
            if isinstance(data, dict):
                _apply_generic(entry, data)
            yield entry


def _first(data: dict, *keys):
    for k in keys:
        if data.get(k) not in (None, "", []):
            return data[k]
    return None


def _apply_generic(entry: Entry, data: dict) -> None:
    tags = _first(data, "tags", "keywords")
    entry.tags = clean_tags(tags if isinstance(tags, (list, str)) else [])
    entry.notes = str(_first(data, "description", "notes", "caption") or "")
    title = _first(data, "title")
    if title and not entry.notes:
        entry.notes = str(title)
    try:
        entry.rating = max(0, min(5, int(_first(data, "rating") or 0)))
    except (TypeError, ValueError):
        pass
    entry.created_at = parse_time(_first(data, "created_at", "date", "taken_at"))
    prov = {
        "page_url": safe_url(_first(data, "page_url", "source_url", "url")),
        "image_url": safe_url(_first(data, "image_url")),
        "creator": _first(data, "creator", "author", "artist"),
        "credit": _first(data, "credit"), "license": _first(data, "license"),
    }
    if isinstance(data.get("ai_generated"), bool):
        prov["ai_generated"] = data["ai_generated"]
    entry.provenance = {k: v for k, v in prov.items() if v not in (None, "")}


# ---------- Google Takeout ----------

YEAR_FOLDER = re.compile(r"^Photos from \d{4}$")
_NUMBERED = re.compile(r"^(?P<stem>.*)\((?P<n>\d+)\)(?P<ext>\.[^.]+)$")


def takeout_sidecar(f: Path) -> Path | None:
    """The JSON sidecar of a Takeout photo, across the naming variants."""
    d, name = f.parent, f.name
    candidates = [d / f"{name}.json", d / f"{name}.supplemental-metadata.json"]
    m = _NUMBERED.match(name)
    if m:  # IMG(1).jpg -> IMG.jpg(1).json / IMG.jpg.supplemental-metadata(1).json
        base = f"{m['stem']}{m['ext']}"
        candidates += [d / f"{base}({m['n']}).json",
                       d / f"{base}.supplemental-metadata({m['n']}).json"]
    for c in candidates:
        if c.is_file():
            return c
    # cut-short names: IMG_0001.jpg.supplemental-metad.json, IMG_0001.jpg.supp.json
    prefix = (f"{m['stem']}{m['ext']}" if m else name) + "."
    for c in sorted(d.glob("*.json")):
        if c.name.startswith(prefix) and (not m or f"({m['n']})" in c.name):
            return c
    # very long names are cut before the extension: match on the title inside
    for c in sorted(d.glob("*.json")):
        data = _json(c)
        if isinstance(data, dict) and data.get("title") == name:
            return c
    return None


class TakeoutImporter(Importer):
    kind = "takeout"

    def entries(self) -> Iterator[Entry]:
        root = _root(self.source, self.workdir)
        photos_root = next((p for p in root.rglob("Google Photos") if p.is_dir()), root)
        for f in _files(photos_root):
            rel = f.relative_to(photos_root)
            album = list(rel.parent.parts)
            folders = [album] if album and not YEAR_FOLDER.match(album[0]) else []
            side = takeout_sidecar(f)
            data = _json(side) if side else None
            entry = Entry(key=str(rel), path=f, filename=f.name, folders=folders,
                          xmp_path=xmp.neighbour(f))
            if isinstance(data, dict):
                taken = (data.get("photoTakenTime") or {}).get("timestamp")
                created = (data.get("creationTime") or {}).get("timestamp")
                entry.created_at = parse_time(taken) or parse_time(created)
                entry.notes = str(data.get("description") or "")
                if data.get("title") and "." in str(data["title"]):
                    entry.filename = str(data["title"])
                geo = data.get("geoData") or data.get("geoDataExif") or {}
                try:
                    lat, lon = float(geo.get("latitude")), float(geo.get("longitude"))
                    if lat or lon:
                        entry.provenance["geo"] = {"lat": lat, "lon": lon}
                except (TypeError, ValueError):
                    pass
                if data.get("favorited"):
                    entry.tags.append("favorite")
                entry.deleted = bool(data.get("trashed"))
                if data.get("url"):
                    entry.key = str(data["url"])
            yield entry


# ---------- Apple Photos via osxphotos ----------


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _gps(data: dict, axis: str) -> float | None:
    for key in (f"EXIF:GPS{axis}", f"XMP:GPS{axis}", f"Composite:GPS{axis}"):
        value = data.get(key)
        if value in (None, ""):
            continue
        try:
            n = float(value)
        except (TypeError, ValueError):
            continue
        ref = str(data.get(f"EXIF:GPS{axis}Ref") or "").upper()
        if ref in ("S", "W") and n > 0:
            n = -n
        return n
    return None


class ApplePhotosImporter(Importer):
    kind = "apple-photos"

    def entries(self) -> Iterator[Entry]:
        root = _root(self.source, self.workdir)
        for f in _files(root):
            side = next((c for c in (f.parent / f"{f.name}.json", f.with_suffix(".json"))
                         if c.is_file()), None)
            data = _json(side) if side else None
            if isinstance(data, list):
                data = data[0] if data and isinstance(data[0], dict) else None
            entry = Entry(key=str(f.relative_to(root)), path=f, filename=f.name,
                          folders=_folder_of(root, f), xmp_path=xmp.neighbour(f))
            if side is not None and data is None:
                entry.error = f"{side.name} unreadable"
            elif isinstance(data, dict):
                title = _first(data, "XMP:Title", "IPTC:ObjectName")
                desc = _first(data, "XMP:Description", "EXIF:ImageDescription",
                              "IPTC:Caption-Abstract")
                entry.notes = "\n\n".join(str(x) for x in (title, desc) if x)
                keywords = (_as_list(data.get("XMP:Subject")) or _as_list(data.get("IPTC:Keywords"))
                            or _as_list(data.get("XMP:TagsList")))
                people = [f"person:{p}" for p in _as_list(data.get("XMP:PersonInImage"))]
                entry.tags = clean_tags([*keywords, *people])
                try:
                    entry.rating = max(0, min(5, int(data.get("XMP:Rating") or 0)))
                except (TypeError, ValueError):
                    pass
                when = _first(data, "EXIF:DateTimeOriginal", "XMP:DateTimeOriginal",
                              "XMP:DateCreated", "QuickTime:CreationDate")
                offset = data.get("EXIF:OffsetTimeOriginal")
                if when and offset and len(str(when)) == 19:
                    when = f"{when}{offset}"
                entry.created_at = parse_time(when)
                lat, lon = _gps(data, "Latitude"), _gps(data, "Longitude")
                if lat is not None and lon is not None:
                    entry.provenance["geo"] = {"lat": lat, "lon": lon}
                if data.get("XMP:Credit") or data.get("IPTC:Credit"):
                    entry.provenance["credit"] = data.get("XMP:Credit") or data.get("IPTC:Credit")
                if data.get("XMP:Creator") or data.get("IPTC:By-line"):
                    creator = data.get("XMP:Creator") or data.get("IPTC:By-line")
                    entry.provenance["creator"] = ", ".join(map(str, _as_list(creator)))
            yield entry
