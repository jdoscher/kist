# Backup and verify

The library folder is the whole state. Postgres is only an index that Kist can
rebuild from the files; the `pgdata` volume is never the only copy of
anything.

```
library/
├── items/<id>/          original, previews, meta.json, events.jsonl (history),
│                        fixity.jsonl, original.xmp, ai/, page/
├── boards/<id>/         board.json, thumb.webp, events.jsonl
├── folders.json         folders and smart folders
├── vocabulary.json      tag aliases and implied tags
└── .kist/               hashed tokens, people, templates, shares, presets,
                         db.sql.gz, the last verify and backup results
```

## The simplest backup

```bash
rsync -a --delete /path/to/library/ backup-host:/backups/kist/
```

## Built-in backups with restic

Set `KIST_BACKUP_REPO` (a path mounted into the worker, or `sftp:`, `rest:`,
`s3:`, `b2:` …) and `RESTIC_PASSWORD` (or `RESTIC_PASSWORD_FILE`), then:

```bash
docker compose exec worker kist backup
```

It dumps the database to `library/.kist/db.sql.gz`, runs `restic backup` of
the library (creating the repository on first use) and records the result.
`KIST_BACKUP_INTERVAL_HOURS` makes the worker do it on a schedule.

**Settings → Archive health** shows the last backup and its age, fixity
coverage, the last verify and a 3-2-1 checklist (a repository set, a backup
within 7 days, a copy offsite, which you tick yourself, and a clean verify).

## Checks Kist runs for you

- **`kist verify`**, daily: where the database and the files disagree (missing
  originals, stale `meta.json`, failed earlier writes). It changes nothing and
  names the command that fixes each problem.
- **Fixity**: the worker re-hashes about 1/30 of the originals every day, so
  every file is checked monthly. A mismatch shows in Needs attention with the
  expected SHA-256, so you can find the right copy in a backup.
- **`kist selftest`**: runs tiny files of every kind through the real preview
  pipeline. Worth running after each rebuild.

`GET /api/health/deep` (with a token) reports all of this in one answer for an
uptime checker.

## Restore, or move to new hardware

```bash
# put the library back where KIST_LIBRARY points, then
docker compose up -d db app                  # empty database; migrations run
docker compose exec app kist rebuild-index   # everything from the files
docker compose up -d
```

`rebuild-index` restores folders, tokens, items with their tags, history,
stacks, flags and labels, AI results, annotations, the vocabulary, people,
templates and boards. Nothing is recomputed. Anything it can't place is
printed with `!` and makes the exit code 2.
