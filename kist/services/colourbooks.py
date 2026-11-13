"""Colour books (F5): the user's own named colour lists, and the nearest
code to any colour.

A colour book is a list of swatches {code, name, hex, lab} the user imports
from a file they hold the rights to: Adobe Swatch Exchange (.ase, binary),
CSV (name, code, and hex or Lab) or a GIMP palette (.gpl). Kist ships no
books (Pantone, NCS and RAL lists are licensed; users load their own).

Storage is files only: one JSON file per book in library/.kist/colourbooks/
(`<id>.json`, written atomically). Nothing is in Postgres, so
`kist rebuild-index` keeps them as they are, and `kist verify` checks that
each file parses (check_files).

Nearest code: palette colours (and any hex) are compared with every swatch
of the enabled books. OKLab distance (cheap, perceptual) picks a handful of
candidates per book; CIEDE2000 in CIE Lab (D65, the same maths as the
colour search in services/colour.py) decides and is the ΔE shown.

Search by code: `color=book:<book>/<code>` (or a bare code, looked up in the
enabled books) resolves to the swatch's Lab and runs the existing colour
search (services/ranking.ColourQuery).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import re
import struct
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..config import get_settings
from . import colour
from . import meta as meta_svc
from .validation import ValidationError

log = logging.getLogger(__name__)

DIR = "colourbooks"
FILE_VERSION = 1
FORMATS = ("ase", "csv", "gpl")
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_SWATCHES = 20_000
MAX_BOOKS = 50
NAME_MAX = 80
CODE_MAX = 80
SWATCH_NAME_MAX = 120
# OKLab candidates per book that CIEDE2000 then ranks
PREFILTER = 12
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
ILLUMINANTS = ("D65", "D50")


class NotFound(Exception):
    pass


# ---------- colour maths ----------

D65 = (0.95047, 1.0, 1.08883)
D50 = (0.96422, 1.0, 0.82521)
# Bradford chromatic adaptation, XYZ D50 -> XYZ D65
BRADFORD_D50_D65 = (
    (0.9555766, -0.0230393, 0.0631636),
    (-0.0282895, 1.0099416, 0.0210077),
    (0.0122982, -0.0204830, 1.3299098),
)
XYZ_TO_SRGB = (
    (3.2404542, -1.5371385, -0.4985314),
    (-0.9692660, 1.8760108, 0.0415560),
    (0.0556434, -0.2040259, 1.0572252),
)


def _mul(m, v):
    return tuple(m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2] for i in range(3))


def lab_to_xyz(lab, white=D65):
    light, a, b = lab
    fy = (light + 16) / 116
    fx = fy + a / 500
    fz = fy - b / 200
    eps, kappa = 216 / 24389, 24389 / 27

    def inv(t):
        return t**3 if t**3 > eps else (116 * t - 16) / kappa

    yr = ((light + 16) / 116) ** 3 if light > kappa * eps else light / kappa
    return inv(fx) * white[0], yr * white[1], inv(fz) * white[2]


def xyz_to_lab(xyz, white=D65):
    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = (f(xyz[i] / white[i]) for i in range(3))
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def lab_d50_to_d65(lab):
    """Adobe's Lab (ASE files, Photoshop) is D50; Kist's is D65."""
    return xyz_to_lab(_mul(BRADFORD_D50_D65, lab_to_xyz(lab, D50)), D65)


def lab_to_linear_rgb(lab):
    """CIE Lab (D65) -> linear sRGB, unclamped (may leave 0..1)."""
    return _mul(XYZ_TO_SRGB, lab_to_xyz(lab, D65))


def _encode(c: float) -> float:
    c = min(max(c, 0.0), 1.0)
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def lab_to_hex(lab) -> tuple[str, bool]:
    """(nearest displayable '#rrggbb', whether it is outside sRGB)."""
    lin = lab_to_linear_rgb(lab)
    out = any(c < -0.002 or c > 1.002 for c in lin)
    return "#" + "".join(f"{round(_encode(c) * 255):02x}" for c in lin), out


def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def oklab_from_linear(rgb):
    r, g, b = rgb
    l_ = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m_ = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s_ = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in (l_, m_, s_))
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def oklab_from_lab(lab):
    return oklab_from_linear(lab_to_linear_rgb(lab))


def oklab_from_hex(value: str):
    return oklab_from_linear(tuple(_lin(c) for c in colour.hex_to_rgb(value)))


# ---------- books ----------


@dataclass
class Swatch:
    code: str
    name: str
    hex: str
    lab: tuple[float, float, float]
    approx: bool = False  # converted from CMYK/grey without a profile, or out of sRGB

    def out(self) -> dict:
        d = {"code": self.code, "name": self.name, "hex": self.hex,
             "lab": [round(v, 3) for v in self.lab]}
        if self.approx:
            d["approx"] = True
        return d


@dataclass
class Book:
    id: str
    name: str
    enabled: bool
    format: str
    source_filename: str | None
    imported_at: str
    swatches: list[Swatch]
    _oklab: list | None = field(default=None, repr=False)
    _index: dict | None = field(default=None, repr=False)

    def summary(self) -> dict:
        return {
            "id": self.id, "name": self.name, "enabled": self.enabled, "format": self.format,
            "source_filename": self.source_filename, "imported_at": self.imported_at,
            "count": len(self.swatches),
        }

    def to_file(self) -> dict:
        return {
            "version": FILE_VERSION, "id": self.id, "name": self.name,
            "enabled": self.enabled, "format": self.format,
            "source_filename": self.source_filename, "imported_at": self.imported_at,
            "swatches": [s.out() for s in self.swatches],
        }

    @property
    def oklab(self) -> list:
        if self._oklab is None:
            self._oklab = [oklab_from_lab(s.lab) for s in self.swatches]
        return self._oklab

    def find(self, code: str) -> Swatch | None:
        if self._index is None:
            idx: dict[str, Swatch] = {}
            for s in self.swatches:  # codes win over names; the first of a duplicate wins
                idx.setdefault(norm(s.code), s)
            for s in self.swatches:
                if s.name:
                    idx.setdefault(norm(s.name), s)
            self._index = idx
        return self._index.get(norm(code))


def norm(text: str) -> str:
    """'RAL 7016' == 'ral7016' == 'RAL-7016'."""
    return re.sub(r"[\s\-_.]+", "", (text or "").casefold())


_lock = threading.Lock()
_cache: dict[str, tuple[tuple[int, int], Book]] = {}


def books_dir() -> Path:
    return get_settings().state_dir / DIR


def _path(book_id: str) -> Path:
    return books_dir() / f"{book_id}.json"


def _from_file(data: dict) -> Book:
    if not isinstance(data, dict) or data.get("version") != FILE_VERSION:
        raise ValueError("not a Kist colour book (version 1)")
    sw = []
    for s in data.get("swatches") or []:
        lab = tuple(float(v) for v in s["lab"])
        if len(lab) != 3:
            raise ValueError("a swatch's lab needs three numbers")
        sw.append(Swatch(str(s["code"]), str(s.get("name") or ""), colour.parse_hex(s["hex"]),
                         lab, bool(s.get("approx"))))  # type: ignore[arg-type]
    if not ID_RE.match(str(data.get("id", ""))):
        raise ValueError("bad id")
    return Book(id=data["id"], name=str(data.get("name") or data["id"]),
                enabled=bool(data.get("enabled", True)), format=str(data.get("format") or ""),
                source_filename=data.get("source_filename"),
                imported_at=str(data.get("imported_at") or ""), swatches=sw)


def _load(path: Path) -> Book:
    st = path.stat()
    key = (st.st_mtime_ns, st.st_size)
    with _lock:
        hit = _cache.get(str(path))
        if hit and hit[0] == key:
            return hit[1]
    book = _from_file(meta_svc.read_json(path))
    if book.id != path.stem:
        raise ValueError(f"its id {book.id!r} doesn't match the file name")
    with _lock:
        _cache[str(path)] = (key, book)
    return book


def list_books() -> list[Book]:
    """Every readable book, oldest import first (unreadable files are
    skipped here and reported by check_files)."""
    d = books_dir()
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(_load(p))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("colour book %s is unreadable: %s", p.name, exc)
    out.sort(key=lambda b: (b.imported_at, b.id))
    return out


def get_book(book_ref: str) -> Book:
    """By id, else by name (case-insensitive)."""
    ref = (book_ref or "").strip()
    books = list_books()
    for b in books:
        if b.id == ref:
            return b
    for b in books:
        if b.name.casefold() == ref.casefold() or norm(b.name) == norm(ref):
            return b
    raise NotFound(f"no colour book {book_ref!r}")


def _save(book: Book) -> None:
    book._oklab = None
    book._index = None
    meta_svc.atomic_write_text(_path(book.id), json.dumps(book.to_file(), ensure_ascii=False))


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:40].strip("-")
    return s or "book"


def _clean_name(name: str) -> str:
    name = " ".join(str(name or "").split())
    if not name:
        raise ValidationError("a colour book needs a name")
    if len(name) > NAME_MAX:
        raise ValidationError(f"a colour book name is at most {NAME_MAX} characters")
    return name


def import_book(data: bytes, filename: str | None = None, name: str | None = None,
                fmt: str | None = None) -> tuple[Book, dict]:
    """Parse and store a new book. Returns (book, report{skipped, warnings})."""
    if len(data) > MAX_FILE_BYTES:
        raise ValidationError(f"colour book files are at most {MAX_FILE_BYTES // 1024 // 1024} MB")
    fmt = (fmt or detect_format(data, filename)).lower()
    if fmt not in FORMATS:
        raise ValidationError("colour books are .ase, .csv or .gpl files")
    parser = {"ase": parse_ase, "csv": parse_csv, "gpl": parse_gpl}[fmt]
    swatches, report, title = parser(data)
    if not swatches:
        raise ValidationError("no colours found in that file")
    if len(swatches) > MAX_SWATCHES:
        raise ValidationError(f"a colour book holds at most {MAX_SWATCHES} colours")
    stem = Path(filename).stem if filename else ""
    book_name = _clean_name(name or title or stem or "Colour book")
    with _lock:
        existing = list_books_unlocked_ids()
    if len(existing) >= MAX_BOOKS:
        raise ValidationError(f"Kist keeps at most {MAX_BOOKS} colour books; delete one first")
    base = _slug(book_name)
    book_id, n = base, 2
    while book_id in existing or _path(book_id).exists():
        book_id = f"{base[:44]}-{n}"
        n += 1
    book = Book(id=book_id, name=book_name, enabled=True, format=fmt,
                source_filename=(Path(filename).name[:200] if filename else None),
                imported_at=datetime.now(UTC).isoformat(timespec="microseconds"), swatches=swatches)
    _save(book)
    log.info("colour book %s imported (%d colours)", book.id, len(swatches))
    return book, report


def list_books_unlocked_ids() -> set[str]:
    d = books_dir()
    return {p.stem for p in d.glob("*.json")} if d.is_dir() else set()


def update_book(book_id: str, *, name: str | None = None, enabled: bool | None = None) -> Book:
    book = get_book(book_id)
    if name is not None:
        book.name = _clean_name(name)
    if enabled is not None:
        book.enabled = bool(enabled)
    _save(book)
    return book


def delete_book(book_id: str) -> None:
    book = get_book(book_id)
    _path(book.id).unlink(missing_ok=True)
    with _lock:
        _cache.pop(str(_path(book.id)), None)


def check_files() -> list[str]:
    """verify: every book file parses and matches its name."""
    d = books_dir()
    if not d.is_dir():
        return []
    problems = []
    for p in sorted(d.glob("*.json")):
        try:
            _load(p)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            problems.append(f".kist/{DIR}/{p.name}: unreadable colour book ({exc})")
    return problems


# ---------- parsers ----------


def detect_format(data: bytes, filename: str | None) -> str:
    if data[:4] == b"ASEF":
        return "ase"
    if data.lstrip()[:12].startswith(b"GIMP Palette"):
        return "gpl"
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if ext in FORMATS:
        return ext
    return "csv"


def _swatch(code: str, name: str, lab, approx: bool = False) -> Swatch:
    code = " ".join(str(code or "").split())[:CODE_MAX]
    name = " ".join(str(name or "").split())[:SWATCH_NAME_MAX]
    if not code:
        code, name = name[:CODE_MAX], ""
    if name == code:
        name = ""
    hex_, out = lab_to_hex(lab)
    return Swatch(code, name, hex_, tuple(round(v, 4) for v in lab), approx or out)  # type: ignore[arg-type]


def _from_rgb(code: str, name: str, rgb, approx: bool = False) -> Swatch:
    h = "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)
    s = _swatch(code, name, colour.hex_to_lab(h), approx)
    s.hex = h  # exactly what the file said
    return s


def parse_ase(data: bytes) -> tuple[list[Swatch], dict, str | None]:
    """Adobe Swatch Exchange 1.0: 'ASEF', version, block count, then
    blocks (0xC001 group start, 0xC002 group end, 0x0001 colour). A colour
    has a UTF-16BE name, a model (RGB, LAB, CMYK, Gray) with big-endian
    floats and a type (global, spot, normal). Lab is Adobe's D50 (adapted
    to D65 here); CMYK and grey have no profile, so they're approximate."""
    if data[:4] != b"ASEF" or len(data) < 12:
        raise ValidationError("not an Adobe Swatch Exchange file")
    (count,) = struct.unpack(">I", data[8:12])
    pos, out, skipped = 12, [], 0
    warnings: set[str] = set()
    try:
        for _ in range(min(count, MAX_SWATCHES * 2 + 1000)):
            btype, blen = struct.unpack(">HI", data[pos:pos + 6])
            if pos + 6 + blen > len(data):
                raise struct.error("block past the end")
            body = data[pos + 6:pos + 6 + blen]
            pos += 6 + blen
            if btype == 0xC002:
                continue
            if len(body) < 2:
                continue
            (nlen,) = struct.unpack(">H", body[:2])
            name = body[2:2 + nlen * 2].decode("utf-16-be", "replace").rstrip("\x00")
            rest = body[2 + nlen * 2:]
            if btype == 0xC001:  # a group's name; swatches keep their own
                continue
            if btype != 0x0001 or len(rest) < 4:
                skipped += 1
                continue
            model = rest[:4].decode("ascii", "replace").strip()
            vals = rest[4:]
            if model == "RGB":
                r, g, b = struct.unpack(">3f", vals[:12])
                out.append(_from_rgb(name, "", (r * 255, g * 255, b * 255)))
            elif model == "LAB":
                light, a, b = struct.unpack(">3f", vals[:12])
                out.append(_swatch(name, "", lab_d50_to_d65((light * 100, a, b))))
            elif model == "CMYK":
                c, m, y, k = struct.unpack(">4f", vals[:16])
                rgb = (255 * (1 - c) * (1 - k), 255 * (1 - m) * (1 - k), 255 * (1 - y) * (1 - k))
                out.append(_from_rgb(name, "", rgb, approx=True))
                warnings.add("CMYK colours were converted without a profile (approximate)")
            elif model == "Gray":
                (v,) = struct.unpack(">f", vals[:4])
                out.append(_from_rgb(name, "", (v * 255,) * 3, approx=True))
            else:
                skipped += 1
    except struct.error:
        raise ValidationError("that .ase file is cut short or damaged") from None
    return out, {"skipped": skipped, "warnings": sorted(warnings)}, None


