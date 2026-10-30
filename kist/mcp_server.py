"""MCP interface: streamable-HTTP endpoint at /mcp, same bearer token.

Thin wrappers over the service layer. Design rules (SPEC.md): results stay
small (metadata, not bytes — except view_item, which returns MCP image
content); destructive ops stop at the trash (no hard-delete tool); every
write tool returns the updated item.
"""

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Image, MCPServer

from . import __version__
from .config import get_settings
from .db import SessionLocal
from .models import Item
from .schemas import ItemOut
from .services import folders as folders_svc
from .services import ingest
from .services import items as items_svc
from .services import search as search_svc
from .services import tags as tags_svc

mcp = MCPServer(
    name="kist",
    instructions=(
        "Kist is a self-hosted visual asset library (images, video, PDFs). "
        "Use search_items to find assets, view_item to actually see one, "
        "set_tags/update_item to organize. Deleting only moves items to the "
        "trash, which the user can restore from in the web UI."
    ),
    version=__version__,
)


def _item_out(item: Item) -> dict[str, Any]:
    out = ItemOut.from_item(item).model_dump(mode="json")
    # keep tool results small; fetch internals via the REST API if needed
    out.pop("phash", None)
    out.pop("dominant_colors", None)
    out["file_url"] = f"/api/items/{item.id}/file"
    return out


