"""Shared idempotent upsert. Used by both ingest sources.

This one function carries INV-3: re-running any window inserts zero rows.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ideas_mining.db.models import RawPost


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse rows sharing a ``(source, external_id)`` key, keeping the first.

    Inputs:
        rows: raw_posts-shaped dicts, possibly containing intra-batch duplicates.

    Returns:
        The same rows in order, with later duplicates of a key dropped.

    Why this exists: Reddit's ``new()`` listing can return the same submission twice
    across pagination, and the two HN passes both surface shared PropTech threads. The
    database would absorb them, but only after the statement has been built — deduping
    in Python keeps the "inserted" count honest and keeps the statement smaller.
    """
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        key = (row["source"], row["external_id"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


async def upsert_raw_posts(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Insert rows, skipping any that already exist. Returns the number inserted.

    Inputs:
        session: an open async session.
        rows: dicts matching RawPost columns. May contain duplicates of stored rows.

    Returns:
        Count of rows actually inserted (len(rows) minus conflicts). This is the number
        the idempotency acceptance test asserts on — ``len(rows)`` is the *fetched*
        count, and the difference between the two is the whole point.

    Invariants:
        INV-3 — idempotent. Calling this twice with the same rows inserts nothing the
        second time.

    Implementation notes:
        * DO NOTHING, never DO UPDATE. Scores and bodies drift after posting; we
          snapshot at first sight and never revise, because revising would require
          re-running enrichment to stay consistent and that breaks INV-4.
        * The whole list goes into ONE statement. Per-row statements turn a 2-second run
          into a 2-minute one.
        * ``RETURNING id`` is what makes the count true; ``rowcount`` after
          ON CONFLICT DO NOTHING is driver-dependent.

    Security: every value here is untrusted forum text. It is bound as a parameter, never
    interpolated, and is stored verbatim without being evaluated.
    """
    if not rows:
        return 0

    unique = dedupe_rows(rows)
    stmt = (
        insert(RawPost)
        .values(unique)
        .on_conflict_do_nothing(index_elements=["source", "external_id"])
        .returning(RawPost.id)
    )
    result = await session.execute(stmt)
    return len(result.scalars().all())
