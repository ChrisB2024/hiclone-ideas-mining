"""Haiku enrichment via the Batch API — the module that decides each post's vertical.

Split into two ARQ jobs (``submit_enrichment`` / ``collect_enrichment``) with the batch
id persisted between them. Never sleep in a poll loop inside a worker slot.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from ideas_mining.config import POST_TEXT_MAX_CHARS, settings
from ideas_mining.db.models import EnrichedSignal, EnrichmentBatch, RawPost
from ideas_mining.db.session import get_session

log = logging.getLogger(__name__)

ENRICH_SYSTEM_PROMPT = """\
You extract structured operational pain points from forum posts for a founder who
builds LLM tools for two industries: insurance and real estate.

"Likely vertical" in the input is a hint from which forum the post came from. It is
often right and sometimes wrong — a post in an insurance forum can be about real
estate, or about neither. Judge from the post text.

Return only the structured fields requested.
"""
# ^ That middle paragraph is load-bearing. Without it the model treats the hint as a
#   label, the `vertical` field degenerates into a copy of the subreddit config, and
#   shared sources become the only posts actually being classified.

#: The custom_id prefix. Results come back in arbitrary order and are re-keyed on this.
_CUSTOM_ID_PREFIX = "post-"


class Enrichment(BaseModel):
    """The extraction contract. Used for BOTH the API constraint and local validation.

    Field descriptions are not documentation — they are the prompt. Two of them carry
    most of the system's quality:

    ``pain_point`` is what clustering runs on. If the model writes narrative sentences,
    semantically identical pains land far apart in vector space and INV-1 breaks. The
    "near-identical sentences" instruction is what makes clustering possible at all.
    The vocabulary clause is the two-vertical fix: left alone the model neutralizes
    domain nouns into generic ones, which is exactly the sentence that makes an
    insurance pain and a real-estate pain embed on top of each other.

    ``vertical``'s consumer-vs-practitioner clause matters more than it reads.
    r/Insurance and r/RealEstate are dominated by customers — a denied claim, confusing
    closing costs. Real friction, passes the pain filter, useless: the person with the
    pain isn't someone you can sell software to.

    ``extra="forbid"`` does double duty: it makes ``model_json_schema()`` emit
    ``additionalProperties: false``, which the API's structured-output mode requires,
    and it rejects a response that invents a field.
    """

    model_config = ConfigDict(extra="forbid")

    vertical: Literal["insurance", "real_estate", "neither"] = Field(
        description=(
            "Which industry's day-to-day operations this pain belongs to. 'insurance' "
            "covers agencies, brokerages, carriers, underwriting, claims. "
            "'real_estate' covers residential and commercial brokerage, property "
            "management, and transactions. Use 'neither' for any other industry, and "
            "for consumer complaints about buying insurance or a house — we want the "
            "practitioner's pain, not the customer's. A pain that genuinely applies to "
            "both industries equally is 'neither'."
        )
    )
    pain_point: str = Field(
        description=(
            "The specific operational pain in one sentence, phrased generically so "
            "that two people describing the same pain produce near-identical "
            "sentences. Name the task and why it hurts. No names, companies, or story "
            "details. Keep the industry's own vocabulary — write 'quote' for insurance "
            "and 'listing' for real estate rather than a neutral word covering both."
        )
    )
    who_has_it: str = Field(
        description=(
            "The role or business type that has this pain, e.g. 'independent P&C "
            "agent', 'residential listing agent', 'property manager'."
        )
    )
    current_workaround: str | None = Field(
        description="What they do today instead. Null if the post doesn't say."
    )
    willingness_to_pay_signal: Literal["none", "implied", "explicit"]
    buildable_with_llm: bool = Field(
        description="Could a small LLM-powered tool plausibly solve most of this?"
    )
    relevance: int = Field(
        ge=0, le=10,
        description=(
            "How central this pain is to the vertical you assigned. 0 if vertical is "
            "'neither'."
        ),
    )

    @model_validator(mode="after")
    def _neither_scores_zero(self) -> Enrichment:
        """Reject a 'neither' row carrying a nonzero relevance (FINDING-1.7).

        Invariant: mirrors the ``ck_signals_neither_zero`` CHECK constraint in the
        database, so the row is rejected at the boundary rather than at INSERT.

        Relevance is scored *relative to the assigned vertical*. A row that belongs to
        no vertical has nothing to be relevant to, so any nonzero value there is a
        model error — and an expensive one, because relevance multiplies into the final
        score and a confidently-rated 'neither' row would drag noise into the ranking if
        the exclusion were ever loosened.

        Failing here rather than at the database means the whole batch entry is logged
        and skipped, and the post is re-selected next tick by the LEFT JOIN — instead of
        an IntegrityError aborting the transaction that was writing its siblings.
        """
        if self.vertical == "neither" and self.relevance != 0:
            raise ValueError("relevance must be 0 when vertical is 'neither'")
        return self


def build_post_text(
    *, subsource: str | None, source: str, vertical_hint: str | None,
    title: str | None, body: str,
) -> str:
    """Render one post for the model.

    Inputs:
        subsource: subreddit name, or "hn".
        source: "reddit" | "hackernews".
        vertical_hint: the subreddit's configured vertical, or None.
        title: submission title; None for comments.
        body: the post text.

    Returns:
        The prompt body::

            Source: r/{subsource}   (or "Hacker News")
            Likely vertical: {vertical_hint or "unknown"}
            Title: {title or "(comment)"}

            {body}

    Body is truncated to POST_TEXT_MAX_CHARS — nothing past that changes the extraction,
    and the tail of a long rant is its least informative part.

    Security: this embeds untrusted forum text into an LLM prompt. Blast radius is one
    row: output is schema-constrained, so a hostile post can mislabel itself but cannot
    emit new fields, reach the DB, or affect other rows. The post text is placed in the
    user turn only; the system prompt is never assembled from post content.
    """
    if source == "reddit" and subsource:
        origin = f"r/{subsource}"
    elif source == "reddit":
        origin = "Reddit"
    else:
        origin = "Hacker News"

    return (
        f"Source: {origin}\n"
        f"Likely vertical: {vertical_hint or 'unknown'}\n"
        f"Title: {title or '(comment)'}\n"
        f"\n"
        f"{body[:POST_TEXT_MAX_CHARS]}"
    )


async def select_unenriched(limit: int | None = None) -> list[RawPost]:
    """Rows needing enrichment.

    Inputs:
        limit: max rows to return. None means no cap.

    Returns:
        RawPost rows, oldest id first.

    ``SELECT rp.* FROM raw_posts rp
     LEFT JOIN enriched_signals es ON es.raw_post_id = rp.id
     WHERE rp.filter_state = 'passed' AND es.id IS NULL``

    That LEFT JOIN ... IS NULL **is** INV-4. Do not replace it with a state flag on
    raw_posts: a flag can drift (crash between the API call and the flag write), a join
    cannot. It also gives free retry — a failed batch result writes nothing, so the row
    is simply re-selected next tick.
    """
    stmt = (
        select(RawPost)
        .outerjoin(EnrichedSignal, EnrichedSignal.raw_post_id == RawPost.id)
        .where(RawPost.filter_state == "passed", EnrichedSignal.id.is_(None))
        .order_by(RawPost.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    async with get_session() as session:
        return list((await session.execute(stmt)).scalars().all())


def _client() -> AsyncAnthropic:
    """Anthropic client. Key comes from settings only, and is never logged."""
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


async def submit_enrichment(ctx: dict[str, object] | None = None) -> str | None:
    """Build and submit one Anthropic batch. Returns the batch id, or None if idle.

    Inputs:
        ctx: ARQ job context, unused (FINDING-1.6).

    Returns:
        The Anthropic batch id, or None when nothing is waiting.

    Per request::

        model=settings.enrich_model, max_tokens=1024,
        system=[{"type": "text", "text": ENRICH_SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        output_config={"format": {"type": "json_schema",
                                  "schema": Enrichment.model_json_schema()}},
        custom_id=f"post-{post.id}"

    The system prompt only actually caches if it's >= ~1024 tokens; below that the
    marker is harmless but inert.

    The batch id is persisted to ``enrichment_batches`` for ``collect_enrichment`` —
    this function does NOT poll. Blocking a worker slot for up to 24h waiting on a batch
    would stall every other cron job on the same worker.

    Idempotency: if the previous submit's rows haven't been collected yet, they are
    still selected here — the LEFT JOIN doesn't know about in-flight batches. That would
    double-spend, so a submit is skipped entirely while any batch is still pending.
    """
    async with get_session() as session:
        pending = (
            await session.execute(
                select(EnrichmentBatch.batch_id).where(
                    EnrichmentBatch.status == "pending"
                )
            )
        ).scalars().all()

    if pending:
        log.info("submit_enrichment: %d batch(es) still pending, skipping", len(pending))
        return None

    posts = await select_unenriched(limit=settings.enrich_batch_size)
    if not posts:
        return None

    requests = [
        {
            "custom_id": f"{_CUSTOM_ID_PREFIX}{post.id}",
            "params": {
                "model": settings.enrich_model,
                "max_tokens": 1024,
                "system": [{
                    "type": "text",
                    "text": ENRICH_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                "messages": [{
                    "role": "user",
                    "content": build_post_text(
                        subsource=post.subsource,
                        source=post.source,
                        vertical_hint=post.vertical_hint,
                        title=post.title,
                        body=post.body,
                    ),
                }],
                "output_config": {
                    "format": {
                        "type": "json_schema",
                        "schema": Enrichment.model_json_schema(),
                    }
                },
            },
        }
        for post in posts
    ]

    client = _client()
    try:
        batch = await client.messages.batches.create(requests=requests)
    finally:
        await client.close()

    async with get_session() as session:
        session.add(
            EnrichmentBatch(
                batch_id=batch.id, status="pending", request_count=len(requests)
            )
        )

    log.info("submitted batch %s with %d requests", batch.id, len(requests))
    return batch.id


async def collect_enrichment(ctx: dict[str, object] | None = None) -> dict[str, int]:
    """Poll pending batches; write results for those that ended. No-op if none.

    Inputs:
        ctx: ARQ job context, unused (FINDING-1.6).

    Returns:
        {"batches": int, "written": int, "invalid": int, "not_succeeded": int}

    Three traps, all of which pass a naive test:

    1. ``.parse()`` does not exist for batches. The schema goes out via
       ``output_config.format`` and the returned text is validated here with
       ``Enrichment.model_validate_json``. The API guarantees shape; the local
       validation is what catches a truncated response.
    2. **Results return in arbitrary order.** Keyed on ``result.custom_id`` (the post id
       is parsed back out of ``post-{id}``), never on position.
    3. **Not every result succeeded.** ``result.result.type`` is succeeded | errored |
       canceled | expired. Rows are written only for succeeded; the rest are logged and
       skipped — they are picked up automatically next tick because the LEFT JOIN still
       finds them.

    Writes use ``on_conflict_do_nothing(index_elements=["raw_post_id"])`` as belt and
    braces against a double-collect, and set ``model`` to the model ID used.

    Low-relevance and 'neither' rows are NOT dropped: they're already paid for,
    thresholds belong in scoring, and the per-source 'neither' rate is the number that
    tells you whether the filter's keyword gate is working.

    Security: model output is validated against ``Enrichment`` before it reaches the
    database, so a prompt-injected response can produce a wrong label but not a wrong
    shape. A row that fails validation is dropped, not coerced.
    """
    totals = {"batches": 0, "written": 0, "invalid": 0, "not_succeeded": 0}

    async with get_session() as session:
        pending = list(
            (
                await session.execute(
                    select(EnrichmentBatch).where(EnrichmentBatch.status == "pending")
                )
            ).scalars().all()
        )

    if not pending:
        return totals

    client = _client()
    try:
        for tracked in pending:
            batch = await client.messages.batches.retrieve(tracked.batch_id)
            if batch.processing_status != "ended":
                continue

            totals["batches"] += 1
            rows: list[dict[str, object]] = []

            async for entry in await client.messages.batches.results(tracked.batch_id):
                if entry.result.type != "succeeded":
                    log.warning(
                        "batch %s: %s -> %s",
                        tracked.batch_id, entry.custom_id, entry.result.type,
                    )
                    totals["not_succeeded"] += 1
                    continue

                raw_post_id = int(entry.custom_id.removeprefix(_CUSTOM_ID_PREFIX))
                text = "".join(
                    block.text
                    for block in entry.result.message.content
                    if getattr(block, "type", None) == "text"
                )

                try:
                    enrichment = Enrichment.model_validate_json(text)
                except ValueError as exc:
                    # Truncation, or a model output that violated the neither/relevance
                    # rule. Dropping it costs one Haiku call; the post is re-selected on
                    # the next tick because no row was written.
                    log.warning(
                        "batch %s: post %d failed validation: %s",
                        tracked.batch_id, raw_post_id, exc,
                    )
                    totals["invalid"] += 1
                    continue

                rows.append({
                    "raw_post_id": raw_post_id,
                    "model": settings.enrich_model,
                    **enrichment.model_dump(),
                })

            async with get_session() as session:
                if rows:
                    result = await session.execute(
                        insert(EnrichedSignal)
                        .values(rows)
                        .on_conflict_do_nothing(index_elements=["raw_post_id"])
                        .returning(EnrichedSignal.id)
                    )
                    totals["written"] += len(result.scalars().all())

                await session.execute(
                    update(EnrichmentBatch)
                    .where(EnrichmentBatch.id == tracked.id)
                    .values(status="collected", collected_at=datetime.now(UTC))
                )
    finally:
        await client.close()

    log.info("collect_enrichment: %s", totals)
    return totals
