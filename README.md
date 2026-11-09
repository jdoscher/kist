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
| `db` | Postgres 16 with pgvector (`pgvector/pgvector:pg16`): the search index (the library on disk is the durable copy) |
| `ml` | internal only: image embeddings (OpenCLIP ViT-B-32) and OCR (RapidOCR) on ONNX Runtime; see [AI](#ai-search-by-meaning-and-ocr) |

### Configuration (`.env`)

| variable | default | |
|---|---|---|
| `KIST_TOKEN` | — | **Required.** Bearer token for the UI, API, and MCP (16+ characters) |
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
| `KIST_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `KIST_GIT_COMMIT` | `unknown` | Build argument naming the commit, shown by `/api/version`: `KIST_GIT_COMMIT=$(git rev-parse --short HEAD) docker compose build` |
| `KIST_INBOX_POLL_SECONDS` / `KIST_WORKER_POLL_SECONDS` | `5` / `2` | How often the worker looks in the inbox and the job queue |
| `KIST_UID` / `KIST_GID` | `1000` | User the containers run as; the library folder must be owned by it |
| `KIST_MEM_LIMIT` | `4g` | Memory cap for each of the app and worker containers |
| `KIST_AI_ENABLED` | `true` in compose | Queue embedding/OCR jobs for new items (`false` outside compose, where there's no ml service) |
| `KIST_ML_URL` | `http://ml:8000` in compose | The ml service the worker sends AI jobs to. Empty: the worker runs none itself (a `kist helper` still can) |
| `KIST_ML_MODEL` | `ViT-B-32__openai` | Embedding model, for the ml service, app and worker alike. `ViT-B-16-SigLIP2__webli` is the opt-in alternative (768-d, ~2.5× slower, 1.5 GB of files) |
| `KIST_AI_WINDOW` | — | When backfill jobs may run, e.g. `01:00-06:00` (local time of the containers' `TZ`; may wrap past midnight). Empty: any time. New items' jobs never wait for it |
| `KIST_AI_CONCURRENCY` | `1` | AI jobs the worker runs at once (on their own threads: imports never wait behind them) |
| `KIST_AI_OCR_PDF_PAGES` | `5` | Scanned PDFs (no text layer): OCR at most this many pages |
| `KIST_ML_TIMEOUT_SECONDS` | `120` | One ml request's time limit |
| `KIST_ML_THREADS` | `4` | Inference threads per request in the ml service (4 keeps a 10-core NAS responsive) |
| `KIST_ML_CONCURRENCY` | `1` | Requests the ml service runs at once (the rest wait) |
| `KIST_ML_MEM_LIMIT` / `KIST_ML_CPUS` | `2g` / `4` | Memory and CPU caps for the ml container |
| `KIST_ML_DEVICE` | `cpu` | `cpu`, or `openvino-gpu` for an Intel GPU (build with `KIST_ML_OPENVINO=1` and pass `/dev/dri` through: see `docker-compose.yml`) |
| `KIST_ML_OPENVINO` | `0` | Build argument: `1` builds the ml image with ONNX Runtime's OpenVINO build (+~200 MB) |
| `KIST_ML_IDLE_SECONDS` | `300` | The ml service unloads a model unused this long (0 = never) |
| `KIST_ML_BASE_URL` | `https://huggingface.co` | Where model files are downloaded from (a mirror works: files are checked by sha256) |
| `TZ` | `UTC` | The worker's time zone, which `KIST_AI_WINDOW` is in |

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

## AI: search by meaning and OCR

Kist 2.0 understands what's in your images, entirely on your own hardware:

- **Embeddings.** Every item with a thumbnail gets a 512-number OpenCLIP
  ViT-B-32 vector (from its 512-px thumbnail). `GET /api/items?semantic=brown
  suede boots` orders results by meaning; the web UI and MCP tools build on
  this in the next milestone.
- **OCR.** Screenshots and other images without a camera's EXIF data are run
  through RapidOCR (PP-OCRv4, Chinese/English). PDFs give up their text
  layer first (free); only scanned pages are OCR'd.
- **Plain files.** Every result is written next to the item before it goes
  into Postgres (`ai/embed-<model>.f16` + `.json`, `ai/ocr.json`), so
  `kist rebuild-index` restores AI results in minutes and never recomputes.

The `ml` container does the work with ONNX Runtime on the CPU (no torch, no
GPU required). On first start it downloads the model files (~600 MB for
ViT-B-32, pinned to a Hugging Face revision and checked by sha256) into the
`ml-models` volume. New items are embedded and read as they arrive; for the
items you already have:

```bash
docker compose exec worker kist ai-backfill            # embed + ocr; or --kind embed --limit 1000
curl -H "Authorization: Bearer $KIST_TOKEN" http://localhost:8080/api/ai/status
```

Backfill jobs wait for `KIST_AI_WINDOW` (e.g. `01:00-06:00`) and for any
import or preview job; the worker runs `KIST_AI_CONCURRENCY` of them at once.
If the ml service is down (or still downloading on first start), its jobs go
back to the queue without using up attempts and the worker pauses AI work
for a minute.

### Speed and memory

Measured on an Apple M6 (CPU only, 4 threads, through the ml service over
HTTP; 100 real screenshots as 512-px WebP thumbnails):

| | per item | |
|---|---|---|
| image embedding (ViT-B-32) | 12 ms (11.7 ms inference) | 80 images/s from one client |
| text embedding (a search) | 9 ms | |
| OCR (screenshot, ≤2048 px) | 415 ms | 600 ms per item as a worker job |
| backfill through the worker (job, ml call, sidecars, Postgres) | 20 ms (concurrency 1), 13 ms (2) | 50 / 77 items/s |
| backfill through `kist helper` (claim 32, fetch thumbnail, embed, report) | 19 ms | 300 items in 6.2 s |

ml service memory (resident, macOS): 50 MB idle, 480 MB with the image tower
loaded, **1.2 GB with ViT-B-32 and RapidOCR loaded and used**, about 1.1–1.6
GB once the text tower is also in (1.6 GB with two requests at once). The
default `KIST_ML_MEM_LIMIT=2g` with `KIST_ML_CONCURRENCY=1` fits that;
idle models unload after 5 minutes.

**Intel i5-1235U (the NAS), estimated, not measured:** its two performance
cores plus eight efficiency cores at 4 threads should run ViT-B-32 about 3–4×
slower than the M6, so roughly 40–50 ms per image, i.e. **15–25 embeddings/s,
about 4–6 minutes for 4,400 items and 1.5–2.5 hours for 100,000**, and
1.5–2.5 s per OCR'd screenshot. That is in line with the 2–5 images/s Big
Iron reports for slower "modern NAS CPUs" (N100 class) running Immich's
ViT-B-32 ([source](https://www.bigiron.cc/guides/immich-smart-search-tuning-clip-model-choice-and-reindex-cost)).
Check the real numbers with `/api/ai/status` after a backfill.

**Intel GPU (optional).** Build the ml image with OpenVINO
(`KIST_ML_OPENVINO=1 docker compose build ml`), set
`KIST_ML_DEVICE=openvino-gpu`, and uncomment the `devices`/`group_add` lines
in `docker-compose.yml` to pass `/dev/dri` through. A device that can't be
used falls back to the CPU with a warning. This path is untested so far (no
Intel GPU was available while building it).

### A Mac as a helper

A Mac can work through the backlog for the NAS. Install Kist with the model
extras and point it at the server:

```bash
pip install 'kist[ml] @ git+https://github.com/jdoscher/kist.git'
export KIST_HELPER_TOKEN=...   # KIST_TOKEN for now; a helper-scoped token once tokens land
kist helper --url https://kist.example.com
```

It claims jobs over HTTPS (`POST /api/ai/jobs/claim`), fetches each
thumbnail, runs the same ONNX models locally and posts the result; the
server checks it (model, size) and writes the sidecars. Stopping it loses
nothing: unreported jobs return to the queue when their lease
(`KIST_JOB_LEASE_SECONDS`) runs out. Scanned PDFs stay with the server.
On an M6 the CPU is the right device: CoreML's Neural Engine is barely
faster for ViT-B-32 (7.8 vs 8.8 ms) and compiles the model for 40–75 s at
every start (`--device coreml` if you want it anyway).

## Storage layout

```
library/
├── inbox/                  # drop zone, watched by the worker
├── import/                 # files agents may add via MCP add_item(file_path)
├── failed/                 # inbox files whose import failed, under their own names
├── folders.json            # folder tree + smart folders snapshot
├── .kist/                  # worker heartbeat, last verify result, failed-write notes
├── .staging/               # files on their way in (the worker re-queues strays)
└── items/
    └── {ulid}/
        ├── original.{ext}  # the untouched original
        ├── thumb_256.webp  # longest side 256
        ├── thumb_512.webp  # short side 512 (grid tiles)
        ├── thumb_1024.webp # longest side 1024 (preview, MCP view_item)
        ├── display.webp    # HEIC only: full-resolution copy browsers can show
        ├── meta.json       # all metadata, rewritten on every change
        └── ai/             # AI results (rebuild-index loads them, never recomputes)
            ├── embed-ViT-B-32__openai.f16   # raw little-endian float16 vector
            ├── embed-ViT-B-32__openai.json  # {model, dim, created_at, kist_version}
            └── ocr.json                     # {model, lang, text, boxes, source, created_at}
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
trashed state and preview versions. `--wipe` clears the tables first, and
refuses to start if `folders.json` can't be read. Nothing on disk is skipped
silently: an item folder with no `meta.json` is restored from its original
file (name, tags and notes were only in `meta.json`, so it comes back as
`recovered-<id>.<ext>`), a folder whose parent is missing or that sits in a
parent loop is restored at the top level, and each of these is printed with
`!` and makes the exit code 2. Other maintenance commands:

- `kist rewrite-meta`: rewrites every `meta.json` and `folders.json` from the
  database. Safe while Kist is running: it re-reads each item as it writes it.
- `kist regen-previews [--all]`: generates missing or failed previews, or all of them.
  It also adds the 2048 px full-screen copy to older images that lack one,
  without touching their other previews.
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
| `update_item`, `set_tags`, `set_folders` | organize. `update_item(folder_id=…)` adds a folder and keeps the others; `set_folders` adds and removes |
| `list_folders`, `create_folder`, `list_tags`, `list_smart_folders` | browse |
| `prune_unused_tags` | deletes tags no item uses (tags on trashed items count) |
| `trash_item`, `restore_item` | into and out of the trash |
| `retry_preview` | queues another attempt at a failed preview |
| `list_jobs`, `retry_job` | inbox imports and preview retries, and why one failed |
| `find_duplicates`, `library_stats` | housekeeping |

Every item carries `state` (`active` or `trashed`), `preview_status`
(`ready`, `pending`, `failed` or `none`) and a `web_url` that opens it in the
web UI. There is deliberately no permanent-delete tool. Emptying the trash is
a human action in the web UI. A tool that can't do what was asked returns an
error that says why (an unknown folder, a bad URL, an item that doesn't exist).

## Links

Every view has its own address, so Back, refresh and bookmarks work:
`/` (Unfiled), `/all`, `/trash`, `/folder/<id>`, `/tag/<name>`,
`/smart/<id>`, with filters as query parameters. `?item=<id>` opens an item's
details and `&full=1` opens it full screen.

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
                                 preview_status, created_after, created_before, limit, cursor,
                                 semantic (text: order by meaning, nearest first;
                                 needs the ml service once vectors exist, 503 if
                                 it's down; the cursor is then an offset)
GET    /api/items/{id}
PATCH  /api/items/{id}           filename, rating, notes, source_url, created_at,
                                 folder_ids (replaces every membership; use
                                 PATCH .../folders to add or remove one).
                                 base_modified_at: the modified_at you last saw;
                                 if the item changed since, 409 with
                                 {"detail": {"message", "item"}} instead of a write
DELETE /api/items/{id}           to trash; ?hard=true deletes permanently (trashed items
                                 only: 409 otherwise)
POST   /api/items/{id}/restore
POST   /api/items/{id}/preview   queue another attempt at the item's previews
PUT    /api/items/{id}/tags      {"tags": [...]}
PUT    /api/items/{id}/folders   {"folder_ids": [...]}
PATCH  /api/items/{id}/tags      {"add": [...], "remove": [...]}: changes only those,
PATCH  /api/items/{id}/folders   so concurrent writers never overwrite each other
GET    /api/items/{id}/file      the original
GET    /api/items/{id}/thumb     ?size=256|512|1024, 2048 (full-screen copy), 4096 (HEIC);
                                 &v=<preview_rev> makes the answer cacheable for good
GET    /api/tags                 with counts
DELETE /api/tags/unused          delete tags no item uses (trashed items count)
GET/POST/PATCH/DELETE /api/folders
GET/POST/PATCH/DELETE /api/smart-folders
GET    /api/duplicates           ?threshold=6 (perceptual-hash distance)
GET    /api/stats                includes failed_jobs and preview_failed
GET    /api/jobs                 ?status=pending|running|done|failed
POST   /api/jobs/{id}/retry      a failed job, with fresh attempts
DELETE /api/jobs/{id}            dismiss a failed or finished job
GET    /api/ai/status            {model, ml_ok, embedded, ocr_done, pending, jobs,
                                 window, window_open, helpers: [{name, last_seen, jobs_done}]}
POST   /api/ai/jobs/claim        helpers: {kinds, max, name} -> {model, jobs: [{id, kind,
                                 item_id, image_url, lease_seconds}]}
POST   /api/ai/jobs/{id}/result  helpers: {vector, model} | {text, model, lang, boxes} |
                                 {error}; X-Kist-Helper names the helper
GET    /api/version              {"version", "commit"}
GET    /api/health/deep          database, migrations, worker heartbeat, library disk,
                                 job queue, recent preview failures, last verify run
POST   /api/client-errors        the web UI reports its crashes here (logged, 30/min)
GET    /api/health               no auth; the database answers
```

Lists use cursor pagination: pass `next_cursor` back as `cursor` until it is
`null` (a cursor the server didn't make is a 400). Interactive docs are at
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
previews that failed in the last 24 hours, and the last daily `verify`
result. Point an uptime checker at it.

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
