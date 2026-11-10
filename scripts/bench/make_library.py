#!/usr/bin/env python3
"""Make a synthetic Kist library for benchmarks (Milestone 6).

Writes items, tags, folders, smart folders, OCR/PDF text and embeddings
straight into Postgres (COPY; minutes, not hours, for 250k items), plus a
minimal directory per item whose thumbnails are hard links to one tiny
WebP, so 250k items cost a few hundred MB of inodes and almost no data.

NEVER run this against a real library. It refuses a database that already
has items (unless --allow-existing) and prints the database it writes to.

Run it where the app runs, so it sees the same database and library:

    # dev0, inside the worker container (scripts/ isn't in the image)
    docker compose exec -T worker python - --items 250000 < scripts/bench/make_library.py

    # locally
    KIST_DATABASE_URL=postgresql+psycopg://kist@/kist_bench?host=/tmp&port=55432 \\
    KIST_LIBRARY_DIR=/tmp/kist-bench-lib python scripts/bench/make_library.py --items 20000

The database must already be migrated (the app/worker did, or
`python -m kist.migrate`). Needs psycopg; Pillow is optional (a real
ThumbHash placeholder when present).

What it makes (deterministic for a --seed):
- dates over 15 years, more recent years busier; 60 % of images have a
  date taken (EXIF) a little before the date added; 1 % are in the trash
- kinds: jpeg/png/heic/gif/webp/svg images, PDFs, videos, audio, fonts, zips
- ~800 tags with Zipf usage (a few tags on most items, a long tail), 25 %
  of items untagged; ~150 folders up to 4 levels deep, 30 % of items in
  none, some in two; 10 smart folders
- ~1.5 % of photos start a stack of 2-3 (the grid's stacks=collapse)
- 3-5 palette colours per image (item_colors fills itself by trigger),
  ratings, pick/reject flags, colour labels, notes, GPS + place + camera
- OCR/PDF text rows for ~10 % (screenshots and PDFs), embeddings (random
  vectors around 64 cluster centres, unit length, dim 512, for the active
  model) for --embed-fraction of the images

Verification: there is no meta.json, so `kist verify` reports every
synthetic item as drift; skip verify on a synthetic library (and don't run
rebuild-index on it: it would drop them). fixity_checked_at is set to now,
so the rolling fixity check leaves the (hard-linked, wrong-hash) originals
alone for its whole cycle, and every item has a ThumbHash (or "") and a
checked media block so no background backfill starts on them.
"""

import argparse
import base64
import io
import json
import math
import os
import random
import secrets
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

DAY = 86400
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

KINDS = [  # (weight, mime, ext, kind)
    (40, "image/jpeg", "jpg", "photo"),
    (22, "image/png", "png", "screenshot"),
    (8, "image/heic", "heic", "photo"),
    (6, "image/webp", "webp", "image"),
    (3, "image/gif", "gif", "image"),
    (1, "image/svg+xml", "svg", "image"),
    (6, "application/pdf", "pdf", "pdf"),
    (6, "video/mp4", "mp4", "video"),
    (2, "video/quicktime", "mov", "video"),
    (2, "audio/mpeg", "mp3", "audio"),
    (1, "font/otf", "otf", "font"),
    (1, "application/zip", "zip", "other"),
    (2, "image/vnd.adobe.photoshop", "psd", "design"),
]
CAMERAS = ["Apple iPhone 15 Pro", "Apple iPhone 12", "Fujifilm X100V", "Sony ILCE-7M3",
           "Canon EOS R6", "Ricoh GR III", "Google Pixel 8"]
PLACES = [("Paris, France", 48.8566, 2.3522), ("Kyoto, Japan", 35.0116, 135.7681),
          ("San Francisco, United States", 37.7749, -122.4194),
          ("Lisbon, Portugal", 38.7223, -9.1393), ("Mexico City, Mexico", 19.4326, -99.1332),
          ("Reykjavik, Iceland", 64.1466, -21.9426), ("Sydney, Australia", -33.8688, 151.2093),
          ("Portland, United States", 45.5152, -122.6784)]
