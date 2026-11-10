"""Type classification.

An item's kind (image | video | pdf | other) comes from the MIME type
libmagic sniffed from its bytes, never from the name it arrived with: an
HTML page saved as "invoice.pdf" is "other". The one exception is a video
extension whose content libmagic can't name at all (application/octet-
stream), which some valid MP4/MOV files sniff as.

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
}

VIDEO_EXTS = {"mp4", "mov", "webm"}

KINDS = ("image", "video", "pdf", "other")

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
# older names libmagic gives fonts; font/* covers the rest
FONT_MIMES = frozenset({
    "application/font-sfnt", "application/x-font-ttf", "application/x-font-otf",
    "application/vnd.ms-opentype", "application/font-woff", "application/x-font-woff",
})
M5_KINDS = ("audio", "model", "font", "design", "bundle")
KINDS = KINDS[:-1] + M5_KINDS + ("other",)


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
    return "other"


def serve_inline(mime: str) -> bool:
    """Whether an original may be shown in the browser (by sniffed MIME).
    Everything else is a download, sandboxed if opened anyway."""
    if mime in DESIGN_MIMES:
        return False  # a PSD or EPS is a download, never a page or an <img>
    return mime.startswith(("image/", "video/", "audio/")) or mime == "application/pdf"
