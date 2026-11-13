"""F7 cutouts: the job, the new item, boards, scope, settings and rebuild
(the ml service is faked; tests/test_f7_cutouts_ml.py covers its side)."""

import io
import json

import httpx
import numpy as np
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from PIL import Image

from kist import cli, worker
from kist.config import Settings, get_settings
from kist.db import SessionLocal
from kist.lifecycle import JobKind, JobStatus
from kist.models import Item, Job
from kist.services import ai, cutouts


def photo_png(size=(400, 300), subject=(120, 80, 280, 220)) -> bytes:
    """A light background with a darker square subject and a shadow strip
    under it (what the fake ml service cuts out is the square)."""
    im = Image.new("RGB", size, (230, 230, 225))
    im.paste((40, 90, 170), subject)
    x0, _y0, x1, y1 = subject
    im.paste((150, 150, 147), (x0 + 10, y1, x1 + 10, min(size[1], y1 + 12)))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class FakeMl:
    """/cutout answers a mask of the middle box (in relative terms, so it
    fits whatever size the worker sends)."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.status = 200
        self.box = (120 / 400, 80 / 300, 280 / 400, 220 / 300)
        self.empty = False
        self.raise_on_cutout: Exception | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((path, dict(request.url.params)))
        if path == "/cutout" and self.raise_on_cutout is not None:
            raise self.raise_on_cutout
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "model": "ViT-B-32__openai",
                                             "cutout": {"default": "isnet-general-use", "models": {
                                                 "isnet-general-use": {
                                                     "state": "missing", "size_bytes": 178648008,
                                                     "license": "Apache-2.0",
                                                     "downloaded_bytes": 0}}}})
        if path == "/cutout":
            if self.status == 503:
                return httpx.Response(503, json={"detail": "downloading the cut-out model "
                                                           "isnet-general-use (12 of 179 MB)"})
            if self.status == 507:
                detail = "The birefnet-lite model needs about 8 GB for Kist's AI service"
                return httpx.Response(507, json={"detail": detail})
            if self.status != 200:
                return httpx.Response(self.status, json={"detail": "nope"})
            im = Image.open(io.BytesIO(request.content))
            w, h = im.size
            mask = Image.new("L", (w, h), 0)
            if not self.empty:
                bx = self.box
                mask.paste(255, (round(bx[0] * w), round(bx[1] * h), round(bx[2] * w),
                                 round(bx[3] * h)))
            buf = io.BytesIO()
            mask.save(buf, "PNG")
            model = request.url.params.get("model") or "isnet-general-use"
            return httpx.Response(200, content=buf.getvalue(),
                                  headers={"content-type": "image/png", "x-kist-model": model,
                                           "x-kist-ms": "12.5"})
        return httpx.Response(404)


@pytest.fixture
def ml(clean, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ml_url", "http://ml.test")
    monkeypatch.setattr(settings, "ai_window", "")
    monkeypatch.setattr(settings, "cutouts", True)
    fake = FakeMl()
    ai.set_transport(httpx.MockTransport(fake))
    yield fake
    ai.set_transport(None)


def drain() -> list[str]:
    """Run every cutout job as the worker's AI lane does; their results."""
    out = []
    while True:
        with SessionLocal() as s:
            jobs = [(j.id, j.kind) for j in ai.claim(s, runner="worker",
                                                     kinds=(JobKind.CUTOUT,), limit=5)]
        if not jobs:
            return out
        for job_id, kind in jobs:
            out.append(worker.run_ai_job(job_id, kind))


def cutout_jobs():
    with SessionLocal() as s:
        return list(s.execute(sa.select(Job).where(Job.kind == JobKind.CUTOUT)
                              .order_by(Job.id)).scalars())


def setup_item(client, upload, **form):
    folder = client.post("/api/folders", json={"name": "Refs"}).json()
    item = upload("chair.png", data=photo_png(), tags="chairs,wood",
                  folder_ids=json.dumps([folder["id"]]),
                  provenance=json.dumps({"creator": "A. Maker", "credit": "Studio X",
                                         "license": "CC-BY-4.0"}),
                  source_url="https://example.com/chair", **form)["item"]
    return folder, item


def test_cutouts_are_on_by_default():
    assert Settings.model_fields["cutouts"].default is True
    assert Settings.model_fields["cutout_max_side"].default == 4096


