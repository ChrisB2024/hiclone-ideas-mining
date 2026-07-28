# Module — `ingest` (`ideas_mining/ingest/`)

Status: **not implemented.** Source: [`specs/02-chunk1-ingest.md`](../../specs/02-chunk1-ingest.md).

## Purpose
Pull new posts and top comments from Reddit and Hacker News into `raw_posts`. No judgement,
no filtering, no LLM — a faithful mirror of what was on the internet.

## Responsibilities
- `reddit.py` — asyncpraw, read-only. Walk `VERTICALS[*]["subreddits"]` +
  `SHARED_SUBREDDITS`; per submission, take the top `COMMENTS_PER_POST` top-level comments
  by score.
- `hackernews.py` — Algolia HN API, one pass per vertical using its `hn_query`, paginated.
- Set `vertical_hint` from the subreddit config; **always `NULL` for HN**, regardless of
  which query matched `[SPECIFIED]`.
- Strip HTML and unescape entities on HN bodies at ingest `[SPECIFIED]`.
- Batched idempotent upsert (`ON CONFLICT DO NOTHING`), one statement per subreddit.

## Non-Responsibilities
- **Does not classify verticals.** `vertical_hint` is a hint; `enrich` decides `[SPECIFIED]`.
- Does not filter, score, or reject. Everything fetched is stored.
- Does not refresh or revise stored rows — `DO NOTHING`, never `DO UPDATE` `[SPECIFIED]`.

## Inputs / Outputs
In: `VERTICALS`, `SHARED_SUBREDDITS`, `INGEST_LOOKBACK_HOURS`, `POSTS_PER_SUBREDDIT`,
`COMMENTS_PER_POST`, Reddit credentials. Out: rows in `raw_posts` with
`filter_state='pending'`; per-source counts logged (fetched / inserted / skipped, broken
down by vertical `[SPECIFIED]`).

## State Machine
Stateless per run. All continuity is the `(source, external_id)` unique constraint.
`LOOKBACK (12h) > INTERVAL (6h)` deliberately, so a missed run self-heals `[SPECIFIED]`.

## Data Model
Writes `raw_posts` only. Field mapping tables (Reddit and HN) are in `specs/02`.

## API Contract
`[INFERRED — NEEDS HUMAN CONFIRMATION]` Two coroutines returning per-source counts, plus an
`ingest_all` that gathers them. Signatures not specified by the human.

## Invariants
- `INV-3` — **re-running inserts zero rows.** The headline acceptance test.
- `INV-5` — a crash mid-run leaves no partial or corrupt rows.
- Comments inherit their submission's `vertical_hint`; never recomputed `[SPECIFIED]`.
- HN rows always have `vertical_hint IS NULL`.

## Trust Boundaries
**The outer edge of the system.** Everything written here is untrusted attacker-controllable
text. It is stored verbatim (after HTML stripping) and never evaluated.

## Failure Modes
- **`author` is `None` for deleted accounts**, and `str(None)` yields `"None"` — which then
  reads as one prolific user and corrupts `distinct_authors` (`INV-7`). Check before
  stringifying `[SPECIFIED]`.
- **`created_utc` is a naive float.** Must attach `tz=UTC` `[SPECIFIED]`.
- A renamed/private subreddit raises mid-gather → must not cancel siblings.
- Reddit rate limit → must not stop HN.
- The same HN `objectID` returned by both vertical queries → absorbed by the upsert.

## Security
Reddit credentials from env only, never logged. Read-only API scope — no user credentials.
Do not log post bodies at INFO.

## Dependencies
asyncpraw, httpx, SQLAlchemy, `html` (stdlib). **No HTML parser dependency** — the human
explicitly ruled one out for this `[SPECIFIED]`.

## Testing Requirements
- Ingest twice → **0 rows inserted the second time**.
- `vertical_hint` histogram shows `insurance`, `real_estate`, and `NULL`, none near zero.
- Every vertical-subreddit row has a non-NULL hint; every HN/shared row has NULL.
- Comments match their parent's hint.
- Pointing one subreddit at a nonexistent sub still lets the run complete.
- `created_at` is timezone-aware.
- No row has the literal string `'None'` as author.
- HN bodies contain no `<p>` or `&#x27;`.

## Open Questions
`OQ-4` — can ingest be tested without live API calls? Field mapping is the most
regression-prone part of this module and is untestable without fixtures or cassettes.
