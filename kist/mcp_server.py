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
from .services import projects as projects_svc  # F6
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
    projects_svc.NotFound,  # F6
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
    "'none') finds items by preview state. type is one of image|video|pdf|audio|"
    "model|font|design|bundle|other (design: PSD, AI, Sketch, Procreate ...; "
    "model: 3D; bundle: a zip kept whole). "
    "order is -created (default), created, filename, -size, -rating, -modified, "
    "-taken or taken (date taken, else date added). "
    "Ranking (order is then ignored; combine freely with the filters): "
    "semantic='a describing phrase' ranks by what the images show (use it for "
    "any descriptive request); query plus semantic is a hybrid of words and "
    "meaning; similar_to=<item id> finds more like that item; color='#2f4f3a' "
    "(color_tol 0-100, default 30) keeps items with that colour in their "
    "palette (or a colour book code: color='book:<book>/<code>'); text='words' "
    "keeps items whose screenshot/PDF text has those "
    "words (snippet shows where); has_text=true keeps items with any text. "
    "query also matches text inside images and captions. type 'font' finds "
    "font files. "
    "A tag also finds items whose tags imply it (see get_vocabulary), and "
    "'brand:*' matches any tag in that namespace. collapse_stacks=true shows "
    "one item per stack (in a ranked search, the stack's best match). person "
    "is a person id from list_people. "
    "flag is pick, reject or none; label is a colour (red, orange, yellow, green, "
    "blue, purple) or none; both filter ranked searches too. query also matches "
    "annotation text, the text of a saved page snapshot and the place name. "
    "Provenance: ai_generated true|false|unknown, license (exact, or 'none'), "
    "source_status ok|gone|moved|error|unchecked (the link-rot watch). "
    "Credits (F4): rights_status ref_only|comp|licensed|cleared|none|uncleared "
    "(comma list ok), needs_credit true (no credit or creator recorded), "
    "print_min / print_max a size at 300 dpi ('8in', '20cm', 'A4'). "
    "Photos and media: taken_after/taken_before (ISO dates), camera and lens "
    "(substrings, e.g. 'X100'), has_gps, place ('Kyoto'), vertical, "
    "has_audio, duration_min/duration_max (seconds). auto_kind is a kind from "
    "list_kinds (screenshots, drawings ...; '-screenshots' leaves them out, "
    "'drawings:unsure' lists the borderline calls to review). Every filter "
    "applies to every ranking too. project=<project id> keeps items filed in that "
    "project's folders; fields={'season': 'SS27', 'edition': '>=3'} filters by "
    "custom fields (see list_projects; '*' = any value, 'none' = no value, "
    "a..b ranges for numbers and dates, substrings for text). "
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
    ai_generated: bool | str | None = None,
    license: str | None = None,
    source_status: str | None = None,
    taken_after: str | None = None,
    taken_before: str | None = None,
    camera: str | None = None,
    lens: str | None = None,
    has_gps: bool | None = None,
    place: str | None = None,
    vertical: bool | None = None,
    has_audio: bool | None = None,
    duration_min: float | None = None,
    duration_max: float | None = None,
    rights_status: str | None = None,
    needs_credit: bool | None = None,
    print_min: str | None = None,
    print_max: str | None = None,
    auto_kind: str | None = None,
    project: str | None = None,
    fields: dict[str, str] | None = None,
) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    credits_spec = _credits_filters(rights_status=rights_status, needs_credit=needs_credit,
                                    print_min=print_min, print_max=print_max)  # F4
    extra = _m45_filters(
        ai_generated=ai_generated, license=license, source_status=source_status,
        taken_after=taken_after, taken_before=taken_before, camera=camera, lens=lens,
        has_gps=has_gps, place=place, vertical=vertical, has_audio=has_audio,
        duration_min=duration_min, duration_max=duration_max,
    )
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
            flag=flag, label=label, credits=credits_spec, auto_kind=auto_kind, **extra,
            project=project, custom_fields=fields,
        )
        return _page(result)


def _credits_filters(**kw) -> dict[str, Any] | None:
    """F4: search()'s credits= kwarg (services/credits.filters)."""
    return {k: v for k, v in kw.items() if v is not None and v != ""} or None


