#!/usr/bin/env python3
"""Time Kist's read paths against a (synthetic) library (Milestone 6).

    python scripts/bench/run.py --url http://localhost:8080 --token "$KIST_TOKEN" \\
        --runs 20 --out bench-250k

Writes <out>.json (every timing) and <out>.md (a p50/p95 table), and
prints the table. GET requests only, but at full speed: NEVER point it at
a real library. It refuses kist.example.com outright and any library that
doesn't look like make_library.py's (no smart folder named "Smart 0")
unless --any-library is given.

What it times (each case: one warm-up, then --runs timed requests):
- first page and a deep page (cursor) for every list order
- GET /api/items/buckets for each date sort
- the count (first page of the newest-first list carries `total`), stats
- a popular and a rare tag, a folder subtree, the unfiled view, two smart
  folders' saved queries
- keyword search (q=), text in images (text=), colour search, more-like-this
  (similar=<item with an embedding>: the pgvector path; --semantic "words"
  adds a meaning search when the ml service runs)
- tags list, sidebar folders + smart folders, timeline (month, day), map
  clusters (world and one city)
- --grid: every list case again with fields=grid, the slim list the web grid
  asks for since the 2.0 performance work (cases named "... [grid]")
Only the standard library is used.
"""

import argparse
import base64
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

# Hosts these benchmarks refuse to load: kist.example.com plus any listed in
# KIST_BENCH_REFUSE_HOSTS (comma-separated), e.g. your own production library.
FORBIDDEN_HOSTS = {"kist.example.com"} | {
    h.strip().lower() for h in os.environ.get("KIST_BENCH_REFUSE_HOSTS", "").split(",") if h.strip()
}
ORDERS = ["-created", "created", "-modified", "modified", "-taken", "taken",
          "filename", "-size", "-rating"]
DATE_SORTS = [("created", "desc"), ("modified", "desc"), ("taken", "desc"), ("taken", "asc")]


class Client:
    def __init__(self, url: str, token: str, timeout: float):
        self.base = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
                        "Accept-Encoding": "identity"}
        self.timeout = timeout

    def get(self, path: str, params: dict | list | None = None) -> tuple[int, bytes, float]:
        query = urllib.parse.urlencode(params or {}, doseq=True)
        req = urllib.request.Request(f"{self.base}{path}{'?' + query if query else ''}",
                                     headers=self.headers)
        t = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                body = res.read()
                status = res.status
        except urllib.error.HTTPError as exc:
            body, status = exc.read(), exc.code
        return status, body, (time.perf_counter() - t) * 1000

    def json(self, path: str, params=None):
        status, body, _ = self.get(path, params)
        if status != 200:
            raise RuntimeError(f"GET {path} {params or ''}: {status} {body[:200]!r}")
        return json.loads(body)


# Crockford base32 ULIDs use 0-9 and A-Z: "Z" * 26 sorts after every one in
# the C and en_US collations alike
ID_AFTER_ALL = "Z" * 26