LABELS = ["red", "orange", "yellow", "green", "blue", "purple"]
WORDS = ("invoice receipt order total shipping meeting agenda notes design brand logo poster "
         "typography grid layout colour palette sketch photo travel recipe kitchen garden "
         "chair table lamp watch shoe jacket camera lens film print paper ticket menu map "
         "train flight hotel museum gallery concert album cover book chapter quote error "
         "screenshot settings password update version release dashboard chart report").split()
NAMESPACES = ["brand", "color", "place", "project", "style", "person", "type"]


def ulid(ms: int, rnd: random.Random) -> str:
    n = (ms << 80) | rnd.getrandbits(80)
    return "".join(CROCKFORD[(n >> (5 * i)) & 31] for i in range(25, -1, -1))


def zipf_weights(n: int, s: float = 1.1) -> list[float]:
    return [1 / (k ** s) for k in range(1, n + 1)]


def dsn_from_env(url: str) -> str:
    # SQLAlchemy URL -> libpq URL
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def tiny_webp() -> tuple[bytes, str]:
    """A 64x48 WebP thumbnail and its ThumbHash ("" without Pillow)."""
    try:
        from PIL import Image

        im = Image.new("RGB", (64, 48), (118, 118, 118))
        for x in range(64):
            for y in range(24, 48):
                im.putpixel((x, y), (60 + x, 90, 140))
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=60)
        data = buf.getvalue()
        try:
            from kist.services import thumbhash

            tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"kist-bench-{os.getpid()}.webp"
            tmp.write_bytes(data)
            try:
                return data, thumbhash.from_image(tmp) or ""
            finally:
                tmp.unlink(missing_ok=True)
        except Exception:
            return data, ""
    except ImportError:
        # a 1x1 grey lossless WebP
        return base64.b64decode(
            "UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA=="), ""


def copy_rows(cur, sql: str, rows) -> int:
    n = 0
    with cur.copy(sql) as cp:
        for row in rows:
            cp.write_row(row)
            n += 1
    return n


