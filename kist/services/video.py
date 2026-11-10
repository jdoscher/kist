"""ffmpeg and ffprobe for video and audio (Milestone 5).

Every run:
- reads only local files, with the demuxer named by the item's sniffed MIME
  type (`-protocol_whitelist file -f <fmt>`): ffmpeg never guesses a format
  from the content, so a playlist or concat script saved as .mp4 can't make
  it open other files or URLs. A type without a known demuxer is refused;
- runs under `nice` (and `ionice -c3` on Linux) with a priority class:
  thumbnails first, scrub sprites next, proxies and clips last;
- uses at most KIST_FFMPEG_THREADS threads, a time limit, and an output
  size limit (-fs);
- may use QSV or VA-API (KIST_HWACCEL) for proxies and clips, falling back
  to software when the hardware path fails.
"""

import json
import logging
import math
import shutil
import subprocess
import sys
from pathlib import Path

from ..config import get_settings

log = logging.getLogger("kist.video")

# sniffed MIME type -> ffmpeg demuxer
DEMUXERS = {
    "video/mp4": "mov", "video/quicktime": "mov", "video/x-m4v": "mov", "video/3gpp": "mov",
    "video/webm": "matroska", "video/x-matroska": "matroska",
    "video/x-msvideo": "avi", "video/avi": "avi", "video/mpeg": "mpeg", "video/mp2t": "mpegts",
    "video/x-flv": "flv", "video/ogg": "ogg",
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/x-wav": "wav", "audio/wav": "wav",
    "audio/vnd.wave": "wav", "audio/flac": "flac", "audio/x-flac": "flac", "audio/ogg": "ogg",
    "audio/x-aiff": "aiff", "audio/aiff": "aiff", "audio/mp4": "mov", "audio/x-m4a": "mov",
    "audio/aac": "aac", "audio/x-hx-aac-adts": "aac", "audio/x-aac": "aac",
    "audio/webm": "matroska",
    "audio/opus": "ogg",
    "image/gif": "gif",
}
# some valid MP4/MOV files sniff as application/octet-stream (filetypes.VIDEO_EXTS)
EXT_DEMUXERS = {"mp4": "mov", "m4v": "mov", "mov": "mov", "webm": "matroska"}

# priority classes: nice levels
PRIORITY = {"thumb": 5, "scrub": 10, "heavy": 15}

OUTPUT_LIMIT_BYTES = 8 * 1024**3


class Unsupported(ValueError):
    """No safe way to read this file with ffmpeg."""


def demuxer(mime: str, ext: str) -> str:
    fmt = DEMUXERS.get(mime) or (
        EXT_DEMUXERS.get(ext.lower()) if mime == "application/octet-stream" else None
    )
    if fmt is None:
        raise Unsupported(f"ffmpeg isn't used for {mime} files")
    return fmt


def input_args(path: Path, mime: str, ext: str, seek: float | None = None) -> list[str]:
    args = ["-protocol_whitelist", "file", "-f", demuxer(mime, ext)]
    if seek is not None and seek > 0:
        args += ["-ss", f"{seek:.3f}"]
    return args + ["-i", str(path)]


def available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _wrap(cmd: list[str], priority: str) -> list[str]:
    prefix: list[str] = []
    if shutil.which("nice"):
        prefix += ["nice", "-n", str(PRIORITY.get(priority, 10))]
    if priority == "heavy" and sys.platform.startswith("linux") and shutil.which("ionice"):
        prefix += ["ionice", "-c3"]
    return prefix + cmd


def threads() -> str:
    return str(max(1, get_settings().ffmpeg_threads))


def run_ffmpeg(args: list[str], *, priority: str = "thumb", timeout: float = 300,
               capture_stdout: bool = False) -> subprocess.CompletedProcess:
    cmd = _wrap(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y",
                 "-threads", threads(), *args], priority)
    out = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if out.returncode != 0:
        err = out.stderr.decode("utf-8", "replace")[-800:]
        raise RuntimeError(f"ffmpeg failed: {err.strip() or out.returncode}")
    return out


# ---------- probe ----------


def ffprobe(path: Path, mime: str, ext: str) -> dict:
    cmd = _wrap(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                 "-show_format", *input_args(path, mime, ext)], "thumb")
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {out.stderr[:500]}")
    return json.loads(out.stdout or "{}")


