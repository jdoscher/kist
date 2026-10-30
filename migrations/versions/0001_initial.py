"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-29

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CITEXT, JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    op.create_table(
        "folders",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "parent_id",
            sa.String(26),
            sa.ForeignKey("folders.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_folders_parent_id", "folders", ["parent_id"])

    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", CITEXT(), nullable=False, unique=True),
    )
    op.execute(
        "CREATE INDEX ix_tags_name_trgm ON tags USING gin (name gin_trgm_ops)"
    )

    op.create_table(
        "items",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("ext", sa.Text(), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("phash", sa.BigInteger(), nullable=True),
        sa.Column("dominant_colors", JSONB(), nullable=True),
        sa.Column(
            "folder_id",
            sa.String(26),
            sa.ForeignKey("folders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("rating", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "modified_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ux_items_sha256", "items", ["sha256"], unique=True)
    op.create_index("ix_items_folder_id", "items", ["folder_id"])
    op.create_index("ix_items_created_at", "items", ["created_at"])
    op.create_index("ix_items_deleted_at", "items", ["deleted_at"])
    op.create_index("ix_items_phash", "items", ["phash"])
    op.execute(
        "CREATE INDEX ix_items_filename_trgm ON items USING gin (filename gin_trgm_ops)"
    )

    op.create_table(
        "item_tags",
        sa.Column(
            "item_id",
            sa.String(26),
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_item_tags_tag_id", "item_tags", ["tag_id"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "run_after",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_jobs_status_run_after", "jobs", ["status", "run_after"])


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("item_tags")
    op.drop_table("items")
    op.drop_table("tags")
    op.drop_table("folders")
