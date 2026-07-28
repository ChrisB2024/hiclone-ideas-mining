# Module — `worker` + `config` (`ideas_mining/worker.py`, `config.py`)

Status: **not implemented.** Sources: all five files in [`specs/`](../../specs/).

## Purpose
`worker.py` is the ARQ entry point — the only place cron schedules exist. `config.py` is the
single place a vertical is named.

## Responsibilities

**`config.py`** — pydantic-settings, env-overridable:
- `VERTICALS` dict: two entries, each `{subreddits, hn_query, keywords}`.
- `SHARED_SUBREDDITS`, ingest tuning, `SIMILARITY_THRESHOLD`, scoring weights,
  `DIGEST_CLUSTERS_PER_VERTICAL`, model IDs, connection strings, SMTP.

**`worker.py`** — `WorkerSettings.cron_jobs`:

| Job | Schedule |
|---|---|
| `ingest_all` | `hour={0,6,12,18}, minute=0` |
| `run_filter` | `hour={0,6,12,18}, minute=10` |
| `submit_enrichment` | `hour={0,6,12,18}, minute=15` |
| `collect_enrichment` | `minute={5,35}` |
| `embed_pending` | `minute={20,50}` |
| `assign_clusters` | `minute={25,55}` |
| `score_clusters` | `weekday=0, hour=7, minute=0` |
| `send_digest` | `weekday=0, hour=7, minute=5` |

## Non-Responsibilities
- **No business logic in `worker.py`.** It wires schedules to functions and nothing else.
- `config.py` holds no logic and imports nothing from the package.
- Neither module knows how a vertical behaves — only its name and its lists.

## Inputs / Outputs
In: environment. Out: `Settings` singleton; a running ARQ worker.

## State Machine
Stateless. All pipeline state is in Postgres — a worker restart loses nothing `[SPECIFIED]`.

## Data Model
None. `config.py` must not import the DB models (would create a cycle).

## API Contract
`[INFERRED — NEEDS HUMAN CONFIRMATION]` ARQ's `WorkerSettings` convention; job functions take
`(ctx, ...)`. `redis_settings`, `on_startup`/`on_shutdown` for the DB pool — inferred, not
specified.

## Invariants
- **`VERTICALS` is the entire multi-vertical machinery.** No other module may hardcode
  `"insurance"` or `"real_estate"` as a branch. Iterate the dict `[SPECIFIED]`.
- One threshold, one weight set, one prompt for both verticals — no per-vertical config
  `[SPECIFIED]`.
- Collect/embed/assign jobs are no-ops when nothing is pending.

## Trust Boundaries
`config.py` reads secrets from env. **It is the boundary between secrets and the rest of the
system** — no other module may read `os.environ` directly.

## Failure Modes
- One job raising must not kill the worker or block later jobs.
- Redis down → nothing fires, silently. `[OPEN QUESTION]` no alerting is specified; the
  weekly digest arriving empty is currently the only signal.
- Overlapping runs if a job exceeds its interval (`collect_enrichment` every 30 min).
  `[INFERRED — NEEDS HUMAN CONFIRMATION]` ARQ's default job-uniqueness behavior is assumed
  sufficient; not confirmed.
- Schedule drift: `submit_enrichment` at :15 assumes `run_filter` finished by then. It's a
  time-based assumption, not a dependency — a slow filter run means an empty submit, which
  self-corrects next tick.

## Security
Secrets from env only, never logged, never defaulted to a real value in code. `.env` must be
gitignored; ship `.env.example` with placeholders.

## Dependencies
`arq`, `pydantic-settings`, Redis.

## Testing Requirements
- `Settings` loads from a fixture env with no real credentials.
- `VERTICALS` has exactly two entries, each with all three keys and non-empty lists.
- Every cron job name in `WorkerSettings` resolves to a real importable callable.
- No module outside `config.py` reads `os.environ` (greppable assertion).
- No module outside `config.py` contains the literal `"insurance"` as a branch condition.
- Each job is a no-op on an empty DB and does not raise.

## Open Questions
`OQ-2` (docker-compose ownership), `OQ-7` (git). Also: no alerting/observability is specified
anywhere — a silently dead pipeline is currently indistinguishable from a quiet week.
