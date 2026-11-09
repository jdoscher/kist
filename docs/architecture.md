# Architecture

Kist is one Python codebase and one container image with two entrypoints,
plus Postgres:

```
docker compose
├── app      FastAPI: REST API, MCP endpoint (/mcp), and the built web UI
├── worker   same image: inbox watcher + Postgres-backed job queue,
│            AI jobs on their own threads
├── ml       kist-ml image (internal only): CLIP embeddings + OCR on ONNX Runtime
└── db       Postgres 16 + pgvector (search index only)

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
| AI | ONNX Runtime (CPU; CoreML/OpenVINO optional), OpenCLIP ViT-B-32 (Immich's ONNX export), RapidOCR; pgvector `halfvec` + HNSW |

## Code map

```
kist/
├── app.py           FastAPI app, lifespan, SPA serving, /mcp mount, service
│                    error -> HTTP status mapping
├── auth.py          bearer-token middleware (/api, /mcp, /docs): resolves the
│                    caller, checks its scope, sets the history context
├── access.py        scopes, actions, the caller (access.current()) and which
│                    HTTP requests each scope may make
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
├── api/             REST routers: items, folders, smart folders, tags, jobs,
│                    tokens, history (batches, undo), misc (health, archive health)
├── services/        all business logic, shared by REST, MCP, CLI, and worker
│   ├── validation.py input rules every path shares (422 / MCP ToolError)
│   ├── ingest.py    hash → dedupe → sniff → store → previews → meta.json;
│   │                inbox scan; the MCP import-dir rule
│   ├── netguard.py  URL downloads: public addresses only, pinned IPs, deadlines
│   ├── thumbs.py    probing, thumbnails, perceptual hash, dominant colors
│   ├── thumbhash.py ThumbHash encoder (port of the reference, byte-exact)
│   ├── preview_child.py  the child process previews run in (time limit)
│   ├── search.py    filters + cursor pagination; KIND_FILTERS = kind in SQL
│   ├── items.py     edits (with the 409 version check), tags, folder
│   │                membership, protection, trash, purge; every change
│   │                records events
│   ├── events.py    item history (item_events + events.jsonl), batches, undo
│   ├── links.py     typed links between items
│   ├── tokens.py    named tokens (hashed) + library/.kist/tokens.json
│   ├── retention.py the worker's trash retention
│   ├── fixity.py    rolling re-hash of originals (fixity.jsonl)
│   ├── backup.py    `kist backup`: pg_dump + restic, backup.json
│   ├── archive.py   /api/archive-health and the 3-2-1 checklist
│   ├── tags.py      tag upsert, counts, pruning
│   ├── folders.py   folder tree (moves under a lock) + folders.json
│   ├── smart_folders.py  saved searches
│   ├── library.py   stats (one query) and near-duplicates
│   ├── jobs.py      job queue transitions (claim, finish, lease, retry),
│   │                staging recovery
│   ├── previews.py  preview status, retries, reclassify
│   ├── meta.py      the durable copy: atomic meta.json / folders.json writes
│   ├── verify.py    compare Postgres with the disk (CLI + daily worker run)
│   ├── ai.py        ml client, embed/ocr jobs and their budget, sidecars,
│   │                helper claim/report, status
│   ├── pdftext.py   a PDF's text layer / page renders (child process)
│   └── health.py    /api/health/deep checks, worker heartbeat file
├── mcp_server.py    MCP tools (thin wrappers over services/; no queries)
├── worker.py        job loop, inbox scanner, staging sweep, daily verify,
│                    trash retention, fixity, scheduled backups (thread),
│                    ThumbHash backfill, heartbeat thread
├── cli.py           rebuild-index, rewrite-meta, regen-previews, verify,
│                    reclassify, selftest, backup, fixity,
│                    ai-backfill, ai-sidecars, helper
├── helper.py        `kist helper`: claims AI jobs over HTTP, runs kist/ml locally
├── ml/              self-contained (imports nothing else from kist): the ml image
│   ├── models.py    model files pinned by revision + sha256, download + verify
│   ├── engine.py    ONNX sessions, preprocessing, tokenizer, RapidOCR, devices
│   └── server.py    the ml service's FastAPI app
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
              preview_rev, thumbhash, protected, fixity_checked_at,
              fixity_failed_at
