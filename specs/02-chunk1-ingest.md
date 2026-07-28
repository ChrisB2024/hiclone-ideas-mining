# 02 — Chunk 1: Ingest

Pull new posts and top comments into `raw_posts`. No LLM, no filtering, no judgement.

**Done when:** the cron has run twice and the second run inserts zero rows for the
overlapping window.

## Config

`ideas_mining/config.py`, pydantic-settings, env-overridable. **This dict is the entire
"multi-vertical" machinery — there is no other file that knows a vertical exists by name.**

```python
VERTICALS = {
    "insurance": {
        "subreddits": ["Insurance", "InsuranceAgent", "InsurancePros"],
        "hn_query": "insurance agency OR insurance broker OR underwriting",
        # used by the filter to gate shared sources — chunk 2
        "keywords": [
            "insurance", "insurer", "underwrit", "policyholder", "premium",
            "carrier", "broker", "agency management system", "AMS360", "Applied Epic",
            "EZLynx", "quoting", "certificate of insurance", "COI", "ACORD",
            "claims", "adjuster", "renewal", "commission", "E&O", "P&C",
        ],
    },
    "real_estate": {
        "subreddits": [
            "realtors", "RealEstate", "realestateinvesting",
            "CommercialRealEstate", "PropertyManagement",
        ],
        "hn_query": "real estate agent OR brokerage OR property management",
        "keywords": [
            "realtor", "real estate", "brokerage", "listing", "MLS", "escrow",
            "closing", "title company", "showing", "open house", "comps", "CMA",
            "transaction coordinator", "under contract", "buyer agent",
            "listing agent", "property manager", "tenant", "lease", "landlord",
            "Zillow", "Follow Up Boss", "kvCORE", "DocuSign", "appraisal",
        ],
    },
}

# Sources that carry no vertical signal on their own. vertical_hint = NULL;
# the filter gates them on keywords, the model decides the vertical.
SHARED_SUBREDDITS = ["smallbusiness", "Entrepreneur"]

INGEST_INTERVAL_HOURS = 6
INGEST_LOOKBACK_HOURS = 12      # > interval, deliberately — see below
POSTS_PER_SUBREDDIT = 100
COMMENTS_PER_POST = 10          # top-level only, sorted by score
```

Two notes on the real-estate list:

- **`r/RealEstate` is mostly consumers** — people buying their first house, not agents with
  operational pain. It's in the list because the agents who answer those questions do
  complain in the comments, and comments are ingested. Expect a low pass rate and a lot of
  `neither`; if it's still noise after a week, drop it. `r/realtors` and
  `r/PropertyManagement` are the high-yield ones.
- **`r/CommercialRealEstate` and `r/PropertyManagement` are different businesses** from
  residential brokerage, with different software and different pains. They stay in one
  vertical anyway — clustering will separate them naturally, because their `pain_point`
  text doesn't overlap. Splitting them into a third and fourth vertical is exactly the
  escape hatch the non-goals list forbids.

The keyword lists are deliberately concrete (product names, acronyms, workflow nouns).
Generic words like "client" or "software" appear in both verticals and gate nothing.

**`LOOKBACK > INTERVAL` is intentional.** A 6h cron fetching a 12h window means a missed or
slow run self-heals on the next tick. The overlap costs nothing because upserts are
idempotent — that's the whole point of building dedupe first.

## Reddit — `ingest/reddit.py`

`asyncpraw` in read-only mode (client id + secret, no user credentials needed).

Build the work list by flattening the config once:

```python
targets = [
    (sub, vertical)
    for vertical, cfg in VERTICALS.items()
    for sub in cfg["subreddits"]
] + [(sub, None) for sub in SHARED_SUBREDDITS]
```

The second element becomes `vertical_hint` on every row from that subreddit — submissions
**and** their comments. A comment inherits its submission's hint; don't recompute it.

