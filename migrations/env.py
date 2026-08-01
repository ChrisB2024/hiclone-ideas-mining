"""Alembic environment — async, driven by ideas_mining.config.

The database URL comes from ``settings``, never from alembic.ini and never from
``os.environ`` here. That keeps config.py the single secrets boundary even for tooling.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from alembic import context
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ideas_mining.config import settings
from ideas_mining.db.models import Base

log = logging.getLogger("alembic.env")

config = context.config

MIGRATIONS_DIR = Path(__file__).parent
#: Opt-in migrations, applied only via alembic.deferred.ini. Not in this config's
#: version_locations, which is what keeps `alembic upgrade head` from applying them.
DEFERRED_DIR = MIGRATIONS_DIR / "deferred"

#: Matches the `revision: str = "0002"` line our template generates. Deliberately a
#: text scan rather than an import or an Alembic RevisionMap: building a map over the
#: deferred directory alone would fail, because those revisions point at a
#: down_revision that lives in the other directory.
_REVISION_RE = re.compile(r"^revision(?:\s*:\s*str)?\s*=\s*[\"']([^\"']+)[\"']", re.M)

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


def _do_run(connection: object, *, ahead_on_opt_in_chain: bool = False) -> None:
    """Configure the migration context and run, unless this upgrade has nothing to do.

    Inputs:
        connection: the sync connection handed over by ``run_sync``.
        ahead_on_opt_in_chain: from the probe — the database sits on a revision that
            belongs to the opt-in chain rather than this configuration's.

    The upgrade test happens *here*, after ``configure()``, because that is the first
    point at which the running command is knowable: ``alembic.context`` is a module of
    proxy functions, not the EnvironmentContext instance, so ``context_opts`` is not
    reachable before this. ``configure()`` copies those options onto the
    MigrationContext, where ``fn`` is the per-command migration function.
    """
    _configure(connection)

    if ahead_on_opt_in_chain and _is_upgrade():
        log.info(
            "database is ahead on the opt-in chain; this configuration has nothing "
            "to upgrade. Use alembic.deferred.ini to manage that chain."
        )
        return

    with context.begin_transaction():
        context.run_migrations()


def _opt_in_revisions() -> set[str]:
    """Revision ids that live in migrations/deferred/ — the opt-in chain.

    Returns:
        The set of revision ids, empty if the directory is absent.
    """
    if not DEFERRED_DIR.is_dir():
        return set()
    found: set[str] = set()
    for path in DEFERRED_DIR.glob("*.py"):
        match = _REVISION_RE.search(path.read_text(encoding="utf-8"))
        if match:
            found.add(match.group(1))
    return found


def _managed_revisions() -> set[str]:
    """Revision ids this Alembic configuration knows about."""
    return {script.revision for script in ScriptDirectory.from_config(config).walk_revisions()}


def _is_upgrade() -> bool:
    """True when the running Alembic command is ``upgrade``.

    Returns:
        Whether this env.py invocation is serving an upgrade.

    FINDING-4.6: the opt-in tolerance below must apply to the deployment upgrade path
    and nothing else. Applied to every command, it made ``downgrade base`` exit 0
    while leaving the schema fully intact — a command reporting success for work it
    did not do, which is worse than the error it was suppressing.

    Alembic passes the per-command migration function as ``fn``; ``upgrade`` and
    ``downgrade`` are named after their commands, ``stamp`` arrives as ``do_stamp``.
    Anything that is not an upgrade falls through to Alembic's own error.

    Only callable after ``context.configure()`` — see ``_do_run``.
    """
    fn = context.get_context().opts.get("fn")
    return getattr(fn, "__name__", "") == "upgrade"


async def _database_is_ahead_on_opt_in_chain(connection: object) -> bool:
    """True when the database sits on an opt-in revision this config doesn't manage.

    Inputs:
        connection: an open async connection.

    Returns:
        True if migrations should be skipped, False to proceed normally.

    FINDING-3.3. Keeping the deferred revision out of the default config's
    ``version_locations`` is what stops ``alembic upgrade head`` applying it — but it
    also meant the default config could no longer *recognise* a database that had
    legitimately applied it. Once someone ran the opt-in command, every subsequent
    ordinary deploy died with ``Can't locate revision identified by '0002'``. The
    deferral fix had made routine deployment fragile, which is a worse bug than the one
    it solved.

    The distinction that matters: a stored revision that belongs to the opt-in chain
    means the database is *ahead* of this config and there is nothing to do. A stored
    revision that belongs to neither chain is real corruption or a rollback to an
    older checkout, and still raises — this is deliberately not a blanket
    "ignore unknown revisions".
    """
    stored = await connection.scalar(text("SELECT to_regclass('alembic_version')"))
    if stored is None:
        return False  # fresh database, nothing recorded yet

    current = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    if current is None or current in _managed_revisions():
        return False

    if current in _opt_in_revisions():
        log.info(
            "database is at opt-in revision %s, which this configuration does not "
            "manage; nothing to upgrade. Use alembic.deferred.ini for that chain.",
            current,
        )
        return True

    # Unknown to both chains — let Alembic raise its own, more informative error.
    return False


async def _run_async() -> None:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        # The probe gets its own connection on purpose. Querying first on the
        # connection Alembic then migrates over leaves an open transaction, so
        # Alembic's own `begin_transaction()` nests inside it and everything is rolled
        # back when the connection closes — migrations report success and change
        # nothing.
        async with engine.connect() as probe:
            ahead = await _database_is_ahead_on_opt_in_chain(probe)

        async with engine.connect() as connection:
            # The skip decision is applied inside _do_run, which is the first place
            # that knows whether this is an upgrade. A downgrade must still reach
            # Alembic and fail on the revision it cannot resolve (FINDING-4.6).
            await connection.run_sync(_do_run, ahead_on_opt_in_chain=ahead)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    """Run migrations against a live async connection."""
    asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
