"""Input rules shared by every surface (REST, MCP, CLI, worker).

Each check returns the cleaned value or raises ValidationError, which REST
answers with 422 and MCP reports as a readable tool error. Pydantic models
repeat the simple bounds so they show up in the OpenAPI schema, but these
functions are what every write path goes through.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

TAG_MAX = 100
FOLDER_NAME_MAX = 200
NOTES_MAX = 100_000
FILENAME_MAX = 255
RATING_MIN, RATING_MAX = 0, 5
POSITION_MIN, POSITION_MAX = -(2**31), 2**31 - 1
# a little slack for clocks that run ahead
FUTURE_SLACK = timedelta(days=1)


class ValidationError(ValueError):
    """Bad input from a caller (HTTP 422)."""


class UnknownFolder(ValidationError):
    def __init__(self, folder_id: str):
        super().__init__(f"Unknown folder: {folder_id}")
        self.folder_id = folder_id


def source_url(value: str | None) -> str | None:
    """None, empty (cleared) or an absolute http(s) URL."""
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        raise ValidationError(
            "source_url must be an absolute http(s) URL, e.g. https://example.com/page"
        )
    return value


def tag_name(value: str) -> str:
    name = " ".join(str(value).split())
    if not name:
        raise ValidationError("tag names can't be empty")
    if "," in name:
        raise ValidationError(f"tag names can't contain a comma: {name!r}")
    if len(name) > TAG_MAX:
        raise ValidationError(f"tag names are at most {TAG_MAX} characters")
    return name


def folder_name(value: str) -> str:
    name = str(value).strip()
    if not name:
        raise ValidationError("names can't be empty")
    if len(name) > FOLDER_NAME_MAX:
        raise ValidationError(f"names are at most {FOLDER_NAME_MAX} characters")
    return name


def notes(value: str) -> str:
    value = str(value)
    if len(value) > NOTES_MAX:
        raise ValidationError(f"notes are at most {NOTES_MAX:,} characters")
    return value


def filename(value: str) -> str:
    name = str(value).strip()
    if not name:
        raise ValidationError("filename can't be empty")
    if "/" in name or "\x00" in name:
        raise ValidationError("filename can't contain '/' or NUL")
    if len(name) > FILENAME_MAX:
        raise ValidationError(f"filename is at most {FILENAME_MAX} characters")
    return name


def rating(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValidationError("rating must be a whole number from 0 to 5") from None
    if isinstance(value, float) and value != n:
        raise ValidationError("rating must be a whole number from 0 to 5")
    if not RATING_MIN <= n <= RATING_MAX:
        raise ValidationError("rating must be from 0 to 5")
    return n


def position(value) -> int:
    n = int(value)
    if not POSITION_MIN <= n <= POSITION_MAX:
        raise ValidationError("position is out of range")
    return n


def timestamp(value: datetime | None, field: str = "created_at") -> datetime | None:
    """Naive times are taken as UTC; times more than a day ahead are refused
    (they would pin an item to the top of the newest-first sort)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    if value > datetime.now(UTC) + FUTURE_SLACK:
        raise ValidationError(f"{field} is in the future")
    return value
