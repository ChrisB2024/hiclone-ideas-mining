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

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_signals_embedding_hnsw ON enriched_signals "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX ix_clusters_centroid_hnsw ON clusters "
        "USING hnsw (centroid vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_clusters_centroid_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_signals_embedding_hnsw")
