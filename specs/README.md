# ideas-mining — Spec Plan

Mine pain points from public forums across two verticals — **insurance** and **real
estate** — cluster them, and email a weekly digest. The digest is the product. There is no
UI.

## Pipeline

```
Ingest → Filter → Enrich → Cluster → Score → Digest
(ARQ cron)  (regex)  (Haiku)  (Voyage+pgvector)  (SQL)  (Sonnet → email/md)
```

## The invariant everything hangs off

**One cluster = one distinct pain, in one vertical.**

A cluster is a *pain point*, not a topic and not a thread. Fifteen people describing
"re-keying the same client data into three quoting portals" is **one** cluster with
`frequency = 15`. Fifteen people talking about "quoting" in general is **not** a cluster —
it's a topic, and topics are worthless as outreach hooks.

Consequences that fall out of this and constrain every chunk:

- Embeddings are computed on `pain_point` text **only** — never the raw post. Raw post text
  carries subreddit register, story framing, and venting, all of which pull unrelated pains
  together in vector space.
- A post belongs to **exactly one** cluster. If a post describes two pains, the enrichment
  step must emit the dominant one; we do not multi-assign.
- **A cluster belongs to exactly one vertical, and cluster assignment never crosses
  verticals.** See below — this is the load-bearing part of the two-vertical change.
- `frequency` counts **distinct authors**, not posts. One person posting the same complaint
  in four subreddits is demand signal of 1.
- Merging two clusters is allowed; splitting is not supported in v1. So the similarity
  threshold should err **tight** (more, smaller clusters) rather than loose.

## Verticals

Exactly two, hardcoded: `insurance` and `real_estate`.

They share one pipeline, one schema, one enrichment prompt, and one digest. The only thing
that differs per vertical is a small config entry — subreddits, an HN query, and a keyword
list — plus the partition line in clustering and scoring.

### Why clusters must not span verticals

Insurance and real estate are *structurally similar businesses*: both are commission-driven
intermediaries with a CRM, a pipeline, a pile of documents, and a compliance step. So their
pain points are lexically near-identical while being commercially unrelated:

> "I re-key the same client details into three different portals to get quotes."
> "I re-key the same listing details into three different portals to syndicate."

Those two sentences embed very close together. Left unpartitioned they merge into one
cluster with `distinct_authors = 14` that scores top of the digest and describes a product
nobody wants — half the members are the wrong industry, so any outreach email off it is
wrong for half the leads. **This is the specific failure mode the vertical partition
exists to prevent**, and it's why the partition lives in the nearest-centroid query
(chunk 3) rather than being a display-time filter.

### How a post gets its vertical

Two steps, because sources aren't cleanly split:

1. **`vertical_hint`** — set at ingest from the subreddit config. `r/InsuranceAgent` →
   `insurance`, `r/realtors` → `real_estate`. Shared sources (`r/smallbusiness`, HN) get
   `NULL`.
2. **`vertical`** — decided at enrichment. Haiku returns `insurance` / `real_estate` /
   `neither` for every post, using `vertical_hint` as a prior, not as an answer. `neither`
   rows are stored and then excluded from clustering.

The hint alone isn't enough (shared sources have none, and people cross-post), and the LLM
call alone wastes the strong signal a dedicated subreddit gives you. Doing both costs one
extra enum field.

## Secondary invariants

- **Ingest is idempotent.** Rerunning any window produces zero new rows. Every external item
  has a stable `(source, external_id)`; upsert on it.
- **The LLM never sees a post twice.** Enrichment is keyed on `raw_post.id`; a post that has
  an `enriched_signal` row is never re-sent, even if the filter rules change.
- **Every stage is resumable.** Each stage reads rows in one state and writes rows in the
  next. Crash anywhere, rerun, no duplicates, no loss.

## Build order

