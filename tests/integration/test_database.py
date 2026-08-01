from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest

import ideas_mining.cluster as cluster_module
from ideas_mining.cluster import assign_clusters, to_pgvector
from ideas_mining.config import settings
from ideas_mining.db.session import get_session
from ideas_mining.ingest.upsert import upsert_raw_posts
from ideas_mining.score import top_clusters


pytestmark = pytest.mark.integration

VECTOR = to_pgvector([1.0] + [0.0] * (settings.embedding_dim - 1))


async def _insert_raw(
    connection: asyncpg.Connection,
    external_id: str,
    *,
    author: str = "author-1",
    vertical_hint: str | None = None,
) -> int:
    return await connection.fetchval(
        """
        INSERT INTO raw_posts (
          source, external_id, parent_external_id, subsource, vertical_hint,
          url, author, title, body, score, created_at
        )
        VALUES (
          'reddit', $1::text, NULL, 'fixture', $2,
          'https://example.com/' || $1::text, $3, 'Fixture title',
          'We manually reconcile this workflow every day.', 10, now()
        )
        RETURNING id
        """,
        external_id,
        vertical_hint,
        author,
    )


async def _insert_signal(
    connection: asyncpg.Connection,
    raw_post_id: int,
    vertical: str,
    *,
    relevance: int = 8,
    embedding: str | None = None,
) -> int:
    return await connection.fetchval(
        """
        INSERT INTO enriched_signals (
          raw_post_id, vertical, pain_point, who_has_it, current_workaround,
          willingness_to_pay_signal, buildable_with_llm, relevance, model, embedding
        )
        VALUES (
          $1, $2, $3, 'operator', 'copy and paste',
          'implied', true, $4, 'fixture-model', $5::text::vector
        )
        RETURNING id
        """,
        raw_post_id,
        vertical,
        (
            "Agents manually re-key quote data between carrier portals."
            if vertical == "insurance"
            else "Agents manually re-key listing data between brokerage portals."
        ),
        relevance,
        embedding,
    )


@pytest.mark.asyncio
async def test_raw_posts_unique_source_external_id_is_database_enforced(
    db_conn: asyncpg.Connection,
) -> None:
    await _insert_raw(db_conn, "duplicate")

    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_raw(db_conn, "duplicate")


@pytest.mark.asyncio
async def test_enriched_signal_raw_post_id_is_database_unique(
    db_conn: asyncpg.Connection,
) -> None:
    raw_post_id = await _insert_raw(db_conn, "one-signal")
    await _insert_signal(db_conn, raw_post_id, "insurance")

    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_signal(db_conn, raw_post_id, "insurance")


@pytest.mark.parametrize(
    ("vertical", "relevance"),
    [
        ("healthcare", 8),
        ("insurance", 11),
        ("neither", 3),
    ],
)
@pytest.mark.asyncio
async def test_enriched_signal_checks_reject_invalid_raw_sql(
    db_conn: asyncpg.Connection,
    vertical: str,
    relevance: int,
) -> None:
    raw_post_id = await _insert_raw(db_conn, f"{vertical}-{relevance}")

    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_signal(
            db_conn,
            raw_post_id,
            vertical,
            relevance=relevance,
        )


@pytest.mark.asyncio
async def test_cluster_distinct_authors_cannot_exceed_members(
    db_conn: asyncpg.Connection,
) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await db_conn.execute(
            """
            INSERT INTO clusters (
              vertical, label, centroid, member_count, distinct_authors,
              first_seen_at, last_seen_at
            )
            VALUES (
              'insurance', 'fixture', $1::text::vector, 1, 2, now(), now()
            )
            """,
            VECTOR,
        )


@pytest.mark.asyncio
async def test_enrichment_batch_status_is_database_enforced(
    db_conn: asyncpg.Connection,
) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await db_conn.execute(
            """
            INSERT INTO enrichment_batches (batch_id, status, request_count)
            VALUES ('batch-invalid', 'stuck', 1)
            """
        )


@pytest.mark.asyncio
async def test_vertical_hint_null_is_accepted(
    db_conn: asyncpg.Connection,
) -> None:
    raw_post_id = await _insert_raw(db_conn, "null-hint", vertical_hint=None)

    assert raw_post_id > 0


@pytest.mark.asyncio
async def test_upsert_raw_posts_is_idempotent_across_runs(
    clean_database: asyncpg.Connection,
) -> None:
    del clean_database
    rows: list[dict[str, Any]] = [
        {
            "source": "reddit",
            "external_id": "upsert-1",
            "parent_external_id": None,
            "subsource": "fixture",
            "vertical_hint": "insurance",
            "url": "https://example.com/upsert-1",
            "author": "agent-1",
            "title": "Fixture",
            "body": "We manually reconcile this workflow every day.",
            "score": 5,
            "created_at": datetime.now(UTC),
        },
        {
            "source": "reddit",
            "external_id": "upsert-2",
            "parent_external_id": None,
            "subsource": "fixture",
            "vertical_hint": "insurance",
            "url": "https://example.com/upsert-2",
            "author": "agent-2",
            "title": "Fixture",
            "body": "We manually reconcile this workflow every day.",
            "score": 4,
            "created_at": datetime.now(UTC),
        },
    ]

    async with get_session() as session:
        first = await upsert_raw_posts(session, rows)
    async with get_session() as session:
        second = await upsert_raw_posts(session, rows)

    assert first == 2
    assert second == 0


