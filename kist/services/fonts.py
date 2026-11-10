"""Fonts as items (Milestone 5, on top of M2's `font` kind).

GET /api/items/{id}/font-info and /glyphs read the font with fontTools
when it is installed (the `media` extra; it also reads WOFF2), else with a
small built-in sfnt reader that covers TrueType, OpenType/CFF and WOFF 1.0:
the name, cmap, maxp, OS/2 and fvar tables. The specimen itself is set in
the browser with @font-face from /file, which serves fonts with their font/*
MIME type.
"""

import struct
import unicodedata
import zlib
from pathlib import Path

MAX_FONT_BYTES = 64 * 1024 * 1024
GLYPH_PAGE = 256

# (first, last, script) for the scripts worth naming; first match wins
SCRIPT_RANGES = [
    (0x0041, 0x024F, "Latin"), (0x1E00, 0x1EFF, "Latin"), (0x0370, 0x03FF, "Greek"),
    (0x1F00, 0x1FFF, "Greek"), (0x0400, 0x052F, "Cyrillic"), (0x0530, 0x058F, "Armenian"),
    (0x0590, 0x05FF, "Hebrew"), (0x0600, 0x06FF, "Arabic"), (0x0750, 0x077F, "Arabic"),
    (0x0900, 0x097F, "Devanagari"), (0x0980, 0x09FF, "Bengali"), (0x0A00, 0x0A7F, "Gurmukhi"),
    (0x0A80, 0x0AFF, "Gujarati"), (0x0B80, 0x0BFF, "Tamil"), (0x0C00, 0x0C7F, "Telugu"),
    (0x0C80, 0x0CFF, "Kannada"), (0x0D00, 0x0D7F, "Malayalam"), (0x0E00, 0x0E7F, "Thai"),
    (0x0E80, 0x0EFF, "Lao"), (0x10A0, 0x10FF, "Georgian"), (0x1100, 0x11FF, "Hangul"),
    (0x1200, 0x137F, "Ethiopic"), (0x13A0, 0x13FF, "Cherokee"), (0x1780, 0x17FF, "Khmer"),
    (0x3040, 0x309F, "Hiragana"), (0x30A0, 0x30FF, "Katakana"), (0x3100, 0x312F, "Bopomofo"),
    (0x4E00, 0x9FFF, "Han"), (0x3400, 0x4DBF, "Han"), (0xAC00, 0xD7AF, "Hangul"),
]
MIN_GLYPHS_FOR_SCRIPT = 10


class FontError(ValueError):
    pass


# ---------- minimal sfnt reader ----------


def _tables(data: bytes) -> dict[str, bytes]:
    tag = data[:4]
    if tag == b"wOFF":
        num = struct.unpack_from(">H", data, 12)[0]
        out = {}
        for i in range(num):
            t, off, comp, orig, _ck = struct.unpack_from(">4sIIII", data, 44 + i * 20)
            raw = data[off:off + comp]
            if comp < orig:
                raw = zlib.decompressobj().decompress(raw, orig)
            out[t.decode("latin-1")] = raw
        return out
    if tag in (b"\x00\x01\x00\x00", b"true", b"OTTO"):
        num = struct.unpack_from(">H", data, 4)[0]
        out = {}
        for i in range(num):
            t, _ck, off, length = struct.unpack_from(">4sIII", data, 12 + i * 16)
            out[t.decode("latin-1")] = data[off:off + length]
        return out
    if tag == b"wOF2":
        raise FontError("WOFF2 fonts need fontTools (pip install 'kist[media]')")
    if tag == b"ttcf":
        num = struct.unpack_from(">I", data, 8)[0]
        if not num:
            raise FontError("empty font collection")
        first = struct.unpack_from(">I", data, 12)[0]
        return _tables_at(data, first)
    raise FontError("not a font file")


def _tables_at(data: bytes, base: int) -> dict[str, bytes]:
    num = struct.unpack_from(">H", data, base + 4)[0]
    out = {}
    for i in range(num):
        t, _ck, off, length = struct.unpack_from(">4sIII", data, base + 12 + i * 16)
        out[t.decode("latin-1")] = data[off:off + length]
    return out


def _names(name: bytes) -> dict[int, str]:
    out: dict[int, str] = {}
    if len(name) < 6:
        return out
    _fmt, count, string_off = struct.unpack_from(">HHH", name, 0)
    best: dict[int, tuple[int, str]] = {}
    for i in range(count):
        pid, eid, lang, nid, length, off = struct.unpack_from(">6H", name, 6 + i * 12)
        raw = name[string_off + off:string_off + off + length]
        if pid == 3 or pid == 0:
            text = raw.decode("utf-16-be", "replace")
            rank = 0 if (pid == 3 and lang == 0x409) else 1
        elif pid == 1 and eid == 0:
            text = raw.decode("mac_roman", "replace")
            rank = 2
        else:
            continue
        if nid not in best or rank < best[nid][0]:
            best[nid] = (rank, text)
    return {k: v for k, (_r, v) in best.items()}


