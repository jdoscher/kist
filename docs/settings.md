# Settings

Kist is configured with environment variables in `.env` next to
`docker-compose.yml`; `.env.example` lists them all with their defaults.
After a change, `docker compose up -d` recreates the containers that use it.
Settings you change in the web app (theme, density, tile size, the first-run
checklist) are kept per browser, and the rest (tokens, vocabulary, templates,
export presets, share links) live in the library, so they survive a rebuild.

Only `KIST_TOKEN` is required.

| variable | default | |
|---|---|---|
| `KIST_TOKEN` | — | **Required.** The owner's bearer token (scope `admin`) for the UI, API, and MCP (16+ characters). Narrower named tokens: see [Tokens](mcp.md#tokens) |
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
| `KIST_FACES` | `0` | Faces and people, opt-in. Set it for the app, worker **and** ml service: the worker then queues a `faces` job per image, and only then does the ml service download (~39 MB) and load the face models. See [Organising](https://github.com/jdoscher/kist#organising-vocabulary-duplicates-stacks-collections-people) |
| `KIST_FACE_MODELS_URL` | — | A mirror for the two face model files (they're checked by sha256 either way) |
| `KIST_COLLECTIONS_INTERVAL_HOURS` | `24` | How often the worker recomputes suggested collections; `0` = only on request (`?refresh=1`) |
| `KIST_EXPORT_RETENTION_DAYS` | `7` | Finished portable exports (`library/exports/`) are deleted after this many days; `0` keeps them |
| `KIST_SNAPSHOTS` | `false` | Let `POST /api/items/{id}/snapshot` with no body fetch the item's page on the server (through the same SSRF guard as URL adds). Off: only snapshots the browser sends |
| `KIST_LINK_CHECK_DAYS` | `0` | Off by default. Set a number of days to have the link-rot watch re-check every item's source page that often; what it finds is shown in the item's info panel only, never as something that needs attention |
| `KIST_LINK_CHECK_DELAY_SECONDS` | `2` | Pause between two link checks (and never the same host within 10 s) |
| `KIST_WAYBACK` | `false` | Ask the Wayback Machine (archive.org, an outside service) for an archived copy of sources that are gone. Off by default: it tells archive.org which pages you kept |
| `KIST_XMP_SIDECARS` | `true` | Write `items/<id>/original.xmp` (rating, keywords, notes, label, creator, rights, provenance) for images on every change, for Lightroom, darktable and digiKam |
| `KIST_SHARE_PASSWORD_ATTEMPTS` | `10` | Wrong share-link passwords allowed per link per 15 minutes (then 429) |
| `KIST_PROOF_ACTIONS_PER_IP` | `120` | Guest feedback actions (pick, heart, comment, name) per visitor address per 10 minutes on share links that let guests pick and comment (then 429). Behind a reverse proxy, set uvicorn's `FORWARDED_ALLOW_IPS` so Kist sees the real address |
| `KIST_PROOF_ACTIONS_PER_SHARE` | `1500` | Guest feedback actions per share link per 10 minutes, all visitors together (then 429) |

M4 and M5 options (exports, link checks, video and bundle limits, map tiles)
are listed in `.env.example` and the
[changelog's upgrade notes](https://github.com/jdoscher/kist/blob/main/CHANGELOG.md).
