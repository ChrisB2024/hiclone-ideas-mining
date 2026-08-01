"""hnsw vector indexes

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-28

**This file lives outside `migrations/versions/` on purpose (FINDING-2.5).**

Alembic only scans the directories named in `version_locations`, and it does not
recurse. Keeping this revision in `versions/` made it the chain head, so the ordinary
`alembic upgrade head` applied it — building both hnsw indexes on empty tables, which
is the exact thing the deferral exists to avoid. A docstring saying "deferred" does not
defer anything; being outside the scanned path does.

So there are two configs, and which one you run *is* the decision:

    alembic upgrade head                          # 0001 only. The default.
    alembic -c alembic.deferred.ini upgrade head  # adds the indexes. Once, later.

Run the second once there are a few thousand signals. Until then sequential scans over
a few hundred centroids are fast, and an hnsw graph built on an empty table is
optimised for nothing — its shape is fixed at build time from the rows present.

``vector_cosine_ops``, NOT the L2 default. Voyage embeddings are normalized for cosine
similarity, and the wrong operator class does not error — it silently returns worse
neighbours, which shows up as clusters that look almost right.

The `clusters` index is optional at this scale (hundreds to low thousands of rows). It
is included because assignment runs one nearest-centroid query per new signal, and that
is the query that gets slow first. Note that hnsw and a `WHERE vertical = :v` filter
interact badly in general — the index is searched first, then filtered, so a selective
filter can return fewer rows than LIMIT asked for. It does not bite here because the
filter keeps roughly half the rows, and it is the reason not to reach for a
partial-index-per-vertical scheme as a "fix" later.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: An index is only reusable if it is valid, is an hnsw index, and uses the cosine
#: operator class. Anything else wearing the name must go.
_INDEX_STATE_SQL = sa.text(
    """
    SELECT idx.indisvalid AS is_valid,
           am.amname      AS access_method,
           opc.opcname    AS operator_class
    FROM pg_index idx
    JOIN pg_class cls ON cls.oid = idx.indexrelid
    JOIN pg_am am ON am.oid = cls.relam
    JOIN pg_opclass opc ON opc.oid = idx.indclass[0]
    WHERE cls.relname = :name
    """
)


def _drop_unusable_index(name: str) -> None:
    """Drop an existing index of this name unless it is exactly what we want.

    Inputs:
        name: the index name this migration owns.

    FINDING-4.5. ``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` matches on the name alone,
    so it silently accepts:

    * an **invalid** index left behind by a concurrent build that failed partway — it
      is never used to answer queries but still costs on every write; and
    * an index on the **wrong operator class**. Voyage vectors are normalized for
      cosine, and an L2 index does not error — it quietly returns worse neighbours.
      That is the failure mode this whole module exists to avoid, and it would have
      been recorded as revision 0002, i.e. as done.

    Either way Alembic stamps the revision and the system believes it has an index it
    does not have. Reconciling is safe because these two names are owned by this
    migration; nothing else in the schema creates them.
    """
    existing = op.get_bind().execute(_INDEX_STATE_SQL, {"name": name}).mappings().first()
    if existing is None:
        return

    usable = (
        existing["is_valid"]
        and existing["access_method"] == "hnsw"
        and existing["operator_class"] == "vector_cosine_ops"
    )
    if usable:
        return

    print(
        f"  reconciling {name}: found "
        f"{existing['access_method']}/{existing['operator_class']} "
        f"valid={existing['is_valid']} — dropping and rebuilding"
    )
    op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")


def upgrade() -> None:
    """Build both hnsw indexes without holding a write lock (FINDING-3.4).

    ``CREATE INDEX`` takes a ``SHARE`` lock, which blocks every INSERT and UPDATE on
    the table for the whole build. On ``enriched_signals`` — the table this migration
    only becomes worth running on once it holds thousands of rows — that is a stall
    measured in minutes, and it lands on the two tables the pipeline writes to on every
    tick. ``CONCURRENTLY`` builds in two passes and never blocks writers.

    ``CONCURRENTLY`` cannot run inside a transaction, and Alembic wraps migrations in
    one, so both statements go in an ``autocommit_block``.

    The cost of that block: these statements are no longer covered by the migration's
    transaction. If the second index fails, the first is already committed. Both use
    ``IF NOT EXISTS`` so a re-run is safe.

    ``IF NOT EXISTS`` matches on the index **name only**, so any leftover index wearing
    the right name is accepted — including an invalid one from a failed concurrent
    build, or one built on the wrong operator class. ``_drop_unusable_index`` below
    removes those first (FINDING-4.5).
    """
    if op.get_context().as_sql:
        # Offline (`--sql`) mode has no connection to inspect, so emit the plain
        # statements. Reconciling an existing index is inherently an online operation.
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_signals_embedding_hnsw "
            "ON enriched_signals USING hnsw (embedding vector_cosine_ops)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clusters_centroid_hnsw "
            "ON clusters USING hnsw (centroid vector_cosine_ops)"
        )
        return

    with op.get_context().autocommit_block():
        _drop_unusable_index("ix_signals_embedding_hnsw")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_signals_embedding_hnsw "
            "ON enriched_signals USING hnsw (embedding vector_cosine_ops)"
        )

        _drop_unusable_index("ix_clusters_centroid_hnsw")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clusters_centroid_hnsw "
            "ON clusters USING hnsw (centroid vector_cosine_ops)"
        )


def downgrade() -> None:
    """Drop both indexes, also concurrently — a plain DROP INDEX takes ACCESS EXCLUSIVE."""
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_clusters_centroid_hnsw")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_signals_embedding_hnsw")
