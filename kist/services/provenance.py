"""Provenance: where an item came from and what may be done with it.

    provenance {page_url, image_url, page_title, captured_at, method,
                creator, credit, license, rights_url, ai_generated,
                digital_source_type, c2pa, imported_from, original_id, geo}

It lives in meta.json (and the items.provenance JSONB column). page_url is
the item's source_url: the column stays the one place it is stored, and
provenance.page_url always mirrors it, so older clients and newer ones see
the same value.

On ingest Kist fills what it knows: the method (upload, paste, url,
extension, inbox, mcp, import), when, the page and image URL of a URL add,
and anything in the file's XMP or a neighbouring .xmp (creator, credit,
rights, IPTC DigitalSourceType). A C2PA manifest (JPEG APP11 JUMBF, PNG
caBX, or a JUMBF box elsewhere) is noted; it marks the item AI-generated
when its manifest names a trained-algorithm source type.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import validation, xmp

METHODS = ("upload", "paste", "url", "extension", "inbox", "mcp", "import")
TEXT_FIELDS = ("page_title", "creator", "credit", "license", "digital_source_type",
               "imported_from", "original_id")
URL_FIELDS = ("page_url", "image_url", "rights_url")
FIELDS = (*URL_FIELDS, "captured_at", "method", *TEXT_FIELDS, "ai_generated", "c2pa", "geo")
TEXT_MAX = 2000

# events.context().source -> the capture method an ingest gets by default
DEFAULT_METHOD = {"web": "upload", "api": "upload", "mcp": "mcp", "import": "inbox",
                  "cli": "import", "worker": "inbox"}


def _url(value, field: str) -> str | None:
    try:
        return validation.source_url(value)
    except validation.ValidationError:
        raise validation.ValidationError(
            f"provenance.{field} must be an absolute http(s) URL"
        ) from None


def clean(data: dict | None, *, partial: bool = False) -> dict:
    """Validated provenance. With partial=True, keys set to None stay (they
    mean "remove this field" in a PATCH)."""
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise validation.ValidationError("provenance must be an object")
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key not in FIELDS:
            raise validation.ValidationError(
                f"unknown provenance field {key!r}; expected some of {', '.join(FIELDS)}"
            )
        if value is None or value == "":
            if partial:
                out[key] = None
            continue
        if key in URL_FIELDS:
            out[key] = _url(value, key)
        elif key == "method":
            v = str(value).strip().lower()
            if v not in METHODS:
                raise validation.ValidationError(
                    f"provenance.method must be one of {', '.join(METHODS)}"
                )
            out[key] = v
        elif key == "captured_at":
            if isinstance(value, datetime):
                dt = value
            else:
                try:
                    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                except ValueError:
                    raise validation.ValidationError(
                        "provenance.captured_at must be an ISO 8601 time"
                    ) from None
            out[key] = (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()
        elif key in ("ai_generated", "c2pa"):
            if not isinstance(value, bool):
                raise validation.ValidationError(f"provenance.{key} must be true, false or null")
            out[key] = value
        elif key == "geo":
            try:
                lat, lon = float(value["lat"]), float(value["lon"])
            except (TypeError, KeyError, ValueError):
                raise validation.ValidationError(
                    "provenance.geo must be {lat, lon}"
                ) from None
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise validation.ValidationError("provenance.geo is out of range")
            out[key] = {"lat": lat, "lon": lon}
        else:
            text = " ".join(str(value).split())
            if len(text) > TEXT_MAX:
                raise validation.ValidationError(
                    f"provenance.{key} is at most {TEXT_MAX} characters"
                )
            out[key] = text
    return out


def stored(item) -> dict:
    """The provenance column without page_url (that is source_url)."""
    return {k: v for k, v in (item.provenance or {}).items() if k != "page_url"}


def out(item) -> dict:
    """Provenance as REST, MCP and meta.json show it."""
    data = stored(item)
    if item.source_url:
        data["page_url"] = item.source_url
    return {k: data[k] for k in FIELDS if data.get(k) is not None}


def merge(item, patch: dict) -> None:
    """Apply a PATCH: given keys replace, null removes. page_url sets
    source_url."""
    patch = clean(patch, partial=True)
    current = stored(item)
    if "page_url" in patch:
        item.source_url = patch.pop("page_url")
    for key, value in patch.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    item.provenance = current or None


def replace(item, data: dict | None) -> None:
    """Set the whole block (undo uses this); page_url is left to source_url."""
    data = {k: v for k, v in clean(data or {}).items() if k != "page_url"}
    item.provenance = data or None


# ---------- reading files ----------


def detect_c2pa(path: Path, limit: int = 8 * 1024 * 1024) -> tuple[bool, bool | None]:
    """(manifest present, ai_generated from it or None). A minimal check:
    JPEG APP11 segments carrying a JUMBF 'c2pa' box, a PNG caBX chunk, or a
    JUMBF superbox labelled c2pa anywhere in the first megabytes (WebP,
    HEIF, AVIF). The manifest is not validated, only looked at."""
    try:
        with path.open("rb") as f:
            head = f.read(limit)
    except OSError:
        return False, None
    found = False
    manifest = b""
    if head[:2] == b"\xff\xd8":
        i = 2
        while i + 4 <= len(head) and head[i] == 0xFF:
            marker = head[i + 1]
            if marker in (0xD9, 0xDA):  # end, start of scan: no more metadata
                break
            seg_len = int.from_bytes(head[i + 2:i + 4], "big")
            if seg_len < 2:
                break
            seg = head[i + 4:i + 2 + seg_len]
            if marker == 0xEB and b"jumb" in seg and b"c2pa" in seg:
                found = True
                manifest += seg
            i += 2 + seg_len
    elif head[:8] == b"\x89PNG\r\n\x1a\n":
        i = 8
        while i + 8 <= len(head):
            n = int.from_bytes(head[i:i + 4], "big")
            ctype = head[i + 4:i + 8]
            if ctype == b"caBX":
                found = True
                manifest += head[i + 8:i + 8 + n]
            if ctype == b"IEND":
                break
            i += 12 + n
    else:
        at = head.find(b"jumb")
        if at >= 0 and b"c2pa" in head[at:at + 256]:
            found = True
            manifest = head[at:]
    if not found:
        return False, None
    if any(t.encode() in manifest for t in xmp.DST_AI):
        return True, True
    return True, None


def from_file(path: Path, xmp_path: Path | None = None) -> dict:
    """XMP fields from a file and its sidecar (the sidecar wins), with any
    C2PA finding folded into provenance."""
    fields = xmp.read_embedded(path)
    if xmp_path is not None:
        side = xmp.read_sidecar(xmp_path)
        prov = {**(fields.get("provenance") or {}), **(side.get("provenance") or {})}
        fields = {**fields, **side}
        if prov:
            fields["provenance"] = prov
    c2pa, ai = detect_c2pa(path)
    if c2pa:
        prov = dict(fields.get("provenance") or {})
        prov["c2pa"] = True
        if ai is not None and prov.get("ai_generated") is None:
            prov["ai_generated"] = ai
        fields["provenance"] = prov
    return fields


def for_ingest(given: dict | None, from_files: dict | None, *, source_url: str | None) -> dict:
    """The provenance a new item starts with: what the caller gave wins over
    what the file says, and the method and capture time are always set."""
    from . import events

    file_prov = {}
    for k, v in ((from_files or {}).get("provenance") or {}).items():
        try:
            file_prov.update(clean({k: v}))
        except validation.ValidationError:
            continue  # a bad value in someone else's XMP is skipped, not fatal
    data = {**file_prov, **clean(given or {})}
    data.setdefault("method", DEFAULT_METHOD.get(events.context().source, "upload"))
    data.setdefault("captured_at", datetime.now(UTC).isoformat())
    if source_url and not data.get("page_url"):
        data["page_url"] = source_url
    return data


# ---------- filters (GET /api/items, MCP search) ----------

SOURCE_STATUSES = ("ok", "gone", "moved", "error", "unchecked")


def filters(*, ai_generated: str | bool | None = None, license: str | None = None,
            source_status: str | None = None) -> list:
    """SQL conditions for ai_generated=true|false|unknown, license=<text>
    (case-insensitive exact; "none" = no licence recorded) and
    source_status=ok|gone|moved|error|unchecked."""
    import sqlalchemy as sa

    from ..models import Item

    out: list = []
    if ai_generated is not None and ai_generated != "":
        value = str(ai_generated).strip().lower()
        flag = Item.provenance["ai_generated"].astext
        if value in ("true", "1", "yes"):
            out.append(flag == "true")
        elif value in ("false", "0", "no"):
            out.append(flag == "false")
        elif value in ("unknown", "null", "none"):
            out.append(flag.is_(None))  # no provenance, no key, or JSON null
        else:
            raise validation.ValidationError("ai_generated must be true, false or unknown")
    if license:
        if license.strip().lower() == "none":
            out.append(sa.or_(Item.provenance.is_(None),
                              sa.not_(Item.provenance.has_key("license"))))
        else:
            out.append(sa.func.lower(Item.provenance["license"].astext)
                       == license.strip().lower())
    if source_status:
        if source_status not in SOURCE_STATUSES:
            raise validation.ValidationError(
                f"source_status must be one of {', '.join(SOURCE_STATUSES)}"
            )
        if source_status == "unchecked":
            out.append(sa.and_(Item.source_url.isnot(None), Item.source_status.is_(None)))
        else:
            out.append(Item.source_status == source_status)
    return out


# ---------- the durable copy ----------


def restore_from_meta(item, meta: dict) -> None:
    """rebuild-index: provenance and the last source check from meta.json."""
    prov = meta.get("provenance") if isinstance(meta.get("provenance"), dict) else {}
    if not item.source_url and prov.get("page_url"):
        item.source_url = prov["page_url"]
    item.provenance = {k: v for k, v in prov.items() if k != "page_url" and k in FIELDS} or None
    check = meta.get("source_check") if isinstance(meta.get("source_check"), dict) else {}
    item.source_status = check.get("status")
    item.source_wayback_url = check.get("wayback_url")
    when = check.get("checked_at")
    item.source_checked_at = datetime.fromisoformat(when) if when else None


def meta_drift(on_disk: dict, expected: dict) -> list[str]:
    """verify: M4 keys of meta.json that differ (page_url is source_url,
    compared already; an older meta.json without them is not drift)."""
    out = []
    a = {k: v for k, v in (on_disk.get("provenance") or {}).items() if k != "page_url"}
    b = {k: v for k, v in (expected.get("provenance") or {}).items() if k != "page_url"}
    if a != b:
        out.append("provenance")
    if (on_disk.get("source_check") or None) != (expected.get("source_check") or None):
        da, db = on_disk.get("source_check") or {}, expected.get("source_check") or {}
        same = (da.get("status") == db.get("status")
                and da.get("wayback_url") == db.get("wayback_url")
                and _instant(da.get("checked_at")) == _instant(db.get("checked_at")))
        if not same:
            out.append("source_check")
    return out


def _instant(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
