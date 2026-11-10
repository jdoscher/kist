# Architecture

Kist is one Python codebase and one container image with two entrypoints,
plus Postgres:

```
docker compose
├── app      FastAPI: REST API, MCP endpoint (/mcp), and the built web UI
├── worker   same image: inbox watcher + Postgres-backed job queue,
│            AI jobs on their own threads
├── ml       kist-ml image (internal only): CLIP embeddings, OCR and (opt-in)
│            faces on ONNX Runtime
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
├── filetypes.py     kind (image/video/pdf/font/other) from the sniffed MIME type,
│                    and which originals may be shown inline
├── lifecycle.py     every state machine: names and allowed transitions
├── models.py        SQLAlchemy models
├── schemas.py       API request/response models; ItemOut.for_agent (MCP shape)
├── api/             REST routers: items, folders, smart folders, tags, jobs,
│                    tokens, history (batches, undo), misc (health, archive health),
│                    ai (status, helper jobs), search (search by image),
│                    organise (vocabulary, cleanup, duplicates, stacks,
│                    collections, people), boards, creative (annotations,
│                    versions, templates, rename, triage, palette)
│                    shares (share-link CRUD), m4 (exports, presets, imports,
│                    snapshots, the iOS Shortcut helper)
├── share_pages.py   the public /s/<slug> pages and /robots.txt: server-rendered,
│                    no script, sandboxing CSP; the only routes with no token
├── services/        all business logic, shared by REST, MCP, CLI, and worker
│   ├── validation.py input rules every path shares (422 / MCP ToolError)
│   ├── ingest.py    hash → dedupe → sniff → store → previews → meta.json;
│   │                inbox scan; the MCP import-dir rule
│   ├── netguard.py  URL downloads: public addresses only, pinned IPs, deadlines
│   ├── thumbs.py    probing, thumbnails, font specimens, perceptual hash,
│   │                dominant colors
│   ├── thumbhash.py ThumbHash encoder (port of the reference, byte-exact)
│   ├── preview_child.py  the child process previews run in (time limit)
│   ├── search.py    filters + cursor pagination; KIND_FILTERS = kind in SQL;
│   │                search() picks the plain or the ranked path
│   ├── ranking.py   ranked search: meaning, similar, colour, text, keyword
│   │                signals, reciprocal rank fusion, match + snippets
│   ├── colour.py    sRGB -> Lab, CIEDE2000, the Lab prefilter box, palettes
│   │                with colour names (the SQL twin is in migration 0201)
│   ├── captions.py  caption providers (ollama/openai/anthropic/gemini), the
│   │                caption job, edits as events, caption.json
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
│   ├── tags.py      tag upsert (through the aliases), counts, pruning,
│   │                cleanup suggestions, merge and rename
│   ├── vocabulary.py aliases, implied tags, namespaces + vocabulary.json;
│   │                the query-time expansion of tag filters
│   ├── suggestions.py tag suggestions (kNN votes + zero-shot), tag vectors,
│   │                per-item feedback (ai/tag-feedback.json)
│   ├── duplicates.py duplicate groups (exact, same_image, similar), resolve
│   ├── stacks.py    stacks, the collapse filter, stack suggestions (EXIF)
│   ├── collections.py suggested collections: numpy k-means + silhouette,
│   │                library/.kist/collections.json, accept
│   ├── faces.py     faces jobs, ai/faces.json, people (assign, cluster,
│   │                merge) + library/.kist/people.json
│   ├── folders.py   folder tree (moves under a lock) + folders.json
│   ├── smart_folders.py  saved searches
│   ├── library.py   stats (one query) and near-duplicates
│   ├── jobs.py      job queue transitions (claim, finish, lease, retry),
│   │                staging recovery
│   ├── previews.py  preview status, retries, reclassify
│   ├── meta.py      the durable copy: atomic meta.json / folders.json writes
│   ├── verify.py    compare Postgres with the disk (CLI + daily worker run)
│   ├── ai.py        ml client, embed/ocr/caption jobs and their budget,
│   │                embed batches, sidecars, helper claim/report, status
│   ├── pdftext.py   a PDF's text layer / page renders (child process)
│   ├── boards.py    boards: document, versioned saves, trash, board events, undo
│   ├── board_render.py  PNG/PDF (Pillow) and self-contained HTML export
│   ├── annotations.py   point/box/range notes on items (annotations.json)
│   ├── templates.py metadata templates (.kist/templates.json) and bulk rename
│   ├── versions.py  the version_of chain and new-version uploads (the
│   │                chain is stacked, the new version its cover)
│   ├── triage.py    the triage cursor over a view
│   ├── palette.py   Cmd+K search across folders, tags, boards, people, items
│   ├── shares.py    share links: scrypt passwords, signed per-link cookie,
│   │                rate limit, shares.json
│   ├── selection.py which items a folder / smart folder / board / list covers
│   │                (shares and exports)
│   ├── exports.py   portable BagIt zip + offline index.html (a worker job)
│   ├── export_images.py  export presets: resize/convert/strip GPS/watermark,
│   │                contact-sheet PDF; export-presets.json
│   ├── provenance.py the provenance block, C2PA detection, filters
│   ├── xmp.py       XMP reader/writer on xml.etree; original.xmp sidecars
│   ├── snapshots.py page snapshots (page/), cleaned HTML, readable text
│   ├── linkrot.py   the weekly source check (netguard) and opt-in Wayback
│   ├── media.py     Milestone 5: previews + metadata of the new kinds (in the
│   │                preview child), items.media and its columns, cover/frame/
│   │                clip/proxy/poster actions, filters, media-backfill
│   ├── formats.py   content-checked MIME types (RAW, design, 3D, font, audio)
│   ├── video.py     ffmpeg/ffprobe: demuxer by MIME, nice + thread cap,
│   │                scrub sprites, frames, clips, proxies, QSV/VA-API
│   ├── audio.py     waveform peaks + thumbnail
│   ├── raw.py       embedded RAW previews (TIFF IFDs, CR3, RAF) and EXIF
│   ├── design_thumbs.py  PSD/AI/EPS/Sketch/Procreate/Krita/ORA/Affinity/Blend
│   ├── svg.py       sanitizer, exact colours, rendering
│   ├── models3d.py  glTF/OBJ/STL/PLY stats, GLB conversion (trimesh)
│   ├── fonts.py     font info and glyphs (fontTools or a built-in sfnt reader)
│   ├── bundles.py   zip bundles: manifest, cover, single files, caps
│   ├── timeline.py  timeline buckets, On this day, map clustering
│   ├── geo.py       offline reverse geocoding (GeoNames cities500)
│   └── health.py    /api/health/deep checks, worker heartbeat file
├── importers/       `kist import` / POST /api/imports: base (runner, folders,
│                    safe unzip), eagle, links (pinterest, arena, raindrop),
│                    sidecar (sidecar, takeout, apple-photos)
├── mcp_server.py    MCP tools (thin wrappers over services/; no queries)
├── worker.py        job loop, inbox scanner, staging sweep, daily verify,
│                    trash retention, fixity, scheduled backups (thread),
│                    ThumbHash backfill, heartbeat thread
├── cli.py           rebuild-index, rewrite-meta, regen-previews, verify,
│                    reclassify, selftest, backup, fixity,
│                    ai-backfill, ai-sidecars, helper
├── media_cli.py     media-backfill, geo-download
├── helper.py        `kist helper`: claims AI jobs over HTTP, runs kist/ml locally
├── ml/              self-contained (imports nothing else from kist): the ml image
│   ├── models.py    model files pinned by revision + sha256, download + verify
│   ├── engine.py    ONNX sessions, preprocessing, tokenizer, RapidOCR, devices
│   ├── faces.py     YuNet + SFace in numpy/Pillow (opt-in, KIST_FACES)
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
              fixity_failed_at, stack_id, flag (pick | reject | null),
              label (red ... purple | null)
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
item_text     item_id, source (ocr | pdf | caption), text, lang, model, created_at
              (GIN index on to_tsvector('simple', text)); one ocr-or-pdf row
              and at most one caption row per item
item_colors   item_id, idx, l, a, b, pct   (CIE Lab of each dominant colour;
              kept by a trigger on items.dominant_colors; index on (a, b, l))
ai_helpers    name, last_seen, jobs_done, info
tag_aliases   alias (citext), tag (citext)        variant -> preferred tag
tag_implies   tag, implied (citext)               boots -> shoes (virtual)
tag_embeddings model, tag, vector (halfvec)       tag names as text vectors (cache)
tag_feedback  item_id, tag, accepted, at          suggestion feedback per item
stacks        id, cover_item_id                   (members: items.stack_id)
faces         id, item_id, idx, box (jsonb: x, y, w, h fractions + landmarks),
              score, model, embedding (halfvec(128), HNSW cosine), person_id
people        id, name (null = unnamed), cover_face_id
boards        id, name, created_at, modified_at, version, item_count, deleted_at,
              doc (jsonb: the board.json document)
board_items   board_id, item_id          ("used in boards"; a purged item's row goes,
                                          its node stays on the board as missing)
board_events  id, board_id, at, actor, source, batch_id, kind, before, after
              (no foreign key; kinds create, update, trash, restore, purge)
annotations   id, item_id, kind (point | box | range), x, y, w, h (0-1),
              t_start, t_end (seconds), text (trigram index), author,
              created_at, resolved
```

