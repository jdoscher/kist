"""fixity: when each original was last re-hashed, and when a check failed

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-09

"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("fixity_checked_at", sa.DateTime(timezone=True),
                                     nullable=True))
    op.add_column("items", sa.Column("fixity_failed_at", sa.DateTime(timezone=True),
                                     nullable=True))
    # the worker checks the oldest-checked first
    op.create_index("ix_items_fixity_checked_at_id", "items", ["fixity_checked_at", "id"])
    op.create_index("ix_items_fixity_failed_at", "items", ["fixity_failed_at"],
                    postgresql_where=sa.text("fixity_failed_at IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_items_fixity_failed_at", table_name="items")
    op.drop_index("ix_items_fixity_checked_at_id", table_name="items")
    op.drop_column("items", "fixity_failed_at")
    op.drop_column("items", "fixity_checked_at")
