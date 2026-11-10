"""live-items index for the taken sort (Milestone 6)

GET /api/items?order=-taken pages and GET /api/items/buckets?sort=taken
read live items only (deleted_at IS NULL), like every other sort order;
0006 gave the others a partial (col, id) index, so their keyset pages and
month counts are index(-only) scans. 0601's ix_items_taken_sort covers
trashed items too and can't answer "live only" without the heap.

Revision ID: 0701
Revises: 0603
Create Date: 2026-11-10

"""

import sqlalchemy as sa
from alembic import op

revision = "0701"
down_revision = "0603"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_items_live_taken_sort_id", "items", ["taken_sort", "id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_items_live_taken_sort_id", table_name="items")
