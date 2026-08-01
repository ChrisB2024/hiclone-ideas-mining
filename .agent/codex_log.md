# Validator Log

Append-only. Written by Codex (Validator). **Claude must never edit this file.**

---

## Session 0 — 2026-07-28 — PROTOCOL ADOPTION

Seeded during DADP installation to establish a baseline. No validation was performed —
there is nothing to validate.

### Context
Reading from: `.agent/handoff.json` (cycle 0), `.agent/claude_log.md#session-0` |
Validating: nothing — the repo contains no source code | Spec ref: `.spec/system_spec.md`.

### Test coverage at adoption

**Zero.** Specifically:

| | State |
|---|---|
| Test files | None |
| Test framework | None installed, none declared |
| Test directory | Does not exist |
| Dependency manifest | Does not exist — nothing to add a test dep to |
| CI | None |
| Fixtures / mocks / cassettes | None |

`AGENTS.md` instructs the Validator to detect and follow the repo's existing test convention
rather than impose one. **There is no convention to detect.** The framework recommendation
(`pytest` + `pytest-asyncio`, `tests/` mirroring the package, integration tests behind a
marker) is recorded in `.agent/prompts/codex_session.md` as a *recommendation pending human
confirmation* — it implies adding dependencies, and only the Builder may touch a manifest.

### Findings

No `[PASSED]` / `[FAILED]` findings — there is no code to produce them against. The
following are `[BLOCKER]`-class observations about the *testability* of the system as
specified, raised now because they shape what cycle 1 should build.

#### [FINDING-0.1] BLOCKER — vector dimension blocks the schema
- Target: `.spec/modules/db.md`, initial Alembic migration
- Detail: `vector(N)` cannot be declared without the Voyage model's dimension (`OQ-1`). No DB
  test can run before the migration exists, so this blocks the entire test surface, not just
  chunk 3.
- Traces to: `OQ-1`

#### [FINDING-0.2] BLOCKER — no runtime for integration tests
- Target: `docker-compose.yml` (does not exist)
- Detail: `INV-2` (cluster vertical partition) and `INV-7` (distinct-author counting) are
  SQL-level invariants. They are the two highest-value tests in the system and **cannot be
  meaningfully tested against a mocked DB** — a mock would assert my own stub, not pgvector's
  behavior. Testing them needs a real Postgres+pgvector. The compose file that would provide
  it is Builder-owned under the write boundary (source reads it at runtime), so I cannot
  create it.
- Traces to: `OQ-2`, `OQ-3`

#### [FINDING-0.3] BLOCKER — a class of requirements has no assertion form
- Target: `.spec/system_spec.md` § Testing posture
- Detail: Several acceptance criteria in `specs/` are explicit human-judgement checks on LLM
  output quality — "eyeball 20 `pain_point` values", "read the digest as its recipient", "is
  it obvious which industry's practitioner you'd be emailing". These are real requirements,
  and among the most important in the system: if `pain_point` phrasing is inconsistent,
  clustering silently degrades and every downstream test still passes. **I cannot convert
  them into assertions, and writing a weak proxy assertion would be worse than leaving them
  visible** — it would signal coverage that doesn't exist.
- Traces to: `OQ-6`

#### [FINDING-0.4] BLOCKER — no version control
- Target: repo root
- Detail: `git rev-parse` fails. DADP's audit trail — logs, handoff state, who-changed-what —
  assumes history exists. Without it the protocol's guarantees degrade to "two files that
  say what happened".
- Traces to: `OQ-7`

### Write-boundary notes for this repo

Resolved in `AGENTS.md`; recording the reasoning here.

- **Mine regardless of directory:** test files, `conftest.py`, fixtures, factories, mocks,
  stubs, sample payloads, snapshots, cassettes.
- **Not mine, even though they look like test data:** `docker-compose.yml`, `.env.example`,
  seed data and sample configs read by source, Alembic migrations, and the `VERTICALS` dict
  in `config.py` — that last one is *especially* tempting to edit for a test and is Builder-owned.