folders       id, name, parent_id, position, description
item_folders  item_id, folder_id               (an item can be in many folders)
tags          id, name (citext unique)
item_tags     item_id, tag_id
smart_folders id, name, query (jsonb), position (a saved GET /api/items query)
jobs          id, kind, payload, status, attempts, run_after, last_error,
              started_at, finished_at
item_links    src_id, dst_id, kind      (derived_from, alternate, related,
                                         not_duplicate, version_of)
item_events   id, item_id, at, actor, source, batch_id, kind, before, after
              (no foreign key: a purged item's history is kept)
api_tokens    id, name, token_sha256, scopes, folder_ids, created_at,
              expires_at, last_used_at, revoked_at
item_embeddings item_id, model, dim, vector (halfvec), created_at
              (one partial HNSW cosine index per model, on vector::halfvec(dim))
item_text     item_id, source (ocr | pdf), text, lang, model, created_at
              (GIN index on to_tsvector('simple', text))
ai_helpers    name, last_seen, jobs_done, info
```

Every list order has a partial `(column, id)` index over live items, so
keyset pages stay index scans; `total` is counted only for the first page.

`deleted_at` is the trash. `sha256` uniqueness makes exact-duplicate uploads
return the existing item. `phash` and `dominant_colors` are captured at ingest
for near-duplicate detection and future color search.

## State machines

Every state has one name, defined in `kist/lifecycle.py`, and every surface
(REST, MCP, web UI, CLI) uses it rather than working it out from columns.

| machine | states | moves |
|---|---|---|
| item | `active`, `trashed` | trash and restore (both safe to repeat); permanent delete only from `trashed`; a `protected` item can't be trashed or purged (409); the worker purges items trashed longer than `KIST_TRASH_RETENTION_DAYS` |
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
rewrites its members' `meta.json`). What else lives on disk, and is restored
by `rebuild-index`:

| state | file |
|---|---|
| links, protected, ThumbHash | each item's `meta.json` (a link in both items') |
| folder descriptions | `folders.json` |
| item history | `items/{id}/events.jsonl`, appended after each commit; a purged item's lines move to `library/.kist/events-purged.jsonl` |
| fixity dates | the last line of `items/{id}/fixity.jsonl` |
| named tokens (hashed) | `library/.kist/tokens.json` |
| backups, checklist, retention start | `library/.kist/backup.json`, `checklist.json`, `retention.json` |

`kist verify` also checks that `tokens.json` matches the database and that
every item's `events.jsonl` has all of its events; `kist rewrite-meta`
rewrites both. `kist rebuild-index` reconstructs the
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

## Tokens and scopes

`auth.BearerTokenMiddleware` turns the bearer token (or, for GET/HEAD, the
`kist_token` cookie) into an `access.Principal`: the owner for `KIST_TOKEN`,
or a named token looked up by SHA-256. `access.request_allowed` maps the
request to the token's scopes before any route runs (403 for a missing scope;
404 for any path outside a folder-limited reader's view). The principal stays
in a context variable for the request, so services apply the few
caller-dependent rules themselves: a folder-limited reader's searches, item
fetches (`items.get_item`, hence file, thumb, history, links) and folder tree
only see live items in its folders; unprotecting needs `write`. MCP tools
declare the action they need and fail closed without a principal. Routes
that need more than the path mapping gives use the `auth.require_scope()`
dependency: `/api/ai/status` is admin only (a read token may GET most of
`/api`), and the `/api/ai/jobs` endpoints take `helper` or `admin`. MCP's
`ai_status` follows the MCP rules (any MCP scope, not a folder-limited
reader).

## History and undo

Each item mutation adds an `item_events` row in the same transaction
(`events.record_changes` compares before/after snapshots: `edit`, `tags`,
`folders`, `trash`, `restore`, `protect`; plus `create`, `purge`, `link`,
`unlink`). A session hook appends the committed events to `events.jsonl`.
The actor, source and batch come from a context variable set by the auth
middleware (one batch per request, or `X-Kist-Batch`), by the MCP tool
wrapper (one per call), by the worker (`importer`/`worker`) or default to
`cli` with one batch per process. Undo walks a batch's events newest first
and applies the inverse through the services, as a new batch; an event is
skipped when a later event outside both batches touched the same field
(edit fields, tag names, folder ids, state, the link), so undo never
overwrites newer work.

## AI jobs

`embed` and `ocr` are job kinds like `ingest`, in the same table, with the
same lease, retry and failure rules, but claimed separately
(`services/ai.claim`) under a budget:

- `jobs.claim_next` (the worker's main loop) never takes AI kinds. The
  worker's `AiLane` claims them for its `KIST_AI_CONCURRENCY` threads, so a
  slow OCR never holds up an import.
- Nothing AI is claimed while a light job (ingest, preview) is pending.
- Jobs queued by `kist ai-backfill` carry `backfill: true` and are only
  claimed inside `KIST_AI_WINDOW`; jobs for new items run at once.
- An unreachable ml service (`AiUnavailable`) puts the job back with
  `jobs.release` (no attempt used) and pauses the lane for a minute.
- `kist helper` claims through `POST /api/ai/jobs/claim` with a `helper`
  token (runner `helper:<name>` in the payload; only that helper may fetch
  the job's image from `/api/ai/jobs/{id}/image` and report), never gets
  PDFs (the server reads their text layer), and reports a vector or text that
  the server validates (model, dimension, finite) and stores exactly as the
  worker would.

AI results are not edits: they write only `item_embeddings`/`item_text`
and the `ai/` sidecars, never the `items` row, so they add no `item_events`
and can't be undone or conflict with an undo. A purge removes them with the
item (foreign keys cascade; the item folder, `ai/` included, is deleted).
`?semantic=` runs through the same `build_filters` as every search, so a
folder-limited reader only ever ranks its own items; its cursor is an
offset tagged as semantic, which a keyword search refuses (and vice versa),
and `total` comes with the first page only, as for any search.

Results are written to `items/<id>/ai/` first, then upserted: the f16
sidecar is the vector itself (little-endian float16, 1 KB at 512-d), and
`rebuild-index` turns sidecars back into rows without asking the ml
service. Vectors are L2-normalised, so cosine distance (`<=>`) orders
`?semantic=` results; vectors of different models never mix (rows are per
model and every query names the model).

The `ml` service (`kist/ml/server.py`) loads each tower on first use and
drops it after `KIST_ML_IDLE_SECONDS`; at most `KIST_ML_CONCURRENCY`
inferences run at once. Model files come from a pinned Hugging Face revision
and are refused unless their sha256 matches (`kist/ml/models.py`).

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
4. Perceptual hash, a 5-color palette and a ThumbHash placeholder, from the
   thumbnails.
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
- Preservation: the worker re-hashes about 1/30 of the originals a day
  (oldest-checked first, in small chunks while idle) and runs `kist backup`
  every `KIST_BACKUP_INTERVAL_HOURS` in a background thread.
  `/api/archive-health` reports fixity coverage and failures, verify, the
  last backup, free disk and the 3-2-1 checklist.

## MCP design rules

- Tools are thin wrappers over `services/`: no logic and no database queries
  live in `mcp_server.py`. Every item comes back in one shape
  (`ItemOut.for_agent`), and service errors become `ToolError`s whose message
  reaches the agent.
- Results stay small (metadata), except `view_item`, which returns image content.
- Every write tool returns the updated item so agents can verify.
- Destructive operations stop at the trash, and every tool call is one
  batch in the item history (`recent_batches`, `undo_batch`).
- Tools check the token's scope (`agent`, `write`, `admin`, or `read` for
  the reading tools; a folder-limited reader only gets search, get, view
  and folder listing).
- `add_item(file_path=…)` only reads from `library/import/` and `library/inbox/`.

## Ideas beyond 1.0

- Color search (palettes are already stored).
- Semantic search: CLIP embeddings with pgvector, `search_semantic` / `find_similar` tools.
- Restore-test for backups (`restic check --read-data` on a schedule).
- A near-duplicate review UI; LSH bucketing for very large libraries.
- Share links (named, scoped tokens exist; no per-user accounts).
