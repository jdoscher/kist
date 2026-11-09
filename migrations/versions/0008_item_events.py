"""item history: one row per item change (copy in items/<id>/events.jsonl)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-09

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # no foreign key: a purged item's history outlives it
    op.create_table(
        "item_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("item_id", sa.String(26), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("batch_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_item_events_item_id_id", "item_events", ["item_id", "id"])
    op.create_index("ix_item_events_batch_id", "item_events", ["batch_id"])


def downgrade() -> None:
    op.drop_table("item_events")
