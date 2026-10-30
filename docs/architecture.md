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
├── app.py           FastAPI app, lifespan, SPA serving, /mcp mount
├── auth.py          bearer-token middleware (/api and /mcp)
├── config.py        KIST_* settings
├── models.py        SQLAlchemy models
├── schemas.py       API request/response models
├── api/             REST routers: items, folders, smart folders, tags, misc
├── services/        all business logic, shared by REST, MCP, CLI, and worker
│   ├── ingest.py    hash → dedupe → sniff → store → previews → meta.json
│   ├── thumbs.py    probing, thumbnails, perceptual hash, dominant colors
│   ├── search.py    filters + cursor pagination
│   ├── items.py     edits, tags, folder membership, trash
│   ├── folders.py   folder tree + folders.json snapshot
│   └── meta.py      meta.json read/write
├── mcp_server.py    MCP tools (thin wrappers over services/)
├── worker.py        job loop + inbox scanner + heartbeat
├── cli.py           rebuild-index, rewrite-meta, regen-previews
└── migrate.py       Alembic upgrade under an advisory lock
web/src/             the single-page UI
migrations/          Alembic revisions
tests/               pytest suite (unit + integration against Postgres)
```

## Data model

```
items         id (ULID), filename, ext, mime, size_bytes, width, height,
              duration_s, sha256 (unique), phash, dominant_colors (jsonb),
              rating, notes, source_url, created_at, modified_at, deleted_at
folders       id, name, parent_id, position
item_folders  item_id, folder_id               (an item can be in many folders)
tags          id, name (citext unique)
item_tags     item_id, tag_id
smart_folders id, name, query (jsonb), position (a saved GET /api/items query)
jobs          id, kind, payload, status, attempts, run_after, last_error
```

`deleted_at` is the trash. `sha256` uniqueness makes exact-duplicate uploads
return the existing item. `phash` and `dominant_colors` are captured at ingest
for near-duplicate detection and future color search.

## The library on disk is the source of truth

Every write to an item rewrites `items/{id}/meta.json` (atomically: written
to a temp file, then renamed), and every folder change rewrites
`folders.json`. `kist rebuild-index` reconstructs the whole database from
those files, so Postgres can always be discarded. `meta.json` carries a
`version` field so older libraries stay readable.

## Ingest

Uploads and URL adds are ingested synchronously in the API process, so the
response is the finished item. The worker handles inbox drops and retries
queued jobs (up to 3 attempts with backoff).

1. Stream to `library/.staging/`, hash (SHA-256), dedupe.
2. Sniff the real type with libmagic, then move the file into `items/{ulid}/original.{ext}`.
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
4. Perceptual hash and a 5-color palette, from the thumbnails.
5. Write `meta.json`.

Preview failures never fail an ingest: the item is stored with a generic
icon, and `kist regen-previews` can retry later.

URL ingest follows redirects, caps size (`max_download_mb`), and, when the
URL is an HTML page, downloads the page's `og:image` / `twitter:image`
instead.

## Web UI

- A virtualized grid (TanStack Virtual) whose column count follows the
  window width. Each tile picks `thumb_512` or `thumb_1024` from its physical
  pixel size.
- The info panel stays mounted while open, so selecting an item never
  reflows the grid.
- The preview shows `thumb_1024` immediately, then swaps in the original (or
  the HEIC `display.webp`).
- The token is kept in `localStorage` and mirrored to a `kist_token` cookie
  so `<img>`/`<video>` tags authenticate. The server accepts that cookie for
  GET/HEAD only.

## MCP design rules

- Tools are thin wrappers over `services/`: no logic lives only in MCP.
- Results stay small (metadata), except `view_item`, which returns image content.
- Every write tool returns the updated item so agents can verify.
- Destructive operations stop at the trash.
- `add_item(file_path=…)` only reads from `library/import/` and `library/inbox/`.

## Ideas beyond 1.0

- Color search (palettes are already stored).
- Semantic search: CLIP embeddings with pgvector, `search_semantic` / `find_similar` tools.
- A near-duplicate review UI; LSH bucketing for very large libraries.
- Multi-user accounts and share links.
