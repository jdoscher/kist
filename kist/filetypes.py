"""Type classification.

An item's kind (image | video | pdf | audio | model | font | design | bundle
| other) comes from the MIME type
libmagic sniffed from its bytes, never from the name it arrived with: an
HTML page saved as "invoice.pdf" is "other". The one exception is a video
extension whose content libmagic can't name at all (application/octet-
stream), which some valid MP4/MOV files sniff as; font files are treated the
same way (older libmagic builds don't know WOFF2).

Font files (TTF/OTF/WOFF/WOFF2) get a specimen preview: the family name and
a sample line rendered with Pillow (services/thumbs.font_specimen).

Files of kind "other" are still stored (content is king) but get no preview;
the UI shows a generic icon.
"""

OCTET_STREAM = "application/octet-stream"

MIME_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/svg+xml": "svg",
    "image/avif": "avif",
    "image/heic": "heic",
    "image/heif": "heic",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
    "application/pdf": "pdf",
    "font/ttf": "ttf",
    "font/otf": "otf",
    "font/woff": "woff",
    "font/woff2": "woff2",
    "font/sfnt": "ttf",
    "font/collection": "ttc",
    "application/vnd.ms-opentype": "otf",
    "application/font-woff": "woff",
    "application/x-font-ttf": "ttf",
}

VIDEO_EXTS = {"mp4", "mov", "webm"}

# keep in step with migrations/versions/0201_search.py
FONT_MIMES = frozenset({
    "font/ttf", "font/otf", "font/woff", "font/woff2", "font/sfnt", "font/collection",
    "application/font-woff", "application/font-woff2", "application/font-sfnt",
    "application/x-font-ttf", "application/x-font-otf", "application/x-font-truetype",
    "application/x-font-opentype", "application/vnd.ms-opentype", "application/x-font-woff",
})
FONT_EXTS = {"ttf", "otf", "woff", "woff2", "ttc"}

KINDS = ("image", "video", "pdf", "font", "other")

# ---- Milestone 5 kinds (services/formats.py names these MIME types) ----
# Design sources: their own kind, so a PSD is never mistaken for a photo.
# Zip-based and unnamed formats get these MIME types from
# formats.refine_mime, which checks the content (a .sketch must be a zip
# holding previews/preview.png), never just the name.
DESIGN_MIMES = frozenset({
    "image/vnd.adobe.photoshop", "application/x-photoshop", "image/x-photoshop",
    "application/x-psb", "application/illustrator", "application/postscript",
    "image/x-eps", "application/x-sketch", "application/x-procreate",
    "application/x-affinity", "application/x-blender", "application/x-krita",
    "image/openraster", "application/x-figma",
})
# plain zip files are kept whole as asset bundles (services/bundles.py)
BUNDLE_MIMES = frozenset({"application/zip", "application/x-zip-compressed"})
# FONT_MIMES (above, Milestone 2) lists the older names libmagic gives fonts;
# font/* covers the rest
M5_KINDS = ("audio", "model", "font", "design", "bundle")
KINDS = ("image", "video", "pdf") + M5_KINDS + ("other",)


def m5_kind(mime: str) -> str | None:
    """The Milestone 5 kinds (None: not one of them)."""
    if mime in DESIGN_MIMES:
        return "design"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("model/"):
        return "model"
    if mime.startswith("font/") or mime in FONT_MIMES:
        return "font"
    if mime in BUNDLE_MIMES:
        return "bundle"
    return None


def kind_for(mime: str, ext: str) -> str:
    """Coarse type used for filtering and previews. Keep in step with
    services.search.KIND_FILTERS, which is the same rule in SQL."""
    if mime == "application/pdf":
        return "pdf"
    extra = m5_kind(mime)
    if extra is not None:
        return extra
    if mime.startswith("video/") or (mime == OCTET_STREAM and ext.lower() in VIDEO_EXTS):
        return "video"
    if mime.startswith("image/"):
        return "image"
    if mime in FONT_MIMES or (mime == OCTET_STREAM and ext.lower() in FONT_EXTS):
        return "font"
    return "other"


def serve_inline(mime: str) -> bool:
    """Whether an original may be shown in the browser (by sniffed MIME).
    Everything else is a download, sandboxed if opened anyway."""
    if mime in DESIGN_MIMES:
        return False  # a PSD or EPS is a download, never a page or an <img>
    return mime.startswith(("image/", "video/", "audio/")) or mime == "application/pdf"
