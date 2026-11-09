"""links between items, protected items, folder descriptions

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-09

"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "item_links",
        sa.Column("src_id", sa.String(26), sa.ForeignKey("items.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("dst_id", sa.String(26), sa.ForeignKey("items.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("kind", sa.Text(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("src_id <> dst_id", name="ck_item_links_not_self"),
    )
    op.create_index("ix_item_links_dst_id", "item_links", ["dst_id"])
    op.add_column("items", sa.Column("protected", sa.Boolean(), nullable=False,
                                     server_default=sa.false()))
    op.add_column("folders", sa.Column("description", sa.Text(), nullable=False,
                                       server_default=""))


def downgrade() -> None:
    op.drop_column("folders", "description")
    op.drop_column("items", "protected")
    op.drop_table("item_links")
