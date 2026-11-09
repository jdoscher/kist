# Architecture

Kist is one Python codebase and one container image with two entrypoints,
plus Postgres:

```
docker compose
├── app      FastAPI: REST API, MCP endpoint (/mcp), and the built web UI
├── worker   same image: inbox watcher + Postgres-backed job queue
└── db       Postgres 16 (search index only)

library/     bind mount: the durable copy of everything
```

| layer | choice |
|---|---|
| API | Python 3.12, FastAPI, SQLAlchemy 2, Alembic |
| previews | libvips (pyvips), ffmpeg/ffprobe, pdfium (pypdfium2), pillow-heif |
| search | Postgres: `pg_trgm` for fuzzy filenames, `citext` tags, keyset pagination |
| queue | a `jobs` table claimed with `SELECT … FOR UPDATE SKIP LOCKED` (no Redis) |
| web | React 19, Vite, TypeScript, TanStack Query + Virtual |
| agents | MCP Python SDK, streamable HTTP, stateless JSON responses |

## Code map

```
kist/
├── app.py           FastAPI app, lifespan, SPA serving, /mcp mount, service
│                    error -> HTTP status mapping
├── auth.py          bearer-token middleware (/api, /mcp, /docs)
├── headers.py       security headers (CSP, HSTS, framing) on every response
├── compress.py      gzip for JSON/JS/CSS, never media
├── observability.py logging format, request ids, 500 handler, rate limiter
├── config.py        KIST_* settings
├── db.py            engine and session factory
├── ids.py           ULIDs
├── filetypes.py     kind (image/video/pdf/other) from the sniffed MIME type,
│                    and which originals may be shown inline
├── lifecycle.py     every state machine: names and allowed transitions
├── models.py        SQLAlchemy models
├── schemas.py       API request/response models; ItemOut.for_agent (MCP shape)
├── api/             REST routers: items, folders, smart folders, tags, jobs, misc
├── services/        all business logic, shared by REST, MCP, CLI, and worker
│   ├── validation.py input rules every path shares (422 / MCP ToolError)
│   ├── ingest.py    hash → dedupe → sniff → store → previews → meta.json;
│   │                inbox scan; the MCP import-dir rule
│   ├── netguard.py  URL downloads: public addresses only, pinned IPs, deadlines
│   ├── thumbs.py    probing, thumbnails, perceptual hash, dominant colors
│   ├── preview_child.py  the child process previews run in (time limit)
│   ├── search.py    filters + cursor pagination; KIND_FILTERS = kind in SQL
│   ├── items.py     edits (with the 409 version check), tags, folder
│   │                membership, trash, purge
│   ├── tags.py      tag upsert, counts, pruning
│   ├── folders.py   folder tree (moves under a lock) + folders.json
│   ├── smart_folders.py  saved searches
│   ├── library.py   stats (one query) and near-duplicates
│   ├── jobs.py      job queue transitions (claim, finish, lease, retry),
│   │                staging recovery
│   ├── previews.py  preview status, retries, reclassify
│   ├── meta.py      the durable copy: atomic meta.json / folders.json writes
│   ├── verify.py    compare Postgres with the disk (CLI + daily worker run)
│   └── health.py    /api/health/deep checks, worker heartbeat file
├── mcp_server.py    MCP tools (thin wrappers over services/; no queries)
├── worker.py        job loop, inbox scanner, staging sweep, daily verify,
│                    heartbeat thread
├── cli.py           rebuild-index, rewrite-meta, regen-previews, verify,
│                    reclassify, selftest
└── migrate.py       Alembic upgrade under an advisory lock
web/src/             the single-page UI
migrations/          Alembic revisions
tests/               pytest suite (unit + integration against Postgres)
```

## Data model

