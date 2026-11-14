#!/usr/bin/env python3
"""Give a make_library.py library realistic thumbnails.

    python scripts/bench/thumb_pool.py --library /tmp/kist-bench-lib \\
        --pool ~/kist-perf-data/thumbpool [--set-dims]

make_library's thumbnails are one 64 px WebP, so thumbnail timings and
bytes say nothing about the real thing. A pool directory holds entries
`<name>/thumb_{256,512,1024}.webp` (real thumbnails, e.g. ~200 GET-fetched
from a library) plus an optional `manifest.json` ([{n, width, height}]).
Every item directory gets hard links to one entry (picked by item id), so
20k items cost inodes, not 20k copies. --set-dims also sets each item's
width/height to its entry's (KIST_DATABASE_URL / --database-url), so tile
shapes and the web's size picker see the real aspect ratios; it refuses a
database without make_library's "Smart 0" smart folder (a real library).
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

NAMES = ("thumb_256.webp", "thumb_512.webp", "thumb_1024.webp")


def entries(pool: Path) -> list[Path]:
    out = sorted(d for d in pool.iterdir() if d.is_dir() and all((d / n).is_file() for n in NAMES))
    if not out:
        raise SystemExit(f"no complete entries (thumb_256/512/1024.webp) in {pool}")
    return out


def pick(item_id: str, n: int) -> int:
    return int.from_bytes(hashlib.blake2b(item_id.encode(), digest_size=4).digest(), "big") % n


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--library", default=os.environ.get("KIST_LIBRARY_DIR"), required=False)
    p.add_argument("--pool", required=True)
    p.add_argument("--set-dims", action="store_true")
    p.add_argument("--database-url", default=os.environ.get("KIST_DATABASE_URL"))
    args = p.parse_args(argv)
    if not args.library:
        p.error("--library (or KIST_LIBRARY_DIR) is required")
    items_dir = Path(args.library) / "items"
    pool = entries(Path(args.pool).expanduser())
    dims = {}
    man = Path(args.pool).expanduser() / "manifest.json"
    if man.is_file():
        dims = {e["n"]: (e.get("width"), e.get("height")) for e in json.loads(man.read_text())}
    rows, done = [], 0
    for d in sorted(items_dir.iterdir()):
        if not d.is_dir():
            continue
        entry = pool[pick(d.name, len(pool))]
        for name in NAMES:
            target = d / name
            tmp = d / (name + ".pool")
            tmp.unlink(missing_ok=True)
            os.link(entry / name, tmp)
            os.replace(tmp, target)
        w, h = dims.get(entry.name, (None, None))
        if w and h:
            rows.append((w, h, d.name))
        done += 1
        if done % 5000 == 0:
            print(f"  linked {done}", flush=True)
    print(f"linked {done} item directories to {len(pool)} pool entries", flush=True)
    if args.set_dims:
        if not args.database_url:
            p.error("--set-dims needs --database-url or KIST_DATABASE_URL")
        import psycopg

        dsn = args.database_url.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(dsn) as conn:
            if not conn.execute("select 1 from smart_folders where name = 'Smart 0'").fetchone():
                print("refusing --set-dims: not a make_library.py database", file=sys.stderr)
                return 2
            with conn.cursor() as cur:
                cur.executemany("update items set width = %s, height = %s where id = %s", rows)
        print(f"set width/height of {len(rows)} items", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
