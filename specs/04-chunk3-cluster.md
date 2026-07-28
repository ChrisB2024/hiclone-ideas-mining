# 04 — Chunk 3: Embed + Cluster

Turn `enriched_signals.pain_point` into vectors, group near-duplicates into `clusters`
**within a vertical**, maintain `distinct_authors` as the demand proxy.

**Done when:** you can point at a cluster with `distinct_authors = 12` and every one of the
12 posts is genuinely the same complaint, from the same industry.

---

## Embeddings

Voyage, via the `voyageai` client. Set the exact model in config as `EMBED_MODEL` and
**verify the current model ID and its dimension at the Voyage docs when you wire this up** —
don't hardcode a dimension you assumed. The `vector(N)` column in the migration must match
whatever you pick; changing it later means re-embedding everything.

```python
result = voyage.embed(
    texts=[s.pain_point for s in batch],
    model=settings.EMBED_MODEL,
    input_type="document",
)
```

- Embed `pain_point` **only**. Not the title, not the body, not `who_has_it`, and **not the
  vertical**. This is the invariant from `README.md` in code form — mixing in raw text
  reintroduces the story framing that the enrichment step just stripped out.
- Batch ~128 texts per call.
- Write `embedding` back keyed on `enriched_signals.id`.
- Selector: `WHERE embedding IS NULL AND vertical <> 'neither'`. Idempotent, resumable, same
  shape as everything else. `neither` rows never get embedded — they're archived, not
  processed.

**Do not prepend the vertical to the embedded text.** Prefixing `"insurance: "` onto every
insurance pain is the obvious-looking way to separate the two industries in vector space,
and it's the wrong one: it shifts every vector in a vertical by the same constant, which
inflates the similarity between *unrelated* pains inside that vertical and blurs the
threshold you spend chunk 3 tuning. The separation belongs in the query filter below, where
it's exact and costs nothing.

### Index

Add in a migration once you have a few thousand rows (an index built on an empty table has
useless statistics):

```sql
CREATE INDEX ON enriched_signals
  USING hnsw (embedding vector_cosine_ops);

CREATE INDEX ON clusters
  USING hnsw (centroid vector_cosine_ops);
```

**`vector_cosine_ops`, not L2.** Voyage embeddings are normalized for cosine similarity;
using the L2 operator class silently gives you worse neighbors rather than an error.

At this scale (hundreds to low thousands of clusters) the `clusters` index is optional —
a sequential scan over centroids is fast. Add it if assignment gets slow; skip it otherwise.
Note that an hnsw index and a `WHERE vertical = :v` filter interact badly in general (the
index is searched first, then filtered, so a selective filter can return fewer rows than
`LIMIT` asked for). It doesn't bite here because the filter keeps ~half the rows, but it's
the reason not to reach for a partial-index-per-vertical scheme as a "fix" later.

---

## Clustering

Greedy incremental nearest-centroid assignment. Not k-means, not DBSCAN.

For each `enriched_signals` row with `embedding IS NOT NULL AND cluster_id IS NULL AND
vertical <> 'neither'`, ordered by `id`:

1. Find the nearest cluster centroid **in the same vertical**:
   ```sql
   SELECT id, 1 - (centroid <=> :emb) AS sim
   FROM clusters
   WHERE vertical = :vertical          -- ← the partition. Not optional.
   ORDER BY centroid <=> :emb LIMIT 1;
   ```
   (`<=>` is cosine distance; `1 - distance` is similarity.)
2. If `sim >= SIMILARITY_THRESHOLD` → assign to that cluster.
3. Else → create a new cluster with `vertical = row.vertical`, `label = pain_point`,
   `centroid = embedding`.
4. Update the cluster's denormalized fields (below).

**That `WHERE vertical = :vertical` is the single most important line in this chunk.**
Without it, structurally-identical pains from the two industries merge into one
high-frequency cluster that tops the digest and is unusable for outreach — the failure mode
spelled out in `README.md`. It's one clause, it's easy to drop during a refactor, and
nothing else in the system will catch it: the resulting cluster looks *better* than the
real ones because its `distinct_authors` is the sum of both. Write the test in the
acceptance list below before you write the query.

```python
SIMILARITY_THRESHOLD = 0.85   # start here, tune down toward 0.80 / up toward 0.90
```

### Why greedy, not k-means

