"""Media metadata (Milestone 5): items.media plus the indexed columns the
filters, the timeline and the map use.

taken_sort is a generated column (taken_at, else created_at) so the `taken`
order and the timeline can use one index with keyset pagination.

Revision ID: 0601
Revises: 0501
Create Date: 2026-11-09

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0601"
down_revision = "0501"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("media", JSONB, nullable=True))
    op.add_column("items", sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "items",
        sa.Column(
            "taken_sort", sa.DateTime(timezone=True),
            sa.Computed("coalesce(taken_at, created_at)", persisted=True),
        ),
    )
    op.add_column(
        "items", sa.Column("has_gps", sa.Boolean, nullable=False, server_default="false")
    )
    op.add_column("items", sa.Column("lat", sa.Float, nullable=True))
    op.add_column("items", sa.Column("lon", sa.Float, nullable=True))
    op.add_column("items", sa.Column("camera", sa.Text, nullable=True))
    op.add_column("items", sa.Column("lens", sa.Text, nullable=True))
    op.add_column("items", sa.Column("vertical", sa.Boolean, nullable=True))
    op.add_column("items", sa.Column("has_audio", sa.Boolean, nullable=True))
    op.add_column("items", sa.Column("place", sa.Text, nullable=True))
    op.add_column("items", sa.Column("raw_pair_key", sa.Text, nullable=True))

    op.create_index("ix_items_taken_sort", "items", ["taken_sort", "id"])
    op.create_index("ix_items_taken_at", "items", ["taken_at"])
    op.create_index(
        "ix_items_geo", "items", ["lat", "lon"], postgresql_where=sa.text("has_gps")
    )
    op.create_index("ix_items_camera", "items", ["camera"])
    op.create_index("ix_items_lens", "items", ["lens"])
    op.create_index("ix_items_duration", "items", ["duration_s"])
    op.create_index("ix_items_raw_pair_key", "items", ["raw_pair_key"])
    op.execute(
        "CREATE INDEX ix_items_place_trgm ON items USING gin (place gin_trgm_ops)"
    )


def downgrade() -> None:
    for name in ("ix_items_place_trgm", "ix_items_raw_pair_key", "ix_items_duration",
                 "ix_items_lens", "ix_items_camera", "ix_items_geo", "ix_items_taken_at",
                 "ix_items_taken_sort"):
        op.drop_index(name, table_name="items")
    for col in ("raw_pair_key", "place", "has_audio", "vertical", "lens", "camera", "lon",
                "lat", "has_gps", "taken_sort", "taken_at", "media"):
        op.drop_column("items", col)
