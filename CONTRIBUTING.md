# Contributing

Bug reports and pull requests are welcome. For anything larger than a small
fix, please open an issue first so we can agree on the approach.

## Setup

See **Development** in the [README](README.md). The quickest full
environment is the compose stack itself. Run the tests inside it against a
separate database:

```bash
docker compose up -d --build
docker compose exec db psql -U kist -c "CREATE DATABASE kist_test"
docker compose run --rm -v "$PWD/tests:/app/tests" \
  -e KIST_TEST_DATABASE_URL=postgresql+psycopg://kist:kist@db:5432/kist_test \
  app sh -c "pip install -q pytest && python -m pytest"
```

The test fixtures wipe the test database between tests, so never point
`KIST_TEST_DATABASE_URL` at a real library.

## Before opening a pull request

- `ruff check kist tests migrations` passes.
- `pytest` passes, with new behavior covered by a test.
- `cd web && npm run build` passes (it type-checks the UI).
- Schema changes come with an Alembic migration, and anything stored per item
  is also written to `meta.json` so `rebuild-index` can restore it.
- Business logic goes in `kist/services/`, so the REST API, MCP tools, CLI,
  and worker all share it.
- User-facing changes get a line under **Unreleased** in `CHANGELOG.md`.
