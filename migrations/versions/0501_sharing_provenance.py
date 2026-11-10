"""M4: share links, provenance, link-rot watch

- shares: public read-only links (/s/<slug>); library/.kist/shares.json is
  the durable copy rebuild-index restores
- items.provenance (JSONB): where an item came from and what may be done
  with it. The existing source_url is its page_url (copied in here; the
  column stays and the two are kept equal)
- items.source_status / source_checked_at / source_wayback_url: the weekly
  link-rot check of page_url (services/linkrot.py)

Revision ID: 0501
Revises: 0101
Create Date: 2026-11-09

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0501"
down_revision = "0101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shares",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("slug", sa.Text, nullable=False, unique=True),
        sa.Column("target_kind", sa.Text, nullable=False),
        sa.Column("target_id", sa.Text, nullable=True),
        sa.Column("item_ids", postgresql.ARRAY(sa.Text), nullable=True),
        sa.Column("title", sa.Text, nullable=False, server_default=""),
        sa.Column("password_hash", sa.Text, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("allow_download", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("allow_original", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_by", sa.Text, nullable=False, server_default="owner"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("view_count", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("items", sa.Column("provenance", postgresql.JSONB, nullable=True))
    op.add_column("items", sa.Column("source_status", sa.Text, nullable=True))
    op.add_column("items", sa.Column("source_checked_at", sa.DateTime(timezone=True),
                                     nullable=True))
    op.add_column("items", sa.Column("source_wayback_url", sa.Text, nullable=True))
    # source_url is the page the item came from: provenance.page_url
    op.execute(
        "UPDATE items SET provenance = jsonb_build_object('page_url', source_url) "
        "WHERE source_url IS NOT NULL"
    )
    # the ai_generated= and license= filters, and the link-rot queue
    op.create_index(
        "ix_items_prov_ai_generated", "items", [sa.text("(provenance->>'ai_generated')")],
        postgresql_where=sa.text("provenance ? 'ai_generated'"),
    )
    op.create_index(
        "ix_items_prov_license", "items", [sa.text("lower(provenance->>'license')")],
        postgresql_where=sa.text("provenance ? 'license'"),
    )
    op.create_index("ix_items_source_checked_at", "items", ["source_checked_at", "id"],
                    postgresql_where=sa.text("source_url IS NOT NULL"))
    op.create_index("ix_items_source_status", "items", ["source_status"],
                    postgresql_where=sa.text("source_status IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_items_source_status", table_name="items")
    op.drop_index("ix_items_source_checked_at", table_name="items")
    op.drop_index("ix_items_prov_license", table_name="items")
    op.drop_index("ix_items_prov_ai_generated", table_name="items")
    op.drop_column("items", "source_wayback_url")
    op.drop_column("items", "source_checked_at")
    op.drop_column("items", "source_status")
    op.drop_column("items", "provenance")
    op.drop_table("shares")
