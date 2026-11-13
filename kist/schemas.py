from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from . import access
from .config import get_settings
from .filetypes import kind_for
from .lifecycle import PreviewStatus, item_state
from .models import Item
from .services import validation


class MatchOut(BaseModel):
    """Why an item is in a ranked (or keyword) search, and how strongly."""

    kind: Literal["keyword", "meaning", "text", "color", "similar", "hybrid"]
    score: float
    # the item's text around the hit, the words marked << >>
    snippet: str | None = None


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    ext: str
    mime: str
    kind: str
    size_bytes: int
    width: int | None
    height: int | None
    duration_s: float | None
    sha256: str
    phash: int | None
    dominant_colors: list[dict[str, Any]] | None
    # base64 ThumbHash placeholder (https://evanw.github.io/thumbhash/), or null
    thumbhash: str | None = None
    folder_ids: list[str]
    rating: int
    notes: str
    source_url: str | None
    tags: list[str]
    # a sentence describing the image (generated or edited; services/captions.py)
    caption: str | None = None
    # tags the vocabulary implies (boots -> shoes); virtual, never stored
    implied_tags: list[str] = []
    protected: bool = False  # can't be trashed or purged until unprotected
    flag: str | None = None  # pick | reject (Milestone 3)
    label: str | None = None  # red | orange | yellow | green | blue | purple
    state: str  # lifecycle.ItemState: active | trashed
    preview_status: str  # lifecycle.PreviewStatus: pending | ready | failed | none
    preview_error: str | None
    preview_rev: int  # changes when the preview files do (thumbnail URL ?v=)
    has_thumb: bool
    thumb_url: str | None
    created_at: datetime
    modified_at: datetime
    deleted_at: datetime | None
    # only in search results: why this item matched (ranked and keyword searches)
    match: MatchOut | None = None
    # the item's stack (services/stacks.py) and how many members you can see
    stack_id: str | None = None
    stack_count: int | None = None
    # M4: where it came from (services/provenance.py; page_url = source_url)
    provenance: dict[str, Any] = {}
    # M4 link-rot watch: ok | gone | moved | error, or null (never checked)
    source_status: str | None = None
    source_checked_at: datetime | None = None
    source_wayback_url: str | None = None
    # Milestone 5 (services/media.py): what Kist read from the file
    # (video/audio/exif/svg/model/font/bundle/design/raw blocks, scrub,
    # playable, cover_time ...), when it was taken and where
    media: dict[str, Any] | None = None
    taken_at: datetime | None = None
    place: str | None = None
    # F6: custom field values {field id: value} (services/projects.py)
    custom_fields: dict[str, Any] = {}

    @classmethod
    def from_item(cls, item: Item, match: dict | None = None) -> "ItemOut":
        from .services.stacks import count_for
        from .services.vocabulary import implied_for_item

        status = item.preview_status or PreviewStatus.PENDING
        # a pending retry keeps serving the old thumbnails until it finishes
        has_thumb = status == PreviewStatus.READY or (
            status == PreviewStatus.PENDING and item.width is not None
        )
        return cls(
            id=item.id,
            filename=item.filename,
            ext=item.ext,
            mime=item.mime,
            kind=kind_for(item.mime, item.ext),
            size_bytes=item.size_bytes,
            width=item.width,
            height=item.height,
            duration_s=item.duration_s,
            sha256=item.sha256,
            phash=item.phash,
            dominant_colors=item.dominant_colors,
            thumbhash=item.thumbhash or None,
            folder_ids=sorted(access.visible_folder_filter(f.id for f in item.folders)),
            rating=item.rating,
            notes=item.notes,
            source_url=item.source_url,
            tags=sorted(t.name for t in item.tags),
            caption=item.caption,
            implied_tags=implied_for_item(item),
            protected=bool(item.protected),
            flag=item.flag,
            label=item.label,
            state=item_state(item).value,
            preview_status=str(status),
            preview_error=item.preview_error,
            preview_rev=item.preview_rev or 0,
            has_thumb=has_thumb,
            thumb_url=(
                f"/api/items/{item.id}/thumb?v={item.preview_rev or 0}" if has_thumb else None
            ),
            created_at=item.created_at,
            modified_at=item.modified_at,
            deleted_at=item.deleted_at,
            match=MatchOut(**match) if match else None,
            stack_id=item.stack_id,
            stack_count=count_for(item),
            **_m4_fields(item),
            # without the media-backfill bookkeeping (M6: media.CHECKED_KEY)
            media={k: v for k, v in item.media.items() if k != "checked_rev"} or None
            if item.media else None,
            taken_at=item.taken_at,
            place=item.place,
            custom_fields=dict(item.custom_fields or {}),
        )

    @classmethod
    def for_agent(cls, item: Item, match: dict | None = None) -> dict[str, Any]:
        """What MCP tools return: the same item, minus the bulky internals
        (phash, dominant_colors), plus links an agent can use or hand on."""
        out = cls.from_item(item, match).model_dump(mode="json")
        out.pop("phash", None)
        out.pop("dominant_colors", None)
        if out.get("match") is None:
            out.pop("match", None)
        out["file_url"] = f"/api/items/{item.id}/file"
        out["web_url"] = web_url(item)
        return out


