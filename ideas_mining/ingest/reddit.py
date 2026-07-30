"""Reddit ingest — asyncpraw, read-only.

Trust boundary: this is the outer edge of the system. Everything written here is
untrusted, attacker-controllable text. It is stored verbatim and never evaluated.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpraw

from ideas_mining.config import SHARED_SUBREDDITS, VERTICALS, settings
from ideas_mining.db.session import get_session
from ideas_mining.ingest.upsert import upsert_raw_posts

log = logging.getLogger(__name__)

SOURCE = "reddit"


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


def _author_name(author: Any) -> str | None:
    """Return the author's name, or None for a deleted account.

    Inputs:
        author: an asyncpraw ``Redditor`` or ``None``.

    Returns:
        The username, or None.

    Invariant: never returns the string "None". ``str(None)`` yields "None", which then
    reads as one extremely prolific user and inflates ``distinct_authors`` on every
    cluster containing a deleted account — corrupting INV-7 silently, since the value
    looks like a perfectly ordinary username.
    """
    if author is None:
        return None
    name = getattr(author, "name", None)
    return name if name else None


def _created_at(created_utc: float) -> datetime:
    """Convert Reddit's epoch float to an aware UTC datetime.

    ``created_utc`` is a naive float. A naive datetime written to a ``timestamptz``
    column is silently interpreted as server-local time, drifting recency scoring by
    however many hours the server is offset from UTC.
    """
    return datetime.fromtimestamp(created_utc, tz=UTC)


def _make_client() -> asyncpraw.Reddit:
    """Build a read-only asyncpraw client from settings.

    Security: credentials come from ``settings`` (which reads the environment) and are
    never logged. No user credentials are requested — this is a script app with
    read-only scope, so a leaked key cannot post, vote, or read private data.
    """
    return asyncpraw.Reddit(
        client_id=settings.reddit_client_id,
        client_secret=settings.reddit_client_secret,
        user_agent=settings.reddit_user_agent,
        read_only=True,
    )


async def ingest_subreddit(
    subreddit: str,
    vertical_hint: str | None,
    *,
    reddit: asyncpraw.Reddit | None = None,
) -> dict[str, int]:
    """Fetch new submissions and their top comments from one subreddit.

    Inputs:
        subreddit: name without the "r/" prefix.
        vertical_hint: from ``build_targets`` — written to every row produced here.
        reddit: an open client to reuse. [SPEC_DEVIATION] Not in the module spec; added
            because ``ingest_reddit`` runs ten subreddits concurrently and building ten
            OAuth clients wastes ten token requests. Defaults to None, in which case
            this function owns and closes its own client, so it stays callable alone
            from a REPL.

    Returns:
        {"fetched": int, "inserted": int, "skipped": int} — ``skipped`` is
        ``fetched - inserted``, i.e. rows the upsert recognised as already stored.

    Invariants:
        * INV-3 — all writes go through ``upsert_raw_posts``.
        * Comments inherit their submission's ``vertical_hint``; never recomputed.

    Security: every field written here is attacker-controlled. Bodies are stored
    verbatim and never evaluated; ``vertical_hint`` comes from our own config, not from
    the post, so a hostile poster cannot assign themselves a vertical.
    """
    owns_client = reddit is None
    client = reddit or _make_client()
    window_start = datetime.now(UTC) - timedelta(hours=settings.ingest_lookback_hours)
    rows: list[dict[str, Any]] = []

    try:
        # FINDING-2.11: three nested failure domains, deliberately. Reddit is a
        # third-party API returning objects assembled from user content, and any single
        # attribute access can raise. Before this, one bad submission — or one comment
        # tree that failed to expand — discarded every row already collected from the
        # subreddit, including the good ones ahead of it in the listing.
        try:
            sub = await client.subreddit(subreddit)
            async for submission in sub.new(limit=settings.posts_per_subreddit):
                try:
                    created = _created_at(submission.created_utc)
                    if created < window_start:
                        # `new` is newest-first, so everything past here is older
                        # still and already stored. Breaking rather than continuing
                        # is what keeps a 12h window cheap on a subreddit with years
                        # of history.
                        break

                    rows.append({
                        "source": SOURCE,
                        "external_id": submission.fullname,
                        "parent_external_id": None,
                        "subsource": str(submission.subreddit),
                        "vertical_hint": vertical_hint,
                        "url": f"https://reddit.com{submission.permalink}",
                        "author": _author_name(submission.author),
                        "title": submission.title,
                        "body": submission.selftext or "",
                        "score": submission.score,
                        "created_at": created,
                    })
                except Exception as exc:
                    log.warning("r/%s: skipping a submission: %s", subreddit, exc)
                    continue

                # Comments are a separate domain from their submission: losing a
                # comment tree must not lose the submission row already collected.
                try:
                    # limit=0 removes the "load more comments" placeholders instead
                    # of expanding them. The long tail is low-signal and each
                    # expansion is a separate API round trip.
                    await submission.comments.replace_more(limit=0)
                    top_level = sorted(
                        submission.comments,
                        key=lambda c: getattr(c, "score", 0),
                        reverse=True,
                    )[: settings.comments_per_post]

                    for comment in top_level:
                        rows.append({
                            "source": SOURCE,
                            "external_id": comment.fullname,
                            "parent_external_id": submission.fullname,
                            "subsource": str(submission.subreddit),
                            # Inherited, never recomputed — a comment is about
                            # whatever its submission's forum is about.
                            "vertical_hint": vertical_hint,
                            "url": f"https://reddit.com{comment.permalink}",
                            "author": _author_name(comment.author),
                            "title": None,
                            "body": comment.body or "",
                            "score": getattr(comment, "score", 0),
                            "created_at": _created_at(comment.created_utc),
                        })
                except Exception as exc:
                    log.warning(
                        "r/%s: comments unavailable for %s: %s",
                        subreddit, getattr(submission, "fullname", "?"), exc,
                    )
        except Exception as exc:
            # The listing itself died (rate limit, private sub, network). Fall through
            # to the upsert so the rows already fetched are not thrown away.
            log.warning("r/%s: listing ended early: %s", subreddit, exc)
    finally:
        if owns_client:
            await client.close()

    async with get_session() as session:
        inserted = await upsert_raw_posts(session, rows)

    return {"fetched": len(rows), "inserted": inserted, "skipped": len(rows) - inserted}


async def ingest_reddit() -> dict[str, int]:
    """Run every target concurrently and aggregate counts.

    Returns:
        {"fetched": int, "inserted": int, "skipped": int, "failed_targets": int}

    Failure mode: ``asyncio.gather(..., return_exceptions=True)``. With ten subreddits,
    one WILL break — renamed, gone private, deleted, or rate-limited — and one raising
    must not cancel its siblings. The exception is logged, the target counts zero, and
    the next tick retries it. Because the lookback window is longer than the interval,
    a skipped run costs nothing.
    """
    targets = build_targets()
    client = _make_client()
    try:
        results = await asyncio.gather(
            *(ingest_subreddit(sub, hint, reddit=client) for sub, hint in targets),
            return_exceptions=True,
        )
    finally:
        await client.close()

    totals = {"fetched": 0, "inserted": 0, "skipped": 0, "failed_targets": 0}
    for (sub, hint), result in zip(targets, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("reddit ingest failed for r/%s (hint=%s): %s", sub, hint, result)
            totals["failed_targets"] += 1
            continue
        # Per-target, per-vertical visibility on day one. If insurance pulls 400 rows a
        # run and real estate 12, that shows up here rather than three weeks later in a
        # lopsided digest.
        log.info(
            "r/%s hint=%s fetched=%d inserted=%d",
            sub, hint, result["fetched"], result["inserted"],
        )
        for key in ("fetched", "inserted", "skipped"):
            totals[key] += result[key]

    return totals
