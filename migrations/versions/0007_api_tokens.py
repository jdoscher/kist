"""named, scoped API tokens

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-09

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("token_sha256", sa.String(64), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("folder_ids", postgresql.ARRAY(sa.Text()), nullable=False,
                  server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ux_api_tokens_token_sha256", "api_tokens", ["token_sha256"], unique=True)


def downgrade() -> None:
    op.drop_table("api_tokens")
