"""Security behaviour: URL fetch guard, decoder limits, auth and headers."""

import io
import ipaddress
import shutil
import socket
import subprocess
import threading
import time

import httpx
import pytest
from conftest import png_bytes

from kist.config import get_settings
from kist.services import ingest, netguard, thumbs

PUBLIC_IP = "93.184.216.34"


@pytest.fixture
def dns(monkeypatch):
    """Fake DNS: name -> list of addresses."""
    table: dict[str, list[str]] = {}

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host not in table:
            raise socket.gaierror("unknown host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in table[host]]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    return table


@pytest.fixture
def settings(monkeypatch):
    s = get_settings()
    for name in ("url_allow_private", "url_allow_hosts", "download_timeout_seconds"):
        monkeypatch.setattr(s, name, getattr(s, name))
    return s


# ---- address checks (unit) --------------------------------------------------

@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1", "10.1.2.3", "172.16.1.68", "192.168.1.1", "169.254.169.254",
        "100.64.0.1", "0.0.0.0", "224.0.0.1", "255.255.255.255", "::1", "fc00::1",
        "fe80::1", "::ffff:127.0.0.1", "::ffff:192.168.1.1", "2002:c0a8:0101::1",
    ],
)
def test_private_and_local_addresses_are_refused(ip, settings):
    with pytest.raises(netguard.BlockedURLError):
        netguard.check_address("h", ipaddress.ip_address(ip))


