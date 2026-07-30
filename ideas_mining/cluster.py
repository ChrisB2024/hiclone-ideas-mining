"""Embed + cluster — the highest-risk module in the system.

INV-2 lives here as a single SQL clause. Read .spec/modules/cluster.md before touching
the assignment query.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime

import voyageai
from sqlalchemy import select, text, update

from ideas_mining.config import settings
from ideas_mining.db.models import Cluster, EnrichedSignal
from ideas_mining.db.session import get_session

log = logging.getLogger(__name__)

#: Placeholder timestamps for a freshly created cluster. Never observed:
#: RECOMPUTE_CLUSTER_SQL replaces both values inside the same transaction, before
#: commit. They exist only because the columns are NOT NULL — and an obviously-wrong
#: sentinel beats now(): if one ever survives to a digest, the 1970 date announces the
#: bug instead of looking like a perfectly fresh cluster.
_EPOCH = datetime.fromtimestamp(0, tz=UTC)

# The nearest-centroid query. Kept as a module constant so the vertical filter is
# visible in one place and hard to drop during a refactor.
#
#   *** DO NOT REMOVE `WHERE vertical = :vertical` ***
#
# Without it, structurally-identical pains from the two industries merge into one
# cluster whose distinct_authors is the SUM of both. That cluster then outranks every
# correct one and reaches the top of the digest. Nothing downstream detects it — it
# looks like the best result in the system while being unusable for outreach, because
# half its members are the wrong industry.
#
# `:emb` is bound as pgvector's text form ('[0.1,0.2,…]') and cast, because asyncpg has
# no native codec for a Python list of floats in a raw statement.
NEAREST_CENTROID_SQL = """
SELECT id, 1 - (centroid <=> CAST(:emb AS vector)) AS sim
FROM clusters
WHERE vertical = :vertical
ORDER BY centroid <=> CAST(:emb AS vector)
LIMIT 1
"""

# Recompute from members rather than nudging the centroid incrementally: one cheap
# query per assignment, and it cannot drift out of sync.
#
# count(DISTINCT rp.author) is the whole game — distinct_authors IS `frequency`, and
# frequency is the demand proxy (INV-7).
RECOMPUTE_CLUSTER_SQL = """
UPDATE clusters c SET
  centroid = sub.centroid,
  member_count = sub.n,
  distinct_authors = sub.authors,
  first_seen_at = sub.first_seen,
  last_seen_at = sub.last_seen
