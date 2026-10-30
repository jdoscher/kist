import pytest
from conftest import png_bytes

from kist.config import get_settings
from kist.services import ingest


@pytest.fixture
def fake_web(monkeypatch):
    """Serve canned responses instead of touching the network."""
    pages: dict[str, tuple[bytes, str]] = {}

    def fake_download(url):
        if url not in pages:
            raise ingest.httpx.HTTPStatusError(
                "404", request=ingest.httpx.Request("GET", url),
                response=ingest.httpx.Response(404),
            )
        body, ctype = pages[url]
        staging = get_settings().staging_dir
        staging.mkdir(parents=True, exist_ok=True)
        path = staging / f"dl-{ingest.new_ulid()}"
        path.write_bytes(body)
        return path, url.rsplit("/", 1)[-1] or "download", ctype

    monkeypatch.setattr(ingest, "_download", fake_download)
    return pages


def test_direct_image_url(client, fake_web):
    fake_web["https://img.test/cat.png"] = (png_bytes(), "image/png")
    res = client.post("/api/items", json={"url": "https://img.test/cat.png"})
    assert res.status_code == 201
    assert res.json()["item"]["source_url"] == "https://img.test/cat.png"


def test_page_url_uses_its_preview_image(client, fake_web):
    fake_web["https://blog.test/post"] = (
        b'<html><meta property="og:image" content="/hero.png"></html>', "text/html",
    )
    fake_web["https://blog.test/hero.png"] = (png_bytes((9, 9, 9)), "image/png")
    res = client.post("/api/items", json={"url": "https://blog.test/post"})
    assert res.status_code == 201
    item = res.json()["item"]
    assert item["kind"] == "image"
    assert item["source_url"] == "https://blog.test/post"


def test_page_without_image_is_rejected(client, fake_web):
    fake_web["https://plain.test/"] = (b"<html><p>hi</p></html>", "text/html")
    res = client.post("/api/items", json={"url": "https://plain.test/"})
    assert res.status_code == 422


def test_failed_download_is_a_bad_gateway(client, fake_web):
    res = client.post("/api/items", json={"url": "https://missing.test/x.png"})
    assert res.status_code == 502