def parse_gpl(data: bytes) -> tuple[list[Swatch], dict, str | None]:
    """GIMP palette: 'GIMP Palette', optional 'Name:' / 'Columns:', then
    'R G B<tab>name' lines (0-255); '#' starts a comment."""
    text = _text(data)
    lines = text.splitlines()
    if not lines or not lines[0].strip().startswith("GIMP Palette"):
        raise ValidationError("not a GIMP palette (it should start with 'GIMP Palette')")
    title, out, skipped = None, [], 0
    for raw in lines[1:]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("name:"):
            title = line[5:].strip() or None
            continue
        if line.lower().startswith("columns:"):
            continue
        m = re.match(r"^(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s*(.*)$", line)
        if not m or any(int(v) > 255 for v in m.groups()[:3]):
            skipped += 1
            continue
        rgb = tuple(int(v) for v in m.groups()[:3])
        label = m.group(4).strip()
        if not label or label.lower() == "untitled":
            label = "#" + "".join(f"{c:02x}" for c in rgb)
        out.append(_from_rgb(label, "", rgb))
    return out, {"skipped": skipped, "warnings": []}, title


HEADER_ALIASES = {
    "code": ("code", "id", "number", "no", "colour code", "color code", "ref", "reference"),
    "name": ("name", "colour name", "color name", "title", "description", "label"),
    "hex": ("hex", "rgb hex", "html", "web", "colour", "color", "srgb"),
    "l": ("l", "l*", "lab l", "lab_l", "cie l"),
    "a": ("a", "a*", "lab a", "lab_a", "cie a"),
    "b": ("b", "b*", "lab b", "lab_b", "cie b"),
    "lab": ("lab", "cielab", "lab d65", "lab d50"),
    "illuminant": ("illuminant", "white", "white point"),
}
HEX_CELL = re.compile(r"^#?[0-9a-fA-F]{6}$")


