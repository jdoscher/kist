"""item_kinds + item_kind_passes: utility collections (Kist 2.0 F1)

item_kinds holds one row per item and kind Kist considered: the automatic
score and call (`auto`, from CLIP zero-shot, rules and the per-kind learned
classifier) and the person's verdict (`verdict`: true = belongs here, false
= not this, NULL = no correction). `member` is the verdict when there is
one, else the automatic call: a confirmed human call always wins.
item_kind_passes remembers which version of the settings and models an
item was last sorted with, so the backfill only does missing work.

The verdicts' durable copy is items/<id>/ai/kinds.json; automatic calls are
recomputed from the embeddings and files (services/kinds.py).

Revision ID: 0901
Revises: 0803
Create Date: 2026-11-12

"""

import sqlalchemy as sa
from alembic import op

revision = "0901"
down_revision = "0803"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "item_kinds",
        sa.Column("item_id", sa.String(26), sa.ForeignKey("items.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("kind", sa.Text(), primary_key=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("auto", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("why", sa.Text(), nullable=True),
        sa.Column("verdict", sa.Boolean(), nullable=True),
        sa.Column("verdict_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("member", sa.Boolean(),
                  sa.Computed("coalesce(verdict, auto)", persisted=True)),
    )
    op.create_index("ix_item_kinds_member", "item_kinds", ["kind", "item_id"],
                    postgresql_where=sa.text("member"))
    op.create_index("ix_item_kinds_verdict", "item_kinds", ["kind"],
                    postgresql_where=sa.text("verdict IS NOT NULL"))
    op.create_table(
        "item_kind_passes",
        sa.Column("item_id", sa.String(26), sa.ForeignKey("items.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("sig", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )


def downgrade() -> None:
    op.drop_table("item_kind_passes")
    op.drop_index("ix_item_kinds_verdict", table_name="item_kinds")
    op.drop_index("ix_item_kinds_member", table_name="item_kinds")
    op.drop_table("item_kinds")
