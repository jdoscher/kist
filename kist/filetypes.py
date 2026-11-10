"""Type classification.

An item's kind (image | video | pdf | font | other) comes from the MIME type
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
    "application/x-font-opentype", "application/vnd.ms-opentype",
})
FONT_EXTS = {"ttf", "otf", "woff", "woff2", "ttc"}

KINDS = ("image", "video", "pdf", "font", "other")


def kind_for(mime: str, ext: str) -> str:
    """Coarse type used for filtering and previews. Keep in step with
    services.search.KIND_FILTERS, which is the same rule in SQL."""
    if mime == "application/pdf":
        return "pdf"
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
    return mime.startswith(("image/", "video/", "audio/")) or mime == "application/pdf"
