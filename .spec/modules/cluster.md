# Module — `cluster` (`ideas_mining/cluster.py`)

Status: **not implemented.** Source: [`specs/04-chunk3-cluster.md`](../../specs/04-chunk3-cluster.md).

**The highest-risk module in the system.** `INV-2` lives here as a single SQL clause, and its
violation is invisible downstream.

## Purpose
Embed `pain_point` text with Voyage, then group near-duplicates into `clusters` within a
vertical, maintaining `distinct_authors` as the demand proxy.

## Responsibilities
- Embed: batch ~128 texts, `input_type="document"`, selector
  `WHERE embedding IS NULL AND vertical <> 'neither'`.
- Assign: greedy incremental nearest-centroid, ordered by `id`, over
  `embedding IS NOT NULL AND cluster_id IS NULL AND vertical <> 'neither'`.
- Recompute the affected cluster's denormalized fields from its members after every
  assignment — never nudge the centroid incrementally `[SPECIFIED]`.
- A manually-invoked full re-cluster (truncate + null out + reassign in `id` order).

## Non-Responsibilities
- Does not split clusters — unsupported in v1 `[SPECIFIED]`.
- Does not merge (manual `UPDATE`).
- Does not touch `neither` rows: never embedded, never clustered, only archived.
- Does not score or rank.

## Inputs / Outputs
In: `enriched_signals.pain_point` + `vertical`; `EMBED_MODEL`; `SIMILARITY_THRESHOLD` (0.85
start). Out: `embedding`, `cluster_id`; rows in `clusters` with maintained
`centroid` / `member_count` / `distinct_authors` / `first_seen_at` / `last_seen_at`.

## State Machine
```
signal (vertical != 'neither')
   └─▶ embedding set ─▶ nearest centroid in SAME vertical
                          ├─ sim >= threshold ─▶ join cluster
                          └─ else            ─▶ create cluster (vertical = row.vertical)
```
Both steps idempotent by NULL-selector.

## Data Model
```sql
SELECT id, 1 - (centroid <=> :emb) AS sim
FROM clusters
WHERE vertical = :vertical          -- ← INV-2. Not optional.
ORDER BY centroid <=> :emb LIMIT 1;
```
hnsw + `vector_cosine_ops` (**not L2** — Voyage vectors are normalized for cosine, and L2
degrades silently rather than erroring) `[SPECIFIED]`.

## API Contract
Voyage `embed()`. Dimension must equal the migration's `vector(N)` — **`OQ-1`**.

## Invariants
- **`INV-2` — assignment never crosses verticals.** `SELECT DISTINCT vertical` over any
  cluster's members returns exactly one row.
- `INV-6` — embed `pain_point` only, and **never prefix the vertical**: a constant shift per
  vertical inflates intra-vertical similarity and blurs the threshold `[SPECIFIED]`.
- `INV-7` — `distinct_authors = count(DISTINCT rp.author)`.
- One threshold for both verticals `[SPECIFIED]` — a per-vertical dict makes the two
  verticals' frequencies incomparable and breaks scoring.
- `sum(member_count) == count(*) WHERE cluster_id IS NOT NULL`.
- `distinct_authors <= member_count`.

## Trust Boundaries
Consumes model-generated text (semi-trusted). No untrusted input reaches a query as SQL —
embeddings are parameterized.

## Failure Modes
- **The dropped `WHERE` clause.** Merges the two industries into one cluster whose
  `distinct_authors` is the *sum*, so it outranks every correct cluster and reaches the top
  of the digest. Nothing downstream detects it; only the digest reader does.
- Threshold too low → a "topic" cluster that looks like the best result and is worthless.
- Threshold too high → two clusters of 8 (recoverable, and the preferred direction).
- Greedy assignment is order-dependent — accepted at this scale `[SPECIFIED]`.
- Dimension mismatch → insert failure.

## Security
`VOYAGE_API_KEY` from env, never logged.

## Dependencies
`voyageai`, pgvector, SQLAlchemy.

## Testing Requirements
- **Write the partition test first `[SPECIFIED]`.** Two hand-written signals with
  near-identical `pain_point` differing only in domain noun, one `insurance` and one
  `real_estate` → must land in **two** clusters. Then assert the same code with `WHERE
  vertical` removed **merges** them — otherwise the test passes vacuously because the
  embeddings happened to be far apart.
- `SELECT DISTINCT vertical` per cluster returns exactly one row (standing check, re-run
  after every re-cluster).
- Every non-`neither` signal ends with non-NULL `embedding` and `cluster_id`; every
  `neither` row keeps both NULL.
- Re-running embed and assign is a no-op.
- Two paraphrases of one pain → one cluster. Two unrelated pains → two clusters.
- `sum(member_count)`, `distinct_authors <= member_count` hold.
- Both verticals have clusters.
- Top-5-per-vertical manual read (**manual — `OQ-6`**).

## Open Questions
`OQ-1` (blocking), `OQ-3` — the partition test is a SQL-semantics test and is meaningless
against a mocked DB; it needs real pgvector.