def crafted_cursor(value, item_id: str) -> str:
    """A keyset cursor (services/search.encode_cursor's format) that starts
    mid-list for a non-date order: [value, id]."""
    raw = json.dumps([value, item_id]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def pct(values: list[float], p: float) -> float:
    """Nearest-rank percentile."""
    import math

    s = sorted(values)
    return s[max(0, min(len(s) - 1, math.ceil(p / 100 * len(s)) - 1))]


def build_cases(c: Client, args) -> list[tuple[str, str, dict | list]]:
    cases: list[tuple[str, str, dict | list]] = []
    limit = args.limit

    for order in ORDERS:
        cases.append((f"items first page order={order}", "/api/items",
                      {"order": order, "limit": limit}))

    # deep pages: a middle month's bucket cursor for the date orders (that's
    # the scrubber jump), a crafted mid-list cursor for the others
    for sort, direction in DATE_SORTS:
        cases.append((f"buckets sort={sort} {direction}", "/api/items/buckets",
                      {"sort": sort, "order": direction}))
    for order in ORDERS:
        sort = order.lstrip("-")
        direction = "desc" if order.startswith("-") else "asc"
        if sort in ("created", "modified", "taken"):
            buckets = c.json("/api/items/buckets", {"sort": sort, "order": direction})["buckets"]
            if not buckets:
                continue
            cursor = buckets[len(buckets) // 2]["cursor"]
        else:
            # an id past every ULID (desc) or before every one (asc). Not
            # "~": in a database with an en_US collation (the official
            # postgres image's default) punctuation sorts before letters
            # and digits, so (rating = 0 AND id < '~') matched nothing and
            # the -rating deep page came back empty (44 bytes)
            last = ID_AFTER_ALL if direction == "desc" else ""
            value = {"filename": "m", "size": 5_000_000, "rating": 0}[sort]
            cursor = crafted_cursor(value, last)
        cases.append((f"items deep page order={order}", "/api/items",
                      {"order": order, "limit": limit, "cursor": cursor}))

    tags = c.json("/api/tags")
    tags.sort(key=lambda t: t.get("count", 0), reverse=True)
    if tags:
        popular, rare = tags[0]["name"], tags[min(len(tags) - 1, len(tags) // 2)]["name"]
        cases.append(("tag filter (most used)", "/api/items", {"tags": popular, "limit": limit}))
        cases.append(("tag filter (median tag)", "/api/items", {"tags": rare, "limit": limit}))
        cases.append(("tags_any (top 3)", "/api/items",
                      {"tags_any": ",".join(t["name"] for t in tags[:3]), "limit": limit}))

    folders = c.json("/api/folders")
    roots = [f for f in folders if not f.get("parent_id")]
    if roots:
        children = {f["id"]: 0 for f in folders}
        for f in folders:
            if f.get("parent_id") in children:
                children[f["parent_id"]] += 1
        root = max(roots, key=lambda f: children.get(f["id"], 0))
        cases.append(("folder filter (subtree)", "/api/items", {"folder": root["id"],
                                                                 "limit": limit}))
    cases.append(("unfiled view", "/api/items", {"view": "unfiled", "limit": limit}))

    smart = c.json("/api/smart-folders")
    seen = set()
    for sf in smart:
        q = dict(sf.get("query") or {})
        kind = "+".join(sorted(k for k, v in q.items()
                               if v not in (None, "", [], False, 0) and k != "order"))
        if not kind or kind in seen:
            continue
        seen.add(kind)
        params: list[tuple[str, str]] = [("limit", str(limit))]
        for k, v in q.items():
            if v in (None, "", [], False, 0) or k == "preview":
                continue
            if k == "tags":
                params += [("tags[]", t) for t in v]
            elif k == "tags_any":
                params.append(("tags_any", ",".join(v)))
            else:
                params.append((k, str(v).lower() if isinstance(v, bool) else str(v)))
        cases.append((f"smart folder ({kind})", "/api/items", params))
        if len(seen) >= 3:
            break

    cases.append(("count (first page total)", "/api/items", {"limit": 1}))
    cases.append(("keyword search q=", "/api/items", {"q": args.word, "limit": limit}))
    cases.append(("text in images text=", "/api/items", {"text": args.word, "limit": limit}))
    cases.append(("colour search", "/api/items", {"color": args.color, "limit": limit}))

    # more like this: the first recent image with an embedding
    page = c.json("/api/items", {"type": "image", "limit": 60})
    for item in page["items"]:
        status, _, _ = c.get("/api/items", {"similar": item["id"], "limit": 1})
        if status == 200:
            cases.append(("similar (vector search)", "/api/items",
                          {"similar": item["id"], "limit": limit}))
            break
    if args.semantic:
        cases.append(("semantic (ml + vector)", "/api/items",
                      {"semantic": args.semantic, "limit": limit}))

    cases += [
        ("stats", "/api/stats", {}),
        ("tags list", "/api/tags", {}),
        ("sidebar folders", "/api/folders", {}),
        ("sidebar smart folders", "/api/smart-folders", {}),
        ("timeline month", "/api/timeline", {"group": "month"}),
        ("timeline day", "/api/timeline", {"group": "day"}),
        ("map clusters (world)", "/api/map", {"zoom": 2}),
        ("map clusters (Paris, z11)", "/api/map", {"bbox": "2.2,48.8,2.5,48.95", "zoom": 11}),
    ]
    return cases


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--url", required=True)
    p.add_argument("--token", default=os.environ.get("KIST_BENCH_TOKEN")
                   or os.environ.get("KIST_TOKEN"))
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--limit", type=int, default=100, help="page size (the grid's)")
    p.add_argument("--word", default="invoice", help="keyword / text search word")
    p.add_argument("--color", default="#3366cc")
    p.add_argument("--semantic", default=None, help="also time a meaning search (needs ml)")
    p.add_argument("--no-stacks", action="store_true",
                   help="don't send stacks=collapse (the web grid sends it outside the trash)")
    p.add_argument("--only", default=None, help="only cases whose name contains this")
    p.add_argument("--grid", action="store_true",
                   help="also time every /api/items list case with fields=grid "
                        "(the web grid's slim list)")
    p.add_argument("--out", default="bench", help="writes <out>.json and <out>.md")
    p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--any-library", action="store_true",
                   help="skip the synthetic-library check (still never prod)")
    args = p.parse_args(argv)
    if not args.token:
        p.error("pass --token or set KIST_BENCH_TOKEN")
    host = urllib.parse.urlsplit(args.url).hostname or ""
    if host.lower() in FORBIDDEN_HOSTS:
        print(f"refusing: {host} is the production library", file=sys.stderr)
        return 2

    c = Client(args.url, args.token, args.timeout)
    smart_names = {s["name"] for s in c.json("/api/smart-folders")}
    if "Smart 0" not in smart_names and not args.any_library:
        print("refusing: this library wasn't made by make_library.py "
              "(no smart folder 'Smart 0'); pass --any-library for a test copy", file=sys.stderr)
        return 2
    stats = c.json("/api/stats")
    try:
        version = c.json("/api/version")
    except RuntimeError:
        version = {}
    cases = build_cases(c, args)
    if args.only:
        cases = [x for x in cases if args.only in x[0]]
    if not args.no_stacks:  # what the grid asks for (web/src/api.ts)
        cases = [(n, path, (dict(params, stacks="collapse") if isinstance(params, dict)
                            else [*params, ("stacks", "collapse")])
                  if path.startswith("/api/items") else params)
                 for n, path, params in cases]

    if args.grid:  # the web grid's slim list (P4): same pages, fewer fields
        cases += [(f"{n} [grid]", path, dict(params, fields="grid"))
                  for n, path, params in list(cases)
                  if path == "/api/items" and isinstance(params, dict) and "ids" not in params]

    results = []
    for name, path, params in cases:
        status, body, _ = c.get(path, params)  # warm-up
        times, errors = [], 0
        for _ in range(args.runs):
            status, body, ms = c.get(path, params)
            if status != 200:
                errors += 1
            times.append(ms)
        row = {"name": name, "path": path,
               "params": params if isinstance(params, dict) else [list(x) for x in params],
               "status": status, "errors": errors, "bytes": len(body),
               "p50_ms": round(statistics.median(times), 1), "p95_ms": round(pct(times, 95), 1),
               "max_ms": round(max(times), 1), "runs_ms": [round(t, 1) for t in times]}
        if status != 200:
            row["error"] = body[:300].decode("utf-8", "replace")
        results.append(row)
        print(f"{row['p50_ms']:9.1f} {row['p95_ms']:9.1f}  {name}"
              + (f"  [HTTP {status}]" if status != 200 else ""), flush=True)

    meta = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "url": args.url,
            "runs": args.runs, "limit": args.limit, "stacks": not args.no_stacks,
            "items": stats.get("total_items"),
            "trashed": stats.get("trashed_items"), "tags": stats.get("tag_count"),
            "folders": stats.get("folder_count"), "version": version.get("version"),
            "commit": version.get("commit")}
    with open(f"{args.out}.json", "w") as f:
        json.dump({"meta": meta, "results": results}, f, indent=1)
    lines = [
        f"Kist benchmark {meta['at']}: {meta['items']} live items, {meta['tags']} tags, "
        f"{meta['folders']} folders; {args.runs} runs each, page size {args.limit}; "
        f"version {meta['version']} ({meta['commit']})",
        "",
        "| case | p50 ms | p95 ms | max ms | bytes | status |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for r in results:
        flag = "ok" if r["status"] == 200 and not r["errors"] else f"HTTP {r['status']}"
        lines.append(f"| {r['name']} | {r['p50_ms']} | {r['p95_ms']} | {r['max_ms']} | "
                     f"{r['bytes']} | {flag} |")
    table = "\n".join(lines) + "\n"
    with open(f"{args.out}.md", "w") as f:
        f.write(table)
    print()
    print(table)
    return 1 if any(r["status"] != 200 or r["errors"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
