"""Library-wide reads shared by REST and MCP: stats and near-duplicates."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import BIT
from sqlalchemy.orm import Session, aliased

from ..lifecycle import JobStatus, PreviewStatus
from ..models import Folder, Item, Job, Tag
from ..schemas import StatsOut
from .search import KIND_FILTERS

DUPLICATES_MAX_LIMIT = 500
DUPLICATES_MAX_THRESHOLD = 20


def stats(session: Session) -> StatsOut:
    """Counts for the sidebar and agents, in one round trip."""
    live = Item.deleted_at.is_(None)

    def count(*conds):
        return sa.func.count().filter(*conds)

    def scalar(stmt):
        return stmt.scalar_subquery()

    row = session.execute(
        sa.select(
            count(live).label("total"),
            sa.func.coalesce(sa.func.sum(Item.size_bytes).filter(live), 0).label("size"),
            count(Item.deleted_at.isnot(None)).label("trashed"),
            count(live, Item.preview_status == PreviewStatus.FAILED).label("preview_failed"),
            *(count(live, cond).label(f"kind_{kind}") for kind, cond in KIND_FILTERS.items()),
            scalar(sa.select(sa.func.count()).select_from(Tag)).label("tags"),
            scalar(sa.select(sa.func.count()).select_from(Folder)).label("folders"),
            scalar(
                sa.select(sa.func.count()).select_from(Job).where(Job.status == JobStatus.FAILED)
            ).label("failed_jobs"),
        ).select_from(Item)
    ).one()
    return StatsOut(
        total_items=row.total,
        trashed_items=row.trashed,
        total_size_bytes=int(row.size),
        by_kind={kind: getattr(row, f"kind_{kind}") for kind in KIND_FILTERS},
        tag_count=row.tags,
        folder_count=row.folders,
        failed_jobs=row.failed_jobs,
        preview_failed=row.preview_failed,
    )


def near_duplicates(
    session: Session, threshold: int = 6, limit: int = 100
) -> list[tuple[Item, Item, int]]:
    """Near-duplicate pairs by perceptual-hash Hamming distance, closest
    first. Exact (sha256) duplicates can't exist: ingest rejects them. The
    self-join is fine for libraries in the tens of thousands; larger ones
    would want LSH bucketing."""
    threshold = max(0, min(int(threshold), DUPLICATES_MAX_THRESHOLD))
    limit = max(1, min(int(limit), DUPLICATES_MAX_LIMIT))
    a, b = aliased(Item, name="a"), aliased(Item, name="b")
    distance = sa.func.bit_count(sa.cast(a.phash.op("#")(b.phash), BIT(64))).label("distance")
    rows = session.execute(
        sa.select(a, b, distance)
        .where(
            a.id < b.id,
            a.phash.isnot(None),
            b.phash.isnot(None),
            a.deleted_at.is_(None),
            b.deleted_at.is_(None),
            distance <= threshold,
        )
        .order_by(distance, a.id, b.id)
        .limit(limit)
    ).all()
    return [(ra, rb, int(d)) for ra, rb, d in rows]
