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


def kind_for(mime: str, ext: str) -> str:
    """Coarse type used for filtering and previews. Keep in step with
    services.search.KIND_FILTERS, which is the same rule in SQL."""
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("video/") or (mime == OCTET_STREAM and ext.lower() in VIDEO_EXTS):
        return "video"
    if mime.startswith("image/"):
        return "image"
    return "other"


def serve_inline(mime: str) -> bool:
    """Whether an original may be shown in the browser (by sniffed MIME).
    Everything else is a download, sandboxed if opened anyway."""
    return mime.startswith(("image/", "video/", "audio/")) or mime == "application/pdf"
