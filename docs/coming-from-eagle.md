# Coming from Eagle

Eagle is a good desktop library. Kist keeps the same idea (drop things in,
tag, folder, find by colour) and moves it onto a server you own: one library
reachable from every computer and phone, kept as plain files, and open to AI
agents over MCP.

| | Eagle | Kist |
|---|---|---|
| Where it runs | a desktop app (macOS, Windows) | a Docker Compose stack on your server or NAS; any browser, installable on a phone |
| Price | one-time licence (US$34.95, two devices) | free, MIT licensed |
| Storage | a `.library` folder of item folders + JSON | a `library/` folder of item folders: the original, previews, `meta.json` |
| Search | tags, folders, colour, filters; meaning and image search with the AI Search plugin | the same, plus meaning, image and text-inside-images search built in, on the server |
| Agents | an MCP plugin (beta) that runs beside the desktop app | a built-in MCP endpoint on the server: search, view, add, tag, file, undo, from any machine |
| Several devices | the library in a shared cloud folder, one app open at a time | one server; every browser, phone and agent talks to it |

## Import your library

1. Copy (or mount) the Eagle library somewhere the Kist worker can read it,
   under `library/import/`, e.g. `library/import/Design.library`.
2. In Kist, open **Settings → Import**, choose **Eagle**, name the path and run
   a **dry run** first: it reports counts, duplicates and errors and changes
   nothing.
3. Run it for real. Everything lands in a folder called
   `Imported/eagle <date>`, as one batch in Recent changes, so one undo takes it
   all back.

From the command line:

```bash
docker compose exec worker kist import eagle /library/import/Design.library --dry-run
docker compose exec worker kist import eagle /library/import/Design.library
```

What comes across: the folder tree, tags, ratings, notes, source URLs, the
trash, and simple smart folders. Every item keeps its provenance
(`imported_from: eagle` and Eagle's id). Exact duplicates are detected by
SHA-256 and kept once.

Afterwards, `kist ai-backfill` (it starts by itself for new items) makes the
whole import searchable by meaning and by the text in its screenshots.

Other importers: Pinterest, Are.na, Raindrop.io, Google Photos Takeout, Apple
Photos (through `osxphotos`), and any folder of files with `.json` or `.xmp`
sidecars.
