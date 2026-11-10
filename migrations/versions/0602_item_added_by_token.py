"""items.added_by_token: the named token that added an item (int-all).

An add-only token (the browser extension's) may attach a page snapshot to an
item it added itself in the last 10 minutes (api/m4.py save_snapshot); this
column is how the server knows. It is request bookkeeping, not library data:
meta.json doesn't carry it and rebuild-index leaves it empty, which only
closes that 10-minute window early.

Revision ID: 0602
Revises: 0601
Create Date: 2026-11-09

"""

import sqlalchemy as sa
from alembic import op

revision = "0602"
down_revision = "0601"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("added_by_token", sa.String(26), nullable=True))


def downgrade() -> None:
    op.drop_column("items", "added_by_token")
