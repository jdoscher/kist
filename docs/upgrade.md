# Upgrade

Kist's migrations run when the app starts, and every new piece of data is a
plain file in the library, so an upgrade is: back up, pull, rebuild, run the
one-off backfills the release notes name. The full notes for each version are
in [CHANGELOG.md](https://github.com/jdoscher/kist/blob/main/CHANGELOG.md).

## Every upgrade

```bash
cd kist
docker compose exec db pg_dump -U kist kist | gzip > kist-before-upgrade.sql.gz
git pull
docker compose build && docker compose up -d
docker compose logs app | grep -E "Running upgrade|ERROR"
docker compose exec worker kist selftest
```

The database dump is the quick way back. The library itself is never rewritten
by a migration, and `kist rebuild-index` can rebuild the database from it
anyway.

## From 1.5 to 2.0

In this order:

1. **Back up the database** (above).
2. **Switch the database image to `pgvector/pgvector:pg16`.** Kist's
   `docker-compose.yml` already uses it; it is the official `postgres:16`
   image plus the pgvector extension, so it opens your existing `pgdata`
   volume as is. If you keep your own compose file, change
   `image: postgres:16` to `image: pgvector/pgvector:pg16`. Stay on `pg16`.
3. **Decide about the trash.** It now empties itself after 30 days; set
   `KIST_TRASH_RETENTION_DAYS=0` first to keep it forever.
4. `docker compose pull db && docker compose build && docker compose up -d`.
   The migrations run on start, one line each.
5. **One-off backfills**, all safe to re-run, best as one detached chain so
   you can close the SSH session:

    ```bash
    docker compose exec -d worker sh -c '(
      echo "== rewrite-meta"; kist rewrite-meta;
      echo "== geo-download"; kist geo-download;
      echo "== media-backfill"; kist media-backfill;
      echo "== colors"; kist regen-previews --colors-only;
      echo "== ai-backfill"; kist ai-backfill;
      echo "== verify"; kist verify;
      echo "== ALL DONE") > /library/upgrade.log 2>&1'
    tail -n 3 library/upgrade.log    # later: the last step it reached
    ```

    `geo-download` is optional (about 10 MB from GeoNames, once; it names the
    places of photos with GPS). The commands print nothing until they finish;
    on a NAS, `rewrite-meta` and `media-backfill` each take roughly 10–20
    minutes per 4,000–5,000 items, and AI embeddings 1.5–2.5 hours per
    100,000 items (set `KIST_AI_WINDOW=01:00-06:00` to keep that at night).
    Watch progress with
    `docker compose exec worker sh -c 'find /library/items -name meta.json -mmin -2 | wc -l'`.

6. **New files to back up** with the library: `items/*/ai/`, `boards/`,
   `vocabulary.json`, `.kist/` (tokens, people, templates, shares, export
   presets) and the item `page/` snapshots. Backing up the whole library folder
   covers them all. The `ml-models` volume needn't be backed up.

Optional extras: captions ([AI](ai.md)), faces (`KIST_FACES=1`), map tiles
([Maps](maps.md)), Intel GPU video encoding (`KIST_HWACCEL=vaapi`).

## Going back

Stop the app, worker and ml services, restore the dump into a fresh database,
check out the previous version and rebuild. 2.0's extra files beside your
items are ignored by 1.5.
