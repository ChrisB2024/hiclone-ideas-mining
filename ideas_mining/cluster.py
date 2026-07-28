"""Embed + cluster — the highest-risk module in the system.

INV-2 lives here as a single SQL clause. Read .spec/modules/cluster.md before touching
the assignment query.
"""

from __future__ import annotations

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
NEAREST_CENTROID_SQL = """
SELECT id, 1 - (centroid <=> :emb) AS sim
FROM clusters
WHERE vertical = :vertical
ORDER BY centroid <=> :emb
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


async def embed_pending(batch_size: int = 128) -> int:
    """Embed pain_point text for signals that don't have a vector yet.

    Selector: ``WHERE embedding IS NULL AND vertical <> 'neither'``. Idempotent and
    resumable. 'neither' rows are archived, never embedded.

    Invariant INV-6 — embed ``pain_point`` ONLY. Not the title, not the body, not
    who_has_it, and **not a vertical prefix**. Prefixing "insurance: " onto every
    insurance pain is the obvious-looking way to separate the industries in vector
    space and is wrong: it shifts every vector in a vertical by the same constant,
    which inflates similarity between UNRELATED pains inside that vertical and blurs
    the threshold. The separation belongs in the query filter, where it's exact.

    Call: ``voyage.embed(texts=[...], model=settings.embed_model,
    input_type="document")``, ~128 per call.

    BLOCKED on OQ-1: settings.embedding_dim must match the model's real dimension.
    """
    raise NotImplementedError("TODO")


async def assign_clusters() -> dict[str, int]:
    """Greedy incremental nearest-centroid assignment, within a vertical.

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

    Invariants: INV-2 (never crosses verticals), INV-7, sum(member_count) ==
    count(cluster_id IS NOT NULL), distinct_authors <= member_count.
    """
    raise NotImplementedError("TODO")


async def recluster_all() -> None:
    """Full rebuild. MANUALLY INVOKED ONLY — never put this on a cron.

    ``TRUNCATE clusters; UPDATE enriched_signals SET cluster_id = NULL;`` then reassign
    everything in id order. Embeddings survive (they don't depend on the threshold), so
    this is free apart from compute. Expect to run it three or four times while tuning.

    Reassign across the whole table, not vertical-by-vertical — the WHERE clause
    already partitions, and an outer loop over verticals just adds a second place to
    get it wrong.
    """
    raise NotImplementedError("TODO")
