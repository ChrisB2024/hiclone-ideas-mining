"""Reddit ingest — asyncpraw, read-only.

Trust boundary: this is the outer edge of the system. Everything written here is
untrusted, attacker-controllable text. It is stored verbatim and never evaluated.
"""

from __future__ import annotations

from ideas_mining.config import SHARED_SUBREDDITS, VERTICALS


def build_targets() -> list[tuple[str, str | None]]:
    """Flatten the config into ``(subreddit, vertical_hint)`` pairs.

    The second element becomes ``vertical_hint`` on every row from that subreddit —
    submissions AND their comments. Shared subreddits get ``None``.

    Returns:
        e.g. [("Insurance", "insurance"), ..., ("smallbusiness", None)]
    """
    targets: list[tuple[str, str | None]] = [
        (sub, vertical)
        for vertical, cfg in VERTICALS.items()
        for sub in cfg["subreddits"]  # type: ignore[union-attr]
    ]
    targets += [(sub, None) for sub in SHARED_SUBREDDITS]
    return targets


async def ingest_subreddit(subreddit: str, vertical_hint: str | None) -> dict[str, int]:
    """Fetch new submissions and their top comments from one subreddit.

    Inputs:
        subreddit: name without the "r/" prefix.
        vertical_hint: from ``build_targets`` — written to every row produced here.

    Returns:
        {"fetched": int, "inserted": int, "skipped": int}

    Invariants:
        * INV-3 — all writes go through ``upsert_raw_posts``.
        * Comments inherit their submission's ``vertical_hint``; never recomputed.

    Steps (specs/02-chunk1-ingest.md):
        1. ``subreddit.new(limit=settings.posts_per_subreddit)``
        2. Skip submissions older than the lookback window — already stored.
        3. Map and collect the submission row.
        4. ``submission.comments.replace_more(limit=0)`` — do NOT expand "load more"
           chains. The long tail is low-signal and expensive.
        5. Take the top ``comments_per_post`` TOP-LEVEL comments by score.

    Two traps that silently corrupt data rather than raising:
        * ``author`` is ``None`` for deleted accounts, and ``str(None)`` yields the
          string "None" — which then reads as one very prolific user and corrupts
          ``distinct_authors`` (INV-7). Check for None BEFORE stringifying.
        * ``created_utc`` is a naive float. Always attach ``tz=UTC``. A naive datetime
          in a timestamptz column is silently read as server-local time and drifts
          recency scoring by hours.
    """
    raise NotImplementedError("TODO")


async def ingest_reddit() -> dict[str, int]:
    """Run every target concurrently and aggregate counts.

    Failure mode: use ``asyncio.gather(..., return_exceptions=True)``. With ten
    subreddits, one WILL break (renamed, private, deleted) — and one raising must not
    cancel its siblings. Log the exception, count zero, let the next tick retry.
    """
    raise NotImplementedError("TODO")
