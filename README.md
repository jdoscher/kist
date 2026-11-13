<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/lockup-dark.svg">
    <img src="docs/brand/lockup-light.svg" alt="Kist" width="260">
  </picture>
</p>

<p align="center"><strong>Keep what catches your eye.</strong><br>
A self-hosted visual library for people and their AI agents.</p>

<p align="center">
  <a href="https://github.com/jdoscher/kist/releases"><img alt="Latest release" src="https://img.shields.io/github/v/release/jdoscher/kist?color=8C5A00&label=release"></a>
  <a href="LICENSE"><img alt="MIT licence" src="https://img.shields.io/badge/licence-MIT-1C1B19"></a>
  <a href="https://github.com/jdoscher/kist/pkgs/container/kist"><img alt="Docker image" src="https://img.shields.io/badge/docker-ghcr.io%2Fjdoscher%2Fkist-1C1B19"></a>
</p>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/screenshots/grid-dark.webp">
  <img src="docs/assets/screenshots/grid-light.webp" alt="Kist's grid: Hokusai's Great Wave, an Olivetti poster, chip dies, Fallingwater, a Porsche 356 and other public-domain images, with folders and boards on the left">
</picture>

Kist keeps the images, screenshots, PDFs, video and design files you collect
in one fast grid on your own server. Every item is a plain folder on disk (the
untouched original, its previews and a `meta.json`), so `rsync` is a backup and
the database can be rebuilt from the files at any time. Search finds things by
meaning, colour and the text inside them, all on your own hardware. And an
**MCP endpoint is built in**: Claude and other agents can search your library,
*see* what's in it, add to it and file it, and every change they make undoes in
one step.

- **Plain files on disk.** Back it up with `rsync`; `kist rebuild-index`
  restores everything (tags, history, AI results, boards) from the files.
- **One small stack.** `docker compose up`: the app, a worker, Postgres and a
  CPU-only AI service. Runs fine on a home server or NAS.
- **Agents as first-class users.** Search, view (the model receives the image
  itself), add, tag and file over MCP. Nothing an agent does can permanently
  delete anything.

Single user, named tokens for phones, family and agents. MIT licensed.
Documentation: [docs/](docs/index.md) (also builds into a site:
[site/mkdocs.yml](site/mkdocs.yml)).

## What's new in 2.0

- **Find anything.** Search by meaning ("moody dark interiors"), by a colour,
  by the words inside screenshots and PDFs, by an image, or "more like this":
  OpenCLIP and RapidOCR on ONNX Runtime, on the CPU, no cloud. Optional
  captions (Ollama at home, or your own key) and opt-in faces.
- **Stay tidy.** Tag aliases and implied tags, duplicates with a suggested
  keeper, RAW+JPEG and version stacks, suggested collections, tag suggestions
  with reasons. Every change is in the item history and undoes as a batch.
- **Creative workflows.** Moodboards, pick/reject culling, colour labels,
  annotations pinned to a spot or a video time, versions and compare, triage,
  a ⌘K palette.
- **More than images.** Video scrubbing, clips and frames; audio waveforms;
  RAW and HEIC with camera data; SVG, fonts, 3D models, design files and zip
  bundles; a timeline and a self-hosted map.
- **In and out.** Importers for Eagle, Pinterest, Are.na, Raindrop, Google
  Takeout and Apple Photos; share links; BagIt exports that browse offline;
  export presets; provenance and licences; XMP sidecars; a link-rot watch.
- **On your phone.** Install it to the Home Screen; edit stars, tags and
  folders with your thumb; it opens offline and badges what's left in Unfiled.
- **Light, dark and gallery grey** (the images and the whole interface on
  one neutral mid-grey, for judging colour).

<p align="center">
  <img src="docs/assets/demo/demo.gif" alt="A 19-second loop: the tiles grow and the grid scrolls, the Great Wave opens in the info panel and full screen, a blue from its palette finds matching items, then a board opens" width="720">
</p>

## How it compares

Kist sits between a desktop asset manager and a photo server. Facts as of
November 2026, from each project's own site:

| | Kist | Eagle | PureRef | Immich |
|---|---|---|---|---|
| What it is | self-hosted visual library | desktop asset library | desktop reference board | self-hosted photo and video backup |
| Runs on | your server (Docker); any browser, installable on a phone | macOS, Windows | Windows, macOS, Linux | your server (Docker); iOS and Android apps, browser |
| Price, licence | free, MIT | one-time US$34.95 (two devices), proprietary | personal: pay what you want; commercial: paid; proprietary | free, AGPL-3.0 |
| Built for | references, screenshots, design files, saved web images | references and design assets | arranging images on a canvas while you work | your camera roll: phone backup, albums, sharing |
| Storage | plain files + `meta.json` per item | a `.library` folder + JSON per item | one `.pur` scene file | originals on disk, metadata in Postgres |
| Search by meaning / text in images | yes / yes, built in and local | with its AI Search plugin (offline) / with plugins | no | yes (CLIP) / yes (OCR, since 2.2) |
| Agents (MCP) | built into the server | an MCP plugin (beta) beside the desktop app | no | community MCP servers |

Coming from Eagle: `kist import eagle <path>` brings folders, tags, ratings,
notes, source URLs and the trash across ([docs/coming-from-eagle.md](docs/coming-from-eagle.md)).

## Quick start

```bash
git clone https://github.com/jdoscher/kist.git && cd kist
cp .env.example .env
# set KIST_TOKEN in .env, e.g. to the output of: openssl rand -hex 24
mkdir library && sudo chown 1000:1000 library   # Kist runs as uid 1000
docker compose up -d --build
```

Kist runs as uid/gid 1000 (`KIST_UID`/`KIST_GID` in `.env` change that). If
Docker creates `./library` itself it belongs to root, and the app and worker
stop at start with one line saying which `chown` to run.

Open `http://<host>:8080`, paste your token, and drop files in. Upgrading
from 1.5: read [docs/upgrade.md](docs/upgrade.md) first (the database image
changes to `pgvector/pgvector:pg16`).

