"""ratings stay 0-5 in the database too (rebuild-index trusts meta.json)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-09

"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE items SET rating = LEAST(GREATEST(rating, 0), 5) "
        "WHERE rating NOT BETWEEN 0 AND 5"
    )
    op.create_check_constraint("ck_items_rating", "items", "rating BETWEEN 0 AND 5")


def downgrade() -> None:
    op.drop_constraint("ck_items_rating", "items", type_="check")