def _text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _num(v: str) -> float:
    return float(v.strip().replace(",", ".")) if v and v.strip() else math.nan


def parse_csv(data: bytes) -> tuple[list[Swatch], dict, str | None]:
    """CSV (comma, semicolon or tab): columns name, code, and either hex or
    Lab (L, a, b columns, or one 'lab' column '52.1 -3.4 20'). The header
    row is optional; without one, a hex cell is the colour and the text
    cells are code then name. Lab is D65 unless an 'illuminant' column (or
    a 'lab d50' header) says D50."""
    text = _text(data)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        delim = dialect.delimiter
    except csv.Error:
        first = sample.splitlines()[0] if sample.strip() else ""
        delim = max(",;\t", key=first.count)
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    if not rows:
        return [], {"skipped": 0, "warnings": []}, None
    head = [c.strip().casefold() for c in rows[0]]
    cols: dict[str, int] = {}
    for key, names in HEADER_ALIASES.items():
        for i, h in enumerate(head):
            if h in names and key not in cols and i not in cols.values():
                cols[key] = i
    has_header = bool(cols) and not any(HEX_CELL.match(c.strip()) for c in rows[0])
    d50_header = any(h == "lab d50" for h in head)
    body = rows[1:] if has_header else rows
    out, skipped = [], 0
    for r in body:
        def cell(k, row=r):
            return row[cols[k]].strip() if k in cols and cols[k] < len(row) else ""

        try:
            if has_header:
                code, name = cell("code"), cell("name")
                lab = None
                if "l" in cols and "a" in cols and "b" in cols and cell("l"):
                    lab = (_num(cell("l")), _num(cell("a")), _num(cell("b")))
                elif "lab" in cols and cell("lab"):
                    parts = re.split(r"[\s/;|]+", cell("lab").strip("() "))
                    lab = tuple(_num(p) for p in parts[:3]) if len(parts) >= 3 else None
                if lab is not None:
                    if any(math.isnan(v) for v in lab) or not 0 <= lab[0] <= 100:
                        raise ValueError
                    ill = (cell("illuminant") or ("D50" if d50_header else "D65")).upper()
                    if ill not in ILLUMINANTS:
                        raise ValueError
                    if ill == "D50":
                        lab = lab_d50_to_d65(lab)
                    out.append(_swatch(code, name, lab))
                elif cell("hex"):
                    h = colour.parse_hex(cell("hex"))
                    out.append(_from_rgb(code, name, colour.hex_to_rgb(h)))
                else:
                    raise ValueError
            else:
                hexes = [c.strip() for c in r if HEX_CELL.match(c.strip())]
                texts = [c.strip() for c in r if c.strip() and not HEX_CELL.match(c.strip())]
                if not hexes:
                    raise ValueError
                h = colour.parse_hex(hexes[0])
                code = texts[0] if texts else h
                name = texts[1] if len(texts) > 1 else ""
                out.append(_from_rgb(code, name, colour.hex_to_rgb(h)))
            if not out[-1].code:
                out.pop()
                raise ValueError
        except (ValueError, IndexError):
            skipped += 1
    return out, {"skipped": skipped, "warnings": []}, None


