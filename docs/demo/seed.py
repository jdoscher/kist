"""Seed a Kist instance with the demo library (public domain / CC0 images).

The README hero, the screenshots on the docs site, the demo loop and the
social card should show this library, never a personal one. The list is
docs/demo/library.json: Wikimedia Commons files, each with its file page,
licence, creator and the tags it gets in Kist.

    KIST_URL=http://localhost:8080 KIST_TOKEN=... KIST_SEED_CONTACT=you@example.com \\
      python docs/demo/seed.py [--dry-run] [--width 2560]

Use a fresh, throwaway instance (its own `.env`, `KIST_PORT`, library and
`COMPOSE_PROJECT_NAME`): the script only adds items, but a demo library mixed
into a real one is no use for screenshots. Standard library only.

Each file is downloaded from Commons at --width pixels (Special:FilePath
scales it; the original can be 9,000 px), uploaded with its tags, and its
provenance carries the Commons page, creator and licence, so the info panel
credits it and `GET /api/items?license=CC0` finds it. Already-added files
answer "Already in Kist" and are skipped, so it is safe to re-run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Wikimedia's User-Agent policy: name the tool and give a way to reach you.
# Set KIST_SEED_CONTACT to your own address or page when you run it.
CONTACT = os.environ.get("KIST_SEED_CONTACT", "https://github.com/jdoscher/kist/issues")
UA = f"kist-demo-seed/2.0 (https://github.com/jdoscher/kist; {CONTACT}) python-urllib"
PAUSE = 2.0  # seconds between Commons downloads


def download(src: str) -> tuple[bytes, str]:
    """GET from Commons, one at a time; back off on 429/5xx (Retry-After)."""
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(src, headers={"User-Agent": UA}), timeout=120) as r:
                return r.read(), r.headers.get_content_type()
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500 or attempt == 4:
                raise
            wait = e.headers.get("Retry-After", "")
            time.sleep(int(wait) if wait.isdigit() else 10 * (attempt + 1))
    raise RuntimeError("unreachable")


def commons_url(file: str, width: int) -> str:
    return "https://commons.wikimedia.org/wiki/Special:FilePath/" + urllib.parse.quote(file) + f"?width={width}"


def multipart(fields: dict[str, str], filename: str, data: bytes, mime: str) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n".encode() + data + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed a Kist instance with the demo library.")
    ap.add_argument("--dry-run", action="store_true", help="list what would be added; download nothing")
    ap.add_argument("--width", type=int, default=2560)
    ap.add_argument("--only", help="comma list of tags: seed only items with one of them")
    args = ap.parse_args()
    lib = json.loads((HERE / "library.json").read_text())
    items = lib["items"]
    if args.only:
        want = {t.strip() for t in args.only.split(",")}
        items = [i for i in items if want & set(i["tags"])]
    if args.dry_run:
        for i in items:
            print(f"{i['license']:14} {', '.join(i['tags']):32} {i['file']}")
        print(len(items), "items")
        return 0
    url = os.environ["KIST_URL"].rstrip("/")
    token = os.environ["KIST_TOKEN"]
    added = skipped = failed = 0
    for i in items:
        src = commons_url(i["file"], args.width)
        try:
            data, mime = download(src)
        except Exception as e:  # noqa: BLE001
            print("download failed:", i["file"], e, file=sys.stderr)
            failed += 1
            continue
        fields = {
            "tags": json.dumps(i["tags"]),
            "method": "import",
            "page_url": i["page"],
            "image_url": src,
            "source_url": i["page"],
            "title": i["file"],
            "license": i["license"],
            "notes": f"Wikimedia Commons, {i['license']}" + (f", {i['creator']}" if i.get("creator") else ""),
        }
        if i.get("creator"):
            fields["creator"] = i["creator"]
        # a short display name (library.json "name"); the extension follows
        # what Commons sent (a scaled TIFF comes back as JPEG)
        ext = {"image/jpeg": ".jpg", "image/png": ".png"}.get(mime) or "." + i["file"].rsplit(".", 1)[-1].lower()
        name = i.get("name", i["file"].rsplit(".", 1)[0]) + ext
        body, ctype = multipart(fields, name, data, mime)
        req = urllib.request.Request(
            url + "/api/items",
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": ctype, "Accept": "text/plain"},
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                msg = r.read().decode(errors="replace").strip()
        except Exception as e:  # noqa: BLE001
            print("upload failed:", i["file"], e, file=sys.stderr)
            failed += 1
            continue
        if msg.startswith("Already"):
            skipped += 1
        else:
            added += 1
        print(msg)
        time.sleep(PAUSE)  # be polite to Commons
    print(f"added {added}, already there {skipped}, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
