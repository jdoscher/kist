# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

Kist 2.0, milestones 1 to 5: the platform and AI infrastructure; finding
things (search by meaning, colour, text in images and by image; a tag
vocabulary, stacks, duplicates, suggested collections, faces); the
backend for creative workflows (boards, flags and labels, annotations,
versions, templates, triage, the command palette); getting things in and
out (share links, portable exports, export presets, provenance, page
snapshots, the link-rot watch, importers, XMP sidecars); and formats and
media (video scrubbing, clips and proxies, audio, camera metadata, RAW,
design files, SVG, 3D, fonts, bundles, a timeline and a map). Everything runs on your
own hardware unless you choose a cloud caption provider, every change is in
the item history and undoes as a batch, and every new piece of data is a
plain file next to the library, so `kist rebuild-index` restores it.

### Upgrade notes
From 1.5.0, in this order:

- **Back up the database first:** `docker compose exec db pg_dump -U kist
  kist | gzip > kist-before-2.0.sql.gz`.
- **Switch the database image to `pgvector/pgvector:pg16`.** Kist's
  `docker-compose.yml` now uses it. It is built on the official `postgres:16`
  image with the pgvector extension added, so it opens your existing
  `pgdata` volume as is: no dump and restore. If you keep your own compose
  file, change `image: postgres:16` to `image: pgvector/pgvector:pg16` for
  the `db` service. Without it the app and worker refuse to start, and
  migration 0101 says why. Stay on the `pg16` tag: a different Postgres
  major version can't open a pg16 data directory.
- Then: `docker compose pull db && docker compose build && docker compose
  up -d`. Migrations 0006–0010, 0101, 0201, 0301, 0401, 0501, 0601 and 0602
  run on start (one line, in that order). The rebuilt image now includes
  exiftool and the `media` extra (fontTools, brotli, resvg-py, trimesh). The new `ml` service
  downloads its model files (~600 MB, checked by sha256) into the
  `ml-models` volume on first start; AI jobs wait for it.
- Run `kist rewrite-meta` once (it adds `protected`, `links` and `thumbhash`
  to every meta.json and writes `tokens.json`). Older items get their
  ThumbHash in the background, or at once with
  `kist regen-previews --thumbhash-only`.
- Existing items: `docker compose exec worker kist ai-backfill` queues their
  embeddings and OCR. Set `KIST_AI_WINDOW=01:00-06:00` first to keep the work
  at night (estimated 1.5–2.5 h per 100,000 items on an i5-1235U).
- The `ml-models` volume needn't be backed up (it re-downloads); the
  library's new `items/*/ai/`, `events.jsonl`, `fixity.jsonl` and
  `.kist/tokens.json` should be, like the rest of the library.
- New settings: `KIST_TRASH_RETENTION_DAYS` (30), `KIST_BACKUP_REPO`,
  `RESTIC_PASSWORD` / `RESTIC_PASSWORD_FILE`, `KIST_BACKUP_INTERVAL_HOURS`
  (0 = manual); `KIST_AI_ENABLED`, `KIST_ML_URL`, `KIST_ML_MODEL`,
  `KIST_ML_TIMEOUT_SECONDS`, `KIST_AI_WINDOW`, `KIST_AI_CONCURRENCY`,
  `KIST_AI_OCR_PDF_PAGES`, and for the ml service `KIST_ML_THREADS` (4),
  `KIST_ML_CONCURRENCY`, `KIST_ML_DEVICE`, `KIST_ML_IDLE_SECONDS`,
  `KIST_ML_BASE_URL`, `KIST_ML_MEM_LIMIT` (2g), `KIST_ML_CPUS`,
  `KIST_ML_OPENVINO`. All are in `.env.example`.
- The trash now empties itself after 30 days; set
  `KIST_TRASH_RETENTION_DAYS=0` before upgrading to keep it forever.
- Milestones 2 and 3's migrations: 0201 (search: the `item_colors` palette
  table kept by a trigger, CIEDE2000 in SQL, caption rows), 0301
  (organising: `tag_aliases`, `tag_implies`, `tag_embeddings`,
  `tag_feedback`, `stacks`, `faces`, `people`, `items.stack_id`) and 0401
  (creative workflows: `items.flag`/`label`, `boards`, `board_items`,
  `board_events`, `annotations`).
- The `docker compose build` above rebuilds the `ml` image, which gained a
  batch embedding endpoint (it needs `python-multipart`) and the optional
  face models. Those (YuNet + SFace, ~39 MB) are only downloaded, on first
  use, when `KIST_FACES=1`.
- The one `kist rewrite-meta` above also adds `stack`, `flag` and `label` to
  every `meta.json` and writes `vocabulary.json` and `.kist/people.json`.
- Palettes changed (they keep small saturated areas):
  `docker compose exec worker kist regen-previews --colors-only` refreshes
  older items' palettes in seconds. Fonts added before get a specimen
  preview queued on upgrade.
- Optional: captions for what you already have,
  `kist ai-backfill --kind caption` (only once a caption provider is set;
  a cloud provider receives every image it captions).
- Optional: faces. Set `KIST_FACES=1` for the app, worker and ml service,
  then `kist ai-backfill --kind faces` looks at the items you already have
  (plain `kist ai-backfill` includes faces whenever they're on).
- New settings, all optional and in `.env.example`: `KIST_AI_EMBED_BATCH`
  (16); captions `KIST_CAPTION_PROVIDER`, `KIST_CAPTION_URL`,
  `KIST_CAPTION_MODEL`, `KIST_CAPTION_API_KEY`, `KIST_CAPTION_AUTO`,
  `KIST_CAPTION_PROMPT`, `KIST_CAPTION_TIMEOUT_SECONDS`; faces `KIST_FACES`
  (0), `KIST_FACE_MODELS_URL`; `KIST_COLLECTIONS_INTERVAL_HOURS` (24).
  `KIST_AI_CONCURRENCY` now defaults to 2.
- New files to back up with the library: `vocabulary.json`, `boards/`,
  `.kist/people.json`, `.kist/templates.json`, `items/*/annotations.json`,
  `items/*/ai/caption.json`, `items/*/ai/faces.json` and
  `items/*/ai/tag-feedback.json` (`.kist/collections.json` is a cache).
- Milestones 4 and 5's migrations: 0501 (the `shares` table,
  `items.provenance` seeded from `source_url`, which stays and equals
  `provenance.page_url`, the link-rot columns, `item_text` source `page`),
  0601 (`items.media` and its indexed columns `taken_at`, `taken_sort`,
  `has_gps`, `lat`/`lon`, `camera`, `lens`, `vertical`, `has_audio`, `place`,
  `raw_pair_key`) and 0602 (`items.added_by_token`, see below).
- The one `kist rewrite-meta` above also adds `provenance`, `source_check`
  and `media` to every meta.json, writes `.kist/shares.json` and an
  `original.xmp` for every image (`kist xmp --rewrite` does only the
  latter). Until then `kist verify` stays quiet about the missing keys.
- Then `docker compose exec worker kist media-backfill` (once; `--limit N`
  does it in parts): camera and video metadata, scrub sprites and
  waveforms, and older PSDs, RAWs, design files, models and zips get their
  new kinds and previews.
- Then `docker compose exec worker kist regen-previews --colors-only`
  (the same command as for Milestone 2's palettes; run it after
  `media-backfill`): audio waveforms and font specimens lose the palette
  they never had (colour search no longer finds Kist's own colours), and
  design files keep theirs.
- Optional: `docker compose exec worker kist geo-download` (about 10 MB from
  GeoNames, once) names the places of photos with GPS, offline afterwards.
- Optional: the map's tiles. By default the web map reads a Protomaps
  PMTiles file you download into the library (`docs/maps.md`); or set
  `KIST_MAP_TILES_URL` and `KIST_MAP_ATTRIBUTION` for a hosted provider
  (that provider then sees which areas you look at).
- Optional: hardware video encoding on an Intel iGPU. Set
  `KIST_HWACCEL=vaapi` (or `qsv`) for the worker and uncomment its
  `/dev/dri` `devices` and `group_add` lines in `docker-compose.yml`; a
  failed GPU encode falls back to the CPU. `KIST_FFMPEG_THREADS` (2) caps
  every ffmpeg run, which also runs under `nice`.
- The link-rot watch is **off by default**: once Kist has its copy it leaves
  the source site alone (re-requesting pages costs those sites requests and
  gets rate-limited). Set `KIST_LINK_CHECK_DAYS` (e.g. `7`) to turn it on;
  what it finds shows in the item's info panel and the "Source page" filter,
  never as "Needs attention". `KIST_WAYBACK=1` (off) also asks archive.org,
  an outside service, about gone sources.
- **Share links are Kist's first pages without a token.** Only `/s/<slug>`
  (and `/robots.txt`) are public; they serve only that share's items,
  never originals unless the share allows it, with no script, a sandboxing
  CSP, `noindex`, scrypt-hashed passwords with a rate limit, and 410 once
  expired or revoked. Visitors' addresses aren't logged. If Kist sits
  behind a proxy with its own login, exempt `/s/` there only if you want
  shares to work for people without that login. Review
  `GET /api/shares` now and then; revoking is instant.
- The browser extension and the bookmarklet's `/add` page: create an
  add-only token for them (Settings → Tokens). It can now also attach a
  page snapshot to an item it added itself in the last 10 minutes
  (migration 0602 records which token added an item; the column isn't in
  meta.json, so a rebuild just closes that window early).
- New files to back up: `.kist/shares.json`, `.kist/export-presets.json`,
  `items/*/page/`, `items/*/original.xmp`, `items/*/poster.png` (uploaded
  3D/design posters) and `.kist/geo/` (or re-run `geo-download`).
  `library/exports/` empties itself; scrub sprites, waveforms, proxies and
  bundle manifests are regenerated by `regen-previews`.
- New settings, all optional, in `.env.example` and passed through by
  `docker-compose.yml`: M4 `KIST_EXPORT_RETENTION_DAYS` (7),
  `KIST_SNAPSHOTS` (off), `KIST_LINK_CHECK_DAYS` (7),
  `KIST_LINK_CHECK_DELAY_SECONDS` (2), `KIST_WAYBACK` (off),
  `KIST_XMP_SIDECARS` (on), `KIST_SHARE_PASSWORD_ATTEMPTS` (10); M5
  `KIST_FFMPEG_THREADS` (2), `KIST_HWACCEL` (none), `KIST_VAAPI_DEVICE`,
  `KIST_SCRUB_FRAMES` (20), `KIST_CLIP_MAX_SECONDS` (600),
  `KIST_GIF_MAX_SECONDS` (60), `KIST_PROXY_MAX_SIDE` (1920),
  `KIST_EXIFTOOL` (on), `KIST_BUNDLE_MAX_ENTRIES`,
  `KIST_BUNDLE_MAX_TOTAL_MB`, `KIST_BUNDLE_MAX_ENTRY_MB`,
  `KIST_MAP_TILES_URL`, `KIST_MAP_ATTRIBUTION`.

### Milestone 1: the platform

Kist 2.0, milestone 1: the platform and AI infrastructure. Named tokens
with scopes, history and undo for every item, links, protection, trash
retention, fixity checks, backups and archive health; and every image is
embedded (OpenCLIP ViT-B-32) and the text in screenshots and PDFs read,
entirely on your own hardware, with the results kept as plain files next to
each item. Plus a new look for the web app.

#### Added
- **Named tokens with scopes.** Make a token for each phone, person and
  agent with `POST /api/tokens`: `add` can only add items (the iPhone
  Shortcut), `read` only looks (optionally only at some folders and their
  subfolders, so family can browse one collection), `agent` can organize but
  never delete for good, `write` can do every edit, `admin` everything, and
  `helper` only the AI job endpoints. Tokens can expire and be revoked, and
  only their hash is stored, also in `library/.kist/tokens.json`, so a
  rebuild keeps them. `KIST_TOKEN` keeps working as the owner's admin token.
  `GET /api/me` says who a token is.
- **History and undo for every item.** Every change (adding, edits, tags,
  folders, trash, restore, permanent delete, links, protection) is recorded
  with who made it (the token's name, or the CLI, the worker or the inbox
  importer), from where (web, MCP, API, CLI) and in which batch.
  `GET /api/items/{id}/history` lists it; `POST /api/batches/{id}/undo` and
  `POST /api/events/{id}/revert` undo a batch or one change. Undo never
  overwrites newer work: a change something later touched again is skipped
  and listed with the reason. Each MCP tool call is one batch, so "undo what
  the agent just did" is one call (`recent_batches`, `undo_batch`). The
  history is also kept in each item's `events.jsonl`.
- **Links between items:** `derived_from` (a crop and its screenshot),
  `alternate`, `related`, `not_duplicate` (the pair stops showing up as
  near-duplicates) and `version_of`. REST `/api/items/{id}/links` and MCP
  `link_items`; both items' `meta.json` carry them.
- **Protected items** can't be trashed or deleted until unprotected (409
  "Protected: unprotect it first"). Agents can protect but not unprotect.
