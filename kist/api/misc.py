import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_session
from ..observability import RateLimiter, request_id_var
from ..schemas import ClientErrorIn, DuplicatePair, ItemOut, StatsOut, VersionOut
from ..services import health as health_svc
from ..services import library

router = APIRouter()
client_log = logging.getLogger("kist.client")
# a page stuck in a crash loop mustn't flood the logs
client_errors = RateLimiter(limit=30, window=60)


@router.get("/health")
def health(session: Annotated[Session, Depends(get_session)]) -> dict:
    """Public and cheap (Docker's healthcheck): the database answers."""
    session.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/health/deep")
def health_deep(session: Annotated[Session, Depends(get_session)]) -> dict:
    """Every part: database, migrations, worker heartbeat, library disk,
    job queue, recent preview failures and the last verify run."""
    return health_svc.deep(session)


@router.get("/version", response_model=VersionOut)
def version():
    return health_svc.version_info()


@router.post("/client-errors", status_code=204)
def client_error(body: ClientErrorIn) -> Response:
    """The web UI reports its crashes here; they land in the server log."""
    if client_errors.allow():
        client_log.warning(
            "client %s: %s | url=%s ua=%s rid=%s\n%s",
            body.kind or "error", body.message, (body.url or "-")[:2000],
            (body.user_agent or "-")[:500],
            request_id_var.get(), body.stack or "",
        )
    return Response(status_code=204)


@router.get("/stats", response_model=StatsOut)
def stats(session: Annotated[Session, Depends(get_session)]):
    return library.stats(session)


@router.get("/duplicates", response_model=list[DuplicatePair])
def duplicates(
    session: Annotated[Session, Depends(get_session)],
    threshold: Annotated[int, Query(ge=0, le=library.DUPLICATES_MAX_THRESHOLD)] = 6,
    limit: Annotated[int, Query(ge=1, le=library.DUPLICATES_MAX_LIMIT)] = 100,
):
    """Near-duplicate pairs by perceptual-hash Hamming distance."""
    return [
        DuplicatePair(a=ItemOut.from_item(a), b=ItemOut.from_item(b), distance=d)
        for a, b, d in library.near_duplicates(session, threshold, limit)
    ]