def test_cut_out_makes_a_linked_transparent_item_and_leaves_the_original(client, upload, ml):
    folder, item = setup_item(client, upload)
    before = client.get(f"/api/items/{item['id']}").json()
    res = client.post(f"/api/items/{item['id']}/cutout", json={"feather": 1.5},
                      headers={"X-Kist-Batch": "cut-1"})
    assert res.status_code == 202, res.text
    job = res.json()
    assert job["kind"] == "cutout" and job["status"] == "pending"
    # asking again with the same settings doesn't queue a second one
    again = client.post(f"/api/items/{item['id']}/cutout", json={"feather": 1.5}).json()
    assert again["id"] == job["id"]

    assert drain() == ["done"]
    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done", done
    new_id = done["result_item_id"]
    new = client.get(f"/api/items/{new_id}").json()
    assert new["mime"] == "image/png" and new["filename"] == "chair cutout.png"
    assert sorted(new["tags"]) == ["chairs", "wood"] and new["folder_ids"] == [folder["id"]]
    assert new["provenance"]["credit"] == "Studio X"
    assert new["provenance"]["creator"] == "A. Maker"
    assert new["provenance"]["page_url"] == "https://example.com/chair"
    assert "c2pa" not in new["provenance"]
    info = new["media"]["cutout"]
    assert info["source_item"] == item["id"] and info["model"] == "isnet-general-use"
    assert info["feather"] == 1.5 and info["shadow"] is False
    # trimmed to the subject plus a 2% margin
    assert abs(new["width"] - 166) <= 10 and abs(new["height"] - 146) <= 10
    links = client.get(f"/api/items/{new_id}/links").json()
    assert any(link["item"]["id"] == item["id"] and link["kind"] == "derived_from"
               for link in links), links

    # the file: transparent outside the subject, opaque inside
    data = client.get(f"/api/items/{new_id}/file").content
    im = Image.open(io.BytesIO(data))
    assert im.mode == "RGBA"
    a = np.asarray(im.getchannel("A"))
    assert a[0, 0] == 0 and a[a.shape[0] // 2, a.shape[1] // 2] == 255
    rgb = im.convert("RGB").getpixel((im.width // 2, im.height // 2))
    assert rgb == (40, 90, 170)

    # the original is untouched: same bytes, same modified_at
    after = client.get(f"/api/items/{item['id']}").json()
    assert after["sha256"] == before["sha256"]
    assert after["modified_at"] == before["modified_at"]
    listed = client.get(f"/api/items/{item['id']}/cutouts").json()
    assert [i["id"] for i in listed] == [new_id]

    # boards draw the transparent copy
    prev = client.get(f"/api/items/{new_id}/cutout-preview?size=1024")
    assert prev.status_code == 200 and prev.headers["content-type"] == "image/webp"
    assert Image.open(io.BytesIO(prev.content)).mode == "RGBA"
    assert client.get(f"/api/items/{item['id']}/cutout-preview").status_code == 404

    # history: the new item was made in the request's batch, so undo trashes it
    hist = client.get(f"/api/items/{new_id}/history").json()
    assert any(e["kind"] == "create" and e["batch_id"] == "cut-1" for e in hist), hist
    undo = client.post("/api/batches/cut-1/undo").json()
    assert undo["undone"] >= 1, undo
    assert client.get(f"/api/items/{new_id}").json()["deleted_at"] is not None
    assert client.get(f"/api/items/{item['id']}").json()["deleted_at"] is None

    # the same cut-out asked for again comes back out of the trash
    client.post(f"/api/items/{item['id']}/cutout", json={"feather": 1.5})
    assert drain() == ["done"]
    assert cutout_jobs()[-1].payload["result_item_id"] == new_id
    assert client.get(f"/api/items/{new_id}").json()["deleted_at"] is None


def test_shadow_and_model_choice(client, upload, ml):
    _folder, item = setup_item(client, upload)
    res = client.post(f"/api/items/{item['id']}/cutout",
                      json={"shadow": True, "model": "birefnet-lite"})
    assert res.status_code == 202
    assert drain() == ["done"]
    assert ("/cutout", {"model": "birefnet-lite"}) in ml.calls
    new_id = cutout_jobs()[-1].payload["result_item_id"]
    new = client.get(f"/api/items/{new_id}").json()
    assert new["media"]["cutout"]["shadow"] is True
    assert new["media"]["cutout"]["shadow_found"] is True
    assert new["media"]["cutout"]["model"] == "birefnet-lite"
    im = Image.open(io.BytesIO(client.get(f"/api/items/{new_id}/file").content))
    # the grey strip under the square is kept as a soft black shadow
    a = np.asarray(im.getchannel("A")).astype(int)
    box = new["media"]["cutout"]["box"]
    sy = 220 - box[1] + 5  # inside the strip, below the subject
    sx = 200 - box[0]
    assert 20 < a[sy, sx] < 255, a[sy, sx]
    assert im.getpixel((sx, sy))[:3] == (0, 0, 0)


def test_bad_requests(client, upload, ml):
    _folder, item = setup_item(client, upload)
    url = f"/api/items/{item['id']}/cutout"
    assert client.post(url, json={"feather": 99}).status_code == 422
    assert client.post(url, json={"model": "u2net"}).status_code == 422
    doc = upload("notes.txt", data=b"just text", mime="text/plain")["item"]
    res = client.post(f"/api/items/{doc['id']}/cutout", json={})
    assert res.status_code == 409 and "only images" in res.text
    client.delete(f"/api/items/{item['id']}")
    assert client.post(url, json={}).status_code == 409
    many = client.post("/api/cutouts", json={"item_ids": [doc["id"], "01NOPE"]}).json()
    assert many["jobs"] == [] and {s["item_id"] for s in many["skipped"]} == {doc["id"], "01NOPE"}


def test_selection_queues_one_job_per_image(client, upload, ml):
    a = upload("a.png", data=photo_png())["item"]
    b = upload("b.png", data=photo_png(subject=(100, 60, 300, 240)))["item"]
    res = client.post("/api/cutouts", json={"item_ids": [a["id"], b["id"], a["id"]]})
    assert res.status_code == 202
    assert len(res.json()["jobs"]) == 2 and res.json()["skipped"] == []
    assert drain() == ["done", "done"]


def test_ml_down_or_downloading_puts_the_job_back(client, upload, ml):
    _folder, item = setup_item(client, upload)
    client.post(f"/api/items/{item['id']}/cutout", json={})
    ml.status = 503
    assert drain() == ["unavailable"]
    job = cutout_jobs()[0]
    assert job.status == JobStatus.PENDING and job.attempts == 0
    assert "downloading the cut-out model" in job.last_error


def _due_now():
    with SessionLocal() as s:
        s.execute(sa.update(Job).where(Job.kind == JobKind.CUTOUT)
                  .values(run_after=sa.func.now() - sa.text("interval '1 second'")))
        s.commit()


def test_ml_down_before_the_request_waits_without_using_attempts(client, upload, ml):
    _folder, item = setup_item(client, upload)
    client.post(f"/api/items/{item['id']}/cutout", json={})
    ml.raise_on_cutout = httpx.ConnectError("refused")
    for _ in range(5):  # never gives up while the service is simply down
        assert drain() == ["unavailable"]
        _due_now()
    job = cutout_jobs()[0]
    assert job.status == JobStatus.PENDING and job.attempts == 0
    assert "isn't answering" in job.last_error and "ml service" not in job.last_error
    assert not (job.payload or {}).get("ml_drops")


def test_a_cut_out_that_keeps_killing_the_ml_service_fails_in_plain_words(client, upload, ml):
    # RemoteProtocolError = the ml service took the image and died (out of
    # memory); retried a few times, then failed, never retried for ever
    _folder, item = setup_item(client, upload)
    client.post(f"/api/items/{item['id']}/cutout", json={})
    ml.raise_on_cutout = httpx.RemoteProtocolError("Server disconnected")
    results = []
    for _ in range(cutouts.MAX_ML_DROPS):
        results += drain()
        _due_now()
    assert results == ["unavailable"] * (cutouts.MAX_ML_DROPS - 1) + ["failed"]
    job = cutout_jobs()[0]
    assert job.status == JobStatus.FAILED
    assert "most likely out of memory" in job.last_error
    assert "RemoteProtocolError" not in job.last_error
    assert job.payload["ml_drops"] == cutouts.MAX_ML_DROPS
    # Retry in Settings → Jobs starts the count over
    with SessionLocal() as s:
        from kist.services import jobs as jobs_svc

        jobs_svc.retry(s, job.id)
    assert "ml_drops" not in cutout_jobs()[0].payload


def test_a_dropped_request_then_success_makes_the_cut_out(client, upload, ml):
    _folder, item = setup_item(client, upload)
    client.post(f"/api/items/{item['id']}/cutout", json={})
    ml.raise_on_cutout = httpx.ReadError("reset")
    assert drain() == ["unavailable"]
    assert "trying again (1 of 3)" in cutout_jobs()[0].last_error
    ml.raise_on_cutout = None
    _due_now()
    assert drain() == ["done"]
    assert cutout_jobs()[0].payload["result_item_id"]


def test_a_model_too_big_for_the_ml_memory_fails_at_once_with_the_reason(client, upload, ml):
    _folder, item = setup_item(client, upload)
    client.post(f"/api/items/{item['id']}/cutout", json={"model": "birefnet-lite"})
    ml.status = 507
    assert drain() == ["failed"]
    job = cutout_jobs()[0]
    assert job.status == JobStatus.FAILED  # no retries: more tries can't help
    assert "needs about 8 GB" in job.last_error


def test_no_subject_fails_the_job_with_a_reason(client, upload, ml):
    _folder, item = setup_item(client, upload)
    client.post(f"/api/items/{item['id']}/cutout", json={})
    ml.empty = True
    assert drain() == ["failed"]
    assert "no subject" in cutout_jobs()[0].last_error


def test_status_and_turning_it_off(client, upload, ml, monkeypatch):
    st = client.get("/api/cutouts/status").json()
    assert st["enabled"] is True and st["available"] is True
    isnet = next(m for m in st["models"] if m["name"] == "isnet-general-use")
    assert isnet["state"] == "missing" and isnet["size_bytes"] == 178648008
    _folder, item = setup_item(client, upload)
    monkeypatch.setattr(get_settings(), "cutouts", False)
    st = client.get("/api/cutouts/status").json()
    assert st["enabled"] is False and "KIST_CUTOUTS" in st["reason"]
    res = client.post(f"/api/items/{item['id']}/cutout", json={})
    assert res.status_code == 409 and "turned off" in res.text
    assert JobKind.CUTOUT not in ai.worker_kinds()


def _board(client, item_id):
    res = client.post("/api/boards", json={"name": "Comp", "item_ids": [item_id]})
    assert res.status_code in (200, 201), res.text
    return res.json()


def test_board_cut_out_swaps_the_node_and_undo_puts_it_back(client, upload, ml):
    _folder, item = setup_item(client, upload)
    board = _board(client, item["id"])
    node = next(n for n in board["nodes"] if n["type"] == "item")
    res = client.post(f"/api/boards/{board['id']}/nodes/{node['id']}/cutout",
                      json={}, headers={"X-Kist-Batch": "board-cut"})
    assert res.status_code == 202, res.text
    assert res.json()["board"] is None and res.json()["job"]["kind"] == "cutout"
    assert drain() == ["done"]
    new_id = cutout_jobs()[-1].payload["result_item_id"]
    got = client.get(f"/api/boards/{board['id']}").json()
    swapped = next(n for n in got["nodes"] if n["id"] == node["id"])
    assert swapped["item_id"] == new_id and swapped.get("crop") is None
    assert swapped["w"] == node["w"]  # same width, the cut-out's proportions
    assert abs(swapped["h"] / swapped["w"] - 146 / 166) < 0.05

    # undo the request's batch: the original is back on the board, the
    # cut-out is in the trash
    undo = client.post("/api/batches/board-cut/undo").json()
    assert undo["undone"] >= 2, undo
    got = client.get(f"/api/boards/{board['id']}").json()
    back = next(n for n in got["nodes"] if n["id"] == node["id"])
    assert back["item_id"] == item["id"]
    assert client.get(f"/api/items/{new_id}").json()["deleted_at"] is not None


def test_board_cut_out_swaps_at_once_when_a_cutout_exists(client, upload, ml):
    _folder, item = setup_item(client, upload)
    client.post(f"/api/items/{item['id']}/cutout", json={})
    assert drain() == ["done"]
    new_id = cutout_jobs()[-1].payload["result_item_id"]
    board = _board(client, item["id"])
    node = next(n for n in board["nodes"] if n["type"] == "item")
    now = client.post(f"/api/boards/{board['id']}/nodes/{node['id']}/cutout", json={}).json()
    assert now["job"] is None
    assert next(n for n in now["board"]["nodes"] if n["id"] == node["id"])["item_id"] == new_id
    # other settings make a new one
    later = client.post(f"/api/boards/{board['id']}/nodes/{node['id']}/cutout",
                        json={"shadow": True})
    assert later.status_code == 409 and "already a cut-out" in later.text


def test_board_cut_out_leaves_a_node_that_changed_meanwhile(client, upload, ml):
    _folder, item = setup_item(client, upload)
    other = upload("other.png")["item"]
    board = _board(client, item["id"])
    node = next(n for n in board["nodes"] if n["type"] == "item")
    client.post(f"/api/boards/{board['id']}/nodes/{node['id']}/cutout", json={})
    client.patch(f"/api/boards/{board['id']}",
                 json={"update": [{"id": node["id"], "item_id": other["id"]}]})
    assert drain() == ["done"]
    got = client.get(f"/api/boards/{board['id']}").json()
    assert next(n for n in got["nodes"] if n["id"] == node["id"])["item_id"] == other["id"]
    res = client.post(f"/api/boards/{board['id']}/nodes/nope/cutout", json={})
    assert res.status_code == 422


def test_folder_limited_reader_and_scopes(client, upload, ml):
    folder, item = setup_item(client, upload)
    outside = upload("outside.png", data=photo_png(subject=(50, 50, 200, 200)))["item"]
    client.post(f"/api/items/{item['id']}/cutout", json={})
    client.post(f"/api/items/{outside['id']}/cutout", json={})
    assert drain() == ["done", "done"]
    inside_cut, outside_cut = (j.payload["result_item_id"] for j in cutout_jobs())

    tok = client.post("/api/tokens", json={"name": "fam", "scopes": ["read"],
                                           "folder_ids": [folder["id"]]}).json()
    fam = TestClient(client.app, headers={"Authorization": f"Bearer {tok['token']}"})
    assert fam.get(f"/api/items/{inside_cut}/cutout-preview").status_code == 200
    for item_id in (outside_cut, outside["id"]):
        assert fam.get(f"/api/items/{item_id}/cutout-preview").status_code == 404
    # a reader can't make cut-outs or list them; the status is off-limits too
    assert fam.post(f"/api/items/{item['id']}/cutout", json={}).status_code in (403, 404)
    assert fam.get(f"/api/items/{item['id']}/cutouts").status_code == 404
    assert fam.get("/api/cutouts/status").status_code == 404
    # an add-only token can't either; an agent token can
    add = client.post("/api/tokens", json={"name": "add", "scopes": ["add"]}).json()
    adder = TestClient(client.app, headers={"Authorization": f"Bearer {add['token']}"})
    assert adder.post(f"/api/items/{item['id']}/cutout", json={}).status_code == 403
    agent = client.post("/api/tokens", json={"name": "bot", "scopes": ["agent"]}).json()
    bot = TestClient(client.app, headers={"Authorization": f"Bearer {agent['token']}"})
    assert bot.post(f"/api/items/{item['id']}/cutout", json={"shadow": True}).status_code == 202


def test_mcp_cut_out_subject(client, upload, ml):
    _folder, item = setup_item(client, upload)
    headers = {"Accept": "application/json, text/event-stream"}

    def rpc(method, params, id_):
        res = client.post("/mcp", json={"jsonrpc": "2.0", "id": id_, "method": method,
                                         "params": params}, headers=headers)
        assert res.status_code == 200, res.text
        return res.json()

    rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "pytest", "version": "0"}}, 1)
    out = rpc("tools/call", {"name": "cut_out_subject",
                             "arguments": {"item_id": item["id"], "feather": 2}}, 2)["result"]
    assert not out.get("isError"), out
    assert "cutout" in json.dumps(out)
    assert drain() == ["done"]
    bad = rpc("tools/call", {"name": "cut_out_subject",
                             "arguments": {"item_id": item["id"], "board_id": "x"}}, 3)["result"]
    assert bad.get("isError") and "node_id" in json.dumps(bad)


