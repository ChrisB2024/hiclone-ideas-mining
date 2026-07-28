# 05 — Chunk 4: Score + Digest

Rank clusters, have Sonnet write up the top 5, deliver it. This is the product.

---

## Scoring (`score.py`)

Pure SQL + Python. No LLM — scoring must be cheap enough to re-run whenever you tweak a
weight.

```
score = frequency_term × recency_term × wtp_term × buildability_term × relevance_term
```

| Term | Formula | Rationale |
|---|---|---|
| `frequency` | `ln(1 + distinct_authors)` | Log, not linear. 20 people isn't 20× more interesting than 1 — but it's meaningfully more than 5 |
| `recency` | `exp(-days_since(last_seen_at) / 14)` | 2-week half-ish-life. A pain nobody has mentioned in two months is solved, seasonal, or dead |
| `wtp` | `1 + 2·(explicit_ratio) + 0.5·(implied_ratio)` | Ratios of members, not counts — otherwise this double-counts frequency |
| `buildability` | `buildable_ratio` (0–1) | Multiplicative and can hit zero. A pain no LLM can address scores 0 by design |
| `relevance` | `avg(relevance) / 10` | Keeps `r/smallbusiness` noise from outranking real vertical pain |

Multiplicative throughout so any single dimension can veto: nothing buildable, nothing
relevant, and nothing stale gets to the top on volume alone.

Exclude clusters with `distinct_authors < 2`. A single person's complaint is an anecdote —
it may be a real pain but it carries no demand signal, and the whole premise is that
frequency is the proxy.

Write `score` and `scored_at` back to `clusters`. Recompute **all** clusters each run;
recency changes even when membership doesn't.

### Ranking is within-vertical

One formula, one set of weights, both verticals — but **`score` is only ever compared
against clusters in the same vertical**:

```sql
SELECT * FROM clusters
WHERE vertical = :vertical AND distinct_authors >= 2
ORDER BY score DESC LIMIT :n;
```

Scores are not comparable across verticals and shouldn't be made to be. Insurance will
almost certainly out-volume real estate — more dedicated subreddits, more B2B software
talk — so a single global top-5 would be all insurance most weeks, and the real-estate
pipeline would silently produce nothing you ever read. Taking the top N *per vertical*
guarantees both get looked at, which is the point of running two.

Do **not** "fix" this by normalizing scores across verticals, or by weighting real estate
up to compensate. The numbers mean what they mean; the ranking is just partitioned. If real
estate genuinely has nothing worth reading in a given week, the right outcome is a thin
real-estate section, not a padded one.

Put every weight in `config.py` as a named constant. You will tune these.

---

## Digest (`digest.py`)

One `claude-sonnet-5` call per week, covering **both verticals**. Sonnet, not Haiku: this is
synthesis and prose quality across ten clusters — the one place in the pipeline worth
paying for. It's ~$0.10/week.

One call, not one per vertical: the input is small enough, and a single call lets the model
see both industries at once and notice when the same underlying pain shows up in both. That
cross-vertical observation is worth having — a tool that solves it twice has a bigger market
— and it's the one place where mixing the verticals is safe, because the model is writing
prose about clusters rather than forming them.

### Input

```python
DIGEST_CLUSTERS_PER_VERTICAL = 5
```

Top 5 clusters by `score` **per vertical** (see the ranking query above), so 10 total. For
each, gather:

