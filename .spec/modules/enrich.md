# Module — `enrich` (`ideas_mining/enrich.py`)

Status: **not implemented.** Source: [`specs/03-chunk2-filter-enrich.md`](../../specs/03-chunk2-filter-enrich.md).

## Purpose
Turn filtered posts into structured `enriched_signals` rows via `claude-haiku-4-5`, using
the Batch API. This is the module that decides each post's vertical.

## Responsibilities
- Select work with `LEFT JOIN enriched_signals ... WHERE es.id IS NULL` — **the join is the
  invariant**; do not add a state flag `[SPECIFIED]`.
- Submit a batch (`submit_enrichment`) and collect it (`collect_enrichment`) as **two
  separate ARQ jobs**, with the batch id persisted between them. Never sleep in a poll loop
  `[SPECIFIED]`.
- Constrain output with `output_config.format` from `Enrichment.model_json_schema()`, then
  validate returned text with `Enrichment.model_validate_json()`.
- Mark the shared system prompt with `cache_control`.
- Write results with `ON CONFLICT DO NOTHING` on `raw_post_id`; set `model` to the ID used.

## Non-Responsibilities
- Does not drop low-relevance or `neither` rows — those thresholds belong to scoring
  `[SPECIFIED]`.
- Does not embed or cluster.
- Does not re-enrich, ever, for any reason.

## Inputs / Outputs
In: `raw_posts` where `filter_state='passed'` and unenriched; `ENRICH_MODEL`;
`ANTHROPIC_API_KEY`. Out: one `enriched_signals` row per successful result.

Prompt payload includes `Likely vertical: {vertical_hint or "unknown"}` **plus the system
sentence declaring it a prior, not a label** — without that sentence the field stops
carrying information `[SPECIFIED]`. Body truncated to ~4000 chars.

## State Machine
```
post passed, no signal row  ──submit──▶  in-flight batch  ──collect──▶  signal row exists
                                              │
                                    errored/expired result
                                              │
                                              └──▶ back to "no signal row" (auto-retried)
```
The failure edge is free: a failed result writes nothing, so the `LEFT JOIN` re-selects it
next tick `[SPECIFIED]`.

## Data Model
Writes `enriched_signals`. `Enrichment` (Pydantic) is the contract — see `specs/03` for the
full model with field descriptions.

## API Contract
Anthropic Batch API. Two hard rules `[SPECIFIED]`:
- **`.parse()` does not exist for batches.** Pass the schema via `output_config.format` and
  validate the returned text yourself.
- **Results return in arbitrary order.** Key on `result.custom_id` (`post-{id}`), never
  position. Check `result.result.type` ∈ `succeeded`/`errored`/`canceled`/`expired`.

## Invariants
- `INV-4` — never enrich twice. Re-running immediately after a collect makes **zero** API calls.
- `INV-8` — one dominant pain per post; never multi-assign.
- `vertical = 'neither'` ⇒ `relevance = 0`.
- Every stored row validates against `Enrichment`.

## Trust Boundaries
**The prompt-injection surface.** Untrusted post text goes into an LLM prompt. Blast radius
is bounded to one row: output is schema-constrained, so a hostile post can mislabel itself
but cannot emit new fields, reach the DB, or affect other rows. `[INFERRED — NEEDS HUMAN
CONFIRMATION]` that this bounded blast radius is acceptable and needs no mitigation.

## Failure Modes
- Partial batch success is **normal**, not an error path.
- A truncated response passes the API's shape constraint but fails Pydantic — which is
  exactly why the local validate step exists.
- Batch expiry (24h ceiling).
- A system prompt under ~1024 tokens won't actually cache; the marker is harmless but inert
  `[SPECIFIED]`.
- Model echoing `vertical_hint` back instead of judging → the field silently becomes a copy
  of the subreddit config.

## Security
`ANTHROPIC_API_KEY` from env, never logged. No secret may enter a prompt or a stored row.

## Dependencies
`anthropic`, Pydantic, SQLAlchemy.

## Testing Requirements
- 20 real passed posts round-trip: submitted, collected, 20 rows.
- Re-running after a successful collect makes **zero** API calls.
- An `errored` result logs, skips, writes no partial row, and is retried next tick.
- Out-of-order `custom_id` results are matched correctly — **shuffle the results in the
  fixture**, since position-keying passes an ordered test.
- `vertical` and `willingness_to_pay_signal` only ever contain their enum values.
- Both industries appear in a real batch.
- Every `neither` row has `relevance = 0`.
- Hand-check 10 rows where `vertical` disagrees with `vertical_hint` (**manual — `OQ-6`**).
- `pain_point` phrasing quality and industry-vocabulary retention (**manual — `OQ-6`**).

## Open Questions
`OQ-4`, `OQ-6`. The two acceptance criteria that matter most here — is `pain_point` phrased
consistently enough to cluster, and is the model actually judging the vertical — are both
human-judgement checks with no assertion form.
