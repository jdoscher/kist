"""Camera RAW files (Milestone 5): the embedded JPEG preview, and EXIF.

Every RAW format carries a camera-made JPEG (often full size). Kist uses
the largest one for thumbnails, the display copy, embeddings and the
perceptual hash, so a RAW is previewed in milliseconds without a RAW
decoder, in the colours the camera showed.

How the preview is found, in order:
1. TIFF-based RAWs (CR2, NEF, ARW, DNG, PEF, ORF, RW2): walk the IFD chain
   and SubIFDs for JPEG data (JPEGInterchangeFormat, or strips/tiles with
   JPEG compression), plus RW2's JpgFromRaw tag.
2. CR3 (ISO BMFF): the PRVW box (1620 px) in Canon's preview uuid box, or
   the full-size JPEG that is CR3 track 1.
3. RAF: the JPEG offset/length in the RAF header.
4. `exiftool -b -JpgFromRaw / -PreviewImage` when exiftool is installed.
5. Any complete JPEG found by scanning the file (bounded), largest wins.

EXIF (make, model, lens, exposure, GPS, date) is read from the same IFDs
with no dependencies, so RAWs get camera metadata even without exiftool.
"""

import io
import logging
import mmap
import shutil
import struct
import subprocess
from pathlib import Path

log = logging.getLogger("kist.raw")

MAX_IFDS = 64
MAX_ENTRIES = 4096
MAX_SCAN_BYTES = 512 * 1024 * 1024

TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}


class Tiff:
    """A minimal TIFF reader over a byte buffer (mmap)."""

    def __init__(self, data, base: int = 0):
        self.data = data
        self.base = base
        order = bytes(data[base:base + 2])
        if order == b"II":
            self.e = "<"
        elif order == b"MM":
            self.e = ">"
        else:
            raise ValueError("not a TIFF structure")
        self.first = self.u32(base + 4)

    def u16(self, off: int) -> int:
        return struct.unpack_from(self.e + "H", self.data, off)[0]

    def u32(self, off: int) -> int:
        return struct.unpack_from(self.e + "I", self.data, off)[0]

    def ifd(self, off: int) -> tuple[dict[int, tuple], int]:
        """{tag: (type, count, value_offset_or_inline_pos)}, next IFD offset."""
        pos = self.base + off
        if off <= 0 or pos + 2 > len(self.data):
            return {}, 0
        n = self.u16(pos)
        if n > MAX_ENTRIES or pos + 2 + n * 12 + 4 > len(self.data):
            return {}, 0
        entries = {}
        for i in range(n):
            e = pos + 2 + i * 12
            tag, typ, count = self.u16(e), self.u16(e + 2), self.u32(e + 4)
            size = TYPE_SIZES.get(typ, 1) * count
            where = e + 8 if size <= 4 else self.base + self.u32(e + 8)
            entries[tag] = (typ, count, where)
        return entries, self.u32(pos + 2 + n * 12)

    def values(self, entry: tuple, limit: int = 64) -> list:
        typ, count, where = entry
        count = min(count, limit)
        if where + TYPE_SIZES.get(typ, 1) * count > len(self.data):
            return []
        if typ == 3:
            return list(struct.unpack_from(f"{self.e}{count}H", self.data, where))
        if typ in (4, 13):
            return list(struct.unpack_from(f"{self.e}{count}I", self.data, where))
        if typ == 9:
            return list(struct.unpack_from(f"{self.e}{count}i", self.data, where))
        if typ in (5, 10):
            fmt = "I" if typ == 5 else "i"
            raw = struct.unpack_from(f"{self.e}{count * 2}{fmt}", self.data, where)
            return [raw[i] / raw[i + 1] if raw[i + 1] else 0.0 for i in range(0, len(raw), 2)]
        if typ in (1, 6, 7):
            return list(bytes(self.data[where:where + count]))
        return []

    def text(self, entry: tuple) -> str | None:
        typ, count, where = entry
        if typ not in (2, 7) or count > 4096:
            return None
        raw = bytes(self.data[where:where + count]).split(b"\x00", 1)[0]
        value = raw.decode("utf-8", "replace").strip()
        return value or None

    def one(self, entries: dict, tag: int):
        if tag not in entries:
            return None
        vals = self.values(entries[tag], 1)
        return vals[0] if vals else None

    def walk(self) -> list[dict]:
        """IFD0, its chain, and every SubIFD (tag 0x14A) below them."""
        seen: set[int] = set()
        out: list[dict] = []
        queue = [self.first]
        while queue and len(out) < MAX_IFDS:
            off = queue.pop(0)
            if not off or off in seen:
                continue
            seen.add(off)
            entries, nxt = self.ifd(off)
            if not entries:
                continue
            out.append(entries)
            if 0x14A in entries:
                queue += [v for v in self.values(entries[0x14A], 16) if v]
            if nxt:
                queue.append(nxt)
        return out


