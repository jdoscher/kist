# Demo library

Kist's README hero, the docs site's screenshots, the demo loop and the social
card should show a library anyone may see, not a personal one. This folder
makes and records it.

| file | what it does |
|---|---|
| `library.json` | 37 public-domain paintings and prints from The Metropolitan Museum of Art's Open Access collection (CC0): Hokusai and Hiroshige, Van Gogh, Seurat, Cézanne, Degas, Vermeer, Rembrandt, Bruegel, El Greco, Velázquez, Goya, Turner, Sargent, the Hudson River School and more. Each has its Met object page, artist, date, credit line, image URL and the tags it gets |
| `met_library.py` | rebuilds `library.json` (and `CREDITS.md`) from the Met's collection API, checking every work is Open Access |
| `seed.py` | adds them to a Kist instance under short names (`--dry-run` lists them; standard library only) |
| `dress.py` | makes the seeded library look lived-in: folders, ratings, stacks, a board, a smart folder |
| `capture.py` | takes the screenshots in `docs/assets/screenshots/` (Playwright) |
| `record.py` | records the silent demo loop: `docs/assets/demo/demo.gif`, plus a `demo.mp4` kept outside the repository for the GitHub release (Playwright + ffmpeg) |
| `CREDITS.md` | every image the committed screenshots, loop and social card show, with artist, date, credit line and Met object page |

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
taking pictures. `seed.py` names itself in its User-Agent (with
`KIST_SEED_CONTACT`), downloads the Met's full-size images one at a time and
pauses 2 s between them; set `KIST_USER_AGENT` in `.env` the same way for
Kist's own requests.

Then, from the repository root:

```bash
export KIST_URL=http://localhost:8090 KIST_TOKEN=...
KIST_SHOT_ITEM=<Great Wave item id> \
  uv run --no-project --with playwright --with pillow python docs/demo/capture.py
KIST_DEMO_SWATCH=4 KIST_DEMO_BOARD="Water and weather" \
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

Every work is public domain, and The Met releases its Open Access images
under [CC0](https://creativecommons.org/publicdomain/zero/1.0/)
([policy](https://www.metmuseum.org/policies/image-resources)).
`met_library.py` refuses any object the API doesn't mark `isPublicDomain`;
re-run it before a release. Credit is not required, but the Met asks for
one, so [CREDITS.md](CREDITS.md) lists every work and the README and the docs
site's footer credit the Met. Don't imply the Met endorses Kist.
