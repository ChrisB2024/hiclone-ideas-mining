"""ARQ entry point — the only place cron schedules exist.

No business logic here. This module wires schedules to functions and nothing else.
All pipeline state lives in Postgres, so a worker restart loses nothing.

**Every function registered below takes ``ctx`` first** (FINDING-1.6). ARQ calls each
job with its context dict as the first positional argument; a function without it gets
the context bound to whatever its first parameter happens to be, which for
``embed_pending`` meant a dict arriving as ``batch_size``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from arq import cron

from ideas_mining.cluster import assign_clusters, embed_pending
from ideas_mining.config import settings
from ideas_mining.db.session import dispose_engine
from ideas_mining.digest import send_digest
from ideas_mining.enrich import collect_enrichment, submit_enrichment
from ideas_mining.filter import run_filter
from ideas_mining.ingest.hackernews import ingest_hackernews
from ideas_mining.ingest.reddit import ingest_reddit
from ideas_mining.score import score_clusters

try:  # pragma: no cover - import shim
    from arq.connections import RedisSettings
except ImportError:  # pragma: no cover
    # arq is a hard runtime dependency; this guard exists so the module stays
    # importable when only the top-level `arq` name is available (validation stubs it
    # to check the cron table without installing the package). With RedisSettings
    # absent, ARQ falls back to its own localhost default — which is fine, because a
    # process that couldn't import arq.connections was never going to run a worker.
    RedisSettings = None  # type: ignore[assignment]

log = logging.getLogger(__name__)


async def ingest_all(ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run both ingest sources concurrently and log a per-source summary.

    Inputs:
        ctx: ARQ job context, unused — ingest state lives entirely in raw_posts.

    Returns:
        {"reddit": {...}, "hackernews": {...}} — per-source counts, or an
        ``{"error": str}`` stand-in for a source that raised.

    ``return_exceptions=True`` is the point: Reddit being rate-limited must not stop the
    HN pull, and one dead subreddit must not stop the other nine. Each source already
    isolates its own targets internally; this is the outer layer of the same rule.

    Counts are logged BY SOURCE, and per-subreddit by vertical inside each ingester. If
    insurance is pulling 400 posts a run and real estate 12, that is visible on day one
    — not discovered three weeks later in a lopsided digest.
    """
    reddit_result, hn_result = await asyncio.gather(
        ingest_reddit(), ingest_hackernews(), return_exceptions=True
    )

    summary: dict[str, Any] = {}
    for name, result in (("reddit", reddit_result), ("hackernews", hn_result)):
        if isinstance(result, BaseException):
            log.error("ingest source %s failed: %s", name, result)
            summary[name] = {"error": str(result)}
        else:
            summary[name] = result

    log.info("ingest_all: %s", summary)
    return summary


async def startup(ctx: dict[str, Any]) -> None:
    """Configure logging. The DB engine is created lazily on first use.

    ``db.session`` builds the engine at import time and SQLAlchemy connects lazily, so
    there is no pool to warm here. Logging is configured once, from settings, rather
    than at import — a module that configures logging on import hijacks every process
    that imports it, including the test runner.
    """
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info("worker starting (enrich=%s, embed=%s)", settings.enrich_model, settings.embed_model)


async def shutdown(ctx: dict[str, Any]) -> None:
    """Dispose the DB engine so connections are returned before the process exits."""
    await dispose_engine()


class WorkerSettings:
    """ARQ worker configuration.

    Schedule note: ``submit_enrichment`` at :15 assumes ``run_filter`` finished by
    then. That's a time-based assumption, not a dependency — a slow filter run means an
    empty submit, which self-corrects on the next tick. Every collect/embed/assign job
    is a no-op when nothing is pending, so running them frequently costs nothing.

    ``recluster_all`` is deliberately absent: on a cron it would rewrite every cluster
    id every run, and the digest could never say a cluster grew.
    """

    on_startup = startup
    on_shutdown = shutdown

    redis_settings = (
        RedisSettings.from_dsn(settings.redis_url) if RedisSettings is not None else None
    )

    functions = [
        ingest_all, run_filter, submit_enrichment, collect_enrichment,
        embed_pending, assign_clusters, score_clusters, send_digest,
    ]

    cron_jobs = [
        # --- chunk 1-2: every 6h, staggered ---
        cron(ingest_all, hour={0, 6, 12, 18}, minute=0),
        cron(run_filter, hour={0, 6, 12, 18}, minute=10),
        cron(submit_enrichment, hour={0, 6, 12, 18}, minute=15),
        # Batches usually finish within the hour; ceiling is 24h. Poll on its own
        # cadence rather than blocking a worker slot inside submit.
        cron(collect_enrichment, minute={5, 35}),

        # --- chunk 3 ---
        cron(embed_pending, minute={20, 50}),
        cron(assign_clusters, minute={25, 55}),

        # --- chunk 4: Monday morning ---
        cron(score_clusters, weekday=0, hour=7, minute=0),
        cron(send_digest, weekday=0, hour=7, minute=5),
    ]