def _fps(stream: dict) -> float | None:
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = stream.get(key) or ""
        if "/" in raw:
            num, den = raw.split("/", 1)
            try:
                if float(den) > 0 and float(num) > 0:
                    return round(float(num) / float(den), 3)
            except ValueError:
                continue
    return None


def _rotation(stream: dict) -> int:
    rot = 0
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            rot = int(float(tags["rotate"]))
        except ValueError:
            rot = 0
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                rot = -int(float(side["rotation"]))
            except (TypeError, ValueError):
                pass
    return rot % 360


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _tag(tags: dict, *names: str) -> str | None:
    lower = {k.lower(): v for k, v in (tags or {}).items()}
    for n in names:
        v = lower.get(n)
        if isinstance(v, str) and v.strip():
            return v.strip()[:300]
    return None


def summarize(data: dict) -> dict:
    """The parts of ffprobe output Kist keeps (items.media.video/audio)."""
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    out: dict = {
        "container": fmt.get("format_name"),
        "duration": _float(fmt.get("duration")),
        "bitrate": _int(fmt.get("bit_rate")),
        "has_audio": audio is not None,
        "has_video": video is not None,
    }
    if out["duration"] is not None:
        out["duration"] = round(out["duration"], 3)
    tags = fmt.get("tags") or {}
    for key, names in (("title", ("title",)), ("artist", ("artist", "album_artist")),
                       ("album", ("album",)), ("creation_time", ("creation_time",)),
                       ("location", ("location", "com.apple.quicktime.location.iso6709"))):
        v = _tag(tags, *names)
        if v:
            out[key] = v
    if video is not None:
        w, h = _int(video.get("width")), _int(video.get("height"))
        rot = _rotation(video)
        if rot in (90, 270) and w and h:
            w, h = h, w
        out.update({
            "codec": video.get("codec_name"),
            "profile": video.get("profile"),
            "pix_fmt": video.get("pix_fmt"),
            "fps": _fps(video),
            "width": w,
            "height": h,
            "rotation": rot,
            "vertical": bool(w and h and h > w),
        })
        if out["duration"] is None:
            out["duration"] = _float(video.get("duration"))
    if audio is not None:
        out.update({
            "audio_codec": audio.get("codec_name"),
            "sample_rate": _int(audio.get("sample_rate")),
            "channels": _int(audio.get("channels")),
        })
        if out["duration"] is None:
            out["duration"] = _float(audio.get("duration"))
    return {k: v for k, v in out.items() if v is not None}


# ---------- playable in a browser? ----------

NATIVE_VIDEO = {
    "mov": {"h264", "av1"},
    "matroska": {"vp8", "vp9", "av1"},
}
NATIVE_AUDIO = {"aac", "mp3", "opus", "vorbis", "flac", None}
NATIVE_AUDIO_ONLY = {"mp3", "aac", "flac", "vorbis", "opus", "pcm_s16le", "pcm_u8", "pcm_f32le",
                     "pcm_s24le"}


def playable(info: dict, mime: str, ext: str) -> str:
    """native: browsers play the original; proxy: needs a web-safe copy;
    none: nothing to play."""
    try:
        fmt = demuxer(mime, ext)
    except Unsupported:
        return "none"
    if not info.get("has_video"):
        if not info.get("has_audio"):
            return "none"
        if fmt in ("mp3", "flac", "ogg", "wav", "mov", "aac", "matroska") and (
            info.get("audio_codec") in NATIVE_AUDIO_ONLY
        ):
            return "native"
        return "proxy"
    codec = info.get("codec")
    native = codec in NATIVE_VIDEO.get(fmt, set())
    if codec == "h264" and info.get("pix_fmt") not in (None, "yuv420p", "yuvj420p"):
        native = False  # 10-bit / 4:2:2 H.264 doesn't play in most browsers
    if info.get("has_audio") and info.get("audio_codec") not in NATIVE_AUDIO:
        native = False
    if mime == "video/webm" and codec not in ("vp8", "vp9", "av1"):
        native = False
    return "native" if native else "proxy"


# ---------- frames ----------


