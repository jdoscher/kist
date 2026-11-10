"""Make the seeded demo library look lived-in, for screenshots and the demo loop.

Run after docs/demo/seed.py, on the same throwaway instance:

    KIST_URL=http://localhost:8082 KIST_TOKEN=... python docs/demo/dress.py

It adds folders (Reference/..., Projects/...), ratings, a few extra tags, two
stacks, a "Mid-century mood" board on a dark canvas and a "Five stars" smart
folder, and spreads the items' added dates so the newest-first grid opens on
the Great Wave, the Olivetti poster and a chip die. It also drops camera and
archive keywords that some Commons files carry in their metadata. Standard
library only. Run it once: it creates new folders, stacks and a board each
time.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent

# keywords embedded in some Commons files that aren't useful tags
JUNK_TAGS = {"Sony-Alpha 6000 ILCE-6000", "Walimex 12mm F2.0", "GLAM on Tour Weimar", "1833", "Icones",
             "Bauhausmuseum", "Pictorial works", "Weimar", "Botany", "France"}
# tag -> folder under Reference
TAG_FOLDERS = {"Architecture": "Architecture", "Bauhaus": "Architecture", "Cars": "Design objects",
               "Watches": "Design objects", "Cameras": "Design objects", "Furniture": "Design objects",
               "Patent drawings": "Design objects", "Posters": "Print and poster", "Prints": "Print and poster",
               "Typography": "Print and poster", "Textiles": "Print and poster", "Botanical": "Print and poster",
               "Maps": "Print and poster", "Aircraft": "Flight and space", "Space": "Flight and space",
               "Chip dies": "Hardware"}
MID_CENTURY = ["Eames LCW chair", "Eames House, Case Study House 8", "Case Study House No. 10, construction",
               "Porsche 356 Super coupe, 1958", "Leica M9", "Olivetti M1 poster, 1912"]
COLOUR_STUDIES = ["Hokusai, The Great Wave", "William Morris, Peacock and Dragon",
                  "Japan travel poster, 1930s (2)", "Redouté, Anemone (1827)"]
RATINGS = {"Hokusai, The Great Wave": 5, "Hitachi HD61700 die": 5, "Fallingwater": 5, "Eames LCW chair": 4,
           "William Morris, Peacock and Dragon": 5, "Porsche 356 Super coupe, 1958": 4,
           "Japan travel poster, 1930s (1)": 4, "Bodoni, Manuale Tipografico": 4, "Intel 4004 chip layout, 1971": 5,
           "Saturn V diagram": 3, "Chrysler Building, crown": 4, "Leica M9": 3, "Redouté, Anemone (1827)": 4,
           "Olivetti M1 poster, 1912": 5, "Ordnance Survey, Barnsbury, 1870s": 3}
FAVOURITES = ["Hokusai, The Great Wave", "Olivetti M1 poster, 1912", "Intel 4004 chip layout, 1971", "Fallingwater"]
STACKS = [("Porsche 356 Super coupe, 1958", "Porsche 356 Super coupe, 1958, rear"),
          ("Hitachi HD61700 die", "Hitachi HD61700 die, code ROM")]
BOARD = ["Eames LCW chair", "Eames House, Case Study House 8", "Case Study House No. 10, construction",
         "Porsche 356 Super coupe, 1958", "Leica M9", "Olivetti M1 poster, 1912", "Fallingwater",
         "Schlemmer, Bauhaus poster", "Bauhaus Dessau, main building"]
# newest first: the grid opens on these
FIRST = ["Hokusai, The Great Wave", "Olivetti M1 poster, 1912", "Hitachi HD61700 die", "Fallingwater",
         "Japan travel poster, 1930s (1)", "William Morris, Peacock and Dragon", "Porsche 356 Super coupe, 1958",
         "Intel 4004 chip layout, 1971", "Redouté, Anemone (1827)", "Chrysler Building, crown", "Leica M9",
         "Saturn V diagram", "Eames LCW chair", "Bodoni, Manuale Tipografico", "Japan travel poster, 1930s (2)",
         "Saturn V 500F at the VAB", "Eames House, Case Study House 8", "Schlemmer, Bauhaus poster",
         "Lockheed Constellation N422NA", "Hokusai, Fine Wind, Clear Morning"]


def main() -> int:
    url = os.environ["KIST_URL"].rstrip("/")
    token = os.environ["KIST_TOKEN"]

    def api(method: str, path: str, body: dict | None = None):
        req = urllib.request.Request(
            url + path, method=method, data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            sys.exit(f"{method} {path}: {e.code} {e.read()[:300]!r}")
        return json.loads(raw) if raw else None

    lib = json.loads((HERE / "library.json").read_text())["items"]
    by_page = {i["page"]: i["name"] for i in lib}
    items = api("GET", "/api/items?limit=500")["items"]
    ids: dict[str, str] = {}
    for it in items:
        name = by_page.get((it.get("provenance") or {}).get("page_url") or it.get("source_url"))
        if name:
            ids[name] = it["id"]
            if junk := [t for t in it["tags"] if t in JUNK_TAGS]:
                api("PATCH", f"/api/items/{it['id']}/tags", {"add": [], "remove": junk})
    missing = [i["name"] for i in lib if i["name"] not in ids]
    if missing:
        sys.exit(f"not seeded yet (run seed.py first): {missing}")

    def folder(name: str, parent: str | None = None, description: str = "") -> str:
        body = {"name": name, "description": description} | ({"parent_id": parent} if parent else {})
        return api("POST", "/api/folders", body)["id"]

    ref = folder("Reference", None, "Things to look at again")
    sub = {n: folder(n, ref) for n in sorted(set(TAG_FOLDERS.values()))}
    projects = folder("Projects")
    mid, colour = folder("Mid-century picks", projects), folder("Colour studies", projects)
    for it in items:
        fs = sorted({sub[TAG_FOLDERS[t]] for t in it["tags"] if t in TAG_FOLDERS})
        name = by_page.get((it.get("provenance") or {}).get("page_url") or it.get("source_url"))
        if name in MID_CENTURY:
            fs.append(mid)
        if name in COLOUR_STUDIES:
            fs.append(colour)
        if fs:
            api("PATCH", f"/api/items/{it['id']}/folders", {"add": fs, "remove": []})
    for name, stars in RATINGS.items():
        api("PATCH", f"/api/items/{ids[name]}", {"rating": stars})
    for name in FAVOURITES:
        api("PATCH", f"/api/items/{ids[name]}/tags", {"add": ["Favourites"], "remove": []})
    for cover, other in STACKS:
        api("POST", "/api/stacks", {"item_ids": [ids[cover], ids[other]], "cover": ids[cover]})
    board = api("POST", "/api/boards", {"name": "Mid-century mood", "layout": "masonry",
                                        "item_ids": [ids[n] for n in BOARD]})
    api("PATCH", f"/api/boards/{board['id']}", {"canvas": {"bg": "#1c1b19", "grid": 0}})
    api("POST", "/api/smart-folders", {"name": "Five stars", "query": {"rating": 5}})
    order = FIRST + [n for n in ids if n not in FIRST]
    start = datetime.now(UTC).replace(microsecond=0)
    for n, name in enumerate(order):
        when = start - timedelta(hours=7 * n + n * n % 5)
        api("PATCH", f"/api/items/{ids[name]}", {"created_at": when.isoformat()})
    print(f"dressed {len(ids)} items: {len(sub) + 4} folders, {len(RATINGS)} ratings, "
          f"{len(STACKS)} stacks, 1 board, 1 smart folder")
    print("the phone item sheet shots use", ids["Hokusai, The Great Wave"], "(KIST_SHOT_ITEM)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
