"""Per-item meta.json snapshots.

Postgres is the query index; meta.json is the durable, denormalized copy
written on every change so the whole library can be rebuilt from disk alone
(`kist rebuild-index`).
"""

import json
from pathlib import Path

from ..config import get_settings
from ..models import Item

META_VERSION = 2


def item_dir(item_id: str) -> Path:
    return get_settings().items_dir / item_id


def to_meta(item: Item) -> dict:
    return {
        "version": META_VERSION,
        "id": item.id,
        "filename": item.filename,
        "ext": item.ext,
        "mime": item.mime,
        "size_bytes": item.size_bytes,
        "width": item.width,
        "height": item.height,
        "duration_s": item.duration_s,
        "sha256": item.sha256,
        "phash": item.phash,
        "dominant_colors": item.dominant_colors,
        "folder_ids": sorted(f.id for f in item.folders),
        "folder_paths": [folder_path(f) for f in item.folders],
        "rating": item.rating,
        "notes": item.notes,
        "source_url": item.source_url,
        "tags": sorted(t.name for t in item.tags),
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "modified_at": item.modified_at.isoformat() if item.modified_at else None,
        "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None,
    }


def folder_path(folder) -> list[str]:
    names: list[str] = []
    seen = set()
    while folder is not None and folder.id not in seen:
        seen.add(folder.id)
        names.append(folder.name)
        folder = folder.parent
    return list(reversed(names))


def write_meta(item: Item) -> None:
    d = item_dir(item.id)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / ".meta.json.tmp"
    tmp.write_text(json.dumps(to_meta(item), indent=2, ensure_ascii=False))
    tmp.replace(d / "meta.json")


def write_folders_snapshot(
    folders: list[dict], smart_folders: list[dict] | None = None
) -> None:
    """library/folders.json — lets rebuild-index restore folders + smart folders."""
    lib = get_settings().library_dir
    lib.mkdir(parents=True, exist_ok=True)
    tmp = lib / ".folders.json.tmp"
    tmp.write_text(
        json.dumps(
            {
                "version": META_VERSION,
                "folders": folders,
                "smart_folders": smart_folders or [],
            },
            indent=2,
            default=str,
        )
    )
    tmp.replace(lib / "folders.json")