class Lib:
    def __init__(self, args):
        self.args = args
        self.rnd = random.Random(args.seed)
        self.now = datetime.now(UTC).replace(microsecond=0)
        self.thumbhash = ""

    # ---------- vocabulary ----------

    def make_tags(self) -> list[str]:
        rnd = self.rnd
        names: set[str] = set()
        while len(names) < self.args.tags:
            w = rnd.choice(WORDS)
            r = rnd.random()
            if r < 0.35:
                name = f"{rnd.choice(NAMESPACES)}:{w}{rnd.randint(1, 60)}"
            elif r < 0.7:
                name = f"{w}-{rnd.choice(WORDS)}"
            else:
                name = f"{w}{rnd.randint(1, 999)}"
            names.add(name)
        out = sorted(names)
        rnd.shuffle(out)  # Zipf rank is random with respect to the name
        return out

    def make_folders(self) -> list[tuple[str, str, str | None, int]]:
        rnd = self.rnd
        folders: list[tuple[str, str, str | None, int]] = []
        depth: dict[str, int] = {}
        base_ms = int((self.now - timedelta(days=5000)).timestamp() * 1000)
        for i in range(self.args.folders):
            fid = ulid(base_ms + i, rnd)
            parents = [f for f in folders if depth[f[0]] < 3]
            parent = None
            if folders and rnd.random() < 0.85 and parents:
                parent = rnd.choice(parents[-60:])[0]
            depth[fid] = depth[parent] + 1 if parent else 0
            name = f"{rnd.choice(WORDS).title()} {rnd.choice(WORDS)} {i}"
            folders.append((fid, name, parent, i))
        return folders

    # ---------- items ----------

    def item_rows(self, tag_count: int, folder_ids: list[str]):
        """Yields (items row, tag indexes, folder ids, text row, embed?)."""
        a, rnd = self.args, self.rnd
        span = 15 * 365 * DAY
        kinds = [k for k in KINDS for _ in range(k[0])]
        tag_w = zipf_weights(tag_count)
        tag_cum = []
        acc = 0.0
        for w in tag_w:
            acc += w
            tag_cum.append(acc)
        folder_w = zipf_weights(len(folder_ids), 0.8) if folder_ids else []
        import bisect

        def pick_tag() -> int:
            return bisect.bisect_left(tag_cum, rnd.random() * acc)

        for n in range(a.items):
            # busier recent years: density grows towards now
            age = span * (1 - rnd.random() ** 0.55)
            created = self.now - timedelta(seconds=age)
            ms = int(created.timestamp() * 1000)
            item_id = ulid(ms, rnd)
            _, mime, ext, kind = rnd.choice(kinds)
            is_image = mime.startswith("image/") and kind != "design"
            w, h = (rnd.choice([(4032, 3024), (3024, 4032), (6000, 4000), (1170, 2532),
                                (2880, 1800), (1920, 1080), (1080, 1350), (800, 600)])
                    if kind not in ("audio", "font", "other") else (None, None))
            taken = None
            exif: dict | None = None
            has_gps, lat, lon, place, camera = False, None, None, None, None
            if kind == "photo" and rnd.random() < 0.85 or kind == "video" and rnd.random() < 0.5:
                taken = created - timedelta(seconds=rnd.random() * 400 * DAY)
                camera = rnd.choice(CAMERAS)
                exif = {"camera": camera, "taken_at": taken.replace(tzinfo=None).isoformat()}
                if rnd.random() < 0.35:
                    place, plat, plon = rnd.choice(PLACES)
                    lat = round(plat + rnd.gauss(0, 0.05), 6)
                    lon = round(plon + rnd.gauss(0, 0.05), 6)
                    has_gps = True
                    exif.update(lat=lat, lon=lon)
            media: dict = {"checked_rev": 1}
            if exif:
                media["exif" if kind != "video" else "video"] = exif
            if place:
                city, country = place.split(", ")
                media["place"] = {"city": city, "country": country}
            colours = None
            if w:
                k = rnd.randint(3, 5)
                pcts = sorted((rnd.random() for _ in range(k)), reverse=True)
                total = sum(pcts)
                colours = [{"hex": f"#{rnd.randrange(1 << 24):06x}",
                            "pct": round(p / total, 3)} for p in pcts]
            rating = rnd.choices([0, 1, 2, 3, 4, 5], [70, 3, 5, 10, 8, 4])[0]
            flag = rnd.choices([None, "pick", "reject"], [93, 5, 2])[0]
            label = rnd.choice(LABELS) if rnd.random() < 0.05 else None
            deleted = (created + timedelta(days=rnd.random() * 30)
                       if rnd.random() < a.trash_fraction else None)
            modified = created + timedelta(seconds=rnd.random() * 30 * DAY * rnd.random())
            modified = min(modified, self.now)
            notes = " ".join(rnd.choices(WORDS, k=rnd.randint(3, 12))) \
                if rnd.random() < 0.05 else ""
            stem = (f"IMG_{rnd.randint(1000, 9999)}" if kind == "photo" else
                    f"Screenshot {created:%Y-%m-%d at %H.%M.%S}" if kind == "screenshot" else
                    f"{rnd.choice(WORDS)}-{rnd.choice(WORDS)}-{n}")
            preview = "none" if kind == "other" else "ready"
            row = (
                item_id, f"{stem}.{ext}", ext, mime, rnd.randint(20_000, 40_000_000),
                w, h, (round(rnd.uniform(3, 600), 2) if kind in ("video", "audio") else None),
                secrets.token_hex(32), rnd.getrandbits(63) - (1 << 62),
                json.dumps(colours) if colours else None,
                rating, notes, created, modified, deleted, preview, 1,
                self.now, self.thumbhash if preview == "ready" else None,
                flag, label, json.dumps(media), taken, has_gps, lat, lon, camera, None,
                (h > w) if w and h and kind in ("photo", "image", "screenshot", "video") else None,
                True if kind == "audio" else (rnd.random() < 0.8 if kind == "video" else None),
                place,
            )
            ntags = 0 if rnd.random() < 0.25 else min(12, int(rnd.expovariate(1 / 3)) + 1)
            tags = {pick_tag() for _ in range(ntags)}
            folders: set[str] = set()
            if folder_ids and rnd.random() < 0.7:
                folders.add(rnd.choices(folder_ids, folder_w)[0])
                if rnd.random() < 0.12:
                    folders.add(rnd.choices(folder_ids, folder_w)[0])
            text = None
            if (kind == "screenshot" and rnd.random() < 0.4) or kind == "pdf":
                words = " ".join(rnd.choices(WORDS, k=rnd.randint(8, 120)))
                text = (item_id, "pdf" if kind == "pdf" else "ocr", words, "en", "bench")
            embed = is_image and rnd.random() < a.embed_fraction
            yield row, tags, folders, text, embed


