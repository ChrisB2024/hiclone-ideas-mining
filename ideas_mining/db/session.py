"""Async engine and session factory.

Purpose: give every stage a session without any stage owning connection lifecycle.
Security: the connection string comes from ``settings`` only — never ``os.environ``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ideas_mining.config import settings

# TODO: create_async_engine(settings.database_url, pool_pre_ping=True).
#       pool_pre_ping matters here — the worker holds connections idle between cron
#       ticks, and Postgres/proxies drop idle connections without telling the client.
engine: AsyncEngine | None = None

# TODO: async_sessionmaker(engine, expire_on_commit=False).
#       expire_on_commit=False because stages read attributes off objects after
#       commit; the default would re-fetch (and fail outside a session).
SessionLocal: async_sessionmaker[AsyncSession] | None = None


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session, committing on success and rolling back on error.

    Invariant supported: INV-5 (resumability). A stage that raises mid-run must leave
    no partial rows, so the rollback here is load-bearing, not hygiene.
    """
    raise NotImplementedError("TODO: yield from SessionLocal with commit/rollback")
    yield  # type: ignore[unreachable]  # keeps this a generator for typing


async def dispose_engine() -> None:
    """Close the pool. Called from ARQ's on_shutdown."""
    raise NotImplementedError("TODO")
