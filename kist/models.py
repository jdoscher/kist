from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
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
    modified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
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

    tags: Mapped[list[Tag]] = relationship(secondary="item_tags", lazy="selectin")
    folders: Mapped[list[Folder]] = relationship(
        secondary="item_folders", lazy="selectin"
    )


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


class ItemText(Base):
    """Text found in an item: OCR of a screenshot, or a PDF's text layer."""

    __tablename__ = "item_text"

    item_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(Text, primary_key=True)  # ocr | pdf
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
