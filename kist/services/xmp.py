"""XMP sidecars: a small reader and writer on xml.etree only.

Kist's meta.json stays the source of truth; XMP is the copy other photo
tools read. Kist writes items/<id>/original.xmp for images (rating,
keywords, notes, label, creator, rights and provenance) whenever an item
changes (KIST_XMP_SIDECARS, on by default), and reads XMP on ingest, both
the packet embedded in the file and a neighbouring .xmp, so ratings and
keywords from Lightroom, darktable and digiKam come in.

Writing merges: a sidecar that already exists keeps every property Kist
doesn't manage (a darktable history stack, Lightroom develop settings).
"""

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

log = logging.getLogger("kist.xmp")

NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "xmpRights": "http://ns.adobe.com/xap/1.0/rights/",
    "photoshop": "http://ns.adobe.com/photoshop/1.0/",
    "Iptc4xmpExt": "http://iptc.org/std/Iptc4xmpExt/2008-02-29/",
    "lr": "http://ns.adobe.com/lightroom/1.0/",
    "digiKam": "http://www.digikam.org/ns/1.0/",
    "kist": "https://kist.dev/ns/xmp/1.0/",
}
for _prefix, _uri in NS.items():
    ET.register_namespace(_prefix, _uri)

RDF = NS["rdf"]
SIDECAR_NAME = "original.xmp"
MAX_XMP_BYTES = 4 * 1024 * 1024
# how far into a file to look for an embedded packet
SCAN_BYTES = 8 * 1024 * 1024

# IPTC digital source type vocabulary (cv.iptc.org/newscodes/digitalsourcetype)
DST_BASE = "http://cv.iptc.org/newscodes/digitalsourcetype/"
DST_AI = {"trainedAlgorithmicMedia", "compositeWithTrainedAlgorithmicMedia"}
DST_NOT_AI = {
    "digitalCapture", "negativeFilm", "positiveFilm", "print", "minorHumanEdits",
    "compositeCapture", "screenCapture", "virtualRecording",
}


def _q(prefix: str, name: str) -> str:
    return f"{{{NS[prefix]}}}{name}"


# what Kist writes (and removes from an existing sidecar before writing)
MANAGED = {
    _q("xmp", "Rating"), _q("xmp", "Label"), _q("dc", "subject"), _q("dc", "description"),
    _q("dc", "title"), _q("dc", "creator"), _q("dc", "rights"), _q("photoshop", "Credit"),
    _q("xmpRights", "UsageTerms"), _q("xmpRights", "WebStatement"),
    _q("Iptc4xmpExt", "DigitalSourceType"),
    *(_q("kist", k) for k in (
        "PageURL", "ImageURL", "CapturedAt", "CaptureMethod", "AIGenerated", "ItemID",
        "ImportedFrom", "OriginalID",
        "RightsStatus", "CreditExempt", "AIGenerator",  # F4
    )),
}


def ai_from_source_type(value: str | None) -> bool | None:
    """True/False for IPTC digital source types that say, else None."""
    if not value:
        return None
    term = value.rstrip("/").rsplit("/", 1)[-1]
    if term in DST_AI:
        return True
    if term in DST_NOT_AI:
        return False
    return None


# ---------- reading ----------


def _values(el: ET.Element) -> list[str]:
    container = None
    for kind in ("Bag", "Seq", "Alt"):
        container = el.find(f"{{{RDF}}}{kind}")
        if container is not None:
            break
    if container is not None:
        out = []
        for li in container.findall(f"{{{RDF}}}li"):
            text = (li.text or "").strip() or li.get(f"{{{RDF}}}resource", "")
            if text:
                out.append(text)
        return out
    text = (el.text or "").strip() or el.get(f"{{{RDF}}}resource", "")
    return [text] if text else []


