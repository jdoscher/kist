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
import json
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__, access
from .access import Action
from .config import get_settings
from .db import SessionLocal
from .lifecycle import PreviewStatus, TransitionError
from .schemas import ItemOut
from .services import ai as ai_svc
from .services import annotations as annotations_svc
from .services import boards as boards_svc
from .services import captions as captions_svc
from .services import collections as collections_svc
from .services import events as events_svc
from .services import faces as faces_svc
from .services import folders as folders_svc
from .services import ingest, library
from .services import items as items_svc
from .services import jobs as jobs_svc
from .services import links as links_svc
from .services import search as search_svc
from .services import smart_folders as smart_svc
from .services import stacks as stacks_svc
from .services import tags as tags_svc
from .services import templates as templates_svc

mcp = MCPServer(
    name="kist",
    instructions=(
        "Kist is a self-hosted visual asset library (images, video, PDFs, fonts). "
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
        "items can't be trashed. When the user describes what something looks "
        "like ('a red car on a beach', 'moody dark interiors'), pass that "
        "description as search_items(semantic=...): it ranks by what the images "
        "show, not by their names. similar_to finds more like an item, color "
        "finds a colour (item_palette shows an item's), text finds words inside "
        "screenshots and PDFs, and search_by_image finds items like an image "
        "that isn't in the library. Ranked results carry match {kind, score, "
        "snippet} saying why each matched. Before tagging, read get_vocabulary: it lists "
        "the preferred tags, the aliases Kist rewrites to them, implied tags "
        "(never add an implied tag yourself: searching it already finds the "
        "item) and namespaces such as brand:rolex. suggest_tags proposes tags "
        "for an item; nothing is applied until you call set_tags."
    ),
    version=__version__,
)