- **Unresolved:** the `digests/` output directory. Created by source at runtime, holds no
  code. Noted in `.spec/modules/digest.md`.

### Handoff
- Status: `AWAITING_BUILD` — unchanged. Cycle 0 was scaffolding only.
- `turn` remains `claude`. **Do not open a validation session until cycle 1 completes.**
- Open blockers passed to the Builder: `OQ-1`, `OQ-2`, `OQ-3`, `OQ-6`, `OQ-7` — all
  `needs: "human"`.

---

## Session 1 — 2026-07-28 — SKELETON VALIDATION

### Context
Reading from: `.agent/handoff.json` (`last_build.cycle = 1`, `turn: codex`),
`.agent/claude_log.md#session-1`, all 18 files in `last_build.files_written`,
`.spec/system_spec.md`, and all 8 module specs |
Validating: declared skeleton contracts only. No behavioral tests were written for
`NotImplementedError` bodies.

### Test suite established

- Framework: `pytest` in `tests/`, following `.agent/prompts/codex_session.md`.
- Files: `tests/test_config_and_targets.py`, `tests/test_declared_contracts.py`,
  `tests/test_enrichment_schema.py`, `tests/test_schema_contract.py`.
- Command: `python3 -m pytest -q`
- Result: **32 passed, 2 failed** in 0.18s.
- `arq` and `pgvector` are not installed in the current interpreter. Tests use narrow
  test-only stubs to inspect worker wiring and SQLAlchemy metadata; no source dependency
  was replaced or modified.

### Findings

#### [FINDING-1.1] [PASSED] — target generation matches config
- `build_targets()` emits every configured vertical subreddit with the correct hint,
  every shared subreddit with `None`, and no duplicates.
- Traces to: `DECISION-1.1`, `INV-3`.

#### [FINDING-1.2] [PASSED] — config and regex contracts hold
- `VERTICALS` has exactly the declared two entries and three non-empty fields per entry.
  `ENRICHMENT_VERTICALS` is derived correctly.
- Every `PAIN_PATTERNS` entry compiles and completes within one second against a 200k-character,
  prompt-injection-shaped hostile string.
- Traces to: `DECISION-1.6`.

#### [FINDING-1.3] [PASSED] — pure/impure deviations are present
- `classify()` and `compute_score()` are synchronous, primitive-argument functions; their
  modules do not import `ideas_mining.db`. Their outer stage functions are coroutines.
- Traces to: `DECISION-1.5`.

#### [FINDING-1.4] [PASSED] — structural schema claims are present
- With a test-only `Vector` type stub and a positive dimension, SQLAlchemy metadata contains
  `UNIQUE(source, external_id)`, `UNIQUE(raw_post_id)`, and the named
  `distinct_authors <= member_count` database `CHECK`.
- With dimension `0`, importing `db.models` raises `RuntimeError` naming `OQ-1`.
- This does **not** substitute for the required raw-SQL constraint tests.
- Traces to: `DECISION-1.2`, `DECISION-1.3`, `INV-3`, `INV-4`, `INV-7`.

#### [FINDING-1.5] [PASSED] — static boundaries and declared wiring hold
- Every Builder Python file compiles. No module outside `config.py` directly reads the
  environment or branches on a hardcoded vertical literal.
- Every `WorkerSettings.functions` and cron target resolves to a coroutine under a test-only
  `arq.cron` stub, and the eight cron targets match the eight registered functions.
- `NEAREST_CENTROID_SQL` contains `WHERE vertical = :vertical`.
- Traces to: `DECISION-1.1`, `DECISION-1.4`, `INV-2`, `INV-9`.

#### [FINDING-1.6] [FAILED] — seven ARQ job signatures omit `ctx`
- Only `worker.ingest_all(ctx)` exposes the ARQ context parameter. The other registered
  functions begin with no argument, or with `batch_size`:
  `filter.run_filter`, `enrich.submit_enrichment`, `enrich.collect_enrichment`,
  `cluster.embed_pending`, `cluster.assign_clusters`, `score.score_clusters`,
  `digest.send_digest`.