Every list order has a partial `(column, id)` index over live items, so
keyset pages stay index scans; `total` is counted only for the first page.

`deleted_at` is the trash. `sha256` uniqueness makes exact-duplicate uploads
return the existing item. `phash` and `dominant_colors` are captured at ingest
for near-duplicate detection and colour search.

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
| tag aliases and implied tags | `library/vocabulary.json` |
| their history (undoable with the batch that changed them, e.g. a tag merge) | `library/vocabulary-events.jsonl` |
| stack membership and cover | each member's `meta.json` (`"stack": {id, cover}`) |
| faces | `items/{id}/ai/faces.json` (a face is `<item>:<idx>` across rebuilds) |
| people, their names and faces | `library/.kist/people.json` |
| tag-suggestion feedback | `items/{id}/ai/tag-feedback.json` |
| suggested collections (a cache) | `library/.kist/collections.json` |
| backups, checklist, retention start | `library/.kist/backup.json`, `checklist.json`, `retention.json` |
| embeddings, OCR/PDF text, captions | `items/{id}/ai/embed-<model>.f16` + `.json`, `ai/ocr.json`, `ai/caption.json` |
| palettes (`item_colors`) | `dominant_colors` in each `meta.json`; the trigger refills the table as rows come back |
| flag, colour label | each item's `meta.json` |
| annotations | `items/{id}/annotations.json`, rewritten from the rows after each change under the item's annotation lock |
| boards | `library/boards/{id}/board.json` (the whole document, incl. `version` and `deleted_at`) + `thumb.webp`; history in `boards/{id}/events.jsonl`, a purged board's in `library/.kist/board-events-purged.jsonl` |
| metadata templates | `library/.kist/templates.json` only (no table: nothing to rebuild; verify checks it parses and its folders exist) |
| provenance, last source check (M4) | each item's `meta.json` (`provenance`, `source_check`) |
| share links (M4; passwords scrypt-hashed) | `library/.kist/shares.json` (view counters are written at most every 5 minutes; verify ignores them) |
| page snapshot text (M4) | `items/{id}/page/text.txt` (item_text rows with source `page`) |
| export presets (M4) | `library/.kist/export-presets.json` (the file is the store; no table) |
| exports (M4) | `library/exports/{id}.json` + `.zip` (the file is the store) |