FROM (
  SELECT AVG(es.embedding)        AS centroid,
         count(*)                 AS n,
         count(DISTINCT rp.author) AS authors,
         min(rp.created_at)       AS first_seen,
         max(rp.created_at)       AS last_seen
  FROM enriched_signals es
  JOIN raw_posts rp ON rp.id = es.raw_post_id
  WHERE es.cluster_id = :cluster_id
) sub
WHERE c.id = :cluster_id
"""


def to_pgvector(embedding: list[float]) -> str:
    """Render a Python vector in pgvector's text input form.

    Inputs:
        embedding: the vector, length == settings.embedding_dim.

    Returns:
        e.g. "[0.1,0.2,0.3]" — accepted by ``CAST(… AS vector)``.

    Only used for raw-SQL binds; ORM columns take the list directly via the Vector type.
    """
    return "[" + ",".join(repr(float(value)) for value in embedding) + "]"


def _voyage() -> voyageai.AsyncClient:
    """Voyage client. Key comes from settings only, and is never logged."""
    return voyageai.AsyncClient(api_key=settings.voyage_api_key)


async def embed_pending(
    ctx: dict[str, object] | None = None, batch_size: int | None = None
) -> int:
    """Embed pain_point text for signals that don't have a vector yet.

    Inputs:
        ctx: ARQ job context, unused (FINDING-1.6).
        batch_size: texts per Voyage call. Defaults to settings.embed_batch_size.

    Returns:
        Number of signals embedded this run.

    Selector: ``WHERE embedding IS NULL AND vertical <> 'neither'``. Idempotent and
    resumable. 'neither' rows are archived, never embedded.

    Invariant INV-6 — embed ``pain_point`` ONLY. Not the title, not the body, not
    who_has_it, and **not a vertical prefix**. Prefixing "insurance: " onto every
    insurance pain is the obvious-looking way to separate the industries in vector
    space and is wrong: it shifts every vector in a vertical by the same constant,
    which inflates similarity between UNRELATED pains inside that vertical and blurs
    the threshold. The separation belongs in the query filter, where it's exact.

    Security: pain_point is model-generated text derived from untrusted posts. It is
    sent to Voyage as data; there is no instruction channel in an embeddings request.
    """
    size = batch_size or settings.embed_batch_size
    embedded = 0
    client = _voyage()

    try:
        while True:
            async with get_session() as session:
                signals = list(
                    (
                        await session.execute(
                            select(EnrichedSignal)
                            .where(
                                EnrichedSignal.embedding.is_(None),
                                EnrichedSignal.vertical != "neither",
                            )
                            .order_by(EnrichedSignal.id)
                            .limit(size)
                        )
                    ).scalars().all()
                )

                if not signals:
                    break

                result = await client.embed(
                    texts=[signal.pain_point for signal in signals],
                    model=settings.embed_model,
                    input_type="document",
                )

                if len(result.embeddings) != len(signals):
                    raise RuntimeError(
                        f"voyage returned {len(result.embeddings)} vectors for "
                        f"{len(signals)} texts — refusing to write misaligned rows"
                    )

                for signal, vector in zip(signals, result.embeddings, strict=True):
                    if len(vector) != settings.embedding_dim:
                        raise RuntimeError(
                            f"{settings.embed_model} returned dimension {len(vector)}, "
                            f"but the schema is vector({settings.embedding_dim}). "
                            "Re-check EMBEDDING_DIM before writing anything."
                        )
                    await session.execute(
                        update(EnrichedSignal)
                        .where(EnrichedSignal.id == signal.id)
                        .values(embedding=vector)
                    )
                    embedded += 1

            if len(signals) < size:
                break
    finally:
        # voyageai's async client holds an httpx session; not closing it leaks a
        # connection per worker tick.
        close = getattr(client, "close", None)
        if close is not None:
            await close()

    log.info("embedded %d signals", embedded)
    return embedded


async def assign_clusters(ctx: dict[str, object] | None = None) -> dict[str, int]:
    """Greedy incremental nearest-centroid assignment, within a vertical.

    Inputs:
        ctx: ARQ job context, unused (FINDING-1.6).

    Returns:
        {"assigned": int, "created": int, "joined": int}

    For each signal with ``embedding IS NOT NULL AND cluster_id IS NULL AND vertical
    <> 'neither'``, ordered by id:

    1. NEAREST_CENTROID_SQL with this row's vertical.
    2. sim >= settings.similarity_threshold -> join that cluster.
    3. else -> create a cluster with ``vertical = row.vertical``, label = pain_point,
       centroid = embedding.
    4. RECOMPUTE_CLUSTER_SQL for the affected cluster.

    Why greedy and not k-means: k-means needs ``k`` up front, and k is the answer we're
    looking for. It also reshuffles every assignment on each run, so frequency counts
    churn between digests and you can't tell a growing pain from a re-partitioned one.
    Greedy is incremental and stable; each run touches only new rows. Order-dependence
    is the price, and it's invisible at this scale.

    Invariants: INV-2 (never crosses verticals — the vertical is read off the row and
    bound into the query, never inferred), INV-7, sum(member_count) ==
    count(cluster_id IS NOT NULL), distinct_authors <= member_count.
    """
    counts: Counter[str] = Counter()

    async with get_session() as session:
        signals = list(
            (
                await session.execute(
                    select(EnrichedSignal)
                    .where(
                        EnrichedSignal.embedding.is_not(None),
                        EnrichedSignal.cluster_id.is_(None),
                        EnrichedSignal.vertical != "neither",
                    )
                    .order_by(EnrichedSignal.id)
                )
            ).scalars().all()
        )

        for signal in signals:
            emb = to_pgvector(list(signal.embedding))

            nearest = (
                await session.execute(
                    text(NEAREST_CENTROID_SQL),
                    # The vertical comes from the row being assigned. There is no
                    # branch on a vertical name anywhere in this function — that is
                    # what makes INV-2 a property of the query rather than of control
                    # flow someone can refactor around.
                    {"emb": emb, "vertical": signal.vertical},
                )
            ).first()

            if nearest is not None and nearest.sim >= settings.similarity_threshold:
                cluster_id = nearest.id
                counts["joined"] += 1
            else:
                cluster = Cluster(
                    vertical=signal.vertical,
                    label=signal.pain_point,
                    centroid=list(signal.embedding),
                    member_count=0,
                    distinct_authors=0,
                    first_seen_at=_EPOCH,
                    last_seen_at=_EPOCH,
                )
                session.add(cluster)
                # Needed before the FK write below; the recompute immediately
                # overwrites the placeholder timestamps with real member aggregates.
                await session.flush()
                cluster_id = cluster.id
                counts["created"] += 1

            await session.execute(
                update(EnrichedSignal)
                .where(EnrichedSignal.id == signal.id)
                .values(cluster_id=cluster_id)
            )
            # Must run inside the same transaction and after the membership write, or
            # the aggregate misses the row that just joined.
            await session.flush()
            await session.execute(
                text(RECOMPUTE_CLUSTER_SQL), {"cluster_id": cluster_id}
            )
            counts["assigned"] += 1

    log.info("assign_clusters: %s", dict(counts))
    return dict(counts)


async def recluster_all() -> None:
    """Full rebuild. MANUALLY INVOKED ONLY — never put this on a cron.

    ``TRUNCATE clusters; UPDATE enriched_signals SET cluster_id = NULL;`` then reassign
    everything in id order. Embeddings survive (they don't depend on the threshold), so
    this is free apart from compute. Expect to run it three or four times while tuning.

    Reassign across the whole table, not vertical-by-vertical — the WHERE clause
    already partitions, and an outer loop over verticals just adds a second place to
    get it wrong.

    Not registered in ``WorkerSettings.functions``. That omission is the safety
    mechanism: on a cron this would rewrite every cluster id every run, so the digest
    could never say "this cluster grew".
    """
    async with get_session() as session:
        # enriched_signals.cluster_id references clusters.id, so the FKs have to be
        # dropped before the truncate — hence CASCADE-free explicit ordering.
        await session.execute(update(EnrichedSignal).values(cluster_id=None))
        await session.execute(text("TRUNCATE clusters RESTART IDENTITY"))

    log.warning("recluster_all: cleared all clusters, reassigning from scratch")
    await assign_clusters()