- ARQ invokes registered functions with `ctx` first. Six jobs will raise `TypeError`;
  `embed_pending` will silently receive the context dict as `batch_size`.
- Builder action: add `ctx` to these job contracts or register Builder-owned wrappers that
  accept `ctx` and call the stage functions.
- Traces to: `DECISION-1.1`, `.spec/modules/worker.md` API contract, `INV-5`.

#### [FINDING-1.7] [FAILED] — enrichment accepts `neither` with nonzero relevance
- `Enrichment.model_validate(...)` accepts `vertical="neither", relevance=3`.
- The model is declared complete and is the local validation contract. The DB check would
  later reject this value, turning a model semantic error into an insert failure.
- Builder action: add cross-field Pydantic validation requiring relevance `0` for
  `vertical="neither"`.
- Traces to: `DECISION-1.2`, `.spec/modules/enrich.md` invariant.

#### [FINDING-1.8] [BLOCKER] — DB and pgvector behavior remains untestable
- There is no migration or running Postgres+pgvector fixture. Raw-SQL checks for enum/range/
  uniqueness constraints and the two-part `INV-2` semantic partition test cannot run.
- The required `INV-2` test must prove both that the vertical clause partitions and that
  removing it merges the controlled fixture; the current string-presence test is only a
  skeleton-level guard.
- Traces to: `OQ-1`, `OQ-2`, `OQ-3`, `INV-2`.

#### [FINDING-1.9] [BLOCKER] — test dependencies are not reproducible from the manifest
- `pytest` and `pytest-asyncio` happen to exist in the current interpreter, but
  `pyproject.toml` declares neither. A clean checkout cannot reproduce the validation run.
- Builder action after human confirmation: add `pytest` and `pytest-asyncio` as test
  dependencies in `pyproject.toml`.
- Traces to: `DECISION-1.7`, `OQ-9`.

#### [FINDING-1.10] [BLOCKER] — manual quality acceptance still needs a human path
- No weak proxy tests were added for pain-point phrasing, vertical disagreements, or digest
  usefulness.
- Traces to: `OQ-6`, `INV-1`, `INV-10`.

#### [FINDING-1.11] [BLOCKER] — no version-control audit trail
- `git status` still reports that the directory is not a repository.
- Traces to: `OQ-7`.

### Handoff
- Status: `VALIDATION_COMPLETE`.
- Builder should address `FINDING-1.6` and `FINDING-1.7`, then flip the turn back to Codex.
- Human decisions remain required for `OQ-1`, `OQ-2`, `OQ-3`, `OQ-6`, `OQ-7`, and `OQ-9`.

---

## Session 2 — 2026-07-28 — IMPLEMENTATION VALIDATION

### Context
Reading from: `.agent/handoff.json` (`last_build.cycle = 2`, `turn: codex`),
`.agent/claude_log.md#session-2`, every file named by `last_build`, the system spec, and all
8 module specs. Validation ran against Python 3.13, PostgreSQL 16.13, and pgvector 0.8.5.
No Builder-owned source was modified.

### Test coverage added

- `tests/test_filter_and_scoring.py`
- `tests/test_ingest_helpers.py`
- `tests/test_enrichment_pipeline.py`
- `tests/test_digest_contract.py`
- `tests/test_migration_and_infra_contract.py`
- `tests/test_worker_runtime.py`
- `tests/integration/conftest.py`
- `tests/integration/test_database.py`
- `pytest.ini`
- Narrowed the cycle-1 pure-function AST assertion in
  `tests/test_declared_contracts.py` per `DECISION-2.12`.

The clean declared install initially failed before Alembic could connect because
`greenlet` was not installed. It was added to the ignored local `.venv` only so the
remaining migration and integration tests could run; the manifest was not changed.

Commands and results:

- Baseline after `pip install -e '.[test]'`: **34 passed**.
- Unit/focused: `.venv/bin/python -m pytest -q -m 'not integration'`:
  **59 passed, 13 failed, 12 deselected**.
- Live integration: `pytest -q -m integration`:
  **11 passed, 1 failed, 72 deselected**.
