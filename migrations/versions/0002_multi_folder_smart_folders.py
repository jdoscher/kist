"""items in multiple folders + smart folders

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "item_folders",
        sa.Column(
            "item_id",
            sa.String(26),
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "folder_id",
            sa.String(26),
            sa.ForeignKey("folders.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_item_folders_folder_id", "item_folders", ["folder_id"])
    op.execute(
        "INSERT INTO item_folders (item_id, folder_id) "
        "SELECT id, folder_id FROM items WHERE folder_id IS NOT NULL"
    )
    op.drop_index("ix_items_folder_id", table_name="items")
    op.drop_column("items", "folder_id")

    op.create_table(
        "smart_folders",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("query", JSONB(), nullable=False, server_default="{}"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.add_column(
        "items",
        sa.Column(
            "folder_id",
            sa.String(26),
            sa.ForeignKey("folders.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE items SET folder_id = sub.folder_id FROM ("
        "  SELECT DISTINCT ON (item_id) item_id, folder_id FROM item_folders"
        ") sub WHERE items.id = sub.item_id"
    )
    op.create_index("ix_items_folder_id", "items", ["folder_id"])
    op.drop_table("smart_folders")
    op.drop_table("item_folders")