def frame_png(path: Path, mime: str, ext: str, t: float, *, width: int | None = None,
              priority: str = "thumb", timeout: float = 120) -> bytes:
    """One frame at `t` seconds as PNG bytes (full size unless width)."""
    vf = ["-vf", f"scale={int(width)}:-2:flags=bicubic"] if width else []
    out = run_ffmpeg(
        [*input_args(path, mime, ext, seek=t), "-frames:v", "1", *vf,
         "-f", "image2pipe", "-c:v", "png", "pipe:1"],
        priority=priority, timeout=timeout, capture_stdout=True,
    )
    if not out.stdout:
        if t > 0:  # past the last frame: take the first one instead
            return frame_png(path, mime, ext, 0, width=width, priority=priority, timeout=timeout)
        raise RuntimeError("ffmpeg returned no frame")
    return out.stdout


def default_cover_time(duration: float | None) -> float:
    """Kist 1.x's poster time: 1 s in, or a quarter in for short clips."""
    return min(1.0, duration * 0.25) if duration else 0.0


def scrub_times(duration: float | None, frames: int) -> list[float]:
    if not duration or duration <= 0:
        return [0.0]
    n = max(1, frames)
    return [round(duration * (i + 0.5) / n, 3) for i in range(n)]


SCRUB_WIDTH = 256
SCRUB_COLS = 5


