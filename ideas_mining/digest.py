"""Weekly digest — the product.

One Sonnet call over the top clusters in both verticals, rendered to markdown,
persisted, then delivered.
"""

from __future__ import annotations

from datetime import datetime

DIGEST_SYSTEM_PROMPT = """\
You are briefing a founder who builds LLM tools for two verticals: insurance and real
estate.

Keep the two sections separate and in that order. For each cluster write: the pain in
one line, who has it, what they do today, the strongest verbatim quote with its link,
and one sentence on what a tool would do.

Never merge a pain from one section into the other, and never describe a cluster as
applying to both industries inside a section.

If — and only if — the same underlying pain appears in both sections, add a final
"## BOTH" section of at most two lines naming it. Omit the section entirely otherwise.

Every quote must include its link.

Terse. No preamble, no "here's your digest", no closing summary. Markdown.
"""
# The two vertical rules are contract, not style. With ten clusters from two
# structurally similar industries the model will otherwise editorialize across the
# boundary — quietly undoing the partition the whole pipeline maintains, at the last
# step, in the one artifact that actually gets read. The "only if" and the line cap on
# BOTH are what stop it being written every week with a strained analogy in it.


async def gather_digest_input(period_start: datetime, period_end: datetime) -> str:
    """Build the plain-text block for the model.

    Top ``DIGEST_CLUSTERS_PER_VERTICAL`` clusters per vertical via
    ``score.top_clusters``, grouped under ``## INSURANCE`` / ``## REAL ESTATE``.

    Per cluster: label, score, distinct_authors, member_count, last_seen_at, plus the
    3 most representative members ``ORDER BY raw_posts.score DESC`` (a proxy for "well
    articulated and agreed with"), each with pain_point, who_has_it,
    current_workaround, WTP signal, a <=300-char excerpt, and the url.

    If a vertical has fewer than N qualifying clusters, pass what exists and say so in
    the block, e.g. "(only 2 clusters cleared the bar this week)". Do NOT backfill from
    the other vertical to reach 10.

    No JSON, no tool use — one call with a fixed shape.
    """
    raise NotImplementedError("TODO")


def render_header(period_start: datetime, period_end: datetime) -> str:
    """Deterministic, Python-computed header. Prepended to the model's markdown.

    Period, plus a per-vertical line: cluster count / new signals / distinct authors.

    Never ask the model for a number it can get wrong — it writes the prose, you write
    the facts. These counts are also the weekly health check: if real estate shows 0
    new signals three weeks running, the problem is upstream (subreddits, keyword gate,
    or the 'neither' rate) and you would otherwise not notice, because the digest would
    still arrive looking fine.
    """
    raise NotImplementedError("TODO")


async def send_digest() -> int:
    """Generate, persist, then deliver. Returns the digests row id.

    Order is the invariant (INV-11), not an implementation detail::

        select -> Sonnet call -> render -> INSERT digests (delivered=false)
                                              |
                                              +- write digests/YYYY-MM-DD.md   (always)
                                              +- SMTP send -> delivered = true
                                                           -> on failure: log, continue

    Two sinks, both on by default. The file sink is the fallback that cannot fail. If
    email fails you still have the digest on disk and in the DB — a failed send must
    never lose content.

    period_start = the previous digest's period_end, or 7 days ago on first run.
    max_tokens=8000, non-streaming.

    Invariant INV-10 — every quote carries its source URL. If the model drops a link,
    the digest failed at its actual job even though nothing raised: the thread author
    is the lead, and a pain point without a URL is trivia.

    Security: SMTP credentials come from settings, never appear in the digest or logs.
    The recipient is a single configured address, not user-supplied. Digest text
    contains untrusted excerpts — do not log the rendered body at INFO.
    """
    raise NotImplementedError("TODO")