def _jpeg_candidates_tiff(t: Tiff) -> list[tuple[int, int]]:
    """(absolute offset, length) of JPEG data the IFDs point at."""
    found = []
    for entries in t.walk():
        off, length = t.one(entries, 0x201), t.one(entries, 0x202)
        if off and length:
            found.append((t.base + off, length))
        compression = t.one(entries, 0x103)
        if compression in (6, 7, 99) and 0x111 in entries and 0x117 in entries:
            offs, lens = t.values(entries[0x111], 4), t.values(entries[0x117], 4)
            if len(offs) == 1 and lens:
                found.append((t.base + offs[0], lens[0]))
        if 0x2E in entries:  # Panasonic RW2 JpgFromRaw (type undefined, inline bytes)
            typ, count, where = entries[0x2E]
            found.append((where, count))
    return found


def _box_iter(data, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, pos)
        header = 8
        if size == 1:
            size = struct.unpack_from(">Q", data, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield kind, pos + header, pos + size
        pos += size


CANON_PREVIEW_UUID = bytes.fromhex("eaf42b5e1c984b88b9fbb7dc406e4d16")
CANON_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")


def _jpeg_candidates_cr3(data) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    end = len(data)
    for kind, body, box_end in _box_iter(data, 0, end):
        if kind == b"uuid" and bytes(data[body:body + 16]) == CANON_PREVIEW_UUID:
            # uuid (16) + 8 bytes, then the PRVW box
            for k2, b2, _e2 in _box_iter(data, body + 24, box_end):
                if k2 == b"PRVW":
                    # PRVW: 4 unknown, 2 unknown, w(2), h(2), 2 unknown, size(4), jpeg
                    size = struct.unpack_from(">I", data, b2 + 12)[0]
                    found.append((b2 + 16, size))
        if kind == b"moov":
            found += _cr3_track1(data, body, box_end)
    return found


def _cr3_track1(data, start: int, end: int) -> list[tuple[int, int]]:
    """The first trak of a CR3 holds the full-size JPEG (stsz + co64)."""
    for kind, body, box_end in _box_iter(data, start, end):
        if kind != b"trak":
            continue
        size = offset = None
        stack = [(body, box_end)]
        while stack:
            s, e = stack.pop()
            for k, b, be in _box_iter(data, s, e):
                if k in (b"mdia", b"minf", b"stbl"):
                    stack.append((b, be))
                elif k == b"stsz":
                    sample_size, count = struct.unpack_from(">II", data, b + 4)
                    size = sample_size or (
                        struct.unpack_from(">I", data, b + 12)[0] if count else None
                    )
                elif k == b"co64":
                    if struct.unpack_from(">I", data, b + 4)[0]:
                        offset = struct.unpack_from(">Q", data, b + 8)[0]
                elif k == b"stco":
                    if struct.unpack_from(">I", data, b + 4)[0]:
                        offset = struct.unpack_from(">I", data, b + 8)[0]
        if size and offset:
            return [(offset, size)]
        return []  # only the first track
    return []


def _jpeg_candidates_raf(data) -> list[tuple[int, int]]:
    if bytes(data[:15]) != b"FUJIFILMCCD-RAW":
        return []
    off, length = struct.unpack_from(">II", data, 84)
    return [(off, length)]


def _jpeg_end(data, start: int, limit: int) -> int | None:
    """The end of a JPEG that starts at `start` (just after its EOI)."""
    pos = start + 2
    end = min(len(data), start + limit)
    while pos + 4 <= end:
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker == 0xD9:
            return pos + 2
        if marker in (0x01,) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        seglen = struct.unpack_from(">H", data, pos + 2)[0]
        pos += 2 + seglen
        if marker == 0xDA:  # entropy-coded data until the EOI
            idx = pos
            while True:
                idx = data.find(b"\xff", idx, end)
                if idx < 0 or idx + 1 >= end:
                    return None
                nxt = data[idx + 1]
                if nxt == 0xD9:
                    return idx + 2
                if nxt == 0x00 or 0xD0 <= nxt <= 0xD7:
                    idx += 2
                    continue
                pos = idx  # another marker (progressive JPEG scans)
                break
    return None


def _jpeg_candidates_scan(data, max_found: int = 32) -> list[tuple[int, int]]:
    found = []
    pos = 0
    limit = min(len(data), MAX_SCAN_BYTES)
    while len(found) < max_found:
        pos = data.find(b"\xff\xd8\xff", pos, limit)
        if pos < 0:
            break
        end = _jpeg_end(data, pos, limit - pos)
        if end:
            found.append((pos, end - pos))
            pos = end
        else:
            pos += 3
    return found


def _decode_size(blob: bytes) -> tuple[int, int] | None:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(blob)) as im:
            if im.format != "JPEG":
                return None
            return im.size
    except Exception:
        return None


