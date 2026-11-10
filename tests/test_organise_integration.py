"""Milestone 2 with the real models (opt-in, like test_ai_integration):

    KIST_ML_INTEGRATION=1 KIST_ML_CACHE=~/.cache/kist-ml \\
    KIST_ORGANISE_SEED=/path/to/photos pytest tests/test_organise_integration.py -s

KIST_ORGANISE_SEED is a folder of JPEGs named <category>_<n>.jpg (several
categories, ten or so each) and, optionally, the face photos
apollo11_crew.jpg, aldrin.jpg, armstrong.jpg and collins.jpg (public-domain
NASA portraits on Wikimedia Commons). Seven pictures per category are
tagged with it, the rest are held out; the test reports how well tag
suggestions, zero-shot and the clustering recover the categories, and that
YuNet + SFace put each astronaut's portrait with his face in the crew photo.
"""

import os
from collections import Counter
from pathlib import Path

import httpx
import pytest

from kist.config import get_settings
from kist.db import SessionLocal
from kist.services import ai, collections, faces, suggestions

SEED = os.environ.get("KIST_ORGANISE_SEED", "")
pytestmark = pytest.mark.skipif(
    os.environ.get("KIST_ML_INTEGRATION") != "1" or not SEED,
    reason="set KIST_ML_INTEGRATION=1 and KIST_ORGANISE_SEED to run the real models",
)
TAGGED = 7


@pytest.fixture
def real_ml(clean, monkeypatch):
    from fastapi.testclient import TestClient

    from kist.ml.server import app as ml_app

    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ml_url", "http://ml.test")
    monkeypatch.setattr(settings, "faces", True)
    monkeypatch.setenv("KIST_ML_PRELOAD", "")
    monkeypatch.setenv("KIST_FACES", "1")
    with TestClient(ml_app) as ml:
        def forward(request: httpx.Request) -> httpx.Response:
            r = ml.request(request.method, request.url.path, content=request.content,
                           headers=dict(request.headers))
            return httpx.Response(r.status_code, headers=r.headers, content=r.content)

        ai.set_transport(httpx.MockTransport(forward))
        yield ml
    ai.set_transport(None)


def test_real_organise(client, upload, real_ml):
    from test_ai import drain

    seed = Path(SEED)
    photos = sorted(p for p in seed.glob("*_*.jpg") if p.stem.split("_")[0] != "apollo11")
    by_cat: dict[str, list[Path]] = {}
    for p in photos:
        by_cat.setdefault(p.stem.split("_")[0], []).append(p)
    held: dict[str, str] = {}
    cat_of: dict[str, str] = {}
    for cat, paths in sorted(by_cat.items()):
        for i, p in enumerate(paths):
            tags = cat if i < TAGGED else ""
            item = upload(p.name, data=p.read_bytes(), mime="image/jpeg", tags=tags)["item"]
            cat_of[item["id"]] = cat
            if i >= TAGGED:
                held[item["id"]] = cat
    face_files = [seed / n for n in ("apollo11_crew.jpg", "aldrin.jpg", "armstrong.jpg",
                                     "collins.jpg") if (seed / n).is_file()]
    face_items = {p.stem: upload(p.name, data=p.read_bytes(), mime="image/jpeg")["item"]["id"]
                  for p in face_files}
    drain()

    # tag suggestions for the held-out pictures
    top1 = top3 = 0
    whys = Counter()
    for item_id, cat in held.items():
        got = client.get(f"/api/items/{item_id}/tag-suggestions").json()
        names = [s["tag"] for s in got]
        whys.update(s["why"] for s in got[:1])
        top1 += bool(names[:1] == [cat])
        top3 += cat in names[:3]
    # zero-shot alone (no neighbours) over the category names
    model = ai.active_model()
    with SessionLocal() as s:
        names, m = suggestions.tag_matrix(s, model)
        zs_ok = 0
        for item_id, cat in held.items():
            vec = suggestions._item_vector(s, item_id, model)
            zs = suggestions.zero_shot(names, m, vec, top=1, strict=False)
            zs_ok += bool(zs and zs[0][0] == cat)
    n = len(held)
    print(f"\ntag suggestions on {n} held-out pictures: top-1 {top1}/{n}, top-3 {top3}/{n}; "
          f"first suggestion's why: {dict(whys)}; zero-shot alone top-1 {zs_ok}/{n}")
    assert top3 >= 0.8 * n and zs_ok >= 0.6 * n

    # suggested collections over everything
    with SessionLocal() as s:
        data = collections.compute(s)
    print(f"clustering: {data['items']} items, k={data['k']}, silhouette {data['silhouette']}, "
          f"{len(data['clusters'])} clusters of 8+")
    purities = []
    coarse = {"cat": "pet", "dog": "pet"}  # CLIP rightly finds pets alike
    for c in data["clusters"]:
        cats = Counter(coarse.get(cat_of.get(i, "face"), cat_of.get(i, "face"))
                       for i in c["item_ids"])
        top_cat, top_n = cats.most_common(1)[0]
        purities.append(top_n / c["size"])
        print(f"  {c['size']:3d} items, label {c['label_suggestion']!r} ({c['label_source']}), "
              f"cohesion {c['cohesion']}, purity {top_n / c['size']:.2f} ({dict(cats)})")
    assert data["clusters"] and min(purities) >= 0.75

    # faces: each portrait's face matches one crew face, and clustering names them
    if face_items:
        with SessionLocal() as s:
            faces.cluster_unassigned(s)
        people = client.get("/api/people").json()
        crew = client.get(f"/api/items/{face_items['apollo11_crew']}/faces").json()
        print(f"faces: crew photo {len(crew)} faces; people {len(people)}: "
              + ", ".join(f"{p['face_count']} faces" for p in people))
        assert len(crew) == 3
        portraits = {k: client.get(f"/api/items/{v}/faces").json() for k, v in face_items.items()
                     if k != "apollo11_crew"}
        assert all(len(f) == 1 for f in portraits.values())
        crew_people = {f["person_id"] for f in crew}
        portrait_people = {f[0]["person_id"] for f in portraits.values()}
        assert None not in portrait_people and portrait_people <= crew_people
        assert len(portrait_people) == len(portraits)
