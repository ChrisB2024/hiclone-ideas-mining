# Module — `filter` (`ideas_mining/filter.py`)

Status: **not implemented.** Source: [`specs/03-chunk2-filter-enrich.md`](../../specs/03-chunk2-filter-enrich.md).

## Purpose
Kill ~90% of ingested volume with regex and length checks, before any token is spent. Pure
Python, no LLM, no network.

## Responsibilities
- Reject rules, cheapest first: `too_short` (<100 chars, no title), `negative_score`,
  `deleted_author`, `removed` (`[deleted]`/`[removed]` body).
- Pass rule: any `PAIN_PATTERNS` regex hit over `title + "\n" + body`, case-insensitive.
- **Vertical gate on shared sources only** — rows with `vertical_hint IS NULL` must
  additionally match a keyword from *either* vertical's list, else `no_vertical_keyword`.
- Always write `filter_reason` — the rule name on reject, the matched pattern on pass.

## Non-Responsibilities
- **Does not assign a vertical.** The gate checks both lists and records neither; that would
  be doing the model's job with a substring match `[SPECIFIED]`.
- Does not delete, mutate, or re-fetch `raw_posts`. Only `filter_state` and `filter_reason`.
- Does not call anything external.

## Inputs / Outputs
In: `raw_posts WHERE filter_state = 'pending'`; `PAIN_PATTERNS`, `VERTICALS[*]["keywords"]`.
Out: same rows with `filter_state` ∈ {`passed`,`rejected`} and a non-NULL `filter_reason`.

## State Machine
`pending → passed | rejected`. One-way. Re-running the filter is a no-op because nothing is
left in `pending`.

Re-running over `rejected` rows after loosening a pattern is explicitly supported and free —
that's why `filter_state` is separate from enrichment `[SPECIFIED]`.

## Data Model
Reads and updates `raw_posts` only. Writes no other table.

## API Contract
`[INFERRED — NEEDS HUMAN CONFIRMATION]` A `run_filter()` coroutine over pending rows, and a
pure `classify(post) -> (state, reason)` that the tests can drive directly. The pure split is
inferred, not specified — but without it this module is only testable through the DB.

## Invariants
- Every row leaving `pending` has a non-NULL `filter_reason`, on **both** paths.
- The gate applies **only** when `vertical_hint IS NULL`.
- Idempotent: a second run selects nothing.

## Trust Boundaries
Operates directly on untrusted text. Regexes run against attacker-controlled input —
see Failure Modes.

## Failure Modes
- `[INFERRED — NEEDS HUMAN CONFIRMATION]` **Catastrophic backtracking.** `PAIN_PATTERNS` are
  attacker-facing. The current set looks linear, but any future pattern with nested
  quantifiers could hang the worker on a crafted post. Worth a Validator robustness probe.
- Pass rate outside 3–20% means the rules are mistuned, not broken `[SPECIFIED]`.
- Real estate is expected to run *lower* than insurance, and `no_vertical_keyword` is
  expected to be the top reject reason overall. Neither is a bug `[SPECIFIED]`.

## Security
No network, no secrets, no eval. Regex-only exposure to untrusted input.

## Dependencies
stdlib `re`, SQLAlchemy.

## Testing Requirements
- Every `pending` row ends `passed` or `rejected`, with a reason on both.
- Second run is a no-op.
- A row with `vertical_hint` set and no keyword match still passes on a pain pattern alone.
- A shared-source row with a pain hit but no keyword → `no_vertical_keyword`.
- `no_vertical_keyword` never appears for a vertical-specific subreddit.
- Each reject rule fires on a crafted input.
- Pass rate on a real corpus lands in 3–20%, per vertical.

## Open Questions
`OQ-6` — the pass-rate check is a distributional property of live data, not a unit test.
Where does it live?
