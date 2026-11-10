"""item_text.tsv: the stored tsvector that text= ranking reads (Kist 2.0 M6 perf)

text= ranks matching items by ts_rank_cd(to_tsvector('simple', text), q).
The GIN index finds the rows, but ranking parsed every matching row's text
again: 22.8k rows of OCR text took 1.2 s of a 1.5 s search on dev0 (N100,
250k items). A generated column keeps the same tsvector (same expression,
'simple' is fixed), so ts_rank_cd gives the same scores without reparsing.
Matching still goes through ix_item_text_tsv on the expression.

Revision ID: 0803
Revises: 0802
Create Date: 2026-11-10

"""

from alembic import op

revision = "0803"
down_revision = "0802"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE item_text ADD COLUMN IF NOT EXISTS tsv tsvector "
               "GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, text)) STORED")


def downgrade() -> None:
    op.drop_column("item_text", "tsv")
