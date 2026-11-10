"""Content-checked MIME types for the formats libmagic can't name (Milestone 5).

libmagic calls a .sketch file "application/zip", a .glb file maybe
"application/octet-stream", a CR3 maybe "video/quicktime". refine_mime gives
them a precise type, but only after checking the bytes: the extension picks
which signature to look for, and the content has to match it. A file named
.sketch that isn't a zip holding previews/preview.png stays what libmagic
said. filetypes.kind_for then maps these types to kinds.
"""

import json
import zipfile
from pathlib import Path

RAW_MIMES = {
    "cr2": "image/x-canon-cr2",
    "cr3": "image/x-canon-cr3",
    "nef": "image/x-nikon-nef",
    "arw": "image/x-sony-arw",
    "raf": "image/x-fuji-raf",
    "orf": "image/x-olympus-orf",
    "rw2": "image/x-panasonic-rw2",
    "dng": "image/x-adobe-dng",
    "pef": "image/x-pentax-pef",
}
RAW_EXTS = frozenset(RAW_MIMES)

FONT_MIMES = {b"\x00\x01\x00\x00": "font/ttf", b"true": "font/ttf", b"OTTO": "font/otf",
              b"wOFF": "font/woff", b"wOF2": "font/woff2", b"ttcf": "font/collection"}

AUDIO_FIX = {
    # libmagic's names for audio in an MP4/ISO container
    "m4a": "audio/mp4", "aac": "audio/aac",
}

ZIP_MIMES = ("application/zip", "application/x-zip-compressed", "application/octet-stream")

MAX_SNIFF_BYTES = 64 * 1024


def _head(path: Path, n: int = 64) -> bytes:
    try:
        with path.open("rb") as f:
            return f.read(n)
    except OSError:
        return b""


def _zip_names(path: Path, limit: int = 20000) -> list[str] | None:
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > limit:
                return None
            return [i.filename for i in infos]
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
        return None


def _zip_text(path: Path, name: str, limit: int = 4096) -> str:
    try:
        with zipfile.ZipFile(path) as zf, zf.open(name) as f:
            return f.read(limit).decode("utf-8", "replace")
    except (KeyError, zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
        return ""


def _is_tiff(head: bytes) -> bool:
    return head[:4] in (b"II*\x00", b"MM\x00*")


def _raw_ok(ext: str, head: bytes) -> bool:
    """Does the content look like the RAW format its extension names?"""
    if ext in ("cr2", "nef", "arw", "dng", "pef"):
        return _is_tiff(head)
    if ext == "cr3":
        return head[4:8] == b"ftyp" and head[8:12] == b"crx "
    if ext == "raf":
        return head.startswith(b"FUJIFILMCCD-RAW")
    if ext == "orf":
        return head[:4] in (b"IIRO", b"IIRS", b"MMOR")
    if ext == "rw2":
        return head[:4] == b"IIU\x00"
    return False


def _design(path: Path, mime: str, ext: str, head: bytes) -> str | None:
    if ext in ("psd", "psb") and head.startswith(b"8BPS"):
        version = int.from_bytes(head[4:6], "big")
        return "application/x-psb" if version == 2 else "image/vnd.adobe.photoshop"
    if ext == "ai":
        if head.startswith(b"%PDF") or head.startswith(b"%!PS"):
            return "application/illustrator"
    if ext in ("eps", "epsf", "epsi"):
        if head.startswith(b"%!PS") or head[:4] == b"\xc5\xd0\xd3\xc6":
            return "application/postscript"
    if ext in ("afdesign", "afphoto", "afpub") and head[:4] == b"\x00\xffKA":
        return "application/x-affinity"
    if ext == "blend" and (head.startswith(b"BLENDER") or head[:2] == b"\x1f\x8b"
                           or head[:4] == b"\x28\xb5\x2f\xfd"):
        return "application/x-blender"
    if ext == "fig" and head.startswith(b"fig-kiwi"):
        return "application/x-figma"
    if mime in ZIP_MIMES or head[:2] == b"PK":
        names = None
        if ext in ("sketch", "procreate", "kra", "ora", "fig"):
            names = _zip_names(path)
        if names is None:
            return None
        nameset = set(names)
        if ext == "sketch" and "previews/preview.png" in nameset:
            return "application/x-sketch"
        if ext == "procreate" and "QuickLook/Thumbnail.png" in nameset:
            return "application/x-procreate"
        if ext == "kra" and "mergedimage.png" in nameset:
            return "application/x-krita"
        if ext == "ora" and ("mergedimage.png" in nameset or "stack.xml" in nameset):
            return "image/openraster"
        if ext == "fig":
            return "application/x-figma"
    if mime == "application/x-krita" or mime == "image/openraster":
        return mime
    return None


def _model(path: Path, mime: str, ext: str, head: bytes) -> str | None:
    if ext == "glb" and head.startswith(b"glTF"):
        return "model/gltf-binary"
    if ext == "gltf":
        try:
            with path.open("rb") as f:
                text = f.read(8 * 1024 * 1024)
            doc = json.loads(text)
        except (OSError, ValueError):
            return None
        if isinstance(doc, dict) and isinstance(doc.get("asset"), dict):
            return "model/gltf+json"
        return None
    if ext == "obj" and mime.startswith("text/"):
        text = _head(path, MAX_SNIFF_BYTES).decode("utf-8", "replace")
        if any(line.startswith(("v ", "f ", "o ", "g ", "vn ", "vt "))
               for line in text.splitlines()):
            return "model/obj"
        return None
    if ext == "stl":
        if head.lstrip().lower().startswith(b"solid") and mime.startswith("text/"):
            return "model/stl"
        try:
            size = path.stat().st_size
            with path.open("rb") as f:
                f.seek(80)
                count = int.from_bytes(f.read(4), "little")
        except OSError:
            return None
        if size == 84 + 50 * count:
            return "model/stl"
        return None
    if ext == "ply" and head.startswith(b"ply") and head[3:4] in (b"\n", b"\r"):
        return "model/x-ply"
    if ext == "fbx" and (head.startswith(b"Kaydara FBX Binary") or b"FBX" in head[:64]):
        return "model/x-fbx"
    if ext == "usdz" and head[:2] == b"PK":
        names = _zip_names(path)
        if names and names[0].lower().endswith((".usd", ".usda", ".usdc")):
            return "model/vnd.usdz+zip"
    return None


def refine_mime(path: Path, mime: str, ext: str) -> str:
    """The MIME type to store for a file libmagic called `mime`."""
    ext = (ext or "").lower()
    head = _head(path)
    if ext in RAW_EXTS and _raw_ok(ext, head):
        return RAW_MIMES[ext]
    design = _design(path, mime, ext, head)
    if design:
        return design
    model = _model(path, mime, ext, head)
    if model:
        return model
    font = FONT_MIMES.get(head[:4])
    if font and (mime.startswith("font/") or mime in (
        "application/octet-stream", "application/font-sfnt", "application/x-font-ttf",
        "application/vnd.ms-opentype", "application/font-woff", "application/x-font-woff",
    )):
        return font
    if ext in AUDIO_FIX and (mime.startswith("audio/") or mime == "video/mp4"):
        if ext == "m4a" and head[4:8] == b"ftyp":
            return "audio/mp4"
        if ext == "aac" and mime.startswith("audio/"):
            return "audio/aac"
    return mime
