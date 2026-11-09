"""preview revision counter (cache-busting for thumbnail URLs)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09

"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "items", sa.Column("preview_rev", sa.Integer(), nullable=False, server_default="0")
    )


def downgrade() -> None:
    op.drop_column("items", "preview_rev")