- **The trash empties itself:** items trashed more than
  `KIST_TRASH_RETENTION_DAYS` (default 30; 0 = never) ago are deleted for
  good by the worker, never protected ones. The clock starts when you
  upgrade, so an old trash isn't emptied the same day.
- **Folder descriptions** (up to 5,000 characters), also shown to agents in
  `list_folders` so they know what belongs where.
- **Fixity checks:** the worker re-hashes about 1/30 of the originals every
  day, so each file is proved intact monthly, and records every check in
  the item's `fixity.jsonl`. A file that changed or vanished shows in Needs
  attention (`fixity_failed` in `/api/stats`, `GET /api/items?fixity=failed`)
  and deep health, with the hash to look for in a backup. `kist fixity`
  checks on demand.
- **Backups:** `kist backup` dumps the database into the library and backs
  the library up with restic (now in the image) to `KIST_BACKUP_REPO`, with
  `RESTIC_PASSWORD` or `RESTIC_PASSWORD_FILE`. The worker can run it every
  `KIST_BACKUP_INTERVAL_HOURS`.
- **Archive health:** `GET /api/archive-health` shows fixity coverage and
  failures, the last verify and backup, free disk, and a 3-2-1 checklist
  (backup repository set, backed up within 7 days, a copy offsite, clean
  verify). Deep health also reports fixity and backups.
- **Placeholders:** every item has a `thumbhash`, a ~25-byte blurred preview
  the grid can paint before the thumbnail arrives.
- **`ml` service** (image `kist-ml`, `ml/Dockerfile`, internal only):
  `POST /embed/image`, `POST /embed/text`, `POST /ocr`, `GET /health` on
  ONNX Runtime (CPU; no torch). Models: Immich's ONNX exports of OpenCLIP
  ViT-B-32 (default) or ViT-B-16-SigLIP2 (`KIST_ML_MODEL`), pinned to a
  Hugging Face revision and checked by sha256; RapidOCR (PP-OCRv4). Models
  load on first use and unload after `KIST_ML_IDLE_SECONDS`. Optional Intel
  GPU through OpenVINO (`KIST_ML_OPENVINO=1` build, `KIST_ML_DEVICE=openvino-gpu`).