SMALL_PREVIEW_PX = 1024 * 768  # below this, keep looking for a bigger one


def _pixels(blob: bytes | None) -> int:
    size = _decode_size(blob) if blob else None
    return size[0] * size[1] if size else 0


def _best(data, candidates: list[tuple[int, int]]) -> bytes | None:
    best, best_px = None, 0
    for off, length in candidates:
        if off <= 0 or length <= 0 or off + length > len(data):
            continue
        blob = bytes(data[off:off + length])
        if not blob.startswith(b"\xff\xd8"):
            continue
        size = _decode_size(blob)
        if size and size[0] * size[1] > best_px:
            best, best_px = blob, size[0] * size[1]
    return best


def _exiftool_preview(path: Path) -> bytes | None:
    from ..config import get_settings

    if not get_settings().exiftool or not shutil.which("exiftool"):
        return None
    best, best_px = None, 0
    for tag in ("-JpgFromRaw", "-PreviewImage", "-OtherImage"):
        try:
            out = subprocess.run(["exiftool", "-b", tag, "--", str(path)],
                                 capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.returncode == 0 and out.stdout.startswith(b"\xff\xd8"):
            size = _decode_size(out.stdout)
            if size and size[0] * size[1] > best_px:
                best, best_px = out.stdout, size[0] * size[1]
    return best


def extract_preview(path: Path) -> bytes:
    """The largest embedded JPEG. Raises when there is none."""
    with path.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
        head = bytes(data[:16])
        candidates: list[tuple[int, int]] = []
        try:
            if head[4:8] == b"ftyp":
                candidates = _jpeg_candidates_cr3(data)
            elif head.startswith(b"FUJIFILMCCD-RAW"):
                candidates = _jpeg_candidates_raf(data)
            elif head[:2] in (b"II", b"MM"):
                candidates = _jpeg_candidates_tiff(Tiff(data))
        except (struct.error, ValueError, IndexError):
            log.debug("structured preview lookup failed for %s", path, exc_info=True)
        best = _best(data, candidates)
        # older cameras keep only a thumbnail where the IFDs point (the real
        # preview hides in maker notes): then ask exiftool, and scan
        if _pixels(best) < SMALL_PREVIEW_PX:
            for more in (lambda: _exiftool_preview(path),
                         lambda: _best(data, _jpeg_candidates_scan(data))):
                found = more()
                if _pixels(found) > _pixels(best):
                    best = found
    if best is None:
        raise RuntimeError("no embedded preview found in this RAW file")
    return best


# ---------- EXIF from TIFF structures ----------

EXIF_IFD, GPS_IFD = 0x8769, 0x8825


def _gps_coord(values: list, ref: str | None) -> float | None:
    if len(values) < 3:
        return None
    deg = values[0] + values[1] / 60 + values[2] / 3600
    if ref in ("S", "W"):
        deg = -deg
    return round(deg, 7)


def tiff_exif(t: Tiff) -> dict:
    """EXIF tags in Pillow's numbering, from IFD0 + Exif IFD + GPS IFD."""
    ifd0, _ = t.ifd(t.first)
    out: dict = {"ifd0": {}, "exif": {}, "gps": {}}
    for tag in (0x10F, 0x110, 0x112, 0x131, 0x132):
        if tag in ifd0:
            out["ifd0"][tag] = t.text(ifd0[tag]) if ifd0[tag][0] == 2 else t.one(ifd0, tag)
    if EXIF_IFD in ifd0:
        sub, _ = t.ifd(t.one(ifd0, EXIF_IFD) or 0)
        for tag in (0x829A, 0x829D, 0x8827, 0x9003, 0x9011, 0x920A, 0xA432, 0xA433, 0xA434):
            if tag in sub:
                entry = sub[tag]
                if entry[0] == 2:
                    out["exif"][tag] = t.text(entry)
                elif tag == 0xA432:
                    out["exif"][tag] = t.values(entry, 4)
                else:
                    out["exif"][tag] = t.one(sub, tag)
    if GPS_IFD in ifd0:
        gps, _ = t.ifd(t.one(ifd0, GPS_IFD) or 0)
        for tag in (1, 2, 3, 4):
            if tag in gps:
                entry = gps[tag]
                out["gps"][tag] = t.text(entry) if entry[0] == 2 else t.values(entry, 3)
    return out


def cr3_exif(data) -> dict:
    """CR3 keeps EXIF as small TIFFs in Canon's moov/uuid box: CMT1 is IFD0,
    CMT2 the Exif IFD, CMT4 the GPS IFD."""
    out: dict = {"ifd0": {}, "exif": {}, "gps": {}}
    for kind, body, box_end in _box_iter(data, 0, len(data)):
        if kind != b"moov":
            continue
        for k2, b2, e2 in _box_iter(data, body, box_end):
            if k2 != b"uuid" or bytes(data[b2:b2 + 16]) != CANON_UUID:
                continue
            for k3, b3, _e3 in _box_iter(data, b2 + 16, e2):
                if k3 not in (b"CMT1", b"CMT2", b"CMT4"):
                    continue
                t = Tiff(data, base=b3)
                entries, _ = t.ifd(t.first)
                key = {b"CMT1": "ifd0", b"CMT2": "exif", b"CMT4": "gps"}[k3]
                for tag, entry in entries.items():
                    if entry[0] == 2:
                        out[key][tag] = t.text(entry)
                    elif key == "gps" or tag == 0xA432:
                        out[key][tag] = t.values(entry, 4)
                    else:
                        out[key][tag] = t.one(entries, tag)
    return out


def read_exif(path: Path) -> dict:
    """Raw tag dicts for a TIFF-based RAW or CR3 (or {} for anything else)."""
    with path.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
        head = bytes(data[:4])
        try:
            if bytes(data[4:8]) == b"ftyp":
                return cr3_exif(data)
            if head in (b"IIRO", b"IIRS", b"MMOR", b"IIU\x00"):
                # Olympus/Panasonic variants: a TIFF with another magic number
                t = Tiff(data)
                return tiff_exif(t)
            if head[:2] in (b"II", b"MM"):
                return tiff_exif(Tiff(data))
            if head.startswith(b"FUJI"):
                off, length = struct.unpack_from(">II", data, 84)
                blob = bytes(data[off:off + length])
                return {"jpeg": blob}
        except (struct.error, ValueError, IndexError):
            return {}
    return {}
