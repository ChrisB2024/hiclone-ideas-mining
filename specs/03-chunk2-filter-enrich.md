# 03 — Chunk 2: Cheap Filter + Haiku Enrichment

Two stages, one chunk. The filter kills ~90% of volume for free; Haiku turns what's left
into structured `enriched_signals` rows.

**This is the milestone.** After this chunk you can `SELECT pain_point, who_has_it, url
FROM enriched_signals JOIN raw_posts …` and read real pain points by hand. Everything after
is ranking.

---

## Stage A — the cheap filter (`filter.py`)

No LLM. Pure Python over `raw_posts WHERE filter_state = 'pending'`.

Runs as its own ARQ job right after ingest.

### Reject rules (checked first, cheapest first)

| Rule | Reason string |
|---|---|
| `len(body.strip()) < 100` and no title | `too_short` |
| `score < 0` | `negative_score` |
| `author IS NULL` | `deleted_author` |
| Body is `[deleted]` / `[removed]` | `removed` |

### Pass rule

Case-insensitive regex over `title + "\n" + body`. Pass if **any** pain-signal pattern hits:

```python
PAIN_PATTERNS = [
    r"\bis there (a|any) (tool|software|app|service)\b",
    r"\bhow (do|does) (you|your team|anyone) (handle|deal with|manage)\b",
    r"\bwasting (hours|time|days)\b",
    r"\b(spend|spending) \w+ hours\b",
    r"\bmanually\b",
    r"\bby hand\b",
    r"\bcopy[- ]?past",
    r"\bre-?key(ing)?\b",
    r"\bi'?d pay\b",
    r"\bwould pay\b",
    r"\bworth paying\b",
    r"\bspreadsheet hell\b",
    r"\bhate (doing|having to)\b",
    r"\btedious\b",
    r"\bthere has to be a better way\b",
    r"\bany recommendations for\b",
]
```

### Vertical gate — shared sources only

Rows with `vertical_hint IS NOT NULL` came from a dedicated subreddit; the subreddit *is*
the topic gate, so a pain-pattern hit is enough.

Rows with `vertical_hint IS NULL` (HN, `r/smallbusiness`, `r/Entrepreneur`) must **also**
match at least one keyword from either vertical's `keywords` list, case-insensitive:

```python
if post.vertical_hint is None:
    haystack = f"{post.title or ''}\n{post.body}".lower()
    if not any(kw.lower() in haystack
               for cfg in VERTICALS.values()
               for kw in cfg["keywords"]):
        reject("no_vertical_keyword")
```

Without this gate, `r/smallbusiness` floods enrichment with well-formed operational pain
from restaurants, agencies, and e-commerce — every one of which passes the pain patterns
cleanly, costs a Haiku call, and comes back `neither`. The gate is the cheap version of the
question the model would otherwise be paid to answer.

Note it checks **both** verticals' keywords and doesn't record which matched. Assigning the
vertical here would be doing the model's job with a substring match — a post saying "my
title company sends the closing packet as a fax" hits a real-estate keyword, but so does a
post about title insurance. Gate on relevance, classify with the model.

Set `filter_state = 'passed'` or `'rejected'`, and always write `filter_reason` (the rule
name on reject, the matched pattern on pass).

**Store the reason.** When you later find a good pain that got filtered out, you need to
know which rule killed it. Re-running the filter over `rejected` rows after loosening a
pattern is free — that's why `raw_posts` and `filter_state` are separate from enrichment.

Tune by inspection, not by theory — and tune **per vertical**, because they'll behave
differently:

```sql
SELECT vertical_hint, filter_reason, count(*) FROM raw_posts
WHERE filter_state='rejected' GROUP BY 1, 2 ORDER BY 3 DESC;

SELECT vertical_hint,
       count(*) FILTER (WHERE filter_state='passed')::float / count(*) AS pass_rate
FROM raw_posts GROUP BY 1;
```

If `too_short` dominates, the threshold is wrong. If the pass rate is above ~20%, tighten;
below ~3%, loosen. Expect real estate to run **lower** than insurance — `r/RealEstate` is
consumer-heavy — and expect `no_vertical_keyword` to be the top reject reason overall. Both
are working as designed; only act if a vertical's pass rate is near zero.

---

## Stage B — Haiku enrichment (`enrich.py`)

Input: `raw_posts` where `filter_state = 'passed'` and no `enriched_signals` row exists.

```sql
SELECT rp.* FROM raw_posts rp
LEFT JOIN enriched_signals es ON es.raw_post_id = rp.id
WHERE rp.filter_state = 'passed' AND es.id IS NULL
```

