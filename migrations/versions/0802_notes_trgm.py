"""Trigram index on items.notes for keyword search (Kist 2.0 M6 perf)

Keyword search (q=) matches the filename, notes, place, annotation text and
text in the item. services/ranking.keyword_condition now unions one index
scan per field instead of testing every item; filename, place, annotations
and item_text already had their indexes, notes had none.

Revision ID: 0802
Revises: 0801
Create Date: 2026-11-10

"""

from alembic import op

revision = "0802"
down_revision = "0801"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_items_notes_trgm "
               "ON items USING gin (notes gin_trgm_ops)")


def downgrade() -> None:
    op.drop_index("ix_items_notes_trgm", table_name="items")