| service | role |
|---|---|
| `app` | REST API, MCP endpoint at `/mcp`, and the web UI |
| `worker` | same image: ingests inbox drops and retries queued jobs |
| `db` | Postgres 16 with pgvector (`pgvector/pgvector:pg16`): the search index (the library on disk is the durable copy) |
| `ml` | internal only: image embeddings (OpenCLIP ViT-B-32) and OCR (RapidOCR) on ONNX Runtime; see [AI](#ai-search-by-meaning-colour-and-the-text-in-images) |

### Connect an agent

```bash
claude mcp add --transport http kist http://<host>:8080/mcp \
  --header "Authorization: Bearer <token>"
```

Then ask things like "find the navy blazer photos I saved and file them into
Menswear" or "undo the filing you just did". Give agents their own `agent`
token (Settings → Tokens): it can edit but never delete for good, and the
history shows its name. All tools: [MCP](#mcp-claude-and-other-agents).

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
| `KIST_AI_ENABLED` | `true` in compose | Queue embedding/OCR jobs for new items (`false` outside compose, where there's no ml service) |
| `KIST_ML_URL` | `http://ml:8000` in compose | The ml service the worker sends AI jobs to. Empty: the worker runs none itself (a `kist helper` still can) |
| `KIST_ML_MODEL` | `ViT-B-32__openai` | Embedding model, for the ml service, app and worker alike. `ViT-B-16-SigLIP2__webli` is the opt-in alternative (768-d, ~2.5× slower, 1.5 GB of files) |
| `KIST_AI_WINDOW` | — | When backfill jobs may run, e.g. `01:00-06:00` (local time of the containers' `TZ`; may wrap past midnight). Empty: any time. New items' jobs never wait for it |
| `KIST_AI_CONCURRENCY` | `2` | AI jobs the worker runs at once (on their own threads: imports never wait behind them) |
| `KIST_AI_EMBED_BATCH` | `16` | Embed jobs one worker thread sends to the ml service in one request (at most 32; `1` sends them one by one) |
| `KIST_CAPTION_PROVIDER` | — | Captions (off when empty): `ollama`, or bring your own key for `openai`, `anthropic` or `gemini`. **The last three send every image you caption to that company** |
| `KIST_CAPTION_URL` | — | `ollama`: its address, e.g. `http://ollama:11434` (empty: a `kist helper --kinds caption` on a Mac with Ollama does them). `openai`: an OpenAI-compatible server's `/v1` address instead of api.openai.com (on your own network, e.g. LM Studio or vLLM, images stay at home and Kist doesn't warn) |
| `KIST_CAPTION_MODEL` | — | e.g. `qwen2.5vl:3b` (Ollama), `gpt-4o-mini`, `claude-haiku-4-5`, `gemini-2.5-flash` |
| `KIST_CAPTION_API_KEY` | — | The key for `openai`, `anthropic` or `gemini` |
| `KIST_CAPTION_AUTO` | `false` | Caption every new item automatically (otherwise only on request and by `kist ai-backfill --kind caption`) |
| `KIST_CAPTION_PROMPT` / `KIST_CAPTION_TIMEOUT_SECONDS` | built in / `180` | The instruction sent with each image; one caption's time limit |
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
| `KIST_FACES` | `0` | Faces and people, opt-in. Set it for the app, worker **and** ml service: the worker then queues a `faces` job per image, and only then does the ml service download (~39 MB) and load the face models. See [Organising](#organising-vocabulary-duplicates-stacks-collections-people) |
| `KIST_FACE_MODELS_URL` | — | A mirror for the two face model files (they're checked by sha256 either way) |
| `KIST_COLLECTIONS_INTERVAL_HOURS` | `24` | How often the worker recomputes suggested collections; `0` = only on request (`?refresh=1`) |
| `KIST_EXPORT_RETENTION_DAYS` | `7` | Finished portable exports (`library/exports/`) are deleted after this many days; `0` keeps them |
| `KIST_SNAPSHOTS` | `false` | Let `POST /api/items/{id}/snapshot` with no body fetch the item's page on the server (through the same SSRF guard as URL adds). Off: only snapshots the browser sends |
| `KIST_LINK_CHECK_DAYS` | `0` | Off by default. Set a number of days to have the link-rot watch re-check every item's source page that often; what it finds is shown in the item's info panel only, never as something that needs attention |
| `KIST_LINK_CHECK_DELAY_SECONDS` | `2` | Pause between two link checks (and never the same host within 10 s) |
| `KIST_WAYBACK` | `false` | Ask the Wayback Machine (archive.org, an outside service) for an archived copy of sources that are gone. Off by default: it tells archive.org which pages you kept |
| `KIST_XMP_SIDECARS` | `true` | Write `items/<id>/original.xmp` (rating, keywords, notes, label, creator, rights, provenance) for images on every change, for Lightroom, darktable and digiKam |
| `KIST_SHARE_PASSWORD_ATTEMPTS` | `10` | Wrong share-link passwords allowed per link per 15 minutes (then 429) |

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
webm, pdf, and font files (ttf, otf, woff, woff2: a specimen with the family
name and "Aa Bb Cc 0123 The quick brown fox"; `?type=font` lists them),
judged by the file's contents rather than its name (an HTML
page named `invoice.pdf` is just a file). Anything else is still stored and
listed with a generic icon, and its original is only ever offered as a
download: ingest never rejects a file for its format.

Exact duplicates (same SHA-256) are detected at ingest and return the
existing item. Near-duplicates (perceptual-hash distance) are available from
`/api/duplicates` and the `find_duplicates` MCP tool.

## AI: search by meaning, colour and the text in images

Kist 2.0 understands what's in your images, entirely on your own hardware:

- **Embeddings.** Every item with a thumbnail gets a 512-number OpenCLIP
  ViT-B-32 vector (from its 512-px thumbnail), which powers search by
  meaning, "more like this" and search by image (see Searching).
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
import or preview job; the worker runs `KIST_AI_CONCURRENCY` of them at once,
and sends embed jobs `KIST_AI_EMBED_BATCH` at a time.
If the ml service is down (or still downloading on first start), its jobs go
back to the queue without using up attempts and the worker pauses AI work
for a minute.

### Searching

All of these are parameters of `GET /api/items` (and of the MCP tool
`search_items`), and every other filter (tags with their aliases, implied
tags and namespaces, folder, type, rating, dates, trash, `person`, `flag`,
`label`) still applies:

| parameter | finds | ranked by |
|---|---|---|
| `q=orvis` | filename, notes, annotation text, and now the text in screenshots, PDFs and captions | the chosen `order` |
| `semantic=brown suede boots` | everything, best matches first | meaning (what the image shows) |
| `q=…&semantic=…` | both at once ("hybrid") | reciprocal rank fusion of the word and meaning ranks |
| `similar=<item id>` | more like that item (not itself) | visual similarity |
| `color=#2f4f3a&color_tol=30` | items with that colour in their palette (tolerance 0–100) | how much of the palette is near it (CIEDE2000) |
| `text=zero trust` | only the text inside images and PDFs (every word, as a prefix) | text relevance |
| `has_text=true` / `false` | items whose OCR found text / found none | — |
| `type=font` | font files | — |

`POST /api/search/image` takes an image file (multipart `file`, not
stored) and the same filters as query parameters, and returns items that
look like it.

Ranked results carry `match: {kind, score, snippet?}`: `kind` is `meaning`,
`similar`, `color`, `text`, `keyword` or `hybrid` (matched more than one
way), and `snippet` is the text around the hit with the words marked
`<<like this>>`. A plain `q` search keeps its order but also says
`keyword` and gives a snippet when the words were found inside the item.
Paging works as everywhere (pass `next_cursor` back; `total` on the first
page only). `stacks=collapse` shows each stack once: as its cover in a plain
list, and as its best-ranked member in a ranked one (the member that
matched, so its `match` is true; `stack_count` says there are more).
`GET /api/items/{id}/palette` gives an item's colours as
`[{hex, pct, name}]`, largest share first.

Palettes come from each thumbnail (octree into 16 colours, merged within
CIEDE2000 10, top 5), so a small red car in a grey street still counts as
red. Items added before 2.0 keep their older, muddier palettes until
`docker compose exec worker kist regen-previews --colors-only` (a few
seconds per thousand items).

### Captions (optional)

A sentence or two describing each image, for search and alt text. Off by
default. Set `KIST_CAPTION_PROVIDER`:

- `ollama` (nothing leaves the house): run [Ollama](https://ollama.com) with a
  vision model such as `qwen2.5vl:3b` (3.2 GB) and set `KIST_CAPTION_URL` and
  `KIST_CAPTION_MODEL`. On a NAS CPU expect tens of seconds per image, so
  caption on request or point at Ollama on a faster machine: with no
  `KIST_CAPTION_URL`, a Mac running `kist helper --kinds caption` (and
  Ollama) does them.
- `openai`, `anthropic`, `gemini`: bring your own key (`KIST_CAPTION_API_KEY`,
  `KIST_CAPTION_MODEL`). **Every image you caption is uploaded to that
  company.** `/api/ai/status` and the worker's log say so.

`POST /api/items/{id}/caption` captions one item now (202, a job);
`kist ai-backfill --kind caption [--limit N]` queues every item without one
(they wait for `KIST_AI_WINDOW`); `KIST_CAPTION_AUTO=true` captions new items.
The caption is `caption` on every item, searchable with `q`, kept in
`ai/caption.json`, and editable: `PATCH /api/items/{id} {"caption": "…"}`
(`""` removes it). Writing one, by a model or a person, is in the item's
history and can be undone.

### Speed and memory

Measured on an Apple M6 (CPU only, 4 threads, through the ml service over
HTTP; 100 real screenshots as 512-px WebP thumbnails):

| | per item | |
|---|---|---|
| image embedding (ViT-B-32) | 12 ms (11.7 ms inference) | 80 images/s from one client |
| text embedding (a search) | 9 ms | |
| OCR (screenshot, ≤2048 px) | 415 ms | 600 ms per item as a worker job |
| backfill through the worker (job, ml call, sidecars, Postgres) | 19 ms (1 at a time, M1), 11 ms (2 threads, batches of 16) | 53 → 84–99 items/s |
| the same with slow disk syncs (each fsync made to wait ~70 ms, like a busy NAS disk) | | 3.9 → 44 items/s |
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
Check the real numbers with `/api/ai/status` after a backfill. The NAS's
measured 6.5 items/s at one-at-a-time points at disk waits rather than the
CPU, which is what batches (sidecars written side by side, one database
commit per batch) and two threads address.

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
export KIST_HELPER_TOKEN=...   # a token with scope helper (see Tokens)
kist helper --url https://kist.example.com
```

Make the token with `POST /api/tokens {"name": "Mac mini", "scopes":
["helper"]}`: it can claim and report AI jobs and fetch their images, and
nothing else (no browsing, no edits). The helper shows in `/api/ai/status`
under the token's name unless `--name` says otherwise.

It claims jobs over HTTPS (`POST /api/ai/jobs/claim`), fetches each
job's thumbnail (`GET /api/ai/jobs/{id}/image`), runs the same ONNX models locally and posts the result; the
server checks it (model, size) and writes the sidecars. Stopping it loses
nothing: unreported jobs return to the queue when their lease
(`KIST_JOB_LEASE_SECONDS`) runs out. Scanned PDFs stay with the server.
With `--kinds caption` the helper captions with the Ollama on the Mac
(`--caption-url`, default `http://localhost:11434`; `--caption-model`,
default the server's `KIST_CAPTION_MODEL`). On an M6 the CPU is the right
device for embeddings: CoreML's Neural Engine is barely
faster for ViT-B-32 (7.8 vs 8.8 ms) and compiles the model for 40–75 s at
every start (`--device coreml` if you want it anyway).

## Organising: vocabulary, duplicates, stacks, collections, people

Kist 2.0 helps keep a growing library tidy. Nothing here changes an item on
its own: every change is a request (or MCP call) you or an agent makes, it is
recorded in the item history, and one undo reverts it.

- **Vocabulary.** An *alias* maps a variant to the preferred tag
  (`Fashion → Clothing`): adding one rewrites the items that carry it, and
  every later use (web, API, MCP, uploads, imports) is rewritten on the way
  in. An *implied tag* is virtual (`Boots → Shoes`): searching `#Shoes`
  finds items tagged Boots, and items show it greyed as `implied_tags`, but
  it is never written to them, so removing the rule cleans up everything.
  Loops are refused. Tags of the form `namespace:value` (`brand:Rolex`) are
  grouped by namespace (`GET /api/tags?namespace=brand`, search
  `tags=brand:*`). The rules live in `library/vocabulary.json`.
- **Tag cleanup.** `GET /api/tags/cleanup` lists tags used once, case or
  spacing clashes (`dark mode` / `dark-mode`), near-misspellings and plurals
  (`Staitionery`, `Restaurants`), tags unused since a date, and smart folders
  that name a tag or folder that doesn't exist, each with a proposal.
  `POST /api/tags/merge` (with `?dry_run=1` first) and `POST /api/tags/rename`
  apply one as a single undoable batch.
- **Tag suggestions.** `GET /api/items/{id}/tag-suggestions` proposes tags
  from the item's look-alikes ("similar items") and from comparing the
  picture with every tag's name ("matches the image", CLIP zero-shot).
  Never applied by themselves; rejected ones aren't suggested for that item
  again.
- **Duplicates.** `GET /api/duplicates/groups?kind=same_image` (perceptual
  hash within 4 bits and the same aspect ratio), `similar` (embeddings at
  cosine ≥ 0.92) or `exact` (same bytes; ingest already refuses those). Each
  group suggests a keeper (largest, then oldest, then with a source URL).
  `POST /api/duplicates/resolve` merges the others' tags, folders, notes,
  best rating and source URL into the keeper and trashes them. Pairs linked
  `not_duplicate` are never grouped.
- **Stacks.** Show a RAW and its JPEG, a burst or several versions as one
  item: `POST /api/stacks`, then `GET /api/items?stacks=collapse` lists only
  the covers with a `stack_count`. `GET /api/stacks/suggestions` finds RAW+JPEG
  pairs (same name, same EXIF time), bursts (same camera within 2 s) and
  `version_of` links. Membership is in each member's `meta.json`.
- **Suggested collections.** The worker clusters the embeddings once a day
  (numpy k-means, k chosen by silhouette) and keeps groups of 8+ look-alike
  items that aren't already one folder, each with a label from their common
  tags or zero-shot over the tag names: "You seem to collect…". Accept one to
  make a folder (or a tag plus a smart folder) holding them.
- **Faces and people (opt-in, `KIST_FACES=1`).** OpenCV-zoo's YuNet finds
  faces and SFace describes them, on your own hardware, in the ml container
  (pre- and post-processing in numpy, no OpenCV). Faces of one person are
  grouped into unnamed people for you to name (`PATCH /api/people/{id}`),
  merge, and search (`GET /api/items?person=`). Results live in
  `items/<id>/ai/faces.json` and `library/.kist/people.json`, so a rebuild
  keeps every name. Off by default: face data is personal, and nothing is
  computed or downloaded until you turn it on.

Model files for faces (Apache-2.0; opencv_zoo commit `48b10fa`):

| file | sha256 |
|---|---|
| `face_detection_yunet_2023mar.onnx` (227 KB) | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` |
| `face_recognition_sface_2021dec.onnx` (37 MB) | `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79` |

For existing items: `docker compose exec worker kist ai-backfill --kind faces`.

## Creative workflows: boards, culling, annotations, versions

The backend for the boards canvas, compare, triage, quick look and the ⌘K
palette (the web screens follow):

- **Boards** are moodboards: items, notes, text, shapes, groups and arrows on
  a canvas, kept as `library/boards/<id>/board.json` with a rendered
  `thumb.webp`, exported as PNG, PDF or one self-contained HTML file. Saves
  carry the version they started from, so two editors never overwrite each
  other (409). A deleted board goes to its own trash; a deleted item stays
  on its boards as a "missing item".
- **Flags and labels:** pick / reject and six colour labels, for culling.
  Filter with `flag=` and `label=`, in every kind of search.
- **Annotations** pin a note to a point, a box or a stretch of video; their
  text is found by `q`.
- **Versions:** upload a new version of an item (`POST
  /api/items/{id}/versions`); it is linked `version_of` the newest, takes its
  folders and tags, and is stacked with the older versions as the cover (it
  joins their stack, or the chain becomes one).
  `GET /api/items?ids=a,b,c` fetches items to compare side by side.
- **Templates** apply a set of tags, folders, notes, rating, flag, label,
  credit and licence (credit and licence become `credit:` and `license:`
  tags) to many items as one batch; **bulk rename** takes a pattern
  (`{name}`, `{n:3}`, `{date:%Y%m%d}`, `{w}x{h}`, `{folder}`, `{tag:ns}`) with a
  dry run first.
- **Triage** walks a view one item at a time; the **command palette**
  searches folders, smart folders, tags, boards, people (when faces are on)
  and items at once.

Everything is in the item history and undoes as a batch (board edits too),
and `kist rebuild-index` restores boards, annotations, flags, labels and
templates from their files.
## Video, audio, photos, design files, 3D and fonts

Kist 2.0 looks inside more than images. In the web app: video and animated
GIF tiles scrub as you move the pointer across them (desktop); the
full-screen view plays video and audio (Set cover, Save frame, Clip…),
copies an SVG or its colours, shows a font's specimen, axes and glyphs,
turns a 3D model (Set poster renders the thumbnail) and lists what's inside
a bundle. The info panel shows camera and media details, **Filters** in the
top bar narrows by date taken, camera, lens, place, location, length and
licence, and the sidebar has **Timeline** (with On this day) and **Map**
(MapLibre with the self-hosted PMTiles basemap; picking a group shows its
items).

- **Video**: hover-scrub sprites (20 frames, one WebP), duration, fps,
  codec, bitrate, rotation and audio from ffprobe. Pick any frame as the
  cover, save a frame as its own image, or cut a clip to MP4, GIF or
  animated WebP (a worker job). Files a browser can't play (ProRes, HEVC,
  AVI, MKV with odd codecs) get a web-safe H.264 proxy the first time they're
  played; originals are always served with HTTP Range. Animated GIF and WebP
  get the same scrub sprite. Agents can watch a video with MCP `view_video`.
- **Audio** (mp3, wav, flac, aac, m4a, ogg, aiff): a waveform thumbnail,
  peaks for the player, duration, artist and title; clips to mp3/wav.
- **Photos**: camera, lens, focal length, aperture, shutter, ISO, date
  taken and GPS from EXIF; filters for all of them, sort by date taken, a
  timeline, On this day, and a map. Place names ("Kyoto, Japan") come from
  GeoNames, offline: run `docker compose exec worker kist geo-download` once.
  Originals are never changed, so a downloaded original still carries its
  GPS position: share exports (which strip it) instead of originals when
  that matters.
- **RAW** (CR2, CR3, NEF, ARW, RAF, ORF, RW2, DNG, PEF): previews from the
  camera's embedded JPEG, in milliseconds.
- **Design files**: thumbnails from the preview PSD/PSB, AI, EPS, Sketch,
  Procreate, Krita, OpenRaster, Affinity and Blender files carry inside.
  Figma `.fig` files have none: they show an icon until you upload a poster.
- **SVG**: sanitized (no script, no external references) for thumbnails and
  "Copy SVG", with its exact fill and stroke colours.
- **3D** (glb, gltf, obj, fbx, stl, usdz, ply): bounds and triangle counts;
  the web shows them with `<model-viewer>` and saves a poster as the
  thumbnail. OBJ, STL and PLY are converted to GLB when `trimesh` is
  installed (it is in the image).
- **Fonts**: family, style, glyphs, variable axes and scripts.
- **Bundles**: a zip (a model with its textures, a texture set, a kit) is
  kept whole, listed, previewed from the best picture inside, and single
  files can be opened from it.

Items added before 2.0 get all this with
`docker compose exec worker kist media-backfill`.

ffmpeg runs under `nice` with `KIST_FFMPEG_THREADS` threads (default 2), and
clips and proxies wait behind imports and previews. An Intel iGPU can encode
proxies and clips: set `KIST_HWACCEL=vaapi` (or `qsv`) and pass `/dev/dri`
to the worker (see `docker-compose.yml`).

## Keyboard

The web app's shortcuts, one table per screen. No key does two things on
the same screen; keys typed into a text field (or a select) never trigger
them, and an open dialog or preview stops the keys of what's behind it. The
table is `web/src/keymap.ts`; `web/tests/keymap.test.ts` keeps this section,
the handlers and the table in step.

<!-- keymap:start -->
**Library grid**

| Keys | Does |
| --- | --- |
| `←` `→` `↑` `↓` | move and select |
| `⇧ ←` `⇧ →` `⇧ ↑` `⇧ ↓` | extend the selection |
| `⌘/Ctrl ←` `⌘/Ctrl →` `⌘/Ctrl ↑` `⌘/Ctrl ↓` | move without selecting |
| `Home` `End` | first, last item |
| `Space` | tap: add or remove the item; hold: quick look |
| `Enter` | open full screen |
| `Escape` | clear the selection |
| `⌘/Ctrl A` | select all |
| `Delete` `Backspace` | move the selection to the Trash (in the Trash: delete forever) |
| `S` | open or close the focused stack |
| `C` | compare the selection (2–4 items) |
| `N` | survey the selection: pick and reject one by one |
| `ContextMenu` | the item menu (More like this, colour, stack) |
| `⌘/Ctrl Z` | Recent changes, focused on the newest Undo |
| `⌘/Ctrl K` | command palette |

**Quick look (holding Space over a tile)**

| Keys | Does |
| --- | --- |
| `Space` | release: close (once latched, press again) |
| `Z` | loupe (100%) on or off; latches the peek open |
| `Escape` | close |

**Full-screen preview**

| Keys | Does |
| --- | --- |
| `→` `↓` | next item |
| `←` `↑` | previous item |
| `Escape` | cancel a note, tool or loupe; else close |
| `A` | show or hide notes (annotations) |
| `Z` | loupe on or off (images) |
| `M` | note at the current time (video) |
| `I` | mark in (video) |
| `O` | mark out: a note over the range (video) |
| `⌘/Ctrl K` | command palette |

**Triage (/triage)**

| Keys | Does |
| --- | --- |
| `P` | pick |
| `X` | reject |
| `U` | unflag |
| `0` `1` `2` `3` `4` `5` | rating (0 clears) |
| `6` `7` `8` `9` | red, orange, yellow, green label (again: clear) |
| `T` | tag |
| `F` | file in a folder |
| `→` `J` | next |
| `←` `K` | previous |
| `⌘/Ctrl Z` | undo the last change |
| `Delete` `Backspace` | move to the Trash |
| `Escape` | leave triage |
| `⌘/Ctrl K` | command palette |

**Compare and survey**

| Keys | Does |
| --- | --- |
| `P` | pick the current item |
| `X` | reject it |
| `U` | unflag it |
| `1` `2` `3` `4` `5` | rating (survey) |
| `6` `7` `8` `9` | red, orange, yellow, green label |
| `→` `J` | next item |
| `←` `K` | previous item |
| `+` `=` | zoom in (all panes) |
| `-` | zoom out |
| `0` | fit |
| `Escape` | close |
| `⌘/Ctrl K` | command palette |

**Board canvas (/board/…)**

| Keys | Does |
| --- | --- |
| `V` | select tool |
| `H` | hand (pan) tool |
| `C` | connect (arrow) tool |
| `N` | add a note |
| `T` | add text |
| `R` | add a shape |
| `Space (hold)` | pan while held |
| `←` `→` `↑` `↓` | nudge 1px |
| `⇧ ←` `⇧ →` `⇧ ↑` `⇧ ↓` | nudge a grid step |
| `Tab` `⇧ Tab` | next, previous thing on the canvas |
| `Delete` `Backspace` | delete the selection or arrow |
| `⌘/Ctrl D` | duplicate |
| `⌘/Ctrl G` | group |
| `⇧ ⌘/Ctrl G` | ungroup |
| `⌘/Ctrl A` | select all |
| `⌘/Ctrl Z` | undo |
| `⇧ ⌘/Ctrl Z` `⌘/Ctrl Y` | redo |
| `+` `=` | zoom in |
| `-` | zoom out |
| `0` | zoom to fit |
| `1` | zoom to 100% |
| `Escape` | clear the selection, back to the select tool |
| `⌘/Ctrl K` | command palette |
<!-- keymap:end -->

## Storage layout

```
library/
├── inbox/                  # drop zone, watched by the worker
├── import/                 # files agents may add via MCP add_item(file_path)
├── failed/                 # inbox files whose import failed, under their own names
├── folders.json            # folder tree (with descriptions) + smart folders snapshot
├── vocabulary.json         # tag aliases and implied tags
├── vocabulary-events.jsonl # their history, so undoing a tag merge drops its alias
├── exports/                # portable exports ({id}.zip + {id}.json), auto-deleted
├── .kist/                  # worker heartbeat, last verify result, failed-write notes,
│                           # tokens.json (hashed tokens), events-purged.jsonl,
│                           # backup.json, db.sql.gz, checklist.json, retention.json,
│                           # people.json (names + faces), collections.json (a cache),
│                           # templates.json (metadata templates),
│                           # board-events-purged.jsonl,
│                           # shares.json (share links, hashed passwords),
│                           # export-presets.json
├── boards/
│   └── {ulid}/
│       ├── board.json      # the board: nodes, edges, canvas, version
│       ├── thumb.webp      # a small rendered snapshot
│       └── events.jsonl    # the board's history (append-only)
├── .staging/               # files on their way in (the worker re-queues strays)
└── items/
    └── {ulid}/
        ├── original.{ext}  # the untouched original
        ├── thumb_256.webp  # longest side 256
        ├── thumb_512.webp  # short side 512 (grid tiles)
        ├── thumb_1024.webp # longest side 1024 (preview, MCP view_item)
        ├── display.webp    # HEIC and RAW: full-resolution copy browsers can show
        ├── scrub.webp      # video / animated GIF+WebP: hover-scrub sprite (+ scrub.json)
        ├── waveform.json   # audio: peaks (BBC audiowaveform JSON)
        ├── proxy.mp4       # video browsers can't play: web-safe H.264 copy (on first play)
        ├── model.glb       # OBJ/STL/PLY converted for the 3D viewer (needs trimesh)
        ├── bundle.json     # zip bundles: what's inside
        ├── sanitized.svg   # SVG: what "Copy SVG" returns
        ├── poster.png      # a poster you uploaded (3D models, .fig ...): back it up
        ├── meta.json       # all metadata (incl. links, protected, flag, label,
        │                   # provenance, media), rewritten on every change
        ├── annotations.json # notes pinned to points, boxes and video times
        ├── events.jsonl    # the item's history, one line per change (append-only)
        ├── fixity.jsonl    # every re-hash of the original: {at, sha256, ok}
        ├── original.xmp    # images: XMP for other photo tools (KIST_XMP_SIDECARS)
        ├── page/           # page snapshot: snapshot.html, text.txt, snapshot.json
        └── ai/             # AI results (rebuild-index loads them, never recomputes)
            ├── embed-ViT-B-32__openai.f16   # raw little-endian float16 vector
            ├── embed-ViT-B-32__openai.json  # {model, dim, created_at, kist_version}
            ├── ocr.json                     # {model, lang, text, boxes, source, created_at}
            ├── caption.json                 # {text, model, provider, created_at, edited}
            ├── faces.json                   # KIST_FACES: {model, faces: [{box, landmarks,
            │                                #   score, embedding}]} (boxes as fractions)
            └── tag-feedback.json            # accepted/rejected tag suggestions
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
protection, links, stacks, flags, labels, ThumbHash and preview versions,
each item's history from its `events.jsonl` (and purged items' from
`.kist/events-purged.jsonl`), fixity dates from `fixity.jsonl`, AI results
(embeddings, OCR text, captions, faces, tag feedback) from `ai/`,
annotations from `annotations.json`, the vocabulary from `vocabulary.json`
(and its history from `vocabulary-events.jsonl`),
people from `.kist/people.json`, templates from `.kist/templates.json`, and
boards with their history from `boards/`. Nothing is recomputed. `--wipe` clears the tables first, and
refuses to start if `folders.json` can't be read. Nothing on disk is skipped
silently: an item folder with no `meta.json` is restored from its original
file (name, tags and notes were only in `meta.json`, so it comes back as
`recovered-<id>.<ext>`), a folder whose parent is missing or that sits in a
parent loop is restored at the top level, and each of these is printed with
`!` and makes the exit code 2. Other maintenance commands:

- `kist rewrite-meta`: rewrites every `meta.json`, `events.jsonl`,
  `folders.json`, `tokens.json`, `vocabulary.json`, `vocabulary-events.jsonl`, `.kist/people.json`,
  tag feedback, `annotations.json` and the board files from the database.
  Safe while Kist is running: it re-reads each item as it writes it.
- `kist regen-previews [--all]`: generates missing or failed previews, or all of them.
  It also adds the 2048 px full-screen copy to older images that lack one,
  without touching their other previews. `--thumbhash-only` just adds missing
  ThumbHash placeholders (the worker also does this in the background);
  `--colors-only` just recomputes palettes (after upgrading to 2.0).
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

## Sharing, exporting and importing

In the web app: **Share** (the top bar's share button for a folder or
smart folder, the selection bar, a board's toolbar) makes a link with an
optional password, an expiry and download switches; Settings → Sharing
lists every link with its views and revokes them. **Export** packs a
folder, selection or the whole library; **Export images** makes resized,
watermarked copies or a contact sheet from saved presets. Settings → Import
runs the importers below (upload the export or name a server path; dry run
first), and Settings → Phone & browser has the iPhone Shortcut steps and
the `/bookmarklet` page (its `/add` confirm card saves with your signed-in
session; it never holds a token). The info panel shows and edits where an
item came from, its licence and whether it's AI-generated, and opens the
saved copy of its page.

**Share links.** `POST /api/shares {target_kind: folder|smart|board|items,
target_id | item_ids, title?, password?, expires_in_days? (30; 0 = never) |
expires_at?, allow_download?, allow_original?}` (admin or write token) makes
a read-only page at `/s/<slug>` anyone with the link can open, with no token
and no account. It is plain HTML with no script, under a sandboxing CSP,
`noindex` and `Referrer-Policy: no-referrer`; `robots.txt` disallows `/s/`.
A share serves only the items it covers *right now* (a smart-folder share
stays live; trashed items never show), the original only with
`allow_original`, and the full-screen copy as a download with
`allow_download`. A password share sets an HttpOnly cookie for that link
only after the right password (scrypt-hashed; 10 wrong tries per link per
15 minutes, then 429). Expired or revoked links answer 410. Views are
counted without recording who: the link's visitors are left out of Kist's
access log too. Shares are kept in `library/.kist/shares.json`.

**Portable export.** `POST /api/exports {target: folder|smart|items|all,
target_id?, item_ids?, include: {originals, sidecars, events}}` builds a
BagIt 1.0 zip in the background (`GET /api/exports/{id}` for status,
`…/download` for the file): originals under readable names in
`data/<folder path>/`, a `.json` and `.xmp` beside each, `items.csv`, and an
`index.html` that browses the whole thing offline from a USB stick.
`manifest-sha256.txt` lets any BagIt tool check every file. Exports are
deleted after `KIST_EXPORT_RETENTION_DAYS`.

**Export presets.** `POST /api/items/export-images {item_ids, preset}`
answers a zip of resized copies (`max_side`, `format` jpg/webp/png/avif,
`quality`, a text `watermark`) or, with `contact_sheet: {cols, caption}`, a
PDF contact sheet. Metadata is stripped by default and GPS always is.
Presets are saved in `library/.kist/export-presets.json`
(`/api/export-presets`); `web-2048`, `full-res`, `instagram-1080`,
`client-proof` and `contact-sheet` come built in.

**Provenance.** Every item has a `provenance` block: `page_url` (the same
as `source_url`), `image_url`, `page_title`, `captured_at`, `method`
(upload, paste, url, extension, inbox, mcp, import), `creator`, `credit`,
`license`, `rights_url`, `ai_generated`, `digital_source_type`, and for
imports `imported_from` / `original_id`. Kist fills what it knows on ingest,
reads XMP (embedded, or a `.xmp` beside an inbox file) for ratings,
keywords, notes, creator and rights, and marks `ai_generated` from IPTC
`DigitalSourceType` or a C2PA manifest. `PATCH /api/items/{id}
{provenance: {...}}` edits it (null removes a field; undoable), and
`GET /api/items?ai_generated=true|false|unknown&license=CC0` filters.

**XMP sidecars.** With `KIST_XMP_SIDECARS` (on), every image has an
`items/<id>/original.xmp` that Lightroom, darktable and digiKam read; Kist
merges into an existing one, keeping properties it doesn't manage.
`kist xmp --export DIR` writes `<name>.xmp` beside files you exported
(matched by content), `kist xmp --rewrite` refreshes them all.

**Page snapshots.** `POST /api/items/{id}/snapshot {html, page_url, title,
captured_at}` (or a raw `text/html` body) keeps the page an item came from
(as the browser extension captured it) in `items/<id>/page/`, with scripts
stripped; its text is found by `q` (not by `text=`, which is the text inside
images and PDFs). `GET /api/items/{id}/snapshot` serves it sandboxed. With
`KIST_SNAPSHOTS=1`, sending no body makes the server fetch `page_url` once.
The extension's add-only token may snapshot an item it added itself in the
last 10 minutes; anything else needs a write, agent or admin token. A
folder-limited reader doesn't see snapshots.

**Link-rot watch (off by default).** With `KIST_LINK_CHECK_DAYS` set, the
worker re-checks every source page that often, politely, and records
`source_status` (`ok`, `moved`, `gone`, `error`) and `source_checked_at`.
It's informational: the item's info panel shows it and
`GET /api/items?source_status=gone` lists them (`/api/stats` still counts
`source_gone`), but a page that moved or went away is never shown as
something that needs attention, because your copy is what matters. `kist link-check` runs a
batch now. `KIST_WAYBACK=1` also records an archived copy's URL.

**Importers.** `kist import <kind> <path|url> [--dry-run] [--folder NAME]`,
or `POST /api/imports {kind, source, dry_run?, folder?}` (write or admin; a
path must be inside `library/import/` or the inbox; `GET /api/imports/{id}`
shows progress). Each run files into its own folder, `Imported/<kind>
<date>`, keeps provenance, and is one history batch (undo it with
`POST /api/batches/{batch_id}/undo`). A dry run reports counts, duplicates
and errors and changes nothing.

| kind | source |
|---|---|
| `pinterest` | the account data export zip (pins are downloaded from their image link or signature, else the source page's preview image; boards become folders) |
| `arena` | a public channel URL (`https://www.are.na/<user>/<channel>`, through the public API, no key) or a "Download channel" zip |
| `raindrop` | Raindrop.io's CSV or HTML export (cover image, else the page's preview image; collections become folders) |
| `takeout` | Google Photos Takeout (JSON sidecars, including the `.supplemental-metadata.json` and cut-short names: date, description, geo, favourite) |
| `apple-photos` | a folder from `osxphotos export DEST --sidecar json` (title, description, keywords, people, rating, date, GPS; sub-folders become folders) |
| `sidecar` | any folder of files with `.json` and/or `.xmp` sidecars |
| `eagle` | an Eagle `.library` folder (folder tree, tags, ratings, notes, source URL, trash, simple smart folders) |

**iPhone.** A "Save to Kist" Shortcut in the share sheet posts images, files
and links to `POST /api/items` with an add-only token; send `Accept:
text/plain` and Kist answers `Saved to Kist: <name>`. Step by step:
[docs/ios-shortcut.md](docs/ios-shortcut.md) (or `GET /api/shortcut`).

## MCP (Claude and other agents)

Streamable-HTTP endpoint at `http://<host>:8080/mcp`, using the same bearer token:

```bash
claude mcp add --transport http kist http://<host>:8080/mcp \
  --header "Authorization: Bearer $KIST_TOKEN"
```

| tool | |
|---|---|
| `search_items` | `query` (filename, notes, annotation text, text in images and captions, a saved page's text, the place name), tags (all / any / none; aliases, implied tags and `brand:*` namespaces match), folder, type (image, video, pdf, audio, model, font, design, bundle, other), rating, `view="unfiled"` (the inbox), `state="trashed"`, `preview_status`, `flag`, `label`, `person`, `collapse_stacks`; provenance `ai_generated` (true/false/unknown), `license`, `source_status`; media `taken_after`/`taken_before` (ISO dates), `camera`, `lens`, `has_gps`, `place`, `vertical`, `has_audio`, `duration_min`/`duration_max`; `order` also `-taken`/`taken`; ranked by `semantic` (a description: what the images show), `similar_to` (an item id), `color` (+ `color_tol`), `text` (words inside screenshots and PDFs); `has_text`. Every filter works with every ranking. Ranked items carry `match {kind, score, snippet}` |
| `search_by_image` | items that look like an image given by URL or a file under `library/import/` (nothing is saved); the main filters (query, tags, folder, type, rating, color, flag, label, `person`, `collapse_stacks`, and the provenance and media filters) |
| `item_palette` | an item's colours `[{hex, pct, name}]` |
| `get_item` | full metadata |
| `view_item` | returns the image itself so the model can see it |
| `add_item` | from a URL, or a file under `library/import/` or `library/inbox/` |
| `update_item`, `set_tags`, `set_folders` | organize. `update_item(caption=…)` sets the caption, `flag` (`pick`, `reject`) and `label` (`red`, `orange`, `yellow`, `green`, `blue`, `purple`; `none` clears either) cull; `update_item(folder_id=…)` adds a folder and keeps the others; `set_folders` adds and removes; `update_item(protected=true)` protects an item (only a write or admin token can unprotect) |
| `list_folders`, `create_folder`, `list_tags`, `list_smart_folders` | browse. Folders carry a `description` saying what belongs in them |
| `link_items` | `derived_from`, `alternate`, `related`, `not_duplicate`, `version_of` links between items |
| `recent_batches`, `undo_batch` | what was changed lately, by whom, and undo a batch (e.g. "undo the filing you just did") |
| `prune_unused_tags` | deletes tags no item uses (tags on trashed items count) |
| `trash_item`, `restore_item` | into and out of the trash |
| `retry_preview` | queues another attempt at a failed preview |
| `list_jobs`, `retry_job` | inbox imports and preview retries, and why one failed |
| `find_duplicates`, `library_stats` | housekeeping |
| `get_vocabulary` | preferred tags, aliases, implied tags and namespaces: read it before tagging (`set_tags` rewrites aliases anyway) |
| `suggest_tags` | tag suggestions for an item with why; apply the good ones with `set_tags` |
| `tag_cleanup`, `merge_tags` | cleanup suggestions, and merging tags as one undoable batch (`dry_run` first) |
| `duplicate_groups`, `resolve_duplicates` | duplicate groups with a suggested keeper; keep one, merge the rest into it and trash them |
| `suggest_stacks`, `stack_items` | RAW+JPEG, burst and version stacks to propose, and stacking (or `unstack`) |
| `suggested_collections`, `list_people` | "you seem to collect…" clusters; people found by faces (when on) |
| `list_boards`, `get_board` | boards (moodboards) and their nodes |
| `create_board(name, item_ids, layout)`, `add_to_board(id, item_ids, layout, notes)` | lay items out as a `grid` or `masonry` board, add more items and notes |
| `list_shows()`, `create_show(name, source_kind, source_id, item_ids, compare, pairs, surround, captions, filenames, ken_burns, advance_seconds, gesture_seconds)` | saved Present shows (full-screen slideshows) of a folder, smart folder, board or chosen items; returns the show's `web_url` |
| `annotate(item_id, text, box?, point?, time?)`, `list_annotations` | pin a note to an area (`box=[x, y, w, h]`, fractions 0–1), a spot or a video time (`time=[start, end]` seconds) |
| `set_provenance` | creator, credit, license, page/image URL, `ai_generated` … (undoable) |
| `export_collection`, `export_status` | build a portable BagIt export of a folder, smart folder, items or everything, then hand the user its download link |
| `get_page_snapshot` | the readable text of the page an item was saved from, if kept |
| `view_video`, `save_frame`, `make_clip` | watch a video as one contact sheet with timestamps, keep a frame as its own item, cut a clip (a job) |

Every item carries `state` (`active` or `trashed`), `preview_status`
(`ready`, `pending`, `failed` or `none`) and a `web_url` that opens it in the
web UI. Every tool call is one batch in the item history, so whatever an
agent did can be undone in one step. MCP accepts `admin`, `write`, `agent` and
`read` tokens, and each tool checks the scope (a read token can only search,
get, view and list). There is deliberately no permanent-delete tool. Emptying the trash is
a human action in the web UI. A tool that can't do what was asked returns an
error that says why (an unknown folder, a bad URL, an item that doesn't exist).
The server tells agents to send a user's description of what something looks
like as `semantic`, so "find the moody dark interiors" works without Kist
running a language model of its own.

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
| `read` | GETs only. With `folder_ids`, only live items in those folders and their subfolders are visible: searches, items, folders, people, stacks and faces are filtered to them, and every other path answers 404 |
| `helper` | the AI job claim/report endpoints only |

A missing scope answers 403 `{"detail": "This token can't do that"}`; a
revoked or expired token 401. `expires_at` is optional. Every change a token
makes is recorded in the item history under its name.

## REST API

Every route except `/api/health`, `/robots.txt` and the share pages under `/s/` needs `Authorization: Bearer $KIST_TOKEN`.

```
POST   /api/items                multipart upload, or JSON {"url": …}. Both accept
                                 tags, folder_ids, notes, rating, source_url,
                                 created_at, modified_at, deleted_at (ISO 8601;
                                 deleted_at files the item straight into the trash),
                                 so importers can carry metadata over from other tools
GET    /api/items                q, tags (csv, all; a tag also matches through aliases and
                                 implied tags, brand:* a whole namespace), tags_any (csv, any),
                                 untagged, stacks=collapse (one item per stack, with
                                 stack_count: the cover; in a ranked search the
                                 stack's best-ranked member), person (a person id),
                                 flag (pick | reject | none), label (a colour | none),
                                 view (unfiled: no folder and no tags),
                                 folder (id, subfolders included; or "none"), type, rating, order,
                                 state (active | trashed; deleted=true still works),
                                 preview_status, fixity (failed), created_after,
                                 created_before, limit, cursor, has_text; ranked:
                                 semantic (text; needs the ml service once vectors
                                 exist, 503 if it's down), similar (item id), color
                                 (hex) + color_tol (0-100, default 30), text (words
                                 inside images/PDFs); q with semantic or similar is
                                 hybrid. Every filter applies to every mode. q matches
                                 filename, notes, annotation text and the text in
                                 images, PDFs and captions. Ranked items carry match
                                 {kind, score, snippet?} and the cursor is an offset.
                                 total is counted on the first page only (no cursor);
                                 later pages say null.
                                 M4 filters: ai_generated (true|false|unknown),
                                 license (exact, none), source_status (ok|gone|moved|
                                 error|unchecked). M5 media filters: taken_after,
                                 taken_before, has_gps, camera, lens, place
                                 (substrings), duration_min/_max (seconds), vertical,
                                 has_audio; order=-taken|taken sorts by date taken
                                 (else date added). type also takes audio, model,
                                 design, bundle (and font). date_after/date_before:
                                 the timeline's date (taken, else added); bbox=w,s,e,n:
                                 items with GPS inside a map area (the web map)
                                 ids=a,b,c instead: those items in that order (compare);
                                 unknown or invisible ids are left out, other
                                 parameters ignored
POST   /api/search/image         read scope: multipart file (not stored) + the same
                                 filters as query params -> the same page shape
GET    /api/items/{id}/palette   [{hex, pct, name}], largest share first
POST   /api/items/{id}/caption   queue a caption now -> 202 {job_id, status, provider,
                                 warning}; 422 when captions are off
GET    /api/items/{id}
PATCH  /api/items/{id}           filename, rating, notes, source_url, created_at, protected,
                                 caption ("" removes it; a caption event), flag (pick |
                                 reject | null), label (red, orange, yellow, green,
                                 blue, purple | null),
                                 folder_ids (replaces every membership; use
                                 PATCH .../folders to add or remove one).
                                 base_modified_at: the modified_at you last saw;
                                 if a field you send was changed since, 409 with
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
GET    /api/items/{id}/scrub     video / animated GIF scrub sprite (WebP); scrub.json:
                                 {frames, cols, rows, frame_w, frame_h, times[]}
POST   /api/items/{id}/cover     {"t": seconds | null}: the video's thumbnail frame
POST   /api/items/{id}/frames    {"t": seconds}: save that frame as a new PNG item
                                 (derived_from the video, with its tags and folders) -> 201
POST   /api/items/{id}/clip      {start, end, format: mp4|gif|webp (video) | mp3|wav
                                 (audio), max_side?} -> 202 job; GET /api/jobs/{id}
                                 until done, then result_item_id is the new item
GET    /api/items/{id}/stream    something the browser can play, with Range: the original,
                                 or the web-safe proxy (202 {status: preparing} while
                                 it's made); POST .../proxy queues it by hand
GET    /api/items/{id}/waveform  audio peaks (BBC audiowaveform JSON, 1,000 buckets)
GET    /api/items/{id}/svg       the sanitized SVG text ("Copy SVG")
POST   /api/items/{id}/poster    a PNG/JPEG/WebP poster for a 3D model, design file or bundle
GET    /api/items/{id}/model     the model for <model-viewer> (GLB/glTF, or converted GLB)
GET    /api/items/{id}/font-info family, style, glyph count, variable axes, scripts
GET    /api/items/{id}/glyphs    ?page=: the font's codepoints, 256 a page
GET    /api/items/{id}/bundle    what's inside a zip bundle; .../bundle/{path} serves one
                                 file from it (paths that leave the bundle: 400)
GET    /api/timeline             ?group=day|month|year + every GET /api/items filter:
                                 [{key, count, first_item_id}] by date taken
                                 (on-this-day and map take the same filters)
GET    /api/on-this-day          ?day=YYYY-MM-DD: items taken that day in earlier years
GET    /api/map                  ?bbox=w,s,e,n&zoom=: GPS items clustered on a grid
GET    /api/map/config           tile URL, attribution, whether place names are loaded
GET    /api/jobs/{id}            one job (clips and proxies report result_item_id)
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
DELETE /api/tokens/{id}/entry    (admin) remove a revoked or expired token from the list -> 204 (409 while it still works)
GET    /api/archive-health       fixity, last verify, last backup, free disk, the 3-2-1
                                 checklist
POST   /api/archive-health/checklist  {"id": "offsite", "done": true}
GET    /api/tags                 with counts, namespace and value; ?namespace=brand | none
GET    /api/tags/cleanup         ?unused_since=: [{kind: one_off | clash | near | unused |
                                 smart_folder, tags, counts, proposal}]
POST   /api/tags/merge           {"from": [...], "into", "keep_alias"?}; ?dry_run=1 counts
                                 the items; one batch (undo it to put everything back)
POST   /api/tags/rename          {"from", "to"} (onto an existing tag: a merge)
GET    /api/vocabulary           {aliases: [{alias, tag}], implies: [{tag, implied}],
                                 namespaces: [{namespace, tags}]}
POST   /api/vocabulary/aliases   {"alias", "tag"}: rewrites items carrying the alias
DELETE /api/vocabulary/aliases   ?alias=
POST   /api/vocabulary/implies   {"tag", "implied"}: a loop answers 422
DELETE /api/vocabulary/implies   ?tag=&implied=
GET    /api/items/{id}/tag-suggestions        ?limit=10: [{tag, score, why: "similar items" |
                                              "matches the image"}]
POST   /api/items/{id}/tag-suggestions/feedback  {"tag", "accepted"}
GET    /api/duplicates/groups    ?kind=same_image | similar | exact: [{id, kind, items,
                                 pairs, suggested_keep}]
POST   /api/duplicates/resolve   {"keep", "others": [...], "merge": {tags, folders, notes,
                                 rating: "max" | "keep", source_url}}: one batch
GET    /api/stacks/suggestions   [{kind: raw_jpeg | burst | version, item_ids,
                                 cover_item_id, reason}]
POST   /api/stacks               {"item_ids": [...], "cover"?} -> {id, cover_item_id, count,
                                 items}
GET    /api/stacks/{id}
PATCH  /api/stacks/{id}          {"cover"?, "add"?, "remove"?}
DELETE /api/stacks/{id}          unstack (the items stay)
GET    /api/collections/suggested  ?refresh=1: {computed_at, clusters: [{id, size,
                                 item_ids, sample_item_ids, label_suggestion, ...}]}
POST   /api/collections/suggested/{id}/accept  {"name", "kind": "folder" | "smart_folder",
                                 "parent_id"?}
GET    /api/people               [{id, name, face_count, item_count, cover}]
PATCH  /api/people/{id}          {"name"?, "cover_face_id"?}
POST   /api/people/merge         {"from": [...], "into"}
GET    /api/items/{id}/faces     [{id, box (fractions), person_id, person_name, thumb_url}]
GET    /api/faces/{id}/thumb     ?size=160: a square crop (webp)
PATCH  /api/faces/{id}           {"person_id": id | null}
GET    /api/items/{id}/annotations        [{id, kind: point|box|range, x, y, w, h,
POST   /api/items/{id}/annotations         t_start, t_end, text, author, created_at,
PATCH  /api/items/{id}/annotations/{aid}   resolved}]. x/y/w/h are fractions 0-1 of the
DELETE /api/items/{id}/annotations/{aid}   image, t_* seconds; every change is an event
GET    /api/items/{id}/versions  {items (the version_of chain, oldest first), current_id}
POST   /api/items/{id}/versions  multipart 'file': a new version, linked version_of the
                                 chain's newest, with its folders and tags; the chain
                                 is stacked with it as the cover
GET    /api/items/{id}/boards    boards the item is on
POST   /api/items/rename         {item_ids, pattern, dry_run (default true), start}:
                                 {name} {ext} {n:3} {date:%Y%m%d} {w} {h} {folder}
                                 {tag:ns}; collisions get " (2)"; one batch
                                 -> {items: [{id, before, after, changed}], batch_id}
GET/POST /api/templates          {name, fields: {tags_add, folder_add, notes_append,
PUT/DELETE /api/templates/{id}    source_url, rating, label, flag, credit, license}}
POST   /api/templates/{id}/apply {item_ids}: one batch -> {items, batch_id}
GET    /api/triage/next          ?view=unfiled|all&after=<id>&prefetch=5&order=
                                 -> {item, prefetch_ids, remaining}
GET    /api/palette?q=           [{kind: folder|smart_folder|tag|board|person|item|action,
                                 id, title, subtitle}], top 5 of each kind (person: named
                                 people, when faces are on)
GET    /api/boards               ?state=active|trashed&q= -> [{id, name, version,
                                 item_count, created_at, modified_at, thumb_url}]
POST   /api/boards               {name, canvas?, nodes?, edges?, item_ids?, layout:
                                 grid|masonry} -> the board
GET    /api/boards/{id}          board.json plus state, item_count, missing_item_ids
PUT    /api/boards/{id}          {base_version, name, canvas, nodes, edges}: 409
                                 {"detail": {"message", "board"}} if base_version is stale
PATCH  /api/boards/{id}          {base_version?, name?, canvas?, add, update (JSON merge,
                                 null removes a key), remove, edges_add, edges_update,
                                 edges_remove}; without base_version: apply to the latest
POST   /api/boards/{id}/items    {item_ids, layout}: lay them out below the rest
DELETE /api/boards/{id}          to the boards trash (purged after the trash retention);
                                 ?hard=true deletes a trashed board for good
POST   /api/boards/{id}/restore
POST   /api/boards/{id}/duplicate
GET    /api/boards/{id}/thumb    the rendered snapshot (webp)
GET    /api/boards/{id}/export   ?format=png|pdf|html&scale=1&max_pixels=: PNG/PDF drawn
                                 from the items' previews (at most 40 MP); HTML is one
                                 self-contained file with the images inlined
DELETE /api/tags/unused          delete tags no item uses (trashed items count)
GET/POST/PATCH/DELETE /api/folders  folders have a description (up to 5,000 characters)
GET/POST/PATCH/DELETE /api/smart-folders
GET    /api/duplicates           ?threshold=6 (perceptual-hash distance)
GET    /api/stats                includes failed_jobs, preview_failed and fixity_failed
GET    /api/jobs                 ?status=pending|running|done|failed
POST   /api/jobs/{id}/retry      a failed job, with fresh attempts
DELETE /api/jobs/{id}            dismiss a failed or finished job
GET    /api/ai/status            admin: {model, ml_ok, embedded, ocr_done, captioned,
                                 pending, jobs, window, window_open, concurrency,
                                 embed_batch, captions: {provider, model, auto, runs_on,
                                 leaves_house, warning}, helpers: [{name, last_seen,
                                 jobs_done}]}
POST   /api/ai/jobs/claim        helper scope: {kinds, max, name} -> {model, jobs: [{id,
                                 kind, item_id, image_url, lease_seconds}]}
GET    /api/ai/jobs/{id}/image   helper scope: the image for a job this helper holds
POST   /api/ai/jobs/{id}/result  helper scope: {vector, model} | {text, model, lang, boxes} |
                                 {text, model} (a caption) | {error}; X-Kist-Helper names
                                 the helper. Captions are only handed to helpers that
                                 ask for kind caption
GET    /api/version              {"version", "commit"}
GET    /api/health/deep          database, migrations, worker heartbeat, library disk,
                                 job queue, recent preview failures, last verify run
POST   /api/client-errors        the web UI reports its crashes here (logged, 30/min)
GET    /api/health               no auth; the database answers

Milestone 4:
GET    /api/items                + ai_generated (true|false|unknown), license (text, or
                                 "none"), source_status (ok|gone|moved|error|unchecked)
PATCH  /api/items/{id}           + provenance {...} (merged; null removes a field;
                                 page_url sets source_url)
POST   /api/items                + provenance (JSON; a form field for uploads), and/or
                                 flat fields (form fields or JSON keys, as the browser
                                 extension and the /add page send them): page_url,
                                 image_url, title, captured_at, method (upload|paste|url|
                                 extension|bookmarklet|inbox|mcp|import), author|creator,
                                 credit, site, license, license_url|rights_url (a value
                                 that doesn't validate is dropped; a provenance object
                                 wins); for {url}: title (-> provenance.page_title) and
                                 text (added to the notes); Accept: text/plain answers
                                 "Saved to Kist: <name>" (201) / "Already in Kist: <name>"
POST   /api/items/{id}/snapshot  JSON {html, page_url?, title?, captured_at?} or a raw
                                 text/html body; no body: fetch page_url on the server
                                 (KIST_SNAPSHOTS=1, else 409). An edit scope, or an
                                 add-only token for an item it added itself under 10
                                 minutes ago (else 403)
GET    /api/items/{id}/snapshot  the saved page (CSP: sandbox)
GET    /api/items/{id}/snapshot/text
POST   /api/items/export-images  {item_ids, preset: name | {max_side, format, quality,
                                 strip_metadata, watermark?, contact_sheet?}} -> zip | PDF
GET/POST /api/export-presets     saved presets; PUT/DELETE /api/export-presets/{name}
POST   /api/exports              {target, target_id?, item_ids?, include?, format} -> 202
GET    /api/exports              and /api/exports/{id} (status; ?download=1 = the zip),
GET    /api/exports/{id}/download  DELETE /api/exports/{id}
GET/POST /api/shares             (write/admin) share links; GET/PATCH/DELETE
                                 /api/shares/{id}, POST /api/shares/{id}/revoke
POST   /api/imports/upload       (write/admin) multipart 'file': an export sent from the
                                 browser, kept in library/import/uploads/ -> {source}
POST   /api/imports              (write/admin) {kind, source, dry_run?, folder?} -> 202;
GET    /api/imports/{id}         status and report
GET    /api/shortcut             (admin) the iOS Shortcut's steps for this server
GET    /api/stats                + source_gone
GET    /s/{slug}                 no token: the public share page (POST: its password);
GET    /s/{slug}/i/{id}/thumb|display|file
GET    /robots.txt               no token
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
0–5; unknown `type`, `order`, `view` or `preview_status` values; a `color`
that isn't a hex colour, `color_tol` outside 0–100, a `text` with no words, a
caption over 2,000 characters, or `similar` naming an item with no embedding
yet.

Board nodes are `{id, type: item|note|text|shape|group|link|color, x, y, w,
h, rot, z, item_id?, crop?, text?, style?, children?}` and edges `{id, from,
to, label?, style?}`. Every save bumps `version`. A deleted item's node stays
on the board and is listed in `missing_item_ids` (exports draw a grey box).
Board edits are batched like item edits: `X-Kist-Batch` groups them and
`POST /api/batches/{id}/undo` reverts boards and items together (board
event kinds show as `board_create`, `board_update`, … in `/api/batches`; a
skipped board change has `"target": "board"`). A folder-limited read token
sees a board only if every item on it is one it can see (a board with no
items is hidden from it too).

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
# backend (needs libvips, libmagic, ffmpeg + ffprobe, and a Postgres to point at;
# exiftool and the "media" extra are optional: tests skip or use fallbacks)
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev,media]"
KIST_TOKEN=dev-token-0123456789 \
KIST_LIBRARY_DIR=./library \
KIST_DATABASE_URL=postgresql+psycopg://kist:kist@localhost:5432/kist \
  .venv/bin/uvicorn kist.app:app --reload

# frontend dev server (proxies /api and /mcp to :8080)
cd web && npm install && npm run dev

# tests: integration tests need a throwaway Postgres database
KIST_TEST_DATABASE_URL=postgresql+psycopg://kist:kist@localhost:5432/kist_test \
  .venv/bin/pytest
# real RAW files (CC0 samples from raw.pixls.us, listed in tests/test_media_unit.py):
# KIST_TEST_RAW_DIR=~/raw-samples .venv/bin/pytest tests/test_media_unit.py
.venv/bin/ruff check kist tests migrations
```

See [docs/architecture.md](docs/architecture.md) for how it fits together,
and [CONTRIBUTING.md](CONTRIBUTING.md) before sending a pull request.

## License

MIT. See [LICENSE](LICENSE).
