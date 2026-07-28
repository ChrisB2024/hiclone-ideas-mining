"""Shared idempotent upsert. Used by both ingest sources.

This one function carries INV-3: re-running any window inserts zero rows.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


async def upsert_raw_posts(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Insert rows, skipping any that already exist. Returns the number inserted.

    Inputs:
        session: an open async session.
        rows: dicts matching RawPost columns. May contain duplicates of stored rows.

    Returns:
        Count of rows actually inserted (len(rows) minus conflicts).

    Invariants:
        INV-3 — idempotent. Calling this twice with the same rows inserts nothing the
        second time.

    Implementation notes:
        * ``postgresql.insert(RawPost).on_conflict_do_nothing(index_elements=["source",
          "external_id"])``.
        * DO NOTHING, never DO UPDATE. Scores and bodies drift after posting; we
          snapshot at first sight and never revise, because revising would require
          re-running enrichment to stay consistent and that breaks INV-4.
        * Batch the whole list into ONE statement. Per-row statements turn a 2-second
          run into a 2-minute one.
        * Use ``RETURNING id`` (or ``result.rowcount``) to get the true inserted count
          — len(rows) is the fetched count, not the inserted count, and the difference
          is exactly what the idempotency test asserts on.
    """
    raise NotImplementedError("TODO: batched ON CONFLICT DO NOTHING")