- **`embed` and `ocr` jobs**, queued for new items (embeddings for anything
  with a thumbnail; OCR for images without a camera's EXIF data, and PDFs,
  whose text layer is read first and whose scanned pages are OCR'd) and by
  `kist ai-backfill [--kind embed|ocr] [--limit N]`. They run on their own
  threads in the worker (`KIST_AI_CONCURRENCY`), only while no import or
  preview job is waiting, and backfilled ones only inside `KIST_AI_WINDOW`.
  A down ml service puts jobs back without using up their attempts.
- **AI sidecar files**: `items/<id>/ai/embed-<model>.f16` (raw float16) and
  `.json`, and `ai/ocr.json`, written before the database rows.
  `kist rebuild-index` loads them and never recomputes; `kist verify` reports
  rows without files and files not in the database; `kist ai-sidecars`
  writes missing files from the database.
- **`kist helper`**: run AI jobs on another machine (a Mac) for the server,
  over HTTP: `POST /api/ai/jobs/claim` and `POST /api/ai/jobs/{id}/result`.
  Install with `pip install 'kist[ml]'`.
- `GET /api/ai/status` and the MCP tool `ai_status`.
- `GET /api/items?semantic=<text>` orders results by meaning (minimal; the
  real search comes in milestone 2).
- AI settings (see the upgrade notes): `KIST_AI_ENABLED`, `KIST_ML_URL`, `KIST_ML_MODEL`,
  `KIST_ML_TIMEOUT_SECONDS`, `KIST_AI_WINDOW`, `KIST_AI_CONCURRENCY`,
  `KIST_AI_OCR_PDF_PAGES`, and for the ml service `KIST_ML_THREADS` (4),
  `KIST_ML_CONCURRENCY`, `KIST_ML_DEVICE`, `KIST_ML_IDLE_SECONDS`,
  `KIST_ML_BASE_URL`, `KIST_ML_MEM_LIMIT` (2g), `KIST_ML_CPUS`,
  `KIST_ML_OPENVINO`.
- Migration 0101: the `vector` extension, `item_embeddings` (halfvec, one
  partial HNSW cosine index per model), `item_text` (GIN tsvector index),
  `ai_helpers`.
- AI results are not edits: embeddings and OCR text don't appear in an
  item's history or in undo batches. Deleting an item for good also deletes
  its embeddings, text and `ai/` files.
- `GET /api/items?semantic=` respects a folder-limited read token, like
  every other search; `/api/ai/status` needs an admin token, and the helper
  endpoints a `helper` (or admin) token.
- **Web app:**
  - A new look. Kist has a new mark (two brass corners and a kept item), a
    "kist" wordmark and a full icon set: an adaptive favicon, iPhone home
    screen icon, Android and maskable icons, and a single-colour icon.
  - Kist can be installed as an app: it now ships a web app manifest, so "Add
    to Home Screen" opens it full screen with its own icon.
  - Light, dark and gallery-grey themes. Kist follows your device's light or
    dark setting by default; the new Appearance menu (the palette button, or
    the bottom of the phone menu) overrides it. Gallery grey puts the images and
    the whole interface on one neutral mid-grey (#5c5c5c) for judging colour,
    with a thin light line where the top bar and side panels meet the images.
  - Density: Compact (tight grid for sorting), Comfortable, and Gallery
    (bigger tiles, more space, sidebars tucked away behind a Panels button).
  - Tiles show the image's colour while the thumbnail loads, then fade it in.
  - Opening an item grows its tile into the full-screen view, and closing
    shrinks it back, in browsers that support it.
  - The brand guide is in `docs/brand.md`.
  - **Settings** (the gear in the top bar, or the phone menu; owner and
    full-access tokens only), with its own address (`/settings/tokens`,
    `/settings/archive`, `/settings/ai`):
    - **Tokens:** every named token with what it can do, its folders, when
      it was made and last used, when it expires and whether it's revoked.
      New token offers five presets, each explained: Family read-only (tick
      the folders it may see), Share sheet (add only), Agent, Helper (the Mac
      AI helper) and Full access. The new token is shown once with a Copy
      button and a warning, plus the `kist helper` command or the MCP line
      where they apply. Revoke asks first.
    - **Archive health:** how many originals were re-checked in the last 30
      days, any whose file changed (with the fingerprint to look for in a
      backup, and a link to them in the library), the last verify, the last
      backup and its age, free disk, how long the Trash keeps things, and the
      3-2-1 checklist, where you tick "a copy is offsite" yourself. Without a
      backup repository it says how to set `KIST_BACKUP_REPO` up.
    - **AI:** whether it's on, the model, whether the ml service answers, how
      many images are understood and how many have their text read, what's
      waiting, the hours it runs in, and each Mac helper with when it was
      last seen, plus how to start one.
  - **History** in the info panel and on the phone's item sheet: every change
    to the item in plain words ("Claude added tags #a #b", "You moved it to
    the Trash"), who made it, from where and when, each with Revert. A change
    something touched again later is left alone, and a notice says why.
  - **Recent changes** (the clock button, the phone menu, or ⌘Z / Ctrl+Z
    outside text fields): the latest batches of changes by you, agents and
    Kist itself ("Claude via MCP tagged 12 items · 2 min ago"), each with
    Undo, and Redo after an undo. A bulk action in the web app is one batch,
    so Undo puts all of it back; the Undo after trashing items undoes that
    batch.
  - **Links** in the info panel: linked items with their picture and how
    they're related (Derived from / Source of, Alternate, Related, Not a
    duplicate of, Version of / Has version). Add one by searching for the
    other item's name; remove one with its ✕. Pressing a link shows that item.
  - **Protect:** a lock button in the info panel and Protect / Unprotect in
    the selection bar. Protected items show a lock on their tile, and trying
    to trash one says "Protected: unprotect it first". Agents can protect
    but not unprotect, so the button says so for an agent token.
  - **Folder descriptions:** the folder dialog (the pencil) edits the name
    and a description of what belongs there; the description shows under the
    folder's name above its items.
  - **Needs attention** also lists originals that failed their fixity check,
    with Show them (`/all?fixity=failed`, shown as a removable filter).
  - Other tokens get the app they can use: a read-only token sees no edit
    controls, and one limited to some folders sees only those (no Unfiled,
    Trash, tags or smart folders). An add-only or helper token is told at
    the token screen that it can't open the library, instead of failing.

#### Changed
- The database image is `pgvector/pgvector:pg16` (see the upgrade notes).
- `/api/health/deep`'s oldest pending job age ignores AI jobs, which wait
  for their window on purpose.
- **Web app:**
  - Selected items are marked with brass corners and a check, so selection
    and the keyboard focus ring look different.
  - Icons are drawn from one consistent set instead of emoji and symbols.
  - New type: Inter for the interface, Instrument Serif for headings and
    JetBrains Mono for file details, all served by Kist itself.
  - The accent colour is brass instead of blue, and rating stars match it.
  - Animations are switched off when your device asks for reduced motion.
  - Edits made in the web app show as "web" in an item's history (it sends
    `X-Kist-Client: web`).
  - The token screen checks a token with `GET /api/me`, so a family token
    limited to some folders can sign in (it used to be refused, because it
    can't read `/api/stats`).

#### Fixed
- A fixity check, or the ThumbHash backfill after upgrading, no longer
  counts as an edit: it used to move the item's `modified_at`, so every
  checked item looked "recently edited", `kist verify` reported its
  meta.json as out of date, and saving notes in an open info panel could
  report a false "changed elsewhere".

#### Performance
- Paging through a large list no longer recounts every match on each page:
  `total` comes with the first page only, and later pages say `null`.
- Every sort order has its own index, so deep pages stay fast.


### Milestone 2: finding things

Search by what images show, by colour, by the words inside them, or by
another image, and "more like this" on any item; and keep a growing
library tidy: a tag vocabulary with aliases and implied tags, a cleanup
workbench, tag suggestions, duplicate groups you resolve in one step,
stacks, suggested collections, and (opt-in) faces and people. All of it
runs on your own hardware (unless you choose a cloud caption provider),
writes plain files the database can be rebuilt from, and every change
undoes as one batch.

#### Search
- **One search box, several ways to search.** `GET /api/items` (and the MCP
  tool `search_items`) gains `semantic` (describe it: "brown suede boots on
  cobblestones"), `similar` (more like this item), `color` with `color_tol`
  (0–100), `text` (only words inside screenshots and PDFs) and `has_text`.
  They combine with every filter you already use (tags, folders, type,
  rating, dates). Words plus a description (`q` + `semantic`) blend both
  rankings. Keyword search (`q`) now also finds the text inside screenshots,
  PDFs and captions.
- **Why it matched.** Ranked results carry `match` with the way they matched
  (meaning, similar, colour, text, keyword, or several: hybrid), a score, and
  for text hits a snippet with the words marked `<<like this>>`.
- **Search by image:** `POST /api/search/image` with a picture (it isn't
  saved) finds items that look like it. Read-only tokens may use it.
- **Colour.** Every item's palette is at `GET /api/items/{id}/palette` with
  everyday colour names; colour search uses perceptual distance (CIEDE2000),
  not RGB. New palettes keep small saturated areas (a red car in a grey
  street now counts as red); `kist regen-previews --colors-only` refreshes
  older items' palettes in seconds.
- **Fonts.** TTF, OTF, WOFF and WOFF2 files are now their own type (`font`)
  with a specimen preview showing the family name and "Aa Bb Cc 0123 The
  quick brown fox". Fonts you added before are queued for a specimen when
  you upgrade.
- **Captions (optional, off by default).** A sentence describing an image,
  written by a local Ollama vision model (e.g. `qwen2.5vl:3b`), or by OpenAI,
  Anthropic or Gemini with your own key. **The cloud providers receive every
  image you caption.** Caption one item (`POST /api/items/{id}/caption`), a
  backlog (`kist ai-backfill --kind caption`) or every new item
  (`KIST_CAPTION_AUTO=true`). Captions are searchable, editable
  (`PATCH /api/items/{id} {"caption": …}`), in the item's history and
  undoable, and kept in `ai/caption.json` next to the item. A Mac running
  Ollama can do them for the NAS: `kist helper --kinds caption`.
- **Faster embedding.** The worker now runs two AI jobs at once and sends
  embeddings 16 at a time (`KIST_AI_CONCURRENCY=2`, `KIST_AI_EMBED_BATCH=16`),
  writing results side by side. On a fast disk that measured 53 → 84–99
  items/s; with slow disk syncs like a busy NAS, 3.9 → 44 items/s.
- **For agents (MCP):** `search_items` takes `semantic`, `similar_to`,
  `color`, `color_tol`, `text` and `has_text`; new `search_by_image`
  (a URL or a file in the import folder) and `item_palette`;
  `update_item(caption=…)`. Agents are told to pass descriptions as
  `semantic`.

#### Organising
- **Tag vocabulary.** Aliases (`POST /api/vocabulary/aliases`) rewrite the
  items carrying the variant, as tag events in one batch, and every later
  use on every path; implied tags (`POST /api/vocabulary/implies`) are
  matched at search time and shown as `implied_tags`, never written to items,
  and loops are refused; `namespace:value` tags are grouped
  (`GET /api/tags?namespace=`, `tags=brand:*`). Kept in
  `library/vocabulary.json`. MCP `get_vocabulary`; `set_tags` resolves aliases.
- **Tag cleanup.** `GET /api/tags/cleanup` (one-offs, case/spacing clashes,
  near-misspellings and plurals, unused since a date, smart folders naming
  missing tags or folders), `POST /api/tags/merge` with a dry run and
  `POST /api/tags/rename`, each one undoable batch. MCP `tag_cleanup`,
  `merge_tags`.
- **Tag suggestions.** `GET /api/items/{id}/tag-suggestions`: the tags of the
  item's nearest look-alikes ("similar items") and CLIP zero-shot over the
  tag names ("matches the image"), with feedback that keeps a rejected tag
  away from that item. MCP `suggest_tags`.
- **Duplicate groups.** `GET /api/duplicates/groups?kind=same_image|similar|exact`
  with a suggested keeper; `POST /api/duplicates/resolve` merges tags,
  folders, notes, the best rating and the source URL into it and trashes the
  rest, in one batch. MCP `duplicate_groups`, `resolve_duplicates`.
- **Stacks.** `POST/GET/PATCH/DELETE /api/stacks`, `GET /api/items?stacks=collapse`
  (covers only, with `stack_count`), suggestions for RAW+JPEG pairs, bursts
  and `version_of` links. Membership is a `stack` event and lives in
  `meta.json`. MCP `suggest_stacks`, `stack_items`.
- **Suggested collections.** `GET /api/collections/suggested`: clusters of 8+
  look-alike items not already in one folder, labelled from their tags or
  zero-shot, recomputed daily by the worker; accept one as a folder or a
  tag plus smart folder. MCP `suggested_collections`.
- **Faces and people (opt-in).** YuNet + SFace (OpenCV zoo, Apache-2.0) in the
  ml service with numpy pre/post-processing; a `faces` job per image; people
  formed automatically, named, merged and searched
  (`GET /api/people`, `PATCH /api/people/{id}`, `POST /api/people/merge`,
  `GET /api/items?person=`, face crops). MCP `list_people`.
- A folder-limited read token sees people, stacks and faces only for its
  own items.

#### Web app
- **One search box, several ways to search.** A mode button beside the box:
  **Auto** (the default) searches names and codes as words (`IMG_2231`,
  `receipt.pdf`, a quoted phrase) and anything else as words *and* meaning
  together, so "orvis" finds the receipt that says it and "dog on grass"
  finds the photo that shows it; `#2f4f3a` searches that colour. Or pick
  **Words**, **Meaning**, **Text in images**, **Colour** (a picker with
  swatches, a hex field and how close) or **Image**: choose a picture, or
  drop or paste one onto the search box (it searches; it isn't added to
  the library, and it isn't kept). The mode you pick is remembered.
- **Why it matched.** Ranked results say "Best matches first"; where words
  and meaning were mixed, the tiles the words found carry a badge, and text
  hits show the words around the hit with the match highlighted, on the
  tile and in the info panel ("Why it's here", with the score). If the AI
  service is down, a words + meaning search falls back to the words and
  says so; a meaning-only search offers "Search the words instead".
- **More like this** in the info panel, the phone's item sheet, the
  full-screen preview and a new right-click menu on tiles (which also finds
  an item's main colour). More like this, a colour, an image or a person is
  its own place in the history, so Back returns to where you were.
- **Info panel:** the item's colours (press one to find that colour), the
  caption (editable; Describe / Regenerate when a caption provider is set,
  asking first when the provider is a cloud service, with the warning that
  the image leaves your network), suggested tags under the tag box (add
  one, or say no and it isn't suggested for that item again; each says
  whether it came from look-alike items or the picture), implied tags shown
  greyed with what implies them, `brand:rolex` tags shown as two-part chips,
  the people in the photo (name them, move a face to someone else) and the
  item's stack.
- **Stacks in the grid.** A stack shows once, with a count on it; press the
  count (or S) to open it in place and again to close it. Stack and Unstack
  in the selection bar, Make cover and Take out in the info panel, each with
  Undo.
- **Organise** (sidebar: Duplicates, Collections, People; its own URLs under
  `/organise/`): **Duplicates** review, groups of the same picture or of
  look-alikes side by side with the suggested keeper, choose the copy to
  keep and what to carry over to it (tags, folders, notes, best rating,
  source), "Not duplicates", and "Keep the suggested copy in every group";
  **Stacks** suggestions (RAW + JPEG, bursts, versions); **"You seem to
  collect…"** cards with samples, kept as a folder or a tag plus smart
  folder, or put off; **People** (when faces are on): name, merge, and see
  everything with someone.
- **Settings → Vocabulary** (aliases, implied tags, namespaces) and
  **Settings → Tag cleanup** (clashes, near spellings and plurals, tags used
  once, unused tags, smart folders naming missing tags; every merge and
  rename says first how many items it changes, and Undo in the notice puts
  the tags back and removes the aliases the merge left). **Settings → AI**
  shows the caption provider (with "Images leave your network" for OpenAI,
  Anthropic and Gemini) and whether faces are on.
- The new screens have phone layouts and pass axe-core with no violations
  in the dark, light and gallery themes. Organise and the tag screens load
  when first opened.

#### Fixed
- A keyword match whose words were split between the filename (or notes)
  and the text inside the item now still gets a snippet marking the words
  the text has.

### Milestone 3: creative workflows (backend)

Boards, culling and pinned notes: the backend for the boards canvas,
compare, triage, quick look and the Cmd+K palette (the web screens follow).

- **Boards.** `library/boards/{id}/board.json` holds nodes (items, notes,
  text, shapes, groups, links, colour swatches) and arrows; `thumb.webp` is
  a rendered snapshot. `GET/POST /api/boards`, `GET/PUT/PATCH/DELETE
  /api/boards/{id}` (PUT/PATCH with `base_version`, 409 when stale),
  duplicate, restore, a boards trash purged with the item trash's retention,
  `GET /api/items/{id}/boards`, and export to PNG or PDF at a chosen scale
  or to one self-contained HTML file. A deleted item stays on its boards as
  a missing-item node. Board edits are in the batch history and undo.
- **Pick/reject flags and colour labels** on items: in `ItemOut` and
  `meta.json`, filterable (`flag=`, `label=`), editable with PATCH and MCP
  `update_item`, recorded as edits.
- **Annotations:** notes pinned to a point, a box or a video time range
  (`/api/items/{id}/annotations`), kept in `items/{id}/annotations.json`,
  found by `q`, recorded in the item history.
- **Versions and compare:** `GET/POST /api/items/{id}/versions` (the
  `version_of` chain; uploading a new version links it to the newest) and
  `GET /api/items?ids=a,b,c`.
- **Metadata templates** (`/api/templates`, applied as one batch) and
  **bulk rename** with a pattern (`POST /api/items/rename`, dry run first).
- **Triage queue** (`GET /api/triage/next`) and the **command palette**
  (`GET /api/palette?q=`).
- MCP: `list_boards`, `get_board`, `create_board`, `add_to_board`,
  `annotate`, `list_annotations`; flags and labels in `update_item` and
  `search_items`.
- `kist rebuild-index` restores boards, board history, annotations, flags
  and labels from their files; `kist verify` checks them (and
  `templates.json`); `kist rewrite-meta` rewrites them.

### Milestone 3: creative workflows (web app)

- **Boards.** A canvas of library items, notes, text, shapes, colour swatches and labelled arrows. Drag items in from a library panel or from the grid (or use Add buttons). Pan, zoom, marquee-select, align and distribute, group (⌘G), duplicate (⌘D), crop and snap. Saves automatically; if an agent or another tab changed the board, a banner offers to combine, keep theirs or overwrite. Export to PNG, PDF or a self-contained HTML file. A list view gives keyboard and screen-reader users every editing action without dragging. Phones can view, pan and zoom.
- **Compare and survey.** Select 2–4 items and compare them side by side with synced zoom, a wipe slider and a difference overlay, or survey a selection and pick or reject with P/X and auto-advance (from the grid: C compares, N surveys).
- **Triage.** Go through Unfiled one item at a time: P/X/U, 1–5 stars, 6–9 colour labels, T to tag, F to file, arrows to move, Undo. On a phone, swipe: left/right moves, up keeps, down rejects.
- **Quick look.** Hold Space on a tile to peek; Z shows a 100% loupe from the original, also in the full-screen view.
- **Pinned notes.** Pins and boxes on images; time-range notes on videos, shown as marks on the scrubber (M adds one at the current time).
- **⌘K palette.** Jump to any folder, tag, board or item, or run an action; a full-height sheet on phones.
- **Flags, labels and versions.** Pick/reject flags and colour labels on tiles, in the info panel, the selection bar and sidebar filters. Upload a new version of an item and compare it with the previous one.
- **Templates and bulk rename.** Save sets of tags, folders, notes and labels as templates (Settings) and apply them from the selection bar. Rename many items with a pattern ({name}, {n:3}, {date:%Y%m%d}, {w}x{h}…) with a live preview; one Undo restores every name.
- **First run.** Empty views explain the ways in (drop or paste, Eagle import, the browser extension, an agent over MCP), and a dismissable checklist walks through setup.

### Milestones 2 and 3 together
- Every search mode takes every filter: the vocabulary's aliases, implied
  tags and `ns:*` namespaces, `person`, `flag`, `label` and annotation text
  (through `q`) work with `semantic`, `similar`, `color`, `text`, hybrid
  and search by image as well as with the plain list, and so does a
  folder-limited token's scope.
- `stacks=collapse` in a ranked search collapses after ranking: a stack
  shows once, as its best-ranked member (the one that matched, with its
  `match` and snippet, plus `stack_count`). The plain list keeps showing
  each stack's cover.
- Smart folders save any of the new fields (`semantic`, `color`,
  `color_tol`, `text`, `has_text`, `stacks`, `person`, `flag`, `label`).
- Uploading a new version of an item that's in a stack puts the new
  version in that stack as its cover.
- The command palette also finds named people when faces are on.
- MCP is one set of tools: `search_items` takes every new parameter
  (`semantic`, `similar_to`, `color`, `color_tol`, `text`, `has_text`,
  `collapse_stacks`, `person`, `flag`, `label`) and `update_item` takes
  `caption`, `flag` and `label`.
- `kist rebuild-index` puts stacked items back in their stacks without
  touching their `modified_at` (verify used to report them afterwards).

### The web app with milestones 2 and 3 together
- **One keyboard map.** Every shortcut is in one table per screen
  (`web/src/keymap.ts`, shown in the README's Keyboard section) and a test
  fails if a key does two things on one screen, if a handler checks a key
  the table doesn't list, or if the README drifts. S in the grid opens and
  closes a stack; survey moved to N (Lightroom's key; C still compares). On
  a board, ⌘Z is the board's own undo only (it no longer also opens Recent
  changes when focus is on the board's toolbar).
- Search modes, flag and label filters, the type filter and every view
  round-trip through the URL together, and a smart folder saves the search
  mode, colour, text, person, flag and label filters.
- The type filter offers every kind the server knows (audio, 3D models,
  fonts, design files and bundles as well as images, videos and PDFs), and
  the sort menu has Date taken. The milestone 4 and 5 item fields
  (provenance, link status, media, taken, place) are typed in the client;
  their screens come later.
- Faster first load: Settings, the boards list, a board, triage, Organise,
  compare, the command palette and preview notes load when first used, and
  React and TanStack are their own long-cached files. The app's own script
  went from 579 kB (179 kB gzipped) to 238 kB (75 kB gzipped).

### Milestone 4: in, out and sharing

(Upgrade notes: in the combined list above.)

#### Added
- **Share links.** Read-only pages at `/s/<slug>` for a folder, smart
  folder, board or hand-picked items, with an optional password, expiry
  (30 days by default), and download / original switches. The first pages
  Kist serves without a token: plain HTML with no script, a sandboxing CSP,
  noindex, a per-link signed cookie, rate-limited passwords, 410 once
  expired or revoked, and views counted without recording who
  (`/api/shares`, write and admin tokens).
- **Portable export.** `POST /api/exports` builds a BagIt 1.0 zip with the
  originals under readable folder paths and names, `.json` and `.xmp`
  sidecars, optional history, `items.csv` and an `index.html` that browses
  offline. Old exports are removed after `KIST_EXPORT_RETENTION_DAYS`.
- **Export presets.** Resized/converted copies (jpg, webp, png, avif) with
  GPS always stripped, an optional text watermark, or a PDF contact sheet,
  from `POST /api/items/export-images`; presets saved in
  `library/.kist/export-presets.json`.
- **Provenance and the AI-generated flag.** Every item records where it came
  from (page and image URL, title, when, how, creator, credit, licence,
  rights URL) and whether it is AI-generated, read from IPTC
  `DigitalSourceType` in XMP or a C2PA manifest. Editable (and undoable),
  and filterable with `ai_generated=` and `license=`.
- **XMP sidecars.** `items/<id>/original.xmp` for every image, merged with
  what other tools wrote; ratings, keywords, notes, labels and rights come
  in from embedded XMP and from a `.xmp` dropped beside a file in the inbox.
  `kist xmp --export DIR` writes `<name>.xmp` beside exported files.
- **Page snapshots.** `POST /api/items/{id}/snapshot` keeps the page an item
  came from (sent by the browser, or fetched once by the server with
  `KIST_SNAPSHOTS=1`), scripts stripped, served sandboxed; its text is
  stored as item text for search and `get_page_snapshot` over MCP.
- **Link-rot watch.** Weekly, polite checks of every source page record
  `ok`, `moved`, `gone` or `error`; gone sources are counted in
  `/api/stats` (`source_gone`) and listed with `source_status=gone`.
  `KIST_WAYBACK=1` keeps the Wayback Machine's copy as well.
- **Importers.** `kist import pinterest|arena|raindrop|takeout|apple-photos|
  sidecar|eagle <path|url> [--dry-run] [--folder NAME]` and
  `POST /api/imports`: each run files into `Imported/<kind> <date>`, keeps
  provenance, is one undoable history batch, and a dry run reports counts,
  duplicates and errors. The Eagle importer is the former standalone
  `aerie_import.py`, now built in (folders, tags, ratings, notes, trash and
  simple smart folders).
- **iPhone share sheet.** `POST /api/items` takes `{url, title, text}` from
  a Shortcut and answers `Saved to Kist: <name>` when asked for
  `text/plain`; `docs/ios-shortcut.md` and `GET /api/shortcut` walk through
  building the Shortcut with an add-only token.
- MCP: `set_provenance`, `export_collection`, `export_status`,
  `get_page_snapshot`.
- CLI: `kist import`, `kist xmp`, `kist link-check`.

### Milestone 5: formats and media

Kist now looks inside video, audio, photos, RAW files, design sources, SVG,
3D models, fonts and zip bundles, and adds a timeline, On this day and a map.

(Upgrade notes: in the combined list above.)

#### Added
- Video: a hover-scrub sprite per video (`scrub.webp` + `scrub.json`, 20
  frames), and the same for animated GIF and WebP. ffprobe metadata:
  duration, fps, codec, bitrate, audio, rotation, vertical.
- Pick a video's cover frame (`POST /api/items/{id}/cover`; undoable), save
  any frame as a new PNG item linked `derived_from` the video
  (`POST .../frames`), and cut clips to MP4, GIF or animated WebP, or MP3/WAV
  from audio (`POST .../clip`, a worker job; `GET /api/jobs/{id}` reports
  `result_item_id`).
- Play anything: `GET /api/items/{id}/stream` serves the original with HTTP
  Range when browsers can play it, else a web-safe H.264 proxy made on first
  play. Items say `playable: native | proxy | none`.
- Audio items (mp3, wav, flac, aac, m4a, ogg, aiff): waveform thumbnail and
  peaks (`GET .../waveform`, BBC audiowaveform JSON), duration, artist, title.
- Camera metadata from EXIF (camera, lens, focal length, aperture, shutter,
  ISO, date taken, GPS) with filters on `/api/items` (`taken_after`,
  `taken_before`, `has_gps`, `camera`, `lens`, `place`, `duration_min`,
  `duration_max`, `vertical`, `has_audio`) and `order=-taken`.
- RAW files (CR2, CR3, NEF, ARW, RAF, ORF, RW2, DNG, PEF) preview from their
  embedded JPEG; `raw_pair_key` (basename + date taken) pairs a RAW with its
  camera JPEG for stacks.
- Design-file thumbnails from the preview inside PSD/PSB, AI, EPS, Sketch,
  Procreate, Krita, OpenRaster, Affinity and Blender files (new kind
  `design`); Figma files show an icon until a poster is uploaded.
- SVGs are sanitized for thumbnails and "Copy SVG" (`GET .../svg`), with
  their exact fill and stroke colours in `media.svg.colors`.
- 3D models (glb, gltf, obj, fbx, stl, usdz, ply; kind `model`): bounds and
  triangle counts, OBJ/STL/PLY converted to GLB (`GET .../model`), and
  posters from the web viewer (`POST .../poster`).
- Fonts: `GET .../font-info` (family, style, glyphs, axes, scripts) and
  `GET .../glyphs`; `/file` serves fonts as `font/*`, same-origin.
- Zip files are kept whole as bundles (kind `bundle`): `GET .../bundle`
  lists them, `GET .../bundle/{path}` serves one file, and the preview is the
  best picture inside.
- `GET /api/timeline` (day/month/year counts by date taken),
  `GET /api/on-this-day`, `GET /api/map` (server-side clustering) and
  `GET /api/map/config`; offline place names (`kist geo-download`), searchable.
- MCP: `view_video` (a contact sheet with timestamps), `save_frame`,
  `make_clip`.
- CLI: `kist media-backfill`, `kist geo-download`.

#### Changed
- ffmpeg runs under `nice` (and `ionice` on Linux) with
  `KIST_FFMPEG_THREADS` threads (default 2); clip and proxy jobs wait behind
  imports and previews.
- PSD files are kind `design`, not `image`; `type=` accepts audio, model,
  font, design and bundle; `/api/stats` counts them.
- Video `width`/`height` follow the rotation a phone recorded.

#### Security
- ffmpeg reads every input with the demuxer named by the sniffed MIME type
  and refuses types without one, so a playlist named `.mp4` can't open other
  files.
- SVG sanitizing: no DOCTYPE/entities, scripts, event handlers,
  foreignObject or external references.
- Bundles are never extracted; traversal paths are refused and size, total
  and entry-count caps stop zip bombs. Design-file zip entries are capped.

### Milestones 4 and 5 with 2 and 3
- One filter path: the provenance filters (`ai_generated`, `license`,
  `source_status`) and the media filters (`taken_after`, `taken_before`,
  `has_gps`, `camera`, `lens`, `place`, `duration_min`, `duration_max`,
  `vertical`, `has_audio`) go through the same filter builder as M2 and M3's,
  so they work in the plain list, `semantic`, `similar`, `color`, `text`,
  hybrid and search by image, and for a folder-limited token. Smart folders
  can save them, and a share or export of a smart folder holds exactly what
  it shows. `/api/timeline`, `/api/on-this-day` and `/api/map` take every
  `GET /api/items` filter (flags, labels, people, tags with the
  vocabulary, `text=` ...).
- `q` also finds the text of a saved page snapshot and the place name;
  `text=` (and `has_text`) stays the text inside images and PDFs only.
  Snippets prefer OCR/PDF text, then the page, then the caption.
- Boards can be shared and exported: their live items in reading order (top
  to bottom, left to right); a trashed board can't be.
- RAW+JPEG stack suggestions use the date Kist reads from the RAW itself
  (`raw_pair_key`, `taken_at`), so a reused file name from another shoot
  isn't paired; times are compared to the second.
- Fonts keep Milestone 2's specimen as their thumbnail and gain font-info
  and glyphs. What gets embedded (for meaning search and "more like this"):
  images, RAW, SVG, video, PDF and design-file previews, font specimens, and
  a model's or bundle's real picture; never audio (its thumbnail is a
  waveform) or a generic icon. Waveforms, font specimens and icons get no
  perceptual hash or palette. OCR stays with screenshots and PDFs.
- The browser extension's requests as it sends them: `POST /api/items`
  takes flat provenance fields beside the file or URL (`page_url`,
  `image_url`, `title`, `captured_at`, `method` incl. `bookmarklet`,
  `author`/`creator`, `credit`, `site`, `license`,
  `license_url`/`rights_url`; a value that doesn't validate is dropped
  rather than failing the save), and `POST /api/items/{id}/snapshot` takes
  `{html, page_url, title, captured_at}` or a raw `text/html` body.
- MCP: `search_items` and `search_by_image` take the provenance and media
  filters; `search_by_image` also `person` and `collapse_stacks`; `type`
  lists the new kinds and `order` takes `-taken`/`taken`.
- `kist rebuild-index` and `kist verify` cover all five milestones at once
  (shares, provenance, snapshots, XMP, link-rot results, media blocks and
  their sidecars, alongside M2/M3's data).

### Milestones 4 and 5: the web app
- **Share links.** Share a folder or smart folder (the top bar's share
  button), the selection (the selection bar) or a board (its toolbar): a
  title, an optional password, an expiry (30 days by default, a date, or
  never) and download / original switches (originals need downloads on).
  The link is copied with one press. Settings → Sharing lists every link
  with its views and last view, and revokes or removes them.
- **Export.** A BagIt zip of a folder, smart folder, the selection or the
  whole library (originals, sidecars, history), with progress and the
  download when it's ready. **Export images** makes resized copies (size,
  JPEG/WebP/PNG/AVIF, quality, metadata, a text watermark) or a PDF contact
  sheet, from saved presets you can add to and delete; GPS is always removed.
- **Where it came from.** The info panel shows an item's provenance (page,
  image URL, title, creator, credit, licence, rights URL, how and when it
  came in), the source page's link status with the Wayback Machine copy,
  and the AI-generated flag with what the file's C2PA/IPTC data said. Edits
  send only the fields that changed and are undoable. **Saved page** opens
  the page snapshot in a fully sandboxed frame, or its text.
- **Import.** Settings → Import picks the kind (Eagle, Pinterest, Are.na,
  Raindrop, Google Takeout, Apple Photos, files with sidecars), uploads the
  export or takes a server path (or an Are.na URL), runs a dry run with its
  counts, duplicates and errors, then the import with progress. It explains
  the XMP sidecars too.
- **Phone and browser.** Settings → Phone & browser shows this server's
  iPhone Shortcut steps (`GET /api/shortcut`) and links to `/bookmarklet`,
  the bookmarklet generator (no token needed). The bookmarklet's **/add**
  card previews the image (no referrer), lets you pick another image from
  the page, add tags, a folder (recent first) and a note, and saves only on
  Save (Enter saves, Esc cancels) with `method: bookmarklet` provenance;
  "Already in Kist" for a repeat, plain words for a failed download, and
  Back to the page when the pop-up was blocked. Parameters are checked:
  only http(s) URLs, length caps, text never HTML.
- **Needs attention** also lists items whose source page is gone.
- **Video and GIFs.** Tiles scrub through the 20-frame sprite as the
  pointer moves across them (desktop only; the sprite loads on first
  hover). The full-screen view plays from `/stream` (with Range; a web copy
  is made on first play for files browsers can't play), and has Set cover
  (the frame on screen), Save frame (a new item) and **Clip…** (in and out
  from the player or typed, MP4/GIF/WebP, size; the job's result opens in
  one press).
- **Audio** plays under its waveform (click to seek) and clips to MP3 or WAV.
  **SVG** shows the sanitized drawing with Copy SVG and its colours (press
  one to copy it). **Fonts** show an editable specimen in the font itself,
  size and variable-axis sliders, and the glyphs page by page. **3D models**
  turn in `<model-viewer>` (loaded only when a model is opened); Set poster
  renders the current view as the item's thumbnail. **Bundles** list their
  files (preview the images, view a model inside, download one file).
  **Design files** show their embedded preview with a note about the source.
- **Media details** in the info panel: camera, lens, exposure, date taken,
  place and location (with a privacy note and "Items near here"), or
  duration, frame rate, codecs, bit rate and sound; triangles and size for
  models.
- **Filters**: date taken, camera, lens, place, has a location, vertical,
  has sound, length, AI-generated, licence and source page status, each a
  chip in the search box; they round-trip through the URL and smart
  folders save them.
- **Timeline** (`/timeline`): days, months or years with counts and a bar,
  jump links by section; picking one shows those items in the grid sorted
  by date taken. **On this day** sits on top of it and in the info panel
  when nothing is selected.
- **Map** (`/map`): MapLibre GL with the self-hosted Protomaps basemap
  through the PMTiles protocol (or a hosted XYZ/PMTiles URL), server-side
  clusters as buttons, and a list of the groups beside it for the keyboard;
  picking a group (or "Show these in the grid") filters the grid to that
  area. Without a basemap the points still show on a plain map.
- Phone layout for every new screen; axe-core finds nothing in the dark,
  light and gallery themes. MapLibre (with its worker as a same-origin file)
  and model-viewer are separate chunks loaded on first use; the first page
  grew from 238 kB to 258 kB (81 kB gzipped).
- Server: `GET /api/items` (and every listing, smart folders, shares and
  exports) takes `date_after`/`date_before` (the timeline's date: taken,
  else added) and `bbox=w,s,e,n` (items with GPS in a map area);
  `POST /api/imports/upload` keeps an export sent from the browser in
  `library/import/uploads/`; the app shell sends `frame-ancestors 'none'`
  and `X-Frame-Options: DENY` (the /add card can't be framed), the /add
  page may load images from the web for its preview, a hosted
  `KIST_MAP_TILES_URL`'s origin is allowed for images and fetches, and
  `script-src` allows `'wasm-unsafe-eval'` (WebAssembly only, never JS
  eval) for the 3D viewer's mesh decoder.
- Fixed: the video notes scrubber had a label without a role, and the
  Notes button pointed `aria-controls` at a panel that wasn't there.

### Milestone 6: phone and speed (backend)
- **Date buckets for the scrubber.** `GET /api/items/buckets` counts the
  items of a listing per month (UTC, like the timeline), in the list's
  order, with the same filters as `GET /api/items` (smart folders, tags,
  colour labels, `text=`, a folder-limited token's scope ...). It takes
  `sort=created|modified|taken&order=asc|desc` or the list's own
  `order=-taken`, and answers `{sort, order, item_order, total, buckets:
  [{key: "2024-06", count, cursor}]}`: each cursor, passed unchanged to
  `GET /api/items?order=<item_order>&cursor=...`, starts at that month's
  first item and pages on into the next. MCP: `item_buckets`.
  `order=modified` (oldest edit first) joins the list orders.
- **Unfiled count** in `GET /api/stats` (`unfiled`) for the app badge.
- **`kist media-backfill` only does missing work.** Reading an item's file
  now leaves `checked_rev` in its media block, also when there was nothing
  to read (screenshots, PDFs), so later runs skip it; it is in meta.json, so
  it survives `rebuild-index` and `verify` checks it. A failed read leaves
  the item for the next run; `--all` still does everything.
- **First install:** the app, the worker and the container entrypoint check
  that the library is writable and otherwise stop with one line naming the
  `chown` to run. The README's quick start creates `library/` owned by uid
  1000 first.
- **Speed at 250k items:** migration 0701 adds a live-items `(taken_sort,
  id)` index, has the created/modified/taken list indexes include
  `stack_id` (the grid's `stacks=collapse` stays an index-only scan) and
  gives Postgres statistics on the month expressions, so month counts over
  250k items take ~10 ms (~30 ms with stacks collapsed) instead of ~60.
- **Benchmark harness** in `scripts/bench/`: `make_library.py` writes a
  synthetic library of any size straight into Postgres, `run.py` times
  every read path (p50/p95, JSON + a markdown table). See its README.
### Milestone 6: phone editing and the app shell (web app)
- **Editing on a phone.** The item sheet and the full-screen preview now
  have tap stars, tags (add with suggestions from your existing tags as you
  type, ✕ to remove), **Move to…** (a bottom sheet with a folder search,
  your five most recent folders and the whole tree; *Move* makes it the
  item's only folder, *Also in* adds one) and **Trash** with Undo. Every
  change is one batch in Recent changes. Notes, renaming, folder management,
  smart folders and Settings stay on the computer; a read-only token sees
  no edit controls. The desktop layout is unchanged.
- **Opens offline.** A service worker keeps the app shell (the page, the
  built scripts and styles, fonts and icons) so Kist opens without a
  connection and says it is offline; it never stores anything from the
  library (`/api/*`, item files, thumbnails), MCP or share pages. A new
  version installs in the background and shows "Kist was updated" with
  Reload. It registers only on https (or localhost) in a production build.
- **Offline banner** when the browser is offline or the server stops
  answering; it clears by itself when the connection is back.
- **App badge:** the installed app's icon shows how many items are in
  Unfiled (where the browser supports it), updated as you file things.
- **iPhone:** the first visit in Safari shows a one-time hint on adding
  Kist to the Home Screen.

### Fixed (2.0 smoke test)
- Triage: each decision takes the item out of the Unfiled queue, so
  "N left" counts down, and ⌘Z puts it back.
- A bulk tag edit's notice offers Undo, like Trash and Stack.
- AI tag suggestions and collection labels no longer guess with a small
  vocabulary (a dog as "watch" or "brand:Omega", buildings as
  "credit:Wikimedia"): a zero-shot tag must stand out against the picture's
  match with generic reference prompts (a z-score) and clear a raw cosine of
  0.24; credit:, license:, source: and other bookkeeping tags are never
  suggested or used as labels; a "similar items" tag whose name the picture
  clearly doesn't match the way its neighbours do is dropped; a collection's
  zero-shot label must be what most of its members match too, in the usual
  spelling of the tag. On the 60-photo seed set over 11 vocabularies,
  zero-shot precision went from 43% to 99%; in a library seeded with 3 of
  10 photos tagged per category, every suggestion was right (93/93, was
  121/168).
- Trashing a protected item says why ("Couldn't move it to Trash:
  Protected: unprotect it first.") without a useless Retry: Retry is offered
  only where trying again could help (no answer, a server error, a timeout).
- Board: ⌘A, ⌘Z, ⌘D, ⌘G and Escape work with focus on the toolbar, not only
  after clicking the canvas.
- Uploading a new version selects it (the info panel follows) and stacks the
  version chain with the new version as the cover, so the grid shows one
  picture; the notice offers Compare.
- `PATCH /api/items/{id}` with `base_modified_at` answers 409 only when a
  field it writes was changed since: a link, tags, a stack or a rating
  changed meanwhile no longer refuse a notes or name save.
- Captions through an OpenAI-compatible server on the home network
  (`KIST_CAPTION_URL` on a private address) no longer warn that images go to
  OpenAI; another host is named in the warning.

### Milestone 7: presence and launch (docs and brand only)
- **README:** a new top: the lockup, the tagline, a hero screenshot (dark or
  light with the reader's GitHub theme), a one-paragraph pitch, what's new in
  2.0, the demo loop, a factual comparison with Eagle, PureRef and Immich,
  the quick start with the `library/` ownership step, and connecting an
  agent. The reference sections below are unchanged. A broken link to the AI
  section is fixed.
- **Docs site:** `site/mkdocs.yml` (MkDocs + Material) builds the pages in
  `docs/` into a static site: a landing page, Install (with a reverse
  proxy), Upgrade (1.5 to 2.0 in order, with the backfills as one detached
  chain), Coming from Eagle, Settings, AI, Agents (MCP), Phone and PWA,
  Backup and verify, Privacy (every outside contact Kist makes, and how to
  turn it off), plus the existing architecture, maps, Shortcut, brand and
  releasing pages. Fonts are self-hosted; `mkdocs build --strict` passes.
  `.github/workflows/docs.yml` builds it, and publishes to GitHub Pages only
  when run by hand with "deploy" ticked.
- **Demo library:** `docs/demo/library.json` lists 43 public-domain and CC0
  Wikimedia Commons images (chip dies, patent drawings, architecture, prints,
  posters, textiles, cars, maps, aircraft, space); `docs/demo/seed.py` adds
  them to a throwaway instance under short names with tags and provenance
  (creator, licence, Commons page), with a descriptive User-Agent and a pause
  between downloads, and `docs/demo/dress.py` adds folders, ratings, stacks,
  a board and a smart folder. `docs/demo/CREDITS.md` credits every image.
- **Screenshots and demo loop:** every README and docs picture is taken on a
  throwaway instance holding only the demo library. `docs/demo/capture.py`
  takes them in three themes, desktop 1440x900 and phone 390x844 (grid,
  colour search, the phone item sheet), and `docs/demo/record.py` records a
  ~19 s silent loop (the grid, an item, full screen, search by colour, a
  board) as a GIF under 5 MB, read-only with Playwright. The loop's MP4 is
  not in the repository: `record.py` writes it outside, to attach to the
  GitHub release.
- **Social preview:** `docs/brand/build_social.py` makes the 1280x640 card
  (lockup, tagline, a collage of demo-library images) for GitHub's social
  preview and the docs site.

### Design pass

The aesthetic pass: one grey, one control language, one type system.

- Screen headers (Timeline, Map, Organise, Settings, Boards, Triage) and the board bar sit on the same chrome grey as the top bar and sidebar, with the same light pinstripe; no more second grey band.
- Real italics: Inter's italic face is loaded, and the browser never fakes a slanted or bold style again.
- Every text size sits on Kist's six-step type scale (11/13/15/18/24/34 px); headings no longer pick up the browser's own sizes ("On this day" was 19.5 px bold). A test keeps it that way.
- Gallery grey's phone status bar is the same grey as the top bar (it was a leftover darker grey), and the app opens in its theme's colour with no dark flash in gallery or light.
- Light theme: the grid sits on a neutral light grey (#ececec) instead of warm paper, so photos aren't judged against a warm surround, and the full-screen preview uses the gallery mid-grey instead of near-black. The chrome stays warm paper.
- Smaller corners: grid tiles 4 px (square in compact density), the full-screen image square, board items 2 px.
- Menus, popovers, dialogs, toasts and the ⌘K palette share one floating look: a darker surface, a hairline edge and a soft layered shadow over a lighter backdrop. The palette is tighter and shows its keys as keycaps.
- Calm motion: menus and dialogs fade and slide in over 120 ms, the info panel slides in and fades out, buttons give slightly when pressed. Reduce Motion turns all of it off.
- Brass means something again: it's kept for the main button, selection, the focus ring and ratings. Tag, folder and board chips are neutral grey, links in the info panel are white and underlined, the Info toggle, Settings sections and the ⌘K active row show a fill with a thin brass bar, and tile selection uses the theme's own brass.
- One control language: every button and field is 28 px tall; buttons are quiet (no box until you hover), Upload and other main actions are the only brass buttons, fields keep their outline, and dropdowns get Kist's own chevron.
- Type details: sentences get 1.5 line spacing and a comfortable line length, small section labels share one 11 px semibold style, every label is in sentence case ("Triage unfiled", "In trash"), and secondary text in gallery grey is a visible step dimmer.
- Titles get a voice: every view, page and panel title is set in Instrument Serif (Timeline, Map, Boards, Triage, each Settings section); counts are in Inter with even-width figures, and the monospaced face is kept for machine strings like MIME types, hashes and URLs.
- Icons: three sizes and one 1.5 px line everywhere, and one meaning per icon (the sparkle is only for AI, a flag always means Picks, boards have their own icon, themes no longer show a lightning bolt).
- Settings sit in one 640 px column with their buttons on its edge; the Appearance menu shows each theme as a colour chip, with the radio buttons lined up with the first line of text.
- **Columns layout:** a third tile shape next to Rows and Square. Fixed-width
  columns show every image uncropped at its own height (only shapes beyond
  1:3 or 5:1 crop), like Eagle's Waterfall. Each item goes into the shortest
  column, so the order still reads left to right: arrow keys, Shift+arrows
  and Shift+click select the same runs as in Rows, and Up/Down move to the
  tile directly above or below in the same column. The Size slider sets the
  column width; it works with the date scrubber, stacks, every density and
  on a phone (2 columns or more; 1 column stays the feed). Rows stays the
  default. On a phone the tile-shape button steps Rows, Square, Columns.
- Phone polish: the top bar drops the desktop-only ⌘K and Export buttons (Share and Export are in the drawer now), search is a 44 px pill, the second row is one calm filled style, and each view gets a large serif title. The item sheet lifts off the grid with a light top edge, rounder corners and a visible grabber, tags and folders are neutral chips, Trash is a bin icon beside a full-width Move to…, and the tile you opened shows its brass corners without the multi-select check. The drawer opens with the Kist mark and name. In the full-screen preview the stars, Tags / Move / Trash, the arrows, Notes and close float in dark glass capsules that stay readable over any picture (solid when Reduce Transparency or Increase Contrast is on).

## [1.5.0] - 2026-11-09

A hardening release. Kist was reviewed for security, data integrity,
failure handling, performance, accessibility, observability and first-use
friction, and everything found was fixed:

- A file can no longer pretend to be a PDF to run script as Kist.
- The on-disk copy of the library (meta.json) is written atomically and
  `kist rebuild-index` no longer drops anything silently.
- Edits from two tabs or from an agent can't overwrite each other unnoticed.
- Failed saves say why and offer Retry, trashing offers Undo, and a lost
  token no longer throws away uploads or typed text.
- The whole app works from the keyboard and with a screen reader (WCAG 2.2 AA).
- `GET /api/health/deep`, request ids, client error reports and
  `kist selftest` make problems visible.
- Phones can upload, and zooming a desktop browser keeps the full app.

After upgrading, run `kist rewrite-meta` once (it adds the preview revision
to every meta.json), then `kist selftest`. If Kist sits behind a reverse
proxy on the same host, set `KIST_BIND=127.0.0.1`.

### Security
- **A web page saved with a `.pdf` name can no longer run as Kist.** Kist
  decided a file was a PDF from its name, then served it inline, so an HTML
  "invoice.pdf" could run script with your token as soon as you opened it.
  The type now comes from the file's contents everywhere: only real images,
  video, audio and PDFs are shown in the browser, and anything else is a
  sandboxed download, whatever it's called. Run `kist reclassify` once to
  tidy up items added before this (see Changed).
- The API docs (`/docs`, `/redoc`, `/openapi.json`) need the token. In a
  browser, being signed in to the web UI is enough.
- A token with non-ASCII characters is refused with 401; it used to cause a
  server error.
- Direct uploads are capped at `KIST_MAX_UPLOAD_BYTES` (4 GiB by default);
  bigger ones get 413.
- `source_url` must be an http(s) address. Anything else (say, `see notes`)
  could blank the web UI when you opened the item.
- New `KIST_BIND` setting for the address the port is published on. The
  default is unchanged (every interface); set `KIST_BIND=127.0.0.1` when the
  reverse proxy runs on the same host so the LAN can't reach Kist over plain
  HTTP. The Postgres password default stays `kist`, now with a note to change it.

### Performance
Measured on a 4,417-item library; details in the performance review.
- **The full-screen view no longer downloads originals on a phone.** Every
  image over 1024 px gets a 2048 px WebP copy (about 0.5 MB, where
  originals run 5-30 MB), and the preview picks the copy unless the screen
  shows the image wider than that. Five swipes through large photos went
  from 93 MB to about 3 MB. Run `kist regen-previews` once to make copies
  for existing images; it makes only the missing copy, leaving the other
  previews alone.
- **Edits no longer reload the whole list.** Changing a rating, tags or
  folders, trashing or restoring updates the loaded list in place (an
  item that no longer belongs in the view drops out of it), instead of
  refetching every loaded page (37 pages and 4.5 MB on a fully scrolled
  library). Coming back to the tab no longer reloads the list either, and a
  batch upload refreshes it once at the end, not after every file.
- **Compressed responses:** JSON, JS and CSS are gzipped (3-6× smaller);
  images and videos aren't.
- **Caching:** the app's hashed files, originals and versioned thumbnails
  are cached by the browser for good. Thumbnail URLs carry `?v=<preview_rev>`
  (new on items), which changes whenever a preview is regenerated.
- **Faster start:** the app no longer waits on a server check before
  loading the library.

### Fixed
- **Edits no longer fail silently or overwrite each other** (from a trace of
  network drops, server errors, slow responses, sign-outs and double
  submits):
  - Tags and folders are changed with add/remove
    (`PATCH /api/items/{id}/tags` and `/folders`, `{add, remove}`) instead of
    sending the whole list, so two quick clicks, or a browser tab and an
    agent working on the same item, can't undo each other.
  - Writes to one item go out in click order, so the server ends on your
    last click.
  - A failed save says why ("No connection to the server", "Server error
    (500)", "The server took too long") with Retry. Unsaved notes and
    names are kept, marked "not saved yet", survive selecting another item,
    and the browser asks before closing the tab on them.
  - Requests time out after 30 s (URL adds after 150 s) instead of hanging.
  - Bulk actions carry on past a failure, then say "Tagged 37 of 40. 3
    failed: …" with Retry, keeping just the failed items selected.
  - When the token stops working, the token screen appears over the app
    instead of replacing it: queued uploads and typed text survive, and
    uploads now notice a sign-out too.
  - Folder and smart-folder actions say when they fail.
- The token screen accepted any token: it checked `/api/health`, which needs
  none. It now checks with a request that does.
- **A file dropped in the inbox can no longer be lost.** If importing it
  failed partway (for example the database was briefly unavailable), Kist
  deleted it and then marked the job done. Now the file is put back, a
  missing file fails the job instead of passing it, and after the last
  attempt the file is kept in `library/failed/` under its own name.
- **Re-adding something that's in the Trash says so.** Instead of "already in
  library" for an item you can't see, the upload shows "in Trash" with a
  Restore button. API and MCP answers carry the item's `state`.
- **Uploads stay in the folder you dropped them in.** Switching folders while
  a batch was uploading sent the rest of the batch to the new folder.
- **Jobs left running by a stopped worker are picked up again** after
  `KIST_JOB_LEASE_SECONDS` (default 900) instead of staying "running" forever.
- Smart folders now show as selected, and "All items" is only highlighted
  when nothing narrows the view.

- **meta.json and folders.json can no longer be corrupted by two saves at
  once.** Every writer used the same temporary file; two quick edits (a tab
  and an agent, or an edit while a preview was being remade) could leave a
  half-written file, or answer "Couldn't save" for a change that had saved.
  Each write now uses its own temporary file, is flushed to disk, and
  re-reads the database under a lock, so the file always ends on the newest
  state. If writing the file fails, the edit still succeeds and
  `kist verify` points at it until `kist rewrite-meta` writes it again.
- **Edits made in another tab or by an agent are no longer silently
  overwritten.** A notes or filename save from a page that was opened
  before someone else changed the item is refused (409) with the current
  item, so the web UI can ask which to keep.
- **Inbox files can't get stuck in `.staging` any more.** If the database was
  down when the worker picked up an inbox file, the file sat in
  `library/.staging/` forever with no import. It now goes back to the inbox,
  and the worker sweeps staging at start and every 5 minutes for anything
  left there.
- **Dropping a file that's already in the library onto a folder files it
  there.** A duplicate upload or URL add used to ignore the folder, tags and
  notes sent with it.
- **Requests that name a folder that doesn't exist fail instead of quietly
  doing nothing.** Uploading to, or filing into, a folder deleted in another
  tab answered "done" and left the items unfiled; it's now a 422 "Unknown
  folder". Removing an item from such a folder still just works.
- `kist rebuild-index` no longer drops things without a word:
  - An item folder with its file but no meta.json is restored from the file
    (as `recovered-<id>.<ext>`, since its name, tags and notes only lived in
    meta.json) and reported.
  - `--wipe` checks folders.json can be read before emptying anything.
  - A folder whose parent is missing, or that's in a parent loop, is
    restored at the top level with a warning.
  - Thumbnail versions (`preview_rev`) are saved in meta.json and restored,
    so browsers don't keep showing an old cached preview after a rebuild.
  - Anything it had to skip or work around makes the exit code 2.
- Deleting, renaming or moving a folder rewrites its items' meta.json, so
  `kist verify` no longer reports drift after an ordinary folder delete.
- `kist rewrite-meta` no longer overwrites edits made while it runs: it
  re-reads each item as it writes it, so it's safe to run with Kist up.
- Two folder moves at the same moment can't put folders in a loop (which
  hid them from the sidebar). Two writers adding the same new tag at once no
  longer fail with a server error.
- Moving a failed inbox file to `library/failed/` (and back on Retry) happens
  only after the job is saved, so a database hiccup can't leave a job
  pointing at the wrong place.
- Permanently deleting an item cancels its queued preview retries (no more
  noisy failed jobs) and can't be undone by a preview finishing at the same
  moment.
- `kist regen-previews` re-reads each item before working on it, and preview
  versions go up in the database, so running it alongside the worker no
  longer reuses a version number browsers have cached.
- Smart folders keep the preview-status and Trash filters they were saved
  with.
- The type filter and the stats now agree with each item's type (an `.mp4`
  that libmagic can't name counts as video everywhere).
- A malformed `cursor` is a 400, not a server error.
- MCP tools say why they failed (for example "Unknown folder: …" or "rating
  must be from 0 to 5") instead of only "Error executing tool".
- `tests/test_rebuild.py` no longer fails when the Postgres server isn't on
  UTC.

- Text typed into notes or a name while an earlier save was still on its
  way is kept; it used to be replaced by the saved copy when the save
  returned.
- Rating or renaming items while the list is sorted by rating, name or last
  edit no longer leaves them at the old position, shows a tile twice or
  drops one: that list reloads.
- Restore from an upload's "already in Trash" notice refreshes the counts
  once instead of twice.
- Backspace on a focused button no longer trashes the selection; Delete and
  Backspace work only with focus in the grid.
- The PDF preview stays an unsandboxed frame: Chrome's PDF viewer refuses to
  run in any sandboxed frame. Non-PDF files can't reach it, because the
  server serves them as sandboxed downloads.

### Added
- **Every view has an address.** `/`, `/all`, `/trash`, `/folder/<id>`,
  `/tag/<name>`, `/smart/<id>`, with filters as parameters and `?item=<id>`
  (`&full=1` for full screen). Refresh keeps your place, links and bookmarks
  work, and on a phone Back closes the full-screen view, then the item
  sheet, then the menu, instead of leaving Kist.
- **Item and preview states.** Items carry `state` (`active` / `trashed`),
  `preview_status` (`ready` / `pending` / `failed` / `none`) and
  `preview_error`. A failed preview shows ⚠ on its tile and a Retry button in
  the info panel and item sheet; trashed items show Restore.
- **Needs attention** in the sidebar lists inbox imports that failed (Retry
  or Dismiss) and links to the items whose preview failed.
- A banner when the server can't be reached; it clears itself.
- Upload errors have Retry and Dismiss buttons.
- REST: `view=unfiled`, `state`, `preview_status` filters;
  `POST /api/items/{id}/preview`; `/api/jobs` with retry and dismiss; stats
  count failed jobs and failed previews.
- MCP: `restore_item`, `retry_preview`, `list_jobs`, `retry_job`;
  `search_items` takes `view`, `state` and `preview_status`; items carry a
  `web_url` (absolute when `KIST_PUBLIC_URL` is set). `view_item` says why an
  item has no preview.
- `kist verify` reports drift between the database and the files on disk.

- `GET /api/health/deep` (with the token): database, schema revision, worker
  heartbeat, library disk space and writability, failed and stuck jobs,
  previews that failed in the last 24 hours, and the last daily verify.
  `/api/health` stays public and cheap.
- `GET /api/version` names the version and the commit the image was built
  from (build with `KIST_GIT_COMMIT=$(git rev-parse --short HEAD)`).
- `POST /api/client-errors` for the web UI's crash reports (logged, at most
  30 a minute).
- Every response carries an `X-Request-ID`, every log line names it, and a
  server error answers `{"detail": "Internal error", "request_id": …}`.
- The worker runs `kist verify` once a day (`KIST_VERIFY_INTERVAL_HOURS`)
  and writes a heartbeat to `library/.kist/` after each database check; the
  worker's healthcheck reads it and no longer trips during a long preview.
- `kist selftest` runs tiny sample files through every preview decoder and
  exits 1 if one fails. Run it after rebuilding the image.
- `kist reclassify [--dry-run]` gives items Kist can't preview the preview
  status "none" and lists items named `.pdf` that aren't PDFs.
- Settings: `KIST_BIND`, `KIST_MAX_UPLOAD_BYTES`, `KIST_LOG_LEVEL`,
  `KIST_VERIFY_INTERVAL_HOURS`, `KIST_GIT_COMMIT`. `.env.example` now lists
  every optional setting.

- **Undo after Trash.** Trashing items (the Trash button or the Delete
  key, one item or many) shows "Moved 3 items to Trash" with Undo.
- **"Changed elsewhere" instead of silent overwrites.** If notes or a name
  you're editing were changed in another tab or by an agent meanwhile, the
  web UI says so and offers Keep mine or Use theirs (it sends
  `base_modified_at`; the server answers 409).
- **Upload from a phone.** The phone menu (☰) has Upload, which opens Photos
  on an iPhone. Editing stays on the desktop.
- An Open ⤢ button on the info panel's picture, and tile tooltips that say
  double-click opens an item.
- The version and commit show in the sidebar footer and the library summary
  (from `GET /api/version`).

### Changed
- Unfiled is defined by the server (`view=unfiled`), so the web UI and agents
  see the same inbox. MCP's `folder="none"` still means "in no folder",
  tagged or not; its description no longer calls that "unfiled".
- Clicking a tag while in Unfiled shows every item with that tag (it used to
  show only those in no folder).
- Permanent delete (`DELETE /api/items/{id}?hard=true`) only works on items
  in the Trash; anything else gets 409. Trashing a trashed item or restoring
  an active one changes nothing.
- Folder delete confirmations say when subfolders go too.
- Migration `0003` adds the new columns and backfills preview status from
  existing data; jobs left `running` are re-queued.
- **Logs are readable and kept.** The app and worker log with timestamps,
  levels and logger names (`KIST_LOG_LEVEL`), and each service keeps 5 × 10 MB
  of logs instead of growing without limit.
- Input is checked the same way everywhere (web, REST, MCP, uploads), with a
  422 that says what's wrong: tag names can't be empty, contain a comma or
  run over 100 characters (they used to be cut short silently); folder and
  smart-folder names can't be blank or over 200; notes are capped at 100,000
  characters; filenames at 255, without `/`; ratings must be 0–5 (an upload
  with rating 7 used to become 5); dates more than a day in the future are
  refused; unknown `type` or `order` values are refused instead of ignored.
  The database enforces ratings too (migration `0005`).
- MCP `update_item(folder_id=…)` now adds the folder and keeps the item's
  other folders (it used to replace them all). `set_folders` removes.
  REST `PATCH /api/items/{id}` with `folder_ids` still replaces, as
  documented.
- MCP tools all return items in the same shape, `get_item` included (with
  `web_url` and `file_url`), and `search_items` takes `order`. Stats come
  from one database query.
- **After updating:** run `kist rewrite-meta` once (it adds the thumbnail
  version to every meta.json; until then `kist verify` reports it as drift)
  and `kist reclassify` once.

- **The Unfiled view explains itself.** It says it lists items with no folder
  and no tag, and the first time tagging or filing an item takes it out of
  Unfiled, a notice says where it went (See it in All items). A first visit
  with an empty Unfiled opens All items instead.
- **Empty results say why.** A search with no hits names the view ("Nothing
  in Unfiled matches “dog”") and offers Search everything; only an empty
  library talks about uploading (and on a phone, never about dropping files).
- The token screen asks for the "access token" and tells you to ask whoever
  set up Kist; where to find `KIST_TOKEN` is behind "Where do I find it?".
- The tile-shape toggle and the size slider have visible labels; the
  sidebar's empty hints are plainer ("Save a search: filter the grid, then
  press + above").
- The preview's ✕ is a round button of its own instead of floating over the
  toolbar behind.
- The server banner tells "Kist is up but its database isn't answering"
  from "Can't reach the Kist server", and server errors show their reference
  ("Server error (500, ref 3f9a…)") to match against the logs.
- Smart folders save and restore the preview filter and the Trash; a smart
  folder over the Trash keeps its own address (`/smart/<id>?trash=1`).
- A write to a folder deleted elsewhere says "That folder no longer exists"
  and reloads the folder list.

### Accessibility
All 59 findings of the WCAG 2.2 AA audit are fixed (axe-core: 0 violations
on the desktop, zoomed and phone views).
- **Everything works from the keyboard.** Sidebar rows are buttons (the
  current view is announced; tags say pressed or not), folder actions show on
  keyboard focus, and the grid is a multi-select list: arrows move and
  select, Shift extends, Ctrl/⌘ moves without selecting, Space adds or
  removes one item, Enter opens it, Home/End jump. A tile left behind by
  Trash hands focus to the next one.
- **Overlays behave like dialogs.** The preview, Needs attention, the phone
  menu, the token screen and every confirm/prompt take focus, keep Tab
  inside, close with Escape, make the page behind inert and put focus back
  where it was (the preview returns to the tile you ended on). The closed
  phone menu is out of the Tab order, and it has a ✕.
- **Every control and field has a name**: chip removers ("Remove tag
  holiday"), arrows, ✕ buttons, the five rating buttons (pressed state, ★/☆),
  the twisty (expanded state), search (a combobox with announced
  suggestions), the bulk bar, filename, tags, folders, notes, the token field.
- **Changes are announced**: result counts, saving, upload progress and
  results, bulk results, Undo, item status, the server banner, errors.
- **Visible focus** everywhere (accent ring; white ring on tiles, distinct
  from selection), stronger field borders, a clearer active row and
  highlighted suggestion, readable dim text on the active row, visible
  unrated stars, and 24 px minimum targets for small buttons.
- **Zooming no longer switches to the view-only phone layout.** The phone
  layout needs a touch screen; a zoomed desktop browser keeps every control
  and reflows instead.
- The phone preview keeps small ‹ › buttons, so changing items doesn't need a
  swipe. Headings, landmark names and text alternatives for stars, colours
  and file icons are in place.

### Removed
- The unused `KIST_THUMB_SIZES` setting (it never did anything) and some dead
  code.


## [1.4.0] - 2026-11-09

### Added
- **Remove unused tags.** `DELETE /api/tags/unused` (and the MCP tool
  `prune_unused_tags`) deletes every tag that no item uses and returns the
  names. Tags on trashed items are kept, so restoring an item keeps its tags.

## [1.3.0] - 2026-11-08

### Added
- **Phone layout.** On narrow screens (and phones held sideways) the grid
  fills the screen. Folders, smart folders and tags slide in from a ☰
  button and close when you pick one. Tapping an item opens a read-only
  sheet (tags, folders, notes, details); tap a tag or folder there to
  filter by it. The full-screen preview swipes left/right between items and
  down to close. A 2 / 3 column switch replaces the size slider; inputs use
  16 px text so iPhone Safari doesn't zoom, and the layout respects the
  notch and home bar. Editing stays on desktop for now. The desktop layout
  is unchanged.

## [1.2.1] - 2026-11-03

### Changed
- **Unfiled** is now the inbox of items with no folder *and* no tags, so
  tagging an item or putting it in a folder takes it out. The app still
  opens there. Clicking Unfiled clears other filters (keeping the sort), and
  leaving it (a folder, a tag, Trash) drops its "untagged" condition.
  `GET /api/items?folder=none` is unchanged (no folder, tags or not).

## [1.2.0] - 2026-11-03

### Security
- **Add from URL no longer reaches your private network.** Private, loopback,
  link-local, CGNAT, multicast and unique-local addresses are refused,
  including through redirects and a page's preview image. Kist connects to
  the exact address it checked, so a DNS answer can't change between check
  and connect. Allow trusted LAN sources with `KIST_URL_ALLOW_HOSTS` (host
  names or CIDR ranges) or all of them with `KIST_URL_ALLOW_PRIVATE=true`.
- URL adds keep only images, videos and PDFs, have an overall time limit
  (`KIST_DOWNLOAD_TIMEOUT_SECONDS`, default 120), and report network errors
  without detail so they can't be used to map a network.
- Uploads and URL adds no longer block the server: their work runs off the
  event loop, so a slow download or a heavy file can't stall the UI, the API
  or MCP.
- Previews are generated in a child process with a time limit
  (`KIST_PREVIEW_TIMEOUT_SECONDS`, default 300). PDF posters render at most
  2048 px on the long side: a 553-byte PDF declaring a huge page used 4.2 GB
  of memory before, about 90 MB now. ffmpeg reads only local files, in the
  container format the extension names.
- The containers run as an unprivileged user (uid 1000) with a read-only
  root filesystem, no Linux capabilities, no privilege escalation, and memory
  and process limits (`KIST_MEM_LIMIT`, default 4g). **Upgrading:** the
  library folder must be owned by uid 1000 (or set `KIST_UID`/`KIST_GID` to
  its owner).
- The `?token=` query parameter is no longer accepted: query strings land in
  access logs. Use the `Authorization: Bearer` header.
- Every response carries a strict Content-Security-Policy, X-Frame-Options,
  Referrer-Policy, Cross-Origin-Opener-Policy, Permissions-Policy and HSTS
  (`KIST_HSTS_SECONDS`, default 180 days; browsers ignore it over plain HTTP).
- Originals opened on their own are sandboxed documents, except PDFs, which
  Chromium-based browsers won't render sandboxed.

## [1.1.0] - 2026-11-02

### Added
- **True-shape grid** (the new default): tiles follow each image's own
  proportions in justified rows, as in Eagle. Every row fills the width; a
  row's height flexes up to 15% to fit, and the last row keeps the normal
  height. Very tall or wide images are cropped to between 1:2 and 3:1 so they
  stay readable. Items without a preview stay square.
- A **tile size slider** in the top bar (120–400 px, default 200; double-click
  resets) and a **square / true-shape switch**. Both are remembered in the
  browser.
- Up/Down arrow keys move to the nearest tile in the row above or below.
- Web layout tests (`npm test`), run in CI.

## [1.0.3] - 2026-11-02

### Changed
- A folder now shows everything in its subfolders too, at any depth: clicking
  it lists those items and its count includes them (an item filed in two
  subfolders counts once). The same applies to `GET /api/items?folder=`, smart
  folders, and the MCP `search_items` / `list_folders` tools.
- **All items** clears every filter (tags, smart-folder tag sets, untagged,
  dates, search text, type and rating) and keeps the sort order. The app still
  opens on Unfiled.

### Fixed
- Filters set by a smart folder (`tags_any`, untagged, date range) stayed
  active after you left it, with nothing on screen showing them, so folders
  could look empty. Every active filter now shows as a removable chip in the
  search bar.

## [1.0.2] - 2026-11-01

### Changed
- New app icon: a K whose upper arm is an arrow, inside a ring, light grey on
  deep grey (a matched set with sklent's S). It replaces the ⟡ glyph in the top
  bar and is the browser tab, bookmark and home-screen icon (`favicon.svg`,
  `favicon-32.png`, `favicon.ico`, `apple-touch-icon.png`). Icon URLs carry a
  version query so browsers pick up a changed icon.

## [1.0.1] - 2026-10-31

### Changed
- The web UI opens on **Unfiled** at every page load, so new arrivals waiting
  to be organized are the first thing you see.

## [1.0.0] - 2026-10-30

First public release.

### Library
- Plain-files storage: one folder per item, with the original, WebP previews,
  and a `meta.json`; `folders.json` holds the folder tree and smart folders.
- `kist rebuild-index` reconstructs the database from disk; `kist rewrite-meta`
  and `kist regen-previews` for maintenance.
- Exact-duplicate detection (SHA-256) and near-duplicate search (perceptual hash).

### Ingest
- Upload, drag and drop, paste (screenshots, copied images, image or page
  URLs), inbox folder, URL, and MCP.
- Previews for png, jpg, webp, gif, svg, avif, heic, mp4, mov, webm, and pdf;
  every other format is stored with a generic icon.
- HEIC fallback decoder for iPhone photos the system libheif rejects.
- Import metadata on upload: tags, folders, notes, rating, source URL,
  original dates, and trashed state.

### Organizing and search
- Nested folders with multi-folder membership, tags, ratings, notes, trash.
- Smart folders (saved searches), including match-any-tag and untagged filters.
- Fuzzy filename search, tag autocomplete, type/rating/date filters,
  cursor pagination.

### Web UI
- Virtualized grid that fills the window, sharp on high-density displays.
- Persistent info panel, multi-select and bulk actions, keyboard navigation,
  full-resolution preview.

### MCP
- Streamable-HTTP endpoint at `/mcp` with 14 tools, including `view_item`,
  which returns the image itself. No permanent-delete tool.

### Security
- The server refuses to start with a missing or placeholder token.
- The auth cookie only authorizes reads (no cross-site writes).
- Originals that could carry scripts (SVG, HTML, and other non-media files)
  are sandboxed or served as downloads.
