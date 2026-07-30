"""Alembic environment — async, driven by ideas_mining.config.

The database URL comes from ``settings``, never from alembic.ini and never from
``os.environ`` here. That keeps config.py the single secrets boundary even for tooling.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from ideas_mining.config import settings
from ideas_mining.db.models import Base

config = context.config

#: Autogenerate compares against this. Importing models also runs the embedding_dim
#: guard, so a migration can never be generated against an unset vector dimension.
target_metadata = Base.metadata


def _configure(connection: object) -> None:
    """Shared context configuration for both modes."""
    context.configure(
        connection=connection,  # type: ignore[arg-type]
        target_metadata=target_metadata,
        compare_type=True,
        # pgvector's Vector type renders as a plain type; without this, autogenerate
        # emits an import-less `Vector(1024)` that fails at migration import time.
        render_as_batch=False,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live connection (``alembic upgrade head --sql``)."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: object) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


def run_migrations_online() -> None:
    """Run migrations against a live async connection."""
    asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
