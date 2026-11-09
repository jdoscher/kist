"""AI infrastructure: jobs, budget, sidecars, helpers, status, semantic order.

A fake ml service stands in for the real models (deterministic vectors: an
image's vector is its mean colour; the words red/green/blue are the pure
colours), so these tests need neither ONNX Runtime nor model downloads.
tests/test_ai_integration.py runs the real models (KIST_ML_INTEGRATION=1).
"""

import io
import json
from datetime import datetime

import httpx
import pytest
import sqlalchemy as sa
from conftest import png_bytes
from PIL import Image

from kist import cli, worker
from kist.config import get_settings
from kist.db import SessionLocal
from kist.lifecycle import JobKind, JobStatus
from kist.models import ItemEmbedding, ItemText, Job
from kist.services import ai

DIM = 512
COLOURS = {"red": (1.0, 0.0, 0.0), "green": (0.0, 1.0, 0.0), "blue": (0.0, 0.0, 1.0)}


def colour_vector(rgb) -> list[float]:
    vec = [0.0] * DIM
    vec[0], vec[1], vec[2] = rgb
    vec[3] = 0.05  # never all zeros
    return vec


class FakeMl:
    def __init__(self):
        self.calls: list[str] = []
        self.down = False
        self.model = "ViT-B-32__openai"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if self.down:
            raise httpx.ConnectError("ml is down", request=request)
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "model": self.model})
        if path == "/embed/image":
            im = Image.open(io.BytesIO(request.content)).convert("RGB").resize((8, 8))
            px = list(im.getdata())
            rgb = [sum(p[i] for p in px) / len(px) / 255 for i in range(3)]
            return httpx.Response(200, json={"model": self.model, "dim": DIM,
                                             "vector": colour_vector(rgb)})
        if path == "/embed/text":
            text = json.loads(request.content)["text"]
            rgb = COLOURS.get(text.strip().lower(), (0.3, 0.3, 0.3))
            return httpx.Response(200, json={"model": self.model, "dim": DIM,
                                             "vector": colour_vector(rgb)})
        if path == "/ocr":
            im = Image.open(io.BytesIO(request.content))
            return httpx.Response(200, json={
                "model": "fake-ocr", "lang": None,
                "text": f"hello from ocr {im.width}x{im.height}",
                "boxes": [{"box": [[0, 0], [10, 0], [10, 10], [0, 10]], "text": "hello",
                           "score": 0.9}],
            })
        return httpx.Response(404)


@pytest.fixture
def ml(clean, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ml_url", "http://ml.test")
    monkeypatch.setattr(settings, "ai_window", "")
    fake = FakeMl()
    ai.set_transport(httpx.MockTransport(fake))
    yield fake
    ai.set_transport(None)


def drain(runner="worker") -> int:
    """Run every AI job the budget allows, as the worker's AI lane does."""
    done = 0
    while True:
        with SessionLocal() as s:
            jobs = [(j.id, j.kind) for j in ai.claim(s, runner=runner, limit=10)]
        if not jobs:
            return done
        for job_id, kind in jobs:
            worker.run_ai_job(job_id, kind)
            done += 1


def jobs_of(kind=None, status=None):
    with SessionLocal() as s:
        stmt = sa.select(Job).order_by(Job.id)
        if kind:
            stmt = stmt.where(Job.kind == kind)
        if status:
            stmt = stmt.where(Job.status == status)
        return list(s.execute(stmt).scalars())


def solid_png(rgb) -> bytes:
    return png_bytes(color=rgb, size=(320, 240))


def camera_jpeg() -> bytes:
    im = Image.new("RGB", (320, 240), (90, 120, 30))
    exif = Image.Exif()
    exif[0x010F] = "Canon"
    exif[0x0110] = "EOS R5"
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def text_pdf(text: str) -> bytes:
    content = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1, xref)
    return out


def blank_pdf() -> bytes:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    pdf.new_page(300, 200)
    buf = io.BytesIO()
    pdf.save(buf)
    pdf.close()
    return buf.getvalue()


# ---------- pure logic ----------


