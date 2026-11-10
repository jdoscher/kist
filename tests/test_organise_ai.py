"""Milestone 2 with a fake ml service: tag suggestions, suggested
collections, faces and people. The fake's image vector is the mean colour
(test_ai.FakeMl); text vectors map colour words; /faces reports one face
per image whose identity is its dominant colour channel.
"""

import json

import httpx
import numpy as np
import pytest
import sqlalchemy as sa
from conftest import png_bytes
from test_ai import COLOURS, DIM, FakeMl, colour_vector, drain
from test_tokens import as_token, make_token, mcp_result

from kist import cli
from kist.config import get_settings
from kist.db import SessionLocal
from kist.models import Face, Person
from kist.services import ai, collections, faces, suggestions


class FakeOrganiseMl(FakeMl):
    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/embed/text":
            self.calls.append(path)
            text = json.loads(request.content)["text"].lower()
            rgb = next((v for k, v in COLOURS.items() if k in text.split()), (0.3, 0.3, 0.3))
            return httpx.Response(200, json={"model": self.model, "dim": DIM,
                                             "vector": colour_vector(rgb)})
        if path == "/faces":
            self.calls.append(path)
            import io

            from PIL import Image

            im = Image.open(io.BytesIO(request.content)).convert("RGB")
            r, g, b = (float(v) for v in np.asarray(im.resize((4, 4)), dtype=np.float32)
                       .reshape(-1, 3).mean(0))
            who = max(range(3), key=lambda i: (r, g, b)[i])
            emb = [0.0] * 128
            emb[who] = 1.0
            emb[3 + who] = 0.1 * (r % 7) / 7  # a little noise per picture
            w, h = im.size
            return httpx.Response(200, json={
                "model": "fake-faces", "width": w, "height": h,
                "faces": [{"box": [w * 0.25, h * 0.25, w * 0.5, h * 0.5],
                           "landmarks": [[w * 0.4, h * 0.4]] * 5, "score": 0.95,
                           "embedding": emb}],
            })
        return super().__call__(request)


