"""MCP interface: streamable-HTTP endpoint at /mcp, same bearer token.

Thin wrappers over the service layer: no database queries and no rules live
here. Design rules: results stay small (metadata, not bytes — except
view_item, which returns MCP image content); destructive ops stop at the
trash (no hard-delete tool); every write tool returns the updated item, in
the one shape schemas.ItemOut.for_agent builds.

Errors: every tool runs through `tool()`, which turns the services' errors
into ToolError with a readable sentence, so the agent sees why a call
failed (a bare ValueError reaches it as just "Error executing tool").
"""

import functools
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__, access
from .access import Action
from .config import get_settings
from .db import SessionLocal
from .lifecycle import PreviewStatus, TransitionError
from .schemas import ItemOut
from .services import events as events_svc
from .services import folders as folders_svc
from .services import ingest, library
from .services import items as items_svc
from .services import jobs as jobs_svc
from .services import links as links_svc
from .services import search as search_svc
from .services import smart_folders as smart_svc
from .services import tags as tags_svc

mcp = MCPServer(
    name="kist",
    instructions=(
        "Kist is a self-hosted visual asset library (images, video, PDFs). "
        "Use search_items to find assets, view_item to actually see one, "
        "set_tags/set_folders/update_item to organize. Every item has a state (active or "
        "trashed) and a preview_status (ready, pending, failed or none). "
        "search_items(view='unfiled') is the user's inbox, the same list the "
        "web UI shows as Unfiled. Deleting only moves items to the trash; "
        "restore_item brings them back. Each item carries a web_url you can "
        "give the user to open it in Kist. list_jobs shows inbox imports and "
        "preview retries that failed. Read folder descriptions (list_folders) "
        "before filing. Every tool call is recorded as one batch in the item "
        "history: recent_batches lists them and undo_batch reverts one, so "
        "offer to undo when the user says you got something wrong. Protected "
        "items can't be trashed."
    ),
    version=__version__,
)

# service error -> what the agent is told (anything else is a real crash)
EXPECTED_ERRORS = (
    items_svc.NotFound,
    jobs_svc.NotFound,
    smart_svc.NotFound,
    events_svc.NotFound,
    folders_svc.FolderError,
    TransitionError,
    ValueError,  # validation.ValidationError, BadCursor, NotMediaError, ...
    access.Forbidden,
    ToolError,
)


def as_tool_error(exc: BaseException) -> ToolError:
    described = ingest.describe_download_error(exc) if isinstance(exc, Exception) else None
    return ToolError(described[1] if described else str(exc) or type(exc).__name__)


def check_access(needs: Action, restricted_ok: bool) -> None:
    """The token's scope must cover the tool. A folder-limited read token
    may only use the tools that filter items to its folders."""
    p = access.current()
    if p is None:  # BearerTokenMiddleware always sets one: fail closed
        raise ToolError(access.FORBIDDEN_MESSAGE)
    if not p.can(needs) or (p.restricted and not restricted_ok):
        raise ToolError(access.FORBIDDEN_MESSAGE)


def tool(description: str, needs: Action = Action.READ, restricted_ok: bool = False):
    """@mcp.tool with Kist's scope check and error mapping. `needs` is the
    action the token must be allowed (access.Action)."""

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            check_access(needs, restricted_ok)
            principal = access.current()
            try:
                # each tool call is one batch in the item history
                with events_svc.acting(principal.name, "mcp"):  # type: ignore[union-attr]
                    return fn(*args, **kwargs)
            except EXPECTED_ERRORS as exc:
                raise as_tool_error(exc) from None
            except Exception as exc:
                described = ingest.describe_download_error(exc)
                if described is None:
                    raise
                raise ToolError(described[1]) from None

        return mcp.tool(description=description)(wrapper)

    return decorate


item_out = ItemOut.for_agent


