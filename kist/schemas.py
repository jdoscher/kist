from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from . import access
from .config import get_settings
from .filetypes import kind_for
from .lifecycle import PreviewStatus, item_state
from .models import Item
from .services import validation


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
    # tags the vocabulary implies (boots -> shoes); virtual, never stored
    implied_tags: list[str] = []
    protected: bool = False  # can't be trashed or purged until unprotected
    state: str  # lifecycle.ItemState: active | trashed
    preview_status: str  # lifecycle.PreviewStatus: pending | ready | failed | none
    preview_error: str | None
    preview_rev: int  # changes when the preview files do (thumbnail URL ?v=)
    has_thumb: bool
    thumb_url: str | None
    created_at: datetime
    modified_at: datetime
    deleted_at: datetime | None
    # the item's stack (services/stacks.py) and how many members you can see
    stack_id: str | None = None
    stack_count: int | None = None

    @classmethod
    def from_item(cls, item: Item) -> "ItemOut":
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
            implied_tags=implied_for_item(item),
            protected=bool(item.protected),
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
            stack_id=item.stack_id,
            stack_count=count_for(item),
        )

    @classmethod
    def for_agent(cls, item: Item) -> dict[str, Any]:
        """What MCP tools return: the same item, minus the bulky internals
        (phash, dominant_colors), plus links an agent can use or hand on."""
        out = cls.from_item(item).model_dump(mode="json")
        out.pop("phash", None)
        out.pop("dominant_colors", None)
        out["file_url"] = f"/api/items/{item.id}/file"
        out["web_url"] = web_url(item)
        return out


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


OrderName = Literal["-created", "created", "filename", "-size", "-rating", "-modified"]
KindName = Literal["image", "video", "pdf", "other"]
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
    stacks: Literal["collapse"] | None = None  # covers only
    person: str | None = None  # a person id (faces)


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


class JobOut(BaseModel):
    id: int
    kind: str
    status: str  # lifecycle.JobStatus
    attempts: int
    filename: str | None  # the file an ingest job imports
    item_id: str | None  # the item a regen_preview job works on
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
    item_id: str
    event_id: int
    reason: str


class UndoOut(BaseModel):
    undone: int
    skipped: list[UndoSkip]
    batch_id: str  # the new batch the undo was recorded as (undo it to redo)


class VersionOut(BaseModel):
    version: str
    commit: str