@pytest.fixture
def ml(clean, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ml_url", "http://ml.test")
    monkeypatch.setattr(settings, "ai_window", "")
    fake = FakeOrganiseMl()
    ai.set_transport(httpx.MockTransport(fake))
    yield fake
    ai.set_transport(None)


@pytest.fixture
def faces_on(ml, monkeypatch):
    monkeypatch.setattr(get_settings(), "faces", True)
    return ml


def solid(rgb):
    return png_bytes(color=rgb, size=(320, 240))


# ---------- tag suggestions ----------


def test_tag_suggestions_knn_zero_shot_and_feedback(client, upload, ml):
    for i in range(3):
        upload(f"r{i}.png", data=solid((230, 10 + i, 10)), tags="crimson,red")
    upload("g.png", data=solid((10, 230, 10)), tags="green")
    upload("b.png", data=solid((10, 10, 230)), tags="blue")
    target = upload("new.png", data=solid((235, 12, 12)))["item"]
    drain()
    got = client.get(f"/api/items/{target['id']}/tag-suggestions").json()
    by = {s["tag"]: s for s in got}
    assert by["crimson"]["why"] == "similar items" and by["crimson"]["score"] > 0.9
    assert by["red"]["score"] > 0.9
    assert "green" not in by and "blue" not in by
    # a blue item: kNN has nothing close, zero-shot finds the blue tag
    other = upload("navy.png", data=solid((5, 5, 200)))["item"]
    drain()
    got = {s["tag"]: s for s in
           client.get(f"/api/items/{other['id']}/tag-suggestions").json()}
    assert got["blue"]["why"] in ("matches the image", "similar items")
    # rejected: not suggested for that item again (and the item's own tags never)
    res = client.post(f"/api/items/{target['id']}/tag-suggestions/feedback",
                      json={"tag": "crimson", "accepted": False})
    assert res.status_code == 200
    again = {s["tag"] for s in client.get(f"/api/items/{target['id']}/tag-suggestions").json()}
    assert "crimson" not in again and "red" in again
    client.patch(f"/api/items/{target['id']}/tags", json={"add": ["red"]})
    again = {s["tag"] for s in client.get(f"/api/items/{target['id']}/tag-suggestions").json()}
    assert "red" not in again
    # tag vectors are cached per model
    text_calls = ml.calls.count("/embed/text")
    client.get(f"/api/items/{target['id']}/tag-suggestions")
    assert ml.calls.count("/embed/text") == text_calls
    # feedback survives a rebuild via its sidecar
    assert cli.verify() == 0
    assert cli.rebuild_index(wipe=True) == 0
    again = {s["tag"] for s in client.get(f"/api/items/{target['id']}/tag-suggestions").json()}
    assert "crimson" not in again
    # MCP
    out = mcp_result(client, "suggest_tags", {"id": other["id"]})
    assert not out.get("isError")


def test_similar_items_skip_bookkeeping_and_contradicted_tags(client, upload, ml):
    """Neighbours vote with their tags, but not with credit:/license: tags,
    and not with a tag whose name the picture clearly doesn't match the way
    its own items do (CLIP puts dogs next to tagged cats)."""
    for i in range(4):
        upload(f"r{i}.png", data=solid((230, 10 + i, 10)),
               tags="red,client-acme,credit:Wikimedia")
    # close to the reds as a picture, but not red the way they are
    target = upload("magenta.png", data=solid((220, 0, 128)))["item"]
    drain()
    got = {s["tag"]: s for s in
           client.get(f"/api/items/{target['id']}/tag-suggestions").json()}
    assert got["client-acme"]["why"] == "similar items"
    assert "credit:Wikimedia" not in got
    assert got.get("red", {}).get("why") != "similar items"
    # a picture as red as they are isn't contradicted
    twin = upload("red.png", data=solid((232, 12, 10)))["item"]
    drain()
    with SessionLocal() as s:
        model = ai.active_model()
        names, m = suggestions.tag_matrix(s, model)
        vec = suggestions._item_vector(s, twin["id"], model)
        ids = [i["id"] for i in client.get("/api/items", params={"tags": "red"}).json()["items"]]
        voters = {"red": ids, "client-acme": ids}
        assert suggestions._text_contradicts(s, model, vec, voters, names, m) == set()
        vec = suggestions._item_vector(s, target["id"], model)
        assert suggestions._text_contradicts(s, model, vec, voters, names, m) == {"red"}


def test_zero_shot_scores_are_a_softmax():
    names = ["red", "green", "blue"]
    m = np.asarray([colour_vector(COLOURS[n]) for n in names], dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    out = suggestions.zero_shot(names, m, colour_vector((0.0, 0.0, 1.0)))
    assert out[0][0] == "blue" and out[0][1] > 0.99
    assert abs(sum(p for _, p in out) - 1.0) < 1e-5


def test_bookkeeping_tags_are_never_zero_shot_candidates():
    assert not suggestions.candidate_tag("credit:Wikimedia")
    assert not suggestions.candidate_tag("License:CC-BY-4.0")
    assert not suggestions.candidate_tag("source:Pinterest")
    assert suggestions.candidate_tag("brand:Omega") and suggestions.candidate_tag("dog")
    names = ["credit:blue", "blue"]
    m = np.asarray([colour_vector(COLOURS["blue"])] * 2, dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    assert [t for t, _ in suggestions.zero_shot(names, m, colour_vector((0.0, 0.0, 1.0)))] \
        == ["blue"]


def _unit_rows(rows):
    m = np.asarray(rows, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def test_zero_shot_needs_a_tag_that_stands_out():
    """CLIP-like numbers: every text sits ~0.2 from the image; a softmax
    alone would crown the best of a bad lot."""
    rng = np.random.default_rng(3)
    dim = 64
    img = rng.standard_normal(dim)
    img /= np.linalg.norm(img)

    def text(cos):  # a unit vector at cosine `cos` from the image
        r = rng.standard_normal(dim)
        r -= (r @ img) * img
        r /= np.linalg.norm(r)
        return cos * img + np.sqrt(1 - cos * cos) * r

    reference = _unit_rows([text(c) for c in rng.normal(0.19, 0.012, 48)])
    # a small vocabulary of near misses: nothing is suggested
    near = _unit_rows([text(0.215), text(0.21), text(0.205)])
    assert suggestions.zero_shot(["watch", "brand:Omega", "portrait"], near, img,
                                 reference=reference) == []
    # ...where the old softmax-only rule picked one
    assert suggestions.zero_shot(["watch", "brand:Omega", "portrait"], near, img,
                                 strict=False)[0][0] == "watch"
    # a tag that matches (well above the reference) is
    m = _unit_rows([text(0.29), text(0.215), text(0.2)])
    got = suggestions.zero_shot(["dog", "watch", "portrait"], m, img, reference=reference)
    assert [t for t, _ in got] == ["dog"]
    # without a reference: a z-score among 12+ candidates, else a stricter floor
    many = _unit_rows([text(0.29)] + [text(c) for c in rng.normal(0.19, 0.012, 20)])
    names = ["dog"] + [f"t{i}" for i in range(20)]
    assert [t for t, _ in suggestions.zero_shot(names, many, img)] == ["dog"]
    assert suggestions.zero_shot(["dog", "x"], _unit_rows([text(0.25), text(0.2)]), img) == []


def test_zero_shot_reference_is_embedded_once_and_kept(ml):
    model = ai.active_model()
    suggestions._reference_cache.clear()
    m = suggestions.reference_matrix(model)
    assert m.shape == (len(suggestions.REFERENCE_PROMPTS), DIM)
    calls = ml.calls.count("/embed/text")
    suggestions._reference_cache.clear()  # a new process: read from disk
    assert suggestions.reference_matrix(model) is not None
    assert ml.calls.count("/embed/text") == calls
    assert (get_settings().state_dir / suggestions.REFERENCE_FILE).is_file()


def test_suggestions_without_an_embedding_are_empty(client, upload, ml):
    item = upload("x.png")["item"]
    assert client.get(f"/api/items/{item['id']}/tag-suggestions").json() == []


def test_suggestions_only_use_visible_neighbours(client, upload, ml):
    a = client.post("/api/folders", json={"name": "A"}).json()
    b = client.post("/api/folders", json={"name": "B"}).json()
    upload("secret.png", data=solid((230, 10, 10)), tags="secret",
           folder_ids=json.dumps([b["id"]]))
    mine = upload("mine.png", data=solid((231, 10, 10)), folder_ids=json.dumps([a["id"]]))
    drain()
    # a folder-limited agent can't use suggestions at all (404 path), and an
    # unrestricted caller sees the neighbour's tag
    fam = as_token(client, make_token(client, "Fam", ["read"], folder_ids=[a["id"]])["token"])
    assert fam.get(f"/api/items/{mine['item']['id']}/tag-suggestions").status_code == 404
    got = {s["tag"] for s in client.get(
        f"/api/items/{mine['item']['id']}/tag-suggestions").json()}
    assert "secret" in got


# ---------- suggested collections ----------


def blobs(n_per, centres, noise, rng):
    xs, labels = [], []
    for c, centre in enumerate(centres):
        pts = centre + noise * rng.standard_normal((n_per, len(centre)))
        xs.append(pts)
        labels += [c] * n_per
    x = np.concatenate(xs).astype(np.float32)
    return x / np.linalg.norm(x, axis=1, keepdims=True), np.array(labels)


def test_kmeans_picks_k_by_silhouette_on_synthetic_clusters():
    rng = np.random.default_rng(1)
    centres = rng.standard_normal((4, 64))
    x, truth = blobs(60, centres, 0.15, rng)
    labels, _, k, sil = collections.cluster(x)
    assert k == 4 and sil > 0.5
    # every found cluster is pure
    for c in range(k):
        assert len(set(truth[labels == c])) == 1
    # pure noise has a poor silhouette whatever k
    noise = rng.standard_normal((200, 64)).astype(np.float32)
    noise /= np.linalg.norm(noise, axis=1, keepdims=True)
    assert collections.cluster(noise)[3] < 0.1


def test_suggested_collections_end_to_end(client, upload, ml):
    for i in range(9):
        upload(f"red{i}.png", data=solid((220 + i, 10, 10)), tags="red" if i < 6 else "")
    for i in range(9):
        upload(f"blue{i}.png", data=solid((10, 10, 220 + i)))
    for i in range(3):
        upload(f"green{i}.png", data=solid((10, 220 + i, 10)), tags="blue sky" if i else "")
    drain()
    data = client.get("/api/collections/suggested", params={"refresh": 1}).json()
    assert data["items"] == 21
    by_label = {c["label_suggestion"]: c for c in data["clusters"]}
    assert set(by_label) == {"red", "blue sky"}, data["clusters"]
    assert by_label["red"]["label_source"] == "tags" and by_label["red"]["size"] == 9
    # no blue item is tagged: its label comes from zero-shot over the tag
    # names, and only a tag that really matches ("red" doesn't) is used
    assert by_label["blue sky"]["label_source"] == "zero_shot"
    cached = json.loads((get_settings().state_dir / "collections.json").read_text())
    assert len(cached["clusters"]) == 2
    # accepting one files its items into a new folder, in one batch
    cid = by_label["blue sky"]["id"]
    res = client.post(f"/api/collections/suggested/{cid}/accept", json={"name": "Blues"})
    assert res.status_code == 201, res.text
    folder_id = res.json()["folder_id"]
    page = client.get("/api/items", params={"folder": folder_id}).json()
    assert page["total"] == 9
    left = client.get("/api/collections/suggested").json()["clusters"]
    assert [c["label_suggestion"] for c in left] == ["red"]
    undo = client.post(f"/api/batches/{res.json()['batch_id']}/undo").json()
    assert undo["undone"] == 9
    assert client.get("/api/items", params={"folder": folder_id}).json()["total"] == 0
    # smart-folder kind: a tag plus a smart folder
    res = client.post(f"/api/collections/suggested/{by_label['red']['id']}/accept",
                      json={"name": "Reds", "kind": "smart_folder"}).json()
    assert client.get("/api/items", params={"tags": "Reds"}).json()["total"] == 9
    assert res["smart_folder_id"]
    out = mcp_result(client, "suggested_collections")
    assert not out.get("isError")


def test_collection_labels_skip_bookkeeping_tags(client, upload, ml):
    """Every red item shares credit:Wikimedia, and the vocabulary is tiny:
    the label is what the items show or nothing, never the credit."""
    for i in range(9):
        upload(f"red{i}.png", data=solid((220 + i, 10, 10)),
               tags="credit:Wikimedia,red" if i < 3 else "credit:Wikimedia")
    for i in range(9):
        upload(f"blue{i}.png", data=solid((10, 10, 220 + i)), tags="license:CC-BY-4.0")
    drain()
    data = client.get("/api/collections/suggested", params={"refresh": 1}).json()
    labels = {c["label_suggestion"] for c in data["clusters"]}
    assert not any(lab and ":" in lab for lab in labels), data["clusters"]
    # the reds' label comes from zero-shot over "red"; the blues match no tag
    assert labels == {"red", None}, data["clusters"]


def test_members_must_agree_with_a_zero_shot_label():
    names = ["red", "green", "blue"]
    m = np.asarray([colour_vector(COLOURS[n]) for n in names], dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    reds = np.asarray([colour_vector((1.0, 0.05 * i, 0.0)) for i in range(6)])
    mixed = np.asarray([colour_vector((1.0, 0.0, 0.0))] * 2
                       + [colour_vector((0.0, 0.0, 1.0))] * 4)
    assert collections._members_agree("red", reds, names, m)
    assert not collections._members_agree("red", mixed, names, m)
    assert not collections._members_agree("credit:red", reds, ["credit:red"] + names[1:], m)


# ---------- faces ----------


def people_by_name(client):
    return {p["name"]: p for p in client.get("/api/people").json()}


def test_faces_people_merge_search_rebuild(client, upload, faces_on):
    reds = [upload(f"r{i}.png", data=solid((200 + i * 10, 10, 10)))["item"] for i in range(3)]
    blues = [upload(f"b{i}.png", data=solid((10, 10, 200 + i * 10)))["item"] for i in range(2)]
    assert drain() == 15  # embed, OCR (they look like screenshots) and faces for each
    sidecar = json.loads((get_settings().items_dir / reds[0]["id"] / "ai" / "faces.json")
                         .read_text())
    assert sidecar["faces"][0]["box"] == {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}
    with SessionLocal() as s:
        assert s.execute(sa.select(sa.func.count()).select_from(Face)).scalar_one() == 5
        assert faces.cluster_unassigned(s) == 2
    people = client.get("/api/people").json()
    assert sorted(p["face_count"] for p in people) == [2, 3]
    red = next(p for p in people if p["face_count"] == 3)
    blue = next(p for p in people if p["face_count"] == 2)
    assert client.patch(f"/api/people/{red['id']}", json={"name": "Ada"}).json()["name"] == "Ada"
    page = client.get("/api/items", params={"person": red["id"]}).json()
    assert {i["id"] for i in page["items"]} == {r["id"] for r in reds}
    # a new picture of Ada is recognised as her straight away
    later = upload("r9.png", data=solid((250, 12, 12)))["item"]
    drain()
    item_faces = client.get(f"/api/items/{later['id']}/faces").json()
    assert item_faces[0]["person_id"] == red["id"] and item_faces[0]["person_name"] == "Ada"
    thumb = client.get(item_faces[0]["thumb_url"])
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"
    # merge the two people; the name stays
    merged = client.post("/api/people/merge", json={"from": [blue["id"]],
                                                   "into": red["id"]}).json()
    assert merged["face_count"] == 6 and merged["name"] == "Ada"
    assert client.get("/api/people").json()[0]["id"] == red["id"]
    # move one face back to nobody
    face_id = client.get(f"/api/items/{blues[0]['id']}/faces").json()[0]["id"]
    assert client.patch(f"/api/faces/{face_id}", json={"person_id": None}).json()[
        "person_id"] is None
    assert cli.verify() == 0
    people_file = json.loads((get_settings().state_dir / "people.json").read_text())
    assert people_file["people"][0]["name"] == "Ada"
    # rebuild: faces from sidecars, people and names from people.json
    ml.down = True
    assert cli.rebuild_index(wipe=True) == 0
    after = client.get("/api/people").json()
    assert [(p["id"], p["name"], p["face_count"]) for p in after] == [(red["id"], "Ada", 5)]
    assert cli.verify() == 0
    # purging an item keeps people.json in step
    ml.down = False
    client.delete(f"/api/items/{later['id']}")
    client.delete(f"/api/items/{later['id']}", params={"hard": "true"})
    assert cli.verify() == 0
    assert client.get("/api/people").json()[0]["face_count"] == 4
    out = mcp_result(client, "list_people")
    assert json.loads(out["content"][0]["text"])["name"] == "Ada"


def test_faces_off_queues_nothing(client, upload, ml):
    upload("r.png", data=solid((200, 10, 10)))
    assert drain() == 2  # the embedding and OCR, no faces
    assert "/faces" not in ml.calls
    with SessionLocal() as s:
        assert "faces" not in ai.backfill(s)


def test_faces_backfill_and_people_scope(client, upload, ml, monkeypatch):
    a = client.post("/api/folders", json={"name": "A"}).json()
    b = client.post("/api/folders", json={"name": "B"}).json()
    in_a = [upload(f"a{i}.png", data=solid((200 + i, 10, 10)),
                   folder_ids=json.dumps([a["id"]]))["item"] for i in range(2)]
    in_b = [upload(f"b{i}.png", data=solid((10, 10, 200 + i)),
                   folder_ids=json.dumps([b["id"]]))["item"] for i in range(2)]
    drain()
    monkeypatch.setattr(get_settings(), "faces", True)
    with SessionLocal() as s:
        assert ai.backfill(s, ("faces",)) == {"faces": 4}
    drain()
    with SessionLocal() as s:
        assert faces.cluster_unassigned(s) == 2
        assert s.execute(sa.select(sa.func.count()).select_from(Person)).scalar_one() == 2
    fam = as_token(client, make_token(client, "Fam", ["read"], folder_ids=[a["id"]])["token"])
    seen = fam.get("/api/people").json()
    assert len(seen) == 1 and seen[0]["face_count"] == 2
    assert seen[0]["cover"]["item_id"] in {i["id"] for i in in_a}
    hidden = client.get(f"/api/items/{in_b[0]['id']}/faces").json()[0]
    assert fam.get(hidden["thumb_url"]).status_code == 404
    assert fam.patch(f"/api/people/{seen[0]['id']}", json={"name": "x"}).status_code == 404
    page = fam.get("/api/items", params={"person": hidden["person_id"]}).json()
    assert page["items"] == []


def test_worker_upkeep_clusters_faces_and_collections(client, upload, faces_on):
    from kist import worker

    for i in range(9):
        upload(f"r{i}.png", data=solid((200 + i, 10, 10)), tags="red")
    for i in range(9):
        upload(f"b{i}.png", data=solid((10, 10, 200 + i)))
    drain()
    assert collections.due(24) is True
    worker.run_organise()
    assert collections.due(24) is False
    assert len(collections.read_cache()["clusters"]) == 2
    assert len(client.get("/api/people").json()) == 2
    # faces of a picture added later join the right person
    upload("r9.png", data=solid((240, 10, 10)))
    drain()
    counts = sorted(p["face_count"] for p in client.get("/api/people").json())
    assert counts == [9, 10]


def test_similar_duplicates_by_embedding(client, upload, ml):
    a = upload("a.png", data=solid((200, 30, 30)))["item"]
    b = upload("b.png", data=solid((201, 30, 30)))["item"]
    upload("c.png", data=solid((30, 30, 200)))
    drain()
    groups = client.get("/api/duplicates/groups", params={"kind": "similar"}).json()
    assert len(groups) == 1
    assert {i["id"] for i in groups[0]["items"]} == {a["id"], b["id"]}
    assert groups[0]["pairs"][0]["score"] >= 0.92
    # stacked together: not duplicates of each other any more
    client.post("/api/stacks", json={"item_ids": [a["id"], b["id"]]})
    assert client.get("/api/duplicates/groups", params={"kind": "similar"}).json() == []
    out = mcp_result(client, "duplicate_groups", {"kind": "similar"})
    assert not out.get("isError")
