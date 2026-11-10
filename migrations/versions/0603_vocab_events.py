"""vocab_events: changes to the tag vocabulary, batched with item events.

A tag merge retags items (item events) and leaves the old names as aliases;
recording the alias and rule changes in the same batch lets undo (Recent
changes, MCP undo_batch) take the alias back too. library/
vocabulary-events.jsonl is the durable copy (rebuild-index restores it).

Revision ID: 0603
Revises: 0602
Create Date: 2026-11-10

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0603"
down_revision = "0602"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vocab_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("batch_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_vocab_events_batch_id", "vocab_events", ["batch_id"])


def downgrade() -> None:
    op.drop_table("vocab_events")