```
items         id (ULID), filename, ext, mime, size_bytes, width, height,
              duration_s, sha256 (unique), phash, dominant_colors (jsonb),
              rating (0-5, checked), notes, source_url, created_at,
              modified_at, deleted_at, preview_status, preview_error,
              preview_rev
folders       id, name, parent_id, position
item_folders  item_id, folder_id               (an item can be in many folders)
tags          id, name (citext unique)
item_tags     item_id, tag_id
smart_folders id, name, query (jsonb), position (a saved GET /api/items query)
jobs          id, kind, payload, status, attempts, run_after, last_error,
              started_at, finished_at
```

`deleted_at` is the trash. `sha256` uniqueness makes exact-duplicate uploads
return the existing item. `phash` and `dominant_colors` are captured at ingest
for near-duplicate detection and future color search.

## State machines

Every state has one name, defined in `kist/lifecycle.py`, and every surface
(REST, MCP, web UI, CLI) uses it rather than working it out from columns.

| machine | states | moves |
|---|---|---|
| item | `active`, `trashed` | trash and restore (both safe to repeat); permanent delete only from `trashed` |
| preview | `pending`, `ready`, `failed`, `none` | set by ingest; `POST /api/items/{id}/preview` (or MCP `retry_preview`) queues a `regen_preview` job |
| job | `pending`, `running`, `done`, `failed` | claim → finish; a failure retries with backoff up to 3 attempts; a `running` job older than the lease is re-queued; `failed` → `pending` by retry |
| upload (web) | `queued`, `uploading`, `done`, `duplicate`, `inTrash`, `error` | `web/src/uploads.ts`; the destination folder is fixed when a file is added |
| view (web) | the URL | `web/src/url.ts`: routes, and which changes push, replace or go Back |

Unfiled (the inbox) is a server view, `view=unfiled`: active items in no
folder and with no tags. The UI and MCP use the same definition.

An inbox file whose import fails is never deleted: ingest puts it back, and
after the last attempt the worker moves it to `library/failed/` under its own
name, where `retry` picks it up again. Files move only after the commit that
records where they're going (a failed move is written back), and the worker
sweeps `library/.staging/` at start and every 5 minutes for inbox files that
never got a job (the database was down at the wrong moment).

## The library on disk is the source of truth

