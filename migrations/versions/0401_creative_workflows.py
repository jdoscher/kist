"""Milestone 3: flags and colour labels, boards, board events, annotations

Revision ID: 0401
Revises: 0301
Create Date: 2026-11-09

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0401"
down_revision = "0301"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("flag", sa.Text(), nullable=True))
    op.add_column("items", sa.Column("label", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_items_flag", "items", "flag IS NULL OR flag IN ('pick', 'reject')"
    )
    op.create_check_constraint(
        "ck_items_label", "items",
        "label IS NULL OR label IN ('red', 'orange', 'yellow', 'green', 'blue', 'purple')",
    )
    op.create_index("ix_items_flag", "items", ["flag"],
                    postgresql_where=sa.text("flag IS NOT NULL"))
    op.create_index("ix_items_label", "items", ["label"],
                    postgresql_where=sa.text("label IS NOT NULL"))

    op.create_table(
        "boards",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("doc", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_boards_modified_at", "boards", ["modified_at"])
    op.create_table(
        "board_items",
        sa.Column("board_id", sa.String(26), sa.ForeignKey("boards.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("item_id", sa.String(26), sa.ForeignKey("items.id", ondelete="CASCADE"),
                  primary_key=True),
    )
    op.create_index("ix_board_items_item_id", "board_items", ["item_id"])
    # no foreign key: a purged board's history outlives it
    op.create_table(
        "board_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("board_id", sa.String(26), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("batch_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_board_events_board_id_id", "board_events", ["board_id", "id"])
    op.create_index("ix_board_events_batch_id", "board_events", ["batch_id"])

    op.create_table(
        "annotations",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("item_id", sa.String(26), sa.ForeignKey("items.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("x", sa.Float(), nullable=True),
        sa.Column("y", sa.Float(), nullable=True),
        sa.Column("w", sa.Float(), nullable=True),
        sa.Column("h", sa.Float(), nullable=True),
        sa.Column("t_start", sa.Float(), nullable=True),
        sa.Column("t_end", sa.Float(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("author", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("kind IN ('point', 'box', 'range')", name="ck_annotations_kind"),
    )
    op.create_index("ix_annotations_item_id", "annotations", ["item_id"])
    # annotation text is searched with q (ILIKE '%...%')
    op.execute(
        "CREATE INDEX ix_annotations_text_trgm ON annotations USING gin (text gin_trgm_ops)"
    )


def downgrade() -> None:
    op.drop_table("annotations")
    op.drop_table("board_events")
    op.drop_table("board_items")
    op.drop_table("boards")
    op.drop_index("ix_items_label", "items")
    op.drop_index("ix_items_flag", "items")
    op.drop_constraint("ck_items_label", "items")
    op.drop_constraint("ck_items_flag", "items")
    op.drop_column("items", "label")
    op.drop_column("items", "flag")
