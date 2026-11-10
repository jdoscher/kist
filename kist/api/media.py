"""Milestone 5 routes: video frames, clips and proxies, waveforms, SVG,
3D posters, fonts, bundles, the timeline and the map.

Errors follow the rest of the API (handlers in kist/app.py):
items_svc.NotFound -> 404, ValidationError -> 422, TransitionError -> 409.
"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from ..db import get_session
from ..filetypes import kind_for
from ..schemas import ItemCreateResponse, ItemOut, JobOut
from ..services import bundles, fonts, geo, media, search, timeline, video
from ..services import items as items_svc
from ..services import jobs as jobs_svc
from .items import CACHE_FOREVER, CACHE_REVALIDATE, search_filters

router = APIRouter(tags=["media"])

SANDBOX = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"}


class CoverIn(BaseModel):
    t: float | None = Field(default=None, ge=0)  # None: back to the default frame


class FrameIn(BaseModel):
    t: float = Field(ge=0)


class ClipIn(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    format: Literal["mp4", "gif", "webp", "mp3", "wav"] = "mp4"
    max_side: int | None = Field(default=None, ge=16, le=8192)


def _item_dir(item):
    return media.item_paths(item)


def _file(path, media_type: str, *, v: int | None = None, rev: int = 0, headers=None):
    if not path.is_file():
        raise HTTPException(404, "not made yet for this item")
    cache = CACHE_FOREVER if v is not None and v == rev else CACHE_REVALIDATE
    return FileResponse(path, media_type=media_type,
                        headers={"Cache-Control": cache, **SANDBOX, **(headers or {})})


# ---------- video ----------


@router.get("/items/{item_id}/scrub")
def get_scrub(item_id: str, session: Annotated[Session, Depends(get_session)],
              v: int | None = None):
    """The scrub sprite (WebP): scrub.json says how it's laid out."""
    item = items_svc.get_item(session, item_id)
    item_dir, _ = _item_dir(item)
    return _file(item_dir / "scrub.webp", "image/webp", v=v, rev=item.preview_rev or 0)


@router.get("/items/{item_id}/scrub.json")
def get_scrub_info(item_id: str, session: Annotated[Session, Depends(get_session)]):
    """{frames, cols, rows, frame_w, frame_h, times[]} for the sprite."""
    item = items_svc.get_item(session, item_id)
    item_dir, _ = _item_dir(item)
    info = video.scrub_info(item_dir)
    if info is None:
        raise HTTPException(404, "this item has no scrub sprite")
    return info


@router.post("/items/{item_id}/cover", response_model=ItemOut)
def set_cover(item_id: str, body: CoverIn, session: Annotated[Session, Depends(get_session)]):
    """Use the frame at t seconds as the video's thumbnail; the thumbnails
    are made again from it (t null: back to the default frame)."""
    return ItemOut.from_item(media.set_cover(session, item_id, body.t))


@router.post("/items/{item_id}/frames", response_model=ItemCreateResponse, status_code=201)
def save_frame(item_id: str, body: FrameIn, session: Annotated[Session, Depends(get_session)]):
    """Save the frame at t seconds (full resolution, from the original) as a
    new PNG item, linked derived_from the video, with its tags and folders."""
    result = media.save_frame(session, item_id, body.t)
    return ItemCreateResponse(item=ItemOut.from_item(result.item), created=result.created)


@router.post("/items/{item_id}/clip", response_model=JobOut, status_code=202)
def make_clip(item_id: str, body: ClipIn, session: Annotated[Session, Depends(get_session)]):
    """Cut start..end into a new item (mp4/gif/webp from video, mp3/wav from
    audio) as a worker job; the job's result_item_id is the new item."""
    job = media.queue_clip(session, item_id, body.start, body.end, body.format, body.max_side)
    return jobs_svc.job_out(job)


@router.get("/items/{item_id}/stream")
def stream(item_id: str, session: Annotated[Session, Depends(get_session)]):
    """Something the browser can play, with HTTP Range: the original when
    it plays natively, else the web-safe proxy. A missing proxy is queued
    (202 {status: preparing, job}); poll until this answers 200."""
    item = items_svc.get_item(session, item_id)
    if kind_for(item.mime, item.ext) not in ("video", "audio"):
        raise HTTPException(404, "only video and audio can be streamed")
    state = media.proxy_state(item)
    item_dir, original = _item_dir(item)
    headers = {"Cache-Control": CACHE_FOREVER, **SANDBOX, "Accept-Ranges": "bytes"}
    if state["playable"] == "native":
        if not original.is_file():
            raise HTTPException(404, "original file missing on disk")
        return FileResponse(original, media_type=item.mime, headers=headers)
    if state["path"] is not None:
        mt = "video/mp4" if state["path"].suffix == ".mp4" else "audio/mp4"
        return FileResponse(state["path"], media_type=mt,
                            headers={**headers, "Cache-Control": CACHE_REVALIDATE})
    if state["playable"] == "none":
        raise HTTPException(404, "nothing playable in this file")
    job = media.queue_proxy(session, item)
    return JSONResponse({"status": "preparing", "job": jobs_svc.job_out(job).model_dump(
        mode="json")}, status_code=202)


