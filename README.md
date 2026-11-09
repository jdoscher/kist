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
- **Organize your way.** Nested folders (an item can live in several; a folder
  shows its subfolders' items too), tags, ratings, notes, source URLs, smart
  folders (saved searches), and a trash.
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
| `KIST_TOKEN` | — | **Required.** The owner's bearer token (scope `admin`) for the UI, API, and MCP (16+ characters). Narrower named tokens: see [Tokens](#tokens) |
| `KIST_PORT` | `8080` | Host port (a number) |
| `KIST_BIND` | `0.0.0.0` | Host address the port is published on. `0.0.0.0` is every interface, so the LAN can reach Kist over plain HTTP; set `127.0.0.1` when the TLS reverse proxy runs on the same host. (An old `KIST_PORT=127.0.0.1:8080` moves here.) |
| `KIST_LIBRARY` | `./library` | Host path of the asset library |
| `KIST_DB_PASSWORD` | `kist` | Postgres password. Postgres publishes no port, so only the compose network reaches it, but set your own anyway |
| `KIST_USER_AGENT` | `kist/<version> (self-hosted asset manager)` | User-Agent for URL downloads; some sites (e.g. Wikimedia) require contact info in it |
| `KIST_URL_ALLOW_PRIVATE` | `false` | Let "add from URL" fetch private/LAN/loopback addresses. Off by default so a web page can't point Kist at devices on your network |
| `KIST_URL_ALLOW_HOSTS` | — | Comma list of host names or CIDR ranges allowed anyway, e.g. `nas.lan,192.168.1.0/24` |
| `KIST_DOWNLOAD_TIMEOUT_SECONDS` | `120` | Overall time limit for one URL download |
| `KIST_PREVIEW_TIMEOUT_SECONDS` | `300` | Time limit for generating one item's previews |
| `KIST_HSTS_SECONDS` | `15552000` | `Strict-Transport-Security` max-age (180 days); `0` turns it off |
| `KIST_PUBLIC_URL` | — | The address you open Kist at (e.g. `https://kist.example.com`); MCP items then carry absolute `web_url` links |
| `KIST_JOB_LEASE_SECONDS` | `900` | A background job running longer than this is treated as abandoned by a stopped worker and queued again |
| `KIST_MAX_UPLOAD_BYTES` | `4294967296` (4 GiB) | Largest direct upload; bigger ones get 413 |
| `KIST_MAX_DOWNLOAD_MB` | `512` | Largest "add from URL" download |
| `KIST_VERIFY_INTERVAL_HOURS` | `24` | How often the worker runs `kist verify` (0 turns it off); `/api/health/deep` shows the last result |
| `KIST_TRASH_RETENTION_DAYS` | `30` | The worker deletes items trashed longer than this for good (never protected ones); `0` keeps the trash forever. The clock starts the first time the worker runs with it on |
| `KIST_BACKUP_REPO` | — | restic repository `kist backup` writes to (a path mounted into the worker, or `sftp:`, `rest:`, `s3:`, `b2:` …) |
| `RESTIC_PASSWORD` / `RESTIC_PASSWORD_FILE` | — | The backup repository's password (restic's own variables) |
| `KIST_BACKUP_INTERVAL_HOURS` | `0` | The worker runs `kist backup` this often; `0` = only by hand |
| `KIST_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `KIST_GIT_COMMIT` | `unknown` | Build argument naming the commit, shown by `/api/version`: `KIST_GIT_COMMIT=$(git rev-parse --short HEAD) docker compose build` |
| `KIST_INBOX_POLL_SECONDS` / `KIST_WORKER_POLL_SECONDS` | `5` / `2` | How often the worker looks in the inbox and the job queue |
| `KIST_UID` / `KIST_GID` | `1000` | User the containers run as; the library folder must be owned by it |
| `KIST_MEM_LIMIT` | `4g` | Memory cap for each of the app and worker containers |

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
webm, and pdf, judged by the file's contents rather than its name (an HTML
page named `invoice.pdf` is just a file). Anything else is still stored and
listed with a generic icon, and its original is only ever offered as a
download: ingest never rejects a file for its format.

Exact duplicates (same SHA-256) are detected at ingest and return the
existing item. Near-duplicates (perceptual-hash distance) are available from
`/api/duplicates` and the `find_duplicates` MCP tool.

## Storage layout

```
library/
├── inbox/                  # drop zone, watched by the worker
├── import/                 # files agents may add via MCP add_item(file_path)
├── failed/                 # inbox files whose import failed, under their own names
├── folders.json            # folder tree (with descriptions) + smart folders snapshot
├── .kist/                  # worker heartbeat, last verify result, failed-write notes,
│                           # tokens.json (hashed tokens), events-purged.jsonl,
│                           # backup.json, db.sql.gz, checklist.json, retention.json
├── .staging/               # files on their way in (the worker re-queues strays)
└── items/
    └── {ulid}/
        ├── original.{ext}  # the untouched original
        ├── thumb_256.webp  # longest side 256
        ├── thumb_512.webp  # short side 512 (grid tiles)
        ├── thumb_1024.webp # longest side 1024 (preview, MCP view_item)
        ├── display.webp    # HEIC only: full-resolution copy browsers can show
        ├── meta.json       # all metadata (incl. links, protected), rewritten on every change
        ├── events.jsonl    # the item's history, one line per change (append-only)
        └── fixity.jsonl    # every re-hash of the original: {at, sha256, ok}
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

