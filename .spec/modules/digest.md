# Module — `digest` (`ideas_mining/digest.py`)

Status: **not implemented.** Source: [`specs/05-chunk4-digest.md`](../../specs/05-chunk4-digest.md).

## Purpose
The product. One weekly `claude-sonnet-5` call over the top clusters in both verticals,
rendered to markdown, persisted, and emailed.

## Responsibilities
- Select top `DIGEST_CLUSTERS_PER_VERTICAL` (5) clusters **per vertical** via the ranking
  query.
- For each, gather the 3 most representative members (`ORDER BY raw_posts.score DESC`) with
  `pain_point`, `who_has_it`, `current_workaround`, WTP signal, a ≤300-char excerpt, and the
  `url`.
- Assemble a plain-text block grouped under `## INSURANCE` / `## REAL ESTATE`.
- One Sonnet call, `max_tokens=8000`, non-streaming.
- Prepend a **deterministic Python-computed header** — period, plus per-vertical cluster
  count / new signals / distinct authors.
- **Write the `digests` row, then attempt delivery** (file + SMTP).

## Non-Responsibilities
- Does not score or cluster.
- Does not compute any number that appears in the output — the model writes prose, Python
  writes facts `[SPECIFIED]`.
- Does not retry or queue failed email.

## Inputs / Outputs
In: `clusters` + members; `DIGEST_MODEL`; SMTP config. Out: a `digests` row; a file at
`digests/YYYY-MM-DD.md`; an email.

`period_start` = previous digest's `period_end`, or 7 days ago on first run.

## State Machine
```
select ─▶ Sonnet call ─▶ render ─▶ INSERT digests (delivered=false)
                                        │
                                        ├─ write file  (always; cannot fail the run)
                                        └─ SMTP send ──success──▶ delivered = true
                                                     └─failure──▶ log, continue
```
The insert **precedes** delivery. This ordering is the invariant, not an implementation
detail `[SPECIFIED]`.

## Data Model
Writes `digests` (`cluster_ids` = insurance in rank order, then real estate). Reads
`clusters`, `enriched_signals`, `raw_posts`.

## API Contract
Anthropic Messages API, plain text in / markdown out. No tools, no JSON, no structured
output — it's one call with a fixed shape `[SPECIFIED]`.

Three prompt constraints that are contract, not style:
- Sections stay separate and ordered; **never merge a pain across sections.**
- A `## BOTH` section is emitted **only if** a genuine overlap exists, max two lines.
- Every quote carries its link.

## Invariants
- `INV-10` — **every quote carries its source URL.** A pain without a URL is trivia; the
  thread author is the lead.
- `INV-11` — persisted before delivery; a failed send never loses content.
- Every cluster appears under the heading matching its `clusters.vertical`.
- A short vertical is rendered short — **never backfilled** from the other `[SPECIFIED]`.

## Trust Boundaries
- **Untrusted text reaches the model and the output.** Post excerpts and quotes are
  attacker-controllable, and they land in an email you read. `[INFERRED — NEEDS HUMAN
  CONFIRMATION]` A hostile post could inject markdown or a misleading link into the digest.
  Impact is limited to misleading a single human reader (the operator), and the output is
  markdown in a file/email, not rendered HTML in a browser session. Accepted risk, but the
  human should confirm.
- SMTP credentials are secrets and must never appear in the digest or the log.

## Failure Modes
- Sonnet drops a link → **the digest failed at its actual job** `[SPECIFIED]`, even though
  nothing errored.
- The model editorializes across the vertical boundary → undoes the partition the whole
  pipeline maintains, at the last step, in the one artifact that gets read.
- `## BOTH` emitted every week with a strained analogy (happens without the "only if" + line
  cap).
- SMTP unreachable → non-fatal by design.
- Fewer than 5 qualifying clusters in a vertical → expected, render short.

## Security
SMTP creds from env. Single hardcoded recipient — not user-supplied, so no injection into
the address. Do not log the rendered digest at INFO if excerpts may carry PII.

## Dependencies
`anthropic`, stdlib `smtplib`/`email`, SQLAlchemy.

## Testing Requirements
- Generates from real data, contains both sections, **every quote has a working link**.
- Every cluster appears under the heading matching its DB `vertical` — spot-check all 10.
- A vertical starved to 2 clusters renders short and does not backfill.
- `## BOTH` absent when nothing overlaps.
- **The `digests` row is written when SMTP is unreachable** — inject a failing sender and
  assert the row exists and the file landed.
- The markdown file lands in `digests/`.
- Header numbers are computed in Python and match the DB, not the prose.
- Manual: read as recipient; is it obvious which industry's practitioner you'd email
  (**manual — `OQ-6`**).

## Open Questions
`OQ-6`. Also `[OPEN QUESTION]` — is the `digests/` output directory Builder-owned? It's
created by source at runtime, so yes under the write boundary, but it holds no code.
