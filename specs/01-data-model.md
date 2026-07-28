# 01 — Data Model

Write this first. Every other chunk is a function over these tables.

Four tables, one pipeline direction:

```
raw_posts ──(filter)──▶ raw_posts.filter_state
    │
    └──(Haiku)──▶ enriched_signals ──(Voyage+pgvector)──▶ clusters
                                                              │
                                                       (Sonnet)└──▶ digests
```

## Extensions

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

---

## `raw_posts`

Immutable record of what was on the internet. Never edited after insert except
`filter_state`.

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` PK | Internal |
| `source` | `text` NOT NULL | `reddit` \| `hackernews` |
| `external_id` | `text` NOT NULL | Reddit fullname (`t3_abc123` / `t1_def456`), HN object ID |
| `parent_external_id` | `text` NULL | Set on comments; points at the submission |
| `subsource` | `text` NULL | Subreddit name, or `hn` |
| `vertical_hint` | `text` NULL | `insurance` \| `real_estate`, from the subreddit config. `NULL` for shared sources (`r/smallbusiness`, HN). **A hint, not the answer** |
| `url` | `text` NOT NULL | Permalink — goes in the digest, must be clickable |
| `author` | `text` NULL | `null` for deleted. **The dedupe key for frequency counting** |
| `title` | `text` NULL | Submissions only |
| `body` | `text` NOT NULL | Selftext or comment body. `''` for link-only posts |
| `score` | `int` NOT NULL | Upvotes / HN points at fetch time. Not refreshed |
| `created_at` | `timestamptz` NOT NULL | **Original post time**, not fetch time |
| `fetched_at` | `timestamptz` NOT NULL DEFAULT `now()` | |
| `filter_state` | `text` NOT NULL DEFAULT `'pending'` | `pending` \| `passed` \| `rejected` |
| `filter_reason` | `text` NULL | Why rejected — for tuning the regex without re-fetching |

```sql
UNIQUE (source, external_id)              -- the idempotency contract
INDEX  (filter_state) WHERE filter_state = 'passed'
INDEX  (created_at DESC)
```

**`author` matters more than it looks.** It's how `frequency` counts distinct people
instead of distinct posts. Store it even though it feels like metadata.

**`created_at` is the post's time, not ours.** Recency scoring uses it. Getting this wrong
makes every cluster look equally fresh.

---

## `enriched_signals`

One row per post that survived the filter and got a Haiku call. Exactly the JSON shape the
model returns, plus bookkeeping.

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` PK | |
| `raw_post_id` | `bigint` NOT NULL UNIQUE → `raw_posts.id` | **UNIQUE is the "never enrich twice" guarantee** |
| `vertical` | `text` NOT NULL | `insurance` \| `real_estate` \| `neither`. Decided by the model |
| `pain_point` | `text` NOT NULL | One sentence, normalized. **The only text that gets embedded** |
| `who_has_it` | `text` NOT NULL | e.g. "independent P&C agent", "residential listing agent" |
| `current_workaround` | `text` NULL | What they do today. `null` when the post doesn't say |
| `willingness_to_pay_signal` | `text` NOT NULL | `none` \| `implied` \| `explicit` — see enum note |
| `buildable_with_llm` | `bool` NOT NULL | Could an LLM app plausibly solve this |
| `relevance` | `smallint` NOT NULL | 0–10, **relative to `vertical`**. Always 0 when `vertical = 'neither'` |
| `model` | `text` NOT NULL | Model ID used. Lets you re-run a subset when you change models |
| `enriched_at` | `timestamptz` NOT NULL DEFAULT `now()` | |
| `embedding` | `vector(N)` NULL | N = Voyage model dim. NULL until chunk 3 runs |
| `cluster_id` | `bigint` NULL → `clusters.id` | NULL until clustered |

```sql
UNIQUE (raw_post_id)
INDEX  (cluster_id)
INDEX  (vertical, relevance DESC)
-- chunk 3 adds the hnsw index on embedding
```

`vertical` is a **3-value enum including `neither`**, not a 2-value one. Shared sources
(`r/smallbusiness`, HN) send plenty of posts that pass the pain filter and are about
neither industry — "my Shopify inventory sync is manual" is a real pain, correctly
extracted, and useless here. Forcing a binary choice makes the model guess, and those
guesses land in real clusters. `neither` rows are written (they're already paid for, and
their volume tells you whether the filter needs tightening) and then excluded at the
clustering step.