@pytest.mark.asyncio
async def test_top_clusters_never_returns_the_other_vertical(
    clean_database: asyncpg.Connection,
) -> None:
    await clean_database.executemany(
        """
        INSERT INTO clusters (
          vertical, label, centroid, member_count, distinct_authors,
          first_seen_at, last_seen_at, score, scored_at
        )
        VALUES ($1, $2, $3::text::vector, 2, 2, now(), now(), $4, now())
        """,
        [
            ("insurance", "insurance pain", VECTOR, 1.0),
            ("real_estate", "higher cross-vertical score", VECTOR, 999.0),
        ],
    )

    ranked = await top_clusters("insurance", 5)

    assert [row["vertical"] for row in ranked] == ["insurance"]
    assert [row["label"] for row in ranked] == ["insurance pain"]


@pytest.mark.asyncio
async def test_cluster_partition_and_unpartitioned_merge_witness(
    clean_database: asyncpg.Connection,
) -> None:
    insurance_post = await _insert_raw(
        clean_database,
        "insurance-pain",
        author="insurance-agent",
        vertical_hint="insurance",
    )
    real_estate_post = await _insert_raw(
        clean_database,
        "real-estate-pain",
        author="real-estate-agent",
        vertical_hint="real_estate",
    )
    await _insert_signal(
        clean_database,
        insurance_post,
        "insurance",
        embedding=VECTOR,
    )
    real_estate_signal = await _insert_signal(
        clean_database,
        real_estate_post,
        "real_estate",
        embedding=VECTOR,
    )

    assert await assign_clusters() == {"created": 2, "assigned": 2}

    clusters = await clean_database.fetch(
        """
        SELECT c.id, c.vertical, c.member_count, c.distinct_authors,
               array_agg(DISTINCT es.vertical) AS member_verticals
        FROM clusters c
        JOIN enriched_signals es ON es.cluster_id = c.id
        GROUP BY c.id
        ORDER BY c.id
        """
    )
    assert len(clusters) == 2
    assert {
        (row["vertical"], tuple(row["member_verticals"]))
        for row in clusters
    } == {
        ("insurance", ("insurance",)),
        ("real_estate", ("real_estate",)),
    }
    assert all(row["member_count"] == 1 for row in clusters)
    assert all(row["distinct_authors"] == 1 for row in clusters)

    real_estate_cluster_id = next(
        row["id"] for row in clusters if row["vertical"] == "real_estate"
    )
    await clean_database.execute(
        "UPDATE enriched_signals SET cluster_id = NULL WHERE id = $1",
        real_estate_signal,
    )
    await clean_database.execute(
        "DELETE FROM clusters WHERE id = $1",
        real_estate_cluster_id,
    )

    unpartitioned = await clean_database.fetchrow(
        """
        SELECT id, vertical, 1 - (centroid <=> $1::text::vector) AS sim
        FROM clusters
        ORDER BY centroid <=> $1::text::vector, id
        LIMIT 1
        """,
        VECTOR,
    )
    assert unpartitioned is not None
    assert unpartitioned["vertical"] == "insurance"
    assert unpartitioned["sim"] >= settings.similarity_threshold


@pytest.mark.asyncio
async def test_recluster_all_clears_referenced_clusters_without_fk_failure(
    clean_database: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_post_id = await _insert_raw(
        clean_database,
        "recluster",
        vertical_hint="insurance",
    )
    await _insert_signal(
        clean_database,
        raw_post_id,
        "insurance",
        embedding=VECTOR,
    )
    embedding_before = await clean_database.fetchval(
        "SELECT embedding::text FROM enriched_signals WHERE raw_post_id = $1",
        raw_post_id,
    )
    assert await assign_clusters() == {"created": 1, "assigned": 1}

    async def do_not_reassign(ctx: object | None = None) -> dict[str, int]:
        del ctx
        return {"created": 0, "assigned": 0}

    monkeypatch.setattr(cluster_module, "assign_clusters", do_not_reassign)

    await cluster_module.recluster_all()

    assert await clean_database.fetchval("SELECT count(*) FROM clusters") == 0
    assert (
        await clean_database.fetchval(
            "SELECT count(*) FROM enriched_signals WHERE cluster_id IS NOT NULL"
        )
        == 0
    )
    assert (
        await clean_database.fetchval(
            "SELECT embedding::text FROM enriched_signals WHERE raw_post_id = $1",
            raw_post_id,
        )
        == embedding_before
    )