def _m4_fields(item: Item) -> dict[str, Any]:
    from .services.provenance import out as provenance_out

    return {
        "provenance": provenance_out(item),
        "source_status": getattr(item, "source_status", None),
        "source_checked_at": getattr(item, "source_checked_at", None),
        "source_wayback_url": getattr(item, "source_wayback_url", None),
    }


def web_url(item: Item) -> str:
    """Where the user opens this item in the web UI (KIST_PUBLIC_URL makes it
    absolute)."""
    base = get_settings().public_url.rstrip("/")
    view = "trash" if item.deleted_at is not None else "all"
    return f"{base}/{view}?item={item.id}"


def _check_source_url(value: str | None) -> str | None:
    try:
        return validation.source_url(value)
    except validation.ValidationError as exc:
        raise ValueError(str(exc)) from None


class ItemPage(BaseModel):
    items: list[ItemOut]
    next_cursor: str | None
    total: int | None = None


class ItemCreateResponse(BaseModel):
    item: ItemOut
    created: bool


class ItemPatch(BaseModel):
    filename: str | None = Field(default=None, max_length=validation.FILENAME_MAX)
    rating: int | None = Field(default=None, ge=0, le=5)
    notes: str | None = Field(default=None, max_length=validation.NOTES_MAX)
    # folder_id / folder_ids REPLACE every membership; PATCH .../folders
    # with {add, remove} changes one without touching the rest
    folder_id: str | None = None
    folder_ids: list[str] | None = None
    source_url: str | None = None
    created_at: datetime | None = None
    modified_at: datetime | None = None
    # protected items can't be trashed or purged; unprotecting needs the
    # write or admin scope
    protected: bool | None = None
    # the item's caption; "" removes it (recorded as a caption event)
    caption: str | None = Field(default=None, max_length=2000)
    # Milestone 3: null clears
    flag: Literal["pick", "reject"] | None = None
    label: Literal["red", "orange", "yellow", "green", "blue", "purple"] | None = None
    # M4: merged into the item's provenance; a field set to null is removed.
    # provenance.page_url is source_url
    provenance: dict[str, Any] | None = None
    # the modified_at the client last saw; a mismatch answers 409
    base_modified_at: datetime | None = None

    _source_url = field_validator("source_url")(_check_source_url)


class TagsPut(BaseModel):
    tags: list[str]


class FoldersPut(BaseModel):
    folder_ids: list[str]


class MembershipPatch(BaseModel):
    """Add and/or remove entries without sending the whole list, so two
    writers (a browser tab and an agent) never overwrite each other."""

    add: list[str] = []
    remove: list[str] = []