def test_window_parsing_and_wrapping():
    at = lambda h, m=0: datetime(2026, 11, 9, h, m)  # noqa: E731
    assert ai.window_open("", at(14))
    assert ai.window_open("01:00-06:00", at(1))
    assert ai.window_open("01:00-06:00", at(5, 59))
    assert not ai.window_open("01:00-06:00", at(6))
    assert not ai.window_open("01:00-06:00", at(0, 59))
    # past midnight
    assert ai.window_open("22:00-06:00", at(23))
    assert ai.window_open("22:00-06:00", at(3))
    assert not ai.window_open("22:00-06:00", at(12))
    with pytest.raises(ValueError):
        ai.parse_window("1am-6am")


def test_f16_roundtrip_and_vector_checks():
    vec = [0.5, -0.25, 0.125, 1.0]
    assert ai.unpack_f16(ai.pack_f16(vec)) == vec
    assert len(ai.pack_f16([0.0] * DIM)) == DIM * 2
    with pytest.raises(ai.ResultRejected):
        ai.clean_vector([1.0, 2.0], 512)
    with pytest.raises(ai.ResultRejected):
        ai.clean_vector([float("nan")] * 512, 512)
    with pytest.raises(ai.ResultRejected):
        ai.clean_vector([0.0] * 512, 512)
    v = ai.clean_vector([3.0, 4.0], None)
    assert v == [0.6, 0.8]