# ---------- nearest and lookup ----------


def _best_in(book: Book, lab, ok) -> tuple[Swatch, float] | None:
    if not book.swatches:
        return None
    oks = book.oklab
    dist = [(o[0] - ok[0]) ** 2 + (o[1] - ok[1]) ** 2 + (o[2] - ok[2]) ** 2 for o in oks]
    k = min(PREFILTER, len(dist))
    cand = sorted(range(len(dist)), key=dist.__getitem__)[:k]
    best = min(cand, key=lambda i: colour.ciede2000(lab, book.swatches[i].lab))
    return book.swatches[best], colour.ciede2000(lab, book.swatches[best].lab)


def match_out(book: Book, sw: Swatch, de: float) -> dict:
    d = {"book_id": book.id, "book": book.name, **sw.out(), "de": round(de, 2)}
    d.pop("lab", None)
    return d


def nearest(hex_value: str, books: list[Book] | None = None) -> list[dict]:
    """The nearest swatch in each enabled book (or `books`), closest first:
    [{book_id, book, code, name, hex, de}] (de is CIEDE2000)."""
    h = colour.parse_hex(hex_value)
    lab, ok = colour.hex_to_lab(h), oklab_from_hex(h)
    out = []
    for b in (books if books is not None else [b for b in list_books() if b.enabled]):
        hit = _best_in(b, lab, ok)
        if hit:
            out.append(match_out(b, *hit))
    out.sort(key=lambda m: m["de"])
    return out


