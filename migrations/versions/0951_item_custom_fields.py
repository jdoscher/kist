"""items.custom_fields: per-project custom field values (Kist 2.0 F6)

A project (library/.kist/projects.json, services/projects.py) defines
fields (text, number, date, single choice, URL); an item in the project's
folders stores its values here, keyed by field id. meta.json carries the
same block, so rebuild-index restores it. The GIN index serves
field.<name>= filters (containment for exact choice matches, key presence).

Revision ID: 0951
Revises: 0921
Create Date: 2026-11-12

"""

from alembic import op

revision = "0951"
down_revision = "0921"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS custom_fields jsonb "
               "NOT NULL DEFAULT '{}'::jsonb")
    op.execute("CREATE INDEX IF NOT EXISTS ix_items_custom_fields ON items "
               "USING gin (custom_fields)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_items_custom_fields")
    op.drop_column("items", "custom_fields")