ITEM_COLUMNS = (
    "id, filename, ext, mime, size_bytes, width, height, duration_s, sha256, phash, "
    "dominant_colors, rating, notes, created_at, modified_at, deleted_at, preview_status, "
    "preview_rev, fixity_checked_at, thumbhash, flag, label, media, taken_at, has_gps, lat, "
    "lon, camera, lens, vertical, has_audio, place"
)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--items", type=int, default=250_000)
    p.add_argument("--tags", type=int, default=800)
    p.add_argument("--folders", type=int, default=150)
    p.add_argument("--smart-folders", type=int, default=10)
    p.add_argument("--embed-fraction", type=float, default=0.3,
                   help="share of images given an embedding (default 0.3)")
    p.add_argument("--trash-fraction", type=float, default=0.01)
    p.add_argument("--seed", type=int, default=6)
    p.add_argument("--database-url", default=os.environ.get("KIST_DATABASE_URL"))
    p.add_argument("--library", default=os.environ.get("KIST_LIBRARY_DIR", "/library"))
    p.add_argument("--model", default=os.environ.get("KIST_ML_MODEL", "ViT-B-32__openai"))
    p.add_argument("--dim", type=int, default=512)
    p.add_argument("--no-files", action="store_true",
                   help="don't make item directories (thumbnails then 404)")
    p.add_argument("--allow-existing", action="store_true",
                   help="add to a database that already has items (never a real library!)")
    args = p.parse_args(argv)
    if not args.database_url:
        p.error("set KIST_DATABASE_URL or --database-url")

    import psycopg

    lib = Lib(args)
    thumb, lib.thumbhash = tiny_webp()
    t0 = time.monotonic()
    with psycopg.connect(dsn_from_env(args.database_url)) as conn:
        cur = conn.cursor()
        db = cur.execute("select current_database()").fetchone()[0]
        existing = cur.execute("select count(*) from items").fetchone()[0]
        if existing and not args.allow_existing:
            print(f"refusing: database {db} already has {existing} items "
                  "(a real library?). Use a fresh database.", file=sys.stderr)
            return 2
        print(f"writing {args.items} synthetic items into database {db!r}, "
              f"library {args.library}", flush=True)

        tags = lib.make_tags()
        by_name = dict((name.lower(), tid) for tid, name in cur.execute(
            "insert into tags (name) select unnest(%s::text[]) "
            "on conflict (name) do update set name = excluded.name returning id, name::text",
            (tags,)).fetchall())
        tag_ids = [by_name[t.lower()] for t in tags]  # in Zipf rank order
        folders = lib.make_folders()
        copy_rows(cur, "COPY folders (id, name, parent_id, position) FROM STDIN", folders)
        folder_ids = [f[0] for f in folders]

        items_dir = Path(args.library) / "items"
        template = None
        if not args.no_files:
            items_dir.mkdir(parents=True, exist_ok=True)
            template = Path(args.library) / ".bench-thumb.webp"
            template.write_bytes(thumb)

        item_tags, item_folders, texts, embeds, ids, photos = [], [], [], [], [], []

        def rows():
            for row, tset, fset, text, embed in lib.item_rows(len(tag_ids), folder_ids):
                ids.append(row[0])
                item_tags.extend((row[0], tag_ids[t]) for t in tset)
                item_folders.extend((row[0], f) for f in fset)
                if text:
                    texts.append(text)
                if embed:
                    embeds.append((row[0], lib.rnd.randrange(64)))
                if row[3] in ("image/jpeg", "image/heic") and row[15] is None:
                    photos.append(row[0])
                yield row

        n = copy_rows(cur, f"COPY items ({ITEM_COLUMNS}) FROM STDIN", rows())
        print(f"  items: {n} ({time.monotonic() - t0:.0f} s)", flush=True)
        copy_rows(cur, "COPY item_tags (item_id, tag_id) FROM STDIN", item_tags)
        copy_rows(cur, "COPY item_folders (item_id, folder_id) FROM STDIN", item_folders)
        copy_rows(cur, "COPY item_text (item_id, source, text, lang, model) FROM STDIN", texts)
        print(f"  tags {len(item_tags)}, folder memberships {len(item_folders)}, "
              f"text rows {len(texts)} ({time.monotonic() - t0:.0f} s)", flush=True)

        # stacks: ~3 % of photos in stacks of 2-3 (RAW+JPEG, bursts), so the
        # grid's stacks=collapse filter has work to do
        rnd = lib.rnd
        stacks, members, i = [], [], 0
        while i < len(photos) - 3:
            if rnd.random() < 0.015:
                group = photos[i:i + rnd.choice((2, 2, 3))]
                sid = ulid(int(time.time() * 1000), rnd)
                stacks.append((sid, group[0]))
                members += [(m, sid) for m in group]
                i += len(group)
            else:
                i += 1
        if stacks:
            copy_rows(cur, "COPY stacks (id, cover_item_id) FROM STDIN", stacks)
            cur.execute("create temp table bench_stack (item_id text, stack_id text)")
            copy_rows(cur, "COPY bench_stack (item_id, stack_id) FROM STDIN", members)
            cur.execute("update items set stack_id = b.stack_id from bench_stack b "
                        "where items.id = b.item_id")
            print(f"  stacks: {len(stacks)} ({len(members)} items)", flush=True)

        # smart folders: saved GET /api/items queries (what the sidebar lists)
        smart = []
        for i in range(args.smart_folders):
            q = rnd.choice([
                {"tags_any": rnd.sample(tags[:40], 3)},
                {"rating": 4, "type": "image"},
                {"tags": [tags[i]], "order": "-taken"},
                {"q": rnd.choice(WORDS)},
                {"view": "unfiled"},
            ])
            smart.append((ulid(int(time.time() * 1000) + i, rnd), f"Smart {i}", json.dumps(q), i))
        copy_rows(cur, "COPY smart_folders (id, name, query, position) FROM STDIN", smart)

        # embeddings: unit vectors around 64 random centres, made in Postgres
        if embeds:
            centres = []
            for _ in range(64):
                v = [rnd.gauss(0, 1) for _ in range(args.dim)]
                norm = math.sqrt(sum(x * x for x in v))
                centres.append("[" + ",".join(f"{x / norm:.5f}" for x in v) + "]")
            cur.execute("create temp table bench_centre (k int primary key, v vector)")
            copy_rows(cur, "COPY bench_centre (k, v) FROM STDIN", enumerate(centres))
            cur.execute("create temp table bench_embed (item_id text, k int)")
            copy_rows(cur, "COPY bench_embed (item_id, k) FROM STDIN", embeds)
            cur.execute(
                "insert into item_embeddings (item_id, model, dim, vector) "
                "select e.item_id, %s, %s, l2_normalize(c.v + array("
                "  select (random() - 0.5) * 0.08 from generate_series(1, %s) "
                "  where e.item_id is not null)::vector)::halfvec "
                "from bench_embed e join bench_centre c using (k)",
                (args.model, args.dim, args.dim))
            print(f"  embeddings: {len(embeds)} ({time.monotonic() - t0:.0f} s)", flush=True)
        conn.commit()
    # steady state: visibility map set (index-only scans), planner statistics
    with psycopg.connect(dsn_from_env(args.database_url), autocommit=True) as conn:
        conn.execute("vacuum (analyze) items, item_tags, item_folders, item_text, "
                     "item_colors, item_embeddings, folders, tags, stacks")
    print(f"  vacuum analyze ({time.monotonic() - t0:.0f} s)", flush=True)

    if template is not None:
        for i, item_id in enumerate(ids):
            d = items_dir / item_id
            d.mkdir(exist_ok=True)
            for name in ("thumb_256.webp", "thumb_1024.webp"):
                target = d / name
                if not target.exists():
                    try:
                        os.link(template, target)
                    except OSError:
                        target.write_bytes(thumb)
            if i and i % 50_000 == 0:
                print(f"  item directories: {i}", flush=True)
    print(f"done: {len(ids)} items in {time.monotonic() - t0:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
