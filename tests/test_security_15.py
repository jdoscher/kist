"""1.5 security fixes: originals by sniffed type, auth edge cases, upload
limit, source_url, API docs behind the token."""

import io

import pytest
from conftest import TOKEN, png_bytes

from kist.config import get_settings

HTML = b"<!doctype html><html><body><script>document.title='LEAK'</script></body></html>"


def test_html_named_pdf_is_a_sandboxed_download(client, upload):
    item = upload("invoice.pdf", HTML, "application/pdf")["item"]
    assert item["mime"] == "text/html"
    assert item["kind"] == "other"
    assert item["preview_status"] == "none" and not item["has_thumb"]
    res = client.get(f"/api/items/{item['id']}/file")
    assert res.headers["content-disposition"].startswith("attachment")
    assert res.headers["content-security-policy"] == "sandbox"
    # the type filter agrees with the item's kind
    assert client.get("/api/items?type=pdf").json()["total"] == 0
    assert client.get("/api/items?type=other").json()["total"] == 1


def test_real_pdfs_are_still_shown_inline(client, upload):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    pdf.new_page(300, 200)
    buf = io.BytesIO()
    pdf.save(buf)
    item = upload("real.pdf", buf.getvalue(), "application/pdf")["item"]
    res = client.get(f"/api/items/{item['id']}/file")
    assert item["kind"] == "pdf"
    assert res.headers["content-disposition"].startswith("inline")
    assert res.headers["content-type"] == "application/pdf"


def test_reclassify_fixes_items_from_before(client, upload, clean, capsys):
    """Older versions called any .pdf a PDF and tried to preview it."""
    from kist import cli
    from kist.db import SessionLocal
    from kist.models import Item

    item = upload("invoice.pdf", HTML, "application/pdf")["item"]
    (clean / "items" / item["id"] / "thumb_256.webp").write_bytes(b"stale")
    with SessionLocal() as s:
        row = s.get(Item, item["id"])
        row.preview_status, row.preview_error = "failed", "pdfium: not a PDF"
        s.commit()

    assert cli.reclassify(dry_run=True) == 0
    out = capsys.readouterr().out
    assert "named .pdf but is text/html" in out and "would fix" in out
    assert client.get(f"/api/items/{item['id']}").json()["preview_status"] == "failed"

    assert cli.reclassify(dry_run=False) == 0
    got = client.get(f"/api/items/{item['id']}").json()
    assert got["preview_status"] == "none" and got["preview_error"] is None
    assert not (clean / "items" / item["id"] / "thumb_256.webp").exists()
    assert cli.verify() == 0


def test_non_ascii_token_is_a_401_not_a_500(client):
    raw = "Bearer t\u00f6ken-\u00e9\u00e9\u00e9-0123456789".encode()
    res = client.get("/api/items", headers={"Authorization": raw})
    assert res.status_code == 401


def test_api_docs_need_the_token(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path, headers={"Authorization": ""}).status_code == 401, path
    assert client.get("/openapi.json").status_code == 200
    # a browser that signed in to the web UI has the cookie
    client.cookies.set("kist_token", TOKEN)
    assert client.get("/docs", headers={"Authorization": ""}).status_code == 200


def test_uploads_over_the_limit_are_refused(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 1000)
    data = png_bytes(size=(400, 400))
    assert len(data) > 1000
    res = client.post("/api/items", files={"file": ("big.png", data, "image/png")})
    assert res.status_code == 413
    assert client.get("/api/items").json()["total"] == 0
    assert list(get_settings().staging_dir.iterdir()) == []


@pytest.mark.parametrize("bad", ["x", "pinterest.com/pin/1", "javascript:alert(1)", "ftp://a/b"])
def test_source_url_must_be_http(client, upload, bad):
    item = upload()["item"]
    res = client.patch(f"/api/items/{item['id']}", json={"source_url": bad})
    assert res.status_code == 422
    res = client.post(
        "/api/items", files={"file": ("x.png", png_bytes((9, 9, 9)), "image/png")},
        data={"source_url": bad},
    )
    assert res.status_code == 422
    assert client.post("/api/items", json={"url": "https://e.test/a.png",
                                           "source_url": bad}).status_code == 422


def test_source_url_can_be_set_and_cleared(client, upload):
    item = upload()["item"]
    res = client.patch(f"/api/items/{item['id']}", json={"source_url": "https://e.test/p"})
    assert res.json()["source_url"] == "https://e.test/p"
    res = client.patch(f"/api/items/{item['id']}", json={"source_url": ""})
    assert res.json()["source_url"] is None
