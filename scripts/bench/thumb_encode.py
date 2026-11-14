#!/usr/bin/env python3
"""Compare thumbnail encodes on real originals (perf P3).

    python scripts/bench/thumb_encode.py --originals DIR [--out DIR] [--jobs 4]

DIR holds the originals and a manifest.json ([{file, category, ...}]); every
original is made into the two grid sizes Kist serves (thumb_512: short side
512, long side at most 2048; thumb_1024: long side 1024) exactly as
services/thumbs.py resizes, then each candidate encode is decoded again and
scored against that resized reference:

- bytes and encode time
- SSIM on luma (Gaussian 11x11, sigma 1.5, Wang et al. 2004), its 1st
  percentile over the image (the worst local patches: banding on gradients,
  ringing around product edges) and SSIM on the chroma planes (smeared
  coloured edges)

Bench-only dependencies: numpy and scipy (not Kist runtime deps). Writes
<out>/results.json and prints a summary per category and candidate.
"""

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

GRID_SHORT_SIDE, GRID_MAX_LONG_SIDE = 512, 2048

# label -> (format, options)
CANDIDATES = {
    "webp_q86": ("webp", {"Q": 86, "smart_subsample": True}),  # Kist <= cd27cb7
    "webp_q80_e6": ("webp", {"Q": 80, "smart_subsample": True, "effort": 6}),
    "webp_q76_e6": ("webp", {"Q": 76, "smart_subsample": True, "effort": 6}),
    "webp_q72_e6": ("webp", {"Q": 72, "smart_subsample": True, "effort": 6}),
    "webp_q80_e4": ("webp", {"Q": 80, "smart_subsample": True}),
    "webp_q82_e6": ("webp", {"Q": 82, "smart_subsample": True, "effort": 6}),
    "webp_q78_e4": ("webp", {"Q": 78, "smart_subsample": True}),
    "avif_q60": ("avif", {"Q": 60, "compression": "av1", "effort": 4}),
    "avif_q50": ("avif", {"Q": 50, "compression": "av1", "effort": 4}),
}


def load(path: Path):
    import pyvips

    img = pyvips.Image.new_from_file(str(path))
    return img.width, img.height


def resized(path: Path, size: str):
    import pyvips

    w, h = load(path)
    if size == "1024":
        img = pyvips.Image.thumbnail(str(path), 1024, height=1024, size="down")
    else:
        short, long_ = min(w, h), max(w, h)
        scale = min(GRID_SHORT_SIDE / short, GRID_MAX_LONG_SIDE / long_)
        img = pyvips.Image.thumbnail(str(path), max(1, round(w * scale)),
                                     height=max(1, round(h * scale)), size="down")
    if img.hasalpha():
        img = img.flatten(background=[24, 24, 27])
    if img.bands == 1:
        img = img.colourspace("srgb")
    if img.interpretation not in ("srgb", "b-w"):
        img = img.colourspace("srgb")
    return img.cast("uchar").copy_memory()


def to_array(img):
    import numpy as np

    return np.ndarray(buffer=img.write_to_memory(), dtype=np.uint8,
                      shape=(img.height, img.width, img.bands))[:, :, :3].astype(np.float64)


def ycc(a):
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return y, cb, cr


def ssim_map(x, y):
    from scipy.ndimage import gaussian_filter

    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    f = lambda v: gaussian_filter(v, 1.5, truncate=3.5)  # noqa: E731 (11x11)
    mx, my = f(x), f(y)
    sxx, syy, sxy = f(x * x) - mx * mx, f(y * y) - my * my, f(x * y) - mx * my
    return ((2 * mx * my + c1) * (2 * sxy + c2)) / ((mx * mx + my * my + c1) * (sxx + syy + c2))


def encode(img, fmt: str, opts: dict) -> bytes:
    if fmt == "webp":
        return img.webpsave_buffer(**opts)
    return img.heifsave_buffer(**opts)


SCREEN_SOURCE: list[str] = []  # candidates also made from a display_2048-like copy


def screen_copy(path: Path, work: Path) -> Path:
    """Kist's display_2048.webp (thumbs.make_screen_copy: 2048 long side, Q82)."""
    import pyvips

    p = work / "display_2048.webp"
    img = pyvips.Image.thumbnail(str(path), 2048, height=2048, size="down")
    if img.hasalpha():
        img = img.flatten(background=[24, 24, 27])
    img.webpsave(str(p), Q=82, smart_subsample=True)
    return p


