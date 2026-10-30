import httpx
import pytest

from kist.app import check_token
from kist.filetypes import kind_for
from kist.services.ingest import filename_from_url, page_image_url, sanitize_filename
from kist.services.search import decode_cursor, encode_cursor


def test_sanitize_filename():
    assert sanitize_filename("a/b\\c.png") != "a/b\\c.png"
    assert "/" not in sanitize_filename("../../etc/passwd")
    assert sanitize_filename("   ") == "file"
    assert len(sanitize_filename("x" * 400)) == 255


def test_filename_from_url_prefers_content_disposition():
    req = httpx.Request("GET", "https://example.com/dl?id=1")
    res = httpx.Response(
        200, request=req, headers={"content-disposition": 'attachment; filename="cat.jpg"'}
    )
    assert filename_from_url("https://example.com/dl?id=1", res) == "cat.jpg"
    plain = httpx.Response(200, request=httpx.Request("GET", "https://e.com/p/dog%20pic.png"))
    assert filename_from_url("https://e.com/p/dog%20pic.png", plain) == "dog pic.png"


@pytest.mark.parametrize(
    "html, expected",
    [
        ('<meta property="og:image" content="/a.jpg">', "https://site.test/a.jpg"),
        ("<meta content='https://cdn.test/b.png' property='og:image'>", "https://cdn.test/b.png"),
        ('<meta name="twitter:image" content="c.webp">', "https://site.test/post/c.webp"),
        ('<meta property="og:image" content="/x.jpg?a=1&amp;b=2">', "https://site.test/x.jpg?a=1&b=2"),
        ("<html><title>no image</title></html>", None),
    ],
)
def test_page_image_url(html, expected):
    assert page_image_url(html, "https://site.test/post/1") == expected


def test_page_image_url_prefers_og_over_twitter():
    html = (
        '<meta name="twitter:image" content="https://t.test/t.png">'
        '<meta property="og:image" content="https://o.test/o.png">'
    )
    assert page_image_url(html, "https://site.test/") == "https://o.test/o.png"


def test_kind_for():
    assert kind_for("image/png", "png") == "image"
    assert kind_for("application/pdf", "pdf") == "pdf"
    assert kind_for("video/mp4", "mp4") == "video"
    assert kind_for("text/plain", "txt") == "other"


def test_cursor_roundtrip():
    cursor = encode_cursor("2026-01-02T03:04:05+00:00", "01JABC")
    value, item_id = decode_cursor(cursor)
    assert item_id == "01JABC"
    assert "2026-01-02" in str(value)


@pytest.mark.parametrize("token", ["", "change-me", "short"])
def test_weak_tokens_are_refused(token):
    with pytest.raises(RuntimeError):
        check_token(token)


def test_strong_token_is_accepted():
    check_token("a" * 32)
