"""`kist media-backfill` and `kist geo-download` (Milestone 5).

Kept out of cli.py so the other milestones' commands don't collide with
these; cli.main registers them with add_parsers() and runs them with run().
"""

import sys
from pathlib import Path

from .db import SessionLocal

COMMANDS = ("media-backfill", "geo-download")


def add_parsers(sub) -> None:
    p = sub.add_parser(
        "media-backfill",
        help="read media metadata (EXIF, ffprobe ...) and make scrub sprites, waveforms and "
             "design/RAW previews for items added before Kist 2.0's media support",
    )
    p.add_argument("--limit", type=int, default=None, help="at most this many items")
    p.add_argument("--all", action="store_true",
                   help="every item, not only those without media metadata")
    g = sub.add_parser(
        "geo-download",
        help="download GeoNames' cities500 place names (about 10 MB, once) for offline "
             "reverse geocoding, then name the places of photos with GPS",
    )
    g.add_argument("--from", dest="source", default=None,
                   help="read cities500.zip/.txt, countryInfo.txt and admin1CodesASCII.txt "
                        "from this directory instead of downloading them")


def media_backfill(limit: int | None, all_items: bool) -> int:
    from .services import media

    def progress(item_id: str, what: str) -> None:
        mark = "!" if what == "failed" else "✓"
        print(f"  {mark} {item_id}: {what}")

    with SessionLocal() as session:
        counts = media.backfill(session, limit=limit, all_items=all_items, progress=progress)
        placed = media.geocode_missing(session)
    total = sum(counts.values())
    print(f"media: {total} items looked at ({', '.join(f'{n} {k}' for k, n in counts.items())})"
          if total else "media: nothing to do")
    if placed:
        print(f"places named: {placed}")
    return 2 if counts.get("failed") else 0


def geo_download(source: str | None) -> int:
    from .services import geo, media

    try:
        count = geo.download(Path(source) if source else None)
    except Exception as exc:
        print(f"geo-download failed: {exc}", file=sys.stderr)
        return 1
    print(f"places: {count} written to {geo.places_file()}")
    with SessionLocal() as session:
        placed = media.geocode_missing(session)
    print(f"photos and videos given a place name: {placed}")
    return 0


def run(args) -> int | None:
    if args.cmd == "media-backfill":
        return media_backfill(args.limit, args.all)
    if args.cmd == "geo-download":
        return geo_download(args.source)
    return None
