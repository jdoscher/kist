from fastapi import APIRouter

from . import (
    ai,
    boards,
    creative,
    folders,
    history,
    items,
    jobs,
    misc,
    organise,
    search,
    smart_folders,
    tags,
    tokens,
)

api_router = APIRouter()
api_router.include_router(misc.router)
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