`items/{id}/original.xmp` is derived from `meta.json` and rewritten with
it (`kist xmp --rewrite` refreshes them); it is never read back as the
source of truth.

`kist verify` also checks that `tokens.json` matches the database and that
every item's `events.jsonl` has all of its events; `kist rewrite-meta`
rewrites both. `kist rebuild-index` reconstructs the
whole database from those files, so Postgres can always be discarded
(`tests/test_m23_integration.py` writes a bit of every kind of state above,
wipes, rebuilds without the ml service and compares).
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
`base_modified_at` locks the row and answers 409 if a field it writes was
changed since the client loaded it (an item event after that time: other
changes such as tags, links or stacks move `modified_at` but don't conflict); folder moves take a tree-wide lock and re-check for loops;
new tags are an upsert.

## Share links (the public surface)

`/s/<slug>` and `/robots.txt` are the only routes served without a token
(`auth.PUBLIC_PREFIXES`; `tests/test_shares_security.py` checks that every
other path still answers 401, in both directions). The rules:

- The slug is 128 random bits. Each request re-checks that the share is
  active (410 when expired or revoked) and that the item is covered by it
  right now (`selection.filters`): an item id from anywhere else, a trashed
  one, or one filed out of the shared folder is a 404 indistinguishable
  from a missing one. Item ids must look like ULIDs and variants are a
  fixed list, so nothing in the path reaches the file system.
- Pages are rendered with `html.escape` everywhere and carry `CSP:
  default-src 'none'; img-src 'self'; style-src 'unsafe-inline';
  sandbox allow-forms allow-same-origin allow-downloads` (no script at all),
  `X-Robots-Tag: noindex`, `Referrer-Policy: no-referrer`, `no-store`. They
  never show where an item came from.
- Originals only with `allow_original` (which needs `allow_download`), and
  then with `CSP: sandbox`.
- Passwords are scrypt hashes; the right one sets `kist_share`, an
  HttpOnly cookie for `/s/<slug>` signed with a key derived from
  `KIST_TOKEN` over the slug, the expiry and the password hash (changing
  the password signs visitors out). Wrong passwords are counted per link in
  memory (`KIST_SHARE_PASSWORD_ATTEMPTS` per 15 minutes, then 429).
- The share pages never read `kist_token` or a bearer header and never set
  either. View counts are kept; who viewed isn't (uvicorn's access log
  drops the client address for `/s/` lines).
