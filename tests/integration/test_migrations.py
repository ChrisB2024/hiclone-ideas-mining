from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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


@asynccontextmanager
async def _temporary_database() -> AsyncIterator[str]:
    base_url = _test_database_url()
    admin_url = _replace_database(base_url, "postgres")
    database_name = f"ideas_migration_{uuid4().hex}"
    database_url = _replace_database(base_url, database_name)

    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(f'CREATE DATABASE "{database_name}"')
        yield database_url
    finally:
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                database_name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            await admin.close()


async def _insert_signal(connection: asyncpg.Connection, suffix: str) -> None:
    await connection.execute(
        """
        WITH post AS (
          INSERT INTO raw_posts (
            source, external_id, url, author, body, score, created_at
          ) VALUES (
            'test', $1, $2, 'validator', 'pain body', 1, now()
          )
          RETURNING id
        )
        INSERT INTO enriched_signals (
          raw_post_id, vertical, pain_point, who_has_it,
          willingness_to_pay_signal, buildable_with_llm, relevance, model
        )
        SELECT id, 'insurance', 'manual re-keying', 'operator',
               'implied', true, 8, 'validator'
        FROM post
        """,
        f"migration-{suffix}",
        f"https://example.com/{suffix}",
    )


@pytest.mark.asyncio
async def test_default_alembic_remains_usable_after_deferred_indexes() -> None:
    async with _temporary_database() as database_url:
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
            tables = await connection.fetch(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename IN (
                    'raw_posts', 'clusters', 'enriched_signals',
                    'enrichment_batches', 'digests'
                  )
                """
            )
            assert {row["tablename"] for row in tables} == {
                "raw_posts",
                "clusters",
                "enriched_signals",
                "enrichment_batches",
                "digests",
            }
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


@pytest.mark.asyncio
async def test_default_alembic_still_rejects_an_unknown_revision() -> None:
    async with _temporary_database() as database_url:
        upgrade = await _run_alembic(database_url, "upgrade", "head")
        assert upgrade.returncode == 0, _result_output(upgrade)

        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(
                "UPDATE alembic_version SET version_num = 'not_a_real_revision'"
            )
        finally:
            await connection.close()

        rerun = await _run_alembic(database_url, "upgrade", "head")

        assert rerun.returncode != 0, _result_output(rerun)
        assert "Can't locate revision identified by 'not_a_real_revision'" in (
            rerun.stdout + rerun.stderr
        )


@pytest.mark.asyncio
async def test_default_downgrade_never_reports_success_without_downgrading() -> None:
    async with _temporary_database() as database_url:
        upgrade = await _run_alembic(database_url, "upgrade", "head")
        assert upgrade.returncode == 0, _result_output(upgrade)
        deferred = await _run_alembic(
            database_url,
            "-c",
            "alembic.deferred.ini",
            "upgrade",
            "head",
        )
        assert deferred.returncode == 0, _result_output(deferred)

        downgrade = await _run_alembic(database_url, "downgrade", "base")

        connection = await asyncpg.connect(database_url)
        try:
            schema_was_removed = not await connection.fetchval(
                "SELECT to_regclass('raw_posts') IS NOT NULL"
            )
        finally:
            await connection.close()

        assert downgrade.returncode != 0 or schema_was_removed, (
            "default Alembic exited successfully without performing the requested "
            "downgrade\n" + _result_output(downgrade)
        )


@pytest.mark.asyncio
async def test_deferred_migration_does_not_accept_a_wrong_existing_index() -> None:
    async with _temporary_database() as database_url:
        upgrade = await _run_alembic(database_url, "upgrade", "head")
        assert upgrade.returncode == 0, _result_output(upgrade)

        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(
                "CREATE INDEX ix_signals_embedding_hnsw "
                "ON enriched_signals USING hnsw (embedding vector_l2_ops)"
            )
        finally:
            await connection.close()

        deferred = await _run_alembic(
            database_url,
            "-c",
            "alembic.deferred.ini",
            "upgrade",
            "head",
        )
        assert deferred.returncode == 0, _result_output(deferred)

        connection = await asyncpg.connect(database_url)
        try:
            index = await connection.fetchrow(
                """
                SELECT opc.opcname, idx.indisvalid
                FROM pg_index idx
                JOIN pg_class cls ON cls.oid = idx.indexrelid
                JOIN pg_opclass opc ON opc.oid = idx.indclass[0]
                WHERE cls.relname = 'ix_signals_embedding_hnsw'
                """
            )
            assert index is not None
            assert index["indisvalid"] is True
            assert index["opcname"] == "vector_cosine_ops"
        finally:
            await connection.close()


@pytest.mark.asyncio
async def test_concurrent_hnsw_build_allows_a_new_pipeline_write() -> None:
    async with _temporary_database() as database_url:
        upgrade = await _run_alembic(database_url, "upgrade", "head")
        assert upgrade.returncode == 0, _result_output(upgrade)

        blocker = await asyncpg.connect(database_url)
        blocker_transaction = blocker.transaction()
        await blocker_transaction.start()
        await _insert_signal(blocker, "existing-writer")

        migration = asyncio.create_task(
            _run_alembic(
                database_url,
                "-c",
                "alembic.deferred.ini",
                "upgrade",
                "head",
            )
        )
        observer = await asyncpg.connect(database_url)
        writer_error: Exception | None = None
        saw_concurrent_build = False
        try:
            for _ in range(200):
                saw_concurrent_build = bool(
                    await observer.fetchval(
                        """
                        SELECT EXISTS (
                          SELECT 1
                          FROM pg_stat_activity
                          WHERE datname = current_database()
                            AND pid <> pg_backend_pid()
                            AND query LIKE 'CREATE INDEX CONCURRENTLY%'
                        )
                        """
                    )
                )
                if saw_concurrent_build:
                    break
                if migration.done():
                    break
                await asyncio.sleep(0.05)

            if saw_concurrent_build:
                writer = await asyncpg.connect(database_url)
                try:
                    await writer.execute("SET statement_timeout = '2s'")
                    await _insert_signal(writer, "new-writer")
                except Exception as exc:  # assertion below preserves cleanup
                    writer_error = exc
                finally:
                    await writer.close()
        finally:
            await blocker_transaction.rollback()
            await blocker.close()
            await observer.close()

        result = await migration
        assert saw_concurrent_build, _result_output(result)
        assert writer_error is None, (
            "CREATE INDEX CONCURRENTLY blocked a new pipeline write: "
            f"{writer_error!r}"
        )
        assert result.returncode == 0, _result_output(result)
