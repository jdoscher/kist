# Kist

A self-hosted visual asset library for images, screenshots, SVGs, video,
and PDFs, with a fast web grid and an **MCP interface built in**, so Claude and
other AI agents can search your library, *see* what's in it, add to it, and
organize it.

- **Plain files on disk.** Every item is a folder holding the untouched
  original, its previews, and a `meta.json`. Back it up with `rsync`; rebuild
  the database from it at any time.
- **One small stack.** A single `docker compose up`: the app, a background
  worker, and Postgres. Runs fine on a home server or NAS.
- **Organize your way.** Folders (an item can live in several), tags,
  ratings, notes, source URLs, smart folders (saved searches), and a trash.
- **Agents as first-class users.** A streamable-HTTP MCP endpoint exposes
  search, viewing (the model receives the image itself), adding, tagging, and
  filing. Nothing an agent does can permanently delete anything.

Single user, one shared token. MIT licensed.

## Quick start

```bash
git clone https://github.com/jdoscher/kist.git && cd kist
cp .env.example .env
# set KIST_TOKEN in .env, e.g. to the output of: openssl rand -hex 24
docker compose up -d --build
```

Open `http://<host>:8080`, paste your token, and drop files in.

| service | role |
|---|---|
| `app` | REST API, MCP endpoint at `/mcp`, and the web UI |
| `worker` | same image: ingests inbox drops and retries queued jobs |
| `db` | Postgres 16: the search index (the library on disk is the durable copy) |

### Configuration (`.env`)

| variable | default | |
|---|---|---|
| `KIST_TOKEN` | — | **Required.** Bearer token for the UI, API, and MCP (16+ characters) |
| `KIST_PORT` | `8080` | Host port |
| `KIST_LIBRARY` | `./library` | Host path of the asset library |
| `KIST_DB_PASSWORD` | `kist` | Postgres password (internal to the compose network) |
| `KIST_USER_AGENT` | `kist/<version> (self-hosted asset manager)` | User-Agent for URL downloads; some sites (e.g. Wikimedia) require contact info in it |

Kist speaks plain HTTP. To reach it from outside your LAN, put it behind a
TLS-terminating reverse proxy (Caddy, nginx, Traefik). Over HTTPS the
**Paste** button can also read the clipboard directly.

## Adding assets

- **Web UI:** drag and drop anywhere, the **Upload** button, or paste:
  ⌘V/Ctrl-V anywhere outside a text field, or the **Paste** button.
  Screenshots and copied images upload as-is. For an image copied from a web
  page, Kist downloads the original file (full quality, GIF animation intact)
  and falls back to the clipboard copy if the site refuses. Pasted image URLs
  are downloaded; pasted *page* URLs use the page's preview image
  (`og:image` / `twitter:image`).
- **Inbox:** drop files into `<library>/inbox/`; the worker ingests them.
- **API / MCP:** `POST /api/items` with a file or `{"url": "…"}`, or the
  `add_item` MCP tool.

Previews are generated for png, jpg, webp, gif, svg, avif, heic, mp4, mov,
webm, and pdf. Anything else is still stored and listed with a generic icon:
ingest never rejects a file for its format.

Exact duplicates (same SHA-256) are detected at ingest and return the
existing item. Near-duplicates (perceptual-hash distance) are available from
`/api/duplicates` and the `find_duplicates` MCP tool.

## Storage layout

```
library/
├── inbox/                  # drop zone, watched by the worker
├── import/                 # files agents may add via MCP add_item(file_path)
├── folders.json            # folder tree + smart folders snapshot
└── items/
    └── {ulid}/
        ├── original.{ext}  # the untouched original
        ├── thumb_256.webp  # longest side 256
        ├── thumb_512.webp  # short side 512 (grid tiles)
        ├── thumb_1024.webp # longest side 1024 (preview, MCP view_item)
        ├── display.webp    # HEIC only: full-resolution copy browsers can show
        └── meta.json       # all metadata, rewritten on every change
```

Postgres is only the query index. Every item's metadata lives next to its
file, so the library is greppable, rsync-able, and fully reconstructible from
disk alone.

## Backup & restore

The library directory is the whole state:

```bash
rsync -a --delete /path/to/library/ backup-host:/backups/kist/
```

To restore, move to new hardware, or recover from a lost database:

```bash
docker compose up -d db app                       # empty database, migrations run
docker compose exec app kist rebuild-index        # re-import from meta.json files
```

`rebuild-index` restores folders and smart folders from `folders.json`, then
every item from its `meta.json`, including tags, folder memberships, dates,
and trashed state. `--wipe` clears the tables first. Other maintenance commands:

- `kist rewrite-meta`: rewrites every `meta.json` from the database.
- `kist regen-previews [--all]`: generates missing previews, or all of them.

The `pgdata` volume can always be thrown away; it is never the only copy of
anything.

## MCP (Claude and other agents)

Streamable-HTTP endpoint at `http://<host>:8080/mcp`, using the same bearer token:

```bash
claude mcp add --transport http kist http://<host>:8080/mcp \
  --header "Authorization: Bearer $KIST_TOKEN"
```

| tool | |
|---|---|
| `search_items` | filename, tags (all / any / none), folder, type, rating |
| `get_item` | full metadata |
| `view_item` | returns the image itself so the model can see it |
| `add_item` | from a URL, or a file under `library/import/` or `library/inbox/` |
| `update_item`, `set_tags`, `set_folders` | organize |
| `list_folders`, `create_folder`, `list_tags`, `list_smart_folders` | browse |
| `trash_item` | moves to the trash (restorable in the web UI) |
| `find_duplicates`, `library_stats` | housekeeping |

There is deliberately no permanent-delete tool. Emptying the trash is a human
action in the web UI.

## REST API

Every route except `/api/health` needs `Authorization: Bearer $KIST_TOKEN`
(`?token=` also works, for curl).

```
POST   /api/items                multipart upload, or JSON {"url": …}. Both accept
                                 tags, folder_ids, notes, rating, source_url,
                                 created_at, modified_at, deleted_at (ISO 8601;
                                 deleted_at files the item straight into the trash),
                                 so importers can carry metadata over from other tools
GET    /api/items                q, tags (csv, all), tags_any (csv, any), untagged,
                                 folder (id or "none"), type, rating, order,
                                 created_after, created_before, deleted, limit, cursor
GET    /api/items/{id}
PATCH  /api/items/{id}           filename, rating, notes, source_url, created_at,
                                 folder_ids
DELETE /api/items/{id}           to trash; ?hard=true deletes permanently
POST   /api/items/{id}/restore
PUT    /api/items/{id}/tags      {"tags": [...]}
PUT    /api/items/{id}/folders   {"folder_ids": [...]}
GET    /api/items/{id}/file      the original
GET    /api/items/{id}/thumb     ?size=256|512|1024
GET    /api/tags                 with counts
GET/POST/PATCH/DELETE /api/folders
GET/POST/PATCH/DELETE /api/smart-folders
GET    /api/duplicates           ?threshold=6 (perceptual-hash distance)
GET    /api/stats
GET    /api/health               no auth
```

Lists use cursor pagination: pass `next_cursor` back as `cursor` until it is
`null`. Interactive docs are at `/docs`.

## Security

Kist is built for one person on a trusted network. Read
[SECURITY.md](SECURITY.md) before exposing it to the internet.

## Development

```bash
# backend (needs libvips, libmagic, ffmpeg, and a Postgres to point at)
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
KIST_TOKEN=dev-token-0123456789 \
KIST_LIBRARY_DIR=./library \
KIST_DATABASE_URL=postgresql+psycopg://kist:kist@localhost:5432/kist \
  .venv/bin/uvicorn kist.app:app --reload

# frontend dev server (proxies /api and /mcp to :8080)
cd web && npm install && npm run dev

# tests: integration tests need a throwaway Postgres database
KIST_TEST_DATABASE_URL=postgresql+psycopg://kist:kist@localhost:5432/kist_test \
  .venv/bin/pytest
.venv/bin/ruff check kist tests migrations
```

See [docs/architecture.md](docs/architecture.md) for how it fits together,
and [CONTRIBUTING.md](CONTRIBUTING.md) before sending a pull request.

## License

MIT. See [LICENSE](LICENSE).
