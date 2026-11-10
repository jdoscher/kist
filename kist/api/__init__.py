from fastapi import APIRouter

from . import ai, folders, history, items, jobs, misc, smart_folders, tags, tokens
from . import media as media_routes

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
api_router.include_router(media_routes.router)  # Milestone 5
