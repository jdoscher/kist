"""F5 colour books: parsers (ASE, CSV, GPL), nearest code by CIEDE2000,
search by code through the colour search, scopes, MCP, rebuild and verify."""

import io
import json
import random
import struct

import pytest
from conftest import png_bytes
from test_tokens import as_token, make_token, mcp_result

from kist import cli
from kist.services import colour, colourbooks
from kist.services.validation import ValidationError

# ---------- fixtures: build the files ----------


def ase_bytes(entries, groups=False) -> bytes:
    """entries: (name, model, values) with model RGB / LAB / CMYK / Gray."""

    def name_block(name):
        u = (name + "\x00").encode("utf-16-be")
        return struct.pack(">H", len(u) // 2) + u

    blocks = []
    if groups:
        body = name_block("My group")
        blocks.append(struct.pack(">HI", 0xC001, len(body)) + body)
    for name, model, values in entries:
        body = name_block(name) + model.ljust(4).encode("ascii")
        body += struct.pack(f">{len(values)}f", *values) + struct.pack(">H", 2)
        blocks.append(struct.pack(">HI", 0x0001, len(body)) + body)
    if groups:
        blocks.append(struct.pack(">HI", 0xC002, 0))
    return b"ASEF" + struct.pack(">HHI", 1, 0, len(blocks)) + b"".join(blocks)


CSV_HEX = "name,code,hex\nSignal red,RAL 3001,#9b2423\nAnthracite grey,RAL 7016,#383e42\n"
CSV_LAB = "code;name;L;a;b\n19-4052 TCX;Classic Blue;27.66;5.57;-34.02\nbad;row;x;y;z\n"
GPL = (
    "GIMP Palette\nName: House\nColumns: 4\n# a comment\n"
    "255 0 0\tBrand red\n0 0 255 Brand blue\n12 12\n"
)


# ---------- unit: maths and parsers ----------


def test_lab_conversions_round_trip():
    for h in ("#000000", "#ffffff", "#9b2423", "#2f4f3a", "#1030e8"):
        lab = colour.hex_to_lab(h)
        back, out = colourbooks.lab_to_hex(lab)
        assert back == h and not out
    # D50 white is D65 white after adaptation
    light, a, b = colourbooks.lab_d50_to_d65((100.0, 0.0, 0.0))
    assert abs(light - 100) < 0.01 and abs(a) < 0.05 and abs(b) < 0.05
    # OKLab of white is (1, 0, 0)
    ok = colourbooks.oklab_from_hex("#ffffff")
    assert abs(ok[0] - 1) < 1e-3 and abs(ok[1]) < 1e-3 and abs(ok[2]) < 1e-3
    # an impossible Lab colour is flagged as outside sRGB
    assert colourbooks.lab_to_hex((50, 120, 0))[1]


def test_parse_ase_rgb_lab_cmyk_gray_and_groups():
    data = ase_bytes(
        [
            ("Brand red", "RGB", (1.0, 0.0, 0.0)),
            ("PANTONE 19-4052 TCX", "LAB", (0.2766, 5.57, -34.02)),
            ("Rich black", "CMYK", (0.0, 0.0, 0.0, 1.0)),
            ("Mid grey", "Gray", (0.5,)),
        ],
        groups=True,
    )
    sw, report, _ = colourbooks.parse_ase(data)
    assert [s.code for s in sw] == ["Brand red", "PANTONE 19-4052 TCX", "Rich black", "Mid grey"]
    assert sw[0].hex == "#ff0000" and not sw[0].approx
    assert sw[2].hex == "#000000" and sw[2].approx and sw[3].approx
    assert report["warnings"]  # CMYK without a profile
    # Lab came in as D50 and is stored as D65: close to, not equal to, the input
    assert abs(sw[1].lab[0] - 27.66) < 2 and sw[1].lab != (27.66, 5.57, -34.02)
    with pytest.raises(ValidationError):
        colourbooks.parse_ase(b"ASEF\x00")
    with pytest.raises(ValidationError):
        colourbooks.parse_ase(data[:40])


def test_parse_csv_hex_lab_and_headerless():
    sw, report, _ = colourbooks.parse_csv(CSV_HEX.encode())
    assert [(s.code, s.name, s.hex) for s in sw] == [
        ("RAL 3001", "Signal red", "#9b2423"),
        ("RAL 7016", "Anthracite grey", "#383e42"),
    ]
    sw, report, _ = colourbooks.parse_csv(CSV_LAB.encode())
    assert [s.code for s in sw] == ["19-4052 TCX"] and report["skipped"] == 1
    assert sw[0].lab == (27.66, 5.57, -34.02)  # D65 by default: kept as given
    sw, _, _ = colourbooks.parse_csv(b"code,name,lab d50\nX,Y,50 10 10\n")
    assert sw[0].lab != (50.0, 10.0, 10.0)
    # no header: the hex cell is the colour, text cells are code then name
    sw, _, _ = colourbooks.parse_csv(b"A1;#112233;Deep\nA2;#445566\n")
    assert [(s.code, s.name, s.hex) for s in sw] == [
        ("A1", "Deep", "#112233"),
        ("A2", "", "#445566"),
    ]
    # Excel's UTF-8 BOM and a Lab column
    sw, _, _ = colourbooks.parse_csv('\ufeffCode,Lab\nZ,"40 0 0"\n'.encode())
    assert sw[0].code == "Z"


def test_parse_gpl():
    sw, report, title = colourbooks.parse_gpl(GPL.encode())
    assert title == "House"
    assert [(s.code, s.hex) for s in sw] == [("Brand red", "#ff0000"), ("Brand blue", "#0000ff")]
    assert report["skipped"] == 1
    with pytest.raises(ValidationError):
        colourbooks.parse_gpl(b"not a palette")


def test_detect_format():
    assert colourbooks.detect_format(b"ASEF...", None) == "ase"
    assert colourbooks.detect_format(b"GIMP Palette\n", "x.txt") == "gpl"
    assert colourbooks.detect_format(b"a,b", "x.csv") == "csv"
    assert colourbooks.detect_format(b"a,b", None) == "csv"


def test_nearest_matches_brute_force_ciede2000():
    rnd = random.Random(5)
    hexes = [
        f"#{rnd.randrange(256):02x}{rnd.randrange(256):02x}{rnd.randrange(256):02x}"
        for _ in range(600)
    ]
    book = colourbooks.Book(
        id="t",
        name="T",
        enabled=True,
        format="csv",
        source_filename=None,
        imported_at="",
        swatches=[
            colourbooks._from_rgb(f"C{i}", "", colour.hex_to_rgb(h)) for i, h in enumerate(hexes)
        ],
    )
    misses = 0
    for _ in range(200):
        q = f"#{rnd.randrange(256):02x}{rnd.randrange(256):02x}{rnd.randrange(256):02x}"
        lab = colour.hex_to_lab(q)
        brute = min(colour.ciede2000(lab, s.lab) for s in book.swatches)
        got = colourbooks.nearest(q, [book])[0]
        assert got["de"] >= round(brute, 2) - 0.01
        misses += got["de"] > round(brute, 2) + 0.01
    assert misses <= 2  # the OKLab pre-filter almost never changes the answer
    exact = colourbooks.nearest(hexes[7], [book])[0]
    assert exact["code"] == "C7" and exact["de"] == 0


def test_codes_match_loosely():
    book = colourbooks.Book(
        id="ral",
        name="RAL Classic",
        enabled=True,
        format="csv",
        source_filename=None,
        imported_at="",
        swatches=[colourbooks._from_rgb("RAL 7016", "Anthracite grey", (56, 62, 66))],
    )
    assert book.find("ral7016") is book.find("RAL-7016") is book.find("anthracite grey")
    assert book.find("RAL 7015") is None


# ---------- REST ----------


def _import(client, text, filename, **form):
    res = client.post(
        "/api/colourbooks", files={"file": (filename, text, "application/octet-stream")}, data=form
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_import_list_patch_delete(client):
    ral = _import(client, CSV_HEX, "ral.csv", name="RAL Classic")
    assert ral["id"] == "ral-classic" and ral["count"] == 2 and ral["enabled"]
    house = _import(client, GPL, "house.gpl")
    assert house["name"] == "House" and house["format"] == "gpl"
    tcx = _import(client, ase_bytes([("19-4052 TCX", "LAB", (0.2766, 5.57, -34.02))]), "tcx.ase")
    assert tcx["name"] == "tcx" and tcx["format"] == "ase"
    again = _import(client, CSV_HEX, "ral.csv", name="RAL Classic")
    assert again["id"] == "ral-classic-2"

    books = client.get("/api/colourbooks").json()
    assert [b["id"] for b in books] == ["ral-classic", "house", "tcx", "ral-classic-2"]
    full = client.get("/api/colourbooks/ral-classic").json()
    assert full["swatches"][0]["code"] == "RAL 3001"
    assert client.get("/api/colourbooks/nope").status_code == 404

    res = client.patch("/api/colourbooks/house", json={"enabled": False, "name": "House colours"})
    assert res.status_code == 200 and res.json()["enabled"] is False
    assert res.json()["name"] == "House colours"
    assert client.delete("/api/colourbooks/ral-classic-2").status_code == 204
    assert client.get("/api/colourbooks/ral-classic-2").status_code == 404
    # files are the store
    stored = colourbooks.books_dir() / "house.json"
    assert json.loads(stored.read_text())["enabled"] is False

    bad = client.post("/api/colourbooks", files={"file": ("x.csv", "nothing here", "text/csv")})
    assert bad.status_code == 422


def test_nearest_lookup_and_palette_codes(client, upload):
    _import(client, CSV_HEX, "ral.csv", name="RAL")
    _import(client, GPL, "house.gpl")
    near = client.get("/api/colourbooks/nearest?hex=%239a2524").json()
    assert near[0]["matches"][0]["code"] == "RAL 3001"
    assert near[0]["matches"][0]["de"] < 1
    assert [m["book_id"] for m in near[0]["matches"]] == ["ral", "house"]  # closest first
    only = client.get("/api/colourbooks/nearest?hex=%239a2524&book=house").json()
    assert [m["book_id"] for m in only[0]["matches"]] == ["house"]

    found = client.get("/api/colourbooks/lookup?q=7016").json()
    assert found[0]["code"] == "RAL 7016" and found[0]["ref"] == "book:ral/RAL 7016"
    assert client.get("/api/colourbooks/lookup?q=book:ral/ral3001").json()[0]["code"] == "RAL 3001"
    assert client.get("/api/colourbooks/lookup?q=book:ral/zzz").json() == []

    item = upload("red.png", data=png_bytes(color=(155, 36, 35)))["item"]
    codes = client.get(f"/api/items/{item['id']}/palette/codes").json()
    assert codes and codes[0]["hex"]
    assert codes[0]["matches"][0]["code"] == "RAL 3001"
    # disabled books drop out
    client.patch("/api/colourbooks/ral", json={"enabled": False})
    client.patch("/api/colourbooks/house", json={"enabled": False})
    codes = client.get(f"/api/items/{item['id']}/palette/codes").json()
    assert codes[0]["matches"] == []


def test_search_by_code_uses_the_colour_search(client, upload):
    _import(client, CSV_HEX, "ral.csv", name="RAL")
    red = upload("red.png", data=png_bytes(color=(155, 36, 35)))["item"]
    upload("blue.png", data=png_bytes(color=(20, 40, 220)))
    for value in ("book:ral/RAL 3001", "book:RAL/ral3001", "RAL 3001", "Signal red"):
        page = client.get("/api/items", params={"color": value}).json()
        assert [i["id"] for i in page["items"]] == [red["id"]], value
    assert client.get("/api/items", params={"color": "book:ral/RAL 9999"}).status_code == 422
    assert client.get("/api/items", params={"color": "book:nope/RAL 3001"}).status_code == 422
    assert client.get("/api/items", params={"color": "not a colour"}).status_code == 422
    # a disabled book still answers an explicit book: reference, not a bare code
    client.patch("/api/colourbooks/ral", json={"enabled": False})
    assert client.get("/api/items", params={"color": "book:ral/RAL 3001"}).json()["items"]
    assert client.get("/api/items", params={"color": "RAL 3001"}).status_code == 422


def test_scopes_and_folder_limited_reader(client, upload):
    _import(client, CSV_HEX, "ral.csv", name="RAL")
    folder = client.post("/api/folders", json={"name": "Shared"}).json()
    inside = upload(
        "in.png", data=png_bytes(color=(155, 36, 35)), folder_ids=json.dumps([folder["id"]])
    )["item"]
    outside = upload("out.png", data=png_bytes(color=(156, 36, 35)))["item"]

    fam = as_token(
        client, make_token(client, "Family", ["read"], folder_ids=[folder["id"]])["token"]
    )
    assert fam.get(f"/api/items/{inside['id']}/palette/codes").json()[0]["matches"]
    assert fam.get(f"/api/items/{outside['id']}/palette/codes").status_code == 404
    page = fam.get("/api/items", params={"color": "book:ral/RAL 3001"}).json()
    assert [i["id"] for i in page["items"]] == [inside["id"]]
    assert fam.get("/api/colourbooks/lookup?q=3001").status_code == 200
    assert fam.get("/api/colourbooks").status_code == 404
    assert fam.get("/api/colourbooks/ral").status_code == 404

    reader = as_token(client, make_token(client, "r", ["read"])["token"])
    assert reader.get("/api/colourbooks").status_code == 200
    up = reader.post("/api/colourbooks", files={"file": ("x.csv", CSV_HEX, "text/csv")})
    assert up.status_code == 403
    assert reader.patch("/api/colourbooks/ral", json={"enabled": False}).status_code == 403

    agent = as_token(client, make_token(client, "bot", ["agent"])["token"])
    assert agent.patch("/api/colourbooks/ral", json={"enabled": True}).status_code == 200
    assert agent.delete("/api/colourbooks/ral").status_code == 403  # no destructive powers
    writer = as_token(client, make_token(client, "w", ["write"])["token"])
    assert writer.delete("/api/colourbooks/ral").status_code == 204


def test_mcp_nearest_colour_code(client, upload):
    _import(client, CSV_HEX, "ral.csv", name="RAL")
    item = upload("red.png", data=png_bytes(color=(155, 36, 35)))["item"]
    res = mcp_result(client, "nearest_colour_code", {"hex": "#383e42"})
    data = res["structuredContent"]["result"]
    assert data[0]["matches"][0]["code"] == "RAL 7016"
    res = mcp_result(client, "nearest_colour_code", {"item_id": item["id"]})
    assert res["structuredContent"]["result"][0]["matches"][0]["code"] == "RAL 3001"
    res = mcp_result(client, "list_colour_books", {"query": "7016"})
    assert res["structuredContent"]["result"][0]["ref"] == "book:ral/RAL 7016"
    res = mcp_result(client, "search_items", {"color": "book:ral/RAL 3001"})
    assert [i["id"] for i in res["structuredContent"]["items"]] == [item["id"]]


def test_books_survive_rebuild_and_verify_checks_them(client, upload, capsys):
    _import(client, CSV_HEX, "ral.csv", name="RAL")
    client.patch("/api/colourbooks/ral", json={"enabled": False, "name": "RAL old"})
    red = upload("red.png", data=png_bytes(color=(155, 36, 35)))["item"]

    assert cli.rebuild_index(wipe=True) == 0

    books = client.get("/api/colourbooks").json()
    assert [(b["id"], b["name"], b["enabled"], b["count"]) for b in books] == [
        ("ral", "RAL old", False, 2)
    ]
    page = client.get("/api/items", params={"color": "book:ral/RAL 3001"}).json()
    assert [i["id"] for i in page["items"]] == [red["id"]]
    assert cli.verify() == 0

    (colourbooks.books_dir() / "broken.json").write_text("{not json")
    assert cli.verify() == 2
    assert "broken.json" in capsys.readouterr().out
    (colourbooks.books_dir() / "broken.json").unlink()


def test_limits(client, monkeypatch):
    monkeypatch.setattr(colourbooks, "MAX_FILE_BYTES", 10)
    res = client.post("/api/colourbooks", files={"file": ("x.csv", io.BytesIO(CSV_HEX.encode()))})
    assert res.status_code == 422
    monkeypatch.setattr(colourbooks, "MAX_FILE_BYTES", 5 * 1024 * 1024)
    monkeypatch.setattr(colourbooks, "MAX_BOOKS", 1)
    _import(client, CSV_HEX, "a.csv")
    res = client.post("/api/colourbooks", files={"file": ("b.csv", CSV_HEX)})
    assert res.status_code == 422 and "at most 1" in res.json()["detail"]