def parse_properties(xml_bytes: bytes | str) -> dict[str, list[str]]:
    """{'{ns}name': [values]} from every rdf:Description in an XMP packet.
    Raises ValueError if it isn't XML. Entities are never expanded: DTDs
    are refused outright (no billion laughs)."""
    if isinstance(xml_bytes, str):
        xml_bytes = xml_bytes.encode("utf-8")
    if len(xml_bytes) > MAX_XMP_BYTES:
        raise ValueError("XMP packet too large")
    if b"<!DOCTYPE" in xml_bytes or b"<!ENTITY" in xml_bytes:
        raise ValueError("XMP with a DTD is refused")
    # the xpacket processing instructions are fine for ElementTree
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"not XMP: {exc}") from None
    props: dict[str, list[str]] = {}
    for desc in root.iter(f"{{{RDF}}}Description"):
        for key, value in desc.attrib.items():
            if key.startswith(f"{{{RDF}}}"):
                continue
            props.setdefault(key, []).append(value)
        for child in desc:
            vals = _values(child)
            if vals:
                props.setdefault(child.tag, []).extend(vals)
    return props


def fields_from_properties(props: dict[str, list[str]]) -> dict:
    """The parts of an XMP packet Kist uses: rating, tags, notes, title,
    label and provenance fields."""

    def one(prefix, name):
        vals = props.get(_q(prefix, name)) or []
        return vals[0].strip() if vals and vals[0].strip() else None

    out: dict = {}
    rating = one("xmp", "Rating")
    if rating is not None:
        try:
            r = int(float(rating))
            if 0 <= r <= 5:
                out["rating"] = r
        except ValueError:
            pass
    tags = [t.strip() for t in props.get(_q("dc", "subject"), []) if t.strip()]
    if not tags:
        # hierarchical keywords (Lightroom "a|b|c", digiKam "a/b/c"): the path
        for key, sep in ((_q("lr", "hierarchicalSubject"), "|"), (_q("digiKam", "TagsList"), "/")):
            for t in props.get(key, []):
                parts = [p.strip() for p in t.split(sep) if p.strip()]
                if parts:
                    tags.append("/".join(parts))
    if tags:
        out["tags"] = list(dict.fromkeys(tags))
    for key, prefix, name in (
        ("notes", "dc", "description"), ("title", "dc", "title"), ("label", "xmp", "Label"),
    ):
        value = one(prefix, name)
        if value:
            out[key] = value
    prov: dict = {}
    creators = [c for c in props.get(_q("dc", "creator"), []) if c.strip()]
    if creators:
        prov["creator"] = ", ".join(creators)
    for key, prefix, name in (
        ("credit", "photoshop", "Credit"), ("license", "xmpRights", "UsageTerms"),
        ("rights_url", "xmpRights", "WebStatement"), ("rights", "dc", "rights"),
        ("digital_source_type", "Iptc4xmpExt", "DigitalSourceType"),
        ("page_url", "kist", "PageURL"), ("image_url", "kist", "ImageURL"),
        ("captured_at", "kist", "CapturedAt"), ("method", "kist", "CaptureMethod"),
        ("imported_from", "kist", "ImportedFrom"), ("original_id", "kist", "OriginalID"),
        ("rights_status", "kist", "RightsStatus"), ("ai_generator", "kist", "AIGenerator"),
    ):
        value = one(prefix, name)
        if value:
            prov[key] = value
    if one("kist", "CreditExempt") in ("True", "true", "1"):  # F4
        prov["credit_exempt"] = True
    if "rights" in prov and "license" not in prov:
        prov["license"] = prov["rights"]
    prov.pop("rights", None)
    ai = one("kist", "AIGenerated")
    if ai in ("True", "true", "1"):
        prov["ai_generated"] = True
    elif ai in ("False", "false", "0"):
        prov["ai_generated"] = False
    if "ai_generated" not in prov:
        flag = ai_from_source_type(prov.get("digital_source_type"))
        if flag is not None:
            prov["ai_generated"] = flag
    if prov:
        out["provenance"] = prov
    return out


def read_fields(xml_bytes: bytes | str) -> dict:
    return fields_from_properties(parse_properties(xml_bytes))


