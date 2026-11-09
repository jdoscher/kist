"""Performance fixes from the 2026-11-09 review: full-screen copies, cache
headers, compression."""

import io

import pytest
from conftest import png_bytes

FOREVER = "private, max-age=31536000, immutable"


def test_large_images_get_a_2048_copy(upload, clean):
    big = upload("big.png", png_bytes(size=(3000, 2000)))["item"]
    small = upload("small.png", png_bytes(size=(800, 600)))["item"]
    copy = clean / "items" / big["id"] / "display_2048.webp"
    assert copy.is_file()
    from PIL import Image

    assert max(Image.open(copy).size) == 2048
    assert not (clean / "items" / small["id"] / "display_2048.webp").exists()


def test_thumbnails_cache_forever_only_when_versioned(client, upload):
    item = upload("big.png", png_bytes(size=(3000, 2000)))["item"]
    v = item["preview_rev"]
    assert item["thumb_url"].endswith(f"?v={v}")
    url = f"/api/items/{item['id']}/thumb"
    assert client.get(f"{url}?size=512&v={v}").headers["cache-control"] == FOREVER
    # no version, or an old one: the browser has to check back
    assert client.get(f"{url}?size=512").headers["cache-control"] == "private, no-cache"
    assert client.get(f"{url}?size=512&v={v + 1}").headers["cache-control"] == "private, no-cache"
    screen = client.get(f"{url}?size=2048&v={v}")
    assert screen.status_code == 200 and screen.headers["cache-control"] == FOREVER
    from PIL import Image

    assert max(Image.open(io.BytesIO(screen.content)).size) == 2048


def test_missing_screen_copy_falls_back_without_long_caching(client, upload, clean):
    item = upload("big.png", png_bytes(size=(3000, 2000)))["item"]
    (clean / "items" / item["id"] / "display_2048.webp").unlink()
    r = client.get(f"/api/items/{item['id']}/thumb?size=2048&v={item['preview_rev']}")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "private, no-cache"  # it's the 1024 stand-in


def test_regen_adds_only_the_missing_screen_copy(client, upload, clean):
    from kist import cli

    item = upload("big.png", png_bytes(size=(3000, 2000)))["item"]
    item_dir = clean / "items" / item["id"]
    (item_dir / "display_2048.webp").unlink()
    thumb_before = (item_dir / "thumb_256.webp").stat().st_mtime_ns
    assert cli.regen_previews(False) == 0
    assert (item_dir / "display_2048.webp").is_file()
    assert (item_dir / "thumb_256.webp").stat().st_mtime_ns == thumb_before  # untouched
    after = client.get(f"/api/items/{item['id']}").json()
    assert after["preview_rev"] == item["preview_rev"] + 1  # new URLs, so caches refresh
    assert cli.regen_previews(False) == 0  # second run: nothing to do


def test_originals_are_cached_for_good(client, upload):
    item = upload()["item"]
    assert client.get(f"/api/items/{item['id']}/file").headers["cache-control"] == FOREVER


def test_json_is_compressed_media_is_not(client, upload):
    for n in range(12):
        upload(f"p{n}.png", notes="x" * 200)
    r = client.get("/api/items?limit=50", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"
    assert len(r.json()["items"]) == 12  # TestClient decodes it
    item = r.json()["items"][0]
    for url in (f"/api/items/{item['id']}/thumb?size=256", f"/api/items/{item['id']}/file"):
        m = client.get(url, headers={"Accept-Encoding": "gzip"})
        assert "content-encoding" not in m.headers, url


def test_hashed_assets_are_immutable(client):
    from kist.app import WEB_DIST

    assets = sorted((WEB_DIST / "assets").glob("*.js")) if WEB_DIST.is_dir() else []
    if not assets:
        pytest.skip("web/dist not built")
    r = client.get(f"/assets/{assets[0].name}", headers={"Accept-Encoding": "gzip"})
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert r.headers.get("content-encoding") == "gzip"


def test_unknown_api_path_is_404_not_the_web_app(client):
    from kist.app import WEB_DIST

    if not WEB_DIST.is_dir():
        pytest.skip("web/dist not built")
    r = client.get("/api/items/nope/original")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert client.get("/all").headers["content-type"].startswith("text/html")
