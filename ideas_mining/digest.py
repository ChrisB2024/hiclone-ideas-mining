"""Weekly digest — the product.

One Sonnet call over the top clusters in both verticals, rendered to markdown,
persisted, then delivered.
"""

from __future__ import annotations

import asyncio
import logging
import re
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from anthropic import AsyncAnthropic
from sqlalchemy import select, text, update

from ideas_mining.config import (
    DIGEST_CLUSTERS_PER_VERTICAL, DIGEST_EXCERPT_CHARS, DIGEST_MEMBERS_PER_CLUSTER,
    VERTICAL_NAMES, settings,
)
from ideas_mining.db.models import Digest
from ideas_mining.db.session import get_session
from ideas_mining.score import top_clusters

log = logging.getLogger(__name__)

DIGEST_SYSTEM_PROMPT = """\
You are briefing a founder who builds LLM tools for two verticals: insurance and real
estate.

Keep the two sections separate and in that order. For each cluster write: the pain in
one line, who has it, what they do today, the strongest verbatim quote with its link,
and one sentence on what a tool would do.

Never merge a pain from one section into the other, and never describe a cluster as
applying to both industries inside a section.

If — and only if — the same underlying pain appears in both sections, add a final
"## BOTH" section of at most two lines naming it. Omit the section entirely otherwise.

Every quote must include its link.

Terse. No preamble, no "here's your digest", no closing summary. Markdown.
"""
# The two vertical rules are contract, not style. With ten clusters from two
# structurally similar industries the model will otherwise editorialize across the
# boundary — quietly undoing the partition the whole pipeline maintains, at the last
# step, in the one artifact that actually gets read. The "only if" and the line cap on
# BOTH are what stop it being written every week with a strained analogy in it.

#: The 3 most representative members of a cluster: ordered by the post's own score, a
#: proxy for "well articulated and agreed with".
CLUSTER_MEMBERS_SQL = """
SELECT es.pain_point,
       es.who_has_it,
       es.current_workaround,
       es.willingness_to_pay_signal,
       rp.url,
       rp.score,
       left(rp.body, :excerpt_chars) AS excerpt
FROM enriched_signals es
JOIN raw_posts rp ON rp.id = es.raw_post_id
WHERE es.cluster_id = :cluster_id
ORDER BY rp.score DESC
LIMIT :n
"""

#: Header facts. Computed in SQL, never asked of the model.
PERIOD_STATS_SQL = """
SELECT es.vertical                              AS vertical,
       count(*)                                 AS new_signals,
       count(DISTINCT rp.author)                AS distinct_authors,
       count(DISTINCT es.cluster_id)            AS clusters
FROM enriched_signals es
JOIN raw_posts rp ON rp.id = es.raw_post_id
WHERE rp.created_at >= :period_start AND rp.created_at < :period_end
GROUP BY es.vertical
"""


def heading_for(vertical: str) -> str:
    """Render a vertical name as its digest section heading.

    Inputs:
        vertical: a key of VERTICALS, e.g. "real_estate".

    Returns:
        "## REAL ESTATE".

    Derived rather than hardcoded so no module outside config.py names a vertical.
    """
    return f"## {vertical.replace('_', ' ').upper()}"


