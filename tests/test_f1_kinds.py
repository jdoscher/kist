"""F1 utility collections ("Kinds"): rules, CLIP zero-shot with a fake ml
service, children, corrections + undo + history, learning, strictness,
packs, the auto_kind filter everywhere, MCP, reader scope and rebuild.

The fake ml service maps words to concept axes: a prompt's text vector is
the sum of the axes of the concept words in it (plus a small per-prompt
hash so unrelated prompts differ), and test items get image vectors built
from the same axes, so CLIP calls are predictable.
"""

import hashlib
import json

import httpx
import numpy as np
import pytest
import sqlalchemy as sa
from conftest import png_bytes
from media_fixtures import exif_bytes, jpeg
from test_ai import DIM, FakeMl
from test_tokens import as_token, make_token, mcp_result

from kist import cli, worker
from kist.config import get_settings
from kist.db import SessionLocal
from kist.models import Item, ItemKind, ItemKindPass
from kist.services import ai, kinds, kinds_catalog

CONCEPTS = [
    ("screenshot", 20), ("document", 21), ("page", 21), ("text", 21), ("letter", 21),
    ("contract", 21), ("sheet", 21), ("drawing", 22), ("sketch", 22), ("drawn", 22),
    ("pencil", 23), ("graphite", 23), ("ink", 24), ("pen", 24), ("line art", 24),
    ("painting", 25), ("oil", 25), ("watercolor", 25), ("gouache", 25), ("acrylic", 25),
    ("photo", 26), ("photograph", 26), ("runway", 30), ("catwalk", 30),
]


def text_vector(text: str) -> list[float]:
    t = text.lower()
    v = np.zeros(DIM, dtype=np.float64)
    for word, axis in CONCEPTS:
        if word in t:
            v[axis] = 1.0
    h = hashlib.sha1(t.encode()).digest()
    for i in range(8):  # a little per-prompt noise, far from the concept axes
        v[100 + h[i] % 300] += 0.15
    return list(v / np.linalg.norm(v))


def image_vector(*axes: int, noise: float = 0.0, seed: int = 0) -> list[float]:
    v = np.zeros(DIM)
    for a in axes:
        v[a] = 1.0
    if noise:
        v[100:400] += np.random.default_rng(seed).normal(0, noise, 300)
    return list(v / np.linalg.norm(v))


def centroid(kind_id: str):
    c = np.mean([text_vector(p) for p in kinds_catalog.BY_ID[kind_id].prompts], axis=0)
    return c / np.linalg.norm(c)


def between(a: str, b: str):
    """Exactly as close to kind a's prompts as to kind b's."""
    v = centroid(a) + centroid(b)
    return v / np.linalg.norm(v)


class FakeKindsMl(FakeMl):
    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/embed/text":
            self.calls.append("/embed/text")
            if self.down:
                raise httpx.ConnectError("ml is down", request=request)
            text = json.loads(request.content)["text"]
            return httpx.Response(200, json={"model": self.model, "dim": DIM,
                                             "vector": text_vector(text)})
        return super().__call__(request)