k-means needs `k` up front, and `k` is the answer we're looking for. It also reshuffles
every assignment on each run, so `frequency` counts churn between digests and you can't
tell a growing pain from a re-partitioned one. Greedy assignment is incremental, stable, and
each run only touches new rows. Order-dependence is the price; at this scale it's invisible.

**Err tight.** Splitting a cluster isn't supported (see `README.md`); merging is a manual
`UPDATE`. A too-high threshold gives you two clusters of 8 that you can spot and merge. A
too-low threshold gives you one cluster of 16 labelled "quoting is annoying" — a topic, not
a pain — and it looks like your best result while being worthless.

**One threshold for both verticals**, per the non-goals. Tune it against whichever vertical
has more data and accept the other. If the two genuinely need different values, the real
problem is inconsistent `pain_point` phrasing in one of them — fix chunk 2's prompt, not
this constant. A per-vertical threshold dict is the first brick in the framework you're
trying not to build, and it makes the two verticals' `frequency` numbers incomparable,
which breaks scoring in chunk 4.

### Maintaining the denormalized fields

After each assignment, for the affected cluster:

```sql
UPDATE clusters c SET
  centroid = sub.centroid,
  member_count = sub.n,
  distinct_authors = sub.authors,
  first_seen_at = sub.first_seen,
  last_seen_at = sub.last_seen
FROM (
  SELECT AVG(es.embedding)              AS centroid,
         count(*)                        AS n,
         count(DISTINCT rp.author)       AS authors,
         min(rp.created_at)              AS first_seen,
         max(rp.created_at)              AS last_seen
  FROM enriched_signals es
  JOIN raw_posts rp ON rp.id = es.raw_post_id
  WHERE es.cluster_id = :cluster_id
) sub
WHERE c.id = :cluster_id;
```

Recompute from members rather than incrementally nudging the centroid — it's one cheap
query per assignment and it can't drift out of sync.

`count(DISTINCT rp.author)` is the whole game: **`distinct_authors` is `frequency`**, and
`frequency` is the demand proxy. One person cross-posting to four subreddits contributes 1.
`author IS NULL` rows were rejected back in the filter, so no NULL-collapsing to worry
about.

---

## Re-clustering

Threshold changes require a full rebuild. Make this an explicit, manually-invoked job —
never a cron:

```
TRUNCATE clusters;
UPDATE enriched_signals SET cluster_id = NULL;
-- then run assignment over everything, ordered by id
```

Embeddings survive (they don't depend on the threshold), so this is free apart from
compute. Expect to run it three or four times while tuning.

Re-assign in `id` order across the whole table, not vertical-by-vertical. The `WHERE
vertical = :vertical` clause already does the partitioning; looping over verticals in the
outer scope just gives you two places to get it wrong.

## Wiring

```python
cron_jobs = [
    ...,
    cron(embed_pending,   minute={20, 50}),
    cron(assign_clusters, minute={25, 55}),
]
```

## Acceptance

- [ ] Every `enriched_signals` row with `vertical <> 'neither'` ends with a non-NULL
      `embedding` and `cluster_id`
- [ ] Every `vertical = 'neither'` row still has `embedding IS NULL` and
      `cluster_id IS NULL`
- [ ] Re-running embed and assign is a no-op (nothing selected)
- [ ] `sum(member_count)` across clusters == `count(*)` from `enriched_signals` where
      `cluster_id IS NOT NULL`
- [ ] `distinct_authors <= member_count` for every cluster
- [ ] Deliberately insert two paraphrases of the same pain → they land in one cluster
- [ ] Insert two clearly unrelated pains → two clusters
- [ ] **The partition test — write this one first.** Insert two hand-written signals with
      near-identical `pain_point` text differing only in domain noun ("re-key client
      details into three quoting portals" / "re-key listing details into three syndication
      portals"), one tagged `insurance` and one `real_estate`. Assert they land in **two**
      clusters. Then assert the naive query (same code, `WHERE vertical` removed) merges
      them — so you know the test actually exercises the partition and isn't passing because
      the embeddings happened to be far apart
- [ ] `SELECT DISTINCT es.vertical FROM enriched_signals es WHERE es.cluster_id = X` returns
      exactly one row, for every cluster — a standing invariant check worth running after
      every re-cluster
- [ ] Both verticals have clusters; neither is empty
- [ ] **Manual read of the top 5 clusters by `member_count` in each vertical.** Open every
      member's `url`. If any cluster contains two genuinely different problems, raise the
      threshold and re-cluster. Do not proceed to chunk 4 until the biggest clusters in
      **both** verticals are clean — the digest is only as good as this
