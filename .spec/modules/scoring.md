# Module — `score` (`ideas_mining/score.py`)

Status: **not implemented.** Source: [`specs/05-chunk4-digest.md`](../../specs/05-chunk4-digest.md).

## Purpose
Rank clusters within their vertical. Pure SQL + Python, no LLM — scoring must be cheap
enough to re-run on every weight tweak.

## Responsibilities
- Compute `score` for every cluster with `distinct_authors >= 2`.
- Write `score` and `scored_at` back to `clusters`.
- Recompute **all** clusters each run — recency changes even when membership doesn't
  `[SPECIFIED]`.

## Non-Responsibilities
- Does not select the digest's clusters (that's `digest`, using the ranking query).
- Does not normalize or reconcile scores across verticals — explicitly forbidden `[SPECIFIED]`.
- Does not mutate cluster membership.

## Inputs / Outputs
In: `clusters` + joined `enriched_signals`/`raw_posts`; weight constants from config.
Out: `clusters.score`, `clusters.scored_at`.

## State Machine
Stateless, fully recomputed each run. Idempotent given identical inputs and clock.

## Data Model
```
score = ln(1 + distinct_authors)
      × exp(-days_since(last_seen_at) / 14)
      × (1 + 2·explicit_ratio + 0.5·implied_ratio)
      × buildable_ratio
      × avg(relevance) / 10
```

Ranking query — **the vertical filter is part of the contract** `[SPECIFIED]`:
```sql
SELECT * FROM clusters
WHERE vertical = :vertical AND distinct_authors >= 2
ORDER BY score DESC LIMIT :n;
```

Rationale per term is in `specs/05`. Two design points a test must not "fix":
- **Multiplicative**, so any single dimension can veto.
- **WTP uses ratios, not counts** — counts would double-count frequency.

Every weight lives in `config.py` as a named constant `[SPECIFIED]`.

## API Contract
`[INFERRED — NEEDS HUMAN CONFIRMATION]` `score_clusters()` coroutine; a pure
`compute_score(...) -> float` for direct testing. The pure split is inferred.

## Invariants
- `INV-9` — **scores are only ever compared within a vertical.**
- `buildable_ratio == 0` ⇒ `score == 0`. Multiplicative veto, by design.
- Clusters with `distinct_authors < 2` are excluded, not scored zero — an anecdote carries
  no demand signal `[SPECIFIED]`.

## Trust Boundaries
Reads only model-derived and DB-internal values. No untrusted input, no network.

## Failure Modes
- **`INV-9` is violated by omission, not by error** — a global `ORDER BY score` runs fine and
  produces an all-insurance digest, silently starving the real-estate pipeline.
- Recency uses `last_seen_at`, which is derived from `raw_posts.created_at` — a naive
  datetime written at ingest drifts the whole ranking.
- Division by zero if a cluster has zero members (shouldn't occur; assert rather than guard).

## Security
None. No secrets, no I/O beyond the DB.

## Dependencies
SQLAlchemy, stdlib `math`.

## Testing Requirements
- Every cluster with `distinct_authors >= 2` gets a non-NULL `score`.
- `buildable_ratio = 0` ⇒ score 0.
- Within a vertical: a 12-author cluster from last week outranks a 3-author cluster from two
  months ago.
- Clusters with `distinct_authors < 2` are absent from the ranking, not present with score 0.
- **The ranking query returns only the requested vertical** — construct a fixture where a
  cross-vertical cluster would outrank everything, and assert it doesn't appear.
- Each term is individually monotonic (hold four fixed, vary one).

## Open Questions
None specific. `OQ-3` applies if the score is expressed in SQL rather than Python.
