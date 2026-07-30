"""Async engine and session factory.

Purpose: give every stage a session without any stage owning connection lifecycle.
Security: the connection string comes from ``settings`` only — never ``os.environ``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine,
)

from ideas_mining.config import settings

#: pool_pre_ping matters here — the worker holds connections idle between cron ticks,
#: and Postgres (or a proxy in front of it) drops idle connections without telling the
#: client. Without the ping, the first query after a quiet hour raises instead of
#: transparently reconnecting.
#:
#: Creating the engine at import time is safe: SQLAlchemy connects lazily, so importing
#: this module never touches the network.
engine: AsyncEngine = create_async_engine(settings.database_url, pool_pre_ping=True)

#: expire_on_commit=False because stages read attributes off ORM objects after commit;
#: the default expires every attribute on commit and the next access triggers a refetch,
#: which raises outside an open session.
SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session, committing on success and rolling back on error.

    Returns:
        An async context manager yielding one ``AsyncSession``.

    Invariant supported: INV-5 (resumability). A stage that raises mid-run must leave no
    partial rows, so the rollback here is load-bearing, not hygiene — every stage's
    "just re-run it" property depends on a failed run having written nothing.

    Security: no credentials pass through this function; they were resolved once in
    ``settings`` when the engine was built.
    """
    session = SessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def dispose_engine() -> None:
    """Close the pool. Called from ARQ's on_shutdown.

    Idempotent — disposing an already-disposed engine is a no-op, so a worker that
    crashes during shutdown can be restarted without special handling.
    """
    await engine.dispose()