Every write to an item rewrites `items/{id}/meta.json`, and every folder
change rewrites `folders.json` (a folder rename, move or delete also
rewrites its members' `meta.json`). `kist rebuild-index` reconstructs the
whole database from those files, so Postgres can always be discarded.
`meta.json` carries a `version` field so older libraries stay readable.

How the files stay right with several writers (API threads, MCP, worker,
CLI), in `services/meta.py`:

- Each file is written to a unique temp file, fsynced, and renamed into
  place, so nobody ever reads a torn file.
- The database commits first. Then, holding a Postgres advisory lock for
  that one file, the writer reads the row again and writes it. The last
  writer read last, so the file ends on the newest state even when two
  writers reach the disk out of order.
- A file write that fails after the commit doesn't fail the request: the
  change is safe in Postgres. It's logged and noted in
  `library/.kist/meta-pending`; `kist verify` (daily, from the worker)
  reports it and `kist rewrite-meta` repairs it.
- A purge holds the item's lock, so a late write can't re-create a purged
  item's folder.

Edits that can race in Postgres are guarded there: `PATCH` with
`base_modified_at` locks the row and answers 409 if the item changed since the
client loaded it; folder moves take a tree-wide lock and re-check for loops;
new tags are an upsert.

## Ingest

Uploads and URL adds are ingested synchronously in the API process, so the
response is the finished item. The worker handles inbox drops and retries
queued jobs (up to 3 attempts with backoff).

1. Stream to `library/.staging/`, hash (SHA-256), dedupe.
2. Sniff the real type with libmagic, then move the file into `items/{ulid}/original.{ext}`.
   The sniffed type decides the item's kind and whether its original is
   ever shown inline: only real images, video, audio and PDFs are, and
   everything else is a sandboxed download whatever its name says.
3. Previews:
   - images through libvips: `thumb_256` and `thumb_1024` (longest side) and
     `thumb_512` (short side 512, long side ≤ 2048), because grid tiles are
     square crops and need the short side to cover the tile at 2× density.
   - video: an ffmpeg poster frame; PDF: page 1 via pdfium.
   - HEIC: libvips first. If the system libheif rejects a file (for example,
     iPhone photos with HDR gain maps), pillow-heif's bundled libheif decodes
     it in a subprocess, since the two libheif copies conflict in one process.
     HEIC also gets a full-resolution `display.webp`, because browsers can't
     render HEIC.
   - every image over 1024 px also gets `display_2048.webp`, the copy the
     full-screen preview shows unless the screen needs more (originals run
     5-30 MB; this is about 0.5 MB).
4. Perceptual hash and a 5-color palette, from the thumbnails.
5. Write `meta.json`.

Preview failures never fail an ingest: the item is stored with
`preview_status=failed` and a one-line `preview_error`, and can be retried
from the UI, MCP, or `kist regen-previews`.

URL ingest follows redirects, caps size (`max_download_mb`), and, when the
URL is an HTML page, downloads the page's `og:image` / `twitter:image`
instead.

## Web UI

- A virtualized grid (TanStack Virtual) whose column count follows the
  window width. Each tile picks `thumb_512` or `thumb_1024` from its physical
  pixel size.
- The info panel stays mounted while open, so selecting an item never
  reflows the grid.
- The preview shows `thumb_1024` immediately, then swaps in what the screen
  needs (`web/src/preview.ts`): the 2048 copy, or on a big high-density screen
  the original (HEIC: `display.webp`).
- Edits patch the loaded list in place with the item the API returns
  (`useApplyItems`, `stillMatches`) instead of refetching every page.
- Caching: hashed `/assets/*` and originals are immutable; thumbnails are
  immutable when requested with `v=<preview_rev>`, which regeneration bumps.
  JSON/JS/CSS are gzipped (`kist/compress.py`); media never is.
- The token is kept in `localStorage` and mirrored to a `kist_token` cookie
  so `<img>`/`<video>` tags authenticate. The server accepts that cookie for
  GET/HEAD only.

## Observability

- Logs: one format for app, worker and uvicorn (time, level, logger, request
  id), level from `KIST_LOG_LEVEL`. `observability.RequestContext` gives each
  request an id (or keeps the proxy's `X-Request-ID`), returns it on every
  response, and turns an unhandled exception into a logged 500 that carries it.
- The web UI posts its crashes to `POST /api/client-errors` (logged, 30/min).
- The worker's heartbeat thread writes `library/.kist/worker-heartbeat`
  every 15 s after a database round trip, unless the main loop has been
  stuck longer than the job lease. The container healthcheck and
  `/api/health/deep` read it, so a long preview doesn't look like a dead
  worker.
- `/api/health/deep` reports the database, schema revision vs head, worker,
  library disk, job queue, previews failed in 24 h and the last daily verify
  (`library/.kist/verify.json`). `/api/version` names the build's commit.
- `kist selftest` checks every decoder after an image rebuild.

## MCP design rules

- Tools are thin wrappers over `services/`: no logic and no database queries
  live in `mcp_server.py`. Every item comes back in one shape
  (`ItemOut.for_agent`), and service errors become `ToolError`s whose message
  reaches the agent.
- Results stay small (metadata), except `view_item`, which returns image content.
- Every write tool returns the updated item so agents can verify.
- Destructive operations stop at the trash.
- `add_item(file_path=…)` only reads from `library/import/` and `library/inbox/`.

## Ideas beyond 1.0

- Color search (palettes are already stored).
- Semantic search: CLIP embeddings with pgvector, `search_semantic` / `find_similar` tools.
- A near-duplicate review UI; LSH bucketing for very large libraries.
- Multi-user accounts and share links.
