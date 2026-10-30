from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import BIT
from sqlalchemy.orm import Session, aliased

from ..db import get_session
from ..models import Folder, Item, Tag
from ..schemas import DuplicatePair, ItemOut, StatsOut
from ..services.search import KIND_FILTERS

router = APIRouter()


@router.get("/health")
def health(session: Annotated[Session, Depends(get_session)]) -> dict:
    session.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/stats", response_model=StatsOut)
def stats(session: Annotated[Session, Depends(get_session)]):
    live = Item.deleted_at.is_(None)
    total, size = session.execute(
        sa.select(sa.func.count(), sa.func.coalesce(sa.func.sum(Item.size_bytes), 0))
        .where(live)
    ).one()
    trashed = session.execute(
        sa.select(sa.func.count()).where(Item.deleted_at.isnot(None))
    ).scalar_one()
    by_kind = {
        kind: session.execute(
            sa.select(sa.func.count()).where(live, cond)
        ).scalar_one()
        for kind, cond in KIND_FILTERS.items()
    }
    tag_count = session.execute(sa.select(sa.func.count()).select_from(Tag)).scalar_one()
    folder_count = session.execute(
        sa.select(sa.func.count()).select_from(Folder)
    ).scalar_one()
    return StatsOut(
        total_items=total,
        trashed_items=trashed,
        total_size_bytes=size,
        by_kind=by_kind,
        tag_count=tag_count,
        folder_count=folder_count,
    )


@router.get("/duplicates", response_model=list[DuplicatePair])
def duplicates(
    session: Annotated[Session, Depends(get_session)],
    threshold: Annotated[int, Query(ge=0, le=20)] = 6,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    """Near-duplicate pairs by perceptual-hash Hamming distance.

    Exact (sha256) duplicates can't exist: ingest rejects them. The
    self-join is fine for libraries in the tens of thousands; larger ones
    would want LSH bucketing.
    """
    a, b = aliased(Item, name="a"), aliased(Item, name="b")
    distance = sa.func.bit_count(
        sa.cast(a.phash.op("#")(b.phash), BIT(64))
    ).label("distance")
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
        .order_by(distance)
        .limit(limit)
    ).all()
    return [
        DuplicatePair(a=ItemOut.from_item(ra), b=ItemOut.from_item(rb), distance=d)
        for ra, rb, d in rows
    ]