- Final combined suite against live PostgreSQL: **70 passed, 14 failed**.
  The 14 failing cases represent 11 distinct production defects; the malformed HN
  timestamp defect has three parameterized cases and empty/truncated digest output has
  two.

### Live migration and database validation

No container runtime exists on this host, so the compose services could not be started.
Instead, the official pgvector 0.8.5 source was built against the installed PostgreSQL 16
and a temporary local cluster was created on port 55439.

1. `alembic upgrade 0001` succeeded after the validator-only `greenlet` install.
2. The vector extension and all five tables were verified.
3. Raw-SQL uniqueness, range, enum, and author-count constraints passed.
4. `INV-2` passed both halves: vertical-partitioned assignment created two clusters, while
   the controlled unpartitioned query selected the cross-vertical identical vector.
5. `alembic downgrade base` removed the application schema.
6. `alembic upgrade head` applied both 0001 and 0002 and created both cosine HNSW indexes,
   disproving the claim that 0002 is “deliberately unapplied”.

### CodeRabbit review

`coderabbit doctor` passed all 9 checks and confirmed authentication. The full review
stalled without completing and was interrupted; the light review completed over all
Builder and Validator changes with 11 findings.

Confirmed and regression-tested:

- Public compose port bindings (`FINDING-2.13`).
- Malformed HN timestamps abort a query (`FINDING-2.10`).
- Digest prose and persisted cluster IDs can come from different rankings
  (`FINDING-2.8`).
- Empty or token-truncated digest output is persisted (`FINDING-2.7`).
- An accepted Anthropic batch can become untracked without logging its recoverable ID
  (`FINDING-2.9`).
- `recluster_all()` uses an FK-prohibited truncate (`FINDING-2.12`).
- One Reddit submission/comment failure discards later submissions
  (`FINDING-2.11`).
- Invalid `LOG_LEVEL` can crash worker startup (`FINDING-2.14`).

Accepted Validator-test feedback: the out-of-order enrichment fixture now contains two
successful shuffled IDs, so position-keyed parsing cannot pass accidentally.

Rejected:

- Moving `IDEAS_MINING_TEST_DATABASE_URL` from integration `conftest.py` into production
  config would violate the test-only write boundary and make a test control part of runtime
  configuration.
- Closing `.agent/handoff.json` during the review was premature; it is closed only after
  all findings and final counts are recorded.

### Findings

#### [FINDING-2.1] [PASSED] — live schema and partition invariants hold
- Migration 0001 applies to an empty PostgreSQL+pgvector database. Raw-SQL constraints,
  idempotent upsert, vertical-only ranking, distinct-author bounds, and both halves of the
  `INV-2` partition witness pass.
- Traces to: `DECISION-2.1`, `DECISION-2.2`, `DECISION-2.3`, `DECISION-2.9`,
  `INV-2`, `INV-3`, `INV-4`, `INV-7`.

#### [FINDING-2.2] [PASSED] — cycle-1 failures and declared stage contracts are fixed
- All registered ARQ jobs accept `ctx`; `Enrichment` rejects `neither` with nonzero
  relevance; partial/out-of-order batch collection is isolated; pending batches block a
  second submit; the narrowed pure-function AST checks pass.
- Traces to: `DECISION-2.3`, `DECISION-2.4`, `DECISION-2.5`,
  `DECISION-2.12`, `INV-5`, `INV-9`.

#### [FINDING-2.3] [PASSED] — filter, score, and ordinary ingest behavior holds
- Exhaustive filter reasons, both gate paths, removed-before-too-short deviation, score
  veto/freshness/WTP behavior, HTML stripping, deleted Reddit authors, timezone-aware
  timestamps, and intra-batch dedupe pass.
- Traces to: `DECISION-2.6`, `INV-3`, `INV-6`, `INV-8`.

#### [FINDING-2.4] [FAILED] — async SQLAlchemy runtime dependency is absent
- A clean `pip install -e '.[test]'` cannot run Alembic: SQLAlchemy raises
  `ValueError: the greenlet library is required`. Neither `greenlet` nor
  `sqlalchemy[asyncio]` is declared.
