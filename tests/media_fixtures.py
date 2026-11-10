"""Small media fixtures for the Milestone 5 tests, built at test time.

Nothing here is downloaded: video and audio come from ffmpeg's test
sources, design files and RAW containers are written byte by byte to the
formats' documented layouts, with real (tiny) JPEG/PNG payloads.
"""

import io
import json
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path

FFMPEG = shutil.which("ffmpeg")
HAVE_FFMPEG = bool(FFMPEG and shutil.which("ffprobe"))


def png(color=(200, 40, 40), size=(64, 48), mode="RGB") -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, "PNG")
    return buf.getvalue()


def jpeg(color=(30, 120, 200), size=(320, 240), exif: bytes | None = None) -> bytes:
    from PIL import Image, ImageDraw

    im = Image.new("RGB", size, color)
    ImageDraw.Draw(im).rectangle((10, 10, size[0] // 2, size[1] // 2), fill=(250, 250, 250))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80, **({"exif": exif} if exif else {}))
    return buf.getvalue()


def ffmpeg(*args: str) -> None:
    subprocess.run([FFMPEG, "-v", "error", "-y", *args], check=True, capture_output=True)


# ---------- video / audio ----------


def video(path: Path, *, size="320x240", seconds=3, codec="libx264", audio=True,
          extra: tuple = ()) -> Path:
    args = ["-f", "lavfi", "-i", f"testsrc=size={size}:rate=10"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=22050"]
    args += ["-t", str(seconds), "-c:v", codec, "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac" if path.suffix in (".mp4", ".mov") else "mp3", "-shortest"]
    ffmpeg(*args, *extra, str(path))
    return path


def audio(path: Path, seconds=2, title="Beep", artist="Tester") -> Path:
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=22050", "-t", str(seconds),
           "-metadata", f"title={title}", "-metadata", f"artist={artist}", str(path))
    return path


def animated(path: Path, fmt: str, frames: int = 12) -> Path:
    from PIL import Image

    imgs = [Image.new("RGB", (60, 40), (i * 20 % 256, 80, 200 - i * 10)) for i in range(frames)]
    imgs[0].save(path, fmt, save_all=True, append_images=imgs[1:], duration=100, loop=0)
    return path


# ---------- photos ----------


def exif_bytes(make="Canon", model="Canon EOS R5", lens="RF24-70mm F2.8 L IS USM",
               taken="2023:06:15 14:30:00", gps=(48.8584, 2.2945)) -> bytes:
    from PIL import Image

    ex = Image.Exif()
    ex[0x10F] = make
    ex[0x110] = model
    ex[0x112] = 1
    sub = ex.get_ifd(0x8769)
    sub[0x9003] = taken
    sub[0x829A] = 1 / 250
    sub[0x829D] = 2.8
    sub[0x8827] = 400
    sub[0x920A] = 50.0
    sub[0xA434] = lens
    if gps:
        lat, lon = gps
        g = ex.get_ifd(0x8825)

        def dms(v):
            v = abs(v)
            d = int(v)
            m = int((v - d) * 60)
            s = round(((v - d) * 60 - m) * 60, 4)
            return (float(d), float(m), float(s))

        g[1] = "N" if lat >= 0 else "S"
        g[2] = dms(lat)
        g[3] = "E" if lon >= 0 else "W"
        g[4] = dms(lon)
    return ex.tobytes()


# ---------- RAW containers ----------


def _ifd(entries: list[tuple[int, int, int, bytes]], next_off: int, base_off: int,
         data_off: int) -> tuple[bytes, bytes]:
    """Little-endian IFD at base_off with out-of-line data placed from data_off."""
    body = struct.pack("<H", len(entries))
    extra = b""
    for tag, typ, count, value in sorted(entries):
        if len(value) <= 4:
            body += struct.pack("<HHI", tag, typ, count) + value.ljust(4, b"\x00")
        else:
            body += struct.pack("<HHII", tag, typ, count, data_off + len(extra))
            extra += value + (b"\x00" if len(value) % 2 else b"")
    body += struct.pack("<I", next_off)
    return body, extra


def _ascii(s: str) -> bytes:
    return s.encode() + b"\x00"


def tiff_raw(path: Path, preview: bytes, *, thumb: bytes | None = None,
             header: bytes = b"II*\x00", make="NIKON CORPORATION", model="NIKON Z 6",
             taken="2022:08:01 09:15:30", orientation=1) -> Path:
    """A TIFF-structured RAW: IFD0 (EXIF pointer, small JPEG thumbnail via
    JPEGInterchangeFormat) and a SubIFD holding the big preview JPEG."""
    thumb = thumb or jpeg(size=(80, 60))
    # layout: header(8) | IFD0 @8 | its data | EXIF IFD | data | SubIFD | thumb | preview
    ifd0_off = 8
    # sizes are computed in two passes
    def build(exif_off, sub_off, thumb_off, prev_off, data0_off, exif_data_off):
        ifd0, extra0 = _ifd([
            (0x10F, 2, len(_ascii(make)), _ascii(make)),
            (0x110, 2, len(_ascii(model)), _ascii(model)),
            (0x112, 3, 1, struct.pack("<H", orientation)),
            (0x14A, 4, 1, struct.pack("<I", sub_off)),
            (0x201, 4, 1, struct.pack("<I", thumb_off)),
            (0x202, 4, 1, struct.pack("<I", len(thumb))),
            (0x8769, 4, 1, struct.pack("<I", exif_off)),
        ], 0, ifd0_off, data0_off)
        exif, extra1 = _ifd([
            (0x829A, 5, 1, b""), (0x829D, 5, 1, b""),
            (0x8827, 3, 1, struct.pack("<H", 800)),
            (0x9003, 2, len(_ascii(taken)), _ascii(taken)),
        ], 0, exif_off, exif_data_off)
        return ifd0, extra0, exif, extra1

    # fixed-size rationals: patch them in by hand below
    ifd0, extra0, exif, extra1 = build(0, 0, 0, 0, 0, 0)
    data0_off = ifd0_off + len(ifd0)
    exif_off = data0_off + len(extra0)
    exif_data_off = exif_off + len(exif)
    rationals_off = exif_data_off + len(extra1)
    sub_off = rationals_off + 16
    sub_len = 2 + 3 * 12 + 4
    thumb_off = sub_off + sub_len
    prev_off = thumb_off + len(thumb)
    ifd0, extra0, exif, extra1 = build(exif_off, sub_off, thumb_off, prev_off, data0_off,
                                       exif_data_off)
    # point the two rational entries at rationals_off
    exif = bytearray(exif)
    for i in range(2):
        e = 2 + i * 12
        struct.pack_into("<I", exif, e + 8, rationals_off + i * 8)
    rationals = struct.pack("<IIII", 1, 500, 40, 10)  # 1/500 s, f/4
    sub, _ = _ifd([
        (0x103, 3, 1, struct.pack("<H", 6)),
        (0x201, 4, 1, struct.pack("<I", prev_off)),
        (0x202, 4, 1, struct.pack("<I", len(preview))),
    ], 0, sub_off, 0)
    data = header + struct.pack("<I", ifd0_off) + ifd0 + extra0 + bytes(exif) + extra1 + \
        rationals + sub + thumb + preview
    assert len(header) + 4 == 8
    path.write_bytes(data)
    return path


def _box(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", 8 + len(body)) + kind + body


def _tiff_block(entries) -> bytes:
    """A standalone little-endian TIFF with one IFD at offset 8."""
    ifd, extra = _ifd(entries, 0, 8, 0)
    ifd, extra = _ifd(entries, 0, 8, 8 + len(ifd))
    return b"II*\x00" + struct.pack("<I", 8) + ifd + extra


def cr3(path: Path, preview: bytes, make="Canon", model="Canon EOS R6",
        taken="2021:12:24 18:00:05") -> Path:
    """ftyp crx + moov holding Canon's metadata uuid box (CMT1 IFD0, CMT2
    Exif IFD) + the preview uuid box with a PRVW JPEG."""
    ftyp = _box(b"ftyp", b"crx " + b"\x00\x00\x00\x01" + b"crx isom")
    cmt1 = _box(b"CMT1", _tiff_block([(0x10F, 2, len(_ascii(make)), _ascii(make)),
                                      (0x110, 2, len(_ascii(model)), _ascii(model))]))
    cmt2 = _box(b"CMT2", _tiff_block([(0x9003, 2, len(_ascii(taken)), _ascii(taken)),
                                      (0x8827, 3, 1, struct.pack("<H", 1600))]))
    meta = _box(b"uuid", bytes.fromhex("85c0b687820f11e08111f4ce462b6a48") + cmt1 + cmt2)
    prvw = _box(b"PRVW", struct.pack(">IHHHHI", 0, 1, 1620, 1080, 1, len(preview)) + preview)
    uuid = _box(b"uuid", bytes.fromhex("eaf42b5e1c984b88b9fbb7dc406e4d16") + b"\x00" * 8 + prvw)
    path.write_bytes(ftyp + _box(b"moov", meta) + uuid)
    return path


def raf(path: Path, preview: bytes) -> Path:
    header = b"FUJIFILMCCD-RAW 0201FF383501".ljust(84, b"\x00")
    offset = 84 + 8 + 16
    data = header + struct.pack(">II", offset, len(preview)) + b"\x00" * 16 + preview
    path.write_bytes(data)
    return path


# ---------- design files ----------


def psd(path: Path, size=(40, 30), color=(10, 200, 30), thumb: bytes | None = None,
        version: int = 1) -> Path:
    w, h = size
    header = b"8BPS" + struct.pack(">H6xHIIHH", version, 3, h, w, 8, 3)
    color_mode = struct.pack(">I", 0)
    res = b""
    if thumb:
        body = struct.pack(">IIIIIIHH", 1, 80, 60, 240, 240 * 60, len(thumb), 24, 1) + thumb
        res += b"8BIM" + struct.pack(">H", 1036) + b"\x00\x00" + struct.pack(">I", len(body)) \
            + body + (b"\x00" if len(body) % 2 else b"")
    resources = struct.pack(">I", len(res)) + res
    layers = struct.pack(">I" if version == 1 else ">Q", 0)
    pixels = b"".join(bytes([c]) * (w * h) for c in color)
    image = struct.pack(">H", 0) + pixels
    path.write_bytes(header + color_mode + resources + layers + image)
    return path


def zip_with(path: Path, entries: dict[str, bytes], first: tuple[str, bytes] | None = None,
             compression=zipfile.ZIP_DEFLATED) -> Path:
    with zipfile.ZipFile(path, "w", compression) as zf:
        if first:
            zf.writestr(first[0], first[1], compress_type=zipfile.ZIP_STORED)
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def affinity(path: Path) -> Path:
    path.write_bytes(b"\x00\xffKA" + b"\x10" * 200 + png((0, 0, 255), (50, 40)) + b"\x00" * 64)
    return path


def blend(path: Path, size=(16, 12)) -> Path:
    from PIL import Image

    w, h = size
    img = Image.new("RGBA", size, (255, 128, 0, 255))
    pixels = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()
    body = struct.pack("<ii", w, h) + pixels
    blocks = b"REND" + struct.pack("<iQii", 8, 0, 0, 1) + b"\x00" * 8
    blocks += b"TEST" + struct.pack("<iQii", len(body), 0, 0, 1) + body
    blocks += b"ENDB" + struct.pack("<iQii", 0, 0, 0, 0)
    path.write_bytes(b"BLENDER-v300" + blocks)
    return path


def dos_eps(path: Path) -> Path:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (30, 20), (90, 10, 160)).save(buf, "TIFF")
    tiff = buf.getvalue()
    ps = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 30 20\nshowpage\n"
    head_len = 30
    header = b"\xc5\xd0\xd3\xc6" + struct.pack("<6I", head_len, len(ps), 0, 0,
                                               head_len + len(ps), len(tiff)) + b"\xff\xff"
    path.write_bytes(header + ps + tiff)
    return path


def epsi(path: Path) -> Path:
    # 8x2, 1-bit: first row all ink (black), second row none
    path.write_bytes(
        b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 8 2\n"
        b"%%BeginPreview: 8 2 1 2\n% FF\n% 00\n%%EndPreview\nshowpage\n"
    )
    return path


def pdf_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (100, 60), (255, 0, 0)).save(buf, "PDF")
    return buf.getvalue()


# ---------- 3D ----------


def glb(path: Path) -> Path:
    positions = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 2, 0)
    doc = {
        "asset": {"version": "2.0", "generator": "kist-test"},
        "buffers": [{"byteLength": len(positions)}],
        "bufferViews": [{"buffer": 0, "byteLength": len(positions)}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
                       "min": [0, 0, 0], "max": [1, 2, 0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "nodes": [{"mesh": 0}], "scenes": [{"nodes": [0]}],
    }
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    bin_ = positions + b"\x00" * (-len(positions) % 4)
    total = 12 + 8 + len(js) + 8 + len(bin_)
    data = (b"glTF" + struct.pack("<II", 2, total) + struct.pack("<I", len(js)) + b"JSON" + js
            + struct.pack("<I", len(bin_)) + b"BIN\x00" + bin_)
    path.write_bytes(data)
    return path


OBJ = b"# cube-ish\no tri\nv 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 3\nf 1 2 3\nf 1 2 4\n"


def stl_binary(path: Path) -> Path:
    tri = struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0)
    path.write_bytes(b"binary stl".ljust(80, b" ") + struct.pack("<I", 2) + tri + tri)
    return path


PLY = (b"ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\n"
       b"property float z\nelement face 1\nproperty list uchar int vertex_indices\n"
       b"end_header\n0 0 0\n1 0 0\n0 1 0\n3 0 1 2\n")


# ---------- fonts ----------


def font(path: Path, family="Kist Test", variable=False) -> Path:
    """A tiny TrueType font with A-Z, a-z (fontTools' FontBuilder)."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    chars = [chr(c) for c in range(0x41, 0x5B)] + [chr(c) for c in range(0x61, 0x7B)]
    names = [".notdef"] + [f"g{ord(c)}" for c in chars]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({ord(c): f"g{ord(c)}" for c in chars})
    pen = TTGlyphPen(None)
    pen.moveTo((100, 0))
    pen.lineTo((100, 700))
    pen.lineTo((500, 700))
    pen.lineTo((500, 0))
    pen.closePath()
    box = pen.glyph()
    fb.setupGlyf({n: box for n in names})
    fb.setupHorizontalMetrics({n: (600, 100) for n in names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": family, "styleName": "Regular"})
    fb.setupOS2(usWeightClass=400)
    fb.setupPost()
    if variable:
        fb.setupFvar(axes=[("wght", 100, 400, 900, "Weight")], instances=[])
    fb.save(str(path))
    return path
