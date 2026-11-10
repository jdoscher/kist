"""Thumbnails for design source files (Milestone 5), pure Python + Pillow.

Each extractor returns a PNG/JPEG/TIFF blob (or a PIL image) of the file's
own embedded preview, or None when the file has none (a .fig usually):
the item then keeps a generic icon. Nothing here renders a document; it
only reads the picture the authoring app saved inside it.

- PSD/PSB: Pillow's flattened composite when the image is small enough,
  else the thumbnail image resource (1036, JPEG; 1033 on old files).
- AI: the PDF inside (PDF-compatible .ai files), rendered by pdfium; else
  as EPS.
- EPS: the TIFF preview of a DOS EPS binary header, or an EPSI hex preview.
- Sketch: previews/preview.png.  Procreate: QuickLook/Thumbnail.png.
- Krita (.kra) and OpenRaster (.ora): mergedimage.png (ORA's
  Thumbnails/thumbnail.png as a fallback).
- Affinity: the PNG thumbnail embedded in the file.
- Blender: the TEST block (raw RGBA, bottom-up), gzip-compressed files
  too (zstd-compressed ones need the `zstandard` package).
- Figma: a .fig that is a zip may carry thumbnail.png; otherwise none.

Zip entries are read with a size cap (a zip bomb can't expand past it).
"""

import gzip
import io
import struct
import zipfile
from pathlib import Path

MAX_ENTRY_BYTES = 256 * 1024 * 1024
MAX_COMPOSITE_PIXELS = 120_000_000
MAX_SCAN_BYTES = 256 * 1024 * 1024


class NoPreview(Exception):
    """The file has no embedded preview (not an error)."""


def read_zip_entry(path: Path, names: list[str], limit: int = MAX_ENTRY_BYTES) -> bytes | None:
    """The first of `names` found in the zip, refusing anything that would
    expand past `limit` bytes."""
    with zipfile.ZipFile(path) as zf:
        available = {i.filename: i for i in zf.infolist()}
        for name in names:
            info = available.get(name)
            if info is None:
                continue
            if info.file_size > limit:
                raise ValueError(f"{name} inside the file is too large")
            with zf.open(info) as f:
                data = f.read(limit + 1)
            if len(data) > limit:
                raise ValueError(f"{name} inside the file is too large")
            return data
    return None


# ---------- PSD / PSB ----------


def psd_info(path: Path) -> dict:
    with path.open("rb") as f:
        head = f.read(26)
    if not head.startswith(b"8BPS"):
        raise ValueError("not a Photoshop file")
    version, channels, height, width, depth, mode = struct.unpack(">H6xHIIHH", head[4:26])
    return {"version": version, "channels": channels, "doc_width": width,
            "doc_height": height, "depth": depth, "color_mode": mode}


def psd_thumbnail_resource(path: Path) -> bytes | None:
    """The JPEG in image resource 1036 (or 1033)."""
    with path.open("rb") as f:
        head = f.read(26)
        if not head.startswith(b"8BPS"):
            return None
        (cm_len,) = struct.unpack(">I", f.read(4))
        f.seek(cm_len, 1)
        (res_len,) = struct.unpack(">I", f.read(4))
        end = f.tell() + res_len
        best = None
        while f.tell() + 12 <= end:
            if f.read(4) != b"8BIM":
                break
            (rid,) = struct.unpack(">H", f.read(2))
            (nlen,) = struct.unpack(">B", f.read(1))
            f.seek(nlen + ((nlen + 1) % 2), 1)  # pascal string, padded to even
            (size,) = struct.unpack(">I", f.read(4))
            start = f.tell()
            if rid in (1036, 1033) and 28 < size < 64 * 1024 * 1024:
                f.seek(start + 28)  # format, width, height, ... header
                data = f.read(size - 28)
                if data.startswith(b"\xff\xd8") and (best is None or rid == 1036):
                    best = data
            f.seek(start + size + (size % 2))
        return best


