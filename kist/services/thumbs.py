"""Preview generation: probing, thumbnails, perceptual hash, dominant colors.

All heavy deps (pyvips, pypdfium2, PIL, imagehash) are imported lazily so the
API process can serve requests even if a native lib is missing; failures here
never fail an ingest — items just end up without a preview.
"""

import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from ..filetypes import kind_for

log = logging.getLogger("kist.thumbs")

THUMB_SIZES = (256, 1024)


class Probe:
    def __init__(self) -> None:
        self.width: int | None = None
        self.height: int | None = None
        self.duration_s: float | None = None


def probe_video(path: Path) -> Probe:
    p = Probe()
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_streams", "-show_format", str(path),
        ],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {out.stderr[:500]}")
    data = json.loads(out.stdout)
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            p.width = stream.get("width")
            p.height = stream.get("height")
            break
    dur = data.get("format", {}).get("duration")
    if dur is not None:
        p.duration_s = round(float(dur), 3)
    return p


def video_poster(path: Path, out_png: Path, duration_s: float | None) -> None:
    seek = min(1.0, (duration_s or 0) * 0.25) if duration_s else 0
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-ss", str(seek), "-i", str(path),
        "-frames:v", "1", str(out_png),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if out.returncode != 0 or not out_png.exists():
        raise RuntimeError(f"ffmpeg poster failed: {out.stderr[:500]}")


def pdf_poster(path: Path, out_png: Path) -> None:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[0]
        bitmap = page.render(scale=2.0)
        bitmap.to_pil().save(out_png)
    finally:
        pdf.close()


HEIF_EXTS = {"heic", "heif", "avif"}


_HEIF_DECODE = """
import sys
from pillow_heif import register_heif_opener
register_heif_opener()
from PIL import Image, ImageOps
with Image.open(sys.argv[1]) as im:
    ImageOps.exif_transpose(im).convert("RGB").save(sys.argv[2])
"""


def heif_poster(path: Path, out_png: Path) -> None:
    """Decode HEIF/AVIF via pillow-heif (bundles a modern libheif).

    Fallback for files Debian's older libheif rejects — e.g. iPhone photos
    with HDR gain map + depth map auxiliary images. Runs in a subprocess:
    once pyvips has loaded the system libheif, pillow-heif's bundled copy
    resolves to the old symbols in-process and fails the same way.
    """
    out = subprocess.run(
        [sys.executable, "-c", _HEIF_DECODE, str(path), str(out_png)],
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode != 0 or not out_png.exists():
        raise RuntimeError(f"pillow-heif decode failed: {out.stderr[-500:]}")


GRID_SHORT_SIDE = 512
GRID_MAX_LONG_SIDE = 2048


def _save_webp(thumb, path: Path) -> None:
    if thumb.hasalpha():
        thumb = thumb.flatten(background=[24, 24, 27])
    # smart_subsample keeps colored edges crisp (plain 4:2:0 smears them)
    thumb.webpsave(str(path), Q=86, smart_subsample=True)


DISPLAY_MAX = 4096


def make_display_copy(source: Path, item_dir: Path) -> None:
    """Full-res WebP for formats browsers can't show (HEIC), so the
    full-screen preview stays sharp on hi-dpi displays."""
    import pyvips

    img = pyvips.Image.thumbnail(str(source), DISPLAY_MAX, height=DISPLAY_MAX, size="down")
    if img.hasalpha():
        img = img.flatten(background=[24, 24, 27])
    img.webpsave(str(item_dir / "display.webp"), Q=88, smart_subsample=True)


def make_thumbs_from(source: Path, item_dir: Path) -> tuple[int | None, int | None]:
    """Generate thumbnails from a raster-able source. Returns (width, height).

    thumb_256 / thumb_1024: longest side fits the size (previews, MCP).
    thumb_512: SHORT side is 512 — the grid crops to square tiles, so the
    short side is what has to cover the tile at 2x pixel density.
    """
    import pyvips

    probe_img = pyvips.Image.new_from_file(str(source), access="sequential")
    width, height = probe_img.width, probe_img.height

    for size in THUMB_SIZES:
        thumb = pyvips.Image.thumbnail(str(source), size, height=size, size="down")
        _save_webp(thumb, item_dir / f"thumb_{size}.webp")

    short, long_ = min(width, height), max(width, height)
    scale = min(GRID_SHORT_SIDE / short, GRID_MAX_LONG_SIDE / long_)
    box_w = max(1, round(width * scale))
    box_h = max(1, round(height * scale))
    grid = pyvips.Image.thumbnail(str(source), box_w, height=box_h, size="down")
    _save_webp(grid, item_dir / f"thumb_{GRID_SHORT_SIDE}.webp")
    return width, height


def compute_phash(image_path: Path) -> int | None:
    import imagehash
    from PIL import Image

    with Image.open(image_path) as img:
        h = imagehash.phash(img)
    value = int(str(h), 16)
    if value >= 1 << 63:  # store as signed bigint
        value -= 1 << 64
    return value


def dominant_colors(image_path: Path, count: int = 5) -> list[dict]:
    from PIL import Image

    with Image.open(image_path) as img:
        small = img.convert("RGB").resize((100, 100))
        quantized = small.quantize(count)
        palette = quantized.getpalette()
        counts = sorted(quantized.getcolors(), reverse=True)
        total = sum(n for n, _ in counts)
        out = []
        for n, idx in counts[:count]:
            r, g, b = palette[idx * 3 : idx * 3 + 3]
            out.append({"hex": f"#{r:02x}{g:02x}{b:02x}", "pct": round(n / total, 3)})
        return out


def generate_previews(original: Path, item_dir: Path, mime: str, ext: str) -> Probe:
    """Produce thumbnails + probe data for the original file.

    Raises on failure for allowed formats; callers treat failures as
    'no preview' rather than failing the ingest.
    """
    kind = kind_for(mime, ext)
    probe = Probe()

    if kind == "video":
        probe = probe_video(original)
        with tempfile.TemporaryDirectory() as td:
            poster = Path(td) / "poster.png"
            video_poster(original, poster, probe.duration_s)
            make_thumbs_from(poster, item_dir)
    elif kind == "pdf":
        with tempfile.TemporaryDirectory() as td:
            poster = Path(td) / "page1.png"
            pdf_poster(original, poster)
            probe.width, probe.height = make_thumbs_from(poster, item_dir)
    elif kind == "image":
        is_heif = ext.lower() in HEIF_EXTS and ext.lower() != "avif"
        try:
            probe.width, probe.height = make_thumbs_from(original, item_dir)
            if is_heif:
                make_display_copy(original, item_dir)
        except Exception:
            if ext.lower() not in HEIF_EXTS:
                raise
            with tempfile.TemporaryDirectory() as td:
                poster = Path(td) / "decoded.png"
                heif_poster(original, poster)
                probe.width, probe.height = make_thumbs_from(poster, item_dir)
                if is_heif:
                    make_display_copy(poster, item_dir)
    else:
        return probe  # no preview for 'other'

    return probe


def enrich(item_dir: Path) -> tuple[int | None, list[dict] | None]:
    """Perceptual hash + dominant colors, computed from generated thumbs."""
    phash = None
    colors = None
    big = item_dir / "thumb_1024.webp"
    small = item_dir / "thumb_256.webp"
    try:
        if big.exists():
            phash = compute_phash(big)
    except Exception:
        log.exception("phash failed for %s", item_dir)
    try:
        if small.exists():
            colors = dominant_colors(small)
    except Exception:
        log.exception("dominant colors failed for %s", item_dir)
    return phash, colors