`relevance` is scored **against the row's own `vertical`**, not against insurance — a
strong real-estate pain scores 9, not 0. Rows with `vertical = 'neither'` always get 0.

`willingness_to_pay_signal` is a **3-value string enum**, not a bool and not a float:

- `none` — describes a problem, no purchase intent
- `implied` — "there has to be a better way", "we're evaluating vendors"
- `explicit` — "I'd pay for this", "we budget $X", "we already pay Y for this"

Three levels because it's the highest-signal field in the score and a bool throws away the
distinction that matters (`explicit` is worth far more than `implied`). More than three and
the model's assignments stop being consistent.

`buildable_with_llm` is a bool because it's genuinely binary and it's a **filter**, not a
weight — non-buildable pains are real pains you can't act on.

**Nullable `embedding` and `cluster_id` are deliberate.** They're what makes chunks 1–2 a
shippable milestone: the table is fully useful with both NULL.

---

## `clusters`

One row per distinct pain. This is the table the invariant is about.

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` PK | |
| `vertical` | `text` NOT NULL | `insurance` \| `real_estate`. Never `neither` — those aren't clustered |
| `label` | `text` NOT NULL | Human-readable pain name. Seeded from the first member's `pain_point` |
| `centroid` | `vector(N)` NOT NULL | Mean of member embeddings. Recomputed on every join |
| `member_count` | `int` NOT NULL DEFAULT 0 | Denormalized post count |
| `distinct_authors` | `int` NOT NULL DEFAULT 0 | **This is `frequency`.** Denormalized |
| `first_seen_at` | `timestamptz` NOT NULL | Min member `raw_posts.created_at` |
| `last_seen_at` | `timestamptz` NOT NULL | Max member `raw_posts.created_at`. Drives recency |
| `score` | `double precision` NULL | Written by chunk 4 |
| `scored_at` | `timestamptz` NULL | |

```sql
INDEX (vertical, score DESC NULLS LAST)
```

The index leads on `vertical` because **every** read of this table is
vertical-scoped — the nearest-centroid search in chunk 3, the ranking query in chunk 4,
and every manual inspection query. There is no query in the system that wants clusters
from both verticals at once.

Denormalize `member_count` / `distinct_authors` / `last_seen_at` rather than aggregating at
digest time — the digest query stays a plain `ORDER BY score DESC LIMIT 5`, and these are
cheap to maintain because assignment is incremental (chunk 3).

---

## `digests`

Archive of what you sent yourself. Cheap, and the only way to answer "did I already see
this idea three weeks ago".

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` PK | |
| `generated_at` | `timestamptz` NOT NULL DEFAULT `now()` | |
| `period_start` | `timestamptz` NOT NULL | |
| `period_end` | `timestamptz` NOT NULL | |
| `cluster_ids` | `bigint[]` NOT NULL | Which clusters made the cut — insurance in rank order, then real estate in rank order |
| `markdown` | `text` NOT NULL | Rendered digest, exactly as delivered |
| `delivered` | `bool` NOT NULL DEFAULT false | Set after successful send |

---

## Scaffold

`ideas_mining/db/models.py` — SQLAlchemy 2.0 declarative, `Mapped[]` annotations,
`pgvector.sqlalchemy.Vector` for the vector columns. Then:

```
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

Autogenerate does **not** emit `CREATE EXTENSION vector` — hand-write it as the first
operation in the initial migration's `upgrade()`.

## Acceptance

- [ ] `alembic upgrade head` on an empty DB creates all four tables + the vector extension
- [ ] `alembic downgrade base` then `upgrade head` is clean
- [ ] Inserting two rows with the same `(source, external_id)` raises `IntegrityError`
- [ ] Inserting two `enriched_signals` for one `raw_post_id` raises `IntegrityError`
- [ ] A `raw_post` can be inserted with only ingest fields; every enrichment/cluster column
      is nullable or defaulted
- [ ] A `raw_post` from a shared source inserts fine with `vertical_hint = NULL`
- [ ] `enriched_signals.vertical` and `clusters.vertical` accept only their allowed values
      (CHECK constraint or SQLAlchemy `Enum` — either, but enforce it in the DB, not just
      in Python)
