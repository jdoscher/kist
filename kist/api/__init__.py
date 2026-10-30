from fastapi import APIRouter

from . import folders, items, misc, smart_folders, tags

api_router = APIRouter()
api_router.include_router(misc.router)
api_router.include_router(items.router)
api_router.include_router(folders.router)
api_router.include_router(smart_folders.router)
api_router.include_router(tags.router)
