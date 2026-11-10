"""What the Milestone 4/5 web screens need from the server: the timeline's
date filters (date_after/date_before on taken-else-added), the map's area
filter (bbox), and the app shell refusing to be framed (the /add card)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import media_fixtures as F  # noqa: E402


def _ids(client, q):
    res = client.get(f"/api/items?{q}")
    assert res.status_code == 200, res.text
    return {i["id"] for i in res.json()["items"]}


def test_date_and_bbox_filters(client, upload):
    paris = upload("p.jpg", F.jpeg(exif=F.exif_bytes()), "image/jpeg")["item"]
    sydney = upload(
        "s.jpg",
        F.jpeg(
            color=(1, 2, 3),
            exif=F.exif_bytes(taken="2021:06:15 08:00:00", gps=(-33.8568, 151.2153)),
        ),
        "image/jpeg",
    )["item"]
    plain = upload("plain.png", F.png(), "image/png")["item"]
    assert paris["taken_at"].startswith("2023-06-15")

    # the timeline's day for an undated item is the day it was added
    added = plain["created_at"][:10]
    day = client.get("/api/timeline?group=day").json()
    assert any(b["key"] == added for b in day)
    assert _ids(client, f"date_after={added}T00:00:00Z&date_before={added}T23:59:59Z") == {
        plain["id"]
    }
    # taken_* stays the date taken only
    assert _ids(client, f"taken_after={added}T00:00:00Z") == set()
    assert _ids(client, "date_after=2023-06-15T00:00:00Z&date_before=2023-06-15T23:59:59Z") == {
        paris["id"]
    }
    assert _ids(client, "date_before=2022-01-01T00:00:00Z") == {sydney["id"]}
    # the timeline with the same filter counts the same items
    months = client.get("/api/timeline?group=month&date_before=2022-01-01T00:00:00Z").json()
    assert months == [{"key": "2021-06", "count": 1, "first_item_id": sydney["id"]}]

    # a map area: only items with GPS inside it; across the antimeridian too
    assert _ids(client, "bbox=-10,35,30,60") == {paris["id"]}
    assert _ids(client, "bbox=150,-40,-170,0") == {sydney["id"]}
    assert _ids(client, "bbox=-180,-90,180,90") == {paris["id"], sydney["id"]}
    assert client.get("/api/items?bbox=1,2,3").status_code == 422
    assert client.get("/api/items?bbox=0,10,5,5").status_code == 422
    # other modes and the map take it as well
    assert client.get("/api/map?bbox=-180,-90,180,90&zoom=1").json()["total"] == 2

    # a smart folder keeps them, and a share/export of it holds the same
    sf = client.post(
        "/api/smart-folders",
        json={
            "name": "Europe",
            "query": {"bbox": "-10,35,30,60", "date_after": "2023-01-01T00:00:00Z"},
        },
    )
    assert sf.status_code == 201, sf.text
    from kist.db import SessionLocal
    from kist.models import Item
    from kist.services import selection

    with SessionLocal() as s:
        conds = selection.smart_filters(sf.json()["query"])
        rows = s.query(Item.id).filter(*conds).all()
    assert {r[0] for r in rows} == {paris["id"]}


def test_app_shell_cannot_be_framed(client):
    # /add (the bookmarklet's card) and every other app route
    for path in ("/add", "/", "/timeline", "/bookmarklet"):
        res = client.get(path)
        assert res.headers["x-frame-options"] == "DENY", path
        assert "frame-ancestors 'none'" in res.headers["content-security-policy"], path
    # only the /add card may show images from other sites (its preview)
    assert (
        "img-src 'self' data: blob: https: http:"
        in client.get("/add").headers["content-security-policy"]
    )
    assert "https:" not in client.get("/all").headers["content-security-policy"]
    # WebAssembly may compile (the 3D viewer's decoder); JS eval may not
    csp = client.get("/all").headers["content-security-policy"]
    assert "script-src 'self' 'wasm-unsafe-eval';" in csp and "'unsafe-eval'" not in csp.replace(
        "'wasm-unsafe-eval'", ""
    )
    # the API and share pages keep their own rules
    res = client.get("/api/health")
    assert res.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in res.headers["content-security-policy"]


def test_hosted_map_tiles_are_allowed_by_the_csp(client, monkeypatch):
    from kist.config import get_settings
    from kist.headers import app_csp

    assert "tiles.example.com" not in client.get("/api/health").headers["content-security-policy"]
    monkeypatch.setattr(
        get_settings(), "map_tiles_url", "https://tiles.example.com/{z}/{x}/{y}.png"
    )
    csp = client.get("/api/health").headers["content-security-policy"]
    assert "img-src 'self' data: blob: https://tiles.example.com" in csp
    assert "connect-src 'self' https://tiles.example.com" in csp
    # a value that would break out of the header is ignored
    assert "evil" not in app_csp("https://a.example.com;script-src evil")
    assert app_csp("/api/map/basemap.pmtiles") == app_csp("")


def test_import_upload_then_dry_run(client):
    """Settings → Import can send an export file from the browser; it lands
    in library/import/uploads/ where POST /api/imports may read it."""
    from kist import worker
    from kist.config import get_settings

    csv = (
        b"id,title,note,excerpt,url,folder,tags,created,cover,highlights,favorite\n"
        b"1,Example,,,https://example.com/a,Unsorted,,2024-01-01T00:00:00Z,,,\n"
    )
    res = client.post(
        "/api/imports/upload", files={"file": ("../../evil name?.csv", csv, "text/csv")}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    src = body["source"]
    root = (get_settings().library_dir / "import" / "uploads").resolve()
    assert Path(src).resolve().is_relative_to(root)
    assert body["filename"] == "evil name_.csv" and body["size_bytes"] == len(csv)
    res = client.post("/api/imports", json={"kind": "raindrop", "source": src, "dry_run": True})
    assert res.status_code == 202, res.text
    while worker.run_one():
        pass
    got = client.get(f"/api/imports/{res.json()['id']}").json()
    assert got["status"] == "done" and got["report"]["found"] == 1
    assert client.post("/api/imports/upload", data={"x": "1"}).status_code == 422


def test_import_upload_needs_write(client):
    from test_tokens import as_token, make_token

    reader = as_token(client, make_token(client, name="r", scopes=["read"])["token"])
    assert (
        reader.post("/api/imports/upload", files={"file": ("a.csv", b"x", "text/csv")}).status_code
        == 403
    )
