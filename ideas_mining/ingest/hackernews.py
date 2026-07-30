"""Hacker News ingest — Algolia HN API, no key, plain httpx."""

from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ideas_mining.config import VERTICALS, settings
from ideas_mining.db.session import get_session
from ideas_mining.ingest.upsert import upsert_raw_posts

log = logging.getLogger(__name__)

ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"

SOURCE = "hackernews"
HITS_PER_PAGE = 100
#: Algolia caps pagination anyway; this is a guard against an unbounded loop if the API
#: ever reports a nbPages it won't actually serve.
MAX_PAGES = 20

#: Deliberately linear: one character class, one bounded scan, no nested quantifiers.
#: This runs over untrusted text, so a pattern like `<(.|\n)*?>` — which backtracks
#: catastrophically on a body full of unclosed angle brackets — is not acceptable here.
_TAG_RE = re.compile(r"<[^>]*>")


def strip_html(text: str) -> str:
    """Strip tags and unescape entities from HN comment HTML.

    Inputs:
        text: an HN ``comment_text`` or ``story_text`` fragment.

    Returns:
        Plain text with tags removed and entities decoded.

    HN comment bodies are HTML (``<p>``, ``&#x27;``, ``<a href>``). If this isn't done
    at ingest, the regex filter matches on markup and the enrichment model spends tokens
    on ``<p>``. Paragraph tags become blank lines rather than vanishing, so sentences
    that were in separate paragraphs don't get glued together into a false match.

    No HTML parser dependency — explicitly ruled out in specs/02-chunk1-ingest.md.

    Security: output is stored and shown, never rendered as HTML. Stripping is for
    signal quality, not for sanitisation, and must not be relied on as an XSS defence.
    """
    if not text:
        return ""
    with_breaks = re.sub(r"</p>|<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    return html.unescape(_TAG_RE.sub("", with_breaks)).strip()


def _parse_created_at(value: Any) -> datetime | None:
    """Parse Algolia's ISO 8601 ``created_at``, or None if it is unusable.

    Inputs:
        value: whatever the API returned — normally a string, but missing, null, or a
            number in practice.

    Returns:
        An aware datetime, or None.

    FINDING-2.10: this used to be an unguarded
    ``datetime.fromisoformat(hit["created_at"].replace(...))``. A hit with no
    ``created_at`` raised KeyError, a null one AttributeError, and a numeric one
    TypeError — each of which escaped ``ingest_hn_query`` and discarded every hit
    already collected in that pass, including the ones that were fine. A third party's
    malformed field should cost one row, not a run.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # A naive timestamp here would be read as server-local time in a timestamptz
    # column and drift recency scoring, so treat it as UTC rather than trusting it.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _hit_to_row(hit: dict[str, Any]) -> dict[str, Any] | None:
    """Map one Algolia hit to a raw_posts row, or None if it is unusable.

    Returns None — never raises — for a hit with no id, no text at all, or an
    unparseable timestamp. The caller drops it and keeps going.

    Invariant: ``vertical_hint`` is ALWAYS None on HN rows. See ``ingest_hn_query``.
    """
    object_id = hit.get("objectID")
    if not object_id:
        return None

    body = strip_html(hit.get("story_text") or hit.get("comment_text") or "")
    title = hit.get("title")
    if not body and not title:
        # A link-only story with no text and no title has nothing to filter or enrich.
        return None

    created_at = _parse_created_at(hit.get("created_at"))
    if created_at is None:
        log.warning("hn hit %s has unusable created_at, skipping", object_id)
        return None

    return {
        "source": SOURCE,
        "external_id": str(object_id),
        "parent_external_id": (
            str(hit["story_id"]) if hit.get("story_id") else None
        ),
        "subsource": "hn",
        "vertical_hint": None,
        "url": f"https://news.ycombinator.com/item?id={object_id}",
        "author": hit.get("author") or None,
        "title": title,
        "body": body,
        "score": hit.get("points") or 0,
        "created_at": created_at,
    }


async def ingest_hn_query(query: str) -> dict[str, int]:
    """Fetch one query's window from Algolia, paginating to exhaustion.

    Inputs:
        query: an OR-joined phrase query from ``VERTICALS[v]["hn_query"]``.

    Returns:
        {"fetched": int, "inserted": int, "skipped": int}

    Invariant:
        ``vertical_hint`` is ALWAYS None on HN rows, regardless of which vertical's
        query found the hit. The query is a retrieval device, not a classification:
        "insurance agency" surfaces health-insurance startups, and both queries return
        the same PropTech thread. The model decides at enrichment.

    Security: the query string comes from our own config, not from user input, and is
    passed as a URL parameter rather than concatenated.
    """
    window_start = int(
        (datetime.now(UTC) - timedelta(hours=settings.ingest_lookback_hours)).timestamp()
    )
    rows: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        page = 0
        while page < MAX_PAGES:
            response = await client.get(
                ALGOLIA_SEARCH_URL,
                params={
                    "query": query,
                    "tags": "(story,comment)",
                    "numericFilters": f"created_at_i>{window_start}",
                    "hitsPerPage": HITS_PER_PAGE,
                    "page": page,
                },
            )
            response.raise_for_status()
            payload = response.json()

            for hit in payload.get("hits", []):
                row = _hit_to_row(hit)
                if row is not None:
                    rows.append(row)

            page += 1
            if page >= payload.get("nbPages", 0):
                break

    async with get_session() as session:
        inserted = await upsert_raw_posts(session, rows)

    return {"fetched": len(rows), "inserted": inserted, "skipped": len(rows) - inserted}


async def ingest_hackernews() -> dict[str, int]:
    """Run one pass per vertical using its ``hn_query``.

    Returns:
        {"fetched": int, "inserted": int, "skipped": int, "failed_queries": int}

    The same ``objectID`` can come back from both passes — absorbed by the
    ``(source, external_id)`` upsert, which is exactly what that constraint is for. The
    second pass counts it as skipped, not as an error.

    One query failing (Algolia rate limit, transient 5xx) must not cancel the other, so
    the passes gather with ``return_exceptions=True`` like the Reddit side.
    """
    queries = [(name, cfg["hn_query"]) for name, cfg in VERTICALS.items()]
    results = await asyncio.gather(
        *(ingest_hn_query(str(query)) for _, query in queries),
        return_exceptions=True,
    )

    totals = {"fetched": 0, "inserted": 0, "skipped": 0, "failed_queries": 0}
    for (name, _), result in zip(queries, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("hn ingest failed for %s query: %s", name, result)
            totals["failed_queries"] += 1
            continue
        log.info(
            "hn query=%s fetched=%d inserted=%d",
            name, result["fetched"], result["inserted"],
        )
        for key in ("fetched", "inserted", "skipped"):
            totals[key] += result[key]

    return totals