@router.post("/items/{item_id}/proxy", status_code=202)
def request_proxy(item_id: str, session: Annotated[Session, Depends(get_session)]):
    """Queue (or re-make) the web-safe proxy of a video or audio file."""
    item = items_svc.get_item(session, item_id)
    if kind_for(item.mime, item.ext) not in ("video", "audio"):
        raise HTTPException(409, "only video and audio get proxies")
    job = media.queue_proxy(session, item)
    return {"status": "queued", "job": jobs_svc.job_out(job).model_dump(mode="json")}


# ---------- audio ----------


@router.get("/items/{item_id}/waveform")
def waveform(item_id: str, session: Annotated[Session, Depends(get_session)]):
    """Peaks in the BBC audiowaveform JSON format."""
    item = items_svc.get_item(session, item_id)
    item_dir, _ = _item_dir(item)
    path = item_dir / "waveform.json"
    if not path.is_file():
        raise HTTPException(404, "this item has no waveform")
    return Response(path.read_bytes(), media_type="application/json",
                    headers={"Cache-Control": CACHE_REVALIDATE})


# ---------- SVG ----------


@router.get("/items/{item_id}/svg")
def svg_text(item_id: str, session: Annotated[Session, Depends(get_session)]):
    """The sanitized SVG ("Copy SVG"): no script, no external references."""
    item = items_svc.get_item(session, item_id)
    text = media.sanitized_svg(item)
    return Response(text, media_type="image/svg+xml", headers={
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                   "img-src data:; sandbox",
        "Cache-Control": CACHE_REVALIDATE,
    })


# ---------- 3D ----------

POSTER_LIMIT = media.POSTER_MAX_BYTES


@router.post("/items/{item_id}/poster", response_model=ItemOut)
async def upload_poster(item_id: str, request: Request,
                        session: Annotated[Session, Depends(get_session)]):
    """A poster image (PNG/JPEG/WebP) for a 3D model, a design file without
    a preview, or a bundle: multipart field 'file', or the image as the body."""
    from starlette.concurrency import run_in_threadpool

    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > POSTER_LIMIT + 64 * 1024:
        raise HTTPException(413, "the poster image is too large (25 MB at most)")
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/"):
        form = await request.form()
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise HTTPException(422, "multipart field 'file' is required")
        data = await file.read(POSTER_LIMIT + 1)
    else:
        data = b""
        async for chunk in request.stream():
            data += chunk
            if len(data) > POSTER_LIMIT:
                break
    item = await run_in_threadpool(media.set_poster, session, item_id, data)
    return ItemOut.from_item(item)


@router.get("/items/{item_id}/model")
def model_file(item_id: str, session: Annotated[Session, Depends(get_session)]):
    """The model for <model-viewer>: model.glb when OBJ/STL/PLY was
    converted (needs trimesh), else a GLB/glTF original."""
    item = items_svc.get_item(session, item_id)
    item_dir, original = _item_dir(item)
    converted = item_dir / "model.glb"
    if converted.is_file():
        return FileResponse(converted, media_type="model/gltf-binary",
                            headers={"Cache-Control": CACHE_REVALIDATE, **SANDBOX})
    if item.mime in ("model/gltf-binary", "model/gltf+json", "model/vnd.usdz+zip"):
        return FileResponse(original, media_type=item.mime,
                            headers={"Cache-Control": CACHE_FOREVER, **SANDBOX})
    raise HTTPException(404, "no web-viewable model for this item")


# ---------- fonts ----------


def _font_path(session: Session, item_id: str):
    item = items_svc.get_item(session, item_id)
    if kind_for(item.mime, item.ext) != "font":
        raise HTTPException(409, "this item isn't a font")
    _dir, original = _item_dir(item)
    return original


@router.get("/items/{item_id}/font-info")
def font_info(item_id: str, session: Annotated[Session, Depends(get_session)]):
    """Family, style, glyph count, variable axes and supported scripts."""
    try:
        return fonts.info(_font_path(session, item_id))
    except fonts.FontError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/items/{item_id}/glyphs")