- No public route fetches anything from the network.

## Tokens and scopes

`auth.BearerTokenMiddleware` turns the bearer token (or, for GET/HEAD, the
`kist_token` cookie) into an `access.Principal`: the owner for `KIST_TOKEN`,
or a named token looked up by SHA-256. `access.request_allowed` maps the
request to the token's scopes before any route runs (403 for a missing scope;
404 for any path outside a folder-limited reader's view). The principal stays
in a context variable for the request, so services apply the few
caller-dependent rules themselves: a folder-limited reader's searches, item
fetches (`items.get_item`, hence file, thumb, history, links, annotations,
versions) and folder tree
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

Milestone 3 adds two extension points in `services/events.py`, so new kinds
of change plug into the same history without touching the undo core:

- `EXTRA_KINDS`: item event kinds defined elsewhere, with what they touch
  and how to revert them. Annotations register `annotation` (before/after
  are the annotation or null; the conflict key is the annotation id).
- `UNDO_SOURCES`: other event tables batched with item events. Board edits
  are **board-level events in their own `board_events` table** (not
  `item_events` with a null item: an item event always belongs to an item,
  and a board edit can touch none). They carry the same actor, source and
  batch id, so `GET /api/batches` lists both (board kinds prefixed `board_`)
  and `POST /api/batches/{id}/undo` reverts both in one new batch. A board
  update is undone by saving the earlier document as a new version; it is
  skipped when a later board event outside the batch exists.

## Boards (Milestone 3)

`services/boards.py` owns the document, `services/board_render.py` draws
it. Saves are whole-document (`PUT`) or node ops (`PATCH`), both checked
against `base_version` under a row lock (409 with the current board when
stale), then written to `board.json` from the committed row under an
advisory lock, like `meta.json`. Item nodes point at items, never copies;
a purge removes the `board_items` row and leaves the node, reported in
`missing_item_ids`. Exports composite the items' preview copies (the
smallest one big enough for the requested scale, capped at 40 MP) with
Pillow; PDF is Pillow's writer; HTML is one file with inlined base64
images, absolutely positioned, served with a sandboxing CSP.

Who sees a board: a folder-limited read token sees a board only if it shows
at least one item and every item node is visible to it; otherwise the board
answers 404 (list, get, thumb, export, `/items/{id}/boards`). Hiding the
nodes it can't see instead would still leak the board's notes and layout.

## AI jobs

`embed`, `ocr`, `caption` and `faces` (see Organise) are job kinds like
`ingest`, in the same table,
with the same lease, retry and failure rules, but claimed separately
(`services/ai.claim`) under a budget:

