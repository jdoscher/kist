"""ThumbHash: a ~25-byte placeholder that encodes an image's colours,
layout, aspect ratio and alpha (https://evanw.github.io/thumbhash/).

A pure-Python port of the reference encoder, `rgbaToThumbHash` in
evanw/thumbhash js/thumbhash.js (MIT, Copyright (c) 2023 Evan Wallace).
JavaScript's Math.round rounds halves up, unlike Python's round(); `_round`
keeps that, so the bytes match the reference exactly (tests/test_thumbhash.py
checks vectors produced by the reference code).
"""

import base64
import math
from pathlib import Path

MAX_SIDE = 100


def _round(x: float) -> int:
    return math.floor(x + 0.5)


def rgba_to_thumbhash(w: int, h: int, rgba: bytes | bytearray | list[int]) -> bytes:
    """Encode a w x h RGBA image (not premultiplied, both sides <= 100)."""
    if w > MAX_SIDE or h > MAX_SIDE:
        raise ValueError(f"{w}x{h} doesn't fit in 100x100")
    if len(rgba) != w * h * 4:
        raise ValueError("rgba must have w*h*4 values")
    n = w * h

    avg_r = avg_g = avg_b = avg_a = 0.0
    for j in range(0, n * 4, 4):
        alpha = rgba[j + 3] / 255
        avg_r += alpha / 255 * rgba[j]
        avg_g += alpha / 255 * rgba[j + 1]
        avg_b += alpha / 255 * rgba[j + 2]
        avg_a += alpha
    if avg_a:
        avg_r /= avg_a
        avg_g /= avg_a
        avg_b /= avg_a

    has_alpha = avg_a < n
    l_limit = 5 if has_alpha else 7  # fewer luminance bits if there's alpha
    lx = max(1, _round(l_limit * w / max(w, h)))
    ly = max(1, _round(l_limit * h / max(w, h)))
    lum: list[float] = [0.0] * n
    p: list[float] = [0.0] * n  # yellow - blue
    q: list[float] = [0.0] * n  # red - green
    a: list[float] = [0.0] * n

    # RGBA -> LPQA, composited atop the average colour
    for i in range(n):
        j = i * 4
        alpha = rgba[j + 3] / 255
        r = avg_r * (1 - alpha) + alpha / 255 * rgba[j]
        g = avg_g * (1 - alpha) + alpha / 255 * rgba[j + 1]
        b = avg_b * (1 - alpha) + alpha / 255 * rgba[j + 2]
        lum[i] = (r + g + b) / 3
        p[i] = (r + g) / 2 - b
        q[i] = r - g
        a[i] = alpha

    def encode_channel(channel: list[float], nx: int, ny: int):
        dc = 0.0
        ac: list[float] = []
        scale = 0.0
        for cy in range(ny):
            cx = 0
            while cx * ny < nx * (ny - cy):
                fx = [math.cos(math.pi / w * cx * (x + 0.5)) for x in range(w)]
                f = 0.0
                for y in range(h):
                    fy = math.cos(math.pi / h * cy * (y + 0.5))
                    row = y * w
                    for x in range(w):
                        f += channel[x + row] * fx[x] * fy
                f /= n
                if cx or cy:
                    ac.append(f)
                    scale = max(scale, abs(f))
                else:
                    dc = f
                cx += 1
        if scale:
            ac = [0.5 + 0.5 / scale * v for v in ac]
        return dc, ac, scale

    l_dc, l_ac, l_scale = encode_channel(lum, max(3, lx), max(3, ly))
    p_dc, p_ac, p_scale = encode_channel(p, 3, 3)
    q_dc, q_ac, q_scale = encode_channel(q, 3, 3)
    a_dc, a_ac, a_scale = encode_channel(a, 5, 5) if has_alpha else (0.0, [], 0.0)

    is_landscape = w > h
    header24 = (
        _round(63 * l_dc)
        | (_round(31.5 + 31.5 * p_dc) << 6)
        | (_round(31.5 + 31.5 * q_dc) << 12)
        | (_round(31 * l_scale) << 18)
        | (int(has_alpha) << 23)
    )
    header16 = (
        (ly if is_landscape else lx)
        | (_round(63 * p_scale) << 3)
        | (_round(63 * q_scale) << 9)
        | (int(is_landscape) << 15)
    )
    out = [header24 & 255, (header24 >> 8) & 255, header24 >> 16, header16 & 255, header16 >> 8]
    ac_start = 6 if has_alpha else 5
    if has_alpha:
        out.append(_round(15 * a_dc) | (_round(15 * a_scale) << 4))

    ac_index = 0
    for channel_ac in [l_ac, p_ac, q_ac] + ([a_ac] if has_alpha else []):
        for f in channel_ac:
            pos = ac_start + (ac_index >> 1)
            while len(out) <= pos:
                out.append(0)
            out[pos] |= _round(15 * f) << ((ac_index & 1) << 2)
            ac_index += 1
    return bytes(b & 255 for b in out)


def encode_b64(w: int, h: int, rgba) -> str:
    return base64.b64encode(rgba_to_thumbhash(w, h, rgba)).decode("ascii")


def from_image(path: Path) -> str:
    """ThumbHash (base64) of an image file, from a <=100 px downscale."""
    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("RGBA")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        return encode_b64(img.width, img.height, img.tobytes())


def aspect_ratio(hash_bytes: bytes) -> float:
    """The approximate width/height the hash encodes (reference decoder's
    thumbHashToApproximateAspectRatio)."""
    header = hash_bytes[3]
    has_alpha = hash_bytes[2] & 0x80
    is_landscape = hash_bytes[4] & 0x80
    lx = (7 if not has_alpha else 5) if is_landscape else header & 7
    ly = header & 7 if is_landscape else (7 if not has_alpha else 5)
    return lx / ly