async def gather_digest_input(
    period_start: datetime, period_end: datetime
) -> tuple[str, list[int]]:
    """Build the plain-text block for the model, and the cluster ids it was built from.

    Inputs:
        period_start, period_end: the reporting window, used only for the label — the
            clusters themselves are ranked on all-time score with a recency term, not
            filtered to the window.

    Returns:
        ``(prompt_text, cluster_ids)`` — the user-turn text, and the ids of the
        clusters actually in it, insurance in rank order then real estate.

    FINDING-2.8: the ids are returned rather than re-queried by the caller. Ranking
    twice meant ``score_clusters`` running in between could change the result, and the
    ``digests`` row would then archive a different set of clusters than the prose it
    stores describes — with nothing to reveal the mismatch, since both lists look
    perfectly reasonable on their own.

    Top ``DIGEST_CLUSTERS_PER_VERTICAL`` clusters per vertical via
    ``score.top_clusters``, grouped under ``## INSURANCE`` / ``## REAL ESTATE``.

    Per cluster: label, score, distinct_authors, member_count, last_seen_at, plus the
    3 most representative members ``ORDER BY raw_posts.score DESC``, each with
    pain_point, who_has_it, current_workaround, WTP signal, a <=300-char excerpt, and
    the url.

    If a vertical has fewer than N qualifying clusters, pass what exists and say so in
    the block, e.g. "(only 2 clusters cleared the bar this week)". Do NOT backfill from
    the other vertical to reach 10.

    No JSON, no tool use — one call with a fixed shape.

    Security: excerpts are untrusted forum text. They enter the user turn only, and the
    system prompt is a constant — a hostile excerpt can influence the prose it appears
    in but cannot reach the database or the delivery path.
    """
    parts: list[str] = [
        f"Reporting period: {period_start:%Y-%m-%d} to {period_end:%Y-%m-%d}",
        "",
    ]
    cluster_ids: list[int] = []

    async with get_session() as session:
        for vertical in VERTICAL_NAMES:
            clusters = await top_clusters(vertical, DIGEST_CLUSTERS_PER_VERTICAL)
            cluster_ids.extend(int(cluster["id"]) for cluster in clusters)
            parts.append(heading_for(vertical))

            if not clusters:
                parts.append("(no clusters cleared the bar this week)")
                parts.append("")
                continue

            if len(clusters) < DIGEST_CLUSTERS_PER_VERTICAL:
                parts.append(
                    f"(only {len(clusters)} clusters cleared the bar this week)"
                )

            for rank, cluster in enumerate(clusters, start=1):
                parts.append(
                    f"\n{rank}. {cluster['label']}\n"
                    f"   score={cluster['score']:.3f} "
                    f"distinct_authors={cluster['distinct_authors']} "
                    f"member_count={cluster['member_count']} "
                    f"last_seen={cluster['last_seen_at']:%Y-%m-%d}"
                )

                members = (
                    await session.execute(
                        text(CLUSTER_MEMBERS_SQL),
                        {
                            "cluster_id": cluster["id"],
                            "excerpt_chars": DIGEST_EXCERPT_CHARS,
                            "n": DIGEST_MEMBERS_PER_CLUSTER,
                        },
                    )
                ).mappings().all()

                for member in members:
                    workaround = member["current_workaround"] or "not stated"
                    excerpt = " ".join((member["excerpt"] or "").split())
                    parts.append(
                        f"   - pain: {member['pain_point']}\n"
                        f"     who: {member['who_has_it']}\n"
                        f"     today: {workaround}\n"
                        f"     wtp: {member['willingness_to_pay_signal']}\n"
                        f"     excerpt: {excerpt}\n"
                        f"     url: {member['url']}"
                    )

            parts.append("")

    return "\n".join(parts), cluster_ids


async def render_header(period_start: datetime, period_end: datetime) -> str:
    """Deterministic, Python-computed header. Prepended to the model's markdown.

    Inputs:
        period_start, period_end: the reporting window.

    Returns:
        A markdown block: period, plus a per-vertical line of cluster count / new
        signals / distinct authors.

    [SPEC_DEVIATION] Declared synchronous in cycle 1's scaffold. It is a coroutine
    because the counts are a database aggregate; computing them in the caller and
    passing them in would just move the same query one frame up and split one fact
    across two functions.

    Never ask the model for a number it can get wrong — it writes the prose, you write
    the facts. These counts are also the weekly health check: if real estate shows 0
    new signals three weeks running, the problem is upstream (subreddits, keyword gate,
    or the 'neither' rate) and you would otherwise not notice, because the digest would
    still arrive looking fine.
    """
    async with get_session() as session:
        rows = (
            await session.execute(
                text(PERIOD_STATS_SQL),
                {"period_start": period_start, "period_end": period_end},
            )
        ).mappings().all()

    stats = {row["vertical"]: row for row in rows}

    lines = [
        f"# Pain digest — {period_start:%Y-%m-%d} to {period_end:%Y-%m-%d}",
        "",
    ]
    for vertical in VERTICAL_NAMES:
        row = stats.get(vertical)
        label = vertical.replace("_", " ")
        lines.append(
            f"- **{label}** — {row['clusters'] if row else 0} clusters, "
            f"{row['new_signals'] if row else 0} new signals, "
            f"{row['distinct_authors'] if row else 0} distinct authors"
        )
    lines.append("")
    return "\n".join(lines)


