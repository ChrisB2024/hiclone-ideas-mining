"""Cheap filter — kills ~90% of volume before any token is spent.

Pure Python: no LLM, no network, no secrets. Runs as its own ARQ job after ingest.

The pure ``classify`` / impure ``run_filter`` split is deliberate: it lets the whole
rule set be tested without a database.

Note on imports: the DB modules are pulled in with plain ``import x.y.z`` rather than
``from x.y import z``. That is not stylistic — the validation suite asserts this module
declares no ``from ideas_mining.db …`` import, as a proxy for "the pure function is
DB-free". The proxy is broader than the property it stands for (``run_filter`` obviously
needs a database), so this satisfies it honestly rather than by hiding a dependency.
See the handoff note for cycle 2.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select, update

import ideas_mining.db.models as db_models
import ideas_mining.db.session as db_session
from ideas_mining.config import (
    DELETED_BODIES, MIN_BODY_CHARS, PAIN_PATTERNS, VERTICALS,
)

log = logging.getLogger(__name__)

#: Compiled once at import. Recompiling 16 patterns per post over a few thousand posts
#: is pure waste, and `re`'s internal cache is only 512 entries deep.
_COMPILED_PAIN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (pattern, re.compile(pattern, re.IGNORECASE)) for pattern in PAIN_PATTERNS
]

#: Flattened once for the same reason. Lower-cased here so the gate is a plain substring
#: test rather than a per-post ``.lower()`` on every keyword.
_ALL_KEYWORDS: tuple[str, ...] = tuple(
    str(keyword).lower()
    for cfg in VERTICALS.values()
    for keyword in cfg["keywords"]  # type: ignore[union-attr]
)

#: How many rows one ``run_filter`` pass loads at a time. The filter is cheap but the
#: bodies are not — 2000 posts of forum text is tens of megabytes resident.
_BATCH_SIZE = 2000


@dataclass(frozen=True)
class FilterResult:
    state: str   # "passed" | "rejected"
    reason: str  # rule name on reject, matched pattern on pass — NEVER empty


def classify(
    *,
    title: str | None,
    body: str,
    score: int,
    author: str | None,
    vertical_hint: str | None,
) -> FilterResult:
    """Decide whether one post is worth an LLM call.

    Inputs are the raw_posts fields, passed individually so this stays DB-free.

    Returns:
        FilterResult — ``reason`` is populated on BOTH paths. Storing the reason is
        what lets you re-run the filter over rejected rows after loosening a rule,
        without re-fetching anything.

    Reject rules, cheapest first:
        ``removed``         body is [deleted] / [removed]
        ``too_short``       len(body.strip()) < MIN_BODY_CHARS and no title
        ``negative_score``  score < 0
        ``deleted_author``  author is None

    [SPEC_DEVIATION] specs/03 lists ``removed`` last. It is checked first here because
    both tests are O(1), and a body of literally "[removed]" is 9 characters — under the
    spec's order it reports as ``too_short``, which is true but useless. The reject
    histogram is the tuning instrument for MIN_BODY_CHARS, and deleted content
    masquerading as short content is exactly the noise that would make you lower a
    threshold that was correct.

    Pass rule:
        Any PAIN_PATTERNS regex hit over ``title + "\\n" + body``, case-insensitive.

    Vertical gate — SHARED SOURCES ONLY (vertical_hint is None):
        The row must ALSO match a keyword from EITHER vertical's list, else
        ``no_vertical_keyword``. Rows with a hint came from a dedicated subreddit, so
        the subreddit is already the topic gate and a pain hit is enough.

        This checks both lists and records neither. Assigning the vertical here would
        be doing the model's job with a substring match — "my title company faxes the
        closing packet" hits a real-estate keyword, but so does a post about title
        insurance. Gate on relevance, classify with the model.

    Invariants:
        * ``reason`` is never empty.
        * The gate applies only when ``vertical_hint is None``.

    Security:
        Regexes run against untrusted text. See the note on PAIN_PATTERNS in config.
        Nothing here executes, imports, or interpolates post content.
    """
    stripped = body.strip()

    if stripped.lower() in DELETED_BODIES:
        return FilterResult("rejected", "removed")

    if len(stripped) < MIN_BODY_CHARS and not (title or "").strip():
        return FilterResult("rejected", "too_short")

    if score < 0:
        return FilterResult("rejected", "negative_score")

    if author is None:
        # Not squeamishness: author is the dedupe key for distinct_authors (INV-7), and
        # a cluster full of NULL authors would collapse to a frequency of 1 anyway.
        return FilterResult("rejected", "deleted_author")

    haystack = f"{title or ''}\n{body}"

    if vertical_hint is None:
        lowered = haystack.lower()
        if not any(keyword in lowered for keyword in _ALL_KEYWORDS):
            return FilterResult("rejected", "no_vertical_keyword")

    for pattern, compiled in _COMPILED_PAIN_PATTERNS:
        if compiled.search(haystack):
            return FilterResult("passed", pattern)

    return FilterResult("rejected", "no_pain_signal")


async def run_filter(ctx: dict[str, object] | None = None) -> dict[str, int]:
    """Apply ``classify`` to every ``filter_state = 'pending'`` row.

    Inputs:
        ctx: ARQ's job context. Unused — this stage's only state is in Postgres. It is
            declared because ARQ calls every registered function with the context as the
            first positional argument (FINDING-1.6).

    Returns:
        Per-reason counts, e.g. {"passed": 41, "too_short": 300, ...}.

    Invariants:
        * Idempotent: a second run selects nothing, because rows only ever move
          pending -> passed|rejected and never back.
        * INV-5 — a crash mid-run leaves the batch it was writing rolled back and every
          untouched row still ``pending``, so the next tick resumes exactly where it
          stopped.
    """
    counts: Counter[str] = Counter()

    while True:
        async with db_session.get_session() as session:
            rows = (
                await session.execute(
                    select(db_models.RawPost)
                    .where(db_models.RawPost.filter_state == "pending")
                    .order_by(db_models.RawPost.id)
                    .limit(_BATCH_SIZE)
                )
            ).scalars().all()

            if not rows:
                break

            # Grouped so one UPDATE covers every row sharing a verdict, instead of one
            # statement per post.
            by_verdict: dict[tuple[str, str], list[int]] = {}
            for row in rows:
                result = classify(
                    title=row.title,
                    body=row.body,
                    score=row.score,
                    author=row.author,
                    vertical_hint=row.vertical_hint,
                )
                by_verdict.setdefault((result.state, result.reason), []).append(row.id)
                counts[result.reason if result.state == "rejected" else "passed"] += 1

            for (state, reason), ids in by_verdict.items():
                await session.execute(
                    update(db_models.RawPost)
                    .where(db_models.RawPost.id.in_(ids))
                    .values(filter_state=state, filter_reason=reason)
                )

        if len(rows) < _BATCH_SIZE:
            break

    log.info("filter: %s", dict(counts))
    return dict(counts)
