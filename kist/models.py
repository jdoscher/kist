from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    SmallInteger,
    String,
    Text,
    event,
    func,
)
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import UserDefinedType


class HalfVec(UserDefinedType):
    """pgvector's halfvec (float16 vector), sent as its text form '[1,2,3]'.
    HalfVec() is the column (any size); HalfVec(512) is a cast to one size,
    which is what the per-model HNSW indexes are built on."""

    cache_ok = True

    def __init__(self, dim: int | None = None):
        self.dim = dim

    def get_col_spec(self, **kw) -> str:
        return f"halfvec({self.dim})" if self.dim else "halfvec"

    def bind_processor(self, dialect):
        def process(value):
            if value is None or isinstance(value, str):
                return value
            return "[" + ",".join(repr(float(x)) for x in value) + "]"

        return process

    def result_processor(self, dialect, coltype):
        def process(value):
            if value is None or not isinstance(value, str):
                return value
            return [float(x) for x in value.strip("[]").split(",") if x]

        return process


class Base(DeclarativeBase):
    pass


class Folder(Base):
    __tablename__ = "folders"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    parent_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("folders.id", ondelete="CASCADE"), nullable=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    description: Mapped[str] = mapped_column(Text, default="", server_default="")

    children: Mapped[list["Folder"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    parent: Mapped["Folder | None"] = relationship(
        back_populates="children", remote_side=[id]
    )


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(CITEXT, unique=True)


class ItemTag(Base):
    __tablename__ = "item_tags"

    item_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


class ItemFolder(Base):
    __tablename__ = "item_folders"

    item_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    folder_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("folders.id", ondelete="CASCADE"), primary_key=True
    )


class SmartFolder(Base):
    __tablename__ = "smart_folders"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    query: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Item(Base):
    __tablename__ = "items"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    filename: Mapped[str] = mapped_column(Text)
    ext: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    phash: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dominant_colors: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
    )
    rating: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # moved by edits only: _touch_on_edit below (an ORM flush that changes
    # anything outside BOOKKEEPING_COLUMNS) or an explicit set. Background
    # bookkeeping (previews, link checks, fixity, backfills) leaves it alone.
    modified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # lifecycle.PreviewStatus: pending | ready | failed | none
    preview_status: Mapped[str] = mapped_column(
        Text, default="pending", server_default="pending"
    )
    preview_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # bumped whenever the preview files change; part of every thumbnail URL so
    # browsers can cache them forever and still see a regenerated preview
    preview_rev: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # protected items can't be trashed or purged (retention skips them too)
    protected: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # fixity: the worker re-hashes originals on a rolling schedule
    # (items/<id>/fixity.jsonl is the record; services/fixity.py)
    fixity_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fixity_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # base64 ThumbHash of the preview (services/thumbhash.py); "" = can't be made
    thumbhash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # services/stacks.py; "stack" in meta.json is the durable copy
    stack_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("stacks.id", ondelete="SET NULL"), nullable=True
    )
    # Milestone 3: culling flag (pick | reject) and colour label (red, orange,
    # yellow, green, blue, purple); null = none. services/validation-checked.
    flag: Mapped[str | None] = mapped_column(Text, nullable=True)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    # M4 (services/provenance.py): where it came from and what may be done
    # with it; page_url mirrors source_url
    provenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # M4 link-rot watch (services/linkrot.py): ok | gone | moved | error
    source_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_wayback_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- media (Milestone 5; services/media.py) ----
    # everything Kist read from the file (ffprobe, EXIF, SVG colours, model
    # stats ...) plus the few user choices about it (cover_time, poster).
    # meta.json carries this block; the columns below are derived from it
    # (media.apply) so they can be filtered and indexed
    media: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    # when the photo/video was taken (EXIF DateTimeOriginal as wall-clock
    # time, stored as if UTC; or a video's creation_time)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # taken_at, else created_at: the timeline's date and the `taken` sort
    taken_sort: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), Computed("coalesce(taken_at, created_at)", persisted=True)
    )
    has_gps: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    camera: Mapped[str | None] = mapped_column(Text, nullable=True)
    lens: Mapped[str | None] = mapped_column(Text, nullable=True)
    vertical: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    has_audio: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # "City, Country" from offline reverse geocoding (services/geo.py)
    place: Mapped[str | None] = mapped_column(Text, nullable=True)
    # basename + taken_at: a RAW and its camera JPEG share it (M2 stacks)
    raw_pair_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # the named token that added it (migration 0602): lets an add-only token
    # attach a page snapshot to its own fresh item. Not in meta.json.
    added_by_token: Mapped[str | None] = mapped_column(String(26), nullable=True)

    tags: Mapped[list[Tag]] = relationship(secondary="item_tags", lazy="selectin")
    folders: Mapped[list[Folder]] = relationship(
        secondary="item_folders", lazy="selectin"
    )
    # the caption (services/captions.py), if any: only the caption row is
    # loaded, never an item's (possibly long) OCR text
    caption_row: Mapped["ItemText | None"] = relationship(
        "ItemText",
        primaryjoin="and_(ItemText.item_id == Item.id, ItemText.source == 'caption')",
        foreign_keys="ItemText.item_id",
        uselist=False,
        viewonly=True,
        lazy="selectin",
    )

    @property
    def caption(self) -> str | None:
        row = self.caption_row
        return row.text if row is not None and row.text else None


