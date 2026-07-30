from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio

from ideas_mining.db.session import dispose_engine


def _database_url() -> str:
    url = os.getenv("IDEAS_MINING_TEST_DATABASE_URL")
    if not url:
        pytest.skip("IDEAS_MINING_TEST_DATABASE_URL is not configured")
    return url


@pytest_asyncio.fixture
async def db_conn() -> AsyncIterator[asyncpg.Connection]:
    connection = await asyncpg.connect(_database_url())
    transaction = connection.transaction()
    await transaction.start()
    try:
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def clean_database() -> AsyncIterator[asyncpg.Connection]:
    connection = await asyncpg.connect(_database_url())
    await connection.execute(
        "TRUNCATE digests, enrichment_batches, enriched_signals, clusters, "
        "raw_posts RESTART IDENTITY CASCADE"
    )
    try:
        yield connection
    finally:
        await connection.execute(
            "TRUNCATE digests, enrichment_batches, enriched_signals, clusters, "
            "raw_posts RESTART IDENTITY CASCADE"
        )
        await connection.close()


@pytest_asyncio.fixture(autouse=True)
async def dispose_application_engine_after_test() -> AsyncIterator[None]:
    yield
    await dispose_engine()