That `LEFT JOIN … IS NULL` **is** the "never enrich twice" invariant. Don't track state with
a flag column — the join can't drift.

### Model

`claude-haiku-4-5` — $1.00/$5.00 per MTok, 200K context. Locked in config as
`ENRICH_MODEL`.

Haiku is the right tier here: the task is bounded extraction against a fixed schema with no
reasoning chain. Sonnet would cost 3× for output that's structurally identical.

### The schema

Define once, use for both the API constraint and local validation:

```python
from typing import Literal
from pydantic import BaseModel, Field

class Enrichment(BaseModel):
    vertical: Literal["insurance", "real_estate", "neither"] = Field(description=
        "Which industry's day-to-day operations this pain belongs to. 'insurance' covers "
        "agencies, brokerages, carriers, underwriting, claims. 'real_estate' covers "
        "residential and commercial brokerage, property management, and transactions. "
        "Use 'neither' for any other industry, and for consumer complaints about buying "
        "insurance or a house — we want the practitioner's pain, not the customer's. "
        "A pain that genuinely applies to both industries equally is 'neither'.")
    pain_point: str = Field(description=
        "The specific operational pain in one sentence, phrased generically so that two "
        "people describing the same pain produce near-identical sentences. Name the task "
        "and why it hurts. No names, companies, or story details. Keep the industry's own "
        "vocabulary — write 'quote' for insurance and 'listing' for real estate rather "
        "than a neutral word that covers both.")
    who_has_it: str = Field(description=
        "The role or business type that has this pain, e.g. 'independent P&C agent', "
        "'residential listing agent', 'property manager'.")
    current_workaround: str | None = Field(description=
        "What they do today instead. Null if the post doesn't say.")
    willingness_to_pay_signal: Literal["none", "implied", "explicit"]
    buildable_with_llm: bool = Field(description=
        "Could a small LLM-powered tool plausibly solve most of this?")
    relevance: int = Field(ge=0, le=10, description=
        "How central this pain is to the vertical you assigned. 0 if vertical is 'neither'.")
```

Two field descriptions are load-bearing:

**`pain_point`** is doing the real work in the whole system. Clustering is downstream of it:
if the model writes narrative sentences, semantically identical pains land far apart in
vector space and the invariant breaks. "Phrased generically so two people produce
near-identical sentences" is the instruction that makes clustering possible.

