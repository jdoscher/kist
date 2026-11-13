"""`kist kinds-backfill` (F1 utility collections).

Kept out of cli.py so other features' commands don't collide; cli.main
registers it with add_parsers() and runs it with run().
"""

from .db import SessionLocal

COMMANDS = ("kinds-backfill",)


def add_parsers(sub) -> None:
    p = sub.add_parser(
        "kinds-backfill",
        help="sort items into kinds (Screenshots, Drawings ...) where that is missing or out "
             "of date; only does the missing work unless --all",
    )
    p.add_argument("--limit", type=int, default=None, help="at most this many items")
    p.add_argument("--all", action="store_true", help="re-sort every active item")


def run(args) -> int:
    from .config import get_settings
    from .services import kinds

    if not get_settings().kinds:
        print("kinds are switched off (KIST_KINDS=false): nothing to do")
        return 0
    with SessionLocal() as session:
        retrained = kinds.retrain(session)
        ctx = kinds._Context(session)
        if ctx.scorer is None and any(k.prompts for k in ctx.kinds):
            print("! the ml service can't embed the kinds' prompts now: only the rules ran "
                  "(Screenshots by size and name, Documents from OCR, Photos, Low-res); run "
                  "this again when it's up")
        ids = kinds.stale_ids(session, ctx, args.limit, args.all)
        total = len(ids)
        done = 0
        for start in range(0, total, 1000):
            done += kinds.assign(session, ids[start:start + 1000], ctx)
            print(f"  {done}/{total} items sorted")
    if retrained:
        print(f"learned from corrections: {', '.join(retrained)}")
    print(f"kinds: {done} items sorted" if done else "kinds: nothing to do")
    return 0