def palette_codes(dominant_colors) -> list[dict]:
    """colour.palette() plus each colour's nearest codes (`matches`)."""
    books = [b for b in list_books() if b.enabled]
    pal = colour.palette(dominant_colors)
    for p in pal:
        p["matches"] = nearest(p["hex"], books) if books else []
    return pal


REF_RE = re.compile(r"^book:([^/]+)/(.+)$", re.IGNORECASE)


def resolve(ref: str) -> tuple[Book, Swatch]:
    """'book:<book>/<code>' or a bare code (enabled books, oldest first)."""
    ref = (ref or "").strip()
    m = REF_RE.match(ref)
    if m:
        book = get_book(m.group(1))
        sw = book.find(m.group(2))
        if sw is None:
            raise NotFound(f"no colour {m.group(2)!r} in {book.name}")
        return book, sw
    for book in list_books():
        if book.enabled:
            sw = book.find(ref)
            if sw is not None:
                return book, sw
    raise NotFound(f"no colour code {ref!r} in your colour books")


def ref_for(book: Book, sw: Swatch) -> str:
    return f"book:{book.id}/{sw.code}"


def lookup(q: str, limit: int = 20, book_ref: str | None = None) -> list[dict]:
    """Swatches whose code or name matches `q` (exact, then prefix, then
    contains), for the code search box. Enabled books only unless one is
    named."""
    needle = norm(q)
    if not needle:
        return []
    books = [get_book(book_ref)] if book_ref else [b for b in list_books() if b.enabled]
    scored = []
    for bi, b in enumerate(books):
        for si, s in enumerate(b.swatches):
            best = None
            for text in (s.code, s.name):
                t = norm(text)
                if not t:
                    continue
                score = (0 if t == needle else 1 if t.startswith(needle)
                         else 2 if needle in t else None)
                if score is not None and (best is None or score < best):
                    best = score
            if best is not None:
                scored.append((best, bi, si, b, s))
    scored.sort(key=lambda x: x[:3])
    return [{"book_id": b.id, "book": b.name, **s.out(), "ref": ref_for(b, s)}
            for _, _, _, b, s in scored[:limit]]