def _m45_filters(*, ai_generated=None, license=None, source_status=None, taken_after=None,
                 taken_before=None, **media) -> dict[str, Any]:
    """search() keyword arguments for the M4 provenance and M5 media
    filters (the same build_filters path GET /api/items uses)."""
    from datetime import datetime

    if isinstance(ai_generated, bool):
        ai_generated = "true" if ai_generated else "false"
    for key, value in (("taken_after", taken_after), ("taken_before", taken_before)):
        try:
            media[key] = datetime.fromisoformat(value) if value else None
        except ValueError:
            raise ToolError(f"{key} must be an ISO date like 2024-05-01") from None
    for key in ("duration_min", "duration_max"):
        if media.get(key) is not None and media[key] < 0:
            raise ToolError(f"{key} must be 0 or more seconds")
    return {"ai_generated": ai_generated, "license": license, "source_status": source_status,
            "media": media}


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
    "search_items (query, tags, folder, type, color, flag, label, person, "
    "collapse_stacks, the provenance and media filters ...). Results are "
    "ranked by visual similarity (match.kind 'similar').",
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
    person: str | None = None,
    collapse_stacks: bool = False,
    ai_generated: bool | str | None = None,
    license: str | None = None,
    source_status: str | None = None,
    taken_after: str | None = None,
    taken_before: str | None = None,
    camera: str | None = None,
    lens: str | None = None,
    has_gps: bool | None = None,
    place: str | None = None,
    vertical: bool | None = None,
    has_audio: bool | None = None,
    duration_min: float | None = None,
    duration_max: float | None = None,
    limit: int = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    from .services import ai

    extra = _m45_filters(
        ai_generated=ai_generated, license=license, source_status=source_status,
        taken_after=taken_after, taken_before=taken_before, camera=camera, lens=lens,
        has_gps=has_gps, place=place, vertical=vertical, has_audio=has_audio,
        duration_min=duration_min, duration_max=duration_max,
    )

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
            rating_min=rating_min, color=color, flag=flag, label=label, person=person,
            stacks="collapse" if collapse_stacks else None, **extra,
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


# ---------- F5: colour books ----------


@tool(
    "The nearest code in the user's own colour books (house colours, RAL, "
    "TCX lists they imported) to a colour. Give hex ('#2f4f3a') for one "
    "colour, or item_id for each colour of that item's palette. book limits "
    "it to one book (id or name). Each match is {book_id, book, code, name, "
    "hex, de}; de is the CIEDE2000 difference (under ~2 is hard to tell "
    "apart, over ~5 is clearly different). To find items in a code's "
    "colour, pass search_items(color='book:<book_id>/<code>').",
    restricted_ok=True,
)
def nearest_colour_code(
    hex: str | None = None, item_id: str | None = None, book: str | None = None
) -> list[dict[str, Any]]:
    from .services import colour, colourbooks

    if not hex and not item_id:
        raise ToolError("give hex or item_id")
    try:
        books = [colourbooks.get_book(book)] if book else None
    except colourbooks.NotFound as exc:
        raise ToolError(str(exc)) from None
    if item_id:
        with SessionLocal() as session:
            pal = colour.palette(items_svc.get_item(session, item_id).dominant_colors)
        return [{**p, "matches": colourbooks.nearest(p["hex"], books)} for p in pal]
    return [{"hex": colour.parse_hex(hex or ""), "matches": colourbooks.nearest(hex or "", books)}]


