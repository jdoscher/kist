"""Eagle (eagle.cool) libraries: a `<name>.library` folder.

Ported from the standalone aerie_import.py that moved Jay's Eagle library
into Kist over REST. The layout it reads:

    <name>.library/metadata.json                folders (a tree) + smartFolders
    <name>.library/images/<id>.info/metadata.json   one item: name, ext, tags,
                                                annotation, url, star, folders,
                                                btime, modificationTime, isDeleted
    <name>.library/images/<id>.info/<name>.<ext>    the file

Folders keep their tree under the run folder, tags and ratings come along,
the annotation becomes notes, url becomes provenance.page_url, Eagle's
trash becomes Kist's trash, and the smart folders Kist can express (one
tag rule, any-of tags, untagged) become Kist smart folders.
"""

import json
from collections.abc import Iterator
from pathlib import Path

from .base import Entry, Importer, ImportError_, clean_tags, parse_time, safe_url, unpack


def walk_folders(folders, path=()):
    for f in folders or []:
        p = (*path, str(f.get("name") or "Untitled"))
        yield f, p
        yield from walk_folders(f.get("children"), p)


def smart_folder_plan(root: dict):
    """(name, Kist query) for every Eagle smart folder Kist can express;
    (name, reason) for the rest."""
    ok, skipped = [], []

    def visit(sfs, prefix=""):
        for s in sfs or []:
            name = f"{prefix}{s.get('name') or 'Untitled'}"
            rules = [r for c in s.get("conditions", []) for r in c.get("rules", [])]
            r = rules[0] if len(rules) == 1 else None
            if not rules:
                pass  # a container only
            elif r and r.get("property") == "tags" and r.get("method") == "empty":
                ok.append((name, {"untagged": True}))
            elif (r and r.get("property") == "tags"
                  and r.get("method") in ("union", "intersection", "equal")
                  and len(r.get("value") or []) == 1):
                ok.append((name, {"tags": list(r["value"])}))
            elif r and r.get("property") == "tags" and r.get("method") == "union":
                ok.append((name, {"tags_any": list(r["value"])}))
            else:
                skipped.append((name, json.dumps(rules)[:120]))
            visit(s.get("children"), f"{name} / ")

    visit(root.get("smartFolders"))
    return ok, skipped


class EagleImporter(Importer):
    kind = "eagle"

    def _library(self) -> Path:
        root = unpack(Path(self.source), self.workdir)
        if (root / "metadata.json").is_file() and (root / "images").is_dir():
            return root
        for candidate in sorted(root.glob("*.library")) + sorted(root.glob("*/*.library")):
            if (candidate / "metadata.json").is_file():
                return candidate
        raise ImportError_("no Eagle library there (a folder with metadata.json and images/)")

    def entries(self) -> Iterator[Entry]:
        lib = self._library()
        try:
            self.root = json.loads((lib / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ImportError_(f"the library's metadata.json is unreadable: {exc}") from None
        paths = {f.get("id"): list(p) for f, p in walk_folders(self.root.get("folders"))}
        metas = sorted((lib / "images").glob("*.info/metadata.json"))
        items = []
        for meta in metas:
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                yield Entry(key=meta.parent.name, error=f"metadata.json unreadable: {exc}")
                continue
            m["_file"] = meta.parent / f"{m.get('name')}.{m.get('ext')}"
            items.append(m)
        # live items first, so a trashed exact duplicate never trashes a live copy
        items.sort(key=lambda m: bool(m.get("isDeleted")))
        for m in items:
            key = str(m.get("id") or m["_file"].parent.name)
            if not m["_file"].is_file():
                yield Entry(key=key, error=f"file missing: {m['_file'].name}")
                continue
            modified = parse_time(m.get("modificationTime") or m.get("mtime"))
            yield Entry(
                key=key, path=m["_file"], filename=f"{m.get('name')}.{m.get('ext')}",
                folders=[paths[f] for f in (m.get("folders") or []) if f in paths],
                tags=clean_tags(m.get("tags")), notes=str(m.get("annotation") or ""),
                rating=max(0, min(5, int(m.get("star") or 0))),
                created_at=parse_time(m.get("btime")), modified_at=modified,
                deleted=bool(m.get("isDeleted")),
                provenance={"page_url": safe_url(m.get("url"))},
            )

    def after(self, session, run) -> None:
        from ..schemas import SmartFolderQuery
        from ..services import smart_folders as smart_svc

        ok, skipped = smart_folder_plan(getattr(self, "root", {}) or {})
        made = 0
        for name, query in ok:
            try:
                smart_svc.create(session, f"{run.folder_name}: {name}"[:200],
                                 SmartFolderQuery.model_validate(query))
                made += 1
            except Exception as exc:  # a bad tag name, say
                session.rollback()
                run.report["errors"].append({"key": f"smart folder {name}", "error": str(exc)})
        run.report["smart_folders"] = made
        run.report["smart_folders_skipped"] = [{"name": n, "rules": r} for n, r in skipped]
