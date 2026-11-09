"""AI: pgvector, item_embeddings, item_text, ai_helpers

Needs the pgvector extension, which the stock postgres:16 image doesn't
have. Kist's compose file uses pgvector/pgvector:pg16 (the same Postgres 16
with pgvector added; it uses the existing data directory as is).

Revision ID: 0101
Revises: 0010
Create Date: 2026-11-09

"""

import sqlalchemy as sa
from alembic import op

revision = "0101"
down_revision = "0010"
branch_labels = None
depends_on = None

DEFAULT_MODEL = "ViT-B-32__openai"
DEFAULT_DIM = 512

NO_PGVECTOR = (
    "Kist 2.0 needs the pgvector extension in Postgres, and this database "
    "server doesn't have it. In docker-compose.yml change the db service's image "
    "from postgres:16 to pgvector/pgvector:pg16 (the same Postgres 16 with "
    "pgvector added; it uses your existing data directory unchanged), then run "
    "`docker compose pull db && docker compose up -d`. See the upgrade notes in "
    "CHANGELOG.md."
)


def upgrade() -> None:
    bind = op.get_bind()
    available = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
    ).first()
    if available is None:
        raise RuntimeError(NO_PGVECTOR)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # one row per item and model; halfvec without a fixed size so several
    # models (512-d, 768-d) can live side by side during a switch. Each
    # model gets its own partial HNSW index over a cast to its size
    # (services/ai.ensure_vector_index makes them for opt-in models).
    op.execute(
        """
        CREATE TABLE item_embeddings (
            item_id varchar(26) NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            model text NOT NULL,
            dim integer NOT NULL,
            vector halfvec NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (item_id, model)
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_item_embeddings_hnsw_vit_b_32_openai ON item_embeddings "
        f"USING hnsw ((vector::halfvec({DEFAULT_DIM})) halfvec_cosine_ops) "
        f"WHERE model = '{DEFAULT_MODEL}'"
    )
    op.execute(
        """
        CREATE TABLE item_text (
            item_id varchar(26) NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            source text NOT NULL CHECK (source IN ('ocr', 'pdf')),
            text text NOT NULL,
            lang text,
            model text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (item_id, source)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_item_text_tsv ON item_text USING gin (to_tsvector('simple', text))"
    )
    op.execute(
        """
        CREATE TABLE ai_helpers (
            name text PRIMARY KEY,
            last_seen timestamptz NOT NULL DEFAULT now(),
            jobs_done bigint NOT NULL DEFAULT 0,
            info jsonb NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    # the worker and helpers look for open AI jobs by kind
    op.execute(
        "CREATE INDEX ix_jobs_open_kind ON jobs (kind, id) "
        "WHERE status IN ('pending', 'running')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_jobs_open_kind")
    op.execute("DROP TABLE IF EXISTS ai_helpers")
    op.execute("DROP TABLE IF EXISTS item_text")
    op.execute("DROP TABLE IF EXISTS item_embeddings")
    # the extension stays: other databases on the server may use it