def test_rebuild_restores_cutouts_and_verify_is_clean(client, upload, ml):
    _folder, item = setup_item(client, upload)
    client.post(f"/api/items/{item['id']}/cutout", json={"feather": 3, "shadow": True})
    assert drain() == ["done"]
    new_id = cutout_jobs()[-1].payload["result_item_id"]
    before = client.get(f"/api/items/{new_id}").json()

    assert cli.rebuild_index(wipe=True) == 0

    after = client.get(f"/api/items/{new_id}").json()
    assert after["media"]["cutout"] == before["media"]["cutout"]
    assert after["sha256"] == before["sha256"]
    assert after["modified_at"] == before["modified_at"]
    links = client.get(f"/api/items/{new_id}/links").json()
    assert any(link["item"]["id"] == item["id"] for link in links)
    assert [i["id"] for i in client.get(f"/api/items/{item['id']}/cutouts").json()] == [new_id]

    from kist.services import verify

    with SessionLocal() as s:
        assert cutouts.check(s) == []
        report = verify.run(s)
    assert [p for p in report.problems if "cut-out" in p] == []
    # a lost link is reported
    with SessionLocal() as s:
        s.execute(sa.text("DELETE FROM item_links WHERE src_id = :i"), {"i": new_id})
        s.commit()
        assert any("derived_from" in p for p in cutouts.check(s))


