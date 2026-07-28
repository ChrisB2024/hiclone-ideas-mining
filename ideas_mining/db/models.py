"""SQLAlchemy models — the pipeline's state machine lives in these columns.

Full column-level rationale: specs/01-data-model.md. Contract summary:
.spec/modules/db.md.

Two constraints here carry system invariants and must NOT be relaxed to make a test
pass:

* ``UNIQUE (raw_posts.source, external_id)``  -> INV-3, ingest idempotency
* ``UNIQUE (enriched_signals.raw_post_id)``   -> INV-4, never enrich twice

Both are enforced in the database, not in Python. A module that dedupes in
application code instead is wrong: application-level dedupe drifts under
concurrency and retries, a unique index cannot.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY, BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey,
    Index, Integer, SmallInteger, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ideas_mining.config import ENRICHMENT_VERTICALS, VERTICAL_NAMES, settings

if settings.embedding_dim <= 0:  # pragma: no cover - startup guard
    raise RuntimeError(
        "settings.embedding_dim is unset (OQ-1). The Voyage model and its dimension "
        "must be chosen before the schema can be defined — vector(N) is baked into the "
        "table and changing N later invalidates every stored embedding."
    )

_VERTICAL_SQL = ", ".join(f"'{v}'" for v in VERTICAL_NAMES)
_ENRICH_VERTICAL_SQL = ", ".join(f"'{v}'" for v in ENRICHMENT_VERTICALS)


class Base(DeclarativeBase):
    pass


class RawPost(Base):
    """Immutable record of what was on the internet.

    Written once by ingest and never revised — ``ON CONFLICT DO NOTHING``, never
    ``DO UPDATE``. Scores and bodies drift after posting; revising them would mean
    re-running enrichment to stay consistent, which breaks INV-4 for no benefit.

    Only ``filter_state`` and ``filter_reason`` are mutated after insert.
    """

    __tablename__ = "raw_posts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    source: Mapped[str] = mapped_column(String(32))  # "reddit" | "hackernews"
    external_id: Mapped[str] = mapped_column(String(64))
    parent_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subsource: Mapped[str | None] = mapped_column(String(128), nullable=True)

    #: From the subreddit config; NULL for shared sources and ALWAYS NULL for HN.
    #: A hint, not the answer — enrich decides the real vertical.
    vertical_hint: Mapped[str | None] = mapped_column(String(32), nullable=True)

    url: Mapped[str] = mapped_column(Text)

    #: NULL for deleted accounts. Never store the string "None" — it reads as one
    #: very prolific user and corrupts distinct_authors (INV-7).
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer)

    #: The ORIGINAL post time, not fetch time. Must be timezone-aware — a naive
    #: datetime here silently drifts recency scoring by hours.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    filter_state: Mapped[str] = mapped_column(String(16), default="pending")
    filter_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_raw_posts_source_external"),
        CheckConstraint(
            "filter_state IN ('pending', 'passed', 'rejected')",
            name="ck_raw_posts_filter_state",
        ),
        CheckConstraint(
            f"vertical_hint IS NULL OR vertical_hint IN ({_VERTICAL_SQL})",
            name="ck_raw_posts_vertical_hint",
        ),
        Index(
            "ix_raw_posts_passed",
            "filter_state",
            postgresql_where=(filter_state == "passed"),
        ),
        Index("ix_raw_posts_created_at", created_at.desc()),
    )


class EnrichedSignal(Base):
    """One row per post that survived the filter and got an LLM call.

    ``raw_post_id`` is UNIQUE: that constraint IS the "never enrich twice" guarantee
    (INV-4). Work selection uses ``LEFT JOIN ... WHERE es.id IS NULL`` rather than a
    state flag, because a join cannot drift out of sync with reality.

    ``embedding`` and ``cluster_id`` are nullable on purpose — that is what makes
    chunks 1-2 a shippable milestone with chunk 3 unbuilt.
    """

    __tablename__ = "enriched_signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    raw_post_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("raw_posts.id"), unique=True
    )

    #: insurance | real_estate | neither. Decided by the model, using vertical_hint
    #: as a prior. "neither" is a real value, not a failure: shared sources produce
    #: plenty of well-formed pain from other industries.
    vertical: Mapped[str] = mapped_column(String(32))

    #: The ONLY text that gets embedded (INV-6). Phrased generically so two people
    #: describing the same pain produce near-identical sentences — that property is
    #: what makes clustering possible.
    pain_point: Mapped[str] = mapped_column(Text)

    who_has_it: Mapped[str] = mapped_column(Text)
    current_workaround: Mapped[str | None] = mapped_column(Text, nullable=True)
    willingness_to_pay_signal: Mapped[str] = mapped_column(String(16))
    buildable_with_llm: Mapped[bool] = mapped_column(Boolean)

    #: 0-10, relative to THIS row's vertical. Always 0 when vertical = 'neither'.
    relevance: Mapped[int] = mapped_column(SmallInteger)

    #: Model ID used, so a subset can be re-run when the model changes.
    model: Mapped[str] = mapped_column(String(64))
    enriched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.embedding_dim), nullable=True
    )
    cluster_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("clusters.id"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            f"vertical IN ({_ENRICH_VERTICAL_SQL})", name="ck_signals_vertical"
        ),
        CheckConstraint(
            "willingness_to_pay_signal IN ('none', 'implied', 'explicit')",
            name="ck_signals_wtp",
        ),
        CheckConstraint("relevance BETWEEN 0 AND 10", name="ck_signals_relevance"),
        CheckConstraint(
            "vertical <> 'neither' OR relevance = 0", name="ck_signals_neither_zero"
        ),
        Index("ix_signals_cluster", "cluster_id"),
        Index("ix_signals_vertical_relevance", "vertical", relevance.desc()),
        # TODO(chunk 3): add once there are a few thousand rows — an index built on an
        # empty table has useless statistics. Must be vector_cosine_ops, NOT L2:
        # Voyage vectors are normalized for cosine, and the wrong opclass degrades
        # neighbour quality silently rather than erroring.
        #   Index("ix_signals_embedding", "embedding",
        #         postgresql_using="hnsw",
        #         postgresql_ops={"embedding": "vector_cosine_ops"}),
    )


class Cluster(Base):
    """One distinct pain, in one vertical. The table INV-1 and INV-2 are about.

    ``distinct_authors`` — not ``member_count`` — is the demand proxy (INV-7). One
    person cross-posting to four subreddits is a signal of 1.

    The denormalized counters are recomputed from members after every assignment
    rather than nudged incrementally, so they cannot drift.
    """

    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    #: insurance | real_estate. Never 'neither' — those rows are archived, not clustered.
    vertical: Mapped[str] = mapped_column(String(32))

    label: Mapped[str] = mapped_column(Text)
    centroid: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dim))

    member_count: Mapped[int] = mapped_column(Integer, default=0)
    #: count(DISTINCT raw_posts.author) over members. This is `frequency`.
    distinct_authors: Mapped[int] = mapped_column(Integer, default=0)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: max member created_at. Drives the recency term in scoring.
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(f"vertical IN ({_VERTICAL_SQL})", name="ck_clusters_vertical"),
        CheckConstraint(
            "distinct_authors <= member_count", name="ck_clusters_authors_lte_members"
        ),
        # Leads on vertical because EVERY read of this table is vertical-scoped:
        # the nearest-centroid search, the digest ranking, and manual inspection.
        # No query in the system wants clusters from both verticals at once (INV-9).
        Index("ix_clusters_vertical_score", "vertical", score.desc().nullslast()),
    )


class Digest(Base):
    """Archive of what was sent. Written BEFORE delivery is attempted (INV-11).

    Cheap, and the only way to answer "did I already see this idea three weeks ago".
    """

    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    #: Insurance in rank order, then real estate in rank order.
    cluster_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger))

    markdown: Mapped[str] = mapped_column(Text)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
