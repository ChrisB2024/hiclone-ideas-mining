# Module — `db` (`ideas_mining/db/`)

Status: **not implemented.** Source: [`specs/01-data-model.md`](../../specs/01-data-model.md).

## Purpose
Own the schema. Every other module is a function over these four tables; the pipeline's
state machine lives in columns, not in the scheduler.

## Responsibilities
- SQLAlchemy 2.0 declarative models (`Mapped[]` annotations) for `raw_posts`,
  `enriched_signals`, `clusters`, `digests`.
- Async session factory (asyncpg).
- Alembic migrations, including `CREATE EXTENSION IF NOT EXISTS vector` as the first
  operation of the initial migration — autogenerate will not emit it `[SPECIFIED]`.
- The DB-level constraints that carry the system invariants.

## Non-Responsibilities
- No queries. Selection logic belongs to the module that owns the stage.
- No business logic, no defaults that encode policy (thresholds live in config).

## Inputs / Outputs
In: connection string from config. Out: models, sessions, migration scripts.

## State Machine
The pipeline's state is three columns, and no row ever moves backwards:

```
raw_posts.filter_state:  pending → passed | rejected
enriched_signals:        (row absent) → row exists       [INV-4: one-way, forever]
enriched_signals:        embedding NULL → set → cluster_id NULL → set
```

## Data Model
Full column tables in `specs/01-data-model.md`. Constraint-level summary:

| Constraint | Carries |
|---|---|
| `UNIQUE (raw_posts.source, external_id)` | `INV-3` — ingest idempotency |
| `UNIQUE (enriched_signals.raw_post_id)` | `INV-4` — never enrich twice |
| CHECK/Enum on `enriched_signals.vertical` | 3 values incl. `neither` |
| CHECK/Enum on `clusters.vertical` | 2 values, never `neither` |
| `INDEX (clusters.vertical, score DESC NULLS LAST)` | `INV-9` — every read is vertical-scoped |
| `vector(N)` dimension | **Blocked on `OQ-1`** |

## API Contract
`[INFERRED — NEEDS HUMAN CONFIRMATION]` Models exported from `db.models`, an async
`session_factory` / `get_session()` from `db.session`. Not specified by the human.

## Invariants
`INV-3`, `INV-4` are enforced *here*, in DB constraints — not in Python. A module that
relies on application-level dedupe is wrong.

Enum values must be enforced **in the database**, not only in Python `[SPECIFIED]`.

## Trust Boundaries
The DB is trusted and single-writer. It stores untrusted forum text; it never executes it.

## Failure Modes
- `vector` extension missing → every migration fails. Fail loudly at startup.
- Dimension mismatch between `vector(N)` and the Voyage model → insert errors at chunk 3.
- Naive datetime written to a `timestamptz` column → silent hours-long drift in recency
  scoring `[SPECIFIED]`.

## Security
No secrets in any column. Connection string from env only.

## Dependencies
SQLAlchemy 2.0, asyncpg, Alembic, `pgvector.sqlalchemy`, Postgres 16 + pgvector.

## Testing Requirements
- `alembic upgrade head` on an empty DB creates 4 tables + the extension.
- `downgrade base` → `upgrade head` is clean.
- Duplicate `(source, external_id)` raises `IntegrityError`.
- Duplicate `enriched_signals.raw_post_id` raises `IntegrityError`.
- A `raw_post` inserts with ingest fields only; every later-stage column is nullable/defaulted.
- `vertical_hint = NULL` is accepted.
- Out-of-enum `vertical` is rejected **by the DB**, tested with raw SQL that bypasses the
  ORM — an ORM-level test proves nothing about the constraint.

## Open Questions
`OQ-1` (blocks the migration), `OQ-3`, `OQ-5`.