For each subreddit:
1. `subreddit.new(limit=POSTS_PER_SUBREDDIT)`
2. Skip submissions older than the lookback window (they're already stored)
3. Upsert the submission
4. `submission.comments.replace_more(limit=0)` — do **not** expand "load more" chains; the
   long tail is low-signal and expensive
5. Take the top `COMMENTS_PER_POST` top-level comments by score, upsert each with
   `parent_external_id` set to the submission's fullname

Field mapping:

| `raw_posts` | Submission | Comment |
|---|---|---|
| `external_id` | `submission.fullname` (`t3_…`) | `comment.fullname` (`t1_…`) |
| `parent_external_id` | `NULL` | `submission.fullname` |
| `subsource` | `str(submission.subreddit)` | same |
| `vertical_hint` | from `targets` | same as its submission |
| `url` | `https://reddit.com` + `permalink` | same |
| `author` | `str(submission.author)` or `NULL` | same |
| `title` | `submission.title` | `NULL` |
| `body` | `submission.selftext` | `comment.body` |
| `score` | `submission.score` | `comment.score` |
| `created_at` | `datetime.fromtimestamp(created_utc, tz=UTC)` | same |

Two traps:

- **`author` is `None` for deleted accounts** and `str(None)` gives you the string
  `"None"`, which then looks like one very prolific user in the frequency count. Check for
  `None` before stringifying.
- **`created_utc` is a naive float.** Always attach `tz=UTC`. A naive datetime in a
  `timestamptz` column silently gets interpreted as server-local time and your recency
  scoring drifts by hours.

## Hacker News — `ingest/hackernews.py`

Algolia HN API, no key, plain `httpx`. **One pass per vertical**, using that vertical's
`hn_query`:

```
GET https://hn.algolia.com/api/v1/search_by_date
    ?query={VERTICALS[v]["hn_query"]}
    &tags=(story,comment)
    &numericFilters=created_at_i>{unix_ts_of_window_start}
    &hitsPerPage=100
```

Paginate on `page` until `page >= nbPages` or you've seen the whole window.

**Set `vertical_hint = NULL` on HN rows regardless of which query found them.** The query
is a retrieval device, not a classification: `"insurance agency"` will surface a thread
about health-insurance startups, and the two queries will both return the same PropTech
thread. Let the model decide at enrichment. This also means the same `objectID` can come
back from both passes — the `(source, external_id)` upsert absorbs it, which is exactly
what that constraint is for.

| `raw_posts` | HN hit |
|---|---|
| `source` | `"hackernews"` |
| `external_id` | `hit["objectID"]` |
| `parent_external_id` | `hit.get("story_id")` for comments |
| `subsource` | `"hn"` |
| `vertical_hint` | `NULL` — always |
| `url` | `https://news.ycombinator.com/item?id={objectID}` |
| `author` | `hit["author"]` |
| `title` | `hit.get("title")` |
| `body` | `hit.get("story_text") or hit.get("comment_text") or ""` |
| `score` | `hit.get("points") or 0` |
| `created_at` | `hit["created_at"]` (ISO 8601, already UTC) |

HN comment text is **HTML** (`<p>`, `&#x27;`, `<a href>`). Strip tags and unescape entities
at ingest — if you don't, the regex filter matches on markup and Haiku wastes tokens on
`<p>`. Use `html.unescape` + a minimal tag strip; don't pull in a parser dependency for
this.

## The upsert

One helper, used by both sources:

```python
stmt = insert(RawPost).values(**row).on_conflict_do_nothing(
    index_elements=["source", "external_id"]
)
```

`DO NOTHING`, not `DO UPDATE`. Scores and bodies drift after posting; we snapshot at first
sight and never revise. Revising would mean re-running enrichment to stay consistent, which
breaks the "never enrich twice" invariant for no benefit.

Batch the inserts (one statement per subreddit, not per post) — this is the difference
between a run that takes 2 seconds and one that takes 2 minutes.

## Wiring — `worker.py`

```python
class WorkerSettings:
    cron_jobs = [
        cron(ingest_all, hour={0, 6, 12, 18}, minute=0),
    ]
```

`ingest_all` calls the Reddit and HN ingesters concurrently (`asyncio.gather`) and logs a
one-line summary per source: fetched / inserted / skipped.

**Wrap each source in its own try/except.** Reddit being rate-limited must not stop the HN
pull, and a dead subreddit (renamed, gone private) must not stop the other eight. Log the
exception, return zero counts, let the next tick retry. With ten subreddits across two
verticals this stops being theoretical — one of them will break.

Use `asyncio.gather(..., return_exceptions=True)` so one failed coroutine doesn't cancel
its siblings, and log the per-source counts as a breakdown by vertical. If insurance is
pulling 400 posts a run and real estate 12, you want to see that in the logs on day one,
not discover it three weeks later in a lopsided digest.

## Acceptance

- [ ] `arq` worker starts and the cron fires on schedule
- [ ] After one run, `SELECT count(*) FROM raw_posts` > 0 with rows from every configured
      subreddit and from HN
- [ ] `SELECT vertical_hint, count(*) FROM raw_posts GROUP BY 1` shows all three of
      `insurance`, `real_estate`, `NULL` — and neither vertical is near-zero
- [ ] Every row from a vertical-specific subreddit has a non-NULL `vertical_hint`; every HN
      and shared-subreddit row has `NULL`
- [ ] Comments carry the same `vertical_hint` as their parent submission
- [ ] Killing one subreddit's fetch (point it at a nonexistent sub) still lets the rest of
      the run complete
- [ ] **Running the ingest twice back-to-back inserts 0 rows the second time** — the
      headline test
- [ ] `created_at` values are timezone-aware and plausibly match the post's real age
- [ ] No row has `author = 'None'` (the string)
- [ ] HN bodies contain no `<p>` or `&#x27;`
- [ ] Killing the worker mid-run and restarting leaves no partial/corrupt rows
