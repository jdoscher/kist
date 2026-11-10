"""live-items index for the taken sort; month statistics (Milestone 6)

GET /api/items?order=-taken pages and GET /api/items/buckets?sort=taken
read live items only (deleted_at IS NULL), like every other sort order;
0006 gave the others a partial (col, id) index, so their keyset pages and
month counts are index(-only) scans. 0601's ix_items_taken_sort covers
trashed items too and can't answer "live only" without the heap.

GET /api/items/buckets groups by date_trunc('month', timezone('UTC', col)).
Without statistics on that expression Postgres guesses one group per row,
sorts every row (spilling to disk at 250k items) and aggregates; with them
it knows there are ~200 months and hash-aggregates an index-only scan of
the (col, id) index: 63 ms -> 10 ms on 250k synthetic items.

The web grid always sends stacks=collapse, whose filter reads stack_id
(stacks.representative_filter): the three date-sort indexes INCLUDE it, so
months and keyset pages are still index-only scans (55 ms -> 25 ms).

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


# services/buckets.SORTS
MONTH_COLUMNS = ("created_at", "modified_at", "taken_sort")


def _live_index(col: str, include: bool) -> None:
    op.create_index(
        f"ix_items_live_{col}_id", "items", [col, "id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
        postgresql_include=["stack_id"] if include else [],
    )


def upgrade() -> None:
    _live_index("taken_sort", include=True)
    for col in ("created_at", "modified_at"):  # 0006's, now covering stack_id
        op.drop_index(f"ix_items_live_{col}_id", table_name="items")
        _live_index(col, include=True)
    for col in MONTH_COLUMNS:
        op.execute(f"CREATE STATISTICS st_items_month_{col} "
                   f"ON (date_trunc('month', timezone('UTC', {col}))) FROM items")
    op.execute("ANALYZE items")


def downgrade() -> None:
    for col in MONTH_COLUMNS:
        op.execute(f"DROP STATISTICS IF EXISTS st_items_month_{col}")
    for col in ("created_at", "modified_at"):
        op.drop_index(f"ix_items_live_{col}_id", table_name="items")
        _live_index(col, include=False)
    op.drop_index("ix_items_live_taken_sort_id", table_name="items")