`rebuild-index` restores folders (with descriptions) and smart folders from
`folders.json`, named tokens from `.kist/tokens.json`, then every item from
its `meta.json`, including tags, folder memberships, dates, trashed state,
protection, links, ThumbHash and preview versions, each item's history from
its `events.jsonl` (and purged items' from `.kist/events-purged.jsonl`), and
fixity dates from `fixity.jsonl`. `--wipe` clears the tables first, and
refuses to start if `folders.json` can't be read. Nothing on disk is skipped
silently: an item folder with no `meta.json` is restored from its original
file (name, tags and notes were only in `meta.json`, so it comes back as
`recovered-<id>.<ext>`), a folder whose parent is missing or that sits in a
parent loop is restored at the top level, and each of these is printed with
`!` and makes the exit code 2. Other maintenance commands:

- `kist rewrite-meta`: rewrites every `meta.json`, `events.jsonl`,
  `folders.json` and `tokens.json` from the database. Safe while Kist is
  running: it re-reads each item as it writes it.
- `kist regen-previews [--all]`: generates missing or failed previews, or all of them.
  It also adds the 2048 px full-screen copy to older images that lack one,
  without touching their other previews. `--thumbhash-only` just adds missing
  ThumbHash placeholders (the worker also does this in the background).
- `kist backup`: dumps the database to `library/.kist/db.sql.gz`, then runs
  `restic backup` of the library to `KIST_BACKUP_REPO` (creating the
  repository on first use) and records the result in `library/.kist/backup.json`.
  Without restic, a repository or a password it says so and exits 2.
  `docker compose exec worker kist backup`
- `kist fixity [--limit N]`: re-hashes N originals now, oldest-checked first.
  The worker re-hashes about 1/30 of them every day, so every file is checked
  monthly; a mismatch shows in Needs attention, `/api/health/deep` and
  `/api/archive-health` with the expected SHA-256, so the right copy can be
  found in a backup.
- `kist verify`: reports where the database and the files on disk disagree
  (missing originals, stale `meta.json` or `folders.json`, folders with no
  database row, earlier writes to disk that failed). It changes nothing and
  names the command that fixes each problem. The worker runs it daily.
- `kist reclassify [--dry-run]`: older versions treated any file named `.pdf`
  as a PDF. This lists items named `.pdf` that aren't, and gives every item
  Kist can't preview the preview status `none` (removing previews it
  shouldn't have). Files and names are left alone.
- `kist selftest`: runs tiny generated PNG, JPEG, GIF, WebP, HEIC, PDF and
  (with ffmpeg) MP4 files through the real preview pipeline and exits 1 if a
  decoder fails. Worth running after every image rebuild:
  `docker compose exec worker kist selftest`.

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
| `search_items` | filename, tags (all / any / none), folder, type, rating, `view="unfiled"` (the inbox), `state="trashed"`, `preview_status` |
| `get_item` | full metadata |
| `view_item` | returns the image itself so the model can see it |
| `add_item` | from a URL, or a file under `library/import/` or `library/inbox/` |
| `update_item`, `set_tags`, `set_folders` | organize. `update_item(folder_id=…)` adds a folder and keeps the others; `set_folders` adds and removes; `update_item(protected=true)` protects an item (only a write or admin token can unprotect) |
| `list_folders`, `create_folder`, `list_tags`, `list_smart_folders` | browse. Folders carry a `description` saying what belongs in them |
| `link_items` | `derived_from`, `alternate`, `related`, `not_duplicate`, `version_of` links between items |
| `recent_batches`, `undo_batch` | what was changed lately, by whom, and undo a batch (e.g. "undo the filing you just did") |
| `prune_unused_tags` | deletes tags no item uses (tags on trashed items count) |
| `trash_item`, `restore_item` | into and out of the trash |
| `retry_preview` | queues another attempt at a failed preview |
| `list_jobs`, `retry_job` | inbox imports and preview retries, and why one failed |
| `find_duplicates`, `library_stats` | housekeeping |

Every item carries `state` (`active` or `trashed`), `preview_status`
(`ready`, `pending`, `failed` or `none`) and a `web_url` that opens it in the
web UI. Every tool call is one batch in the item history, so whatever an
agent did can be undone in one step. MCP accepts `admin`, `write`, `agent` and
`read` tokens, and each tool checks the scope (a read token can only search,
get, view and list). There is deliberately no permanent-delete tool. Emptying the trash is
a human action in the web UI. A tool that can't do what was asked returns an
error that says why (an unknown folder, a bad URL, an item that doesn't exist).

## Links

Every view has its own address, so Back, refresh and bookmarks work:
`/` (Unfiled), `/all`, `/trash`, `/folder/<id>`, `/tag/<name>`,
`/smart/<id>`, with filters as query parameters. `?item=<id>` opens an item's
details and `&full=1` opens it full screen.

## Tokens

`KIST_TOKEN` is the owner's token (scope `admin`, actor name `owner`). Make
narrower named tokens for phones, family and agents with `POST /api/tokens`
(as the owner), e.g. `{"name": "iPhone Shortcut", "scopes": ["add"]}` or
`{"name": "Family", "scopes": ["read"], "folder_ids": ["<folder id>"]}`. The
response carries the token once (`kist_…`); only its SHA-256 is stored, in
Postgres and in `library/.kist/tokens.json`, so `rebuild-index` restores
tokens.

| scope | can |
|---|---|
| `admin` | everything, including managing tokens |
| `write` | every REST edit, including permanent delete from the trash and unprotecting |
| `agent` | what MCP allows: edits, never a permanent delete, never unprotect |
| `add` | `POST /api/items` (file or URL) and `GET /api/me` only |
| `read` | GETs only. With `folder_ids`, only live items in those folders and their subfolders are visible; every other path answers 404 |
| `helper` | the AI job claim/report endpoints only |

A missing scope answers 403 `{"detail": "This token can't do that"}`; a
revoked or expired token 401. `expires_at` is optional. Every change a token
makes is recorded in the item history under its name.

## REST API

Every route except `/api/health` needs `Authorization: Bearer $KIST_TOKEN`.

```
POST   /api/items                multipart upload, or JSON {"url": …}. Both accept
                                 tags, folder_ids, notes, rating, source_url,
                                 created_at, modified_at, deleted_at (ISO 8601;
                                 deleted_at files the item straight into the trash),
                                 so importers can carry metadata over from other tools
GET    /api/items                q, tags (csv, all), tags_any (csv, any), untagged,
                                 view (unfiled: no folder and no tags),
                                 folder (id, subfolders included; or "none"), type, rating, order,
                                 state (active | trashed; deleted=true still works),
                                 preview_status, fixity (failed), created_after,
                                 created_before, limit, cursor. total is counted on the
                                 first page only (no cursor); later pages say null
GET    /api/items/{id}
PATCH  /api/items/{id}           filename, rating, notes, source_url, created_at, protected,
                                 folder_ids (replaces every membership; use
                                 PATCH .../folders to add or remove one).
                                 base_modified_at: the modified_at you last saw;
                                 if the item changed since, 409 with
                                 {"detail": {"message", "item"}} instead of a write
DELETE /api/items/{id}           to trash; ?hard=true deletes permanently (trashed items
                                 only: 409 otherwise). A protected item answers 409
                                 "Protected: unprotect it first"
POST   /api/items/{id}/restore
POST   /api/items/{id}/preview   queue another attempt at the item's previews
PUT    /api/items/{id}/tags      {"tags": [...]}
PUT    /api/items/{id}/folders   {"folder_ids": [...]}
PATCH  /api/items/{id}/tags      {"add": [...], "remove": [...]}: changes only those,
PATCH  /api/items/{id}/folders   so concurrent writers never overwrite each other
GET    /api/items/{id}/file      the original
GET    /api/items/{id}/thumb     ?size=256|512|1024, 2048 (full-screen copy), 4096 (HEIC);
                                 &v=<preview_rev> makes the answer cacheable for good
GET    /api/items/{id}/history   every change to the item, newest first
GET    /api/items/{id}/links     [{item, kind, direction: out|in}]
POST   /api/items/{id}/links     {"dst_id", "kind"}: derived_from, alternate, related,
                                 not_duplicate (find_duplicates stops pairing them),
                                 version_of
DELETE /api/items/{id}/links/{dst_id}  ?kind= (else every kind)
GET    /api/batches              ?limit=50&actor=: recent batches {batch_id, actor, source,
                                 at, count, kinds}
POST   /api/batches/{id}/undo    revert a batch newest first, as a new batch:
                                 {undone, skipped: [{item_id, event_id, reason}], batch_id}.
                                 A change something later touched again is skipped
POST   /api/events/{id}/revert   the same for one event
GET    /api/me                   {name, scopes, folder_ids, expires_at}
GET    /api/tokens               (admin) named tokens, without secrets
POST   /api/tokens               (admin) {name, scopes, folder_ids?, expires_at?} -> 201
                                 with the token, shown once
DELETE /api/tokens/{id}          (admin) revoke
GET    /api/archive-health       fixity, last verify, last backup, free disk, the 3-2-1
                                 checklist
POST   /api/archive-health/checklist  {"id": "offsite", "done": true}
GET    /api/tags                 with counts
DELETE /api/tags/unused          delete tags no item uses (trashed items count)
GET/POST/PATCH/DELETE /api/folders  folders have a description (up to 5,000 characters)
GET/POST/PATCH/DELETE /api/smart-folders
GET    /api/duplicates           ?threshold=6 (perceptual-hash distance)
GET    /api/stats                includes failed_jobs, preview_failed and fixity_failed
GET    /api/jobs                 ?status=pending|running|done|failed
POST   /api/jobs/{id}/retry      a failed job, with fresh attempts
DELETE /api/jobs/{id}            dismiss a failed or finished job
GET    /api/version              {"version", "commit"}
GET    /api/health/deep          database, migrations, worker heartbeat, library disk,
                                 job queue, recent preview failures, last verify run
POST   /api/client-errors        the web UI reports its crashes here (logged, 30/min)
GET    /api/health               no auth; the database answers
```

Lists use cursor pagination: pass `next_cursor` back as `cursor` until it is
`null` (a cursor the server didn't make is a 400).

Every item change is recorded in its history with the actor (token name, or
`cli`, `worker`, `importer`), the source (`web`, `mcp`, `api`, `cli`,
`worker`, `import`) and a batch id: one per request, or send the same
`X-Kist-Batch: <id>` header (up to 64 letters, digits, `.`, `_`, `:`, `-`) on
several requests to undo them together. The web UI sends `X-Kist-Client: web`.
Items carry `thumbhash` (a base64 [ThumbHash](https://evanw.github.io/thumbhash/)
placeholder, or null) and `protected`. Interactive docs are at
`/docs`; they need the token too (in a browser, signing in to the web UI is
enough).

Input is checked the same way on every path (REST, MCP, uploads), with 422
for: a `source_url` that isn't an absolute http(s) URL; a folder id that
doesn't exist (`{"detail": "Unknown folder: <id>"}`; removing a membership
that isn't there is fine); tag names that are empty, contain a comma or run
over 100 characters; folder names that are empty or over 200; notes over
100,000 characters; filenames over 255 or containing `/`; ratings outside
0–5; unknown `type`, `order`, `view` or `preview_status` values.

Every response carries an `X-Request-ID` header (the proxy's, if it sent
one), every log line names it, and a server error answers
`{"detail": "Internal error", "request_id": "…"}`.

## Monitoring

`GET /api/health/deep` (with the token) answers `{"ok": …, "checks": {…}}`:
whether the database answers and its schema is current, the worker's
heartbeat age (it writes `library/.kist/worker-heartbeat` every 15 s after a
database round trip), whether the library is mounted and writable and how
much space is free, failed and stuck jobs and the oldest pending one,
previews that failed in the last 24 hours, the last daily `verify`
result, originals that failed a fixity check, and the last backup. Point an
uptime checker at it. `GET /api/archive-health` is the preservation view:
fixity coverage over 30 days and failures, verify, the last backup and its
age, free disk, and a 3-2-1 checklist (a backup repository is set, a backup
within 7 days, a copy offsite, which you tick yourself, and a clean verify).

Logs have timestamps, levels, logger names and request ids, and each service
keeps 5 × 10 MB of them. They are deleted when a container is re-created, so
when something is wrong save them before redeploying:
`docker compose logs -t > pre-deploy.log`.

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
