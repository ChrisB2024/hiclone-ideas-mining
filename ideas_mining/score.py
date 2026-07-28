"""Cluster scoring. Pure SQL + Python, no LLM — cheap enough to re-run on every tweak.

All weights live in config.py. You WILL tune these.
"""

from __future__ import annotations


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
    """
    raise NotImplementedError("TODO")


async def score_clusters() -> int:
    """Recompute score + scored_at for every eligible cluster. Returns count scored.

    Eligibility: ``distinct_authors >= MIN_DISTINCT_AUTHORS``. Clusters below it are
    EXCLUDED, not scored zero — a single person's complaint is an anecdote. It may be a
    real pain, but it carries no demand signal, and frequency-as-proxy is the premise
    the whole system rests on.

    Recompute ALL clusters each run: recency changes even when membership doesn't.
    """
    raise NotImplementedError("TODO")


async def top_clusters(vertical: str, n: int) -> list[object]:
    """The ranking query. INV-9 lives here.

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
    raise NotImplementedError("TODO")