class UrlAdd(BaseModel):
    url: str
    folder_id: str | None = None
    folder_ids: list[str] = []
    tags: list[str] = []
    notes: str = Field(default="", max_length=validation.NOTES_MAX)
    source_url: str | None = None  # defaults to the download url itself
    rating: int = Field(default=0, ge=0, le=5)
    created_at: datetime | None = None
    modified_at: datetime | None = None
    deleted_at: datetime | None = None
    # M4: provenance fields the client knows (method paste/extension/...)
    provenance: dict[str, Any] | None = None
    # an iOS Shortcut's share sheet sends the page title and selected text:
    # title becomes provenance.page_title, text is added to the notes
    title: str | None = Field(default=None, max_length=2000)
    text: str | None = Field(default=None, max_length=validation.NOTES_MAX)

    _source_url = field_validator("source_url")(_check_source_url)


class FolderOut(BaseModel):
    id: str
    name: str
    parent_id: str | None
    position: int
    description: str = ""
    item_count: int = 0


Position = Field(default=None, ge=validation.POSITION_MIN, le=validation.POSITION_MAX)


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=validation.FOLDER_NAME_MAX)
    parent_id: str | None = None
    description: str = Field(default="", max_length=validation.FOLDER_DESCRIPTION_MAX)


class FolderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=validation.FOLDER_NAME_MAX)
    parent_id: str | None = None
    position: int | None = Position
    description: str | None = Field(default=None, max_length=validation.FOLDER_DESCRIPTION_MAX)


class TagOut(BaseModel):
    id: int
    name: str
    count: int
    namespace: str | None = None  # "brand" for brand:rolex
    value: str = ""  # "rolex" for brand:rolex; the name for a plain tag


OrderName = Literal[
    "-created", "created", "filename", "-size", "-rating", "-modified", "-taken", "taken",
    "modified",  # M6
]
KindName = Literal[
    "image", "video", "pdf", "audio", "model", "font", "design", "bundle", "other"
]
PreviewName = Literal["pending", "ready", "failed", "none"]


class SmartFolderQuery(BaseModel):
    """The saved search a smart folder stands for (the GET /api/items
    params; `preview` is preview_status and `deleted` the trash). Unknown
    type/order/view/preview values are refused (422)."""

    q: str = ""
    tags: list[str] = []
    tags_any: list[str] = []  # OR semantics
    untagged: bool = False
    view: Literal["unfiled"] | None = None  # no folder, no tags (see search.VIEWS)
    folder: str | None = None
    type: KindName | None = None
    rating: int = Field(default=0, ge=0, le=5)
    order: OrderName = "-created"
    preview: PreviewName | None = Field(
        default=None, validation_alias=AliasChoices("preview", "preview_status")
    )
    deleted: bool = False
    created_after: datetime | None = None
    created_before: datetime | None = None
    # M2 search: a smart folder can be "meaning", colour or text-in-image
    semantic: str | None = Field(default=None, max_length=1000)
    color: str | None = Field(default=None, pattern=r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")
    color_tol: int | None = Field(default=None, ge=0, le=100)
    text: str | None = Field(default=None, max_length=500)
    has_text: bool | None = None
    # M2 organise: one entry per stack, items showing a person
    stacks: Literal["collapse"] | None = None  # covers only
    person: str | None = None  # a person id (faces)
    # M3: "none" matches items without one
    flag: Literal["pick", "reject", "none"] | None = None
    label: Literal["red", "orange", "yellow", "green", "blue", "purple", "none"] | None = None
    # M4: provenance and the link-rot watch
    ai_generated: Literal["true", "false", "unknown"] | None = None
    license: str | None = Field(default=None, max_length=200)
    source_status: Literal["ok", "gone", "moved", "error", "unchecked"] | None = None
    # M5: media filters (services/media.filters)
    taken_after: datetime | None = None
    taken_before: datetime | None = None
    has_gps: bool | None = None
    camera: str | None = Field(default=None, max_length=200)
    lens: str | None = Field(default=None, max_length=200)
    duration_min: float | None = Field(default=None, ge=0)
    duration_max: float | None = Field(default=None, ge=0)
    vertical: bool | None = None
    has_audio: bool | None = None
    place: str | None = Field(default=None, max_length=200)
    date_after: datetime | None = None
    date_before: datetime | None = None
    bbox: str | None = Field(default=None, max_length=200)
    # F6 (services/projects.py): a project's items; {field key: expr}
    project: str | None = Field(default=None, max_length=64)
    custom_fields: dict[str, str] | None = None


class PaletteColour(BaseModel):
    hex: str
    pct: float
    name: str


class SearchPage(BaseModel):
    """POST /api/search/image: the same page shape as GET /api/items."""

    items: list[ItemOut]
    next_cursor: str | None
    total: int | None = None


class CaptionJob(BaseModel):
    job_id: int
    status: str
    provider: str | None
    warning: str | None = None


class SmartFolderOut(BaseModel):
    id: str
    name: str
    # what was saved, as saved: older rows may hold values the input model
    # refuses today, and listing them must still work
    query: dict[str, Any]
    position: int


class SmartFolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=validation.FOLDER_NAME_MAX)
    query: SmartFolderQuery = SmartFolderQuery()


class SmartFolderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=validation.FOLDER_NAME_MAX)
    query: SmartFolderQuery | None = None
    position: int | None = Position


class StatsOut(BaseModel):
    total_items: int
    trashed_items: int
    total_size_bytes: int
    by_kind: dict[str, int]
    tag_count: int
    folder_count: int
    failed_jobs: int = 0
    preview_failed: int = 0
    fixity_failed: int = 0  # originals whose last fixity check failed
    source_gone: int = 0  # M4: live items whose source page is gone (link-rot watch)
    # M6: live items in no folder and with no tags (view=unfiled; the app badge)
    unfiled: int = 0


class JobOut(BaseModel):
    id: int
    kind: str
    status: str  # lifecycle.JobStatus
    attempts: int
    filename: str | None  # the file an ingest job imports
    item_id: str | None  # the item a regen_preview job works on
    result_item_id: str | None = None  # the item a clip job made
    last_error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class DuplicatePair(BaseModel):
    a: ItemOut
    b: ItemOut
    distance: int


class ClientErrorIn(BaseModel):
    """A browser-side crash report (POST /api/client-errors)."""

    message: str = Field(max_length=2000)
    stack: str | None = Field(default=None, max_length=10000)
    url: str | None = None  # clipped when logged
    user_agent: str | None = None
    kind: Literal["render", "error", "unhandledrejection"] | None = None


class ItemLite(BaseModel):
    """Just enough of an item to show it in a list of links."""

    id: str
    filename: str
    kind: str
    state: str
    thumb_url: str | None
    thumbhash: str | None
    width: int | None
    height: int | None

    @classmethod
    def from_item(cls, item: Item) -> "ItemLite":
        full = ItemOut.from_item(item)
        return cls(**full.model_dump(include=set(cls.model_fields)))


class LinkOut(BaseModel):
    item: ItemLite
    kind: str
    direction: Literal["out", "in"]


class LinkCreate(BaseModel):
    dst_id: str
    kind: str


class EventOut(BaseModel):
    id: int
    item_id: str
    at: datetime
    actor: str
    source: str
    batch_id: str
    kind: str
    before: Any = None
    after: Any = None


class BatchOut(BaseModel):
    batch_id: str
    actor: str
    source: str
    at: datetime
    count: int
    kinds: list[str]


class UndoSkip(BaseModel):
    item_id: str  # the board id when target is "board"; "vocabulary" for tag rules
    event_id: int
    reason: str
    target: Literal["item", "board", "vocabulary"] = "item"


class UndoOut(BaseModel):
    undone: int
    skipped: list[UndoSkip]
    batch_id: str  # the new batch the undo was recorded as (undo it to redo)


class VersionOut(BaseModel):
    version: str
    commit: str



# ---------- Milestone 3: creative workflows ----------


class BoardSummary(BaseModel):
    id: str
    name: str
    version: int
    item_count: int
    created_at: datetime
    modified_at: datetime
    deleted_at: datetime | None = None
    thumb_url: str


class BoardCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    canvas: dict[str, Any] | None = None
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    # convenience: lay these items out as nodes (grid or masonry)
    item_ids: list[str] = []
    layout: Literal["grid", "masonry"] = "grid"