def test_migration_0101_explains_a_missing_pgvector(monkeypatch):
    import importlib.util
    from pathlib import Path

    from alembic import op

    path = Path(__file__).parent.parent / "migrations" / "versions" / "0101_ai.py"
    spec = importlib.util.spec_from_file_location("m0101", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class NoVector:
        def execute(self, *a, **k):
            class R:
                def first(self):
                    return None
            return R()

    monkeypatch.setattr(op, "get_bind", lambda: NoVector(), raising=False)
    with pytest.raises(RuntimeError, match="pgvector/pgvector:pg16"):
        mod.upgrade()


def test_migration_made_the_tables_and_the_extension(database):
    from kist.db import engine

    with engine.connect() as conn:
        assert conn.execute(sa.text(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'")).first()
        names = set(conn.execute(sa.text(
            "SELECT indexname FROM pg_indexes WHERE tablename IN "
            "('item_embeddings', 'item_text')")).scalars())
    assert "ix_item_embeddings_hnsw_vit_b_32_openai" in names
    assert "ix_item_text_tsv" in names


# ---------- queueing ----------


def test_nothing_is_queued_when_ai_is_off(client, upload):
    upload("a.png")
    assert jobs_of() == []


def test_new_items_queue_embed_and_ocr_for_screenshots_only(client, upload, ml):
    shot = upload("shot.png", data=solid_png((200, 10, 10)))["item"]
    photo = upload("photo.jpg", data=camera_jpeg(), mime="image/jpeg")["item"]
    kinds = {(j.payload["item_id"], j.kind) for j in jobs_of()}
    assert (shot["id"], JobKind.EMBED) in kinds
    assert (shot["id"], JobKind.OCR) in kinds
    assert (photo["id"], JobKind.EMBED) in kinds
    assert (photo["id"], JobKind.OCR) not in kinds  # a camera took it
    assert all(not j.payload["backfill"] for j in jobs_of())


def test_worker_results_land_in_sidecars_and_rows(client, upload, ml, clean):
    item = upload("shot.png", data=solid_png((10, 200, 10)))["item"]
    assert drain() == 2
    assert {j.status for j in jobs_of()} == {JobStatus.DONE}

    ai_dir = clean / "items" / item["id"] / "ai"
    f16 = ai_dir / "embed-ViT-B-32__openai.f16"
    assert f16.stat().st_size == DIM * 2
    info = json.loads((ai_dir / "embed-ViT-B-32__openai.json").read_text())
    assert info["model"] == "ViT-B-32__openai" and info["dim"] == DIM
    assert info["kist_version"] and info["created_at"]
    vec = ai.unpack_f16(f16.read_bytes())
    assert vec[1] > 0.9  # green, normalised

    ocr = json.loads((ai_dir / "ocr.json").read_text())
    assert ocr["text"].startswith("hello from ocr") and ocr["source"] == "ocr"
    assert ocr["boxes"][0]["text"] == "hello"
    with SessionLocal() as s:
        row = s.get(ItemEmbedding, (item["id"], "ViT-B-32__openai"))
        assert row.dim == DIM
        assert s.get(ItemText, (item["id"], "ocr")).text == ocr["text"]
    # nothing to do twice
    with SessionLocal() as s:
        assert ai.backfill(s) == {"embed": 0, "ocr": 0}


def test_pdf_text_layer_is_read_without_ocr(client, upload, ml):
    item = upload("doc.pdf", data=text_pdf("Zero Trust diagram"), mime="application/pdf")["item"]
    drain()
    with SessionLocal() as s:
        row = s.get(ItemText, (item["id"], "pdf"))
    assert row is not None and "Zero Trust diagram" in row.text
    assert row.model == ai.PDF_TEXT_MODEL
    assert "/ocr" not in ml.calls


def test_scanned_pdf_pages_go_to_ocr(client, upload, ml):
    item = upload("scan.pdf", data=blank_pdf(), mime="application/pdf")["item"]
    drain()
    with SessionLocal() as s:
        row = s.get(ItemText, (item["id"], "ocr"))
    assert row is not None and row.text.startswith("hello from ocr")
    assert ml.calls.count("/ocr") == 1


# ---------- budget ----------


def test_ai_jobs_wait_while_light_jobs_are_pending(client, upload, ml):
    upload("a.png")
    with SessionLocal() as s:
        s.add(Job(kind=JobKind.INGEST, payload={"path": "/nowhere", "filename": "x"}))
        s.commit()
        assert ai.claim(s, runner="worker", limit=10) == []
        s.execute(sa.delete(Job).where(Job.kind == JobKind.INGEST))
        s.commit()
        assert len(ai.claim(s, runner="worker", limit=10)) == 2


def test_backfill_waits_for_the_window_but_new_items_dont(client, upload, ml, monkeypatch):
    monkeypatch.setattr(ai.get_settings(), "ai_enabled", False)
    old = upload("old.png")["item"]
    monkeypatch.setattr(ai.get_settings(), "ai_enabled", True)
    with SessionLocal() as s:
        assert ai.backfill(s, (JobKind.EMBED,)) == {"embed": 1}
    new = upload("new.png")["item"]
    # a window that is closed right now
    now = datetime.now()
    start = f"{(now.hour + 2) % 24:02d}:00"
    end = f"{(now.hour + 3) % 24:02d}:00"
    monkeypatch.setattr(get_settings(), "ai_window", f"{start}-{end}")
    drain()
    done = {(j.payload["item_id"], j.kind) for j in jobs_of(status=JobStatus.DONE)}
    waiting = {(j.payload["item_id"], j.kind) for j in jobs_of(status=JobStatus.PENDING)}
    assert (new["id"], JobKind.EMBED) in done
    assert waiting == {(old["id"], JobKind.EMBED)}
    monkeypatch.setattr(get_settings(), "ai_window", "")
    drain()
    assert jobs_of(status=JobStatus.PENDING) == []


def test_backfill_limit_and_cli(client, upload, ml, monkeypatch, capsys):
    monkeypatch.setattr(get_settings(), "ai_enabled", False)
    for _ in range(3):
        upload()
    assert cli.ai_backfill("embed", 2) == 0
    assert "queued: 2 embed" in capsys.readouterr().out
    assert len(jobs_of(JobKind.EMBED)) == 2
    assert cli.ai_backfill(None, None) == 0
    assert len(jobs_of(JobKind.EMBED)) == 3  # no duplicates of open jobs
    assert len(jobs_of(JobKind.OCR)) == 3


def test_ml_down_puts_jobs_back_without_using_attempts(client, upload, ml):
    upload("a.png")
    ml.down = True
    lane = worker.AiLane(1)
    assert lane.fill() == 1
    lane.wait(5)
    assert lane.fill() == 0  # paused: the ml service is down
    job = jobs_of(JobKind.EMBED)[0]
    assert job.status == JobStatus.PENDING and job.attempts == 0
    assert "can't be reached" in job.last_error
    assert job.run_after > datetime.now(job.run_after.tzinfo)


def test_wrong_model_on_the_ml_service_is_an_error(client, upload, ml):
    upload("a.png")
    ml.model = "ViT-B-16-SigLIP2__webli"
    drain()
    job = jobs_of(JobKind.EMBED)[0]
    assert "KIST_ML_MODEL" in job.last_error
    with SessionLocal() as s:
        assert s.execute(sa.select(sa.func.count()).select_from(ItemEmbedding)).scalar() == 0


# ---------- helpers ----------


def test_helper_claims_reports_and_shows_in_status(client, upload, ml):
    item = upload("shot.png", data=solid_png((0, 0, 220)))["item"]
    res = client.post("/api/ai/jobs/claim", json={"kinds": ["embed"], "max": 5},
                      headers={"X-Kist-Helper": "quarry"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["model"] == "ViT-B-32__openai"
    [job] = body["jobs"]
    assert job["kind"] == "embed" and job["item_id"] == item["id"]
    img = client.get(job["image_url"], headers={"X-Kist-Helper": "quarry"})
    assert img.status_code == 200 and img.headers["content-type"] == "image/webp"
    # only the helper holding the job gets its image
    assert client.get(job["image_url"], headers={"X-Kist-Helper": "other"}).status_code == 409

    # someone else's job, a wrong-sized vector
    other = client.post(f"/api/ai/jobs/{job['id']}/result",
                        json={"vector": [1.0] * DIM}, headers={"X-Kist-Helper": "other"})
    assert other.status_code == 409
    bad = client.post(f"/api/ai/jobs/{job['id']}/result",
                      json={"vector": [1.0] * 10}, headers={"X-Kist-Helper": "quarry"})
    assert bad.status_code == 422
    wrong_model = client.post(f"/api/ai/jobs/{job['id']}/result",
                              json={"vector": [1.0] * DIM, "model": "ViT-B-16-SigLIP2__webli"},
                              headers={"X-Kist-Helper": "quarry"})
    assert wrong_model.status_code == 422

    ok = client.post(f"/api/ai/jobs/{job['id']}/result",
                     json={"vector": colour_vector((0, 0, 1)), "model": "ViT-B-32__openai"},
                     headers={"X-Kist-Helper": "quarry"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "done"
    assert (get_settings().items_dir / item["id"] / "ai" /
            "embed-ViT-B-32__openai.f16").is_file()

    status = client.get("/api/ai/status").json()
    assert status["embedded"] == 1
    assert status["ml_ok"] is True and status["model"] == "ViT-B-32__openai"
    assert status["pending"] == 1  # the OCR job is still waiting
    assert status["window"] is None
    [helper] = status["helpers"]
    assert helper["name"] == "quarry" and helper["jobs_done"] == 1 and helper["last_seen"]


def test_helper_ocr_result_and_error_report(client, upload, ml):
    upload("shot.png", data=png_bytes(size=(3000, 2000)))
    jobs = client.post("/api/ai/jobs/claim", json={"kinds": ["ocr", "embed"], "max": 5},
                       headers={"X-Kist-Helper": "mac"}).json()["jobs"]
    by_kind = {j["kind"]: j for j in jobs}
    assert set(by_kind) == {"ocr", "embed"}
    # OCR gets the large screen copy, embed a thumbnail
    widths = {}
    for kind, job in by_kind.items():
        img = client.get(job["image_url"], headers={"X-Kist-Helper": "mac"})
        assert img.status_code == 200, img.text
        widths[kind] = Image.open(io.BytesIO(img.content)).width
    assert widths["ocr"] == 2048 and widths["embed"] <= 1024
    r = client.post(f"/api/ai/jobs/{by_kind['ocr']['id']}/result",
                    json={"text": "Orvis receipt", "model": "rapidocr-test"},
                    headers={"X-Kist-Helper": "mac"})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/ai/jobs/{by_kind['embed']['id']}/result",
                    json={"error": "model download failed"}, headers={"X-Kist-Helper": "mac"})
    assert r.status_code == 200
    assert r.json()["status"] == "pending"  # retried later
    assert "model download failed" in r.json()["last_error"]
    with SessionLocal() as s:
        assert s.execute(sa.select(ItemText.text)).scalar() == "Orvis receipt"


def test_helpers_never_get_pdf_jobs(client, upload, ml):
    upload("doc.pdf", data=text_pdf("hello"), mime="application/pdf")
    jobs = client.post("/api/ai/jobs/claim", json={"kinds": ["ocr"], "max": 5}).json()["jobs"]
    assert jobs == []
    assert len(jobs_of(JobKind.OCR, JobStatus.PENDING)) == 1


def test_expired_helper_lease_goes_back_to_the_queue(client, upload, ml):
    from kist.services import jobs as jobs_svc

    upload("a.png")
    client.post("/api/ai/jobs/claim", json={"kinds": ["embed"], "max": 1},
                headers={"X-Kist-Helper": "gone"})
    with SessionLocal() as s:
        assert jobs_svc.reap_stale(s, lease_seconds=-1) == 1
    job = jobs_of(JobKind.EMBED)[0]
    assert job.status == JobStatus.PENDING
    late = client.post(f"/api/ai/jobs/{job.id}/result", json={"vector": [1.0] * DIM},
                       headers={"X-Kist-Helper": "gone"})
    assert late.status_code == 409


def test_kist_helper_client_end_to_end(client, upload, ml):
    """The real `kist helper` loop against this server, with stand-in
    engines (the models themselves are covered by the integration test)."""
    from kist.helper import Helper

    item = upload("shot.png", data=solid_png((220, 0, 0)))["item"]

    class Clip:
        model = "ViT-B-32__openai"

        def embed_image(self, data):
            return colour_vector((1, 0, 0))

    class Ocr:
        def ocr(self, data):
            Image.open(io.BytesIO(data)).verify()
            return {"text": "from the helper", "model": "fake", "lang": None, "boxes": []}

    def forward(request: httpx.Request) -> httpx.Response:
        r = client.request(request.method, request.url.raw_path.decode(),
                           content=request.content, headers=dict(request.headers))
        return httpx.Response(r.status_code, headers=r.headers, content=r.content)

    token = client.headers["Authorization"].split(" ", 1)[1]
    h = Helper("http://testserver", token, name="testmac", kinds=["embed", "ocr"], batch=4,
               device="cpu", threads=1, cache_dir=get_settings().library_dir,
               transport=httpx.MockTransport(forward))
    h._engines = lambda model: (Clip(), Ocr())
    assert h.run(once=True) == 0
    assert h.done == 2
    with SessionLocal() as s:
        assert s.get(ItemText, (item["id"], "ocr")).text == "from the helper"
        assert s.get(ItemEmbedding, (item["id"], "ViT-B-32__openai")) is not None


# ---------- semantic order ----------


def test_semantic_orders_by_meaning(client, upload, ml):
    ids = {}
    for name, rgb in (("r.png", (230, 10, 10)), ("g.png", (10, 230, 10)),
                      ("b.png", (10, 10, 230))):
        ids[name] = upload(name, data=solid_png(rgb), tags=name[0])["item"]["id"]
    unembedded = upload("late.png", data=solid_png((120, 120, 120)))["item"]["id"]
    drain()
    with SessionLocal() as s:  # one item without an embedding: it sorts last
        s.execute(sa.delete(ItemEmbedding).where(ItemEmbedding.item_id == unembedded))
        s.commit()
    page = client.get("/api/items", params={"semantic": "green"}).json()
    order = [i["id"] for i in page["items"]]
    assert order[0] == ids["g.png"]
    assert order[-1] == unembedded
    assert page["total"] == 4
    # offset cursor
    first = client.get("/api/items", params={"semantic": "blue", "limit": 1}).json()
    assert first["items"][0]["id"] == ids["b.png"]
    second = client.get("/api/items", params={"semantic": "blue", "limit": 1,
                                              "cursor": first["next_cursor"]}).json()
    assert second["items"][0]["id"] != ids["b.png"]
    # a keyword cursor isn't a semantic one
    bad = client.get("/api/items", params={"semantic": "blue", "cursor": "abc"})
    assert bad.status_code == 400
    # filters still apply
    only = client.get("/api/items", params={"semantic": "red", "tags": "g"}).json()
    assert [i["id"] for i in only["items"]] == [ids["g.png"]]


def test_semantic_needs_the_ml_service_once_there_are_vectors(client, upload, ml):
    # no vectors yet: plain order, the ml service isn't asked
    upload("a.png")
    assert client.get("/api/items", params={"semantic": "red"}).status_code == 200
    assert "/embed/text" not in ml.calls
    drain()
    ml.down = True
    res = client.get("/api/items", params={"semantic": "something new"})
    assert res.status_code == 503
    assert "ml service" in res.json()["detail"]


# ---------- files are the truth ----------


def test_rebuild_reads_sidecars_and_never_recomputes(client, upload, ml):
    item = upload("shot.png", data=solid_png((10, 10, 230)))["item"]
    pdf = upload("doc.pdf", data=text_pdf("Kist sidecars"), mime="application/pdf")["item"]
    drain()
    ml.calls.clear()
    ml.down = True  # any recompute would fail

    assert cli.rebuild_index(wipe=True) == 0
    with SessionLocal() as s:
        row = s.execute(sa.select(ItemEmbedding).where(
            ItemEmbedding.item_id == item["id"])).scalar_one()
        assert row.model == "ViT-B-32__openai" and row.dim == DIM
        vec = s.execute(sa.select(ItemEmbedding.vector).where(
            ItemEmbedding.item_id == item["id"])).scalar_one()
        assert vec[2] > 0.9
        assert "Kist sidecars" in s.get(ItemText, (pdf["id"], "pdf")).text
        assert s.get(ItemText, (item["id"], "ocr")) is not None
    assert ml.calls == []
    with SessionLocal() as s:
        assert ai.backfill(s) == {"embed": 0, "ocr": 0}


def test_verify_notices_missing_and_unloaded_sidecars(client, upload, ml, capsys):
    item = upload("shot.png")["item"]
    drain()
    assert cli.verify() == 0
    ai_dir = get_settings().items_dir / item["id"] / "ai"
    (ai_dir / "embed-ViT-B-32__openai.f16").unlink()
    (ai_dir / "ocr.json").unlink()
    assert cli.verify() == 2
    out = capsys.readouterr().out
    assert "embedding (ViT-B-32__openai) has no sidecar file" in out
    assert "ai/ocr.json" in out
    assert cli.ai_sidecars() == 0
    assert cli.verify() == 0
    assert len(ai.unpack_f16((ai_dir / "embed-ViT-B-32__openai.f16").read_bytes())) == DIM
    # a sidecar the database doesn't have
    with SessionLocal() as s:
        s.execute(sa.delete(ItemEmbedding))
        s.commit()
    assert cli.verify() == 2
    assert "not in the database" in capsys.readouterr().out
    assert cli.rebuild_index(wipe=False) == 0
    assert cli.verify() == 0


def test_hard_delete_removes_ai_rows(client, upload, ml):
    item = upload("a.png")["item"]
    drain()
    client.delete(f"/api/items/{item['id']}")
    assert client.delete(f"/api/items/{item['id']}?hard=true").status_code == 200
    with SessionLocal() as s:
        assert s.execute(sa.select(sa.func.count()).select_from(ItemEmbedding)).scalar() == 0
        assert s.execute(sa.select(sa.func.count()).select_from(ItemText)).scalar() == 0


def test_result_for_a_purged_item_is_dropped_quietly(client, upload, ml):
    item = upload("a.png")["item"]
    client.delete(f"/api/items/{item['id']}")
    client.delete(f"/api/items/{item['id']}?hard=true")
    drain()
    assert {j.status for j in jobs_of()} == {JobStatus.DONE}


# ---------- status and MCP ----------


def test_status_without_an_ml_service(client):
    status = client.get("/api/ai/status").json()
    assert status["ml_ok"] is False and status["ml_url_set"] is False
    assert status["embedded"] == 0 and status["pending"] == 0
    assert status["helpers"] == []


def test_mcp_ai_status(client, upload, ml):
    from test_mcp import call, init

    upload("a.png")
    drain()
    init(client)
    result = call(client, "ai_status", {})
    data = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    assert data["embedded"] == 1 and data["model"] == "ViT-B-32__openai"


def test_worker_ai_lane_runs_jobs_on_its_threads(client, upload, ml):
    upload("a.png")
    lane = worker.AiLane(2)
    assert lane.fill() == 2
    while lane.wait(5):
        pass
    assert {j.status for j in jobs_of()} == {JobStatus.DONE}