- Traces to: `DECISION-2.11`, `INV-5`.

#### [FINDING-2.5] [FAILED] — “deferred” HNSW migration is the Alembic head
- `ScriptDirectory.get_current_head()` returns 0002, not 0001. A live
  `alembic upgrade head` applied 0002 and created both indexes. The operational command
  documented by the decision cannot keep 0002 unapplied.
- Traces to: `DECISION-2.8`, `INV-5`.

#### [FINDING-2.6] [FAILED] — digest source URLs are not enforced
- `send_digest()` persists model prose containing a quote with no URL, despite `INV-10`.
- Traces to: `INV-10`.

#### [FINDING-2.7] [FAILED] — empty and truncated digest responses are persisted
- A response with no text blocks becomes a header-only digest. A response ending with
  `stop_reason="max_tokens"` is also persisted as complete.
- Traces to: `INV-5`, `INV-10`, `INV-11`.

#### [FINDING-2.8] [FAILED] — persisted cluster IDs can disagree with digest prose
- `gather_digest_input()` ranks clusters for the model, then `send_digest()` independently
  ranks them again for persistence. A ranking change between calls stores IDs that did not
  produce the digest.
- Traces to: `INV-5`, `INV-10`.

#### [FINDING-2.9] [FAILED] — an accepted enrichment batch can become untracked
- If Anthropic accepts a batch and the following `enrichment_batches` insert fails, the
  batch ID is neither persisted nor logged. Recovery is impossible and the next tick can
  resubmit the same posts.
- Traces to: `DECISION-2.3`, `INV-5`.

#### [FINDING-2.10] [FAILED] — one malformed HN timestamp aborts a query
- Missing, non-ISO, and non-string `created_at` values escape `_hit_to_row`. The exception
  prevents the already-collected page rows from reaching the final upsert.
- Traces to: `INV-3`, `INV-5`.

#### [FINDING-2.11] [FAILED] — one Reddit submission failure loses later posts
- Comment expansion errors are only isolated at the subreddit level. A bad first
  submission aborts iteration and prevents later valid submissions and accumulated rows
  from being persisted.
- Traces to: `INV-3`, `INV-5`.

#### [FINDING-2.12] [FAILED] — `recluster_all()` cannot clear clusters
- Live PostgreSQL rejects `TRUNCATE clusters RESTART IDENTITY` because
  `enriched_signals.cluster_id` has a foreign-key constraint. Nulling values first does not
  remove the table-level dependency.
- Traces to: `INV-2`, `INV-5`.

#### [FINDING-2.13] [FAILED] — compose exposes stateful services on all interfaces
- PostgreSQL and Redis publish default ports without a loopback host binding; PostgreSQL
  also uses public development credentials. On a shared/LAN-connected workstation this
  exposes both services beyond the intended local validation runtime.
- Traces to: `DECISION-2.10`, `INV-5`.

#### [FINDING-2.14] [FAILED] — malformed log level crashes worker startup
- Whitespace or an unknown `LOG_LEVEL` reaches `logging.basicConfig` as an invalid string
  and raises `ValueError` before the worker starts.
- Traces to: `INV-5`.

#### [FINDING-2.15] [BLOCKER] — manual quality acceptance still needs a human path
- No proxy assertions were invented for pain-point normalization, vertical disagreements,
  or digest usefulness. Those acceptance criteria require reading actual model output.
- Traces to: `OQ-6`, `INV-1`, `INV-10`.

### Handoff
- Status: `VALIDATION_COMPLETE`; turn returned to Claude.
- Builder should fix `FINDING-2.4` through `FINDING-2.14` and rerun the added regressions.
- `OQ-2` and `OQ-3` are now verified with a real PostgreSQL+pgvector runtime.
- `OQ-6` remains human-owned.

---

## Session 3 — 2026-08-01 — CYCLE-3 FIX VALIDATION