# Columns that background jobs write about an item without anyone editing
# it: what Kist derived from the file (previews, size, metadata, palette),
# and what it observed (fixity, link checks). Changing only these never
# moves modified_at, so "recently modified" keeps meaning edited, and an
# open editor's base_modified_at stays current.
BOOKKEEPING_COLUMNS = frozenset({
    "preview_status", "preview_error", "preview_rev",
    "width", "height", "duration_s", "phash", "dominant_colors", "thumbhash", "mime",
    "fixity_checked_at", "fixity_failed_at",
    "source_status", "source_checked_at", "source_wayback_url",
    "media", "taken_at", "has_gps", "lat", "lon", "camera", "lens", "vertical",
    "has_audio", "place", "raw_pair_key",
})


@event.listens_for(Item, "before_update")
def _touch_on_edit(_mapper, _connection, target: Item) -> None:
    """Move modified_at when a flush edits the item (what onupdate=now did),
    unless it only touches BOOKKEEPING_COLUMNS or sets modified_at itself.
    Core UPDATEs (sa.update(Item)) never move it: they set it explicitly
    when they are an edit (media.update_media(touch=True))."""
    state = sa_inspect(target)
    if state.attrs.modified_at.history.has_changes():
        return
    for attr in state.mapper.column_attrs:
        if attr.key in BOOKKEEPING_COLUMNS or attr.key == "modified_at":
            continue
        if state.attrs[attr.key].history.has_changes():
            target.modified_at = datetime.now(UTC)
            return