def read_sidecar(path: Path) -> dict:
    """Fields from a .xmp file; {} when it is missing or unreadable."""
    try:
        if path.stat().st_size > MAX_XMP_BYTES:
            return {}
        return read_fields(path.read_bytes())
    except (OSError, ValueError) as exc:
        log.info("XMP sidecar %s not read: %s", path.name, exc)
        return {}


_PACKET = re.compile(rb"<x:xmpmeta\b.*?</x:xmpmeta>", re.S)
_RDF_ONLY = re.compile(rb"<rdf:RDF\b.*?</rdf:RDF>", re.S)


def embedded_packet(path: Path) -> bytes | None:
    """The XMP packet inside a JPEG/PNG/TIFF/WebP/HEIF file, found by its
    markers in the first few megabytes (where every format keeps it)."""
    try:
        with path.open("rb") as f:
            head = f.read(SCAN_BYTES)
    except OSError:
        return None
    m = _PACKET.search(head) or _RDF_ONLY.search(head)
    return m.group(0) if m else None


def read_embedded(path: Path) -> dict:
    packet = embedded_packet(path)
    if not packet:
        return {}
    try:
        return read_fields(packet)
    except ValueError as exc:
        log.info("embedded XMP in %s not read: %s", path.name, exc)
        return {}


def neighbour(path: Path, name: str | None = None) -> Path | None:
    """`IMG_1.jpg.xmp` (darktable, digiKam) or `IMG_1.xmp` (Lightroom)
    beside a file, if there is one."""
    name = name or path.name
    for candidate in (path.parent / f"{name}.xmp", path.parent / f"{Path(name).stem}.xmp"):
        if candidate.is_file():
            return candidate
    return None


# ---------- writing ----------


def _bag(parent: ET.Element, tag: str, kind: str, values: list[str], lang: bool = False):
    el = ET.SubElement(parent, tag)
    container = ET.SubElement(el, f"{{{RDF}}}{kind}")
    for v in values:
        li = ET.SubElement(container, f"{{{RDF}}}li")
        if lang:
            li.set("{http://www.w3.org/XML/1998/namespace}lang", "x-default")
        li.text = v


def item_fields(item) -> dict:
    """What Kist writes for an item."""
    from . import provenance as provenance_svc

    tags = sorted(t.name for t in item.tags)
    label = next((t.split(":", 1)[1] for t in tags if t.lower().startswith("label:")), None)
    return {
        "item_id": item.id,
        "rating": item.rating or 0,
        "tags": [t for t in tags if not t.lower().startswith("label:")],
        "label": label,
        "notes": item.notes or "",
        "provenance": provenance_svc.out(item),
    }


