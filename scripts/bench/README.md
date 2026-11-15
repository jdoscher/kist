# Benchmarks

Synthetic libraries and timings for Kist's read paths (Milestone 6). Never
point either script at a real library: `make_library.py` refuses a database
that already has items, and `run.py` refuses the hosts in `KIST_BENCH_REFUSE_HOSTS` (your production library) and any library
without its "Smart 0" smart folder (`--any-library` overrides that, never
the host check).

## Make a library

Into the database and library the app uses, on a throwaway install (dev0):

```bash
docker compose exec -T worker python - --items 250000 < scripts/bench/make_library.py
```

Locally (a fresh, migrated database):

```bash
KIST_DATABASE_URL='postgresql+psycopg://kist@/kist_bench?host=/tmp&port=55432' \
KIST_LIBRARY_DIR=/tmp/kist-bench-lib python scripts/bench/make_library.py --items 20000
```

250k items take about a minute on a laptop (COPY, then `VACUUM ANALYZE`).
Options: `--tags 800 --folders 150 --smart-folders 10 --embed-fraction 0.3
--trash-fraction 0.01 --seed 6 --no-files`. Every item directory holds two
hard links to one 64 px WebP, so the library costs inodes, not data.

The synthetic library has no meta.json files: skip `kist verify` (it reports
every item as drift) and never run `rebuild-index` on it. Fixity, ThumbHash
and media backfills have nothing to do on it (see the script's docstring).

## Time it

```bash
python scripts/bench/run.py --url http://localhost:8080 --token "$KIST_TOKEN" \
    --runs 20 --out bench-250k
```

Writes `bench-250k.json` (every run) and `bench-250k.md` (p50/p95/max per
case). Cases: first and deep pages for every list order, `/api/items/buckets`
for each date sort, the count, tag / folder / unfiled / smart-folder
filters, keyword, text-in-image, colour and more-like-this (pgvector)
searches (`--semantic "words"` adds a meaning search when the ml service
runs), stats, tags list, sidebar folders and smart folders, timeline and map
clusters. Like the web grid it sends `stacks=collapse` (`--no-stacks` to
leave it out); `--only buckets` runs a subset; `--grid` times every list case
again with `fields=grid` (the web grid's slim list, cases named `[grid]`).

## Thumbnails (perf P1)

make_library's thumbnails are one 64 px WebP. For realistic bytes, link a
pool of real thumbnails into every item directory first (`--set-dims` also
gives the synthetic items the pool entries' shapes):

```bash
python scripts/bench/thumb_pool.py --library /tmp/kist-bench-lib \
    --pool ~/kist-perf-data/thumbpool --set-dims
python scripts/bench/thumbs.py --url http://localhost:8080 --token "$KIST_TOKEN" \
    --concurrency 30 --n 300 --out thumbs-20k
```

`thumbs.py` lists items like the grid and fetches `--n` thumbnails per size
from `--concurrency` keep-alive connections: p50/p95/max, wall time,
requests/s, MB/s, average size. It refuses the `KIST_BENCH_REFUSE_HOSTS` hosts unless
`--read-only-prod` (GETs only, capped at 300 requests from 30 connections).
Run it once with the owner token and once with a named token to see what
the token check costs.