- `label`, `score`, `distinct_authors`, `member_count`, `last_seen_at`
- The 3 most representative members — order by `raw_posts.score DESC` (a proxy for "well
  articulated and agreed with")
- For each: `pain_point`, `who_has_it`, `current_workaround`,
  `willingness_to_pay_signal`, a ≤300-char body excerpt, and the `url`

Assemble as a plain-text block, **grouped under two labelled headings** (`## INSURANCE`,
`## REAL ESTATE`). No JSON, no tool use — it's one call with a fixed shape.

If a vertical has fewer than 5 qualifying clusters, pass what there is and say so in the
block (`(only 2 clusters cleared the bar this week)`). Don't backfill from the other
vertical to reach 10.

### Prompt shape

System: you're briefing a founder who builds LLM tools for two verticals, insurance and
real estate. Keep the two sections separate and in that order. For each cluster write: the
pain in one line, who has it, what they do today, the strongest verbatim quote with its
link, and one sentence on what a tool would do. Terse. No preamble, no "here's your digest",
no closing summary. Markdown.

Two vertical-specific instructions:

- *"Never merge a pain from one section into the other, and never describe a cluster as
  applying to both industries inside a section."* Given ten clusters and two structurally
  similar industries, the model will otherwise editorialize across the boundary — which
  quietly undoes the partition the whole pipeline maintains, at the last step, in the one
  artifact you actually read.
- *"If — and only if — the same underlying pain appears in both sections, add a final
  `## BOTH` section of at most two lines naming it. Omit the section entirely otherwise."*
  Bounded and opt-in. Without the "only if" and the line cap, this section gets written
  every week with a strained analogy in it.

**Every quote must carry its link.** The thread authors are warm leads — a pain point
without a URL is trivia. If Sonnet drops a link, the digest failed at its actual job.

Set `max_tokens=8000` — double the single-vertical figure, since the input and output both
roughly doubled. Non-streaming is fine at this size.

### Rendering

Sonnet's markdown, with a deterministic header prepended in Python — period, and a
per-vertical line of cluster count / new signals / total distinct authors. Don't ask the
model for numbers it can get wrong — it writes the prose, you write the facts.

The per-vertical counts in the header are also your weekly health check on the pipeline: if
real estate shows 0 new signals three weeks running, the problem is upstream (subreddits,
keyword gate, or the `neither` rate) and you'd otherwise not notice, because the digest
would still arrive looking fine.

### Delivery

Two sinks, both on by default:

1. **File** — `digests/YYYY-MM-DD.md`. Always. This is the fallback that can't fail.
2. **Email** — plain SMTP to yourself. Config: host, port, user, password, to-address.

Write the `digests` row (with `markdown`) **before** attempting delivery, then set
`delivered = true` after. If email fails you still have the digest on disk and in the DB;
log and move on. A failed send must never lose the content.

### Wiring

```python
cron_jobs = [
    ...,
    cron(score_clusters, weekday=0, hour=7, minute=0),
    cron(send_digest,    weekday=0, hour=7, minute=5),
]
```

Monday morning. `period_start` = last digest's `period_end` (or 7 days ago on first run),
`period_end` = now.

---

## Acceptance

- [ ] `score_clusters` writes a non-NULL `score` to every cluster with `distinct_authors >= 2`
- [ ] Ordering is sane by inspection **within each vertical**: a 12-author cluster from last
      week outranks a 3-author cluster from two months ago
- [ ] A cluster with `buildable_ratio = 0` scores 0
- [ ] Digest generates from real data, contains both sections, and **every quote has a
      working link**
- [ ] Every cluster appears under the heading matching its `clusters.vertical` — spot-check
      all ten against the DB
- [ ] With a vertical starved to 2 qualifying clusters, the digest renders a short section
      and does not backfill from the other one
- [ ] `## BOTH` is absent on a week where nothing genuinely overlaps
- [ ] The `digests` row is written even when SMTP is unreachable
- [ ] The markdown file lands in `digests/`
- [ ] **Read the digest as its recipient.** Could you send a cold email off any of these
      ten? If not, the problem is upstream — usually the clustering threshold or the
      `pain_point` field description — not the digest prompt
- [ ] Ask the sharper version of that question: for each item, is it obvious **which
      industry's practitioner you'd be emailing**? An item where that's ambiguous is a
      merged cluster that slipped the partition — go back to chunk 3

---

## Done

Chunks 1–4 complete: posts in, digest out, both verticals, weekly, unattended.

Anything you're tempted to add next — a dashboard, a third vertical, a per-vertical config
framework, a feedback loop — is on the non-goals list in [README.md](README.md). The next
feature is sending outreach emails to the people in the digest, and that happens in your
inbox, not in this repo.
