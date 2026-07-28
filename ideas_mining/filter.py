"""Cheap filter — kills ~90% of volume before any token is spent.

Pure Python: no LLM, no network, no secrets. Runs as its own ARQ job after ingest.

The pure ``classify`` / impure ``run_filter`` split is deliberate: it lets the whole
rule set be tested without a database.
"""

from __future__ import annotations

from dataclasses import dataclass


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
        ``too_short``       len(body.strip()) < MIN_BODY_CHARS and no title
        ``negative_score``  score < 0
        ``deleted_author``  author is None
        ``removed``         body is [deleted] / [removed]

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
    """
    raise NotImplementedError("TODO")


async def run_filter() -> dict[str, int]:
    """Apply ``classify`` to every ``filter_state = 'pending'`` row.

    Returns per-state counts. Idempotent: a second run selects nothing, because rows
    only ever move pending -> passed|rejected and never back.
    """
    raise NotImplementedError("TODO")
