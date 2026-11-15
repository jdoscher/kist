"""Make the seeded demo library look lived-in, for screenshots and the demo loop.

Run after docs/demo/seed.py, on the same throwaway instance:

    KIST_URL=http://localhost:8082 KIST_TOKEN=... python docs/demo/dress.py

It adds folders (Reference/..., Projects/...), ratings, a few extra tags, a stack
(two impressions of Hokusai's Red Fuji), a "Water and weather" board and a
"Five stars" smart folder, and spreads the items' added dates so the
newest-first grid opens on the Great Wave, Van Gogh's Wheat Field with
Cypresses and Vermeer's Young Woman with a Water Pitcher. Standard
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

# keywords some files carry in their metadata that aren't useful tags
JUNK_TAGS = {"Front", "Overall"}  # the Met's view labels
# tag -> folder under Reference
TAG_FOLDERS = {"Japanese prints": "Japanese prints", "Impressionism": "Impressionism and after",
               "Post-Impressionism": "Impressionism and after", "Dutch Golden Age": "Old masters",
               "Old masters": "Old masters", "Baroque": "Old masters", "Neoclassicism": "Old masters",
               "Romanticism": "Old masters", "Realism": "Old masters", "American painting": "American painting",
               "Portraits": "Portraits"}
WATER = ["Hokusai, The Great Wave", "Hokusai, Storm below Mount Fuji", "Hiroshige, Sudden Shower over Shin-Ōhashi",
         "Turner, Venice from the Salute", "Winslow Homer, The Gulf Stream", "Thomas Cole, The Oxbow"]
COLOUR_STUDIES = ["Hokusai, The Great Wave", "Van Gogh, Irises", "Hokusai, Red Fuji", "Seurat, Circus Sideshow"]
RATINGS = {"Hokusai, The Great Wave": 5, "Van Gogh, Wheat Field with Cypresses": 5, "Vermeer, Young Woman with a Water Pitcher": 5,
           "Bruegel, The Harvesters": 5, "Hokusai, Red Fuji": 4, "Van Gogh, Irises": 5, "Sargent, Madame X": 4,
           "El Greco, View of Toledo": 4, "Hiroshige, Sudden Shower over Shin-Ōhashi": 5, "Seurat, Circus Sideshow": 4,
           "Rembrandt, Aristotle with a Bust of Homer": 4, "Thomas Cole, The Oxbow": 3, "Degas, The Dance Class": 4,
           "Turner, Venice from the Salute": 3, "Cézanne, The Card Players": 3}
FAVOURITES = ["Hokusai, The Great Wave", "Van Gogh, Wheat Field with Cypresses",
              "Vermeer, Young Woman with a Water Pitcher", "Bruegel, The Harvesters"]
# two impressions of the same print: one stack
STACKS = [("Hokusai, Red Fuji", "Hokusai, Red Fuji (second impression)")]
BOARD_NAME = "Water and weather"
BOARD = ["Hokusai, The Great Wave", "Hokusai, Storm below Mount Fuji", "Hiroshige, Sudden Shower over Shin-Ōhashi",
         "Hiroshige, Sudden Shower at Shōno", "Turner, Venice from the Salute", "Winslow Homer, The Gulf Stream",
         "Thomas Cole, The Oxbow", "Manet, Boating", "El Greco, View of Toledo"]
# newest first: the grid opens on these
FIRST = ["Hokusai, The Great Wave", "Van Gogh, Wheat Field with Cypresses", "Vermeer, Young Woman with a Water Pitcher",
         "Bruegel, The Harvesters", "Hokusai, Red Fuji", "Sargent, Madame X", "Van Gogh, Irises",
         "Hiroshige, Sudden Shower over Shin-Ōhashi", "El Greco, View of Toledo", "Seurat, Circus Sideshow",
         "Rembrandt, Aristotle with a Bust of Homer", "Degas, The Dance Class", "Turner, Venice from the Salute",
         "Goya, Manuel Osorio (the Red Boy)", "Hiroshige, Evening Snow at Kanbara", "Thomas Cole, The Oxbow",
         "Cézanne, Still Life with Apples and Primroses", "Velázquez, Juan de Pareja", "Winslow Homer, The Gulf Stream",
         "Hokusai, Storm below Mount Fuji"]

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
            # uploads lose the comma in "Artist, Title" (safe upload names); put the short name back
            want = name + "." + it["filename"].rsplit(".", 1)[-1]
            if it["filename"] != want:
                api("PATCH", f"/api/items/{it['id']}", {"filename": want})
            if junk := [t for t in it["tags"] if t in JUNK_TAGS]:
                api("PATCH", f"/api/items/{it['id']}/tags", {"add": [], "remove": junk})
    api("DELETE", "/api/tags/unused")  # the junk keywords, now on nothing
    missing = [i["name"] for i in lib if i["name"] not in ids]
    if missing:
        sys.exit(f"not seeded yet (run seed.py first): {missing}")

    def folder(name: str, parent: str | None = None, description: str = "") -> str:
        body = {"name": name, "description": description} | ({"parent_id": parent} if parent else {})
        return api("POST", "/api/folders", body)["id"]

    ref = folder("Reference", None, "Things to look at again")
    sub = {n: folder(n, ref) for n in sorted(set(TAG_FOLDERS.values()))}
    projects = folder("Projects")
    water, colour = folder("Water and weather", projects), folder("Colour studies", projects)
    for it in items:
        fs = sorted({sub[TAG_FOLDERS[t]] for t in it["tags"] if t in TAG_FOLDERS})
        name = by_page.get((it.get("provenance") or {}).get("page_url") or it.get("source_url"))
        if name in WATER:
            fs.append(water)
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
    api("POST", "/api/boards", {"name": BOARD_NAME, "layout": "masonry",
                                        "item_ids": [ids[n] for n in BOARD]})
    # the board keeps the new default background: the theme's own canvas
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