def psd_preview(path: Path):
    from PIL import Image

    info = psd_info(path)
    if info["version"] == 1 and info["doc_width"] * info["doc_height"] <= MAX_COMPOSITE_PIXELS:
        try:
            with Image.open(path) as im:
                im.load()
                return im.convert("RGBA" if im.mode in ("RGBA", "LA", "PA") else "RGB")
        except Exception:
            pass  # 16-bit, odd colour modes ...: fall back to the thumbnail
    try:
        from psd_tools import PSDImage  # type: ignore[import-not-found]
    except ImportError:
        PSDImage = None
    data = psd_thumbnail_resource(path)
    if data:
        return data
    if PSDImage is not None and info["doc_width"] * info["doc_height"] <= MAX_COMPOSITE_PIXELS:
        return PSDImage.open(path).composite()
    raise NoPreview("this Photoshop file has no thumbnail")


# ---------- EPS / AI ----------


def eps_preview(path: Path):
    from PIL import Image

    with path.open("rb") as f:
        head = f.read(30)
        if head[:4] == b"\xc5\xd0\xd3\xc6":
            _ps_off, _ps_len, _wmf_off, _wmf_len, tiff_off, tiff_len = struct.unpack(
                "<6I", head[4:28]
            )
            if tiff_off and 0 < tiff_len < 64 * 1024 * 1024:
                f.seek(tiff_off)
                return f.read(tiff_len)
            raise NoPreview("this EPS has no TIFF preview")
        f.seek(0)
        data = f.read(MAX_SCAN_BYTES)
    start = data.find(b"%%BeginPreview:")
    if start < 0:
        raise NoPreview("this EPS has no preview")
    line_end = data.find(b"\n", start)
    parts = data[start + len(b"%%BeginPreview:"):line_end].split()
    width, height, depth = int(parts[0]), int(parts[1]), int(parts[2])
    if depth not in (1, 2, 4, 8) or width * height > 50_000_000:
        raise NoPreview("unsupported EPSI preview")
    stop = data.find(b"%%EndPreview", line_end)
    hexdata = b"".join(
        ln.strip().lstrip(b"%").strip() for ln in data[line_end:stop].splitlines()
    )
    raw = bytes.fromhex(hexdata.decode("ascii", "ignore"))
    img = Image.frombytes("L", (width, height), _expand(raw, width, height, depth))
    # EPSI previews store ink: 0 is white and the highest value black
    from PIL import ImageOps

    return ImageOps.invert(img)


