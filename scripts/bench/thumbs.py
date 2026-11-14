#!/usr/bin/env python3
"""Time thumbnail serving: GET /api/items/{id}/thumb at a given concurrency.

    python scripts/bench/thumbs.py --url http://localhost:8080 --token "$KIST_TOKEN" \\
        [--concurrency 30 --n 300 --sizes 256,512,1024 --runs 3 --out thumbs-20k]

For each size it lists items (GET /api/items, like the grid: stacks=collapse),
then fetches --n distinct thumbnails (?size=&v=<preview_rev>, the URLs the web
builds) from --concurrency threads, each with its own keep-alive connection
(what a browser does), and reports p50/p95/max latency, wall time,
throughput (req/s, MB/s) and the average size. Every request is a GET. It
refuses kist.example.com unless --read-only-prod is given (then it caps itself at
--n 300 and --concurrency 30, and runs once). Only the standard library.
"""

import argparse
import http.client
import json
import statistics
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

FORBIDDEN_HOSTS = {"kist.example.com"}


class Conn(threading.local):
    def __init__(self, url: str):
        u = urllib.parse.urlsplit(url)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        self.c = cls(u.hostname, u.port, timeout=60)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--url", required=True)
    p.add_argument("--token", required=True)
    p.add_argument("--concurrency", type=int, default=30)
    p.add_argument("--n", type=int, default=300)
    p.add_argument("--sizes", default="256,512,1024")
    p.add_argument("--runs", type=int, default=3, help="timed runs per size (best wall kept)")
    p.add_argument("--read-only-prod", action="store_true")
    p.add_argument("--out", help="write <out>.json with every run")
    args = p.parse_args(argv)
    host = urllib.parse.urlsplit(args.url).hostname or ""
    if host in FORBIDDEN_HOSTS:
        if not args.read_only_prod:
            print(f"refusing {host}: a real library (use --read-only-prod for GET-only, "
                  "capped timings)", file=sys.stderr)
            return 2
        args.n, args.concurrency, args.runs = min(args.n, 300), min(args.concurrency, 30), 1
    headers = {"Authorization": f"Bearer {args.token}", "Accept-Encoding": "identity"}
    local = Conn(args.url)
    base = urllib.parse.urlsplit(args.url).path.rstrip("/")

    def get(path: str) -> tuple[int, int, float]:
        t = time.perf_counter()
        for attempt in (0, 1):
            try:
                local.c.request("GET", base + path, headers=headers)
                r = local.c.getresponse()
                body = r.read()
                return r.status, len(body), time.perf_counter() - t
            except (http.client.HTTPException, OSError):
                local.c.close()
                if attempt:
                    raise
        raise AssertionError

    # the items, like the grid's list (several pages if --n asks for more)
    items, cursor = [], None
    while len(items) < args.n * len(args.sizes.split(",")):
        q = {"limit": 500, "stacks": "collapse"}
        if cursor:
            q["cursor"] = cursor
        local.c.request("GET", f"{base}/api/items?{urllib.parse.urlencode(q)}", headers=headers)
        r = local.c.getresponse()
        page = json.loads(r.read())
        if r.status != 200:
            print(f"list failed: {r.status} {page}", file=sys.stderr)
            return 1
        items += [i for i in page["items"] if i.get("has_thumb")]
        cursor = page.get("next_cursor")
        if not cursor:
            break
    report = []
    print(f"{args.url}: {len(items)} items with thumbnails, n={args.n}, "
          f"concurrency={args.concurrency}")
    print(f"{'size':>5} {'p50 ms':>7} {'p95 ms':>7} {'max ms':>7} {'wall s':>7} "
          f"{'req/s':>7} {'MB/s':>6} {'avg KB':>7} {'errors':>6}")
    for k, size in enumerate(int(s) for s in args.sizes.split(",")):
        # a different slice of items per size, so no size rides on another's page cache
        chosen = (items[k * args.n:(k + 1) * args.n] or items[:args.n])
        paths = [f"/api/items/{i['id']}/thumb?size={size}&v={i.get('preview_rev') or 0}"
                 for i in chosen]
        get(paths[0])  # warm-up (connection, imports)
        runs = []
        for _ in range(args.runs):
            t = time.perf_counter()
            with ThreadPoolExecutor(args.concurrency) as ex:
                res = list(ex.map(get, paths))
            wall = time.perf_counter() - t
            lat = sorted(r[2] for r in res)
            runs.append({
                "size": size, "n": len(res), "wall_s": wall,
                "p50_ms": statistics.median(lat) * 1000,
                "p95_ms": lat[max(0, int(len(lat) * 0.95) - 1)] * 1000,
                "max_ms": lat[-1] * 1000,
                "req_s": len(res) / wall,
                "mb_s": sum(r[1] for r in res) / wall / 1e6,
                "avg_kb": statistics.mean(r[1] for r in res) / 1024,
                "errors": sum(1 for r in res if r[0] != 200),
            })
        best = min(runs, key=lambda r: r["wall_s"])
        report.append({"best": best, "runs": runs})
        print(f"{size:>5} {best['p50_ms']:7.1f} {best['p95_ms']:7.1f} {best['max_ms']:7.1f} "
              f"{best['wall_s']:7.2f} {best['req_s']:7.0f} {best['mb_s']:6.1f} "
              f"{best['avg_kb']:7.1f} {best['errors']:>6}", flush=True)
    if args.out:
        with open(args.out + ".json", "w") as f:
            json.dump({"url": args.url, "n": args.n, "concurrency": args.concurrency,
                       "sizes": report}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
