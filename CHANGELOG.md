# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