@pytest.fixture
def ml(clean, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ml_url", "http://ml.test")
    monkeypatch.setattr(settings, "ai_window", "")
    fake = FakeKindsMl()
    ai.set_transport(httpx.MockTransport(fake))
    kinds._prompt_cache.clear()
    yield fake
    ai.set_transport(None)
    kinds._prompt_cache.clear()


def run_jobs():
    while worker.run_one():
        pass


def with_vector(upload, name, vec, **form):
    """An item whose embedding is `vec` (stored as the worker would)."""
    n = int(hashlib.sha1(name.encode()).hexdigest()[:6], 16)
    item = upload(name, data=png_bytes(color=(n % 256, n // 256 % 256, 77), size=(900, 700)),
                  **form)["item"]
    with SessionLocal() as s:
        ai.store_embedding(s, item["id"], ai.active_model(), vec)
    return item


def ids(client, **params):
    res = client.get("/api/items", params={"limit": 500, **params})
    assert res.status_code == 200, res.text
    return {i["id"] for i in res.json()["items"]}


def modified(item_id):
    with SessionLocal() as s:
        return s.get(Item, item_id).modified_at


# ---------- rules (no AI) ----------


def test_rule_kinds_without_ai(client, upload):
    shot = upload("Screenshot 2026-11-12 at 10.00.00.png")["item"]
    phone = upload("IMG_0001.png", data=png_bytes(size=(1179, 2556)))["item"]
    cam = upload("DSC_1.jpg", data=jpeg(size=(1600, 1200), exif=exif_bytes()),
                 mime="image/jpeg")["item"]
    small = upload("thumb.png", data=png_bytes(size=(300, 200)))["item"]
    big = upload("big.png", data=png_bytes(size=(1600, 1300)))["item"]
    before = {i: modified(i) for i in (shot["id"], cam["id"])}
    run_jobs()

    assert ids(client, auto_kind="screenshots") == {shot["id"], phone["id"]}
    assert ids(client, auto_kind="photos") == {cam["id"]}
    low = ids(client, auto_kind="low-res")
    assert small["id"] in low and big["id"] not in low and cam["id"] not in low
    # exclusion and combination
    assert shot["id"] not in ids(client, auto_kind="-screenshots")
    assert ids(client, auto_kind="screenshots,low-res") == {shot["id"]} & low | (
        {phone["id"]} & low)
    # the why, shown as automatic
    got = {k["kind"]: k for k in client.get(f"/api/items/{phone['id']}/kinds").json()}
    assert got["screenshots"]["source"] == "auto"
    assert "1179 × 2556" in got["screenshots"]["why"]
    assert "Camera" in {k["kind"]: k for k in
                        client.get(f"/api/items/{cam['id']}/kinds").json()}["photos"]["why"]
    # sorting is bookkeeping: modified_at doesn't move
    assert {i: modified(i) for i in before} == before
    # the overview counts what each kind holds, and hides small kinds
    ov = client.get("/api/kinds").json()
    by = {k["id"]: k for k in ov["kinds"]}
    assert by["screenshots"]["count"] == 2 and by["screenshots"]["visible"] is False
    assert by["ai-made"]["enabled"] is False  # opt-in
    assert by["runway"]["enabled"] is False  # a trade pack, off by default
    assert ov["min_items"] == 20
    res = client.patch("/api/kinds", json={"min_items": 2})
    assert {k["id"]: k for k in res.json()["kinds"]}["screenshots"]["visible"] is True
    # an unknown kind in the filter is a 422 with the list
    res = client.get("/api/items", params={"auto_kind": "nope"})
    assert res.status_code == 422 and "screenshots" in res.text


def test_low_res_threshold_and_print_size_hook(client, upload):
    mid = upload("mid.png", data=png_bytes(size=(1000, 800)))["item"]
    run_jobs()
    assert mid["id"] in ids(client, auto_kind="low-res")
    client.patch("/api/kinds", json={"low_res_px": 900})
    run_jobs()  # the re-sort job
    assert mid["id"] not in ids(client, auto_kind="low-res")
    # F4's print check, when registered, decides
    kinds.register_print_size(lambda item: (8.0, 6.0))
    try:
        with SessionLocal() as s:
            kinds.backfill(s, all_items=True)
        got = {k["kind"]: k for k in client.get(f"/api/items/{mid['id']}/kinds").json()}
        assert "low-res" not in got
        kinds.register_print_size(lambda item: (2.0, 1.5))
        with SessionLocal() as s:
            kinds.backfill(s, all_items=True)
        got = {k["kind"]: k for k in client.get(f"/api/items/{mid['id']}/kinds").json()}
        assert "300 dpi" in got["low-res"]["why"]
    finally:
        kinds.register_print_size(None)


def test_ai_made_is_opt_in_and_metadata_only(client, upload):
    item = upload("gen.png")["item"]
    client.patch(f"/api/items/{item['id']}", json={"provenance": {"ai_generated": True}})
    run_jobs()
    assert client.get("/api/items", params={"auto_kind": "ai-made"}).json()["items"] == []
    client.patch("/api/kinds/ai-made", json={"enabled": True})
    run_jobs()
    assert ids(client, auto_kind="ai-made") == {item["id"]}


# ---------- CLIP kinds, children, corrections ----------


def test_clip_kinds_children_corrections_undo(client, upload, ml):
    pencil = with_vector(upload, "p.png", image_vector(22, 23))
    ink = with_vector(upload, "i.png", image_vector(22, 24))
    unsure = with_vector(upload, "d.png", list(between("pencil", "ink")))  # medium unknown
    paint = with_vector(upload, "o.png", image_vector(25))
    photo = with_vector(upload, "ph.png", image_vector(26))
    run_jobs()
    drawings = ids(client, auto_kind="drawings")
    assert {pencil["id"], ink["id"], unsure["id"]} <= drawings
    assert paint["id"] not in drawings and photo["id"] not in drawings
    assert pencil["id"] in ids(client, auto_kind="pencil")
    assert ink["id"] in ids(client, auto_kind="ink")
    # unsure child call: only the parent
    assert unsure["id"] not in ids(client, auto_kind="pencil") | ids(client, auto_kind="ink")
    assert paint["id"] in ids(client, auto_kind="paintings")

    # "Not this" on the parent takes the children too; one batch, undoable
    before = modified(pencil["id"])
    res = client.post("/api/kinds/drawings/correct",
                      json={"item_ids": [pencil["id"]], "verdict": False})
    assert res.status_code == 200 and res.json()["changed"] == 1
    batch = res.json()["batch_id"]
    assert pencil["id"] not in ids(client, auto_kind="drawings")
    assert pencil["id"] not in ids(client, auto_kind="pencil")
    assert pencil["id"] in ids(client, auto_kind="drawings:rejected")
    assert modified(pencil["id"]) == before  # a correction isn't an item edit
    hist = client.get(f"/api/items/{pencil['id']}/history").json()
    assert any(e["kind"] == "kinds" for e in (hist if isinstance(hist, list)
                                              else hist.get("events", [])))
    side = json.loads((get_settings().items_dir / pencil["id"] / "ai" / "kinds.json")
                      .read_text())
    assert side["verdicts"] == [{"kind": "drawings", "verdict": False,
                                 "at": side["verdicts"][0]["at"]}]
    # a re-sort never brings it back
    with SessionLocal() as s:
        kinds.backfill(s, all_items=True)
    assert pencil["id"] not in ids(client, auto_kind="drawings")
    res = client.post(f"/api/batches/{batch}/undo")
    assert res.status_code == 200, res.text
    assert res.json()["undone"] == 1
    assert pencil["id"] in ids(client, auto_kind="drawings")
    assert pencil["id"] in ids(client, auto_kind="pencil")

    # "Belongs here" on a child puts it in the parent too, shown as human
    client.post("/api/kinds/ink/correct", json={"item_ids": [photo["id"]], "verdict": True})
    assert photo["id"] in ids(client, auto_kind="ink")
    assert photo["id"] in ids(client, auto_kind="drawings")
    got = {k["kind"]: k for k in client.get(f"/api/items/{photo['id']}/kinds").json()}
    assert got["ink"]["source"] == "human" and got["ink"]["verdict"] is True
    assert photo["id"] in ids(client, auto_kind="ink:confirmed")
    assert photo["id"] not in ids(client, auto_kind="drawings:auto")
    # back to automatic
    client.post("/api/kinds/ink/correct", json={"item_ids": [photo["id"]], "verdict": None})
    assert photo["id"] not in ids(client, auto_kind="ink")
    assert not (get_settings().items_dir / photo["id"] / "ai" / "kinds.json").exists()
    assert cli.verify() == 0


def test_strictness_packs_and_unsure_queue(client, upload, ml):
    mixed = [with_vector(upload, f"m{i}.png", image_vector(22, 26, noise=0.0), ) for i in range(1)]
    strong = with_vector(upload, "s.png", image_vector(22))
    run_jobs()
    with SessionLocal() as s:
        score = s.get(ItemKind, (mixed[0]["id"], "drawings")).score
    assert 0.05 < score < 0.95  # half drawing, half photo: a borderline call
    # loose: in; strict: out (instantly, no re-sort needed)
    client.patch("/api/kinds/drawings", json={"strictness": 0.0})
    assert mixed[0]["id"] in ids(client, auto_kind="drawings")
    client.patch("/api/kinds/drawings", json={"strictness": 1.0})
    assert mixed[0]["id"] not in ids(client, auto_kind="drawings")
    assert strong["id"] in ids(client, auto_kind="drawings")
    # the review queue: near the line, no verdict
    t = kinds.threshold("drawings")
    client.patch("/api/kinds/drawings", json={"strictness": (score - kinds.T_MIN)
                                              / (kinds.T_MAX - kinds.T_MIN)})
    assert mixed[0]["id"] in ids(client, auto_kind="drawings:unsure")
    assert t != kinds.threshold("drawings")
    # a trade pack: switched on in one go, sorted by the job
    runway = with_vector(upload, "r.png", image_vector(30, 26))
    run_jobs()
    assert client.get("/api/items", params={"auto_kind": "runway"}).json()["items"] == []
    ov = client.patch("/api/kinds", json={"packs": {"fashion": True}}).json()
    assert {p["id"]: p["enabled"] for p in ov["packs"]}["fashion"] is True
    run_jobs()
    assert runway["id"] in ids(client, auto_kind="runway")
    # off again: the automatic calls go, a verdict stays
    client.post("/api/kinds/runway/correct", json={"item_ids": [strong["id"]], "verdict": True})
    client.patch("/api/kinds", json={"packs": {"fashion": False}})
    with SessionLocal() as s:
        rows = s.execute(sa.select(ItemKind).where(ItemKind.kind == "runway")).scalars().all()
        assert [(r.item_id, r.verdict, r.auto) for r in rows] == [(strong["id"], True, False)]
    assert client.patch("/api/kinds", json={"packs": {"base": False}}).status_code == 422
    assert client.patch("/api/kinds/nope", json={"strictness": 0.5}).status_code == 404
    # settings live in the library
    raw = json.loads((get_settings().state_dir / "kinds.json").read_text())
    assert raw["packs"]["fashion"] is False


def test_learning_from_corrections(client, upload, ml):
    # pencil drawings that CLIP reads as ink (the ECCV problem): the person
    # corrects ten of them, and the eleventh is called right
    def near_ink(axis, seed):
        v = 2 * centroid("ink") + centroid("drawings") + 1.5 * np.asarray(
            image_vector(axis, noise=0.01, seed=seed))
        return list(v / np.linalg.norm(v))

    items = [with_vector(upload, f"x{i}.png", near_ink(450, i)) for i in range(12)]
    others = [with_vector(upload, f"y{i}.png", near_ink(451, 50 + i)) for i in range(6)]
    run_jobs()
    probe, train = items[-1], items[:-1]
    assert len({i["id"] for i in items + others}) == 18
    assert probe["id"] in ids(client, auto_kind="ink")
    for it in train[:6]:
        client.post("/api/kinds/pencil/correct", json={"item_ids": [it["id"]], "verdict": True})
    client.post("/api/kinds/ink/correct",
                json={"item_ids": [it["id"] for it in train], "verdict": False})
    client.post("/api/kinds/ink/correct",
                json={"item_ids": [o["id"] for o in others[:4]], "verdict": True})
    run_jobs()  # retrains ink (15 corrections), then re-sorts
    ov = {k["id"]: k for k in client.get("/api/kinds").json()["kinds"]}
    assert ov["ink"]["learned"]["corrections"] == 15
    assert ov["pencil"]["learned"] is None  # 6 < LEARN_MIN
    assert probe["id"] not in ids(client, auto_kind="ink")
    with SessionLocal() as s:
        def score(item):
            row = s.get(ItemKind, (item["id"], "ink"))
            return row.score if row is not None else 0.0
        # the uncorrected look-alike of the confirmed ones now scores well above
        # the uncorrected look-alike of the rejected ones
        assert score(others[-1]) > score(probe) + 0.15
        why = s.get(ItemKind, (others[-1]["id"], "ink")).why
    assert why.startswith("Learned from your corrections")
    clf = json.loads((get_settings().state_dir / "kinds-classifiers.json").read_text())
    assert "ink" in clf["kinds"] and clf["model"] == ai.active_model()


def test_train_logreg_separates_with_offset():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(40, 8)).astype(np.float32)
    y = (X[:, 0] > 0).astype(float)
    w, b, _ = kinds.train_logreg(X, y, np.zeros(40), l2=0.1)
    pred = (X @ w + b) > 0
    assert (pred == y.astype(bool)).mean() > 0.95
    # a strong offset the labels agree with needs no weights
    w2, _, a = kinds.train_logreg(X, y, np.where(y > 0, 6.0, -6.0), l2=1.0)
    assert a > 0.5
    assert np.abs(w2).max() < np.abs(w).max()


# ---------- everywhere: smart folders, MCP, scope ----------


def test_smart_folder_mcp_and_reader_scope(client, upload):
    from kist.services import selection

    folder = client.post("/api/folders", json={"name": "Shared"}).json()
    mine = upload("Screenshot one.png", folder_ids=folder["id"])["item"]
    other = upload("Screenshot two.png")["item"]
    run_jobs()
    with SessionLocal() as s:
        got = s.execute(sa.select(Item.id).where(
            *selection.smart_filters({"auto_kind": "screenshots"}))).scalars().all()
    assert set(got) == {mine["id"], other["id"]}
    sf = client.post("/api/smart-folders", json={"name": "Shots",
                                                 "query": {"auto_kind": "screenshots"}})
    assert sf.status_code in (200, 201), sf.text

    # MCP: list kinds, filter by kind, correct
    out = mcp_result(client, "list_kinds", {})
    assert not out.get("isError")
    data = json.loads(out["content"][0]["text"])
    assert {k["id"]: k for k in data["kinds"]}["screenshots"]["count"] == 2
    out = mcp_result(client, "search_items", {"auto_kind": "screenshots"})
    assert not out.get("isError") and mine["id"] in out["content"][0]["text"]
    out = mcp_result(client, "correct_kind", {"kind": "screenshots", "ids": [other["id"]],
                                              "verdict": "not"})
    assert not out.get("isError"), out
    assert ids(client, auto_kind="screenshots") == {mine["id"]}

    # a folder-limited reader: counts and items of its own folder only
    tok = make_token(client, "reader", ["read"], folder_ids=[folder["id"]])
    reader = as_token(client, tok["token"])
    ov = reader.get("/api/kinds").json()
    assert {k["id"]: k for k in ov["kinds"]}["screenshots"]["count"] == 1
    res = reader.get("/api/items", params={"auto_kind": "-screenshots"}).json()
    assert other["id"] not in {i["id"] for i in res["items"]}
    assert reader.get(f"/api/items/{other['id']}/kinds").status_code == 404
    assert reader.get(f"/api/items/{mine['id']}/kinds").status_code == 200
    res = reader.post("/api/kinds/screenshots/correct",
                      json={"item_ids": [mine["id"]], "verdict": False})
    assert res.status_code in (403, 404)
    assert reader.patch("/api/kinds", json={"min_items": 1}).status_code in (403, 404)
    # an agent token can correct but its MCP read-only sibling can't
    ro = make_token(client, "ro", ["read"])
    out = mcp_result(as_token(client, ro["token"]), "correct_kind",
                     {"kind": "screenshots", "ids": [mine["id"]], "verdict": "not"})
    assert out.get("isError")


# ---------- durability ----------


def test_kinds_survive_rebuild_with_ml_down(client, upload, ml):
    draw = with_vector(upload, "d.png", image_vector(22, 23))
    shot = upload("Screenshot x.png")["item"]
    paint = with_vector(upload, "p.png", image_vector(25))
    run_jobs()
    client.post("/api/kinds/paintings/correct", json={"item_ids": [paint["id"]],
                                                      "verdict": False})
    client.post("/api/kinds/documents/correct", json={"item_ids": [draw["id"]],
                                                      "verdict": True})
    client.patch("/api/kinds", json={"packs": {"editorial": True}, "min_items": 3})
    run_jobs()

    def snapshot():
        with SessionLocal() as s:
            rows = s.execute(sa.select(ItemKind.item_id, ItemKind.kind, ItemKind.member,
                                       ItemKind.verdict)).all()
        return sorted((r[0], r[1], r[2], r[3]) for r in rows if r[2] or r[3] is not None)

    before = snapshot()
    stamps = {i: modified(i) for i in (draw["id"], shot["id"], paint["id"])}
    assert any(r[1] == "drawings" and r[2] for r in before)
    ml.down = True
    kinds._prompt_cache.clear()  # only the library's files are left
    assert cli.rebuild_index(wipe=True) == 0
    assert snapshot() == before
    assert {i: modified(i) for i in stamps} == stamps
    assert client.get("/api/kinds").json()["min_items"] == 3
    with SessionLocal() as s:
        assert s.execute(sa.select(sa.func.count()).select_from(ItemKindPass)).scalar_one() == 3
    assert cli.verify() == 0
    # verify notices a verdict file that went missing; rewrite-meta writes it back
    (get_settings().items_dir / paint["id"] / "ai" / "kinds.json").unlink()
    assert cli.verify() != 0
    assert cli.rewrite_meta() == 0
    assert cli.verify() == 0


def test_backfill_only_does_missing_work(client, upload, ml):
    a = with_vector(upload, "a.png", image_vector(22))
    run_jobs()
    with SessionLocal() as s:
        assert kinds.backfill(s) == 0
        s.execute(sa.delete(ItemKindPass).where(ItemKindPass.item_id == a["id"]))
        s.commit()
        assert kinds.backfill(s) == 1
        assert kinds.backfill(s) == 0


def test_kind_catalogue_is_consistent():
    ids_ = [k.id for k in kinds_catalog.KINDS]
    assert len(ids_) == len(set(ids_))
    for k in kinds_catalog.KINDS:
        assert set(k.packs) <= set(kinds_catalog.PACKS)
        assert k.prompts or k.rule
        if k.parent:
            assert k.parent in kinds_catalog.BY_ID and not k.negatives
        assert kinds.FILTER_RE.match(k.id)
    base = {k.id for k in kinds_catalog.KINDS if "base" in k.packs}
    assert {"screenshots", "documents", "drawings", "pencil", "ink", "paintings", "photos",
            "low-res"} <= base
    assert get_settings().__class__.model_fields["kinds"].default is True


def test_web_names_match_the_catalogue():
    """web/src/kindsCore.ts KIND_NAMES (titles before the overview loads)."""
    import re
    from pathlib import Path

    src = (Path(__file__).parent.parent / "web" / "src" / "kindsCore.ts").read_text()
    block = src[src.index("export const KIND_NAMES"):]
    block = block[:block.index("}")]
    names = dict(re.findall(r"^\s*'?([a-z0-9-]+)'?: '([^']+)',", block, re.MULTILINE))
    assert names == {k.id: k.name for k in kinds_catalog.KINDS}
