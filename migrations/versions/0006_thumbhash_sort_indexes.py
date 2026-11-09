"""thumbhash placeholders; an index per list sort order

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-09

"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# search.ORDERS: keyset pages over live items stay index scans
SORT_COLUMNS = ("created_at", "filename", "size_bytes", "rating", "modified_at")


def upgrade() -> None:
    op.add_column("items", sa.Column("thumbhash", sa.Text(), nullable=True))
    for col in SORT_COLUMNS:
        op.create_index(
            f"ix_items_live_{col}_id", "items", [col, "id"],
            postgresql_where=sa.text("deleted_at IS NULL"),
        )
    op.create_index("ix_items_trashed_deleted_at_id", "items", ["deleted_at", "id"],
                    postgresql_where=sa.text("deleted_at IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_items_trashed_deleted_at_id", table_name="items")
    for col in SORT_COLUMNS:
        op.drop_index(f"ix_items_live_{col}_id", table_name="items")
    op.drop_column("items", "thumbhash")