@pytest.mark.parametrize("ip", [PUBLIC_IP, "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(ip, settings):
    netguard.check_address("h", ipaddress.ip_address(ip))


def test_allowlist_by_host_and_cidr(settings):
    settings.url_allow_hosts = "nas.lan, 10.0.5.0/24"
    netguard.check_address("nas.lan", ipaddress.ip_address("192.168.1.9"))
    netguard.check_address("other", ipaddress.ip_address("10.0.5.7"))
    with pytest.raises(netguard.BlockedURLError):
        netguard.check_address("other", ipaddress.ip_address("10.0.6.7"))
    settings.url_allow_hosts = ""
    settings.url_allow_private = True
    netguard.check_address("other", ipaddress.ip_address("10.0.6.7"))


def test_a_name_with_any_private_address_is_refused(dns, settings):
    dns["mixed.test"] = [PUBLIC_IP, "192.168.1.1"]
    with pytest.raises(netguard.BlockedURLError):
        netguard.resolve("mixed.test", 443)


# ---- guarded fetch (unit, mock transport) -----------------------------------

def _serve(routes):
    """MockTransport keyed by (Host header, path)."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.headers["host"], request.url.path)
        seen.append((str(request.url), request.headers["host"]))
        status, headers, body = routes.get(key, (404, {}, b""))
        return httpx.Response(status, headers=headers, content=body)

    return httpx.MockTransport(handler), seen


def test_fetch_connects_to_the_checked_ip_with_the_real_host(dns, settings):
    dns["img.test"] = [PUBLIC_IP]
    transport, seen = _serve({("img.test", "/a.png"): (200, {"content-type": "image/png"}, b"x")})
    with netguard.guarded_get("http://img.test/a.png", {}, transport=transport) as (resp, url, _):
        assert resp.read() == b"x"
    assert seen == [(f"http://{PUBLIC_IP}/a.png", "img.test")]


def test_unreachable_address_falls_back_to_the_next_vetted_one(dns, settings):
    dns["dual.test"] = ["2606:4700:4700::1111", PUBLIC_IP]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != PUBLIC_IP:
            raise httpx.ConnectError("unreachable")
        return httpx.Response(200, content=b"ok")

    with netguard.guarded_get(
        "http://dual.test/", {}, transport=httpx.MockTransport(handler)
    ) as (resp, _, _):
        assert resp.read() == b"ok"


def test_redirect_to_a_private_address_is_refused(dns, settings):
    dns["evil.test"] = [PUBLIC_IP]
    dns["router.test"] = ["192.168.1.1"]
    transport, seen = _serve(
        {("evil.test", "/r"): (302, {"location": "http://router.test/admin"}, b"")}
    )
    with pytest.raises(netguard.BlockedURLError):
        with netguard.guarded_get("http://evil.test/r", {}, transport=transport):
            pass
    assert [host for _, host in seen] == ["evil.test"]  # never contacted the router


@pytest.mark.parametrize("url", ["ftp://x.test/a", "file:///etc/passwd", "http:///nohost"])
def test_only_http_urls_with_a_host(url, settings):
    with pytest.raises(netguard.BlockedURLError):
        ok = httpx.MockTransport(lambda r: httpx.Response(200))
        with netguard.guarded_get(url, {}, transport=ok):
            pass


def test_download_has_an_overall_deadline(dns, settings):
    dns["drip.test"] = [PUBLIC_IP]
    settings.download_timeout_seconds = 1

    def drip():
        for _ in range(10):
            time.sleep(0.3)
            yield b"x"

    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, headers={"content-type": "image/png"}, content=drip())
    )
    with pytest.raises(netguard.DownloadTimeout):
        ingest._download("http://drip.test/a.png", transport=transport)
    assert not any(get_settings().staging_dir.glob("dl-*"))  # partial file removed


# ---- decoder limits (unit) --------------------------------------------------

def test_pdf_render_scale_is_capped():
    assert thumbs.pdf_render_scale(612, 792) == 2.0  # US Letter: normal 2x
    huge = thumbs.pdf_render_scale(14400, 14400)  # 200 x 200 inch page
    assert 14400 * huge <= thumbs.PDF_MAX_RENDER_PX
    with pytest.raises(RuntimeError):
        thumbs.pdf_render_scale(0, 0)


def test_ffmpeg_reads_only_local_files_in_the_named_format(tmp_path):
    args = thumbs.ffmpeg_input_args(tmp_path / "clip.mp4")
    assert args[:4] == ["-protocol_whitelist", "file", "-f", "mov"]
    assert "-f" not in thumbs.ffmpeg_input_args(tmp_path / "clip.avi")


# ---- integration (need Postgres) ---------------------------------------------

def test_url_add_of_a_private_address_is_refused(client, dns):
    res = client.post("/api/items", json={"url": "http://127.0.0.1:9/secret.json"})
    assert res.status_code == 422
    assert "private or local" in res.json()["detail"]


def test_url_add_keeps_only_media(client, monkeypatch):
    def fake_download(url):
        staging = get_settings().staging_dir
        staging.mkdir(parents=True, exist_ok=True)
        path = staging / f"dl-{ingest.new_ulid()}"
        path.write_bytes(b'{"wifi_psk": "secret"}')
        return path, "config", "application/json"

    monkeypatch.setattr(ingest, "_download", fake_download)
    res = client.post("/api/items", json={"url": "https://api.test/config"})
    assert res.status_code == 422
    assert client.get("/api/items").json()["total"] == 0


def test_network_errors_are_not_detailed(client, monkeypatch):
    def refused(url):
        raise httpx.ConnectError("[Errno 61] Connection refused")

    monkeypatch.setattr(ingest, "_download", refused)
    res = client.post("/api/items", json={"url": "https://down.test/x.png"})
    assert res.status_code == 502
    assert "refused" not in res.json()["detail"].lower()


def test_slow_url_add_does_not_block_other_requests(client, monkeypatch):
    def slow(*args, **kwargs):
        time.sleep(2)
        raise ingest.NotMediaError("slow")

    monkeypatch.setattr(ingest, "ingest_url", slow)
    t = threading.Thread(
        target=lambda: client.post("/api/items", json={"url": "https://slow.test/"})
    )
    t.start()
    time.sleep(0.3)
    started = time.monotonic()
    assert client.get("/api/health").status_code == 200
    assert time.monotonic() - started < 1.0
    t.join()


needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed"
)


@needs_ffmpeg
@pytest.mark.parametrize("ext", ["mp4", "webm", "mov"])
def test_real_videos_still_get_previews(client, upload, tmp_path, ext):
    clip = tmp_path / f"clip.{ext}"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
         "-t", "1", str(clip)],
        check=True,
    )
    mime = {"mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime"}[ext]
    item = upload(f"clip.{ext}", clip.read_bytes(), mime)["item"]
    assert item["kind"] == "video" and item["has_thumb"]
    assert (item["width"], item["height"]) == (320, 240)
    assert item["duration_s"] and item["duration_s"] > 0.5


@needs_ffmpeg
def test_playlist_disguised_as_video_gets_no_preview(client, upload, tmp_path):
    secret = tmp_path / "secret.ts"
    secret.write_bytes(b"not for you")
    playlist = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\n"
        f"file://{secret}\n#EXTINF:1,\nhttp://127.0.0.1:9/x.ts\n#EXT-X-ENDLIST\n"
    ).encode()
    item = upload("trick.mp4", playlist, "video/mp4")["item"]
    assert not item["has_thumb"]  # read as an MP4 container, it's just invalid


def test_pdf_bomb_renders_a_small_poster(client, upload):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    pdf.new_page(14400, 14400)
    buf = io.BytesIO()
    pdf.save(buf)
    item = upload("bomb.pdf", buf.getvalue(), "application/pdf")["item"]
    assert item["has_thumb"]
    assert max(item["width"], item["height"]) <= thumbs.PDF_MAX_RENDER_PX


def test_security_headers(client):
    for path in ("/api/items", "/api/health"):
        res = client.get(path)
        csp = res.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "frame-ancestors 'self'" in csp
        assert res.headers["x-frame-options"] == "SAMEORIGIN"
        assert res.headers["strict-transport-security"].startswith("max-age=")
    unauth = client.get("/api/items", headers={"Authorization": ""})
    assert unauth.status_code == 401 and "content-security-policy" in unauth.headers


def test_originals_are_sandboxed_except_pdfs(client, upload):
    png = upload("a.png", png_bytes())["item"]
    assert client.get(f"/api/items/{png['id']}/file").headers["content-security-policy"] == (
        "sandbox"
    )
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    pdf.new_page(300, 200)
    buf = io.BytesIO()
    pdf.save(buf)
    doc = upload("a.pdf", buf.getvalue(), "application/pdf")["item"]
    res = client.get(f"/api/items/{doc['id']}/file")
    assert res.headers["content-security-policy"] == "frame-ancestors 'self'"
    assert res.headers["content-disposition"].startswith("inline")