def sprite(frames: list, times: list[float], out: Path, frame_w: int = SCRUB_WIDTH) -> dict:
    """Tile PIL images into one WebP; returns the scrub.json data."""
    from PIL import Image

    first = frames[0]
    frame_h = max(2, round(first.height * frame_w / max(1, first.width)))
    frame_h += frame_h % 2
    cols = min(SCRUB_COLS, len(frames))
    rows = math.ceil(len(frames) / cols)
    sheet = Image.new("RGB", (cols * frame_w, rows * frame_h), (24, 24, 27))
    for n, im in enumerate(frames):
        tile = im.convert("RGB").resize((frame_w, frame_h), Image.Resampling.BICUBIC)
        sheet.paste(tile, ((n % cols) * frame_w, (n // cols) * frame_h))
    sheet.save(out, "WEBP", quality=72, method=4)
    return {"frames": len(frames), "cols": cols, "rows": rows, "frame_w": frame_w,
            "frame_h": frame_h, "times": times}


def video_scrub(path: Path, mime: str, ext: str, duration: float | None, item_dir: Path,
                frames: int | None = None) -> dict:
    """items/<id>/scrub.webp + scrub.json from evenly spaced frames. Each
    frame is a fast input seek, so a long video is never decoded in full."""
    import io

    from PIL import Image

    count = frames or get_settings().scrub_frames
    times = scrub_times(duration, count)
    images = []
    for t in times:
        data = frame_png(path, mime, ext, t, width=SCRUB_WIDTH, priority="scrub")
        images.append(Image.open(io.BytesIO(data)))
    return write_scrub(images, times, item_dir)


def write_scrub(images: list, times: list[float], item_dir: Path) -> dict:
    info = sprite(images, times, item_dir / "scrub.webp")
    (item_dir / "scrub.json").write_text(json.dumps(info))
    return {k: v for k, v in info.items() if k != "times"}


ANIM_MAX_FRAMES = 5000
ANIM_MAX_PIXELS = 400_000_000  # frames x width x height decoded for the sprite


def animation_scrub(path: Path, item_dir: Path, frames: int | None = None) -> dict | None:
    """The same sprite for animated GIF / WebP (decoded with Pillow). None
    for a still image."""
    from PIL import Image, ImageSequence

    with Image.open(path) as im:
        n_frames = getattr(im, "n_frames", 1)
        if n_frames <= 1:
            return None
        if n_frames > ANIM_MAX_FRAMES or n_frames * im.width * im.height > ANIM_MAX_PIXELS:
            n_frames = min(n_frames, max(2, ANIM_MAX_PIXELS // max(1, im.width * im.height)),
                           ANIM_MAX_FRAMES)
        starts, t = [], 0.0
        for n, frame in enumerate(ImageSequence.Iterator(im)):
            if n >= n_frames:
                break
            starts.append(t)
            t += max(0.02, (frame.info.get("duration") or 100) / 1000)
        total = t
        count = min(frames or get_settings().scrub_frames, len(starts))
        picks = sorted({min(len(starts) - 1, int(len(starts) * (i + 0.5) / count))
                        for i in range(count)})
        images = []
        for idx in picks:
            im.seek(idx)
            frame = im.convert("RGBA")
            bg = Image.new("RGBA", frame.size, (24, 24, 27, 255))
            bg.alpha_composite(frame)
            frame = bg.convert("RGB")
            frame.thumbnail((SCRUB_WIDTH * 2, SCRUB_WIDTH * 2))
            images.append(frame)
        times = [round(starts[i], 3) for i in picks]
    info = write_scrub(images, times, item_dir)
    info["duration"] = round(total, 3)
    info["anim_frames"] = len(starts)
    return info


def scrub_info(item_dir: Path) -> dict | None:
    try:
        return json.loads((item_dir / "scrub.json").read_text())
    except (OSError, ValueError):
        return None


def contact_sheet(item_dir: Path, frames: int = 9) -> bytes:
    """A grid of `frames` frames from the scrub sprite with each frame's time
    burned in (MCP view_video). PNG bytes."""
    import io

    from PIL import Image, ImageDraw

    info = scrub_info(item_dir)
    sprite_path = item_dir / "scrub.webp"
    if not info or not sprite_path.is_file():
        raise FileNotFoundError("this item has no scrub frames yet")
    total = int(info["frames"])
    frames = max(1, min(int(frames), total))
    picks = sorted({min(total - 1, int(total * (i + 0.5) / frames)) for i in range(frames)})
    fw, fh, cols = info["frame_w"], info["frame_h"], info["cols"]
    out_cols = math.ceil(math.sqrt(len(picks)))
    out_rows = math.ceil(len(picks) / out_cols)
    sheet = Image.new("RGB", (out_cols * fw, out_rows * fh), (24, 24, 27))
    with Image.open(sprite_path) as sp:
        sp = sp.convert("RGB")
        draw = ImageDraw.Draw(sheet)
        for n, idx in enumerate(picks):
            box = ((idx % cols) * fw, (idx // cols) * fh)
            tile = sp.crop((box[0], box[1], box[0] + fw, box[1] + fh))
            x, y = (n % out_cols) * fw, (n // out_cols) * fh
            sheet.paste(tile, (x, y))
            label = timestamp(info["times"][idx])
            draw.rectangle((x + 4, y + fh - 20, x + 8 + 7 * len(label), y + fh - 4),
                           fill=(0, 0, 0))
            draw.text((x + 6, y + fh - 18), label, fill=(255, 255, 255))
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    return buf.getvalue()


def timestamp(t: float) -> str:
    t = max(0.0, float(t))
    m, s = divmod(t, 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{s:05.2f}" if h else f"{m}:{s:05.2f}"


# ---------- clips and proxies (worker jobs) ----------

CLIP_FORMATS = {"video": ("mp4", "gif", "webp"), "audio": ("mp3", "wav")}


def _scale(max_side: int | None) -> str | None:
    if not max_side:
        return None
    m = int(max_side)
    return (f"scale='if(gt(iw,ih),min({m},iw),-2)':'if(gt(iw,ih),-2,min({m},ih))'"
            ":flags=lanczos")


def _hw_video_args(scale_to: int | None) -> list[list[str]] | None:
    """(input args, output args) for the configured hardware encoder."""
    settings = get_settings()
    mode = (settings.hwaccel or "none").lower()
    if mode == "qsv":
        vf = ([f"scale_qsv=w='if(gt(iw,ih),min({scale_to},iw),-1)'"
               f":h='if(gt(iw,ih),-1,min({scale_to},ih))'"] if scale_to else [])
        return [["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"],
                [*(["-vf", vf[0]] if vf else []), "-c:v", "h264_qsv", "-global_quality", "23",
                 "-preset", "veryfast"]]
    if mode == "vaapi":
        scale = (f"scale_vaapi=w='if(gt(iw,ih),min({scale_to},iw),-2)'"
                 f":h='if(gt(iw,ih),-2,min({scale_to},ih))':format=nv12"
                 if scale_to else "scale_vaapi=format=nv12")
        return [["-hwaccel", "vaapi", "-hwaccel_device", settings.vaapi_device,
                 "-hwaccel_output_format", "vaapi"],
                ["-vf", scale, "-c:v", "h264_vaapi", "-qp", "23"]]
    return None


def _sw_h264(scale_to: int | None) -> list[str]:
    vf = _scale(scale_to)
    return [*(["-vf", vf] if vf else []), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p"]


def encode_h264(path: Path, mime: str, ext: str, out: Path, *, start: float | None = None,
                duration: float | None = None, scale_to: int | None = None,
                timeout: float = 3600) -> str:
    """Web-safe H.264/AAC MP4 (+faststart). Returns "hw" or "sw"."""
    common_out = [*(["-t", f"{duration:.3f}"] if duration else []),
                  "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-movflags", "+faststart",
                  "-fs", str(OUTPUT_LIMIT_BYTES), "-f", "mp4", str(out)]
    hw = _hw_video_args(scale_to)
    if hw is not None:
        try:
            run_ffmpeg([*hw[0], *input_args(path, mime, ext, seek=start), *hw[1], *common_out],
                       priority="heavy", timeout=timeout)
            return "hw"
        except Exception as exc:  # no device, driver trouble, unsupported input
            log.warning("hardware encode failed, using software: %s", exc)
            out.unlink(missing_ok=True)
    run_ffmpeg([*input_args(path, mime, ext, seek=start), *_sw_h264(scale_to), *common_out],
               priority="heavy", timeout=timeout)
    return "sw"


def make_clip(path: Path, mime: str, ext: str, out: Path, *, start: float, end: float,
              fmt: str, max_side: int | None) -> None:
    duration = end - start
    if fmt == "mp4":
        encode_h264(path, mime, ext, out, start=start, duration=duration, scale_to=max_side)
        return
    if fmt == "gif":
        scale = _scale(max_side or 480) or ""
        run_ffmpeg([*input_args(path, mime, ext, seek=start), "-t", f"{duration:.3f}",
                    "-filter_complex",
                    f"fps=12,{scale},split[a][b];[a]palettegen=stats_mode=diff[p];"
                    "[b][p]paletteuse=dither=bayer:bayer_scale=4",
                    "-loop", "0", "-fs", str(OUTPUT_LIMIT_BYTES), "-f", "gif", str(out)],
                   priority="heavy", timeout=1800)
        return
    if fmt == "webp":
        scale = _scale(max_side or 640)
        run_ffmpeg([*input_args(path, mime, ext, seek=start), "-t", f"{duration:.3f}",
                    "-vf", f"fps=15,{scale}", "-an", "-c:v", "libwebp_anim", "-loop", "0",
                    "-quality", "70", "-fs", str(OUTPUT_LIMIT_BYTES), "-f", "webp", str(out)],
                   priority="heavy", timeout=1800)
        return
    if fmt == "mp3":
        run_ffmpeg([*input_args(path, mime, ext, seek=start), "-t", f"{duration:.3f}", "-vn",
                    "-c:a", "libmp3lame", "-q:a", "2", "-fs", str(OUTPUT_LIMIT_BYTES),
                    "-f", "mp3", str(out)], priority="heavy", timeout=1800)
        return
    if fmt == "wav":
        run_ffmpeg([*input_args(path, mime, ext, seek=start), "-t", f"{duration:.3f}", "-vn",
                    "-c:a", "pcm_s16le", "-fs", str(OUTPUT_LIMIT_BYTES), "-f", "wav", str(out)],
                   priority="heavy", timeout=1800)
        return
    raise ValueError(f"unknown clip format {fmt!r}")


def make_proxy(path: Path, mime: str, ext: str, item_dir: Path, has_video: bool) -> Path:
    """items/<id>/proxy.mp4 (H.264/AAC) or proxy.m4a (AAC, audio only)."""
    if has_video:
        out = item_dir / "proxy.mp4"
        tmp = item_dir / ".proxy.tmp.mp4"
        encode_h264(path, mime, ext, tmp, scale_to=get_settings().proxy_max_side)
    else:
        out = item_dir / "proxy.m4a"
        tmp = item_dir / ".proxy.tmp.m4a"
        run_ffmpeg([*input_args(path, mime, ext), "-vn", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", "-fs", str(OUTPUT_LIMIT_BYTES), "-f", "mp4",
                    str(tmp)], priority="heavy", timeout=3600)
    tmp.replace(out)
    return out


def proxy_path(item_dir: Path) -> Path | None:
    for name in ("proxy.mp4", "proxy.m4a"):
        if (item_dir / name).is_file():
            return item_dir / name
    return None