class ItemLink(Base):
    """A typed link between two items (services/links.py); kept in both
    items' meta.json."""

    __tablename__ = "item_links"

    src_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    dst_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ItemEmbedding(Base):
    """One image embedding per item and model (pgvector halfvec). The
    vector itself is read and written with SQL in services/ai.py; the copy
    on disk is items/<id>/ai/embed-<model>.f16."""

    __tablename__ = "item_embeddings"

    item_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[list[float]] = mapped_column(HalfVec(), deferred=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ItemEvent(Base):
    """One change to one item (services/events.py). No foreign key: the
    history of a purged item is kept."""

    __tablename__ = "item_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(26))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    batch_id: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    before: Mapped[Any] = mapped_column(JSONB, nullable=True)
    after: Mapped[Any] = mapped_column(JSONB, nullable=True)


class ApiToken(Base):
    """A named token with scopes (access.Scope). Only its SHA-256 is kept;
    library/.kist/tokens.json is the durable copy rebuild-index restores."""

    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    folder_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ItemColor(Base):
    """One dominant colour of an item in CIE Lab, kept in step with
    items.dominant_colors by a database trigger (migration 0201)."""

    __tablename__ = "item_colors"

    item_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    l: Mapped[float] = mapped_column(Float)  # noqa: E741
    a: Mapped[float] = mapped_column(Float)
    b: Mapped[float] = mapped_column(Float)
    pct: Mapped[float] = mapped_column(Float)


class ItemText(Base):
    """Text found in or written about an item: OCR of a screenshot, a PDF's
    text layer, or a caption (source ocr | pdf | caption)."""

    __tablename__ = "item_text"

    item_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(Text, primary_key=True)  # ocr | pdf | caption | page
    text: Mapped[str] = mapped_column(Text)
    lang: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AiHelper(Base):
    """A `kist helper` that has claimed jobs (name, when it was last seen)."""

    __tablename__ = "ai_helpers"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    jobs_done: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    info: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Share(Base):
    """A public read-only link, /s/<slug> (services/shares.py). The durable
    copy is library/.kist/shares.json."""

    __tablename__ = "shares"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    # folder | smart | board | items
    target_kind: Mapped[str] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_ids: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    title: Mapped[str] = mapped_column(Text, default="", server_default="")
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    allow_download: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    allow_original: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_by: Mapped[str] = mapped_column(Text, default="owner", server_default="owner")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    view_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    last_viewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # lifecycle.JobStatus: pending | running | done | failed
    status: Mapped[str] = mapped_column(
        Text, default="pending", server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # set when a worker claims the job; a running job older than the lease
    # belonged to a worker that died and is put back to pending
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ---------- Milestone 2: organise ----------


class TagAlias(Base):
    """A variant spelling and the tag it stands for (services/vocabulary.py);
    library/vocabulary.json is the durable copy."""

    __tablename__ = "tag_aliases"

    alias: Mapped[str] = mapped_column(CITEXT, primary_key=True)
    tag: Mapped[str] = mapped_column(CITEXT)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class VocabEvent(Base):
    """One change to the tag vocabulary (services/vocabulary.py): the aliases,
    implied-tag rules and smart-folder filters it changed, before and after.
    Batched like item events, so undoing a tag merge also takes back the
    alias it left. library/vocabulary-events.jsonl is the durable copy."""

    __tablename__ = "vocab_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    batch_id: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    before: Mapped[Any] = mapped_column(JSONB, nullable=True)
    after: Mapped[Any] = mapped_column(JSONB, nullable=True)


class TagImplies(Base):
    """`tag` implies `implied` (boots -> shoes): searching the implied tag
    finds items carrying `tag`. Never written to items."""

    __tablename__ = "tag_implies"

    tag: Mapped[str] = mapped_column(CITEXT, primary_key=True)
    implied: Mapped[str] = mapped_column(CITEXT, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TagEmbedding(Base):
    """A tag name as a text vector (zero-shot tag suggestions); a cache."""

    __tablename__ = "tag_embeddings"

    model: Mapped[str] = mapped_column(Text, primary_key=True)
    tag: Mapped[str] = mapped_column(CITEXT, primary_key=True)
    vector: Mapped[list[float]] = mapped_column(HalfVec(), deferred=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TagFeedback(Base):
    """A tag suggestion someone accepted or rejected for one item."""

    __tablename__ = "tag_feedback"

    item_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(CITEXT, primary_key=True)
    accepted: Mapped[bool] = mapped_column(Boolean)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Stack(Base):
    """Items shown as one in the grid (RAW+JPEG, a burst, versions)."""

    __tablename__ = "stacks"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    cover_item_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("items.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Person(Base):
    """A cluster of faces, named or not (services/faces.py);
    library/.kist/people.json is the durable copy."""

    __tablename__ = "people"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_face_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Face(Base):
    """One detected face; items/<id>/ai/faces.json is the durable copy
    (face `idx` is its position in that file)."""

    __tablename__ = "faces"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    item_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("items.id", ondelete="CASCADE")
    )
    idx: Mapped[int] = mapped_column(Integer)
    # {x, y, w, h} as fractions of the image, plus landmarks
    box: Mapped[dict[str, Any]] = mapped_column(JSONB)
    score: Mapped[float] = mapped_column(Float)
    model: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(HalfVec(128), deferred=True)
    person_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("people.id", ondelete="SET NULL"), nullable=True
    )
# ---------- Milestone 3: creative workflows ----------


class Board(Base):
    """A board (services/boards.py). library/boards/<id>/board.json is the
    durable copy; `doc` is the same document, kept here so a save can be
    checked against base_version and written under one lock."""

    __tablename__ = "boards"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    modified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    item_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    doc: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class BoardItem(Base):
    """Which items a board shows ("used in boards"). A purged item's row
    goes with it; the board keeps the node, shown as a missing item."""

    __tablename__ = "board_items"

    board_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("boards.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )


class BoardEvent(Base):
    """One change to one board (services/boards.py), batched like item
    events so POST /api/batches/{id}/undo reverts both. No foreign key: a
    purged board's history is kept."""

    __tablename__ = "board_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    board_id: Mapped[str] = mapped_column(String(26))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    batch_id: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    before: Mapped[Any] = mapped_column(JSONB, nullable=True)
    after: Mapped[Any] = mapped_column(JSONB, nullable=True)


class Annotation(Base):
    """A note pinned to part of an item (services/annotations.py);
    items/<id>/annotations.json is the durable copy. Coordinates are 0-1 of
    the image, times are seconds into a video."""

    __tablename__ = "annotations"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    item_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("items.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text)  # point | box | range
    x: Mapped[float | None] = mapped_column(Float, nullable=True)
    y: Mapped[float | None] = mapped_column(Float, nullable=True)
    w: Mapped[float | None] = mapped_column(Float, nullable=True)
    h: Mapped[float | None] = mapped_column(Float, nullable=True)
    t_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    t_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    author: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
