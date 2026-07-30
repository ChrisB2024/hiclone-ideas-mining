"""Configuration — the single place a vertical is named, and the sole secrets boundary.

Two rules that other modules depend on (see .spec/modules/worker.md):

1. **No module outside this file may read ``os.environ``.** Everything reaches the
   environment through ``Settings``.
2. **No module outside this file may branch on a hardcoded vertical name.** Iterate
   ``VERTICALS``; never write ``if vertical == "insurance"``.

``VERTICALS`` is the entire multi-vertical machinery. There is no plugin layer, no
per-vertical threshold, and no ``verticals/`` package — those are on the non-goals list in
specs/README.md and are binding.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Verticals — exactly two, hardcoded. specs/02-chunk1-ingest.md
# ---------------------------------------------------------------------------

VERTICALS: dict[str, dict[str, object]] = {
    "insurance": {
        "subreddits": ["Insurance", "InsuranceAgent", "InsurancePros"],
        "hn_query": "insurance agency OR insurance broker OR underwriting",
        # Used by the filter to gate shared sources only. Deliberately concrete —
        # product names, acronyms, workflow nouns. Generic words like "client" or
        # "software" appear in both verticals and gate nothing.
        "keywords": [
            "insurance", "insurer", "underwrit", "policyholder", "premium",
            "carrier", "broker", "agency management system", "AMS360", "Applied Epic",
            "EZLynx", "quoting", "certificate of insurance", "COI", "ACORD",
            "claims", "adjuster", "renewal", "commission", "E&O", "P&C",
        ],
    },
    "real_estate": {
        "subreddits": [
            "realtors", "RealEstate", "realestateinvesting",
            "CommercialRealEstate", "PropertyManagement",
        ],
        "hn_query": "real estate agent OR brokerage OR property management",
        "keywords": [
            "realtor", "real estate", "brokerage", "listing", "MLS", "escrow",
            "closing", "title company", "showing", "open house", "comps", "CMA",
            "transaction coordinator", "under contract", "buyer agent",
            "listing agent", "property manager", "tenant", "lease", "landlord",
            "Zillow", "Follow Up Boss", "kvCORE", "DocuSign", "appraisal",
        ],
    },
}

# Sources carrying no vertical signal. vertical_hint = NULL; the filter gates them
# on keywords, the model decides the vertical.
SHARED_SUBREDDITS: list[str] = ["smallbusiness", "Entrepreneur"]

VERTICAL_NAMES: tuple[str, ...] = tuple(VERTICALS)
ENRICHMENT_VERTICALS: tuple[str, ...] = VERTICAL_NAMES + ("neither",)

# ---------------------------------------------------------------------------
# Filter — specs/03-chunk2-filter-enrich.md
# ---------------------------------------------------------------------------

# Pain-signal regexes, case-insensitive, matched against title + "\n" + body.
# Vertical-neutral by design: the vertical gate is a separate, keyword-based check.
#
# SECURITY: these run against untrusted forum text. Keep them linear — no nested
# quantifiers — or a crafted post can hang the worker on catastrophic backtracking.
PAIN_PATTERNS: list[str] = [
    r"\bis there (a|any) (tool|software|app|service)\b",
    r"\bhow (do|does) (you|your team|anyone) (handle|deal with|manage)\b",
    r"\bwasting (hours|time|days)\b",
    r"\b(spend|spending) \w+ hours\b",
    r"\bmanually\b",
    r"\bby hand\b",
    r"\bcopy[- ]?past",
    r"\bre-?key(ing)?\b",
    r"\bi'?d pay\b",
    r"\bwould pay\b",
    r"\bworth paying\b",
    r"\bspreadsheet hell\b",
    r"\bhate (doing|having to)\b",
    r"\btedious\b",
    r"\bthere has to be a better way\b",
    r"\bany recommendations for\b",
]

MIN_BODY_CHARS = 100

#: Bodies Reddit/HN mark as deleted. Compared case-insensitively against the stripped
#: body, so a post whose entire content is "[removed]" is rejected before the regexes.
DELETED_BODIES: frozenset[str] = frozenset({"[deleted]", "[removed]", "[removed by reddit]"})

#: Sent to the enrichment model. Nothing past this changes the extraction, and the tail
#: of a long rant is its least informative part.
POST_TEXT_MAX_CHARS = 4000

# ---------------------------------------------------------------------------
# Scoring weights — specs/05-chunk4-digest.md
# ---------------------------------------------------------------------------

RECENCY_HALFLIFE_DAYS = 14.0     # exp(-days / this)
WTP_EXPLICIT_WEIGHT = 2.0        # score = ... * (1 + EXPLICIT*ratio + IMPLIED*ratio)
WTP_IMPLIED_WEIGHT = 0.5
MIN_DISTINCT_AUTHORS = 2         # below this a cluster is an anecdote — excluded, not zeroed
DIGEST_CLUSTERS_PER_VERTICAL = 5

#: Members quoted per cluster in the digest, ordered by raw_posts.score DESC — a proxy
#: for "well articulated and agreed with".
DIGEST_MEMBERS_PER_CLUSTER = 3
DIGEST_EXCERPT_CHARS = 300


class Settings(BaseSettings):
    """Environment-backed settings. The only place ``os.environ`` is read."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Infrastructure -----------------------------------------------------
    database_url: str = "postgresql+asyncpg://localhost/ideas_mining"
    redis_url: str = "redis://localhost:6379"

    # --- Ingest -------------------------------------------------------------
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "ideas-mining/0.1"
    ingest_interval_hours: int = 6
    # Deliberately LONGER than the interval so a missed or slow run self-heals on the
    # next tick. The overlap is free because upserts are idempotent (INV-3).
    ingest_lookback_hours: int = 12
    posts_per_subreddit: int = 100
    comments_per_post: int = 10

    # --- Models -------------------------------------------------------------
    anthropic_api_key: str = ""
    enrich_model: str = "claude-haiku-4-5"
    digest_model: str = "claude-sonnet-5"

    #: How many raw posts one enrichment batch may carry. The Batch API allows far
    #: more; the cap keeps a single bad run from spending the whole budget at once.
    enrich_batch_size: int = 500

    voyage_api_key: str = ""
    # OQ-1 RESOLVED (cycle 2). Verified against Voyage's model list: voyage-3.5 is
    # 1024-dimensional by default, 32K context, and supports 256/512/1024/2048.
    #
    # The dimension, not the model, is the thing that's expensive to change — vector(N)
    # is baked into two columns and changing N invalidates every stored embedding. 1024
    # is the default across the entire current Voyage lineup (voyage-3.5, voyage-3-large,
    # voyage-4, voyage-4-lite, voyage-code-3, voyage-finance-2), so this schema survives
    # a model swap as long as the replacement stays at 1024. Verify that before swapping.
    embed_model: str = "voyage-3.5"
    embedding_dim: int = 1024
    embed_batch_size: int = 128

    # --- Cluster ------------------------------------------------------------
    # One threshold for BOTH verticals. A per-vertical dict makes the two verticals'
    # frequency counts incomparable and breaks scoring. Err tight: splitting a cluster
    # is unsupported, merging is a manual UPDATE.
    similarity_threshold: float = 0.85

    # --- Digest delivery ----------------------------------------------------
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    digest_to_address: str = ""
    #: Falls back to smtp_user when blank — most providers reject a From they don't own.
    digest_from_address: str = ""
    digest_dir: str = "digests"

    # --- Observability ------------------------------------------------------
    log_level: str = "INFO"


settings = Settings()