The added clause — *keep the industry's own vocabulary* — is the two-vertical fix. Left to
itself the model will neutralize domain nouns into generic ones ("re-entering the same
client information into multiple systems"), which is precisely the sentence that makes an
insurance pain and a real-estate pain embed on top of each other. The vertical partition in
chunk 3 catches that, but you want the text separated too, so that inspecting a cluster
tells you which industry you're looking at without a join.

**`vertical`'s "consumer vs practitioner" clause** matters more than it reads. `r/Insurance`
and `r/RealEstate` are dominated by customers — someone whose claim was denied, someone
confused by closing costs. Those posts describe genuine friction, pass the pain filter, and
are useless: the person with the pain is not someone you can sell software to. Without the
explicit carve-out the model labels them with the industry, they cluster, and they'll
outnumber practitioner pains in `r/RealEstate` specifically.

### The call

Structured outputs, not prompt-and-hope. Two paths:

**Sync path** (dev, small batches) — `client.messages.parse()` returns a validated
`Enrichment` on `response.parsed_output`:

```python
response = client.messages.parse(
    model=settings.ENRICH_MODEL,
    max_tokens=1024,
    system=ENRICH_SYSTEM_PROMPT,
    messages=[{"role": "user", "content": post_text}],
    output_format=Enrichment,
)
signal = response.parsed_output   # None if parsing failed — guard it
```

**Batch path** (production, the default) — 50% cheaper, and enrichment is never
latency-sensitive:

```python
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

batch = client.messages.batches.create(requests=[
    Request(
        custom_id=f"post-{post.id}",
        params=MessageCreateParamsNonStreaming(
            model=settings.ENRICH_MODEL,
            max_tokens=1024,
            system=[{"type": "text", "text": ENRICH_SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": post_text}],
            output_config={"format": {
                "type": "json_schema",
                "schema": Enrichment.model_json_schema(),
            }},
        ),
    )
    for post in posts
])
```

Three traps in the batch path:

1. **`.parse()` doesn't exist for batches.** You pass the schema through
   `output_config.format` yourself and validate the returned text with
   `Enrichment.model_validate_json(text)` on the way out. The API constrains the shape; the
   Pydantic call is what catches a truncated response.
2. **Results come back in arbitrary order.** Key on `result.custom_id`, never on position.
   Parse the post id back out of `post-{id}`.
3. **Not every result succeeded.** `result.result.type` is `succeeded` / `errored` /
   `canceled` / `expired`. Only write rows for `succeeded`; log and skip the rest — they'll
   be picked up on the next run automatically, because the `LEFT JOIN` still finds them.

Poll `client.messages.batches.retrieve(batch.id).processing_status` until `"ended"`. Most
batches finish inside an hour; the hard ceiling is 24h. Run submit and collect as **two
separate ARQ jobs** with the batch id persisted between them — don't block a worker slot
sleeping in a poll loop.

The system prompt is identical across every request in the batch, so mark it with
`cache_control` as above. It needs to be ≥1024 tokens to actually cache on Haiku — if yours
is shorter, the marker is harmless but does nothing.

### Post text sent to the model

```
Source: r/{subsource}  (or: Hacker News)
Likely vertical: {vertical_hint or "unknown"}
Title: {title or "(comment)"}

{body}
```

**`Likely vertical` is a prior, not an instruction**, and the system prompt has to say so:
*"Likely vertical is a hint from which forum the post came from. It is often right and
sometimes wrong — a post in an insurance forum can be about real estate or about neither.
Judge from the post text."* Without that sentence the model treats the hint as a label and
the field stops carrying information — you'd get the subreddit config back, and shared
sources (where the hint is `unknown`) would be the only ones actually classified.

Truncate `body` to ~4000 characters. Nothing past that changes the extraction, and the tail
of a long rant is the least informative part of it.

### Writing results

Insert into `enriched_signals` with `on_conflict_do_nothing(index_elements=["raw_post_id"])`
— belt and braces against a double-collect. Set `model` to the model ID used.

**Drop rows with `relevance < 3`, or rows with `vertical = 'neither'`, before insert?** No —
write them all. They're already paid for, the threshold belongs in the scoring query where
you can change it without re-spending tokens, and the `neither` rate per source is the
number that tells you whether the filter's keyword gate is working:

```sql
SELECT rp.subsource, es.vertical, count(*)
FROM enriched_signals es JOIN raw_posts rp ON rp.id = es.raw_post_id
GROUP BY 1, 2 ORDER BY 1, 3 DESC;
```

A shared source running above ~50% `neither` means the keyword list is too loose. A
dedicated subreddit running high `neither` means it's the wrong subreddit — that's the
signal to drop `r/RealEstate` if it doesn't earn its place.

---

## Wiring

```python
cron_jobs = [
    cron(ingest_all,        hour={0, 6, 12, 18}, minute=0),
    cron(run_filter,        hour={0, 6, 12, 18}, minute=10),
    cron(submit_enrichment, hour={0, 6, 12, 18}, minute=15),
    cron(collect_enrichment, minute={5, 35}),   # every 30m, no-op if nothing pending
]
```

## Acceptance

- [ ] Filter marks every `pending` row as `passed` or `rejected`, with a reason on both
- [ ] Pass rate is between 3% and 20%; `filter_reason` histogram looks sane
- [ ] Re-running the filter is a no-op (nothing left in `pending`)
- [ ] A batch of 20 real passed posts round-trips: submitted, collected, 20 rows in
      `enriched_signals`
- [ ] Every `enriched_signals` row validates against `Enrichment`
- [ ] `willingness_to_pay_signal` only ever contains the three enum values
- [ ] `vertical` only ever contains the three enum values, and **both** industries are
      represented in a real batch
- [ ] Every `vertical = 'neither'` row has `relevance = 0`
- [ ] `no_vertical_keyword` appears in the reject histogram for shared sources and never
      for a vertical-specific subreddit
- [ ] **Hand-check 10 rows where `vertical` disagrees with `vertical_hint`.** The model
      should be right most of the time. If it's just echoing the hint back, the "hint is a
      prior" sentence isn't landing and shared sources are the only thing being classified
- [ ] Re-running enrichment immediately after a successful collect makes **zero** API calls
- [ ] An `errored` batch result logs and skips without writing a partial row, and the post
      is retried on the next tick
- [ ] **Eyeball 20 `pain_point` values, 10 per vertical.** Two posts about the same
      underlying pain should read almost identically. If they read like summaries of the
      posts, fix the field description before starting chunk 3 — clustering cannot recover
      from this
- [ ] In that same sample, an insurance `pain_point` and a real-estate one should be
      distinguishable **from the sentence alone**, without looking at `vertical`. If they're
      both written in neutral business language, the "keep the industry's own vocabulary"
      clause isn't working