### Context
Read `.agent/handoff.json` (`last_build.cycle = 3`, `turn: codex`), Claude session 3 in
full, every file in the cycle-3 build manifest, the system spec, and all 8 module specs.
The cycle-3 build was already committed and pushed as `eb39940` and `bb6fd60`; Validator
changes remain uncommitted. No Builder-owned source was modified.

### Test coverage changed

- Resolved `TEST-CONFLICT-3.1` by following the HNSW migration to
  `migrations/deferred/0002_hnsw_indexes.py`.
- Added direct digest validation for blank, truncated, valid-linked, and partially-linked
  multi-quote output; rejected output must write no row/file and attempt no SMTP delivery.
- Added a direct `gather_digest_input()` prompt/cluster-id coupling test.
- Added HN missing/null/numeric/unparseable/naive timestamp cases.
- Added all three Reddit failure domains: bad submission, failed comment tree, and listing
  failure after partial progress.
- Added worker log-level padding/case/unknown/numeric-string cases.
- Strengthened live reclustering to assert embeddings survive.
- Added both Alembic configuration checks, non-blocking HNSW construction, and a disposable
  database test proving the normal migration path remains usable after optional indexes.
- Added a 120-second timeout to every Alembic subprocess used by integration tests.

### Results

- Claude baseline: **71 passed, 1 failed, 12 skipped**. The failure was the stale
  Validator-owned migration path.
- Focused/unit suite after cycle-3 tests: **87 passed, 2 failed, 13 deselected**.
- Live integration suite: **12 passed, 1 failed, 89 deselected**.
- Final clean Linux run inside the Compose network: **99 passed, 3 failed**.
- Clean install: a brand-new venv installed `greenlet 3.5.4` via
  `sqlalchemy[asyncio]` and successfully migrated a fresh database to 0001.

The three failing tests represent three distinct production defects; there are no setup or
test-harness failures.

### Docker and live database validation

- Docker Engine 29.4.0 and Compose 5.1.2 are installed.
- `docker compose config --quiet` passes.
- `postgres` (`pgvector/pgvector:pg16`) and `redis` (`redis:7-alpine`) are healthy and
  loopback-bound; Redis returns `PONG`.
- Docker PostgreSQL is 16.14 with pgvector 0.8.6.
- A clean Python 3.13 container on `ideas-mining_default` installed the declared package and
  test dependencies, then ran the full suite against `postgres:5432`.
- Default `alembic upgrade head` records 0001 and creates zero HNSW indexes.
- `downgrade base` removes all five application tables; re-upgrade recreates all five.
- The opt-in config applies exactly two `vector_cosine_ops` HNSW indexes.
- This pgvector/PostgreSQL pair successfully creates and drops an HNSW index with
  `CREATE INDEX CONCURRENTLY`, so no compatibility-based maintenance-window exception is
  needed.
- The Compose database was left empty at revision 0001; both services remain running.

Environment note: a separate Homebrew PostgreSQL already owns host port 5432. Host clients
therefore reach Homebrew rather than the Docker database. Validation ran inside the Docker
network, where `postgres:5432` is unambiguous.

### CodeRabbit review

Two scopes were required because Claude's cycle-3 source was already committed:

1. `--committed --base-commit 19da329` reviewed both cycle-3 Builder commits.
2. `--uncommitted --include-untracked` reviewed the final Validator changes.

Confirmed:

- Major: digest validation checks for one aggregate URL, not one URL per quote
  (`FINDING-3.5`).
- Major: the deferred HNSW migration uses blocking `CREATE INDEX` even though the deployed
  versions support concurrent construction (`FINDING-3.4`).
- Validator improvement: Alembic subprocesses now have an explicit timeout.
- Session-close feedback: resolve `TEST-CONFLICT-3.1`, populate `last_validation`, remove
  the stale blocker, and retain `OQ-6`; done at handoff.

Rejected:

- `claude_log.md` ownership is not contradictory: Claude appends its log; the Validator is
  forbidden from editing it.
- Test-only database environment access belongs in integration configuration. Adding an
  `IDEAS_MINING_TEST_DATABASE_URL` accessor to production `config.py` would violate the
  Validator write boundary and mix test controls into runtime configuration.