- `jobs.claim_next` (the worker's main loop) never takes AI kinds. The
  worker's `AiLane` claims them for its `KIST_AI_CONCURRENCY` threads (2), so
  a slow OCR never holds up an import. A thread takes the oldest job it may
  run and, if that is an embed job, up to `KIST_AI_EMBED_BATCH - 1` more
  (`ai.claim_batch`), so the queue keeps its order.
- An embed batch is one `POST /embed/images` (multipart, at most 32). The
  ONNX exports take one image per run (their batch dimension is fixed at
  1), so the ml service still loops; what a batch saves is per-item
  overhead. Its sidecars are written on parallel threads (their fsyncs
  overlap), then all rows go in one commit and all successes finish in one
  more. A picture the ml service can't read fails only its own job; an
  older ml service without `/embed/images` gets one request per image. The
  ml service decodes and resizes images before a request waits for its
  inference gate, so with two worker threads decoding overlaps inference.
- The worker runs `embed`/`ocr` when `KIST_ML_URL` is set and `caption` when
  the caption provider is usable from the server (`ai.worker_kinds`).
  `ai-backfill` and helpers take `embed` and `ocr` unless `caption` is named.
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
Captions are the exception, because a person can edit them: writing one
(the worker's, a helper's or an edit) records a `caption` event with the
old and new text, which undo reverts through the same service.

## Ranked search

`search.search()` builds the filters exactly as before (so a folder-limited
reader only ever ranks its own items), adds `has_text`, and, when a ranking
parameter is present, hands them to `ranking.ranked_page`, which adds
`text` (an EXISTS on `item_text` with the `to_tsvector('simple', text)`
expression the GIN index is built on) and `color` (a `colour_hits` CTE:
the palette rows in the Lab box, CIEDE2000 computed once per row, summed
per item; it is both the filter and the colour signal). The first page's
`total` is `count(*) OVER ()` in the same query:

```
base CTE = SELECT id FROM items WHERE <filters>
signal   = (id, score, row_number() OVER (ORDER BY score DESC, id)) per kind:
           meaning/similar  1 - cosine distance (exact, over base)
           keyword          trigram similarity + ILIKE + ts_rank of the text
           color            sum(pct * (1 - dE/maxDE)) over palette rows in the
                            Lab box with kist_ciede2000(...) <= maxDE
           text             max ts_rank_cd over ocr/pdf rows
order    = one signal: its score; several: sum(1 / (60 + rank)) (RRF)
```

Items a meaning signal can't score (no embedding yet) sort last. `q` is a
signal instead of a filter only next to a meaning signal ("hybrid");
otherwise it stays a filter and the page keeps its `order`, with a
`keyword` match and snippet attached. Snippets come from `ts_headline` for
the page's rows only. The cursor is an offset tagged `~ranked` (M1's
`~semantic` cursors still work), refused by the plain path and the other way
round. Vector distances are exact (no HNSW in the ranked path): with
filters in play that keeps every page right, and at 100k items it is one
pass over 100k halfvecs.

Every filter `search.build_filters` knows is in the `base` CTE, so all three
milestones' filters work in every mode alike: the tag filter with the
vocabulary's aliases, implied tags and `ns:*` namespaces
(`search.tag_condition`), `person`, `flag`, `label`, and a folder-limited
reader's scope. The keyword condition and score (`ranking.keyword_condition`,
used by the plain `q` filter and by the hybrid keyword signal alike) cover
filename, notes, annotation text (ILIKE on the annotations trigram index)
and `item_text`.

`stacks=collapse` with a ranking collapses **after** ranking: the fused
score is computed per item, then `row_number() OVER (PARTITION BY
coalesce(stack_id, id) ORDER BY fused DESC, id)` keeps each stack's
best-ranked visible member, at its place, and `total` and the offset
cursor count the collapsed rows. That member, not the cover, stands for the
stack, because it is what matched: its `match` and snippet are true, and
`stack_id`/`stack_count` let the client expand the stack. (Collapsing to
covers first, as the plain list does, would hide a stack whose only match is
not its cover.) The trash is never collapsed.

Colour: `kist_hex_lab` and `kist_ciede2000` (plpgsql, IMMUTABLE) mirror
`services/colour.py`; tests compare them on random colours and the Sharma
reference pairs. The tolerance maps to a CIEDE2000 limit of
2 + 0.48 * tol (default 30 -> 16.4). The box's lightness bound is exact
(dE00 >= |dL| / S_L, S_L <= 1.75); its a*/b* bound widens with the query's
chroma so it never drops a real match (a randomized test checks that), which
also means it narrows little for saturated colours. Measured on 20k
synthetic items (40k palette rows, M6): colour search 43 ms, meaning or
similar 43 ms, both together 65 ms, plain list 2 ms.

