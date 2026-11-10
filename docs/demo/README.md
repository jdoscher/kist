# Demo library

Kist's README hero, the docs site's screenshots, the demo loop and the social
card should show a library anyone may see, not a personal one. This folder
makes and records it.

| file | what it does |
|---|---|
| `library.json` | 43 Wikimedia Commons files, public domain or CC0: chip dies, patent drawings, architecture, Bauhaus, Japanese prints and travel posters, botanical plates, William Morris textiles, cars, watches, aircraft, maps, Apollo/Saturn V, typography. Each has its file page, licence, creator and the tags it gets |
| `seed.py` | adds them to a Kist instance under short names (`--dry-run` lists them; standard library only) |
| `dress.py` | makes the seeded library look lived-in: folders, ratings, stacks, a board, a smart folder |
| `capture.py` | takes the screenshots in `docs/assets/screenshots/` (Playwright) |
| `record.py` | records the silent demo loop: `docs/assets/demo/demo.gif`, plus a `demo.mp4` kept outside the repository for the GitHub release (Playwright + ffmpeg) |
| `CREDITS.md` | every image the committed screenshots, loop and social card show, with creator, licence and Commons page |

## Make a demo instance

Use a throwaway install, never your real library:

```bash
cp -r kist kist-demo && cd kist-demo
cp .env.example .env    # new KIST_TOKEN, KIST_PORT=8090, COMPOSE_PROJECT_NAME=kistdemo
                        # and KIST_USER_AGENT="kist-demo (your contact)"; see below
mkdir library && sudo chown 1000:1000 library
docker compose up -d --build
export KIST_URL=http://localhost:8090 KIST_TOKEN=...
KIST_SEED_CONTACT=you@example.com python docs/demo/seed.py
python docs/demo/dress.py     # once; prints the item id for KIST_SHOT_ITEM
```

Wait until the worker has made every preview and palette and the AI service
has embedded and read every item (`/api/ai/status`: nothing pending) before
taking pictures. Wikimedia refuses requests without a descriptive User-Agent
(its [policy](https://meta.wikimedia.org/wiki/User-Agent_policy)): `seed.py`
sends one with `KIST_SEED_CONTACT` and pauses 2 s between downloads, and
`KIST_USER_AGENT` in `.env` does the same for Kist's own link-rot check of
the Commons pages (without it every item soon shows its source as broken).

Then, from the repository root:

```bash
export KIST_URL=http://localhost:8090 KIST_TOKEN=...
KIST_SHOT_ITEM=<Great Wave item id> \
  uv run --no-project --with playwright --with pillow python docs/demo/capture.py
KIST_DEMO_SWATCH=4 KIST_DEMO_BOARD="Mid-century mood" \
  uv run --no-project --with playwright python docs/demo/record.py   # needs ffmpeg
```

`record.py` writes the GIF into the repository and the MP4 to
`../kist-release-assets/demo.mp4` (or `$KIST_DEMO_MP4`), outside it: attach
the MP4 to the GitHub release; never commit it. Look at every picture and a
contact sheet of the loop (`ffmpeg -i demo.mp4 -vf fps=1,scale=480:-1,tile=5x4
-frames:v 1 sheet.png`) before committing.

`KIST_SHOT_FILTER` (e.g. `any=Architecture,Posters`) limits every shot and the
recording to some tags, for a library that holds anything else.

## Licences

"Public domain" on Commons can mean public domain in the United States only;
each entry's `page` has the details. Check again before a release. Credit is
not required for these files, but [CREDITS.md](CREDITS.md) lists them and the
docs site's footer credits Wikimedia Commons anyway.
