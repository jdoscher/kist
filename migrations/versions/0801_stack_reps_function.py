"""kist_stack_reps(): each stack's representative, for stacks=collapse
(Kist 2.0 M6 perf)

search.build_filters(stacks="collapse") keeps an item outside any stack or
its stack's representative: the cover when the caller sees it, else the
first member it sees (services/stacks.representative_filter). Written as
two subqueries correlated with every row, Postgres costed it per row of
the whole table (6.25 M for 250k items) and planned every grid listing
around that: a common tag's count probed 80k items one by one (550 ms on
dev0's N100), the unfiled count ran nested loops, and the cost started JIT.

As a hashed subplan, (stack_id, id) IN (SELECT ... FROM stacks), it runs
once per query (1,700 stacks: ~9 ms on dev0), but Postgres still adds its
estimated cost (29k: two index probes per stack) to every rescan of a
parameterized index scan, so a rare tag's page fell back to a hash join
over every live item (55 ms instead of 3).

This function returns the same rows from the same SQL, declared at a fixed
small cost (ROWS 2000, COST 10000 = 25 cost units), so the planner chooses
by the filter's real selectivity. plpgsql keeps it from being inlined
(which would expose the per-stack cost again). visible_folders is a
folder-limited reader's folders (NULL for everyone else), like
stacks._visible_cond.

Revision ID: 0801
Revises: 0701
Create Date: 2026-11-10

"""

from alembic import op

revision = "0801"
down_revision = "0701"
branch_labels = None
depends_on = None

# keep in step with services/stacks.py (_visible_cond, representative_filter)
STACK_REPS = """
CREATE OR REPLACE FUNCTION kist_stack_reps(visible_folders text[])
RETURNS TABLE (stack_id varchar, item_id varchar)
LANGUAGE plpgsql STABLE PARALLEL SAFE ROWS 2000 COST 10000 AS $$
BEGIN
  RETURN QUERY
  SELECT s.id, coalesce(
    (SELECT c.id FROM items c
      WHERE c.id = s.cover_item_id AND c.deleted_at IS NULL
        AND (visible_folders IS NULL OR EXISTS (
              SELECT 1 FROM item_folders f
               WHERE f.item_id = c.id AND f.folder_id = ANY (visible_folders)))),
    (SELECT min(m.id) FROM items m
      WHERE m.stack_id = s.id AND m.deleted_at IS NULL
        AND (visible_folders IS NULL OR EXISTS (
              SELECT 1 FROM item_folders f
               WHERE f.item_id = m.id AND f.folder_id = ANY (visible_folders)))))
  FROM stacks s;
END
$$
"""


def upgrade() -> None:
    op.execute(STACK_REPS)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS kist_stack_reps(text[])")
