"""Serving the grid's preview files without a database round trip (perf P1).

The grid asks for one thumbnail per tile, hundreds a second while scrolling.
Each used to open a database session to load the item (and check its
preview_rev) before sending a file that sits at a fixed path anyway:
items_dir/<id>/thumb_<size>.webp. For every caller that may see every item
(owner, admin, write, agent, an unrestricted read token) the item id alone
names the file, so these routes answer from the file system. Only a
folder-limited reader (access.restricted()) still has the item loaded and
checked by items_svc.get_item (404 outside its folders and for trashed
items).
"""

import os
import re
import stat
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from .. import access
from ..config import get_settings

# Item ids are ULIDs (ids.new_ulid: 26 Crockford base-32 characters, the
# first 0-7). Checking the format first keeps any other path segment
# ("..", "a/b", "%2e%2e") from ever reaching the file system.
ITEM_ID = re.compile(r"[0-7][0-9A-HJKMNP-TV-Z]{25}")

# Previews change only when preview_rev does, and every URL the UI builds
# carries ?v=<preview_rev>, so a versioned request can be cached for good.
# Originals never change at all. Both are private: they sit behind the token.
CACHE_FOREVER = "private, max-age=31536000, immutable"
CACHE_REVALIDATE = "private, no-cache"


def item_dir(item_id: str) -> Path:
    """items_dir/<id>, for a well-formed item id; 404 otherwise."""
    if not ITEM_ID.fullmatch(item_id):
        raise HTTPException(404, "Not Found")
    return get_settings().items_dir / item_id


def _check_visible(item_id: str) -> None:
    from ..db import SessionLocal
    from ..services import items as items_svc

    with SessionLocal() as session:
        items_svc.get_item(session, item_id)  # NotFound -> 404


async def check_visible(item_id: str) -> None:
    """A folder-limited reader sees only live items in its folders: load
    the item and check (NotFound -> 404). Everyone else: nothing to do."""
    if access.restricted() is not None:
        await run_in_threadpool(_check_visible, item_id)


def regular_file(path: Path) -> os.stat_result | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st if stat.S_ISREG(st.st_mode) else None


def thumb_candidates(size: int) -> tuple[int, list[str]]:
    """(normalised size, files to try in order) for GET /thumb?size=."""
    from ..services import thumbs

    if size > 2048:
        return size, ["display.webp", thumbs.SCREEN_FILE, "thumb_1024.webp"]
    if size > 1024:
        return size, [thumbs.SCREEN_FILE, "display.webp", "thumb_1024.webp"]
    size = 256 if size <= 256 else 512 if size <= 512 else 1024
    return size, [f"thumb_{size}.webp"] + (["thumb_1024.webp"] if size == 512 else [])


def file_response(directory: Path, names: list[str], v: int | None,
                  missing: str = "no thumbnail for this item") -> FileResponse:
    """The first of `names` that exists in `directory`, as a FileResponse
    (for the AI helper's sync route; the grid's routes use send_file)."""
    found = find_file(directory, names)
    if found is None:
        raise HTTPException(404, missing)
    path, st, exact = found
    return FileResponse(path, media_type="image/webp", stat_result=st,
                        headers={"Cache-Control": cache_control(exact, v)})


def cache_control(exact: bool, v: int | None) -> str:
    """Cached for good only when the URL is versioned (?v=) and the requested
    file itself exists; a fallback copy (a smaller size standing in) is
    revalidated so the real one replaces it once made. The version isn't
    compared with the item's preview_rev (that would need the database):
    every preview rewrite (regenerate, backfills, a new cover frame) writes
    the files first and then bumps preview_rev, and the UI always builds
    the URL from the current preview_rev, so a new rev is a new URL. The
    worst an old or odd `v` can do is cache newer bytes under an older URL,
    which nothing asks for once the list has the new rev."""
    return CACHE_FOREVER if exact and v is not None else CACHE_REVALIDATE


def find_file(directory: Path, names: list[str]) -> tuple[Path, os.stat_result, bool] | None:
    """(path, stat, is_the_first_name) of the first of `names` that exists."""
    for n, name in enumerate(names):
        path = directory / name
        st = regular_file(path)
        if st is not None:
            return path, st, n == 0
    return None


# Files up to this size are read in one go on a worker thread and sent as
# one body. Starlette's FileResponse takes a thread hop each for open, every
# 64 KB chunk and close; for a 20-130 KB thumbnail that is 3-5 hops.
# Measured on 20k items with real thumbnails, 300 requests from 30
# connections (scripts/bench/thumbs.py): FileResponse 1,520-1,700 req/s,
# p95 20-34 ms; one read 2,190-2,240 req/s, p95 16-18 ms. Reading on the
# event loop itself was faster still at the median but p95 39-42 ms (and a
# cold disk would stall every request), so the read stays off the loop.
# Bigger files (the 4096 HEIC copy) keep streaming.
READ_WHOLE_MAX = 1024 * 1024


async def send_file(directory: Path, names: list[str], v: int | None,
                    missing: str = "no thumbnail for this item") -> Response:
    found = find_file(directory, names)
    if found is None:
        raise HTTPException(404, missing)
    path, st, exact = found
    headers = {"Cache-Control": cache_control(exact, v)}
    if st.st_size > READ_WHOLE_MAX:
        return FileResponse(path, media_type="image/webp", stat_result=st, headers=headers)
    try:
        body = await run_in_threadpool(path.read_bytes)
    except OSError:  # removed meanwhile (a regenerate, a purge)
        raise HTTPException(404, missing) from None
    return Response(body, media_type="image/webp", headers=headers)
