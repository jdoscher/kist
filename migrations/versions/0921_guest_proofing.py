"""F3 guest proofing on share links: per-share settings, guest marks
(pick/reject/heart) and guest comments, tied to a numbered round and to
the item version the guest saw. library/.kist/shares/<id>/feedback.json is
the durable copy (services/proofing.py).

Revision ID: 0921
Revises: 0803
Create Date: 2026-11-12

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0921"
down_revision = "0803"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "share_proofing",
        sa.Column(
            "share_id",
            sa.String(26),
            sa.ForeignKey("shares.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("show_all", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("round", sa.Integer, nullable=False, server_default="1"),
        sa.Column("round_open", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("rounds", JSONB, nullable=False, server_default="[]"),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    def common():
        return [
            sa.Column("id", sa.String(26), primary_key=True),
            sa.Column(
                "share_id",
                sa.String(26),
                sa.ForeignKey("shares.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("round", sa.Integer, nullable=False),
            sa.Column("guest_id", sa.Text, nullable=False),
            sa.Column("guest_name", sa.Text, nullable=False, server_default=""),
            # no foreign key: feedback about a purged item is kept (and stays
            # in feedback.json, so verify doesn't flag it)
            sa.Column("item_id", sa.String(26), nullable=False),
            sa.Column("item_sha256", sa.Text, nullable=False, server_default=""),
            sa.Column("item_version", sa.Integer, nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("accepted_batch", sa.Text, nullable=True),
        ]

    op.create_table(
        "proof_marks",
        *common(),
        sa.Column("verdict", sa.Text, nullable=True),  # pick | reject
        sa.Column("heart", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("accepted_as", sa.Text, nullable=True),
        sa.UniqueConstraint(
            "share_id", "round", "guest_id", "item_id", name="uq_proof_marks_guest_item"
        ),
        sa.CheckConstraint(
            "verdict IS NULL OR verdict IN ('pick', 'reject')", name="ck_proof_marks_verdict"
        ),
    )
    op.create_table(
        "proof_comments",
        *common(),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("annotation_id", sa.String(26), nullable=True),
    )
    for table in ("proof_marks", "proof_comments"):
        op.create_index(f"ix_{table}_share_round", table, ["share_id", "round"])
        op.create_index(f"ix_{table}_item", table, ["item_id"])
    op.create_index("ix_proof_marks_updated", "proof_marks", ["updated_at"])
    op.create_index("ix_proof_comments_created", "proof_comments", ["created_at"])


def downgrade() -> None:
    op.drop_table("proof_comments")
    op.drop_table("proof_marks")
    op.drop_table("share_proofing")
