# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

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