#: Any http(s) link. Deliberately crude — this checks that the model kept its links at
#: all, not that they resolve.
_URL_RE = re.compile(r"https?://\S+")

#: A markdown blockquote line. The prompt asks for "the strongest verbatim quote with
#: its link", and the model renders those as blockquotes.
_QUOTE_LINE_RE = re.compile(r"^\s*>")


def _quote_blocks(prose: str) -> list[str]:
    """Group the prose's blockquote lines into blocks, each with its trailing context.

    Inputs:
        prose: the model's markdown.

    Returns:
        One string per quote block: the quoted lines, plus the first non-blank line
        after them.

    Why blocks rather than lines: a quote is routinely written across several ``>``
    lines with the link on the last one, or as a quote followed by an attribution line
    carrying the URL. Checking line-by-line would reject both of those correct shapes;
    checking the whole document at once (the FINDING-3.5 bug) accepts a digest where
    one quote is linked and the next is not.
    """
    blocks: list[str] = []
    lines = prose.splitlines()
    index = 0

    while index < len(lines):
        if not _QUOTE_LINE_RE.match(lines[index]):
            index += 1
            continue

        block: list[str] = []
        while index < len(lines) and _QUOTE_LINE_RE.match(lines[index]):
            block.append(lines[index])
            index += 1

        # The attribution line immediately after the quote counts as part of it.
        trailing = index
        while trailing < len(lines) and not lines[trailing].strip():
            trailing += 1
        if trailing < len(lines) and not _QUOTE_LINE_RE.match(lines[trailing]):
            block.append(lines[trailing])

        blocks.append("\n".join(block))

    return blocks


def validate_model_output(prose: str, stop_reason: str | None) -> None:
    """Refuse digest prose that is empty, truncated, or stripped of its source links.

    Inputs:
        prose: the concatenated text blocks from the model.
        stop_reason: the response's stop reason, if the SDK reported one.

    Raises:
        ValueError: on any of the three failures below. The caller has not persisted
            anything yet, so raising loses a Sonnet call and nothing else.

    Three ways the digest can fail while nothing raises (FINDING-2.6, FINDING-2.7):

    * **Empty output.** Persisting it archives a blank week and sets ``delivered``,
      and the next run's ``period_start`` moves past the window — so the content isn't
      just missing, it's unrecoverable without a manual date fix.
    * **``stop_reason == "max_tokens"``.** The prose ends mid-sentence, usually inside
      the real-estate section, because that section comes second. The digest looks
      complete for insurance and silently drops the vertical it was cut off in.
    * **A quote without its own source URL.** This is INV-10. The thread authors are
      the leads; a pain point without a link is trivia. The check is **per quote**
      (FINDING-3.5): an aggregate "does the document contain a URL" test passes a
      digest whose first quote is linked and whose remaining nine are not, which is the
      realistic failure — the model doesn't usually drop every link, it drops some. A
      digest that reads beautifully has failed at its actual job if you can't email the
      person, and it's the failure most likely to go unnoticed, because the prose is
      exactly as persuasive either way.
    """
    if not prose.strip():
        raise ValueError("digest model returned no text; refusing to persist it")

    if stop_reason == "max_tokens":
        raise ValueError(
            "digest was truncated at max_tokens; refusing to persist a partial digest"
        )

    blocks = _quote_blocks(prose)
    unlinked = [block for block in blocks if not _URL_RE.search(block)]
    if unlinked:
        raise ValueError(
            f"{len(unlinked)} of {len(blocks)} quotes carry no source URL (INV-10); "
            f"refusing to persist it. First offender: {unlinked[0].strip()[:120]!r}"
        )

    # Prose with no blockquotes at all still has to link something, or there is no
    # outreach target anywhere in the digest.
    if not blocks and not _URL_RE.search(prose):
        raise ValueError(
            "digest contains no source URL (INV-10); refusing to persist it"
        )


