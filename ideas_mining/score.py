"""Cluster scoring. Pure SQL + Python, no LLM — cheap enough to re-run on every tweak.

All weights live in config.py. You WILL tune these.

Note on imports: the DB modules are pulled in with plain ``import x.y.z`` rather than
``from x.y import z``, for the reason documented at the top of filter.py.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from sqlalchemy import text, update

import ideas_mining.db.models as db_models
import ideas_mining.db.session as db_session
from ideas_mining.config import (
    MIN_DISTINCT_AUTHORS, RECENCY_HALFLIFE_DAYS, WTP_EXPLICIT_WEIGHT,
    WTP_IMPLIED_WEIGHT,
)

log = logging.getLogger(__name__)

#: One pass over every cluster's members. `FILTER (WHERE …)` is Postgres' conditional
#: aggregate — it counts only matching rows, so each ratio is a single scan rather than
#: one subquery per term.
CLUSTER_STATS_SQL = """
SELECT c.id                                       AS cluster_id,
       c.distinct_authors                         AS distinct_authors,
       EXTRACT(EPOCH FROM (now() - c.last_seen_at)) / 86400.0
                                                  AS days_since_last_seen,
       count(*) FILTER (WHERE es.willingness_to_pay_signal = 'explicit')::float
         / NULLIF(count(*), 0)                    AS explicit_ratio,
       count(*) FILTER (WHERE es.willingness_to_pay_signal = 'implied')::float
         / NULLIF(count(*), 0)                    AS implied_ratio,
       count(*) FILTER (WHERE es.buildable_with_llm)::float
         / NULLIF(count(*), 0)                    AS buildable_ratio,
       avg(es.relevance)::float                   AS avg_relevance
FROM clusters c
JOIN enriched_signals es ON es.cluster_id = c.id
WHERE c.distinct_authors >= :min_authors
GROUP BY c.id
"""

#: INV-9 in query form. Vertical is bound, never interpolated, and there is no code path
#: that ranks without it.
TOP_CLUSTERS_SQL = """
SELECT * FROM clusters
WHERE vertical = :vertical AND distinct_authors >= :min_authors AND score IS NOT NULL
ORDER BY score DESC
LIMIT :n
"""


def compute_score(
    *,
    distinct_authors: int,
    days_since_last_seen: float,
    explicit_ratio: float,
    implied_ratio: float,
    buildable_ratio: float,
    avg_relevance: float,
) -> float:
    """Score one cluster.

        score = ln(1 + distinct_authors)                    frequency
              * exp(-days_since_last_seen / HALFLIFE)       recency
              * (1 + 2*explicit_ratio + 0.5*implied_ratio)  willingness to pay
              * buildable_ratio                             buildability
              * avg_relevance / 10                          relevance

    Two design points a "fix" would break:

    * **Multiplicative throughout**, so any single dimension can veto. Nothing
      buildable, nothing relevant, and nothing stale reaches the top on volume alone.
      buildable_ratio == 0 giving score 0 is intended, not an edge case.
    * **WTP uses ratios of members, not counts.** Counts would double-count frequency,
      which is already its own term.

    Frequency is logged because 20 people isn't 20x more interesting than 1 — but it is
    meaningfully more than 5.

    Args are ratios in [0, 1] except distinct_authors (int) and avg_relevance (0-10).

    Returns:
        A non-negative float. Comparable only against other clusters in the SAME
        vertical — see ``top_clusters``.
    """
    frequency = math.log1p(distinct_authors)
    recency = math.exp(-days_since_last_seen / RECENCY_HALFLIFE_DAYS)
    wtp = 1.0 + WTP_EXPLICIT_WEIGHT * explicit_ratio + WTP_IMPLIED_WEIGHT * implied_ratio
    relevance = avg_relevance / 10.0

    return frequency * recency * wtp * buildable_ratio * relevance


async def score_clusters(ctx: dict[str, object] | None = None) -> int:
    """Recompute score + scored_at for every eligible cluster. Returns count scored.

    Inputs:
        ctx: ARQ job context, unused (FINDING-1.6).

    Returns:
        Number of clusters given a score this run.

    Eligibility: ``distinct_authors >= MIN_DISTINCT_AUTHORS``. Clusters below it are
    EXCLUDED, not scored zero — a single person's complaint is an anecdote. It may be a
    real pain, but it carries no demand signal, and frequency-as-proxy is the premise
    the whole system rests on.

    Recompute ALL eligible clusters each run: recency changes even when membership
    doesn't, so last week's scores are stale by definition.
    """
    scored = 0

    async with db_session.get_session() as session:
        rows = (
            await session.execute(
                text(CLUSTER_STATS_SQL), {"min_authors": MIN_DISTINCT_AUTHORS}
            )
        ).mappings().all()

        for row in rows:
            value = compute_score(
                distinct_authors=row["distinct_authors"],
                days_since_last_seen=row["days_since_last_seen"],
                explicit_ratio=row["explicit_ratio"] or 0.0,
                implied_ratio=row["implied_ratio"] or 0.0,
                buildable_ratio=row["buildable_ratio"] or 0.0,
                avg_relevance=row["avg_relevance"] or 0.0,
            )
            await session.execute(
                update(db_models.Cluster)
                .where(db_models.Cluster.id == row["cluster_id"])
                .values(score=value, scored_at=text("now()"))
            )
            scored += 1

    log.info("scored %d clusters", scored)
    return scored


async def top_clusters(vertical: str, n: int) -> list[Any]:
    """The ranking query. INV-9 lives here.

    Inputs:
        vertical: which vertical to rank within. Bound as a parameter.
        n: how many to return.

    Returns:
        Cluster rows, highest score first. May be shorter than ``n`` — that is a real
        result, not an error.

        SELECT * FROM clusters
        WHERE vertical = :vertical AND distinct_authors >= :min
        ORDER BY score DESC LIMIT :n

    **Scores are only ever compared within a vertical.** They are not comparable
    across verticals and must not be made so — no normalization, no weighting one up to
    compensate.

    Insurance will almost certainly out-volume real estate (more dedicated subreddits,
    more B2B software talk). A single global top-5 would be all insurance most weeks,
    and the real-estate pipeline would silently produce nothing you ever read. Top N
    per vertical is what makes running two verticals mean anything.

    If a vertical genuinely has nothing worth reading this week, the right outcome is a
    thin section — not a padded one.
    """
    async with db_session.get_session() as session:
        result = await session.execute(
            text(TOP_CLUSTERS_SQL),
            {"vertical": vertical, "min_authors": MIN_DISTINCT_AUTHORS, "n": n},
        )
        return list(result.mappings().all())
