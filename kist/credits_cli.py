"""`kist credits-backfill` (F4): fill empty credit fields of existing items
from what Kist already has on disk.

Reads each item's original (EXIF, IPTC-IIM, XMP CreatorTool, PNG text
chunks) and its stored page snapshot (page/snapshot.html meta tags). It
never requests a page. Only empty fields are filled, and an item nobody
marked AI-generated either way gets the flag when a generator names itself
in the metadata. The run is one batch in the history (Recent changes →
Undo reverts it) and leaves modified_at alone. Safe to re-run.
"""

import sys

from .db import SessionLocal

COMMANDS = ("credits-backfill",)


def add_parsers(sub) -> None:
    p = sub.add_parser(
        "credits-backfill",
        help="fill empty credit, creator, licence and AI-made fields from each item's own "
             "metadata and stored page snapshot (no network)",
    )
    p.add_argument("--dry-run", action="store_true", help="only list what would be filled")
    p.add_argument("--limit", type=int, default=None, help="fill at most this many items")


def run(args) -> int:
    from .services import credits, events

    def progress(item_id: str, patch: dict) -> None:
        print(f"  {'·' if args.dry_run else '✓'} {item_id}: "
              + ", ".join(f"{k}={v!r}" for k, v in patch.items()))

    with events.acting("credits-backfill", "cli") as ctx, SessionLocal() as session:
        counts = credits.backfill(session, dry_run=args.dry_run, limit=args.limit,
                                  progress=progress)
    verb = "would fill" if args.dry_run else "filled"
    print(f"checked {counts['checked']}, {verb} {counts['filled']} items "
          f"({counts['fields']} fields, {counts['ai_flagged']} flagged AI-made)"
          + ("" if args.dry_run or not counts["filled"] else f"; batch {ctx.batch_id}"))
    sys.stdout.flush()
    return 0