def _write_file(markdown: str, period_end: datetime) -> Path:
    """Write the digest to ``digests/YYYY-MM-DD.md``. The sink that cannot fail.

    Returns:
        The path written.

    Security: the digest contains untrusted excerpts and possibly PII. ``digests/`` is
    gitignored; the path is derived from a date, never from content.
    """
    directory = Path(settings.digest_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{period_end:%Y-%m-%d}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def _send_email(markdown: str, period_end: datetime) -> None:
    """Send the digest over SMTP. Blocking — call via ``asyncio.to_thread``.

    Raises:
        Whatever smtplib raises. The caller logs and continues; INV-11 requires that a
        failed send never lose content.

    Security: credentials come from settings and are never logged. The recipient is a
    single configured address, never taken from post content.
    """
    message = EmailMessage()
    message["Subject"] = f"Pain digest — {period_end:%Y-%m-%d}"
    message["From"] = settings.digest_from_address or settings.smtp_user
    message["To"] = settings.digest_to_address
    message.set_content(markdown)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)


async def send_digest(ctx: dict[str, object] | None = None) -> int:
    """Generate, persist, then deliver. Returns the digests row id.

    Inputs:
        ctx: ARQ job context, unused (FINDING-1.6).

    Returns:
        The ``digests.id`` of the row written.

    Order is the invariant (INV-11), not an implementation detail::

        select -> Sonnet call -> render -> INSERT digests (delivered=false)
                                              |
                                              +- write digests/YYYY-MM-DD.md   (always)
                                              +- SMTP send -> delivered = true
                                                           -> on failure: log, continue

    Two sinks, both on by default. The file sink is the fallback that cannot fail. If
    email fails you still have the digest on disk and in the DB — a failed send must
    never lose content.

    period_start = the previous digest's period_end, or 7 days ago on first run.
    max_tokens=8000, non-streaming.

    Invariant INV-10 — every quote carries its source URL. If the model drops a link,
    the digest failed at its actual job even though nothing raised: the thread author
    is the lead, and a pain point without a URL is trivia.

    Security: SMTP credentials come from settings, never appear in the digest or logs.
    The recipient is a single configured address, not user-supplied. Digest text
    contains untrusted excerpts — the body is never logged at INFO.
    """
    period_end = datetime.now(UTC)

    async with get_session() as session:
        previous_end = (
            await session.execute(
                select(Digest.period_end).order_by(Digest.period_end.desc()).limit(1)
            )
        ).scalar_one_or_none()

    period_start = previous_end or (period_end - timedelta(days=7))

    gathered = await gather_digest_input(period_start, period_end)
    # gather_digest_input returns (prompt, cluster_ids). The bare-string form is
    # tolerated so a caller that assembles its own prompt — a manual re-run over a
    # hand-edited block — doesn't have to fabricate an id list.
    if isinstance(gathered, tuple):
        body, cluster_ids = gathered
    else:
        body, cluster_ids = gathered, []

    header = await render_header(period_start, period_end)

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.create(
            model=settings.digest_model,
            max_tokens=8000,
            system=DIGEST_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": body}],
        )
    finally:
        await client.close()

    prose = "".join(
        block.text for block in response.content
        if getattr(block, "type", None) == "text"
    )

    # Before anything is written. A bad digest costs one Sonnet call to discard and a
    # whole week to recover from once it has been archived and marked delivered.
    validate_model_output(prose, getattr(response, "stop_reason", None))

    markdown = f"{header}\n{prose}\n"

    # Written before delivery is attempted. Everything after this point can fail
    # without losing the digest.
    async with get_session() as session:
        digest = Digest(
            period_start=period_start,
            period_end=period_end,
            cluster_ids=cluster_ids,
            markdown=markdown,
            delivered=False,
        )
        session.add(digest)
        await session.flush()
        digest_id = digest.id

    path = _write_file(markdown, period_end)
    log.info("digest %d written to %s (%d clusters)", digest_id, path, len(cluster_ids))

    if not settings.smtp_host or not settings.digest_to_address:
        log.warning("digest %d: SMTP not configured, file sink only", digest_id)
        return digest_id

    try:
        await asyncio.to_thread(_send_email, markdown, period_end)
    except Exception as exc:
        # Deliberately swallowed. The content is already in Postgres and on disk; an
        # unreachable mail server must not turn into a lost week.
        log.error("digest %d: SMTP delivery failed: %s", digest_id, exc)
        return digest_id

    async with get_session() as session:
        await session.execute(
            update(Digest).where(Digest.id == digest_id).values(delivered=True)
        )

    log.info("digest %d delivered", digest_id)
    return digest_id