def test_alpha_preview_is_remade_when_missing(client, upload, ml):
    _folder, item = setup_item(client, upload)
    client.post(f"/api/items/{item['id']}/cutout", json={})
    assert drain() == ["done"]
    new_id = cutout_jobs()[-1].payload["result_item_id"]
    d = get_settings().items_dir / new_id
    for p in d.glob("alpha_*.webp"):
        p.unlink()
    assert client.get(f"/api/items/{new_id}/cutout-preview?size=2048").status_code == 200
    assert (d / "alpha_1024.webp").is_file()
    with SessionLocal() as s:
        assert s.get(Item, new_id).media["cutout"]["source_item"] == item["id"]


def test_compose_feather_and_existing_transparency():
    im = Image.new("RGBA", (100, 100), (200, 0, 0, 255))
    im.putalpha(Image.new("L", (100, 100), 128))
    mask = Image.new("L", (50, 50), 0)
    mask.paste(255, (10, 10, 40, 40))
    out, box, _kept = cutouts.compose(im, mask, feather=2)
    a = np.asarray(out.getchannel("A"))
    # the original's half transparency is kept inside the subject
    assert 120 <= a[a.shape[0] // 2, a.shape[1] // 2] <= 130
    assert box[0] < 20 and box[2] > 80
    with pytest.raises(cutouts.NoSubject):
        cutouts.compose(Image.new("RGB", (40, 40)), Image.new("L", (40, 40), 0))


def test_board_export_draws_a_cutout_without_its_background(client, upload, ml):
    _folder, item = setup_item(client, upload)
    client.post(f"/api/items/{item['id']}/cutout", json={})
    assert drain() == ["done"]
    new_id = cutout_jobs()[-1].payload["result_item_id"]
    res = client.post("/api/boards", json={"name": "Export", "item_ids": [new_id],
                                           "canvas": {"bg": "#ff00ff", "grid": 0}})
    board = res.json()
    png = client.get(f"/api/boards/{board['id']}/export?format=png")
    assert png.status_code == 200, png.text
    im = Image.open(io.BytesIO(png.content)).convert("RGB")
    a = np.asarray(im).astype(int)
    magenta = (np.abs(a - [255, 0, 255]).sum(axis=2) < 30).mean()
    subject = (np.abs(a - [40, 90, 170]).sum(axis=2) < 30).mean()
    flattened = (np.abs(a - [24, 24, 27]).sum(axis=2) < 12).mean()
    # the cut-out's transparent margin shows the board; no dark preview card
    # (a card would cover a large share; the few dark pixels allowed are the
    # F4 credits band's text under the board, since the cut-out keeps its credit)
    assert magenta > 0.05 and subject > 0.3 and flattened < 0.01


def test_keep_shadow_finds_none_on_a_busy_background():
    rng = np.random.default_rng(1)
    noise = rng.integers(60, 230, (300, 400, 3), dtype=np.uint8)
    im = Image.fromarray(noise, "RGB")
    im.paste((40, 90, 170), (120, 80, 280, 220))
    mask = Image.new("L", (400, 300), 0)
    mask.paste(255, (120, 80, 280, 220))
    out, _box, kept = cutouts.compose(im, mask, shadow=True)
    assert kept is False
    a = np.asarray(out.getchannel("A"))
    assert int(a[0, 0]) == 0  # nothing of the carpet comes along
    # on a plain light background with a darker strip under it, it does
    plain = Image.open(io.BytesIO(photo_png()))
    _out, _box, kept = cutouts.compose(plain, mask, shadow=True)
    assert kept is True
