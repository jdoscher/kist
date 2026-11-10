"""Importers: bring a collection in from another tool, keeping provenance.

    kist import <kind> <path|url> [--dry-run] [--folder NAME]
    POST /api/imports {kind, source, dry_run?, folder?}   (a worker job)

Kinds: pinterest, arena, raindrop, takeout, apple-photos, sidecar, eagle.

Every run files what it brings in under its own folder, Imported/<kind>
<date> (or --folder NAME), with the source's boards, albums or channels as
subfolders, stamps provenance {method: import, imported_from, original_id,
page_url, ...} and records the whole run as one history batch, so
`POST /api/batches/<id>/undo` takes it back. A dry run changes nothing and
reports what would happen: counts, duplicates (of the library or within
the source) and errors.

Sources are only read, never changed. Zips are unpacked into staging with
path-traversal and size checks, and removed afterwards.
"""

from .base import KINDS, ImportError_, get_importer, run_import

__all__ = ["KINDS", "ImportError_", "get_importer", "run_import"]
