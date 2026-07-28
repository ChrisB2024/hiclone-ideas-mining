"""ARQ entry point — the only place cron schedules exist.

No business logic here. This module wires schedules to functions and nothing else.
All pipeline state lives in Postgres, so a worker restart loses nothing.
"""

from __future__ import annotations

from typing import Any

from arq import cron

from ideas_mining.cluster import assign_clusters, embed_pending
from ideas_mining.digest import send_digest
from ideas_mining.enrich import collect_enrichment, submit_enrichment
from ideas_mining.filter import run_filter
from ideas_mining.ingest.hackernews import ingest_hackernews
from ideas_mining.ingest.reddit import ingest_reddit
from ideas_mining.score import score_clusters


async def ingest_all(ctx: dict[str, Any]) -> dict[str, Any]:
    """Run both ingest sources concurrently and log a per-source summary.

    Use ``asyncio.gather(..., return_exceptions=True)``: Reddit being rate-limited must
    not stop the HN pull, and one dead subreddit must not stop the other nine.

    Log counts broken down BY VERTICAL. If insurance is pulling 400 posts a run and
    real estate 12, you want that visible on day one — not discovered three weeks later
    in a lopsided digest.
    """
    raise NotImplementedError("TODO: gather ingest_reddit() and ingest_hackernews()")


async def startup(ctx: dict[str, Any]) -> None:
    """Create the DB engine/pool."""
    raise NotImplementedError("TODO")


async def shutdown(ctx: dict[str, Any]) -> None:
    """Dispose the DB engine."""
    raise NotImplementedError("TODO")


class WorkerSettings:
    """ARQ worker configuration.

    Schedule note: ``submit_enrichment`` at :15 assumes ``run_filter`` finished by
    then. That's a time-based assumption, not a dependency — a slow filter run means an
    empty submit, which self-corrects on the next tick. Every collect/embed/assign job
    is a no-op when nothing is pending, so running them frequently costs nothing.
    """

    on_startup = startup
    on_shutdown = shutdown

    # TODO: redis_settings = RedisSettings.from_dsn(settings.redis_url)

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