| Chunk | Scope | Useful on its own? |
|---|---|---|
| 1 | Reddit + HN ingest, dedupe, schema | Yes — raw corpus you can grep |
| 2 | Cheap filter + Haiku enrichment | **Yes — this is the first real output.** Read filtered pains by hand |
| 3 | Voyage embeddings + pgvector clustering | Yes — frequency counts |
| 4 | Score + Sonnet digest + delivery | Yes — the product |

Chunks 1–2 are the milestone. If you stop there you already have something you can use.

## Non-goals (the escape hatches — do not build these)

- No dashboard, no web UI, no API.
- **No generic vertical framework.** Two verticals, hardcoded as two entries in one
  `VERTICALS` dict in `config.py`. No `verticals/` package, no per-vertical prompt files, no
  plugin registry, no DB table of verticals. A third vertical is a third dict entry — and if
  you find yourself wanting one, that's a signal to check whether the first two are actually
  producing outreach, not a signal to build the abstraction.
- No per-vertical thresholds, weights, or models. One similarity threshold, one scoring
  formula, one enrichment prompt, both verticals. If insurance needs 0.85 and real estate
  needs 0.80, you have a `pain_point` quality problem, not a config problem.
- No auth, no multi-user, no tenancy.
- No backfill of Reddit history. Forward-only from first run.
- No cluster splitting/merging UI. Bad clusters get fixed by tuning the threshold and
  re-clustering from scratch.

## Timebox

Two evenings. If chunk 3 is fighting you, ship chunks 1–2 and read the pains manually —
that path is explicitly supported by the schema.

## Stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12 | PRAW, Anthropic SDK, ARQ all live here |
| Scheduler | ARQ | Already chosen; Redis-backed cron |
| DB | Postgres 16 + `pgvector` | Need vector search and relational joins in one place |
| DB access | SQLAlchemy 2.0 async + asyncpg | Async to match ARQ |
| Migrations | Alembic | |
| Reddit | `asyncpraw` | Async, so it doesn't block the ARQ worker loop |
| HN | Algolia HN API (`hn.algolia.com/api/v1/search_by_date`) | Free, no key, supports time windows |
| LLM | `anthropic` SDK | |
| Embeddings | `voyageai` | |

## Models and costs

Locked to specific IDs — do not swap without re-reading `specs/03-chunk2-filter-enrich.md`.

| Stage | Model ID | Input $/MTok | Output $/MTok | Notes |
|---|---|---|---|---|
| Enrichment | `claude-haiku-4-5` | $1.00 | $5.00 | 200K context. Run through the Batch API → **50% off** |
| Digest | `claude-sonnet-5` | $3.00 | $15.00 | Intro pricing $2.00/$10.00 through 2026-08-31 |
| Embeddings | Voyage (see chunk 3) | — | — | Set the exact model in config; verify current ID at call time |

Back-of-envelope: ~900 posts/day across both verticals surviving the filter × ~600 input +
~150 output tokens each, on Haiku via Batch ≈ **$0.55/day**. The digest is one call a week
covering both verticals. Doubling the verticals roughly doubles the spend and it's still a
rounding error; do not optimize it.

## Layout

```
ideas_mining/
  config.py          # pydantic-settings; VERTICALS dict, thresholds, model IDs
  db/
    models.py        # SQLAlchemy models
    session.py
  ingest/
    reddit.py        # chunk 1
    hackernews.py    # chunk 1
  filter.py          # chunk 2
  enrich.py          # chunk 2
  cluster.py         # chunk 3
  score.py           # chunk 4
  digest.py          # chunk 4
  worker.py          # ARQ WorkerSettings + cron definitions
migrations/          # alembic
specs/               # this directory
tests/
```

## Spec index

1. [Data model](01-data-model.md) — write this first, everything else references it
2. [Chunk 1 — Ingest](02-chunk1-ingest.md)
3. [Chunk 2 — Filter + enrich](03-chunk2-filter-enrich.md)
4. [Chunk 3 — Cluster](04-chunk3-cluster.md)
5. [Chunk 4 — Score + digest](05-chunk4-digest.md)