def _cmap(cmap: bytes) -> set[int]:
    if len(cmap) < 4:
        return set()
    num = struct.unpack_from(">H", cmap, 2)[0]
    subtables = []
    for i in range(num):
        pid, eid, off = struct.unpack_from(">HHI", cmap, 4 + i * 8)
        subtables.append((pid, eid, off))
    # prefer the full-repertoire tables (format 12), then BMP (format 4)
    for want in (12, 4):
        for _pid, _eid, off in subtables:
            fmt = struct.unpack_from(">H", cmap, off)[0]
            if fmt != want:
                continue
            cps: set[int] = set()
            if fmt == 12:
                n = struct.unpack_from(">I", cmap, off + 12)[0]
                for g in range(min(n, 100_000)):
                    start, end, _gid = struct.unpack_from(">III", cmap, off + 16 + g * 12)
                    if end - start > 0x110000:
                        continue
                    cps.update(range(start, min(end, 0x10FFFF) + 1))
            else:
                segx2 = struct.unpack_from(">H", cmap, off + 6)[0]
                ends = off + 14
                starts = ends + segx2 + 2
                deltas = starts + segx2
                ranges = deltas + segx2
                for s in range(segx2 // 2):
                    end = struct.unpack_from(">H", cmap, ends + 2 * s)[0]
                    start = struct.unpack_from(">H", cmap, starts + 2 * s)[0]
                    delta = struct.unpack_from(">h", cmap, deltas + 2 * s)[0]
                    roff = struct.unpack_from(">H", cmap, ranges + 2 * s)[0]
                    for c in range(start, end + 1):
                        if c == 0xFFFF:
                            continue
                        if roff == 0:
                            gid = (c + delta) & 0xFFFF
                        else:
                            pos = ranges + 2 * s + roff + 2 * (c - start)
                            if pos + 2 > len(cmap):
                                continue
                            gid = struct.unpack_from(">H", cmap, pos)[0]
                            if gid:
                                gid = (gid + delta) & 0xFFFF
                        if gid:
                            cps.add(c)
            return cps
    return set()


def _fvar(fvar: bytes, names: dict[int, str]) -> list[dict]:
    if len(fvar) < 16:
        return []
    axes_off, _res, count, size = struct.unpack_from(">HHHH", fvar, 4)
    out = []
    for i in range(count):
        tag, mn, df, mx, _flags, nid = struct.unpack_from(">4siiiHH", fvar, axes_off + i * size)
        out.append({"tag": tag.decode("latin-1"), "min": mn / 65536, "default": df / 65536,
                    "max": mx / 65536, "name": names.get(nid, tag.decode("latin-1"))})
    return out


def _builtin(data: bytes) -> dict:
    t = _tables(data)
    names = _names(t.get("name", b""))
    glyphs = struct.unpack_from(">H", t["maxp"], 4)[0] if len(t.get("maxp", b"")) >= 6 else None
    weight = None
    os2 = t.get("OS/2", b"")
    if len(os2) >= 6:
        weight = struct.unpack_from(">H", os2, 4)[0]
    return {
        "family": names.get(16) or names.get(1),
        "style": names.get(17) or names.get(2),
        "full_name": names.get(4),
        "version": names.get(5),
        "designer": names.get(9),
        "license": names.get(13),
        "weight": weight,
        "glyph_count": glyphs,
        "axes": _fvar(t["fvar"], names) if "fvar" in t else [],
        "codepoints": _cmap(t.get("cmap", b"")),
        "outlines": "cff" if ("CFF " in t or "CFF2" in t) else "truetype",
    }


def _fonttools(path: Path) -> dict:
    from fontTools.ttLib import TTFont  # type: ignore[import-not-found]

    font = TTFont(str(path), lazy=True, fontNumber=0)
    try:
        name = font["name"] if "name" in font else None

        def n(i: int) -> str | None:
            if name is None:
                return None
            rec = name.getDebugName(i)
            return str(rec) if rec else None

        axes = []
        if "fvar" in font:
            for a in font["fvar"].axes:
                axes.append({"tag": a.axisTag, "min": a.minValue, "default": a.defaultValue,
                             "max": a.maxValue, "name": n(a.axisNameID) or a.axisTag})
        cmap = font.getBestCmap() or {}
        return {
            "family": n(16) or n(1),
            "style": n(17) or n(2),
            "full_name": n(4),
            "version": n(5),
            "designer": n(9),
            "license": n(13),
            "weight": font["OS/2"].usWeightClass if "OS/2" in font else None,
            "glyph_count": font["maxp"].numGlyphs if "maxp" in font else None,
            "axes": axes,
            "codepoints": set(cmap),
            "outlines": "cff" if ("CFF " in font or "CFF2" in font) else "truetype",
        }
    finally:
        font.close()


def read(path: Path) -> dict:
    if path.stat().st_size > MAX_FONT_BYTES:
        raise FontError("font file is too large")
    try:
        import fontTools  # type: ignore[import-not-found] # noqa: F401

        have_ft = True
    except ImportError:
        have_ft = False
    try:
        info = _fonttools(path) if have_ft else _builtin(path.read_bytes())
    except FontError:
        raise
    except Exception as exc:
        raise FontError(f"could not read this font: {exc}") from None
    return info


def scripts(codepoints: set[int]) -> list[str]:
    counts: dict[str, int] = {}
    for cp in codepoints:
        for lo, hi, script in SCRIPT_RANGES:
            if lo <= cp <= hi:
                counts[script] = counts.get(script, 0) + 1
                break
    return sorted(s for s, c in counts.items() if c >= MIN_GLYPHS_FOR_SCRIPT)


def info(path: Path) -> dict:
    """What GET /font-info returns."""
    data = read(path)
    cps = data.pop("codepoints")
    data["codepoint_count"] = len(cps)
    data["scripts"] = scripts(cps)
    data["variable"] = bool(data["axes"])
    data["license"] = (data.get("license") or "")[:2000] or None
    return data


def glyphs(path: Path, page: int = 0, size: int = GLYPH_PAGE) -> dict:
    cps = sorted(read(path)["codepoints"])
    pages = max(1, -(-len(cps) // size))
    page = max(0, page)
    chunk = cps[page * size:(page + 1) * size]
    return {
        "page": page,
        "pages": pages,
        "total": len(cps),
        "glyphs": [
            {"cp": cp, "hex": f"U+{cp:04X}", "char": chr(cp),
             "name": unicodedata.name(chr(cp), None)}
            for cp in chunk
        ],
    }
