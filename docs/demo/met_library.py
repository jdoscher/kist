"""Build docs/demo/library.json from The Met's Open Access API (CC0 works only).

Run from the repo root: python docs/demo/met_library.py  (standard library only).
Polite: one request a second, descriptive User-Agent. It also writes
docs/demo/CREDITS.md; `--credits-only` rewrites that from library.json alone.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

UA = {"User-Agent": "kist-demo (https://github.com/jdoscher/kist)"}
OUT = Path(__file__).resolve().parent / "library.json"
CREDITS = Path(__file__).resolve().parent / "CREDITS.md"

# (Met object ID, short name shown in Kist, tags)
PICKS = [
    (45434, "Hokusai, The Great Wave", ["Japanese prints", "Mount Fuji", "Water", "Blue"]),
    (55736, "Hokusai, Red Fuji", ["Japanese prints", "Mount Fuji", "Landscapes"]),
    (36490, "Hokusai, Red Fuji (second impression)", ["Japanese prints", "Mount Fuji", "Landscapes"]),
    (36492, "Hokusai, Storm below Mount Fuji", ["Japanese prints", "Mount Fuji", "Weather"]),
    (54868, "Hokusai, Reflection in Lake at Misaka", ["Japanese prints", "Mount Fuji", "Water"]),
    (55433, "Hiroshige, Sudden Shower over Shin-Ōhashi", ["Japanese prints", "Weather", "Water"]),
    (56915, "Hiroshige, Evening Snow at Kanbara", ["Japanese prints", "Weather", "Snow"]),
    (36521, "Hiroshige, Sudden Shower at Shōno", ["Japanese prints", "Weather"]),
    (56689, "Hiroshige, Kinryūsan Temple at Asakusa", ["Japanese prints", "Snow", "Architecture"]),
    (436535, "Van Gogh, Wheat Field with Cypresses", ["Post-Impressionism", "Landscapes"]),
    (436528, "Van Gogh, Irises", ["Post-Impressionism", "Flowers", "Blue"]),
    (437980, "Van Gogh, Cypresses", ["Post-Impressionism", "Landscapes"]),
    (436532, "Van Gogh, Self-Portrait with a Straw Hat", ["Post-Impressionism", "Portraits"]),
    (437658, "Seurat, Study for La Grande Jatte", ["Post-Impressionism", "Water", "Landscapes"]),
    (437654, "Seurat, Circus Sideshow", ["Post-Impressionism", "Night"]),
    (435868, "Cézanne, The Card Players", ["Post-Impressionism", "Figures"]),
    (435882, "Cézanne, Still Life with Apples and Primroses", ["Post-Impressionism", "Still life", "Flowers"]),
    (438817, "Degas, The Dance Class", ["Impressionism", "Figures"]),
    (436947, "Manet, Boating", ["Impressionism", "Water", "Figures"]),
    (438010, "Renoir, Nini in the Garden", ["Impressionism", "Portraits", "Flowers"]),
    (12127, "Sargent, Madame X", ["Portraits", "American painting"]),
    (437881, "Vermeer, Young Woman with a Water Pitcher", ["Dutch Golden Age", "Figures", "Blue"]),
    (437394, "Rembrandt, Aristotle with a Bust of Homer", ["Dutch Golden Age", "Portraits"]),
    (435809, "Bruegel, The Harvesters", ["Old masters", "Landscapes"]),
    (436575, "El Greco, View of Toledo", ["Old masters", "Landscapes", "Weather"]),
    (435844, "Caravaggio, The Musicians", ["Old masters", "Baroque", "Figures"]),
    (436838, "Georges de La Tour, The Fortune-Teller", ["Old masters", "Baroque", "Figures"]),
    (437869, "Velázquez, Juan de Pareja", ["Old masters", "Baroque", "Portraits"]),
    (436545, "Goya, Manuel Osorio (the Red Boy)", ["Old masters", "Portraits"]),
    (436105, "David, The Death of Socrates", ["Neoclassicism", "Figures"]),
    (459106, "Ingres, Princesse de Broglie", ["Neoclassicism", "Portraits", "Blue"]),
    (437853, "Turner, Venice from the Salute", ["Romanticism", "Water", "Architecture"]),
    (435702, "Rosa Bonheur, The Horse Fair", ["Realism", "Animals"]),
    (10497, "Thomas Cole, The Oxbow", ["American painting", "Hudson River School", "Landscapes", "Weather"]),
    (10154, "Bierstadt, The Rocky Mountains, Lander's Peak", ["American painting", "Hudson River School", "Landscapes"]),
    (11122, "Winslow Homer, The Gulf Stream", ["American painting", "Water", "Weather"]),
    (11417, "Leutze, Washington Crossing the Delaware", ["American painting", "Water", "Figures"]),
]


def write_credits() -> None:
    items = json.loads(OUT.read_text())["items"]
    rows = "\n".join(
        f"| {i['name']} | [{i['title'].replace('|', '/')}]({i['page']}) | {i['creator']} | {i['date']} | "
        f"{i['credit']} | {i['accession']} |"
        for i in items
    )
    CREDITS.write_text(f"""# Demo image credits

Every image in Kist's committed screenshots (`docs/assets/screenshots/`), the
demo loop (`docs/assets/demo/demo.gif`, and the `demo.mp4` attached to GitHub
releases) and the social card (`docs/brand/social-preview.png`) comes from
this list: the demo library in `library.json`. No other images appear in them.

All {len(items)} works are in the public domain and come from **The Metropolitan
Museum of Art's Open Access** programme, which releases images of public-domain
works under [Creative Commons Zero (CC0)](https://creativecommons.org/publicdomain/zero/1.0/)
([the Met's policy](https://www.metmuseum.org/policies/image-resources)).
No attribution is required; the Met asks for a credit where possible, so it
is given here: *Images courtesy of The Metropolitan Museum of Art, Open
Access.* The Met does not endorse Kist.

Kist shows each under its short name (`name` in `library.json`); the info
panel carries the artist, the Met object page and the licence.

| Name in Kist | Work (Met object page) | Artist | Date | Credit line | Accession |
|---|---|---|---|---|---|
{rows}
""")
    print("wrote", CREDITS)


if "--credits-only" in sys.argv:
    write_credits()
    sys.exit(0)


def get(i):
    u = f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{i}"
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30) as r:
        return json.load(r)


items = []
for oid, name, tags in PICKS:
    o = get(oid)
    time.sleep(1)
    if not (o.get("isPublicDomain") and o.get("primaryImage")):
        sys.exit(f"{oid} {name}: not Open Access")
    items.append({
        "met_id": oid,
        "name": name,
        "title": o["title"],
        "creator": o.get("artistDisplayName") or o.get("culture") or "Unknown",
        "date": o.get("objectDate", ""),
        "medium": o.get("medium", ""),
        "credit": o.get("creditLine", ""),
        "accession": o.get("accessionNumber", ""),
        "page": o["objectURL"],
        "image": o["primaryImage"],
        "license": "CC0 (The Met Open Access)",
        "tags": tags,
    })
    print(oid, name, "ok", flush=True)

OUT.write_text(json.dumps({
    "about": "Demo library for Kist's screenshots, demo loop and social card. Every work is from The "
             "Metropolitan Museum of Art's Open Access collection: public domain, images released under "
             "CC0 (https://www.metmuseum.org/policies/image-resources). Checked through the Met's "
             "collection API when this list was made (" + time.strftime("%Y-%m-%d") + ").",
    "items": items,
}, ensure_ascii=False, indent=1) + "\n")
print(len(items), "items ->", OUT)
write_credits()
