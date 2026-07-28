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
