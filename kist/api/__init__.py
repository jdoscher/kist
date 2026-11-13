from fastapi import APIRouter

from . import (
    ai,
    boards,
    creative,
    folders,
    history,
    items,
    jobs,
    m4,
    misc,
    organise,
    search,
    shares,  # M4
    smart_folders,
    tags,
    tokens,
)
from . import buckets as buckets_routes  # M6
from . import credits as credits_routes  # F4
from . import kinds as kinds_routes  # F1
from . import media as media_routes

api_router = APIRouter()
api_router.include_router(misc.router)
# M6: before items, so /items/buckets isn't read as an item id
api_router.include_router(buckets_routes.router)
api_router.include_router(items.router)
api_router.include_router(folders.router)
api_router.include_router(smart_folders.router)
api_router.include_router(tags.router)
api_router.include_router(jobs.router)
api_router.include_router(tokens.router)
api_router.include_router(history.router)
api_router.include_router(ai.router)
api_router.include_router(search.router)
api_router.include_router(organise.router)
api_router.include_router(boards.router)
api_router.include_router(creative.router)
api_router.include_router(shares.router)
api_router.include_router(m4.router)
api_router.include_router(media_routes.router)  # Milestone 5
api_router.include_router(credits_routes.router)  # F4 credits & rights
api_router.include_router(kinds_routes.router)  # F1 utility collections
