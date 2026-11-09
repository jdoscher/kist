"""explicit states: item preview status, job lease timestamps, status checks

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-09

"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column("preview_status", sa.Text(), nullable=False, server_default="pending"),
    )
    op.add_column("items", sa.Column("preview_error", sa.Text(), nullable=True))
    # backfill from today's data: a known width means thumbnails were made;
    # files that never get a preview ("other") are none; the rest failed
    op.execute(
        """
        UPDATE items SET preview_status = CASE
            WHEN width IS NOT NULL THEN 'ready'
            WHEN mime LIKE 'image/%' OR mime LIKE 'video/%'
                 OR mime = 'application/pdf'
                 OR ext IN ('pdf', 'mp4', 'mov', 'webm') THEN 'failed'
            ELSE 'none'
        END
        """
    )
    op.create_check_constraint(
        "ck_items_preview_status",
        "items",
        "preview_status IN ('pending', 'ready', 'failed', 'none')",
    )

    op.add_column("jobs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE jobs SET status = 'failed' WHERE status NOT IN "
               "('pending', 'running', 'done', 'failed')")
    # a job left running by a dead worker before this release: try again
    op.execute("UPDATE jobs SET status = 'pending' WHERE status = 'running'")
    op.create_check_constraint(
        "ck_jobs_status", "jobs", "status IN ('pending', 'running', 'done', 'failed')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_jobs_status", "jobs", type_="check")
    op.drop_column("jobs", "finished_at")
    op.drop_column("jobs", "started_at")
    op.drop_constraint("ck_items_preview_status", "items", type_="check")
    op.drop_column("items", "preview_error")
    op.drop_column("items", "preview_status")