def build(fields: dict, existing: bytes | None = None) -> bytes:
    """An XMP packet for `fields`, merged into `existing` (other tools'
    properties are kept; Kist's own are replaced)."""
    root = None
    if existing:
        try:
            if b"<!DOCTYPE" not in existing and b"<!ENTITY" not in existing:
                root = ET.fromstring(existing)
        except ET.ParseError:
            root = None
    if root is None or root.find(f".//{{{RDF}}}RDF") is None and root.tag != f"{{{RDF}}}RDF":
        root = ET.Element(_q("x", "xmpmeta"))
        ET.SubElement(root, f"{{{RDF}}}RDF")
    rdf = root if root.tag == f"{{{RDF}}}RDF" else root.find(f".//{{{RDF}}}RDF")
    descs = rdf.findall(f"{{{RDF}}}Description")
    for d in descs:
        for key in [k for k in d.attrib if k in MANAGED]:
            del d.attrib[key]
        for child in [c for c in d if c.tag in MANAGED]:
            d.remove(child)
    if descs:
        desc = descs[0]
    else:
        desc = ET.SubElement(rdf, f"{{{RDF}}}Description")
        desc.set(f"{{{RDF}}}about", "")

    prov = fields.get("provenance") or {}
    desc.set(_q("xmp", "Rating"), str(int(fields.get("rating") or 0)))
    if fields.get("label"):
        desc.set(_q("xmp", "Label"), str(fields["label"]))
    if fields.get("item_id"):
        desc.set(_q("kist", "ItemID"), str(fields["item_id"]))
    for key, name in (
        ("page_url", "PageURL"), ("image_url", "ImageURL"), ("captured_at", "CapturedAt"),
        ("method", "CaptureMethod"), ("imported_from", "ImportedFrom"),
        ("original_id", "OriginalID"),
        ("rights_status", "RightsStatus"), ("ai_generator", "AIGenerator"),  # F4
    ):
        if prov.get(key):
            desc.set(_q("kist", name), str(prov[key]))
    if prov.get("credit_exempt"):  # F4
        desc.set(_q("kist", "CreditExempt"), "True")
    if prov.get("ai_generated") is not None:
        desc.set(_q("kist", "AIGenerated"), "True" if prov["ai_generated"] else "False")
    if prov.get("credit"):
        desc.set(_q("photoshop", "Credit"), str(prov["credit"]))
    if prov.get("rights_url"):
        desc.set(_q("xmpRights", "WebStatement"), str(prov["rights_url"]))
    dst = prov.get("digital_source_type")
    if not dst and prov.get("ai_generated") is True:
        dst = DST_BASE + "trainedAlgorithmicMedia"
    if dst:
        desc.set(_q("Iptc4xmpExt", "DigitalSourceType"), str(dst))
    if fields.get("tags"):
        _bag(desc, _q("dc", "subject"), "Bag", list(fields["tags"]))
    if fields.get("notes"):
        _bag(desc, _q("dc", "description"), "Alt", [fields["notes"]], lang=True)
    if fields.get("title"):
        _bag(desc, _q("dc", "title"), "Alt", [fields["title"]], lang=True)
    if prov.get("creator"):
        _bag(desc, _q("dc", "creator"), "Seq", [str(prov["creator"])])
    if prov.get("license"):
        _bag(desc, _q("xmpRights", "UsageTerms"), "Alt", [str(prov["license"])], lang=True)
    body = ET.tostring(root, encoding="unicode")
    return (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        + body
        + '\n<?xpacket end="w"?>\n'
    ).encode("utf-8")


def sidecar_path(item_id: str) -> Path:
    from .meta import item_dir

    return item_dir(item_id) / SIDECAR_NAME


def wants_sidecar(item) -> bool:
    from ..config import get_settings
    from ..filetypes import kind_for

    return get_settings().xmp_sidecars and kind_for(item.mime, item.ext) == "image"


def write_item_sidecar(item) -> bool:
    """items/<id>/original.xmp, merged into what's there. Best effort: a
    failure is logged, never raised (it's a copy for other tools)."""
    if not wants_sidecar(item):
        return False
    from .meta import atomic_write_text

    path = sidecar_path(item.id)
    try:
        existing = path.read_bytes() if path.is_file() else None
        atomic_write_text(path, build(item_fields(item), existing).decode("utf-8"))
        return True
    except Exception:
        log.exception("XMP sidecar for %s not written", item.id)
        return False


def merge_into_ingest(fields: dict, *, tags: list[str], rating: int, notes: str) -> tuple:
    """Apply XMP fields to what an ingest already has: tags are added,
    rating and notes only fill blanks. Returns (tags, rating, notes)."""
    from . import validation

    out_tags = list(tags)
    have = {t.lower() for t in out_tags}
    extra = list(fields.get("tags") or [])
    if fields.get("label"):
        extra.append(f"label:{fields['label']}")
    for t in extra:
        try:
            name = validation.tag_name(t)
        except validation.ValidationError:
            continue
        if name.lower() not in have:
            have.add(name.lower())
            out_tags.append(name)
    if not rating and fields.get("rating"):
        rating = int(fields["rating"])
    if not notes and fields.get("notes"):
        notes = str(fields["notes"])[: validation.NOTES_MAX]
    return out_tags, rating, notes