def _expand(raw: bytes, width: int, height: int, depth: int) -> bytes:
    row = (width * depth + 7) // 8
    out = bytearray()
    scale = 255 // ((1 << depth) - 1)
    if depth == 8:
        return raw[: row * height].ljust(row * height, b"\x00")
    for y in range(height):
        bits = raw[y * row:(y + 1) * row]
        for x in range(width):
            bit = x * depth
            byte = bits[bit // 8] if bit // 8 < len(bits) else 0
            shift = 8 - depth - (bit % 8)
            out.append(((byte >> shift) & ((1 << depth) - 1)) * scale)
    return bytes(out)


def ai_preview(path: Path, out_png: Path):
    with path.open("rb") as f:
        head = f.read(1024)
    if b"%PDF" in head:
        from .thumbs import pdf_poster

        pdf_poster(path, out_png)
        return out_png
    return eps_preview(path)


# ---------- zip-based ----------


def zip_preview(path: Path, names: list[str]):
    data = read_zip_entry(path, names)
    if data is None:
        raise NoPreview("no preview image inside the file")
    return data


# ---------- Affinity ----------


def affinity_preview(path: Path) -> bytes:
    with path.open("rb") as f:
        data = f.read(MAX_SCAN_BYTES)
    best = None
    pos = 0
    while True:
        pos = data.find(b"\x89PNG\r\n\x1a\n", pos)
        if pos < 0:
            break
        end = data.find(b"IEND\xaeB`\x82", pos)
        if end < 0:
            break
        blob = data[pos:end + 8]
        if best is None or len(blob) > len(best):
            best = blob
        pos = end + 8
    if best is None:
        raise NoPreview("no thumbnail in this Affinity file")
    return best


# ---------- Blender ----------


def _blend_bytes(path: Path) -> bytes:
    with path.open("rb") as f:
        head = f.read(4)
        f.seek(0)
        if head[:2] == b"\x1f\x8b":
            with gzip.open(f) as g:
                return g.read(MAX_SCAN_BYTES)
        if head == b"\x28\xb5\x2f\xfd":
            try:
                import zstandard  # type: ignore[import-not-found]
            except ImportError:
                raise NoPreview("zstd-compressed .blend (install zstandard)") from None
            reader = zstandard.ZstdDecompressor().stream_reader(f)
            return reader.read(MAX_SCAN_BYTES)
        return f.read(MAX_SCAN_BYTES)


def blend_preview(path: Path):
    from PIL import Image

    data = _blend_bytes(path)
    if not data.startswith(b"BLENDER"):
        raise ValueError("not a .blend file")
    if data[7:9].isdigit():
        # Blender 5 header "BLENDER17-01v0500": 64-bit block headers
        pos = int(data[7:9])
        endian = "<" if data[12:13] == b"v" else ">"
        fmt, head_len = endian + "4siQqq", 32
    else:
        # "BLENDER" + pointer size ('_' 4, '-' 8) + endianness + version
        ptr = 8 if data[7:8] == b"-" else 4
        endian = "<" if data[8:9] == b"v" else ">"
        pos, fmt, head_len = 12, endian + "4si", 16 + ptr
    while pos + head_len <= len(data):
        fields = struct.unpack_from(fmt, data, pos)
        code, size = fields[0], (fields[3] if len(fields) == 5 else fields[1])
        body = pos + head_len
        if code == b"TEST":
            w, h = struct.unpack_from(endian + "ii", data, body)
            if not (0 < w <= 4096 and 0 < h <= 4096) or body + 8 + w * h * 4 > len(data):
                raise NoPreview("damaged thumbnail block")
            img = Image.frombytes("RGBA", (w, h), data[body + 8: body + 8 + w * h * 4])
            return img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        if code == b"ENDB" or size < 0:
            break
        pos = body + size
    raise NoPreview("this .blend has no thumbnail (saved without one)")


# ---------- dispatch ----------

FORMATS = {
    "image/vnd.adobe.photoshop": "psd", "application/x-photoshop": "psd",
    "image/x-photoshop": "psd", "application/x-psb": "psb",
    "application/illustrator": "ai", "application/postscript": "eps", "image/x-eps": "eps",
    "application/x-sketch": "sketch", "application/x-procreate": "procreate",
    "application/x-affinity": "affinity", "application/x-blender": "blend",
    "application/x-krita": "krita", "image/openraster": "ora", "application/x-figma": "fig",
}


def extract(path: Path, mime: str, work: Path) -> tuple[Path, dict]:
    """Write the preview to a file in `work`; returns (image path, info).
    Raises NoPreview when the file simply has none."""
    from PIL import Image

    fmt = FORMATS.get(mime)
    info: dict = {"format": fmt}
    if fmt in ("psd", "psb"):
        info.update(psd_info(path))
        result = psd_preview(path)
    elif fmt == "ai":
        result = ai_preview(path, work / "ai.png")
    elif fmt == "eps":
        result = eps_preview(path)
    elif fmt == "sketch":
        result = zip_preview(path, ["previews/preview.png"])
    elif fmt == "procreate":
        result = zip_preview(path, ["QuickLook/Thumbnail.png"])
    elif fmt in ("krita", "ora"):
        result = zip_preview(path, ["mergedimage.png", "Thumbnails/thumbnail.png",
                                    "preview.png"])
    elif fmt == "affinity":
        result = affinity_preview(path)
    elif fmt == "blend":
        result = blend_preview(path)
    elif fmt == "fig":
        if not zipfile.is_zipfile(path):
            raise NoPreview("Figma files carry no preview")
        result = zip_preview(path, ["thumbnail.png"])
    else:
        raise NoPreview(f"no preview extractor for {mime}")

    out = work / "design-preview.png"
    if isinstance(result, Path):
        return result, info
    if isinstance(result, bytes):
        Image.MAX_IMAGE_PIXELS = MAX_COMPOSITE_PIXELS
        with Image.open(io.BytesIO(result)) as im:
            im.load()
            img = im.convert("RGBA" if im.mode in ("RGBA", "LA", "PA", "P") else "RGB")
    else:
        img = result
    info["source"] = "embedded"
    img.save(out, "PNG")
    return out, info
