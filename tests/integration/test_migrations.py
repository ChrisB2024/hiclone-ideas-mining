from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest


pytestmark = pytest.mark.integration

ROOT = Path(__file__).parents[2]
ALEMBIC_TIMEOUT_SECONDS = 120


def _test_database_url() -> str:
    url = os.getenv("IDEAS_MINING_TEST_DATABASE_URL")
    if not url:
        pytest.skip("IDEAS_MINING_TEST_DATABASE_URL is not configured")
    return url


def _replace_database(url: str, database: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(parsed._replace(path=f"/{database}"))


async def _run_alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "DATABASE_URL": database_url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        ),
    }
    return await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=ALEMBIC_TIMEOUT_SECONDS,
    )


def _result_output(result: subprocess.CompletedProcess[str]) -> str:
    return f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


@pytest.mark.asyncio
async def test_default_alembic_remains_usable_after_deferred_indexes() -> None:
    base_url = _test_database_url()
    admin_url = _replace_database(base_url, "postgres")
    database_name = f"ideas_migration_{uuid4().hex}"
    database_url = _replace_database(base_url, database_name)

    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(f'CREATE DATABASE "{database_name}"')
        try:
            default_upgrade = await _run_alembic(database_url, "upgrade", "head")
            assert default_upgrade.returncode == 0, _result_output(default_upgrade)

            connection = await asyncpg.connect(database_url)
            try:
                assert await connection.fetchval(
                    "SELECT version_num FROM alembic_version"
                ) == "0001"
                assert await connection.fetchval(
                    """
                    SELECT count(*)
                    FROM pg_indexes
                    WHERE indexname IN (
                      'ix_signals_embedding_hnsw',
                      'ix_clusters_centroid_hnsw'
                    )
                    """
                ) == 0
            finally:
                await connection.close()

            deferred_upgrade = await _run_alembic(
                database_url,
                "-c",
                "alembic.deferred.ini",
                "upgrade",
                "head",
            )
            assert deferred_upgrade.returncode == 0, _result_output(deferred_upgrade)

            connection = await asyncpg.connect(database_url)
            try:
                assert await connection.fetchval(
                    "SELECT version_num FROM alembic_version"
                ) == "0002"
                assert await connection.fetchval(
                    """
                    SELECT count(*)
                    FROM pg_indexes
                    WHERE indexname IN (
                      'ix_signals_embedding_hnsw',
                      'ix_clusters_centroid_hnsw'
                    )
                    """
                ) == 2
            finally:
                await connection.close()

            later_default_upgrade = await _run_alembic(
                database_url,
                "upgrade",
                "head",
            )
            assert later_default_upgrade.returncode == 0, (
                "The opt-in migration left the database at a revision that the "
                "normal deployment configuration cannot resolve.\n"
                + _result_output(later_default_upgrade)
            )
        finally:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                database_name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
    finally:
        await admin.close()
