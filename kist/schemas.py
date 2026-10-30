from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .filetypes import kind_for
from .models import Item


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
    folder_ids: list[str]
    rating: int
    notes: str
    source_url: str | None
    tags: list[str]
    has_thumb: bool
    thumb_url: str | None
    created_at: datetime
    modified_at: datetime
    deleted_at: datetime | None

    @classmethod
    def from_item(cls, item: Item) -> "ItemOut":
        has_thumb = item.width is not None
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
            folder_ids=sorted(f.id for f in item.folders),
            rating=item.rating,
            notes=item.notes,
            source_url=item.source_url,
            tags=sorted(t.name for t in item.tags),
            has_thumb=has_thumb,
            thumb_url=f"/api/items/{item.id}/thumb" if has_thumb else None,
            created_at=item.created_at,
            modified_at=item.modified_at,
            deleted_at=item.deleted_at,
        )


class ItemPage(BaseModel):
    items: list[ItemOut]
    next_cursor: str | None
    total: int | None = None


class ItemCreateResponse(BaseModel):
    item: ItemOut
    created: bool


class ItemPatch(BaseModel):
    filename: str | None = None
    rating: int | None = Field(default=None, ge=0, le=5)
    notes: str | None = None
    folder_id: str | None = None  # convenience: replaces memberships with one
    folder_ids: list[str] | None = None
    source_url: str | None = None
    created_at: datetime | None = None
    modified_at: datetime | None = None


class TagsPut(BaseModel):
    tags: list[str]


class FoldersPut(BaseModel):
    folder_ids: list[str]


class UrlAdd(BaseModel):
    url: str
    folder_id: str | None = None
    folder_ids: list[str] = []
    tags: list[str] = []
    notes: str = ""
    source_url: str | None = None  # defaults to the download url itself
    rating: int = Field(default=0, ge=0, le=5)
    created_at: datetime | None = None
    modified_at: datetime | None = None
    deleted_at: datetime | None = None


class FolderOut(BaseModel):
    id: str
    name: str
    parent_id: str | None
    position: int
    item_count: int = 0


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None


class FolderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: str | None = None
    position: int | None = None


class TagOut(BaseModel):
    id: int
    name: str
    count: int


class SmartFolderQuery(BaseModel):
    """The saved search a smart folder stands for (same params as GET /api/items)."""

    q: str = ""
    tags: list[str] = []
    tags_any: list[str] = []  # OR semantics
    untagged: bool = False
    folder: str | None = None
    type: str | None = None
    rating: int = Field(default=0, ge=0, le=5)
    order: str = "-created"
    created_after: datetime | None = None
    created_before: datetime | None = None


class SmartFolderOut(BaseModel):
    id: str
    name: str
    query: SmartFolderQuery
    position: int


class SmartFolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    query: SmartFolderQuery = SmartFolderQuery()


class SmartFolderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    query: SmartFolderQuery | None = None
    position: int | None = None


class StatsOut(BaseModel):
    total_items: int
    trashed_items: int
    total_size_bytes: int
    by_kind: dict[str, int]
    tag_count: int
    folder_count: int


class DuplicatePair(BaseModel):
    a: ItemOut
    b: ItemOut
    distance: int
