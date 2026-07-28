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

# ---------------------------------------------------------------------------
# Scoring weights — specs/05-chunk4-digest.md
# ---------------------------------------------------------------------------

RECENCY_HALFLIFE_DAYS = 14.0     # exp(-days / this)
WTP_EXPLICIT_WEIGHT = 2.0        # score = ... * (1 + EXPLICIT*ratio + IMPLIED*ratio)
WTP_IMPLIED_WEIGHT = 0.5
MIN_DISTINCT_AUTHORS = 2         # below this a cluster is an anecdote — excluded, not zeroed
DIGEST_CLUSTERS_PER_VERTICAL = 5


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

    voyage_api_key: str = ""
    # TODO(OQ-1) [BLOCKER]: set the real Voyage model ID and its dimension before
    # writing the initial Alembic migration. `vector(N)` in the schema must equal
    # `embedding_dim`, and changing N later invalidates every embedding already
    # computed. Do NOT guess — verify against Voyage's current docs.
    embed_model: str = "TODO-SET-VOYAGE-MODEL"
    embedding_dim: int = 0  # 0 is a deliberate tripwire: models.py refuses to load on it

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
    digest_to_address: str = ""
    digest_dir: str = "digests"


settings = Settings()