class BoardPut(BaseModel):
    """The whole document. base_version is the version you last saw."""

    base_version: int
    name: str = Field(min_length=1, max_length=200)
    canvas: dict[str, Any] | None = None
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []


class BoardPatch(BaseModel):
    """Incremental save. add: new nodes; update: [{id, ...fields}] merged
    into the node (a null value removes the key); remove: node ids (their
    edges and group memberships go too). edges_* work the same on edges.
    base_version: refused with 409 if the board has moved on; leave it out
    to apply the ops to whatever is current (agents adding items)."""

    base_version: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    canvas: dict[str, Any] | None = None
    add: list[dict[str, Any]] = []
    update: list[dict[str, Any]] = []
    remove: list[str] = []
    edges_add: list[dict[str, Any]] = []
    edges_update: list[dict[str, Any]] = []
    edges_remove: list[str] = []


class BoardAddItems(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=500)
    layout: Literal["grid", "masonry"] = "grid"
    base_version: int | None = None


class AnnotationIn(BaseModel):
    kind: Literal["point", "box", "range"]
    x: float | None = None
    y: float | None = None
    w: float | None = None
    h: float | None = None
    t_start: float | None = None
    t_end: float | None = None
    text: str = Field(default="", max_length=4000)
    resolved: bool = False


class AnnotationPatch(BaseModel):
    x: float | None = None
    y: float | None = None
    w: float | None = None
    h: float | None = None
    t_start: float | None = None
    t_end: float | None = None
    text: str | None = Field(default=None, max_length=4000)
    resolved: bool | None = None


class AnnotationOut(BaseModel):
    id: str
    item_id: str
    kind: str
    x: float | None
    y: float | None
    w: float | None
    h: float | None
    t_start: float | None
    t_end: float | None
    text: str
    author: str
    created_at: datetime
    resolved: bool


class TemplateFields(BaseModel):
    tags_add: list[str] = []
    folder_add: list[str] = []
    notes_append: str = Field(default="", max_length=validation.NOTES_MAX)
    source_url: str | None = None
    rating: int | None = Field(default=None, ge=0, le=5)
    label: Literal["red", "orange", "yellow", "green", "blue", "purple", "none"] | None = None
    flag: Literal["pick", "reject", "none"] | None = None
    credit: str | None = Field(default=None, max_length=200)
    license: str | None = Field(default=None, max_length=200)

    _source_url = field_validator("source_url")(_check_source_url)


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    fields: TemplateFields = TemplateFields()


class TemplateOut(BaseModel):
    id: str
    name: str
    fields: TemplateFields


class TemplateApply(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=1000)


class RenameIn(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=1000)
    pattern: str = Field(min_length=1, max_length=400)
    dry_run: bool = True
    start: int = Field(default=1, ge=0)  # first value of {n}


class RenameRow(BaseModel):
    id: str
    before: str
    after: str
    changed: bool


class RenameOut(BaseModel):
    dry_run: bool
    items: list[RenameRow]
    batch_id: str | None = None  # undo it with POST /api/batches/{id}/undo


class TriageOut(BaseModel):
    item: ItemOut | None
    prefetch_ids: list[str]
    remaining: int  # items in the queue (what's left to triage)


class PaletteResult(BaseModel):
    kind: Literal["folder", "smart_folder", "tag", "board", "person", "item", "action"]
    id: str
    title: str
    subtitle: str = ""


class VersionsOut(BaseModel):
    """The version_of chain, oldest first; current_id is the newest."""

    items: list[ItemOut]
    current_id: str


# ---------- Milestone 6: date scrubber buckets (services/buckets.py) ----------


class BucketOut(BaseModel):
    key: str | None  # "YYYY-MM" (UTC); None = items with no date (last)
    count: int
    cursor: str | None  # GET /api/items?order=<item_order>&cursor=<this>


class BucketsOut(BaseModel):
    sort: str  # created | modified | taken
    order: str  # asc | desc
    item_order: str  # the GET /api/items order key these cursors belong to
    total: int
    buckets: list[BucketOut]
