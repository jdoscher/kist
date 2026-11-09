"""ThumbHash port: byte-exact against the reference encoder.

The vectors come from running evanw/thumbhash js/thumbhash.js (rgbaToThumbHash)
under Node on the same generated images.
"""

import base64

import pytest

from kist.services import thumbhash

IMAGES = {
    "gradient_80x60": (80, 60, lambda x, y: (x * 3 % 256, y * 4 % 256, (x * y) % 256, 255)),
    "portrait_alpha_30x70": (
        30, 70, lambda x, y: (200, (x * 8) % 256, (y * 3) % 256, (x + y) * 2 % 256)
    ),
    "solid_1x1": (1, 1, lambda x, y: (10, 20, 30, 255)),
    "checker_100x100": (
        100, 100,
        lambda x, y: (255, 255, 255, 255) if ((x >> 3) + (y >> 3)) % 2 else (0, 0, 0, 255),
    ),
    "transparent_16x16": (16, 16, lambda x, y: (0, 0, 0, 0)),
}

# from the reference implementation (js/thumbhash.js)
REFERENCE = {
    "gradient_80x60": "3QcKLZhwdndwd3d3d2h3d5CQCfeI",
    "portrait_alpha_30x70": "JomGIggmkLKoeIiCj/R2dXR3cIiHeIg=",
    "solid_1x1": "hecJJwQI9wiIh4hwj3CI+AiIcH/494cP",
    "checker_100x100": "HwgCBwAREQERERERERERERERAAAAAAAA",
    "transparent_16x16": "AAiCBQAAAAAAAAAAAAAAAAAAAAAAAAAAAA==",
}


def render(w, h, f) -> bytes:
    out = bytearray()
    for y in range(h):
        for x in range(w):
            out += bytes(f(x, y))
    return bytes(out)


@pytest.mark.parametrize("name", sorted(IMAGES))
def test_matches_reference_encoder(name):
    w, h, f = IMAGES[name]
    assert thumbhash.encode_b64(w, h, render(w, h, f)) == REFERENCE[name]


def test_rejects_large_images():
    with pytest.raises(ValueError):
        thumbhash.rgba_to_thumbhash(101, 10, bytes(101 * 10 * 4))


def test_from_image_downscales_and_keeps_aspect(tmp_path):
    from PIL import Image

    path = tmp_path / "wide.png"
    Image.new("RGB", (640, 320), (200, 40, 40)).save(path)
    encoded = thumbhash.from_image(path)
    raw = base64.b64decode(encoded)
    assert 5 < len(raw) <= 40
    assert thumbhash.aspect_ratio(raw) > 1.5  # landscape survives