def font_glyphs(item_id: str, session: Annotated[Session, Depends(get_session)],
                page: Annotated[int, Query(ge=0, le=100000)] = 0):
    """The font's codepoints, 256 per page."""
    try:
        return fonts.glyphs(_font_path(session, item_id), page)
    except fonts.FontError as exc:
        raise HTTPException(422, str(exc)) from None


# ---------- bundles ----------


def _bundle(session: Session, item_id: str):
    item = items_svc.get_item(session, item_id)
    if kind_for(item.mime, item.ext) != "bundle":
        raise HTTPException(409, "this item isn't a bundle")
    return item, *_item_dir(item)


@router.get("/items/{item_id}/bundle")
def bundle_manifest(item_id: str, session: Annotated[Session, Depends(get_session)]):
    """What's inside the zip."""
    _item, item_dir, original = _bundle(session, item_id)
    try:
        return bundles.load_manifest(item_dir, original)
    except bundles.BundleError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/items/{item_id}/bundle/{path:path}")
def bundle_file(item_id: str, path: str, session: Annotated[Session, Depends(get_session)]):
    """One file from inside the bundle. Paths that try to leave it (.., /,
    backslashes) are refused."""
    _item, _dir, original = _bundle(session, item_id)
    try:
        data, mime = bundles.read_entry(original, path)
    except bundles.BundleError as exc:
        raise HTTPException(400, str(exc)) from None
    except FileNotFoundError:
        raise HTTPException(404, "no such file in this bundle") from None
    inline = mime in bundles.INLINE_MIMES
    name = path.rsplit("/", 1)[-1].replace('"', "")
    return Response(data, media_type=mime if inline else "application/octet-stream", headers={
        **SANDBOX,
        "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{name}"',
        "Cache-Control": CACHE_FOREVER,
    })


# ---------- timeline and map ----------


def common_filters(params: Annotated[dict, Depends(search_filters)]) -> list:
    """Every GET /api/items filter (M2 organise, M3 flags/labels, M4
    provenance, M5 media, person, text= ...), through search.build_filters
    like every listing; ranking parameters are ignored."""
    return search.filters_from_params(params)


@router.get("/timeline")
def get_timeline(session: Annotated[Session, Depends(get_session)],
                 filters: Annotated[list, Depends(common_filters)],
                 group: Literal["day", "month", "year"] = "month"):
    """[{key, count, first_item_id}], newest first, by date taken (else the
    date added). The same filters as GET /api/items."""
    return timeline.buckets(session, group, filters)


@router.get("/on-this-day")
def get_on_this_day(session: Annotated[Session, Depends(get_session)],
                    filters: Annotated[list, Depends(common_filters)],
                    day: date | None = None,
                    per_year: Annotated[int, Query(ge=1, le=100)] = 12):
    """Items taken on this date in earlier years, grouped by year."""
    data = timeline.on_this_day(session, filters, day, per_year)
    for year in data["years"]:
        year["items"] = [ItemOut.from_item(i).model_dump(mode="json") for i in year["items"]]
    return data


@router.get("/map")
def get_map(session: Annotated[Session, Depends(get_session)],
            filters: Annotated[list, Depends(common_filters)],
            bbox: str | None = None,
            zoom: Annotated[int | None, Query(ge=0, le=22)] = None):
    """Items with GPS, clustered on a grid (cell = 64 px at this zoom).
    bbox is west,south,east,north. A cluster of one has that item's id."""
    box = timeline.parse_bbox(bbox)
    return timeline.clusters(session, filters, box, zoom if zoom is not None
                             else timeline.zoom_for(box))


def _basemap():
    from ..config import get_settings

    return get_settings().library_dir / "maps" / "basemap.pmtiles"


@router.get("/map/config")
def map_config():
    """Tile source for the web map (KIST_MAP_TILES_URL, else the self-hosted
    library/maps/basemap.pmtiles), and whether place names are available."""
    from ..config import get_settings

    s = get_settings()
    url = s.map_tiles_url or ("/api/map/basemap.pmtiles" if _basemap().is_file() else None)
    return {"tiles_url": url, "attribution": s.map_attribution or (
        "© OpenStreetMap contributors, Protomaps" if not s.map_tiles_url and url else None),
        "place_names": geo.available()}


@router.get("/map/basemap.pmtiles")
def basemap():
    """The self-hosted Protomaps file (docs/maps.md), with Range."""
    path = _basemap()
    if not path.is_file():
        raise HTTPException(404, "no basemap: see docs/maps.md")
    return FileResponse(path, media_type="application/vnd.pmtiles",
                        headers={"Cache-Control": "private, max-age=86400", **SANDBOX})
