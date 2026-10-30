"""Format allowlist and type classification.

Files outside the allowlist are still stored (content is king) but get no
preview — the UI shows a generic icon.
"""

ALLOWED_EXTS = {
    "png", "jpg", "jpeg", "webp", "gif", "svg", "avif", "heic",
    "mp4", "mov", "webm", "pdf",
}

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


def kind_for(mime: str, ext: str) -> str:
    """Coarse type used for filtering: image | video | pdf | other."""
    if mime == "application/pdf" or ext == "pdf":
        return "pdf"
    if mime.startswith("video/") or ext in VIDEO_EXTS:
        return "video"
    if mime.startswith("image/"):
        return "image"
    return "other"


def has_preview(mime: str, ext: str) -> bool:
    return ext.lower() in ALLOWED_EXTS or kind_for(mime, ext) in {"image", "video", "pdf"}
