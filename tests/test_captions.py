"""Captions: the four providers against a local stub server (no real or paid
calls), the caption job, edits as events, undo, sidecars and rebuild, the
helper, and search over captions."""

import base64
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import sqlalchemy as sa
from PIL import Image
from test_ai import drain, jobs_of, ml, solid_png  # noqa: F401  (ml is a fixture)
from test_mcp import call, init
from test_tokens import as_token, make_token

from kist import cli
from kist.config import get_settings
from kist.db import SessionLocal
from kist.helper import Helper
from kist.lifecycle import JobStatus
from kist.models import ItemText
from kist.services import ai, captions

CAPTION = "A bright red square on a plain background."


class Stub:
    """A tiny HTTP server that answers like Ollama, OpenAI, Anthropic and
    Gemini, and records every request."""

    def __init__(self):
        self.requests: list[dict] = []
        self.status = 200
        self.reply_text = CAPTION
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append({"path": self.path, "headers": dict(self.headers),
                                      "body": body})
                if stub.status != 200:
                    out = {"error": {"message": "invalid x-api-key"}}
                elif self.path == "/api/chat":
                    out = {"message": {"role": "assistant", "content": stub.reply_text}}
                elif self.path == "/v1/chat/completions":
                    out = {"choices": [{"message": {"content": stub.reply_text}}]}
                elif self.path == "/v1/messages":
                    out = {"content": [{"type": "text", "text": stub.reply_text}]}
                elif self.path.endswith(":generateContent"):
                    out = {"candidates": [{"content": {"parts": [{"text": stub.reply_text}]}}]}
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(out).encode()
                self.send_response(stub.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.close()


@pytest.fixture
def ollama(stub, ml, monkeypatch):  # noqa: F811
    settings = get_settings()
    monkeypatch.setattr(settings, "caption_provider", "ollama")
    monkeypatch.setattr(settings, "caption_url", stub.url)
    monkeypatch.setattr(settings, "caption_model", "qwen2.5vl:3b")
    return stub


def jpeg_ok(b64: str) -> bool:
    with Image.open(io.BytesIO(base64.b64decode(b64))) as im:
        return im.format == "JPEG"


# ---------- providers ----------


@pytest.mark.parametrize("provider", ["ollama", "openai", "anthropic", "gemini"])
def test_providers_speak_their_apis(stub, provider):
    url = stub.url if provider == "ollama" else stub.url + (
        "/v1beta" if provider == "gemini" else "/v1")
    cfg = captions.ProviderConfig(provider=provider, model="m-1", url=url, api_key="sk-test",
                                  prompt="Describe it.")
    image = captions.to_jpeg(solid_png((200, 20, 20)))
    assert captions.make_provider(cfg).caption(image, "image/jpeg") == CAPTION
    req = stub.requests[-1]
    body, headers = req["body"], {k.lower(): v for k, v in req["headers"].items()}
    if provider == "ollama":
        assert req["path"] == "/api/chat" and body["model"] == "m-1"
        assert body["stream"] is False
        msg = body["messages"][0]
        assert msg["content"] == "Describe it." and jpeg_ok(msg["images"][0])
        assert "authorization" not in headers
    elif provider == "openai":
        assert req["path"] == "/v1/chat/completions"
        assert headers["authorization"] == "Bearer sk-test"
        parts = body["messages"][0]["content"]
        assert parts[0] == {"type": "text", "text": "Describe it."}
        data_url = parts[1]["image_url"]["url"]
        assert data_url.startswith("data:image/jpeg;base64,")
        assert jpeg_ok(data_url.split(",", 1)[1])
    elif provider == "anthropic":
        assert req["path"] == "/v1/messages"
        assert headers["x-api-key"] == "sk-test" and headers["anthropic-version"]
        img = body["messages"][0]["content"][0]
        assert img["source"]["media_type"] == "image/jpeg" and jpeg_ok(img["source"]["data"])
        assert body["max_tokens"] > 0
    else:
        assert req["path"] == "/v1beta/models/m-1:generateContent"
        assert headers["x-goog-api-key"] == "sk-test"
        parts = body["contents"][0]["parts"]
        assert parts[0]["inline_data"]["mime_type"] == "image/jpeg"
        assert jpeg_ok(parts[0]["inline_data"]["data"])
        assert parts[1] == {"text": "Describe it."}


def test_provider_errors_are_readable(stub):
    cfg = captions.ProviderConfig(provider="anthropic", model="m", url=stub.url + "/v1",
                                  api_key="bad")
    stub.status = 401
    with pytest.raises(captions.CaptionError, match="401: invalid x-api-key"):
        captions.make_provider(cfg).caption(b"x", "image/jpeg")
    stub.status = 200
    stub.reply_text = "   "
    with pytest.raises(captions.CaptionError, match="no caption"):
        captions.make_provider(cfg).caption(b"x", "image/jpeg")
    gone = captions.ProviderConfig(provider="ollama", model="m", url="http://127.0.0.1:9")
    with pytest.raises(captions.CaptionError, match="can't be reached"):
        captions.make_provider(gone).caption(b"x", "image/jpeg")


def test_configuration_rules(monkeypatch):
    settings = get_settings()
    assert not captions.enabled() and captions.status()["provider"] is None
    monkeypatch.setattr(settings, "caption_provider", "openai")
    monkeypatch.setattr(settings, "caption_model", "gpt-4o-mini")
    assert captions.enabled() and not captions.worker_can_run()  # no key
    monkeypatch.setattr(settings, "caption_api_key", "sk-x")
    assert captions.worker_can_run()
    st = captions.status()
    assert st["leaves_house"] and "sent to OpenAI" in st["warning"]
    monkeypatch.setattr(settings, "caption_provider", "ollama")
    monkeypatch.setattr(settings, "caption_url", "")
    assert captions.enabled() and not captions.worker_can_run()  # waits for a helper
    assert captions.status()["runs_on"] == "helper" and captions.status()["warning"] is None
    monkeypatch.setattr(settings, "caption_provider", "clippy")
    assert not captions.enabled()


# ---------- the job, the item, search ----------


def test_caption_on_demand_lands_everywhere(client, upload, ollama):
    item = upload("square.png", data=solid_png((200, 20, 20)))["item"]
    drain()  # embed + ocr; no caption yet (off by default for new items)
    assert client.get(f"/api/items/{item['id']}").json()["caption"] is None
    res = client.post(f"/api/items/{item['id']}/caption")
    assert res.status_code == 202, res.text
    assert res.json()["provider"] == "ollama" and res.json()["warning"] is None
    # asking twice reuses the open job
    assert client.post(f"/api/items/{item['id']}/caption").json()["job_id"] == res.json()["job_id"]
    assert drain() == 1
    got = client.get(f"/api/items/{item['id']}").json()
    assert got["caption"] == CAPTION
    side = json.loads((captions.sidecar_path(item["id"])).read_text())
    assert side["text"] == CAPTION and side["provider"] == "ollama" and not side["edited"]
    assert side["model"] == "qwen2.5vl:3b"
    hist = client.get(f"/api/items/{item['id']}/history").json()
    assert hist[0]["kind"] == "caption" and hist[0]["after"] == {"caption": CAPTION}
    assert hist[0]["actor"] == "worker"
    # keyword search reads captions (with a snippet); text-in-image doesn't
    page = client.get("/api/items", params={"q": "bright square"}).json()
    assert [i["id"] for i in page["items"]] == [item["id"]]
    assert "<<bright>>" in page["items"][0]["match"]["snippet"]
    assert client.get("/api/items", params={"text": "bright"}).json()["items"] == []
    # OCR text is kept beside the caption
    with SessionLocal() as s:
        sources = set(s.execute(sa.select(ItemText.source)
                                .where(ItemText.item_id == item["id"])).scalars())
    assert sources == {"ocr", "caption"}
    assert client.get("/api/ai/status").json()["captioned"] == 1


def test_editing_a_caption_is_an_event_and_undoable(client, upload, ollama):
    item = upload("square.png", data=solid_png((200, 20, 20)))["item"]
    client.post(f"/api/items/{item['id']}/caption")
    drain()
    res = client.patch(f"/api/items/{item['id']}", json={"caption": "  My red  tile. "},
                       headers={"X-Kist-Batch": "edit-1"})
    assert res.status_code == 200 and res.json()["caption"] == "My red tile."
    assert res.json()["modified_at"] > item["modified_at"]
    side = json.loads(captions.sidecar_path(item["id"]).read_text())
    assert side["edited"] and side["model"] == "qwen2.5vl:3b" and side["text"] == "My red tile."
    ev = client.get(f"/api/items/{item['id']}/history").json()[0]
    assert ev["kind"] == "caption" and ev["before"] == {"caption": CAPTION}
    undo = client.post("/api/batches/edit-1/undo")
    assert undo.status_code == 200 and undo.json()["undone"] == 1, undo.text
    assert client.get(f"/api/items/{item['id']}").json()["caption"] == CAPTION
    # "" removes it, and its file
    assert client.patch(f"/api/items/{item['id']}", json={"caption": ""}).json()["caption"] is None
    assert not captions.sidecar_path(item["id"]).exists()
    too_long = client.patch(f"/api/items/{item['id']}", json={"caption": "x" * 2001})
    assert too_long.status_code == 422


def test_rebuild_restores_captions_and_verify_watches_the_file(client, upload, ollama, capsys):
    item = upload("square.png", data=solid_png((200, 20, 20)))["item"]
    client.post(f"/api/items/{item['id']}/caption")
    drain()
    ollama.status = 500  # nothing may be recomputed
    assert cli.rebuild_index(wipe=True) == 0
    assert client.get(f"/api/items/{item['id']}").json()["caption"] == CAPTION
    captions.sidecar_path(item["id"]).unlink()
    capsys.readouterr()
    assert cli.verify() != 0
    out = capsys.readouterr()
    assert "caption has no sidecar" in out.out + out.err
    assert cli.ai_sidecars() == 0
    assert json.loads(captions.sidecar_path(item["id"]).read_text())["text"] == CAPTION
    assert cli.verify() == 0


def test_captions_off_and_the_read_scope(client, upload, ml):  # noqa: F811
    item = upload("square.png")["item"]
    res = client.post(f"/api/items/{item['id']}/caption")
    assert res.status_code == 422 and "KIST_CAPTION_PROVIDER" in res.json()["detail"]
    reader = as_token(client, make_token(client, "r", ["read"])["token"])
    assert reader.patch(f"/api/items/{item['id']}", json={"caption": "x"}).status_code == 403


def test_a_failing_provider_fails_the_job_with_its_reason(client, upload, ollama):
    item = upload("square.png")["item"]
    drain()
    client.post(f"/api/items/{item['id']}/caption")
    ollama.status = 401
    drain()
    job = jobs_of("caption")[0]
    assert job.status == JobStatus.PENDING and "401" in job.last_error  # retried later
    assert client.get(f"/api/items/{item['id']}").json()["caption"] is None


def test_backfill_and_auto(client, upload, ollama, monkeypatch, capsys):
    a = upload("a.png")["item"]
    upload("b.png")
    with SessionLocal() as s:
        assert ai.backfill(s)["embed"] == 0  # the default kinds never include captions
    assert jobs_of("caption") == []
    assert cli.ai_backfill("caption", None) == 0
    assert len(jobs_of("caption")) == 2
    assert all(j.payload["backfill"] for j in jobs_of("caption"))
    assert cli.ai_backfill("caption", None) == 0  # open jobs aren't doubled
    assert len(jobs_of("caption")) == 2
    drain()
    assert client.get(f"/api/items/{a['id']}").json()["caption"] == CAPTION
    monkeypatch.setattr(get_settings(), "caption_auto", True)
    c = upload("c.png")["item"]
    assert [j.payload["item_id"] for j in jobs_of("caption", JobStatus.PENDING)] == [c["id"]]
    monkeypatch.setattr(get_settings(), "caption_provider", "")
    assert cli.ai_backfill("caption", None) == 2


def test_a_mac_helper_runs_captions_with_its_own_ollama(client, upload, ml, stub, monkeypatch):  # noqa: F811
    settings = get_settings()
    monkeypatch.setattr(settings, "caption_provider", "ollama")
    monkeypatch.setattr(settings, "caption_url", "")  # the NAS has no Ollama
    monkeypatch.setattr(settings, "caption_model", "qwen2.5vl:3b")
    item = upload("square.png", data=solid_png((200, 20, 20)))["item"]
    drain()
    client.post(f"/api/items/{item['id']}/caption")
    assert "caption" not in ai.worker_kinds()  # the worker can't run it
    token = make_token(client, "mac", ["helper"])["token"]
    helper_client = as_token(client, token)

    import httpx

    def forward(request: httpx.Request) -> httpx.Response:
        r = helper_client.request(request.method, request.url.path, content=request.content,
                                  headers=dict(request.headers))
        return httpx.Response(r.status_code, headers=r.headers, content=r.content)

    helper = Helper("http://kist.test", token, name="mac", kinds=["caption"], batch=4,
                    device="cpu", threads=1, cache_dir=get_settings().library_dir,
                    transport=httpx.MockTransport(forward), caption_url=stub.url)
    assert helper.run(once=True) == 0 and helper.done == 1
    req = stub.requests[-1]
    assert req["body"]["model"] == "qwen2.5vl:3b"
    assert jpeg_ok(req["body"]["messages"][0]["images"][0])
    assert client.get(f"/api/items/{item['id']}").json()["caption"] == CAPTION
    side = json.loads(captions.sidecar_path(item["id"]).read_text())
    assert side["provider"] == "helper:mac"
    # helpers that didn't ask for captions never get them
    client.post(f"/api/items/{item['id']}/caption")
    res = helper_client.post("/api/ai/jobs/claim", json={"name": "mac2"}).json()
    assert res["jobs"] == []


def test_mcp_update_item_sets_a_caption(client, upload):
    item = upload("square.png")["item"]
    init(client)
    out = call(client, "update_item", {"id": item["id"], "caption": "A test tile."})
    data = out.get("structuredContent") or json.loads(out["content"][0]["text"])
    assert data["caption"] == "A test tile."
    status = call(client, "ai_status", {})
    data = status.get("structuredContent") or json.loads(status["content"][0]["text"])
    assert data["captions"]["provider"] is None