@mcp.tool(
    description=(
        "Search the asset library. Returns item metadata plus thumb/file URL "
        "paths (same bearer token as this endpoint). Filters combine with AND; "
        "tags must all match, tags_any matches items carrying at least one, "
        "untagged=true matches items with no tags. type is one of "
        "image|video|pdf|other. Pass the returned next_cursor to fetch the "
        "following page."
    )
)
def search_items(
    query: str | None = None,
    tags: list[str] | None = None,
    tags_any: list[str] | None = None,
    untagged: bool = False,
    folder: str | None = None,
    type: str | None = None,
    rating_min: int | None = None,
    limit: int = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    with SessionLocal() as session:
        rows, next_cursor, total = search_svc.search_items(
            session,
            q=query, tags=tags, tags_any=tags_any, untagged=untagged,
            folder=folder, kind=type,
            rating_min=rating_min, limit=limit, cursor=cursor,
        )
        return {
            "items": [_item_out(i) for i in rows],
            "next_cursor": next_cursor,
            "total": total,
        }


@mcp.tool(description="Get full metadata for one item by id.")
def get_item(id: str) -> dict[str, Any]:
    with SessionLocal() as session:
        item = items_svc.get_item(session, id)
        return ItemOut.from_item(item).model_dump(mode="json")


@mcp.tool(
    description=(
        "View an item: returns the actual image content (thumbnail) so you "
        "can see the asset. size is 256, 512, or 1024 (default). Works for images, "
        "video poster frames, and PDF first pages."
    )
)
def view_item(id: str, size: int = 1024) -> Image:
    size = 1024 if size > 512 else 512 if size > 256 else 256
    with SessionLocal() as session:
        item = items_svc.get_item(session, id)
        path = get_settings().items_dir / item.id / f"thumb_{size}.webp"
        if not path.is_file():
            raise ValueError(
                f"item {id} has no preview (kind={item.mime}); "
                "only items with thumbnails can be viewed"
            )
        return Image(path=path)


@mcp.tool(
    description=(
        "Add an asset from a URL, or from a file path inside the configured "
        "import directories (the library inbox/import folders). Provide "
        "exactly one of url or file_path. Returns the item; created=false "
        "means the exact same file already existed."
    )
)
def add_item(
    url: str | None = None,
    file_path: str | None = None,
    folder_id: str | None = None,
    tags: list[str] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    if bool(url) == bool(file_path):
        raise ValueError("provide exactly one of url or file_path")
    with SessionLocal() as session:
        if url:
            result = ingest.ingest_url(
                session, url, folder_id=folder_id, tag_names=tags, notes=notes
            )
        else:
            settings = get_settings()
            path = Path(file_path).resolve()  # type: ignore[arg-type]
            allowed = [d.resolve() for d in settings.import_dirs]
            if not any(path.is_relative_to(d) for d in allowed):
                raise ValueError(
                    "file_path must be inside an import directory: "
                    + ", ".join(str(d) for d in allowed)
                )
            if not path.is_file():
                raise ValueError(f"no file at {path}")
            result = ingest.ingest_file(
                session, path, path.name,
                folder_id=folder_id, tag_names=tags, notes=notes,
            )
        return {"item": _item_out(result.item), "created": result.created}


@mcp.tool(
    description=(
        "Update an item's rating (0-5), notes, filename, or source_url. "
        "folder_id replaces ALL folder memberships with that one folder "
        "(empty string removes it from every folder) — use set_folders to "
        "add/remove individual memberships. Only the fields you pass "
        "change. Returns the updated item."
    )
)
def update_item(
    id: str,
    rating: int | None = None,
    notes: str | None = None,
    filename: str | None = None,
    folder_id: str | None = None,
    source_url: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if rating is not None:
        fields["rating"] = rating
    if notes is not None:
        fields["notes"] = notes
    if filename is not None:
        fields["filename"] = filename
    if folder_id is not None:
        fields["folder_id"] = folder_id or None
    if source_url is not None:
        fields["source_url"] = source_url
    if not fields:
        raise ValueError("nothing to update")
    with SessionLocal() as session:
        item = items_svc.update_item(session, id, fields=fields)
        return _item_out(item)


@mcp.tool(description="Add and/or remove tags on an item. Returns the updated item.")
def set_tags(
    id: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> dict[str, Any]:
    if not add and not remove:
        raise ValueError("pass add and/or remove")
    with SessionLocal() as session:
        item = items_svc.set_tags(session, id, add=add, remove=remove)
        return _item_out(item)


@mcp.tool(
    description=(
        "Add and/or remove an item's folder memberships (items can be in "
        "several folders at once). Returns the updated item."
    )
)
def set_folders(
    id: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> dict[str, Any]:
    if not add and not remove:
        raise ValueError("pass add and/or remove (folder ids)")
    with SessionLocal() as session:
        item = items_svc.set_folders(session, id, add=add, remove=remove)
        return _item_out(item)


@mcp.tool(
    description=(
        "List smart folders (saved searches). Run one by passing its query "
        "fields to search_items."
    )
)
def list_smart_folders() -> list[dict[str, Any]]:
    import sqlalchemy as sa

    from .models import SmartFolder

    with SessionLocal() as session:
        rows = session.execute(
            sa.select(SmartFolder).order_by(SmartFolder.position, SmartFolder.name)
        ).scalars().all()
        return [
            {"id": s.id, "name": s.name, "query": s.query, "position": s.position}
            for s in rows
        ]


@mcp.tool(description="Move an item to the trash (soft delete; restorable in the UI).")
def trash_item(id: str) -> dict[str, Any]:
    with SessionLocal() as session:
        item = items_svc.soft_delete(session, id)
        return _item_out(item)


@mcp.tool(description="List all folders (id, name, parent_id, item_count).")
def list_folders() -> list[dict[str, Any]]:
    with SessionLocal() as session:
        return folders_svc.list_folders(session)


@mcp.tool(description="Create a folder, optionally under a parent folder id.")
def create_folder(name: str, parent_id: str | None = None) -> dict[str, Any]:
    with SessionLocal() as session:
        folder = folders_svc.create_folder(session, name, parent_id)
        return {
            "id": folder.id,
            "name": folder.name,
            "parent_id": folder.parent_id,
        }


@mcp.tool(description="List all tags with usage counts.")
def list_tags() -> list[dict[str, Any]]:
    with SessionLocal() as session:
        return tags_svc.list_with_counts(session)


@mcp.tool(
    description=(
        "Find visually similar items by perceptual-hash distance "
        "(0 = identical-looking, 6 is a good default threshold)."
    )
)
def find_duplicates(threshold: int = 6, limit: int = 50) -> list[dict[str, Any]]:
    import sqlalchemy as sa
    from sqlalchemy.dialects.postgresql import BIT
    from sqlalchemy.orm import aliased

    threshold = max(0, min(threshold, 20))
    with SessionLocal() as session:
        a, b = aliased(Item, name="a"), aliased(Item, name="b")
        distance = sa.func.bit_count(
            sa.cast(a.phash.op("#")(b.phash), BIT(64))
        ).label("distance")
        rows = session.execute(
            sa.select(a, b, distance)
            .where(
                a.id < b.id,
                a.phash.isnot(None), b.phash.isnot(None),
                a.deleted_at.is_(None), b.deleted_at.is_(None),
                distance <= threshold,
            )
            .order_by(distance)
            .limit(max(1, min(limit, 200)))
        ).all()
        return [
            {"a": _item_out(ra), "b": _item_out(rb), "distance": d}
            for ra, rb, d in rows
        ]


@mcp.tool(description="Library statistics: item counts, total size, by-type breakdown.")
def library_stats() -> dict[str, Any]:
    import sqlalchemy as sa

    from .models import Folder, Tag
    from .services.search import KIND_FILTERS

    with SessionLocal() as session:
        live = Item.deleted_at.is_(None)
        total, size = session.execute(
            sa.select(
                sa.func.count(), sa.func.coalesce(sa.func.sum(Item.size_bytes), 0)
            ).where(live)
        ).one()
        trashed = session.execute(
            sa.select(sa.func.count()).where(Item.deleted_at.isnot(None))
        ).scalar_one()
        by_kind = {
            kind: session.execute(
                sa.select(sa.func.count()).where(live, cond)
            ).scalar_one()
            for kind, cond in KIND_FILTERS.items()
        }
        return {
            "total_items": total,
            "trashed_items": trashed,
            "total_size_bytes": int(size),
            "by_kind": by_kind,
            "tag_count": session.execute(
                sa.select(sa.func.count()).select_from(Tag)
            ).scalar_one(),
            "folder_count": session.execute(
                sa.select(sa.func.count()).select_from(Folder)
            ).scalar_one(),
        }


# Mounted by kist.app at /mcp; streamable_http_path="/" so the endpoint is
# exactly /mcp. host="0.0.0.0" disables the SDK's localhost-only DNS-rebinding
# allowlist (the app is reached over the LAN; our bearer middleware guards it).
mcp_http_app = mcp.streamable_http_app(
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
    host="0.0.0.0",
)
