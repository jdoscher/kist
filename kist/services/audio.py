"""Audio items (Milestone 5): waveform peaks and a waveform thumbnail.

items/<id>/waveform.json follows the BBC audiowaveform JSON format
(version 2, one channel, 8-bit min/max pairs), so web players that read
that format can use it as is. The peaks come from ffmpeg decoding the file
to 8 kHz mono, streamed: a long file never sits in memory.
"""

import json
import subprocess
from array import array
from pathlib import Path

from . import video

BUCKETS = 1000
RATE = 8000
MAX_SECONDS = 6 * 3600  # longer files: the waveform covers the first 6 hours


def peaks(path: Path, mime: str, ext: str, duration: float | None,
          buckets: int = BUCKETS) -> dict:
    """min/max pairs scaled to -128..127 over `buckets` equal slices."""
    seconds = min(duration or MAX_SECONDS, MAX_SECONDS)
    total = max(1, int(seconds * RATE))
    per = max(1, total // buckets)
    cmd = video._wrap(
        ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-threads", video.threads(),
         *video.input_args(path, mime, ext), "-t", str(int(seconds) + 1), "-vn", "-ac", "1",
         "-ar", str(RATE), "-f", "s16le", "-c:a", "pcm_s16le", "pipe:1"],
        "scrub",
    )
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    data: list[int] = []
    buf = array("h")
    leftover = b""
    try:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(1 << 18)
            if not chunk:
                break
            chunk = leftover + chunk
            cut = len(chunk) - (len(chunk) % 2)
            leftover = chunk[cut:]
            buf.frombytes(chunk[:cut])
            pos = 0
            while len(buf) - pos >= per:  # min/max of each slice run in C
                seg = buf[pos:pos + per]
                data += [min(seg) >> 8, max(seg) >> 8]
                pos += per
            del buf[:pos]
        proc.wait(timeout=600)
    finally:
        if proc.poll() is None:
            proc.kill()
    if proc.returncode != 0:
        err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "replace")
        raise RuntimeError(f"ffmpeg waveform failed: {err[-500:]}")
    if len(buf):
        data += [min(buf) >> 8, max(buf) >> 8]
    if not data:
        data = [0, 0]
    return {"version": 2, "channels": 1, "sample_rate": RATE, "samples_per_pixel": per,
            "bits": 8, "length": len(data) // 2, "data": data}


def waveform_png(wave: dict, out: Path, size: tuple[int, int] = (1600, 400)) -> None:
    from PIL import Image, ImageDraw

    w, h = size
    img = Image.new("RGB", size, (24, 24, 27))
    draw = ImageDraw.Draw(img)
    data = wave["data"]
    n = max(1, len(data) // 2)
    mid = h / 2
    # the picture is scaled to the loudest peak, so quiet files are visible too
    scale = 128 / max(8, max((abs(v) for v in data), default=8))
    for x in range(w):
        i = min(n - 1, int(x * n / w))
        lo, hi = data[2 * i], data[2 * i + 1]
        y0 = mid - (hi * scale / 128) * (mid - 8)
        y1 = mid - (lo * scale / 128) * (mid - 8)
        draw.line((x, min(y0, mid - 1), x, max(y1, mid + 1)), fill=(94, 160, 255))
    img.save(out, "PNG")


def generate(path: Path, mime: str, ext: str, item_dir: Path, info: dict) -> Path:
    """waveform.json + a PNG for the thumbnails. Returns the PNG path."""
    wave = peaks(path, mime, ext, info.get("duration"))
    (item_dir / "waveform.json").write_text(json.dumps(wave, separators=(",", ":")))
    png = item_dir / ".waveform.png"
    waveform_png(wave, png)
    return png