@tool(
    "The user's colour books: {id, name, enabled, format, count}. With "
    "query, the swatches whose code or name matches instead (each with "
    "ref, the value for search_items(color=...)).",
)
def list_colour_books(query: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    from .services import colourbooks

    if query:
        return colourbooks.lookup(query, max(1, min(limit, 100)))
    return [b.summary() for b in colourbooks.list_books()]


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


# ---------- M4: provenance, exports, page snapshots ----------


@tool(
    "Set where an item came from and what may be done with it: any of "
    "page_url, image_url, page_title, creator, credit, license, rights_url, "
    "ai_generated (true/false), digital_source_type. Only the fields you "
    "pass change; pass an empty string to clear one. Recorded in the item "
    "history (undoable). Returns the updated item (its provenance block).",
    needs=Action.EDIT,
)
def set_provenance(
    id: str,
    page_url: str | None = None,
    image_url: str | None = None,
    page_title: str | None = None,
    creator: str | None = None,
    credit: str | None = None,
    license: str | None = None,
    rights_url: str | None = None,
    ai_generated: bool | None = None,
    digital_source_type: str | None = None,
) -> dict[str, Any]:
    given = {k: v for k, v in {
        "page_url": page_url, "image_url": image_url, "page_title": page_title,
        "creator": creator, "credit": credit, "license": license, "rights_url": rights_url,
        "ai_generated": ai_generated, "digital_source_type": digital_source_type,
    }.items() if v is not None}
    if not given:
        raise ToolError("nothing to update")
    patch = {k: (None if v == "" else v) for k, v in given.items()}
    with SessionLocal() as session:
        return item_out(items_svc.update_item(session, id, fields={"provenance": patch}))


@tool(
    "Export a collection as a portable BagIt zip that opens without Kist "
    "(originals under readable names, .json and .xmp sidecars, items.csv, an "
    "offline index.html). target is folder, smart, items or all; target_id is "
    "the folder or smart folder id; item_ids for target=items. Runs as a job: "
    "poll export_status(id) until status is done, then hand the user its "
    "download_url (same token).",
    needs=Action.EDIT,
)
def export_collection(
    target: str,
    target_id: str | None = None,
    item_ids: list[str] | None = None,
    originals: bool = True,
    events: bool = False,
) -> dict[str, Any]:
    from .services import exports

    principal = access.current()
    with SessionLocal() as session:
        record = exports.create(
            session, target=target, target_id=target_id, item_ids=item_ids,
            include={"originals": originals, "sidecars": True, "events": events},
            created_by=principal.name if principal else "mcp",
        )
    return exports.out(record)


@tool("The status of an export_collection run (pending, running, done, failed).")
def export_status(id: str) -> dict[str, Any]:
    import json

    from .services import exports

    try:
        return json.loads(json.dumps(exports.out(exports.get(id)), default=str))
    except exports.NotFound as exc:
        raise ToolError(str(exc)) from None


@tool(
    "The readable text of the web page an item was saved from, if a page "
    "snapshot was kept (so you can summarise the article behind an image). "
    "Long pages are cut to max_chars.",
    restricted_ok=False,
)
def get_page_snapshot(id: str, max_chars: int = 20000) -> dict[str, Any]:
    from .services import snapshots

    with SessionLocal() as session:
        item = items_svc.get_item(session, id)
        text = snapshots.text_of(item.id)
        if text is None:
            raise ToolError(f"item {id} has no page snapshot")
        found = snapshots.read(item.id)
        info = found[1] if found else {}
        limit = max(100, min(int(max_chars), 200_000))
        return {"item_id": item.id, "url": info.get("url"), "title": info.get("title"),
                "captured_at": info.get("captured_at"), "text": text[:limit],
                "truncated": len(text) > limit}


# ---------- Milestone 5: video for agents ----------


@tool(
    "Watch a video (or animated GIF/WebP): returns one contact-sheet image of "
    "`frames` evenly spaced frames (default 9, at most 36) with each frame's "
    "time burned in, so you can see what happens and when. Use save_frame to "
    "keep a moment as its own item.",
    restricted_ok=True,
)
def view_video(item_id: str, frames: int = 9) -> Image:
    from .services import media

    with SessionLocal() as session:
        item = items_svc.get_item(session, item_id)
        return Image(data=media.contact_sheet(item, frames), format="png")


@tool(
    "Save the frame of a video at t seconds as a new PNG item (full "
    "resolution), linked derived_from the video and given its tags and "
    "folders. Returns the new item.",
    needs=Action.EDIT,
)
def save_frame(item_id: str, t: float) -> dict[str, Any]:
    from .services import media

    with SessionLocal() as session:
        return item_out(media.save_frame(session, item_id, t).item)


@tool(
    "Cut a clip from a video (format mp4, gif or webp) or audio item (mp3 or "
    "wav), start..end in seconds; max_side limits the size. Runs as a job: "
    "list_jobs shows it, and its result_item_id is the new item.",
    needs=Action.EDIT,
)
def make_clip(item_id: str, start: float, end: float, format: str = "mp4",
              max_side: int | None = None) -> dict[str, Any]:
    from .services import media

    with SessionLocal() as session:
        job = media.queue_clip(session, item_id, start, end, format, max_side)
        return jobs_svc.job_out(job).model_dump(mode="json")


# ---------- Milestone 6: date buckets (services/buckets.py) ----------


@tool(
    "How many items fall in each month (UTC), for the date scrubber or a "
    "quick 'when is this from' overview. sort is created, modified or taken "
    "(date taken, else added); order asc or desc. Takes search_items' "
    "filters (query, tags, folder, view='unfiled', type, flag, label, the "
    "provenance and media filters ...). Each bucket {key 'YYYY-MM', count, "
    "cursor}: pass cursor to search_items with order=<item_order> to start "
    "at that month's first item.",
    restricted_ok=True,
)
def item_buckets(
    sort: str = "created",
    order: str = "desc",
    query: str | None = None,
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
    collapse_stacks: bool = False,
    person: str | None = None,
    flag: str | None = None,
    label: str | None = None,
    ai_generated: bool | str | None = None,
    license: str | None = None,
    source_status: str | None = None,
    taken_after: str | None = None,
    taken_before: str | None = None,
    camera: str | None = None,
    lens: str | None = None,
    has_gps: bool | None = None,
    place: str | None = None,
    vertical: bool | None = None,
    has_audio: bool | None = None,
    duration_min: float | None = None,
    duration_max: float | None = None,
) -> dict[str, Any]:
    from .services import buckets

    extra = _m45_filters(
        ai_generated=ai_generated, license=license, source_status=source_status,
        taken_after=taken_after, taken_before=taken_before, camera=camera, lens=lens,
        has_gps=has_gps, place=place, vertical=vertical, has_audio=has_audio,
        duration_min=duration_min, duration_max=duration_max,
    )
    with SessionLocal() as session:
        filters = search_svc.filters_from_params(dict(
            q=query, text=text, has_text=has_text, tags=tags, tags_any=tags_any,
            untagged=untagged, view=view, state=state, preview_status=preview_status,
            folder=folder, kind=type, rating_min=rating_min,
            stacks="collapse" if collapse_stacks else None, person=person,
            flag=flag, label=label, **extra,
        ), session)
        return buckets.buckets(session, filters, sort=sort, order=order)


# ---------- F4: credits and rights, print check ----------


@tool(
    "Set credits and rights on one or many items in one undoable batch: any of "
    "creator, credit, license, rights_url, rights_status (ref_only, comp, "
    "licensed, cleared), credit_exempt (true = needs no credit, e.g. own "
    "work). Only the fields you pass change; an empty string clears one. "
    "Returns {updated, failed, batch_id}.",
    needs=Action.EDIT,
)
def set_credits(
    item_ids: list[str],
    creator: str | None = None,
    credit: str | None = None,
    license: str | None = None,
    rights_url: str | None = None,
    rights_status: str | None = None,
    credit_exempt: bool | None = None,
) -> dict[str, Any]:
    given = {k: v for k, v in {
        "creator": creator, "credit": credit, "license": license, "rights_url": rights_url,
        "rights_status": rights_status, "credit_exempt": credit_exempt,
    }.items() if v is not None}
    if not given or not item_ids:
        raise ToolError("pass item_ids and at least one field to set")
    if len(item_ids) > 2000:
        raise ToolError("at most 2000 items at once")
    from .services import provenance as provenance_svc

    patch = {k: (None if v == "" else v) for k, v in given.items()}
    provenance_svc.clean(patch, partial=True)
    ok, failed = 0, []
    with SessionLocal() as session:
        for item_id in dict.fromkeys(item_ids):
            try:
                items_svc.update_item(session, item_id, fields={"provenance": patch})
                ok += 1
            except items_svc.NotFound:
                session.rollback()
                failed.append({"id": item_id, "error": "not found"})
    return {"updated": ok, "failed": failed, "batch_id": events_svc.context().batch_id}


@tool(
    "Credits for one item: the credit line exports print, Present's caption, "
    "and what auto-fill would add from the item's own metadata and stored "
    "page snapshot (read-only; never fetches anything). With apply=true the "
    "suggestions fill the empty fields (an undoable edit).",
    restricted_ok=True,
)
def item_credits(item_id: str, apply: bool = False) -> dict[str, Any]:
    from .services import credits as credits_svc

    if apply:
        check_access(Action.EDIT, restricted_ok=False)
    with SessionLocal() as session:
        item = items_svc.get_item(session, item_id)
        out = {**credits_svc.item_credit(item), "text": credits_svc.output_text(item),
               "caption": credits_svc.present_caption(item),
               "suggestions": credits_svc.suggestions(item)}
        if apply:
            out["filled"] = credits_svc.autofill(session, item_id, keep_modified=False)
        return out


@tool(
    "Before sharing or exporting: which items aren't licensed or cleared. "
    "target is items (with item_ids), folder, smart, board (with target_id) "
    "or all. Returns {total, uncleared, by_status, items[...]}; tell the user, "
    "it never blocks anything.",
)
def credits_clearance(target: str = "items", target_id: str | None = None,
                      item_ids: list[str] | None = None) -> dict[str, Any]:
    from .services import credits as credits_svc

    with SessionLocal() as session:
        return credits_svc.clearance_for_target(session, target, target_id, item_ids)


@tool(
    "Print check for an image: print size at 300 and 150 dpi (inches and cm), "
    "the paper sizes it fills, the dpi written in the file, the colour "
    "profile (ICC) name and colour space, and low_res (under 5 in at 300 dpi).",
    restricted_ok=True,
)
def print_check(item_id: str) -> dict[str, Any]:
    from .services import credits as credits_svc

    with SessionLocal() as session:
        return credits_svc.print_info(items_svc.get_item(session, item_id))


# ---------- F1 utility collections (services/kinds.py) ----------


@tool(
    "Kinds: the utility collections Kist sorts items into by itself "
    "(Screenshots, Documents & pages, Drawings with Pencil & graphite and Ink "
    "& line art, Paintings, Photos from a camera, Low-res, and trade packs "
    "such as Fashion or Film & games when switched on). For each: id, name, "
    "enabled, count of items in it, how it's detected, strictness, and "
    "whether it has learned from corrections. List a kind's items with "
    "search_items(auto_kind=<id>).",
    restricted_ok=True,
)
def list_kinds() -> dict[str, Any]:
    from .services import kinds as kinds_svc

    with SessionLocal() as session:
        data = kinds_svc.overview(session)
    keep = ("id", "name", "parent", "enabled", "count", "confirmed", "rejected", "how",
            "strictness", "learned", "packs")
    return {"kinds": [{k: v for k, v in row.items() if k in keep} for row in data["kinds"]],
            "packs": data["packs"]}


@tool(
    "Correct a kind for one or more items: verdict 'belongs' (it belongs in "
    "this kind), 'not' (not this; it stays out for good) or 'clear' (back to "
    "Kist's automatic call). A confirmed call always beats the automatic one, "
    "and corrections teach that kind. One batch: undo_batch reverts it. "
    "Returns how many items changed.",
    needs=Action.EDIT,
)
def correct_kind(kind: str, ids: list[str], verdict: str) -> dict[str, Any]:
    from .services import kinds as kinds_svc

    values = {"belongs": True, "not": False, "clear": None}
    if verdict not in values:
        raise ToolError("verdict must be belongs, not or clear")
    with SessionLocal() as session:
        return kinds_svc.correct(session, ids, kind, values[verdict])


# ---------- F6: projects and custom fields (services/projects.py) ----------


@tool(
    "List projects (a job: name, client, job number, phase, status, notes) with "
    "how many folders, smart folders, boards and share links each groups, and "
    "their custom fields (id, name, key, type, choices). status is active "
    "(default), archived or all."
)
def list_projects(status: str = "active") -> list[dict[str, Any]]:
    with SessionLocal() as session:
        return projects_svc.list_projects(session, status)


@tool(
    "Get one project: its details, its members (folders, smart folders, boards, "
    "share links; missing=true when one was deleted), its custom fields, and "
    "its most recently changed items (items filed in its folders). Search a "
    "project's items with search_items(project=<id>)."
)
def get_project(id: str, recent: int = 12) -> dict[str, Any]:
    with SessionLocal() as session:
        out = projects_svc.get(session, id)
        result = search_svc.search(session, project=id, order="-modified",
                                   limit=max(0, min(recent, 50)) or 1)
        out["recent_items"] = [item_out(i) for i in result.items][:max(0, recent)]
        out["item_count"] = result.total
        return out


@tool(
    "Set custom field values on items. values maps a field id (from "
    "list_projects or get_project) to a value: text, a number, a date "
    "(YYYY-MM-DD), one of a choice field's choices, or an http(s) link; null "
    "or '' clears it. A field applies only to items filed in its project's "
    "folders: other items are skipped and listed. One batch (undo_batch "
    "reverts it).",
    needs=Action.EDIT,
)
def set_field_values(item_ids: list[str], values: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as session:
        out = projects_svc.bulk_set(session, item_ids, values)
        items = []
        for item_id in dict.fromkeys(item_ids):
            try:
                items.append(item_out(items_svc.get_item(session, item_id)))
            except items_svc.NotFound:
                continue
        out["items"] = items
        return out


# ---------- F3: guest proofing on share links ----------


@tool(
    "Guest feedback from share links that let guests pick and comment: "
    "marks (verdict pick/reject, heart) and comments, newest first. Filter by "
    "share_id, round, item_id; unseen=true lists only what the owner hasn't "
    "looked at. Each row has the item, the guest's display name, the round "
    "and whether it was accepted. Guests never change the library; use "
    "accept_guest_feedback to turn feedback into picks or annotations.",
    needs=Action.EDIT,
)
def guest_feedback(share_id: str | None = None, round: int | None = None,
                   item_id: str | None = None, unseen: bool = False,
                   limit: int = 50) -> list[dict[str, Any]]:
    from .services import proofing as proofing_svc

    with SessionLocal() as session:
        rows = proofing_svc.feedback_rows(session, share_id=share_id, round=round,
                                          item_id=item_id, unseen=unseen, limit=limit)
        return json.loads(json.dumps(rows, default=str))


@tool(
    "Accept guest feedback (ids from guest_feedback): marks become the item's "
    "pick/reject flag (as_='pick'|'reject'|'label:<colour>', default what the "
    "guest chose; a heart alone becomes a pick), comments become annotations "
    "on the whole image by 'Guest: <name> via <share>'. One undoable batch; "
    "returns its batch_id, the accepted ids and the skipped ones with why.",
    needs=Action.EDIT,
)
def accept_guest_feedback(mark_ids: list[str] | None = None,
                          comment_ids: list[str] | None = None,
                          as_: str | None = None) -> dict[str, Any]:
    from .services import proofing as proofing_svc

    with SessionLocal() as session:
        return proofing_svc.accept(session, mark_ids=mark_ids, comment_ids=comment_ids,
                                   as_=as_)


# ---------- 2.0 F2: Present (services/present.py) ----------


def _show_out(show: dict) -> dict[str, Any]:
    return {**show, "web_url": f"/?present={show['id']}"}


@tool(
    "List saved Present shows (full-screen slideshows): id, name, source "
    "(folder|smart|board|items), options and compare pairs. Open one in the "
    "web app at its web_url.",
    restricted_ok=True,
)
def list_shows() -> list[dict[str, Any]]:
    from .services import present as present_svc

    with SessionLocal() as session:
        return [_show_out(s) for s in present_svc.list_shows(session)]


@tool(
    "Save a Present show (a full-screen slideshow for a crit or client review) "
    "of a folder, smart folder or board (source_kind folder|smart|board with "
    "source_id; a board plays in board order) or of chosen items in order "
    "(source_kind items with item_ids). compare is a list of [a, b] item id "
    "pairs shown side by side; pairs='stacks' or 'versions' pairs every item "
    "with its stack partner or previous version. Options: surround grey|black, "
    "captions (title + credit), filenames (off by default), ken_burns (off by "
    "default; never moves drawings or documents), advance_seconds (0 = off), "
    "gesture_seconds 30|60|120|300 for a gesture-drawing timer. Returns the "
    "show with web_url and slide_count.",
    needs=Action.EDIT,
)
def create_show(name: str, source_kind: str, source_id: str | None = None,
                item_ids: list[str] | None = None, compare: list[list[str]] | None = None,
                pairs: str = "none", surround: str = "grey", captions: bool = True,
                filenames: bool = False, ken_burns: bool = False, advance_seconds: int = 0,
                gesture_seconds: int | None = None) -> dict[str, Any]:
    from .services import present as present_svc

    if source_kind == "search":
        raise ToolError("use source_kind items with the item_ids search_items found")
    source: dict[str, Any] = {"kind": source_kind}
    if source_id:
        source["id"] = source_id
    if item_ids:
        source["item_ids"] = item_ids
    with SessionLocal() as session:
        show = present_svc.create_show(session, name=name, source=source, compare=compare,
                                       options={"pairs": pairs, "surround": surround,
                                                "captions": captions, "filenames": filenames,
                                                "ken_burns": ken_burns,
                                                "advance_seconds": advance_seconds,
                                                "gesture_seconds": gesture_seconds})
        try:
            slides = present_svc.show_slides(session, show["id"])
        except present_svc.NotFound as exc:
            raise ToolError(str(exc)) from None
        return {**_show_out(show), "slide_count": len(slides["slides"])}


# Mounted by kist.app at /mcp; streamable_http_path="/" so the endpoint is
# exactly /mcp. host="0.0.0.0" disables the SDK's localhost-only DNS-rebinding
# allowlist (the app is reached over the LAN; our bearer middleware guards it).
mcp_http_app = mcp.streamable_http_app(
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
    host="0.0.0.0",
)
