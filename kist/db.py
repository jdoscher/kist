from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

# JIT off for Kist's connections. Postgres compiles a query with LLVM once
# its estimated cost passes jit_above_cost (100k), and the stacks=collapse
# filter's correlated subqueries pushed every listing far past that, so a
# 1 ms page paid 250-800 ms of compilation on a 4-core N100 (EXPLAIN
# ANALYZE on 250k items: "JIT: ... Total 776 ms" of a 1216 ms query).
# Kist's queries are short index scans; none run long enough to win it back.
# The official postgres image ships LLVM, so prod paid it too.
#
# pg_trgm.similarity_threshold = 0.25: the `%` operator then holds for every
# filename whose similarity to q passes keyword search's exact test
# (similarity > 0.25, ranking.TRGM_THRESHOLD), so the filename trigram
# index can find them; the default 0.3 would miss some. Nothing else in
# Kist uses `%`.
DB_OPTIONS = "-c jit=off -c pg_trgm.similarity_threshold=0.25"

engine = create_engine(get_settings().database_url, pool_pre_ping=True, pool_size=5,
                       connect_args={"options": DB_OPTIONS})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