## AI results on disk

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

## Organise (Milestone 2)

**Vocabulary.** `tags.get_or_create` sends every new tag through the
aliases (`vocabulary.resolve`), so REST, MCP, uploads and imports all write
the preferred tag; undo and rebuild pass `resolve=False` to put back exactly
what was there. Adding an alias is a merge (`tags.merge`): each item's change
goes through `items.set_tags`, so it is a `tags` event in the request's batch
and one undo reverts it. Implications are never written to items: the search
tag filter (`search.tag_condition`) matches a tag name against a recursive
CTE (`vocabulary.matching_names`) of the name, its alias target and every
tag that implies it at any depth; `ItemOut.implied_tags` walks the same rules
in Python (cached per session). A new rule that would close a loop is
refused (422), and the CTE's `UNION` couldn't loop anyway.

**Tag suggestions** never write anything but feedback. The kNN half asks
pgvector for the item's 24 nearest neighbours through `search.build_filters`
(so a reader only ever votes with items it may see); the zero-shot half
compares the item's vector with every tag's text vector (`tag_embeddings`,
filled lazily, 64 per request, and fully by the worker's daily collections
run) as a softmax at CLIP's temperature, dropping tags whose raw cosine is
under 0.2.

**Duplicates** are pairs (SQL self-join on phash for `same_image`; blockwise
numpy dot products for `similar`) turned into groups with union-find after
removing `not_duplicate` pairs and pairs inside one stack. `resolve` runs the
merge and the trashing through the item services in the request's batch.

**Stacks.** `items.stack_id` plus `stacks.cover_item_id`. Membership changes
are `stack` events (`{stack_id, cover}`), undone by
`stacks.restore_membership`, which re-creates a dissolved stack row by id.
`?stacks=collapse` keeps an item when it has no stack, is its stack's cover,
or (cover trashed or not visible to the caller) is the lowest-id member the
caller can see: correlated subqueries, so it composes with every filter and
with keyset paging. Ranked searches collapse differently, after ranking (see
Ranked search). A stack left with one member dissolves. Uploading a new
version (`versions.add_version`) of a stacked item adds the new version to
that stack as its cover (`versions.on_new_version`); a version chain
outside any stack becomes a new stack with the new version as its cover. `rebuild-index` puts membership back
with a core UPDATE that keeps `modified_at`, so `meta.json` still matches.

**Suggested collections** cluster the active items' embeddings with
spherical k-means (k-means++ seeding, best of 4), k chosen by mean silhouette
on a sample of ≤ 5,000, then drop clusters under 8 items, with mean cosine to
the centre under 0.72, or ≥ 80% in one folder already. Results go to
`library/.kist/collections.json`; the worker recomputes them every
`KIST_COLLECTIONS_INTERVAL_HOURS`.

