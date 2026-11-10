"""Speed at 250k items (2.0 M6 perf, branch m6p): every optimised read path
returns what it returned before.

Each optimisation keeps the old SQL here as the reference and compares the
two on a seeded synthetic library (scripts/bench/make_library.py, plus
vocabulary rules, stacks with trashed or hidden covers, and a
folder-limited reader), so a faster plan can't quietly change a result.
"""

import sqlalchemy as sa


def test_kist_connections_run_without_jit(database):
    """LLVM JIT cost 250-800 ms per listing on dev0's N100 (EXPLAIN ANALYZE
    "JIT: Total 776 ms" on a 1.2 s tag page); the app's engine turns it off."""
    from kist.db import SessionLocal, engine

    with engine.connect() as conn:
        assert conn.execute(sa.text("SHOW jit")).scalar_one() == "off"
    with SessionLocal() as s:
        assert s.execute(sa.text("SHOW jit")).scalar_one() == "off"
