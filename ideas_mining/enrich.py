"""Haiku enrichment via the Batch API — the module that decides each post's vertical.

Split into two ARQ jobs (``submit_enrichment`` / ``collect_enrichment``) with the batch
id persisted between them. Never sleep in a poll loop inside a worker slot.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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
    """

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


def build_post_text(
    *, subsource: str | None, source: str, vertical_hint: str | None,
    title: str | None, body: str,
) -> str:
    """Render one post for the model.

    Format::

        Source: r/{subsource}   (or "Hacker News")
        Likely vertical: {vertical_hint or "unknown"}
        Title: {title or "(comment)"}

        {body}

    Truncate body to ~4000 chars — nothing past that changes the extraction, and the
    tail of a long rant is its least informative part.

    Security: this embeds untrusted forum text into an LLM prompt. Blast radius is one
    row: output is schema-constrained, so a hostile post can mislabel itself but cannot
    emit new fields, reach the DB, or affect other rows.
    """
    raise NotImplementedError("TODO")


async def select_unenriched(limit: int | None = None) -> list[object]:
    """Rows needing enrichment.

    ``SELECT rp.* FROM raw_posts rp
     LEFT JOIN enriched_signals es ON es.raw_post_id = rp.id
     WHERE rp.filter_state = 'passed' AND es.id IS NULL``

    That LEFT JOIN ... IS NULL **is** INV-4. Do not replace it with a state flag on
    raw_posts: a flag can drift (crash between the API call and the flag write), a join
    cannot. It also gives free retry — a failed batch result writes nothing, so the row
    is simply re-selected next tick.
    """
    raise NotImplementedError("TODO")


async def submit_enrichment() -> str | None:
    """Build and submit one Anthropic batch. Returns the batch id, or None if idle.

    Per request::

        model=settings.enrich_model, max_tokens=1024,
        system=[{"type": "text", "text": ENRICH_SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        output_config={"format": {"type": "json_schema",
                                  "schema": Enrichment.model_json_schema()}},
        custom_id=f"post-{post.id}"

    Note the system prompt only actually caches if it's >= ~1024 tokens; below that the
    marker is harmless but inert.

    Persist the returned batch id for ``collect_enrichment`` — do NOT poll here.
    """
    raise NotImplementedError("TODO")


async def collect_enrichment() -> dict[str, int]:
    """Poll pending batches; write results for those that ended. No-op if none.

    Three traps, all of which pass a naive test:

    1. ``.parse()`` does not exist for batches. The schema goes out via
       ``output_config.format`` and you validate the returned text yourself with
       ``Enrichment.model_validate_json``. The API guarantees shape; the local
       validation is what catches a truncated response.
    2. **Results return in arbitrary order.** Key on ``result.custom_id`` (parse the
       post id back out of ``post-{id}``), never on position.
    3. **Not every result succeeded.** ``result.result.type`` is succeeded | errored |
       canceled | expired. Write rows only for succeeded; log and skip the rest — they
       are picked up automatically next tick because the LEFT JOIN still finds them.

    Write with ``on_conflict_do_nothing(index_elements=["raw_post_id"])`` as belt and
    braces against a double-collect, and set ``model`` to the model ID used.

    Do NOT drop low-relevance or 'neither' rows: they're already paid for, thresholds
    belong in scoring, and the per-source 'neither' rate is the number that tells you
    whether the filter's keyword gate is working.
    """
    raise NotImplementedError("TODO")