**Faces** (opt-in, `KIST_FACES`) are a third AI job kind, run by the worker
against the ml service only (helpers don't claim them). The ml service
loads YuNet and SFace only when its own `KIST_FACES` is on; their pre- and
post-processing (letterbox, anchor-free decoding, NMS, Umeyama alignment to
the 112×112 ArcFace template) is numpy + Pillow, no OpenCV. A new face joins
the person of its nearest face at cosine ≥ 0.42 (never two faces of one
picture); the worker's `cluster_unassigned` turns the rest into unnamed
people (connected components at ≥ 0.48, two pictures or more). People are
not item data: renames and merges aren't item events.

**Scopes.** Changes need `write`/`agent`/`admin` like every non-GET. A
folder-limited reader reaches `/api/people`, `/api/stacks/{id}`,
`/api/items/{id}/faces` and `/api/faces/{id}/thumb`, each filtered to items
it can see (a stack shows only visible members, people count only visible
faces); the curation endpoints (vocabulary, cleanup, duplicates,
collections, tag suggestions, stack suggestions) are 404 for it, like
`/api/tags`. Milestone 3 adds `/api/items/{id}/annotations|versions|boards`
and the boards routes (a board only when every item on it is visible); the
palette, triage and templates are 404 for it. `access.RESTRICTED_PATHS` is
the one list of what such a reader may reach;
`tests/test_m23_integration.py` walks every family.
## Milestones 4 and 5 with 2 and 3 (int-all)

**One filter path.** `search.build_filters` takes the M4 provenance
filters (`ai_generated`, `license`, `source_status`, via
`provenance.filters`) and the M5 media filters (`media`, via
`media.filters`) next to M2/M3's, so the plain list, every ranked mode
(`ranking.ranked_page` gets the same list), search by image and the
folder-limited scope all apply them. `search.filters_from_params` turns
`GET /api/items`' parameters into that list for the timeline, on-this-day
and the map; `selection.smart_filters` does the same for a saved
smart-folder query, so a share or export of a smart folder holds what it
shows (`semantic` and `color` order a list and don't narrow a share;
`text=` does).

**Item text sources.** `item_text.source` is `ocr` | `pdf` | `caption` |
`page`. `q` (and a hybrid's keyword half) matches all four; `text=` and
`has_text` are `ai.READ_SOURCES` (`ocr`, `pdf`) only: the words inside the
item. The AI jobs only ever replace their own rows.

**Boards in shares and exports.** `boards.share_item_ids` gives a board's
live items in reading order (by node y, then x); `selection` uses it for
`target_kind: board` and refuses a trashed or unknown board (422).

**RAW pairs.** `stacks.suggestions` groups by file stem, splits a group by
`raw_pair_key` when there are several, and compares capture times to the
second, taking `items.taken_at` (read from the RAW by M5) where Pillow
can't read the file.

**Browser extension.** `provenance.from_flat` maps the flat fields the
extension and the `/add` page send (`title` → `page_title`, `author` →
`creator`, `license_url` → `rights_url`, `site`), dropping values that
don't validate; an explicit `provenance` object wins. Ingest stores the
adding token's id in `items.added_by_token` (migration 0602; not in
meta.json). `api/m4._may_snapshot` lets an add-only token snapshot only an
item with its own token id created under 10 minutes ago; anyone else
without an edit scope gets 403 whether or not the item exists. The
middleware lets the add scope reach exactly `POST /api/items` and
`POST /api/items/{id}/snapshot`.

**Scopes.** A folder-limited reader additionally reaches the M5 item reads
(`scrub`, `stream`, `waveform`, `svg`, `model`, `font-info`, `glyphs`,
`bundle`) of items it sees, and `/api/timeline`, `/api/on-this-day`,
`/api/map` (counted over its items only). Shares, exports, export presets,
imports and snapshots are 404 for it. `/s/<slug>` is public and
share-scoped: with or without a token, it serves only that share's items.
`tests/test_m45_integration.py` walks these.

## Media (Milestone 5)

**Kinds.** `filetypes.kind_for` still decides by sniffed MIME type, never by
name. Formats libmagic can't name (a `.sketch` is "a zip", a CR3 "data") get
a precise type from `formats.refine_mime` at ingest, and only after their
content is checked: the extension picks which signature to look for (a
`.sketch` must be a zip holding `previews/preview.png`, a `.glb` must start
with `glTF`, a `.cr2` must be a TIFF). New kinds: `audio` (audio/*),
`model` (model/*), `font` (font/*; M2 builds specimens on it), `design`
(PSD, AI, EPS, Sketch, Procreate, Affinity, Blend, Krita, ORA, Figma) and
`bundle` (any other zip). RAW files stay `image`. `search.KIND_FILTERS` is
the same rule in SQL.

**Previews.** `thumbs.generate_previews` (in the preview child, with its
time limit) asks `media.generate` first. It writes the usual thumbnails
plus scrub sprites, waveforms, bundle manifests and sanitized SVG, and
returns a `media` block. A design file, model or bundle without a picture
gets a generic icon tile and a `preview_icon` marker, which keeps the icon
out of the perceptual hash and dominant colours. A font's thumbnail is
Milestone 2's specimen (`thumbs.font_specimen`), made after `fonts.info`
reads it; a font FreeType can't render but fontTools can read keeps
`preview_status: none`, and one neither can read fails like any broken
file. Waveforms and font specimens are pictures Kist drew
(`thumbs.DRAWN_PREVIEW_KINDS`): no perceptual hash, no palette.

**What the AI jobs take.** Embeddings (`ai.embed_source`): any item with a
ready preview except audio (`ai.NOT_EMBEDDED_KINDS`) and generic icons, so
images, RAW, SVG, video, PDF, design files, font specimens (similar
typefaces), uploaded posters and a bundle's picture are all searchable by
meaning. OCR (`ai.ocr_kind`): PDFs and images without camera EXIF, as
before (RAW and design files are left out). Faces: images only.

**Metadata.** `items.media` (JSONB) holds what was read: `video`/`audio`
(ffprobe), `exif` (Pillow for photos; exiftool, else the built-in TIFF/CR3
reader, for RAW), `svg`, `model`, `font`, `design`, `raw`, `bundle`,
`scrub`, `playable`, `proxy`, `place`, plus the user's `cover_time` and
`poster`, and `source_item`/`source_time`/`source_range` on frames and clips.
meta.json carries the block; `media.apply` derives the indexed columns from
it (`taken_at`, `has_gps`, `lat`, `lon`, `camera`, `lens`, `vertical`,
`has_audio`, `place`, `raw_pair_key`; `taken_sort` is a generated column,
`taken_at` else `created_at`). rebuild-index restores everything from the
block, `kist verify` compares it, and a preview retry keeps the user keys.
EXIF dates are wall-clock times stored as if UTC, so the timeline groups a
photo on the day it was taken where it was taken.

**ffmpeg.** Inputs are always `-protocol_whitelist file -f <demuxer>`, the
demuxer chosen from the sniffed MIME type (a type without one is refused),
so playlists and concat scripts can't make ffmpeg read other files. Runs are
niced by class (thumbnails 5, scrub sprites 10, clips and proxies 15, plus
`ionice -c3` on Linux), capped at `KIST_FFMPEG_THREADS`, time-limited and
size-limited (`-fs`). Clip and proxy jobs (`lifecycle.HEAVY_KINDS`) are
claimed after every waiting ingest and preview job. With `KIST_HWACCEL`
set, proxies and clips try QSV/VA-API and fall back to libx264.

**Playable.** `playable` is `native` (H.264 8-bit 4:2:0 or AV1 in MP4/MOV,
VP8/VP9/AV1 in WebM, with AAC/MP3/Opus/Vorbis/FLAC audio; browser-friendly
audio), `proxy` or `none`. `GET /stream` serves the original or
`proxy.mp4`/`proxy.m4a` with Range, and queues the proxy on first play.
No HLS: one faststart MP4 is enough for a personal library.

**Safety.** SVGs are parsed with expat after refusing DOCTYPE/ENTITY, and
scripts, event handlers, foreignObject and every external reference are
removed (`svg.py` lists the rules). Bundle entries are only ever looked up
in the zip's directory, never extracted; traversal names are refused and
entry, total and count caps make zip bombs harmless. Design-file zip
entries are read with a size cap. Files served from a bundle are
attachments with a sandbox CSP unless they're raster images.

**Optional dependencies** (the `media` extra, installed in the image):
fontTools (WOFF2; else the built-in reader covers TTF/OTF/WOFF), resvg-py
(SVG rendering; else cairosvg if installed, else librsvg via libvips),
trimesh (OBJ/STL/PLY to GLB; else no conversion). psd-tools is used for
PSDs Pillow can't composite when it's installed. exiftool is in the image;
without it RAW previews and EXIF come from the built-in readers.

**Maps.** `kist geo-download` writes `library/.kist/geo/places.tsv` from
GeoNames (cities500 + country and region names); lookups are in memory with
a 1-degree grid, nearest place within 50 km. `/api/map` clusters on a grid
of 64 px cells at the requested zoom. The web map's tiles: see
[maps.md](maps.md).

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
- `add_item(file_path=…)` and `search_by_image(file_path=…)` only read from
  `library/import/` and `library/inbox/`.
- The search tools are the REST search: `search_items` passes `semantic`,
  `similar_to`, `color`, `text` straight to `search.search()`, and the
  instructions tell agents to put a descriptive request in `semantic`.

## Ideas beyond 1.0

- Restore-test for backups (`restic check --read-data` on a schedule).
- A near-duplicate review UI; LSH bucketing for very large libraries.
- Share links (named, scoped tokens exist; no per-user accounts).