@tool(
    "Search the asset library. Returns item metadata plus thumb/file URL "
    "paths (same bearer token as this endpoint). Filters combine with AND; "
    "tags must all match, tags_any matches items carrying at least one, "
    "untagged=true matches items with no tags. view='unfiled' is the inbox: "
    "items in no folder and with no tags (what the web UI calls Unfiled). "
    "folder is a folder id (its subfolders are included) or 'none' for items "
    "in no folder, tagged or not. state is 'active' (default) or 'trashed' "
    "to search the trash. preview_status ('failed', 'pending', 'ready', "
    "'none') finds items by preview state. type is one of image|video|pdf|other. "
    "order is -created (default), created, filename, -size, -rating or -modified. "
    "Pass the returned next_cursor to fetch the following page.",
    restricted_ok=True,
)
def search_items(
    query: str | None = None,
    tags: list[str] | None = None,
    tags_any: list[str] | None = None,
    untagged: bool = False,
    view: str | None = None,
    state: str | None = None,
    preview_status: str | None = None,
    folder: str | None = None,
    type: str | None = None,
    rating_min: int | None = None,
    order: str = "-created",
    limit: int = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    with SessionLocal() as session:
        rows, next_cursor, total = search_svc.search_items(
            session,
            q=query, tags=tags, tags_any=tags_any, untagged=untagged,
            view=view, state=state, preview_status=preview_status,
            folder=folder, kind=type, order=order,
            rating_min=rating_min, limit=limit, cursor=cursor,
        )
        return {"items": [item_out(i) for i in rows], "next_cursor": next_cursor, "total": total}


@tool("Get full metadata for one item by id.", restricted_ok=True)
def get_item(id: str) -> dict[str, Any]:
    with SessionLocal() as session:
        return item_out(items_svc.get_item(session, id))


NO_PREVIEW = {
    PreviewStatus.NONE: "Kist can't make previews for this file type",
    PreviewStatus.PENDING: "its preview is still being made; try again shortly",
}


@tool(
    "View an item: returns the actual image content (thumbnail) so you "
    "can see the asset. size is 256, 512, or 1024 (default). Works for images, "
    "video poster frames, and PDF first pages.",
    restricted_ok=True,
)
def view_item(id: str, size: int = 1024) -> Image:
    size = 1024 if size > 512 else 512 if size > 256 else 256
    with SessionLocal() as session:
        item = items_svc.get_item(session, id)
        path = get_settings().items_dir / item.id / f"thumb_{size}.webp"
        if not path.is_file():
            if item.preview_status == PreviewStatus.FAILED:
                reason = (
                    f"its preview failed ({item.preview_error or 'unknown error'}); "
                    "retry_preview queues another attempt"
                )
            else:
                reason = NO_PREVIEW.get(item.preview_status, "no thumbnail on disk")
            raise ToolError(f"item {id} ({item.mime}) has no preview: {reason}")
        return Image(path=path)


@tool(
    "Add an asset from a URL, or from a file path inside the configured "
    "import directories (the library inbox/import folders). Provide "
    "exactly one of url or file_path. Returns the item; created=false "
    "means the exact same file already existed (the folder, tags and notes "
    "you passed are still added to it), and if that item's state is "
    "'trashed' it is in the trash (restore_item brings it back).",
    needs=Action.ADD,
)
def add_item(
    url: str | None = None,
    file_path: str | None = None,
    folder_id: str | None = None,
    tags: list[str] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    if bool(url) == bool(file_path):
        raise ToolError("provide exactly one of url or file_path")
    with SessionLocal() as session:
        if url:
            result = ingest.ingest_url(
                session, url, folder_id=folder_id, tag_names=tags, notes=notes
            )
        else:
            path = ingest.resolve_import_path(file_path)  # type: ignore[arg-type]
            result = ingest.ingest_file(
                session, path, path.name, folder_id=folder_id, tag_names=tags, notes=notes,
            )
        return {"item": item_out(result.item), "created": result.created}


@tool(
    "Update an item's rating (0-5), notes, filename, or source_url (an "
    "http(s) URL; empty string clears it). folder_id ADDS the item to that "
    "folder and keeps its other folders; use set_folders to remove one. "
    "protected=true stops the item being trashed or deleted (only a write "
    "or admin token can unprotect). "
    "Only the fields you pass change. Returns the updated item.",
    needs=Action.EDIT,
)
def update_item(
    id: str,
    rating: int | None = None,
    notes: str | None = None,
    filename: str | None = None,
    folder_id: str | None = None,
    source_url: str | None = None,
    protected: bool | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if protected is not None:
        fields["protected"] = protected
    if rating is not None:
        fields["rating"] = rating
    if notes is not None:
        fields["notes"] = notes
    if filename is not None:
        fields["filename"] = filename
    if source_url is not None:
        fields["source_url"] = source_url
    if folder_id == "":
        raise ToolError("folder_id must be a folder id; use set_folders(remove=...) to unfile")
    if not fields and folder_id is None:
        raise ToolError("nothing to update")
    with SessionLocal() as session:
        if folder_id is not None:
            items_svc.set_folders(session, id, add=[folder_id])
        item = items_svc.update_item(session, id, fields=fields) if fields else (
            items_svc.get_item(session, id)
        )
        return item_out(item)


@tool("Add and/or remove tags on an item. Returns the updated item.", needs=Action.EDIT)
def set_tags(
    id: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> dict[str, Any]:
    if not add and not remove:
        raise ToolError("pass add and/or remove")
    with SessionLocal() as session:
        return item_out(items_svc.set_tags(session, id, add=add, remove=remove))


@tool(
    "Add and/or remove an item's folder memberships (items can be in "
    "several folders at once). Returns the updated item.",
    needs=Action.EDIT,
)
def set_folders(
    id: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> dict[str, Any]:
    if not add and not remove:
        raise ToolError("pass add and/or remove (folder ids)")
    with SessionLocal() as session:
        return item_out(items_svc.set_folders(session, id, add=add, remove=remove))


@tool(
    "List smart folders (saved searches). To run one, pass its query to "
    "search_items: q -> query, rating -> rating_min, preview -> "
    "preview_status, deleted=true -> state='trashed'; the other fields have "
    "the same names."
)
def list_smart_folders() -> list[dict[str, Any]]:
    with SessionLocal() as session:
        return [
            smart_svc.out(s).model_dump(mode="json")
            for s in smart_svc.list_smart_folders(session)
        ]


@tool(
    "Move an item to the trash (state -> trashed). Restorable with "
    "restore_item. Trashing an item already in the trash changes nothing.",
    needs=Action.EDIT,
)
def trash_item(id: str) -> dict[str, Any]:
    with SessionLocal() as session:
        return item_out(items_svc.soft_delete(session, id))


@tool(
    "Take an item out of the trash (state -> active). Restoring an active "
    "item changes nothing. Find trashed items with search_items(state='trashed').",
    needs=Action.EDIT,
)
def restore_item(id: str) -> dict[str, Any]:
    with SessionLocal() as session:
        return item_out(items_svc.restore(session, id))


@tool(
    "Queue a new attempt at an item's thumbnails (preview_status -> pending). "
    "Use it for items whose preview_status is 'failed'. Returns the item.",
    needs=Action.EDIT,
)
def retry_preview(id: str) -> dict[str, Any]:
    with SessionLocal() as session:
        return item_out(jobs_svc.retry_preview(session, id))


@tool(
    "List background jobs, newest first: inbox imports (kind 'ingest') and "
    "preview retries ('regen_preview'). status filters to pending, running, "
    "done or failed; failed jobs say why in last_error."
)
def list_jobs(status: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        return [
            jobs_svc.job_out(j).model_dump(mode="json")
            for j in jobs_svc.list_jobs(session, status, max(1, min(limit, 100)))
        ]


@tool(
    "Run a failed job again with fresh attempts (status failed -> pending). "
    "A failed inbox file is moved back from the library's failed folder.",
    needs=Action.EDIT,
)
def retry_job(id: int) -> dict[str, Any]:
    with SessionLocal() as session:
        return jobs_svc.job_out(jobs_svc.retry(session, id)).model_dump(mode="json")


@tool(
    "List all folders (id, name, parent_id, position, description, "
    "item_count). Read a folder's description before filing into it: it "
    "says what belongs there. item_count includes items filed in "
    "subfolders, matching search_items(folder=...).",
    restricted_ok=True,
)
def list_folders() -> list[dict[str, Any]]:
    with SessionLocal() as session:
        return folders_svc.list_folders(session)


@tool(
    "Create a folder, optionally under a parent folder id, with an optional "
    "description of what belongs in it.",
    needs=Action.EDIT,
)
def create_folder(
    name: str, parent_id: str | None = None, description: str = ""
) -> dict[str, Any]:
    with SessionLocal() as session:
        return folders_svc.folder_dict(
            folders_svc.create_folder(session, name, parent_id, description)
        )


@tool("List all tags with usage counts.")
def list_tags() -> list[dict[str, Any]]:
    with SessionLocal() as session:
        return tags_svc.list_with_counts(session)


@tool(
    "Delete every tag that no item uses (tags on trashed items are kept). "
    "Returns the deleted tag names.",
    needs=Action.EDIT,
)
def prune_unused_tags() -> dict[str, Any]:
    with SessionLocal() as session:
        return {"deleted": tags_svc.prune_unused(session)}


@tool(
    "Find visually similar items by perceptual-hash distance "
    "(0 = identical-looking, 6 is a good default threshold, 20 the most). "
    "limit is at most 500."
)
def find_duplicates(threshold: int = 6, limit: int = 50) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        return [
            {"a": item_out(a), "b": item_out(b), "distance": d}
            for a, b, d in library.near_duplicates(session, threshold, limit)
        ]


@tool(
    "Library statistics: item counts, total size, by-type breakdown, tag and "
    "folder counts, failed jobs and failed previews."
)
def library_stats() -> dict[str, Any]:
    with SessionLocal() as session:
        return library.stats(session).model_dump(mode="json")


@tool(
    "Link two items: kind is derived_from (a crop or edit of the other), "
    "alternate, related, not_duplicate (they look alike but aren't the same; "
    "find_duplicates stops pairing them) or version_of. The link points from "
    "id to dst_id. remove=true removes it instead. Returns the item's links.",
    needs=Action.EDIT,
)
def link_items(id: str, dst_id: str, kind: str, remove: bool = False) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        if remove:
            links_svc.unlink(session, id, dst_id, kind)
        else:
            links_svc.link(session, id, dst_id, kind)
        return [
            {"item": item_out(other), "kind": k, "direction": d}
            for other, k, d in links_svc.links_of(session, id)
        ]


@tool(
    "Recent batches of changes, newest first. A batch is what one tool call, "
    "web request or command changed: batch_id, actor (the token's name), "
    "source (mcp, web, api, cli, worker, import), at, count and kinds. Pass "
    "actor to see only one token's batches.",
)
def recent_batches(limit: int = 20, actor: str | None = None) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        return [
            {**b, "at": b["at"].isoformat()}
            for b in events_svc.recent_batches(session, max(1, min(limit, 100)), actor)
        ]


@tool(
    "Undo a batch of changes (see recent_batches): every change in it is "
    "reverted, newest first, as a new batch. Changes something later touched "
    "again are skipped and listed with the reason. Returns undone, skipped "
    "and the new batch_id (undo that to redo).",
    needs=Action.EDIT,
)
def undo_batch(batch_id: str) -> dict[str, Any]:
    with SessionLocal() as session:
        return events_svc.undo_batch(session, batch_id)


# Mounted by kist.app at /mcp; streamable_http_path="/" so the endpoint is
# exactly /mcp. host="0.0.0.0" disables the SDK's localhost-only DNS-rebinding
# allowlist (the app is reached over the LAN; our bearer middleware guards it).
mcp_http_app = mcp.streamable_http_app(
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
    host="0.0.0.0",
)