def study(args):
    path, category, out, screen_source = args
    SCREEN_SOURCE[:] = screen_source
    import numpy as np
    import pyvips

    rows = []
    for size in ("512", "1024"):
        ref = resized(path, size)
        ra = to_array(ref)
        ry, rcb, rcr = ycc(ra)
        work = out / "files" / path.stem
        work.mkdir(parents=True, exist_ok=True)
        ref.pngsave(str(work / f"ref_{size}.png"))
        for label, (fmt, opts) in CANDIDATES.items():
            t = time.perf_counter()
            data = encode(ref, fmt, opts)
            secs = time.perf_counter() - t
            (work / f"{label}_{size}.{fmt}").write_bytes(data)
            dec = to_array(pyvips.Image.new_from_buffer(data, "").colourspace("srgb")
                           .cast("uchar"))
            dy, dcb, dcr = ycc(dec)
            m = ssim_map(ry, dy)
            rows.append({
                "file": path.name, "category": category, "size": size, "candidate": label,
                "source": "original",
                "w": ref.width, "h": ref.height, "bytes": len(data), "encode_s": secs,
                "ssim_y": float(m.mean()), "ssim_y_p1": float(np.percentile(m, 1)),
                "ssim_c": float((ssim_map(rcb, dcb).mean() + ssim_map(rcr, dcr).mean()) / 2),
            })
        w0, h0 = load(path)
        for label in SCREEN_SOURCE:
            if max(w0, h0) <= 1024:
                continue  # Kist makes no 2048 copy for these
            fmt, opts = CANDIDATES[label]
            src2 = screen_copy(path, work)
            t = time.perf_counter()
            img2 = resized(src2, size)
            data = encode(img2, fmt, opts)
            secs = time.perf_counter() - t
            (work / f"{label}@screen_{size}.{fmt}").write_bytes(data)
            dec = to_array(pyvips.Image.new_from_buffer(data, "").colourspace("srgb")
                           .cast("uchar"))
            # the reference at exactly this copy's size (a 2048 copy can make
            # the box a pixel off); resampling the result instead would blur it
            r2 = ra
            if (img2.width, img2.height) != (ref.width, ref.height):
                full = pyvips.Image.thumbnail(str(path), img2.width, height=img2.height,
                                              size="force")
                if full.hasalpha():
                    full = full.flatten(background=[24, 24, 27])
                r2 = to_array(full.colourspace("srgb").cast("uchar").copy_memory())
            ry2, rcb2, rcr2 = ycc(r2)
            dy, dcb, dcr = ycc(dec)
            m = ssim_map(ry2, dy)
            rows.append({
                "file": path.name, "category": category, "size": size,
                "candidate": label + "@screen", "source": "display_2048",
                "w": img2.width, "h": img2.height, "bytes": len(data), "encode_s": secs,
                "ssim_y": float(m.mean()), "ssim_y_p1": float(np.percentile(m, 1)),
                "ssim_c": float((ssim_map(rcb2, dcb).mean() + ssim_map(rcr2, dcr).mean()) / 2),
            })
    return rows


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--originals", required=True)
    p.add_argument("--out", default="thumb-encode")
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--only", help="comma-separated candidate labels")
    p.add_argument("--screen-source", help="candidates to also make from a display_2048 copy "
                   "(the backfill's source), scored against the original-made reference")
    p.add_argument("--decoded", help="DIR of <stem>.png stand-ins for originals this libvips "
                   "can't open (HEIC: decode with pillow-heif first, as Kist's fallback does)")
    args = p.parse_args(argv)
    if args.only:
        for k in list(CANDIDATES):
            if k not in args.only.split(","):
                del CANDIDATES[k]
    src = Path(args.originals)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((src / "manifest.json").read_text())
    def source(m):
        alt = Path(args.decoded or "-") / (Path(m["file"]).stem + ".png")
        return alt if alt.is_file() else src / m["file"]

    screen = [c for c in (args.screen_source or "").split(",") if c]
    tasks = [(source(m), m["category"], out, screen) for m in manifest]
    rows = []
    with ProcessPoolExecutor(args.jobs) as ex:
        for i, r in enumerate(ex.map(study, tasks), 1):
            rows += r
            print(f"  {i}/{len(tasks)} {r[0]['file']}", flush=True)
    (out / "results.json").write_text(json.dumps(rows, indent=1))
    summarise(rows)
    return 0


def summarise(rows):
    import statistics as st

    cats = sorted({r["category"] for r in rows}) + ["ALL"]
    labels = list(dict.fromkeys(r["candidate"] for r in rows))
    for size in ("512", "1024"):
        print(f"\n== thumb_{size}: avg KB / % of webp_q86 / SSIM-Y / SSIM-Y p1 / SSIM-C / ms")
        base = {(r["file"]): r["bytes"] for r in rows
                if r["size"] == size and r["candidate"] == "webp_q86"}
        for label in labels:
            sel = [r for r in rows if r["size"] == size and r["candidate"] == label]
            line = f"{label:20}"
            for cat in cats:
                cs = [r for r in sel if cat in ("ALL", r["category"])]
                if not cs:
                    continue
                kb = st.mean(r["bytes"] for r in cs) / 1024
                pct = 100 * sum(r["bytes"] for r in cs) / sum(base[r["file"]] for r in cs)
                if cat == "ALL":
                    line += (f" | ALL {kb:6.1f} KB {pct:5.1f}% "
                             f"{st.mean(r['ssim_y'] for r in cs):.4f} "
                             f"{st.mean(r['ssim_y_p1'] for r in cs):.4f} "
                             f"{st.mean(r['ssim_c'] for r in cs):.4f} "
                             f"{1000 * st.mean(r['encode_s'] for r in cs):5.0f}")
            print(line)
        print("   per category (avg KB, SSIM-Y p1):")
        for cat in cats[:-1]:
            line = f"   {cat:20}"
            for label in labels:
                cs = [r for r in rows if r["size"] == size and r["candidate"] == label
                      and r["category"] == cat]
                if not cs:
                    continue
                line += (f" {label}: {st.mean(r['bytes'] for r in cs) / 1024:5.1f}"
                         f"/{st.mean(r['ssim_y_p1'] for r in cs):.3f}")
            print(line)


if __name__ == "__main__":
    sys.exit(main())
