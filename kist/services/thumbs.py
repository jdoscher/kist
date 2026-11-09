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


# Read video files as the container their extension names, and only from
# local files: without this ffmpeg picks a demuxer by sniffing the content,
# so a playlist saved as .mp4 could make it open other files or URLs.
VIDEO_FORMATS = {"mp4": "mov", "m4v": "mov", "mov": "mov", "webm": "matroska"}


def ffmpeg_input_args(path: Path) -> list[str]:
    args = ["-protocol_whitelist", "file"]
    fmt = VIDEO_FORMATS.get(path.suffix.lstrip(".").lower())
    if fmt:
        args += ["-f", fmt]
    return args + ["-i", str(path)]


def probe_video(path: Path) -> Probe:
    p = Probe()
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_streams", "-show_format", *ffmpeg_input_args(path),
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
        "ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", str(seek),
        *ffmpeg_input_args(path), "-frames:v", "1", str(out_png),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if out.returncode != 0 or not out_png.exists():
        raise RuntimeError(f"ffmpeg poster failed: {out.stderr[:500]}")


PDF_MAX_RENDER_PX = 2048


def pdf_render_scale(width_pt: float, height_pt: float) -> float:
    """2x for normal pages, less for big ones so the poster's long side never
    exceeds PDF_MAX_RENDER_PX. A tiny PDF can declare a 200 x 200 inch page;
    rendered at 2x that's a 28,800 px square and gigabytes of memory."""
    longest = max(width_pt, height_pt)
    if not longest > 0:
        raise RuntimeError("PDF page has no size")
    return min(2.0, PDF_MAX_RENDER_PX / longest)


def pdf_poster(path: Path, out_png: Path) -> None:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[0]
        bitmap = page.render(scale=pdf_render_scale(*page.get_size()))
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
# the full-screen preview's copy: sharp on phones and most laptop screens at
# ~0.5 MB, where originals run 5-30 MB (measured 2026-11-09)
SCREEN_MAX = 2048
SCREEN_FILE = "display_2048.webp"


def needs_screen_copy(width: int | None, height: int | None) -> bool:
    """Images whose long side is over 1024 get a 2048 copy; smaller ones are
    already fully shown by thumb_1024."""
    return max(width or 0, height or 0) > 1024


def make_screen_copy(source: Path, item_dir: Path) -> None:
    import pyvips

    img = pyvips.Image.thumbnail(str(source), SCREEN_MAX, height=SCREEN_MAX, size="down")
    if img.hasalpha():
        img = img.flatten(background=[24, 24, 27])
    img.webpsave(str(item_dir / SCREEN_FILE), Q=82, smart_subsample=True)


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
    thumb_512: SHORT side is 512 — grid tiles are cover-cropped (square, or
    the image's own shape clamped to 1:2..3:1), so the short side is what has
    to cover the tile at 2x pixel density.
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
            if needs_screen_copy(probe.width, probe.height):
                make_screen_copy(original, item_dir)
        except Exception:
            if ext.lower() not in HEIF_EXTS:
                raise
            with tempfile.TemporaryDirectory() as td:
                poster = Path(td) / "decoded.png"
                heif_poster(original, poster)
                probe.width, probe.height = make_thumbs_from(poster, item_dir)
                if is_heif:
                    make_display_copy(poster, item_dir)
                if needs_screen_copy(probe.width, probe.height):
                    make_screen_copy(poster, item_dir)
    else:
        return probe  # no preview for 'other'

    return probe


PREVIEW_FILES = (
    "thumb_256.webp", "thumb_512.webp", "thumb_1024.webp", "display.webp", SCREEN_FILE,
)


def generate_previews_isolated(
    original: Path, item_dir: Path, mime: str, ext: str, *, cleanup: bool = True
) -> Probe:
    """generate_previews() in a child process with a time limit.

    The decoders (libvips, pdfium, ffmpeg, libheif) handle files from anywhere.
    In a child, a crash, a hang or a memory blow-up costs that one preview
    instead of the API or worker process. Raises on failure; with cleanup,
    leftovers from a failed attempt are removed so a new item never has half
    its previews (regen passes cleanup=False to keep an item's old previews).
    """
    from ..config import get_settings

    try:
        out = subprocess.run(
            [sys.executable, "-m", "kist.services.preview_child",
             str(original), str(item_dir), mime, ext],
            capture_output=True, text=True,
            timeout=get_settings().preview_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        if cleanup:
            _remove_previews(item_dir)
        raise RuntimeError("preview generation timed out") from None
    if out.returncode != 0:
        if cleanup:
            _remove_previews(item_dir)
        raise RuntimeError(
            f"preview generation failed (exit {out.returncode}): {out.stderr[-800:]}"
        )
    data = json.loads(out.stdout.strip().splitlines()[-1])
    probe = Probe()
    probe.width, probe.height, probe.duration_s = (
        data.get("width"), data.get("height"), data.get("duration_s"),
    )
    return probe


def screen_copy_from(original: Path, item_dir: Path, ext: str) -> None:
    """Only the 2048 copy, for items ingested before it existed. HEIC reuses
    its decoded display.webp."""
    display = item_dir / "display.webp"
    if ext.lower() in HEIF_EXTS and display.is_file():
        make_screen_copy(display, item_dir)
        return
    try:
        make_screen_copy(original, item_dir)
    except Exception:
        if ext.lower() not in HEIF_EXTS:
            raise
        with tempfile.TemporaryDirectory() as td:
            poster = Path(td) / "decoded.png"
            heif_poster(original, poster)
            make_screen_copy(poster, item_dir)


def screen_copy_isolated(original: Path, item_dir: Path, ext: str) -> None:
    """screen_copy_from() in a child process with the preview time limit."""
    from ..config import get_settings

    try:
        out = subprocess.run(
            [sys.executable, "-m", "kist.services.preview_child", "--screen",
             str(original), str(item_dir), ext],
            capture_output=True, text=True,
            timeout=get_settings().preview_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        (item_dir / SCREEN_FILE).unlink(missing_ok=True)
        raise RuntimeError("screen copy timed out") from None
    if out.returncode != 0:
        (item_dir / SCREEN_FILE).unlink(missing_ok=True)
        raise RuntimeError(f"screen copy failed (exit {out.returncode}): {out.stderr[-800:]}")


def _remove_previews(item_dir: Path) -> None:
    for name in PREVIEW_FILES:
        (item_dir / name).unlink(missing_ok=True)


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