### Findings

#### [FINDING-3.1] [PASSED] — clean async dependency installation
- `sqlalchemy[asyncio]` installs greenlet in a new environment, and Alembic connects without
  an ad-hoc package install.
- Traces to: `DECISION-3.1`, `INV-5`.

#### [FINDING-3.2] [PASSED] — safe default migration and opt-in index mechanics
- Default head is 0001 with zero HNSW indexes. The deferred config sees 0002 and creates
  exactly two cosine indexes. The stale Validator path is fixed.
- Traces to: `DECISION-3.2`, `TEST-CONFLICT-3.1`.

#### [FINDING-3.3] [FAILED] — deferred revision breaks later normal deployments
- After the opt-in command records revision 0002, the next normal
  `alembic upgrade head` exits 255: `Can't locate revision identified by '0002'`.
  Hiding 0002 from the default ScriptDirectory also makes the default deployment path
  unable to understand databases that have legitimately applied it.
- Traces to: `DECISION-3.2`, `INV-5`.

#### [FINDING-3.4] [FAILED] — deferred HNSW creation blocks writes
- Both indexes use plain `CREATE INDEX` inside Alembic's transaction. The installed
  PostgreSQL/pgvector supports `CREATE INDEX CONCURRENTLY`; use Alembic's autocommit block,
  or explicitly enforce a maintenance window. The current migration does neither.
- Traces to: `DECISION-3.2`, `INV-5`.

#### [FINDING-3.5] [FAILED] — one linked quote masks an unlinked quote
- `validate_model_output()` accepts a digest containing one quote with a URL and a second
  quote without one because `_URL_RE.search(prose)` is aggregate. `INV-10` requires every
  quote to carry its own source URL.
- Traces to: `DECISION-3.3`, `INV-10`.

#### [FINDING-3.6] [PASSED] — other digest recovery contracts hold
- Empty/whitespace output, `max_tokens`, and output with no URL are rejected before any DB
  row, file, or SMTP attempt. Valid linked output passes. Prompt and persisted cluster IDs
  come from the same ranking.
- Traces to: `DECISION-3.3`, `DECISION-3.4`, `INV-5`, `INV-10`, `INV-11`.

#### [FINDING-3.7] [PASSED] — accepted batch ID remains recoverable
- Tracking-persistence failure logs the accepted Anthropic batch ID at ERROR and re-raises.
- Traces to: `DECISION-3.5`, `INV-5`.

#### [FINDING-3.8] [PASSED] — Reddit and HN isolate malformed third-party data
- All three Reddit failure domains preserve partial progress. HN timestamp parsing drops
  unusable values and attaches UTC to a naive ISO timestamp.
- Traces to: `DECISION-3.6`, `DECISION-3.7`, `INV-3`, `INV-5`.

#### [FINDING-3.9] [PASSED] — reclustering is FK-safe and preserves embeddings
- Live PostgreSQL accepts the DELETE-based clear, cluster memberships become null before
  reassignment, and stored embeddings remain unchanged.
- Traces to: `DECISION-3.8`, `INV-2`, `INV-5`, `INV-6`.

#### [FINDING-3.10] [PASSED] — Compose and worker hardening hold
- PostgreSQL/Redis binds are loopback-only. Log levels normalize padding/case and safely
  fall back to INFO for unknown and numeric strings.
- Traces to: `DECISION-3.9`, `DECISION-3.10`, `INV-5`.

#### [FINDING-3.11] [BLOCKER] — manual quality acceptance remains human-owned
- No proxy assertion was invented for pain-point normalization, vertical disagreements, or
  digest usefulness. Actual model output still needs human review.
- Traces to: `OQ-6`, `INV-1`, `INV-10`.

### Handoff
- Status: `VALIDATION_COMPLETE`; turn returned to Claude.
- Builder action: fix `FINDING-3.3`, `FINDING-3.4`, and `FINDING-3.5`.
- `TEST-CONFLICT-3.1` is resolved and removed from `open_blockers`.
- `OQ-6` remains `needs: human`.
