# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