# service error -> what the agent is told (anything else is a real crash)
EXPECTED_ERRORS = (
    items_svc.NotFound,
    jobs_svc.NotFound,
    smart_svc.NotFound,
    events_svc.NotFound,
    stacks_svc.NotFound,  # Milestone 2 organise
    faces_svc.NotFound,
    collections_svc.NotFound,
    boards_svc.NotFound,  # Milestone 3
    boards_svc.Conflict,
    annotations_svc.NotFound,
    templates_svc.NotFound,
    folders_svc.FolderError,
    TransitionError,
    ValueError,  # validation.ValidationError, BadCursor, NotMediaError, ...
    access.Forbidden,
    ai_svc.AiUnavailable,  # the ml service isn't there (semantic, search_by_image)
    captions_svc.CaptionError,
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
    "'none') finds items by preview state. type is one of image|video|pdf|font|other. "
    "order is -created (default), created, filename, -size, -rating or -modified. "
    "Ranking (order is then ignored; combine freely with the filters): "
    "semantic='a describing phrase' ranks by what the images show (use it for "
    "any descriptive request); query plus semantic is a hybrid of words and "
    "meaning; similar_to=<item id> finds more like that item; color='#2f4f3a' "
    "(color_tol 0-100, default 30) keeps items with that colour in their "
    "palette; text='words' keeps items whose screenshot/PDF text has those "
    "words (snippet shows where); has_text=true keeps items with any text. "
    "query also matches text inside images and captions. type 'font' finds "
    "font files. "
    "A tag also finds items whose tags imply it (see get_vocabulary), and "
    "'brand:*' matches any tag in that namespace. collapse_stacks=true shows "
    "one item per stack (in a ranked search, the stack's best match). person "
    "is a person id from list_people. "
    "flag is pick, reject or none; label is a colour (red, orange, yellow, green, "
    "blue, purple) or none; both filter ranked searches too. query also matches "
    "annotation text. "
    "Pass the returned next_cursor to fetch the following page.",
    restricted_ok=True,
)
def search_items(
    query: str | None = None,
    semantic: str | None = None,
    similar_to: str | None = None,
    color: str | None = None,
    color_tol: int | None = None,
    text: str | None = None,
    has_text: bool | None = None,
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
    collapse_stacks: bool = False,
    person: str | None = None,
    flag: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    with SessionLocal() as session:
        result = search_svc.search(
            session,
            q=query, tags=tags, tags_any=tags_any, untagged=untagged,
            view=view, state=state, preview_status=preview_status,
            folder=folder, kind=type, order=order,
            rating_min=rating_min, limit=limit, cursor=cursor,
            semantic=semantic, similar=similar_to, color=color, color_tol=color_tol,
            text=text, has_text=has_text,
            stacks="collapse" if collapse_stacks else None, person=person,
            flag=flag, label=label,
        )
        return _page(result)


def _page(result) -> dict[str, Any]:
    return {
        "items": [item_out(i, result.matches.get(i.id)) for i in result.items],
        "next_cursor": result.next_cursor,
        "total": result.total,
    }


@tool(
    "Find items that look like an image that isn't in the library: a file "
    "path inside the import directories, or an image URL (a page link uses "
    "its preview image). Nothing is saved. Takes the same filters as "
    "search_items (query, tags, folder, type, color, ...). Results are ranked "
    "by visual similarity (match.kind 'similar').",
    restricted_ok=True,
)
def search_by_image(
    file_path: str | None = None,
    url: str | None = None,
    query: str | None = None,
    tags: list[str] | None = None,
    folder: str | None = None,
    type: str | None = None,
    rating_min: int | None = None,
    color: str | None = None,
    flag: str | None = None,
    label: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    from .services import ai

    if bool(url) == bool(file_path):
        raise ToolError("provide exactly one of file_path or url")
    if url:
        data = ingest.fetch_image(url)
    else:
        data = ingest.resolve_import_path(file_path).read_bytes()  # type: ignore[arg-type]
    vec = ai.ml_embed_image(data)
    with SessionLocal() as session:
        return _page(search_svc.search(
            session, image_vector=vec, q=query, tags=tags, folder=folder, kind=type,
            rating_min=rating_min, color=color, flag=flag, label=label,
            limit=max(1, min(limit, 100)), cursor=cursor,
        ))


@tool(
    "An item's colour palette: its dominant colours, largest share first, as "
    "{hex, pct, name} (name is the nearest everyday colour). Pass a hex to "
    "search_items(color=...) to find items in that colour.",
    restricted_ok=True,
)
def item_palette(id: str) -> list[dict[str, Any]]:
    from .services import colour

    with SessionLocal() as session:
        return colour.palette(items_svc.get_item(session, id).dominant_colors)


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
    "Update an item's rating (0-5), notes, filename, caption (a sentence "
    "describing the image; empty string removes it), or source_url (an "
    "http(s) URL; empty string clears it). folder_id ADDS the item to that "
    "folder and keeps its other folders; use set_folders to remove one. "
    "protected=true stops the item being trashed or deleted (only a write "
    "or admin token can unprotect). flag is pick or reject, label a colour (red, "
    "orange, yellow, green, blue, purple); 'none' clears either. "
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
    caption: str | None = None,
    flag: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if caption is not None:
        fields["caption"] = caption
    if flag is not None:
        fields["flag"] = flag
    if label is not None:
        fields["label"] = label
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


@tool(
    "Add and/or remove tags on an item. Added tags go through the vocabulary's "
    "aliases (an alias becomes its preferred tag). Returns the updated item.",
    needs=Action.EDIT,
)
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
    "preview_status, deleted=true -> state='trashed', stacks='collapse' -> "
    "collapse_stacks=true; the other fields (semantic, color, color_tol, "
    "text, has_text, person, flag, label, ...) have the same names."
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


@tool(
    "AI status: the embedding model, whether the ml service answers, how many "
    "items have an embedding and OCR/PDF text, AI jobs waiting, the backfill "
    "window, the caption provider and the Mac helpers seen."
)
def ai_status() -> dict[str, Any]:
    import json

    from .services import ai

    with SessionLocal() as session:
        return json.loads(json.dumps(ai.status(session), default=str))


# ---------- Milestone 2: organise ----------


@tool(
    "The tag vocabulary: aliases (variant -> preferred tag, e.g. Fashion -> "
    "Clothing; Kist rewrites them), implied tags (boots implies shoes: "
    "searching shoes finds boots, so don't add shoes yourself) and the "
    "namespaces in use (brand:, place: ...). Read it before tagging."
)
def get_vocabulary() -> dict[str, Any]:
    from .services import vocabulary

    with SessionLocal() as session:
        return vocabulary.get(session)


@tool(
    "Suggest tags for an item: [{tag, score, why}], why is 'similar items' "
    "(the tags of its look-alikes) or 'matches the image' (the picture matches "
    "the tag's name). Nothing is applied: call set_tags for the ones that fit. "
    "Empty until the item has an image embedding."
)
def suggest_tags(id: str, limit: int = 10) -> list[dict[str, Any]]:
    from .services import suggestions

    with SessionLocal() as session:
        return suggestions.suggest(session, id, max(1, min(limit, 50)))


@tool(
    "Tag cleanup suggestions: one_off (used once), clash (same name but for "
    "case or spacing), near (misspellings, plurals), unused (no recent use) and "
    "smart_folder (a smart folder names a missing tag or folder). Each has a "
    "proposal; propose a batch to the user, then merge_tags."
)
def tag_cleanup() -> list[dict[str, Any]]:
    with SessionLocal() as session:
        return [json_safe(s) for s in tags_svc.cleanup(session)]


@tool(
    "Merge tags: every item with a tag in from_tags gets `into` instead, as one "
    "batch (undo_batch reverts it). dry_run=true only counts the items. "
    "keep_alias=true leaves the old names as aliases of `into`.",
    needs=Action.EDIT,
)
def merge_tags(from_tags: list[str], into: str, dry_run: bool = False,
               keep_alias: bool = False) -> dict[str, Any]:
    with SessionLocal() as session:
        return tags_svc.merge(session, from_tags, into, dry_run=dry_run, keep_alias=keep_alias)


@tool(
    "Duplicate groups: kind 'same_image' (same picture, resized or re-encoded), "
    "'similar' (near-identical by meaning) or 'exact' (same bytes). Each group "
    "has items and a suggested_keep. Propose, then resolve_duplicates once the "
    "user agrees."
)
def duplicate_groups(kind: str = "same_image", limit: int = 20) -> list[dict[str, Any]]:
    from .services import duplicates

    with SessionLocal() as session:
        keys = ("id", "filename", "width", "height", "size_bytes", "tags", "rating",
                "source_url", "created_at", "thumb_url")
        return [
            {**g, "items": [i.model_dump(mode="json", include=set(keys)) for i in g["items"]]}
            for g in duplicates.groups(session, kind, max(1, min(limit, 100)))
        ]


@tool(
    "Resolve a duplicate group: keep one item, merge the others' tags, folders, "
    "notes, best rating and source URL into it, and move the others to the "
    "trash. One batch: undo_batch puts everything back.",
    needs=Action.EDIT,
)
def resolve_duplicates(keep: str, others: list[str]) -> dict[str, Any]:
    from .services import duplicates

    with SessionLocal() as session:
        out = duplicates.resolve(session, keep, others)
        return {**out, "keep": item_out(out["keep"])}


@tool(
    "Stacks worth making: RAW+JPEG pairs, bursts (same camera, within 2 s) and "
    "items linked version_of. Nothing is stacked until stack_items."
)
def suggest_stacks(limit: int = 50) -> list[dict[str, Any]]:
    from .services import stacks

    with SessionLocal() as session:
        return stacks.suggestions(session, max(1, min(limit, 500)))


@tool(
    "Stack items so the grid shows them as one (the cover). unstack=true with "
    "stack_id dissolves a stack instead.",
    needs=Action.EDIT,
)
def stack_items(item_ids: list[str] | None = None, cover: str | None = None,
                stack_id: str | None = None, unstack: bool = False) -> dict[str, Any]:
    from .services import stacks

    with SessionLocal() as session:
        if unstack:
            if not stack_id:
                raise ToolError("pass stack_id to unstack")
            return {"unstacked": stacks.unstack(session, stack_id)}
        stack = stacks.create(session, item_ids or [], cover)
        out = stacks.stack_out(session, stack)
        return {**out, "items": [i.model_dump(mode="json")["id"] for i in out["items"]]}


@tool(
    "Suggested collections: clusters of look-alike items not yet in one folder "
    "({id, size, sample_item_ids, label_suggestion}). Offer them to the user; "
    "accepting one is done in the web UI or POST /api/collections/suggested/{id}/accept."
)
def suggested_collections() -> list[dict[str, Any]]:
    from .services import collections

    with SessionLocal() as session:
        data = collections.suggested(session)
        return [{k: c.get(k) for k in ("id", "size", "sample_item_ids", "label_suggestion",
                                       "common_tags", "cohesion")}
                for c in data["clusters"]]


@tool(
    "People found by face recognition (only when the owner turned faces on): "
    "id, name (null until someone names them), face and item counts. "
    "search_items(person=id) lists their pictures.",
    restricted_ok=True,
)
def list_people() -> list[dict[str, Any]]:
    from .services import faces

    with SessionLocal() as session:
        return faces.list_people(session)


def json_safe(value):
    return json.loads(json.dumps(value, default=str))


# ---------- Milestone 3: boards and annotations ----------



def board_out(session, board) -> dict[str, Any]:
    out = json.loads(json.dumps(boards_svc.out(session, board), default=str))
    out["web_url"] = f"{get_settings().public_url.rstrip('/')}/boards/{board.id}"
    return out


@tool(
    "List boards (moodboards: canvases of library items, notes and arrows), "
    "newest first: id, name, version, item_count. state='trashed' lists the "
    "boards trash.",
    restricted_ok=True,
)
def list_boards(state: str = "active", query: str | None = None) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        return [json.loads(json.dumps(boards_svc.summary(b), default=str))
                for b in boards_svc.list_boards(session, state=state, q=query)]


@tool(
    "Get one board's document: nodes (type item|note|text|shape|group|link|"
    "color, x, y, w, h, item_id for items), edges (arrows from -> to), canvas, "
    "version, and missing_item_ids (items that were deleted).",
    restricted_ok=True,
)
def get_board(id: str) -> dict[str, Any]:
    with SessionLocal() as session:
        return board_out(session, boards_svc.get_board(session, id))


@tool(
    "Create a board from items, laid out as a grid or masonry columns "
    "(layout='grid'|'masonry'). Use search_items first to pick the items. "
    "Returns the board (open it with its web_url).",
    needs=Action.EDIT,
)
def create_board(name: str, item_ids: list[str] | None = None,
                 layout: str = "grid") -> dict[str, Any]:
    with SessionLocal() as session:
        return board_out(session, boards_svc.create_board(
            session, name, item_ids=item_ids or [], layout=layout))


@tool(
    "Add items to a board, laid out below what is already there (grid or "
    "masonry), and/or notes: each note is a text placed under them. Returns the board.",
    needs=Action.EDIT,
)
def add_to_board(id: str, item_ids: list[str] | None = None, layout: str = "grid",
                 notes: list[str] | None = None) -> dict[str, Any]:
    if not item_ids and not notes:
        raise ToolError("pass item_ids and/or notes")
    with SessionLocal() as session:
        board = boards_svc.get_board(session, id)
        if item_ids:
            board = boards_svc.add_items(session, id, item_ids, layout)
        if notes:
            nodes = board.doc.get("nodes") or []
            top = max((n["y"] + n["h"] for n in nodes), default=0) + 40
            left = min((n["x"] for n in nodes), default=0)
            board = boards_svc.patch_board(session, id, {"add": [
                {"type": "note", "text": t, "x": left + i * 260, "y": top, "w": 240, "h": 160}
                for i, t in enumerate(notes)
            ]})
        return board_out(session, board)


@tool(
    "Pin a note to part of an item. box=[x, y, w, h] as fractions of the image "
    "(0-1, from the top left) marks an area; point=[x, y] a spot; time=[start, "
    "end] seconds marks a stretch of a video. With none of them the note is "
    "about the whole image. Annotation text is found by search_items(query=...). "
    "Returns the item's annotations.",
    needs=Action.EDIT,
)
def annotate(item_id: str, text: str, box: list[float] | None = None,
             point: list[float] | None = None,
             time: list[float] | None = None) -> list[dict[str, Any]]:
    data: dict[str, Any] = {"text": text}
    if box is not None:
        if len(box) != 4:
            raise ToolError("box is [x, y, w, h]")
        data.update(kind="box", x=box[0], y=box[1], w=box[2], h=box[3])
    elif point is not None:
        if len(point) != 2:
            raise ToolError("point is [x, y]")
        data.update(kind="point", x=point[0], y=point[1])
    elif time is None:
        data.update(kind="box", x=0.0, y=0.0, w=1.0, h=1.0)
    else:
        data["kind"] = "range"
    if time is not None:
        if not 1 <= len(time) <= 2:
            raise ToolError("time is [start] or [start, end] in seconds")
        data.update(t_start=time[0], t_end=time[-1])
    with SessionLocal() as session:
        annotations_svc.create(session, item_id, data)
        return [json.loads(json.dumps(annotations_svc.as_dict(a), default=str))
                for a in annotations_svc.list_for(session, item_id)]


@tool("List an item's annotations (notes pinned to areas, points or video times).",
      restricted_ok=True)
def list_annotations(item_id: str) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        return [json.loads(json.dumps(annotations_svc.as_dict(a), default=str))
                for a in annotations_svc.list_for(session, item_id)]


# Mounted by kist.app at /mcp; streamable_http_path="/" so the endpoint is
# exactly /mcp. host="0.0.0.0" disables the SDK's localhost-only DNS-rebinding
# allowlist (the app is reached over the LAN; our bearer middleware guards it).
mcp_http_app = mcp.streamable_http_app(
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
    host="0.0.0.0",
)
