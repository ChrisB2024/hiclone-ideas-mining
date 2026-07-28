"""Hacker News ingest — Algolia HN API, no key, plain httpx."""

from __future__ import annotations

ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"


def strip_html(text: str) -> str:
    """Strip tags and unescape entities from HN comment HTML.

    HN comment bodies are HTML (``<p>``, ``&#x27;``, ``<a href>``). If this isn't done
    at ingest, the regex filter matches on markup and the LLM spends tokens on ``<p>``.

    Use ``html.unescape`` plus a minimal tag strip. Do NOT add an HTML parser
    dependency — explicitly ruled out in specs/02-chunk1-ingest.md.
    """
    raise NotImplementedError("TODO: html.unescape + minimal tag strip")


async def ingest_hn_query(query: str) -> dict[str, int]:
    """Fetch one query's window from Algolia, paginating to exhaustion.

    Params: ``query``, ``tags=(story,comment)``,
    ``numericFilters=created_at_i>{window_start_unix}``, ``hitsPerPage=100``.
    Paginate on ``page`` until ``page >= nbPages``.

    Invariant:
        ``vertical_hint`` is ALWAYS None on HN rows, regardless of which vertical's
        query found the hit. The query is a retrieval device, not a classification:
        "insurance agency" surfaces health-insurance startups, and both queries return
        the same PropTech thread. The model decides at enrichment.
    """
    raise NotImplementedError("TODO")


async def ingest_hackernews() -> dict[str, int]:
    """Run one pass per vertical using its ``hn_query``.

    The same ``objectID`` can come back from both passes — absorbed by the
    ``(source, external_id)`` upsert, which is exactly what that constraint is for.
    """
    raise NotImplementedError("TODO")
