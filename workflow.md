Pipeline: Ingest → Filter → Enrich → Cluster → Score → Digest

1. Ingest (ARQ cron, every 6–12h)
PRAW pulls new posts + top comments from a config list of subreddits: r/Insurance, r/InsuranceAgent, r/InsurancePros, r/smallbusiness, plus HN via their free API. Store raw in a raw_posts table (source, url, author, text, score, created_at). Dedupe on post ID — idempotent upserts so reruns are safe.

2. Cheap filter (no LLM yet)
Regex/keyword pass for pain signals: "is there a tool", "how do you handle", "wasting hours", "manually", "I'd pay", "spreadsheet hell". Also drop low-effort posts (<100 chars, negative karma). This kills ~90% of volume before you spend tokens.

3. LLM enrich (Claude Haiku, batched)
For survivors, one structured call per post: extract {pain_point, who_has_it, current_workaround, willingness_to_pay_signal, buildable_with_llm: bool, relevance_to_insurance: 0-10}. Return JSON only. Haiku keeps this near-free. Store in enriched_signals.

4. Cluster (pgvector)
Voyage embedding on the pain_point text. Cosine similarity to merge duplicates — 15 people describing the same quoting-workflow pain is one idea with a frequency count of 15. Frequency is your demand proxy.

5. Score + digest
Rank clusters: frequency × recency × willingness-to-pay signals × buildability. Weekly digest — one Sonnet call summarizing top 5 clusters with example quotes and links — emailed to you or dumped to a Markdown file. Don't build a dashboard. The digest is the product.

Build order (DADP-friendly): spec the enriched_signals schema and the invariant "one cluster = one distinct pain" first. Then: Chunk 1 = Reddit ingest + dedupe, Chunk 2 = filter + Haiku enrichment, Chunk 3 = embedding/clustering, Chunk 4 = digest. Chunks 1–2 alone are already useful — you can read filtered posts manually before clustering exists.

Timebox: this is a 2-evening build, not a two-week one. If it starts growing a UI or a "multi-vertical config system," that's the escape hatch pattern. Its only job is to hand you insurance pain points that become outreach hooks for prospects — including thread authors themselves, who are literally warm leads posting their problems publicly.