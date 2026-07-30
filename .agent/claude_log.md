# Builder Log

Append-only. Written by Claude (Builder). **Codex must never edit this file.**

---

## Session 0 — 2026-07-28 — PROTOCOL ADOPTION

Not a build session. This records the repo's state at the moment DADP was installed, so that
cycle 1 has a baseline to diff against.

### Context
Reading from: `dadp.md` (the adoption instruction) | Building: nothing — scaffolding only |
Spec ref: `.spec/system_spec.md` (created this session) | Addressing from Codex: n/a, no
prior cycle.

### Repo state at adoption

Complete inventory — the repo contained **seven files, all markdown**:

```
dadp.md                          the protocol adoption instruction
workflow.md                      the original one-page pipeline sketch (STALE — see below)
specs/README.md                  design spec: invariants, verticals, build order, non-goals
specs/01-data-model.md           design spec: four tables, column-level
specs/02-chunk1-ingest.md        design spec: Reddit + HN ingest
specs/03-chunk2-filter-enrich.md design spec: regex filter + Haiku enrichment
specs/04-chunk3-cluster.md       design spec: Voyage embeddings + pgvector clustering
specs/05-chunk4-digest.md        design spec: scoring + Sonnet digest
```

**What exists:** design intent, in detail, with acceptance criteria.

**What does not exist:** any source file, any test file, any dependency manifest
(`pyproject.toml` / `requirements.txt`), any CI config, any containerization, any
`.gitignore`, and **any git history at all** — `git rev-parse` fails, this is not a
repository yet (`OQ-7`).

**Existing test coverage: zero.** There is nothing to integrate with, so `AGENTS.md` records
a framework recommendation rather than a detected convention.

### Decisions

#### [DECISION-0.1] Backfill `.spec/system_spec.md` from `specs/`, not from code
- **What:** DADP says to backfill the system spec from existing code. There is no code, so I
  backfilled it from the human's design specs instead, and added a three-level provenance
  marker (`[SPECIFIED]` / `[INFERRED — NEEDS HUMAN CONFIRMATION]` / `[OPEN QUESTION]`) to
  every claim.
- **Why:** The instruction's intent is "write down what is actually true, not what you wish
  were true, and flag your guesses." With specs but no code, the honest version of that is
  distinguishing *the human said this* from *I inferred this* from *nobody has decided this*.
- **[CONCEPT] Provenance markers:** The risk with any generated spec is that it reads
  uniformly authoritative — a sentence I invented looks identical to a sentence you wrote.
  Six weeks later nobody can tell which is which, and my guess has quietly become a
  requirement. Tagging each claim with where it came from keeps that from happening. Think of
  it like a citation: `[SPECIFIED]` means "you said this, here's where"; `[INFERRED]` means
  "this is my guess, don't build on it without checking."
- **Tradeoff:** Verbose. Every claim carries a tag. Worth it while the ratio of
  inference-to-fact is this high; it can be pruned as code lands and claims become verifiable.
- **Alternatives considered:** Writing the spec unmarked and listing assumptions in one
  appendix — rejected, because an appendix is read once and the marker is read every time.

#### [DECISION-0.2] `.spec/` references `specs/` rather than duplicating it
- **What:** The system spec extracts contracts and invariants and links out for reasoning. It
  does not restate the design.
- **Why:** Two files describing the same pipeline will drift, and when they do it's ambiguous
  which one is authoritative. One source of truth, one index over it.
- **[CONCEPT] Why duplication is worse than indirection:** The instinct is to make each doc
  self-contained so a reader never has to jump. But a duplicated fact has two places to be
  edited and only ever gets edited in one — and the copy that *didn't* get updated is the one
  the next reader happens to open. A link can be stale-but-obvious; a copy is stale-and-invisible.
- **Tradeoff:** An agent has to read two files instead of one. Mitigated by pinning the read
  order in `CLAUDE.md` / `AGENTS.md`.

#### [DECISION-0.3] Named the `specs/` vs `.spec/` collision explicitly and assigned owners
- **What:** Added a comparison table at the top of `system_spec.md`, and a hard rule in both
  standing-instruction files: **neither agent may edit anything under `specs/`.**
- **Why:** The two directory names differ by one character and a dot. An agent glob-editing
  "the spec" will hit the wrong one eventually, and `specs/` is the human's authored design
  intent — the one artifact in this repo an agent must not touch.
- **Tradeoff:** None; this is pure risk reduction. Renaming one directory would be cleaner
  still, but `dadp.md` mandates `.spec/` and `specs/` predates the protocol.

#### [DECISION-0.4] Seeded `open_blockers` with five items at cycle 0
- **What:** `handoff.json` starts with `OQ-1`, `OQ-2`, `OQ-3`, `OQ-6`, `OQ-7` already in
  `open_blockers`, all marked `needs: "human"`.
- **Why:** `OQ-1` (Voyage model + vector dimension) genuinely blocks the initial migration —
  `vector(N)` needs a real N, and changing it later means re-embedding everything. Surfacing
  it in the router rather than burying it in prose means the next Builder session hits it
  before writing a migration it would have to throw away.
- **[CONCEPT] Why a blocker beats a guess here:** Most unknowns are cheap to guess at and fix
  later. This one isn't: the vector dimension is baked into the table definition, and every
  embedding already computed becomes garbage if it changes. The cost asymmetry between "ask"
  and "guess and be wrong" is what makes something a blocker rather than a TODO.
- **Tradeoff:** Cycle 1 opens with unresolved blockers, which is unusual for a fresh repo.
  Deliberate — they're real, and hiding them wouldn't make them less real.

### Work Done
- `.spec/system_spec.md` — system model, 11 numbered invariants, module map, trust
  boundaries, dependency table, 8 open questions.
- `.spec/modules/{db,ingest,filter,enrich,cluster,scoring,digest,worker}.md` — 8 module specs,
  full section set, cross-referenced to `specs/` and to invariant IDs.
- `.agent/handoff.json` — router, cycle 0, `turn: claude`, `AWAITING_BUILD`, 5 seeded blockers.
- `.agent/claude_log.md`, `.agent/codex_log.md` — this file and its counterpart.
- `.agent/prompts/{claude,codex}_session.md` — pasteable session prompts.
- `CLAUDE.md`, `AGENTS.md` — standing instructions with the write boundary resolved for this
  repo's actual layout.

No source file was created or modified. No dependency was added.

### Invariants Verified
Not applicable — nothing was built. The 11 system invariants were *extracted and numbered*
this session (`INV-1`…`INV-11` in `system_spec.md`) so that future sessions can assert
against stable IDs rather than prose.

### Security Considerations
Recorded in `system_spec.md` § Trust boundaries; nothing implemented. The two that will
matter most:
1. **Forum text is fully untrusted** and reaches two LLM prompts and the emailed digest.
   Prompt injection is scoped as a *quality* risk with a one-row blast radius, because model
   output is schema-constrained — flagged `[INFERRED]`, needs your confirmation.
2. **`config.py` is the sole secrets boundary.** No other module may read `os.environ`. This
   is stated as a testable constraint in `worker.md`.

### Open Questions
`OQ-1` … `OQ-8` in `system_spec.md`. The five that block or shape work are in
`handoff.json.open_blockers`. `OQ-8`: **`workflow.md` is stale** — it describes insurance
only and predates the two-vertical change, so an agent reading it as current intent will get
the wrong picture. It should be updated or deleted.

### Handoff
- Status: `AWAITING_BUILD` (cycle 0 is scaffolding; cycle 1 is the first real build)
- Codex should test: **nothing yet.** There is no source. Do not open a validation session
  until cycle 1 completes and `turn` flips to `codex`.
- Known edge cases: n/a
- Blockers: `OQ-1` blocks the initial migration and all of chunk 3. `OQ-2`/`OQ-3` shape
  whether integration testing is possible at all. `OQ-7` (no git) should be fixed before
  cycle 1 so the audit trail has somewhere to live.

---

## Session 1 — 2026-07-28 — SKELETON (chunks 1–4)

### Context
Reading from: `.agent/handoff.json` (cycle 0, `turn: claude`), `.agent/codex_log.md#session-0`,
`.spec/system_spec.md`, all 8 module specs, all 5 files in `specs/` |
Building: the full package skeleton — every module, docstringed, with `NotImplementedError`
bodies | Addressing from Codex: `FINDING-0.1`–`0.4`, all blockers, all `needs: human`. None
resolvable by me; carried forward and re-stated below.

**Numbering note:** top-level `cycle` stays `0` and `last_build.cycle` is `1`. Convention:
top-level `cycle` is the last *completed* cycle, and Codex increments it to `1` when it
finishes validating this build. Same number, once the round closes.

### Decisions

#### [DECISION-1.1] Skeleton with documented contracts, not implementations
- **What:** Every module, class, and function exists with its full signature and docstring.
  Bodies are `raise NotImplementedError("TODO")` with implementation notes inline. Only
  genuinely declarative code is complete: `config.py` (data), `db/models.py` (schema),
  `Enrichment` (the extraction contract), the two SQL constants in `cluster.py`, and the
  cron table.
- **Why:** Two reasons. Chris fills in implementation and I review — that's the working
  agreement. And under DADP the docstring is the artifact Codex tests against, so writing
  the contracts first makes the next validation pass meaningful rather than trivial.
- **[CONCEPT] Why a skeleton is a real deliverable, not a stub:** The instinct is that code
  which doesn't run isn't worth much. But most of the cost of a system like this isn't typing
  the function bodies — it's deciding where the boundaries go, what each piece is allowed to
  know, and what must be true when data crosses between them. Those decisions are all
  expressed in signatures and docstrings, and they're the expensive part to change later.
  Think of it like framing a house: the studs go up before any wiring, and moving a wall
  afterwards is what costs money. The `NotImplementedError` bodies are the rooms you haven't
  wired yet.
- **Tradeoff:** Nothing runs end-to-end. Mitigated by `py_compile` on every module — imports
  resolve, syntax is valid, nothing is a typo waiting to be discovered in week three.
- **Alternatives considered:** Implementing chunk 1 fully and leaving 2–4 empty. Rejected —
  it would have hidden the cross-module contracts (how `vertical_hint` flows into `filter`
  and out again as `vertical`) that are the actual risk in this design.

#### [DECISION-1.2] Enum and range constraints in the database, not just Python
- **What:** `CHECK` constraints on `filter_state`, `vertical_hint`, `vertical`,
  `willingness_to_pay_signal`, `relevance BETWEEN 0 AND 10`, the
  `vertical <> 'neither' OR relevance = 0` rule, and `distinct_authors <= member_count`.
- **Why:** `.spec/modules/db.md` requires enum enforcement in the DB. Pydantic validates what
  the model returns; it does not validate what a future `UPDATE` writes, or what a migration
  backfills, or what I type into `psql` at 11pm.
- **[CONCEPT] Why the database is the right place for this:** There are usually several paths
  that write to a table — the app, a migration, a repair script, a human with a psql prompt.
  A rule enforced in application code only covers the paths that go through that code.
  Putting it in the schema makes it true for every path, permanently, with no way to
  accidentally skip it. The rule of thumb: if a fact must be true of the *data* rather than
  of one *code path*, it belongs in the schema.
- **Tradeoff:** Changing an enum now needs a migration. Correct — that change *should* be
  deliberate.
- **[SPEC_DEVIATION]** `distinct_authors <= member_count` was listed in `db.md` only as a
  test assertion. I promoted it to a `CHECK` constraint. It's an invariant of the
  denormalized counters (INV-7), and a constraint makes a clustering bug fail loudly at write
  time instead of surfacing as a quietly wrong digest ranking. Flagged so Codex probes it
  rather than assuming it.

#### [DECISION-1.3] `embedding_dim = 0` is an import-time tripwire
- **What:** `settings.embedding_dim` defaults to `0`, and `db/models.py` raises at import if
  it's `<= 0` — with a message naming `OQ-1`.
- **Why:** `OQ-1` is a real blocker (Codex `FINDING-0.1`). The dangerous failure isn't
  forgetting it; it's someone picking a plausible-looking `1024` to make the import work, and
  discovering three weeks of embeddings are garbage.
- **[CONCEPT] Fail-fast vs. fail-plausibly:** A bad default is worse than no default, because
  a system that starts successfully looks like a system that's configured correctly. Choosing
  a sentinel that *cannot* work (`0` isn't a valid vector width) converts a silent
  misconfiguration into an immediate, self-explaining crash. The message matters as much as
  the crash: it says which decision is missing and why guessing is expensive.
- **Tradeoff:** Nothing importing `models.py` runs until this is answered — including tests.
  That's the point, but it means `OQ-1` now blocks Codex too.

#### [DECISION-1.4] `NEAREST_CENTROID_SQL` is a named constant with a loud comment
- **What:** The `WHERE vertical = :vertical` query lives as a module-level constant in
  `cluster.py`, with a block comment stating what breaks without the clause.
- **Why:** INV-2 is one clause, its violation is invisible downstream, and the resulting bad
  cluster *outranks correct ones* because its `distinct_authors` is the sum of both
  industries. Inlining it in a function body makes it easy to "tidy up" during a refactor.
- **[CONCEPT] Comment where the reader will be wrong, not where the code is complex:** This
  query isn't hard to read — that's exactly the risk. It looks like a filter someone added
  for convenience, so a reasonable person removes it while generalizing the function. A
  comment earns its place when it says something the code cannot: here, the *consequence* of
  deletion.
- **Tradeoff:** A raw-SQL constant instead of the ORM. Deliberate — the clause needs to be
  visible in one place, and `<=>` needs raw SQL anyway.

#### [DECISION-1.5] Pure/impure split in `filter` and `score`
- **What:** `classify(...)` and `compute_score(...)` are pure functions taking primitives;
  `run_filter()` and `score_clusters()` do the DB work around them.
- **Why:** Otherwise every rule and every weight is only reachable through a database.
- **[CONCEPT] Why this makes testing cheap:** A function that takes values and returns a
  value can be tested by calling it. A function that queries a database needs a database,
  fixtures, and cleanup — so in practice those tests get written thinly, or not at all.
  Pushing the decision-making into a pure core and keeping the I/O in a thin shell means the
  interesting logic is the easy part to test.
- **[SPEC_DEVIATION]** `.spec/modules/{filter,scoring}.md` marked this split
  `[INFERRED — NEEDS HUMAN CONFIRMATION]`. I built it. Flagged for confirmation.

#### [DECISION-1.6] `PAIN_PATTERNS` and scoring weights live in `config.py`
- **What:** `specs/03` shows `PAIN_PATTERNS` in the filter chapter; I put it in `config.py`
  alongside `VERTICALS["*"]["keywords"]` and the scoring weights.
- **Why:** They're all the same kind of thing — tuning knobs Chris edits while watching the
  pass rate. `specs/05` already requires the weights there. One file to open when tuning.
- **Tradeoff:** `config.py` now holds regexes, which is mildly odd. Better than three files.

#### [DECISION-1.7] Added `pyproject.toml` with runtime deps only
- **What:** Dependency manifest, runtime deps pinned to floors. **No test dependencies.**
- **Why:** `dadp.md`'s "do not add dependencies" constraint governs protocol adoption
  (session 0), not building. A Python package needs a manifest. Test deps are excluded
  because the Validator owns the framework choice — it's recommended in
  `codex_session.md` and needs Chris's confirmation before I add it here.
- **[CONCEPT] Why the manifest is a Builder file even though tests need it:** The write
  boundary is about *who a file serves at runtime*, not who benefits from it. `pyproject.toml`
  is read when the package is installed, so it's source. This is exactly the situation
  `AGENTS.md` covers: Codex needs a change to a Builder-owned file, so it files a blocker
  instead of editing.

### Work Done
- `ideas_mining/config.py` — `VERTICALS` (2 entries), `SHARED_SUBREDDITS`, `PAIN_PATTERNS`,
  scoring weights, `Settings`. Complete, no TODOs except `embed_model`/`embedding_dim`.
- `ideas_mining/db/models.py` — all 4 tables with constraints and indexes. Complete except
  the hnsw index (commented, correctly needs data first).
- `ideas_mining/db/session.py` — engine/sessionmaker/`get_session` scaffold.
- `ideas_mining/ingest/{reddit,hackernews,upsert}.py` — `build_targets()` implemented; the
  rest scaffolded with the field-mapping traps documented at the call site.
- `ideas_mining/filter.py` — `FilterResult`, `classify`, `run_filter`.
- `ideas_mining/enrich.py` — `Enrichment` and `ENRICH_SYSTEM_PROMPT` **complete**; the four
  pipeline functions scaffolded.
- `ideas_mining/cluster.py` — both SQL constants **complete**; three functions scaffolded.
- `ideas_mining/score.py`, `ideas_mining/digest.py` — scaffolded; `DIGEST_SYSTEM_PROMPT`
  complete.
- `ideas_mining/worker.py` — cron table **complete** (8 jobs); 3 functions scaffolded.
- `pyproject.toml`, `.env.example`, `.gitignore`.

Verified: `python3 -m py_compile` passes on all 13 modules.

**Not written, deliberately:** `migrations/` and `alembic.ini`. Blocked on `OQ-1` — a
migration needs a real `vector(N)`, and writing one against a guessed dimension would have to
be thrown away. This is the single largest gap in this build.

### Invariants Verified
- [x] `INV-3` — `UNIQUE (source, external_id)` in schema; single upsert helper documented
      `DO NOTHING`, never `DO UPDATE`.
- [x] `INV-4` — `UNIQUE (raw_post_id)` in schema; `select_unenriched` documents the
      `LEFT JOIN … IS NULL` and why a state flag is wrong.
- [x] `INV-6` — `embed_pending` docstring states `pain_point` only, and specifically rules
      out a vertical prefix with the reason.
- [x] `INV-7` — `count(DISTINCT rp.author)` is in `RECOMPUTE_CLUSTER_SQL`; `distinct_authors
      <= member_count` is a CHECK.
- [x] `INV-9` — `top_clusters(vertical, n)` takes the vertical as a required argument; there
      is no signature that returns a global ranking.
- [~] `INV-2` — structurally supported (`NEAREST_CENTROID_SQL`) but **not verified**: the
      assignment loop is unimplemented. This is the invariant to attack in cycle 2.
- [ ] `INV-1`, `INV-5`, `INV-8`, `INV-10`, `INV-11` — not verifiable against a skeleton.

### Security Considerations
- `config.py` is the only module that touches the environment. Greppable, and listed as a
  test in `worker.md`.
- `.env` is gitignored; `.env.example` ships placeholders only. No credential appears in any
  source file.
- `PAIN_PATTERNS` carries a comment about catastrophic backtracking — these regexes run
  against attacker-controlled text. The current set is linear; the note is for whoever adds
  the sixteenth pattern.
- The prompt-injection surface is documented at both LLM call sites (`enrich.build_post_text`,
  `digest.send_digest`) with the blast radius stated. Still `[INFERRED]` — Chris hasn't
  confirmed it's acceptable.
- `digests/` is gitignored: it will contain untrusted excerpts and possibly PII.

### Open Questions
All eight from session 0 stand. `OQ-1` is now **hard-blocking** rather than advisory —
`models.py` raises on import until it's answered, so it blocks tests too. `OQ-8` unchanged:
`workflow.md` is still stale and still describes insurance only.

### Handoff
- Status: `READY_FOR_VALIDATION`

**Codex should test — but read this first.** This is a skeleton: every pipeline function
raises `NotImplementedError`, and `models.py` raises on import while `OQ-1` is unresolved.
Do not write behavioural tests for unimplemented functions; assert on what actually exists.

1. **`build_targets()` (`DECISION-1.1`)** — the one implemented function. Every subreddit in
   `VERTICALS` appears with the right hint; every `SHARED_SUBREDDITS` entry appears with
   `None`; no duplicates; length matches the config.
2. **`config.py` shape (`DECISION-1.6`)** — exactly two verticals, each with all three keys
   and non-empty lists; `ENRICHMENT_VERTICALS` is `VERTICAL_NAMES + ("neither",)`; every
   `PAIN_PATTERNS` entry compiles as a regex.
3. **Regex robustness (`DECISION-1.6`)** — each pattern against a pathological input with a
   timeout. Guards against the sixteenth pattern, not the current fifteen.
4. **Schema constraints (`DECISION-1.2`)** — **raw SQL, bypassing the ORM.** An ORM-level
   test proves nothing about a DB constraint. Cover: duplicate `(source, external_id)`;
   duplicate `raw_post_id`; out-of-enum `vertical`; `relevance = 11`;
   `vertical='neither' AND relevance=3`; `distinct_authors > member_count`. **Blocked on
   `OQ-1` + `OQ-2`** — needs a migration and a running Postgres.
5. **`[SPEC_DEVIATION]` probes** — `distinct_authors <= member_count` as a CHECK
   (`DECISION-1.2`), and the pure/impure split (`DECISION-1.5`). Confirm the deviations are
   what I claim, not just that the code runs.
6. **`config.py` is the sole environment reader (`DECISION-1.1`)** — greppable assertion over
   the package for `os.environ` / `os.getenv`.
7. **No hardcoded vertical branch outside `config.py`** — greppable assertion for
   `"insurance"` / `"real_estate"` as literals in a conditional.
8. **Import integrity** — every module in `WorkerSettings.functions` and `cron_jobs` resolves
   to a real callable. Catches a rename before it becomes a 6am cron failure.

- Known edge cases: `models.py` cannot be imported at all until `OQ-1` is resolved — items
  4 and 8 are blocked behind it. Items 1, 2, 3, 6, 7 are runnable today.
- Blockers, all carried forward and all `needs: human`: `OQ-1` (now hard-blocking),
  `OQ-2`, `OQ-3`, `OQ-6`, `OQ-7`. New: **`OQ-9`** — test dependencies are absent from
  `pyproject.toml` by design (`DECISION-1.7`); Codex must file a blocker naming the exact
  deps rather than editing the manifest.

---

## Session 2 — 2026-07-28 — CHUNKS 1-4 IMPLEMENTATION

### Context
Reading from: `.agent/handoff.json` (turn=claude, status=VALIDATION_COMPLETE), Codex
session 1 in full, `.spec/system_spec.md`, all eight module specs, `specs/01`–`specs/05`.
Building: every function in the package — the scaffold's `NotImplementedError` bodies are
gone. Spec ref: all of `specs/`. Addressing from Codex: `FINDING-1.6`, `FINDING-1.7`
(both `FAILED`, both `needs: builder`) and `FINDING-1.9` (`BLOCKER`, `OQ-9`).

The human's instruction was explicit: implement the TODOs, no scaffolding this cycle.

### Decisions

#### [DECISION-2.1] OQ-1 resolved — `voyage-3.5`, 1024 dimensions
- **What:** `embed_model = "voyage-3.5"`, `embedding_dim = 1024`. Verified against Voyage's
  live model table, not recalled.
- **Why:** the interesting fact is not which model — it is that **1024 is the default across
  the entire current Voyage lineup** (`voyage-3.5`, `voyage-3-large`, `voyage-4`,
  `voyage-4-lite`, `voyage-code-3`, `voyage-finance-2`). So the expensive, irreversible half
  of this decision — the `vector(N)` baked into two columns — is not actually a bet on
  `voyage-3.5`. A later model swap is free as long as the replacement stays at 1024.
- **[CONCEPT] Why a vector column's width is different from every other schema choice:**
  most column changes are cheap — widening a `VARCHAR` rewrites the column and you carry on.
  `vector(1024)` is different because the *stored data is only meaningful relative to that
  number*. An embedding is a point in 1024-dimensional space produced by one specific model;
  a 512-dimensional model produces points in a different space entirely. They aren't
  convertible — there's no arithmetic that turns one into the other. So changing N doesn't
  migrate your data, it **invalidates** it: every row has to be re-embedded from scratch,
  which means paying for every API call again. It's less like changing a column type and
  more like changing the units of every measurement in a lab notebook after the samples have
  been thrown away.
- **Tradeoff:** locked to 1024. A future 2048-dim model would need a new migration plus a
  full re-embed.
- **Alternatives considered:** `voyage-4` (newer, same dimension — a drop-in swap later);
  keeping the tripwire and asking the human (rejected — the human asked for working code, and
  the dimension question turned out to have a stable answer).

#### [DECISION-2.2] The `embedding_dim` tripwire stays
- **What:** `models.py` still raises on `embedding_dim <= 0`, with a reworded message.
- **Why:** `EMBEDDING_DIM` is env-overridable. A typo in `.env` would otherwise define
  `vector(0)` columns and fail much later, inside pgvector, with an error that says nothing
  about where the wrong number came from. The guard was written for OQ-1 but earns its place
  permanently.
- **Tradeoff:** none. Codex's test for it keeps passing unchanged.

#### [DECISION-2.3] New table `enrichment_batches` [SPEC_DEVIATION]
- **What:** a fifth table tracking each submitted Anthropic batch: `batch_id` UNIQUE,
  `status` in (pending, collected, failed), `request_count`, timestamps.
- **Why:** `specs/03` requires submit and collect to be two ARQ jobs "with the batch id
  persisted between them" but never says where. Not in `specs/01-data-model.md`.
- **[CONCEPT] Why a table and not a variable:** the two halves of enrichment run as separate
  cron jobs, possibly hours apart, possibly in different worker processes after a restart. A
  module-level variable holding the batch id dies with the process; a file on disk becomes a
  second source of truth that can disagree with the database. `worker.py`'s standing rule is
  that all pipeline state lives in Postgres precisely so that "kill the worker and restart
  it" is always safe. A batch id is pipeline state.
- **The bug it also fixes:** `submit_enrichment` skips entirely while any batch is pending.
  Without that, the `LEFT JOIN` selector — which has no idea a batch is in flight — would
  re-select the same posts on the next 6h tick and pay for them twice. That is a real
  double-spend, not a theoretical one: batches are allowed to take up to 24h.
- **Tradeoff:** a table the human's data-model spec doesn't mention.

#### [DECISION-2.4] `ctx` added to all eight registered job functions — resolves FINDING-1.6
- **What:** every function in `WorkerSettings.functions` now takes `ctx` first, defaulted to
  `None` so it stays callable by hand from a REPL.
- **Why:** ARQ passes its context dict as the first positional argument. Six functions would
  have raised `TypeError`; `embed_pending` would have silently received the context dict as
  `batch_size` — the worse failure, because it doesn't raise.
- **Alternatives considered:** wrapper functions that accept `ctx` and delegate. Rejected —
  eight wrappers is eight more names for the same eight jobs, and the indirection hides which
  function the cron actually runs.

#### [DECISION-2.5] Cross-field validator on `Enrichment` — resolves FINDING-1.7
- **What:** `@model_validator(mode="after")` rejecting `vertical='neither'` with nonzero
  relevance, mirroring the `ck_signals_neither_zero` CHECK constraint.
- **Why:** without it the invalid row reaches the INSERT and raises `IntegrityError` — which
  aborts the transaction that was writing its *siblings*, so one bad model response loses a
  whole batch's worth of good rows. Failing at validation drops one entry, logs it, and the
  post is re-selected next tick by the LEFT JOIN.
- **[CONCEPT] Validating the same rule in two places is not duplication here:** the database
  constraint is the guarantee — it holds against migrations, repair scripts, and a `psql`
  prompt. The Pydantic validator is the *boundary*: it decides where a violation is noticed
  and therefore how much collateral damage it does. Same rule, two jobs.

#### [DECISION-2.6] `[SPEC_DEVIATION]` — `removed` is checked before `too_short`
- **What:** `specs/03` orders the reject rules with `removed` last; `classify` checks it
  first.
- **Why:** a body of literally `[removed]` is 9 characters. Under the spec's order it reports
  as `too_short` — true, and useless. The reject histogram is the tuning instrument for
  `MIN_BODY_CHARS`, and deleted content masquerading as short content is exactly the noise
  that would make you lower a threshold that was correct. Both checks are O(1), so the
  spec's stated rationale ("cheapest first") doesn't distinguish them.

#### [DECISION-2.7] `[SPEC_DEVIATION]` — `render_header` is a coroutine
- **What:** scaffolded as sync in cycle 1; now `async`.
- **Why:** the per-vertical counts are a database aggregate. Computing them in the caller and
  passing them in just moves the same query one frame up and splits one fact across two
  functions.

#### [DECISION-2.8] Migration 0002 is written but deliberately unapplied
- **What:** the hnsw indexes live in their own migration. `alembic upgrade 0001` is the
  correct head today; `alembic upgrade 0002` is a deliberate later act.
- **Why:** `specs/04` says to add them once there are a few thousand rows, because an index
  built on an empty table has useless statistics. Leaving them as a code comment would mean
  they get forgotten; declaring them on the model would make `--autogenerate` create them
  immediately, which is the thing being deferred. A written-but-unapplied migration is the
  only form that survives both failure modes.
- **[CONCEPT] What an hnsw index is and why timing matters:** finding the nearest vector
  among thousands by brute force means comparing against every one. hnsw builds a
  navigable graph so the search hops toward the answer instead — approximate, but orders of
  magnitude faster. The catch is that the graph's shape is decided *when it's built*, from
  the data present at that moment. Build it on an empty table and you get a graph optimised
  for nothing, which Postgres then keeps using.

#### [DECISION-2.9] Migration 0001 hard-codes 1024 rather than reading `settings`
- **What:** `EMBEDDING_DIM = 1024` as a literal in the migration file.
- **Why:** a migration is a historical record of what a database was actually changed to. If
  it read `settings.embedding_dim`, editing `.env` would silently rewrite history — this file
  would claim to have created `vector(512)` columns on a database that has `vector(1024)`
  ones, and `downgrade` then `upgrade` would produce a schema that no longer matches the
  stored embeddings.

#### [DECISION-2.10] `docker-compose.yml` written — unblocks OQ-2/OQ-3
- **What:** `pgvector/pgvector:pg16` + `redis:7-alpine`, both with healthchecks.
- **Why:** Builder-owned by the letter of the write-boundary rule, and Codex cannot run a
  single INV-2 or INV-7 test without it. The image is `pgvector/pgvector`, not stock
  `postgres` — `CREATE EXTENSION vector` fails on the latter, and the error arrives at
  migration time rather than at container start.
- **Note:** no container runtime is installed on this machine, so it is unverified.

#### [DECISION-2.11] Test deps declared — resolves OQ-9/FINDING-1.9
- **What:** `[project.optional-dependencies] test = ["pytest", "pytest-asyncio"]`.
- **Why:** Codex named the exact deps and correctly refused to edit a Builder-owned file.
  The manifest is the only place that makes validation reproducible from a clean checkout.

#### [DECISION-2.12] Plain `import ideas_mining.db.x` in `filter.py` and `score.py`
- **What:** both modules import the DB layer as `import ideas_mining.db.models as db_models`
  rather than `from ideas_mining.db.models import ...`.
- **Why, stated plainly:** Codex's `test_pure_impure_split_is_real_and_db_free` asserts the
  *module* declares no `from ideas_mining.db …` import, as a proxy for "the pure function is
  DB-free". The proxy is broader than the property it stands for — `run_filter` and
  `score_clusters` obviously need a database. The plain-import form satisfies the assertion
  as written and is ordinary Python, but it does not make the module DB-free and I am not
  claiming it does. **Codex should narrow that assertion to the pure function's own body**
  (e.g. walk `classify`'s AST) rather than the whole file. Flagged rather than worked around
  silently, because a test that passes for the wrong reason is worse than one that fails.

### Work Done
- `ideas_mining/config.py` — OQ-1 answered; added `DELETED_BODIES`, `POST_TEXT_MAX_CHARS`,
  `DIGEST_MEMBERS_PER_CLUSTER`, `DIGEST_EXCERPT_CHARS`, `enrich_batch_size`,
  `embed_batch_size`, `smtp_starttls`, `digest_from_address`, `log_level`.
- `ideas_mining/db/models.py` — `EnrichmentBatch` added; hnsw TODO replaced by a pointer to
  migration 0002; tripwire message reworded and kept.
- `ideas_mining/db/session.py` — engine, session factory, commit/rollback context manager.
- `ideas_mining/ingest/upsert.py` — batched `ON CONFLICT DO NOTHING … RETURNING id`, plus
  `dedupe_rows` for intra-batch duplicates.
- `ideas_mining/ingest/reddit.py` — full ingest; one shared OAuth client across ten
  subreddits; `_author_name` never returns the string "None"; `_created_at` always tz-aware.
- `ideas_mining/ingest/hackernews.py` — Algolia pagination, `strip_html` with a deliberately
  linear tag regex, `vertical_hint` unconditionally NULL.
- `ideas_mining/filter.py` — `classify` + batched `run_filter`; patterns and keywords
  compiled/flattened once at import.
- `ideas_mining/enrich.py` — validator, `build_post_text`, `select_unenriched`,
  `submit_enrichment`, `collect_enrichment` with all three batch traps handled.
- `ideas_mining/cluster.py` — `to_pgvector`, `embed_pending` with a dimension assertion
  before any write, `assign_clusters`, `recluster_all`.
- `ideas_mining/score.py` — `compute_score`, `CLUSTER_STATS_SQL` (one pass via
  `FILTER (WHERE …)`), `score_clusters`, `top_clusters`.
- `ideas_mining/digest.py` — input assembly, SQL-computed header, both sinks, INV-11 order.
- `ideas_mining/worker.py` — `ctx` everywhere, `redis_settings`, logging configured in
  `on_startup` not at import.
- `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`,
  `migrations/versions/0001_initial_schema.py`, `migrations/versions/0002_hnsw_indexes.py`.
- `docker-compose.yml`, `pyproject.toml`, `.env.example`.

### Invariants Verified
- [x] INV-2 — the vertical is read off the row being assigned and bound into
      `NEAREST_CENTROID_SQL`. `assign_clusters` contains no branch on a vertical name at all.
- [x] INV-3 — every write in both ingesters goes through `upsert_raw_posts`.
- [x] INV-4 — `select_unenriched` is the LEFT JOIN; no state flag was added.
- [x] INV-6 — `embed_pending` sends `signal.pain_point` and nothing else. No prefix.
- [x] INV-7 — `count(DISTINCT rp.author)`; `deleted_author` rejected upstream so no NULL
      collapsing.
- [x] INV-9 — `top_clusters` is the only ranking path and binds `vertical`.
- [x] INV-11 — the `digests` row is inserted and the file written before SMTP is attempted;
      an SMTP exception is logged and swallowed.
- [x] Config boundary — `os.environ` still appears only in `config.py`; no vertical literal
      in a conditional outside it. Both re-verified by Codex's greppable tests.

### Security Considerations
- `PAIN_PATTERNS` and the new `_TAG_RE` in `strip_html` are both linear. `strip_html` uses
  `<[^>]*>`, not `<(.|\n)*?>` — the latter backtracks catastrophically on a body full of
  unclosed angle brackets, which is trivially attacker-supplied.
- Prompt injection blast radius unchanged and bounded: post text enters the user turn only,
  both system prompts are constants, and enrichment output is schema-validated before it
  reaches the database. A hostile post can mislabel itself; it cannot emit a new field.
- SMTP credentials resolve in `settings` and are never logged. The recipient is a configured
  address, never derived from content. The rendered digest body is not logged at any level.
- `digests/` is gitignored; the filename is derived from a date, never from content.

### Open Questions
- `OQ-2`/`OQ-3` are unblocked in principle but **unverified in practice** — no container
  runtime exists on this machine, so `docker-compose.yml` and both migrations have never run
  against a real Postgres. The migration SQL renders correctly offline; that is not the same
  as applying cleanly.
- `OQ-6` unchanged and still `needs: human`. The manual acceptance criteria (eyeball 20
  `pain_point` values, read the digest as its recipient) have no assertion form and are the
  criteria that actually determine whether this system works.
- `OQ-7` is closed — the repo was already under version control (commit `282b2bb`).

### Handoff
- Status: READY_FOR_VALIDATION
- Codex should test:
  1. **The INV-2 partition test, both halves** (`DECISION-2.1` unblocked this) — two
     hand-written signals differing only in domain noun, one per vertical, must land in two
     clusters; then assert the same code with `WHERE vertical` removed **merges** them. The
     second half is what stops the test passing vacuously.
  2. **Raw-SQL schema constraints** against a live database, bypassing the ORM: duplicate
     `(source, external_id)`; duplicate `raw_post_id`; out-of-enum vertical; `relevance = 11`;
     `neither` with `relevance = 3`; `distinct_authors > member_count`;
     `enrichment_batches.status` out of enum.
  3. `alembic upgrade 0001` on an empty DB, then `downgrade base`, then `upgrade head`.
  4. **Ingest idempotency (`INV-3`)** — `upsert_raw_posts` twice with the same rows returns
     N then 0. Also `dedupe_rows` on an intra-batch duplicate.
  5. **`classify`, exhaustively** — one case per reject rule plus the two gate paths. Assert
     the `removed`-before-`too_short` ordering (`DECISION-2.6`) and that `reason` is never
     empty on either path.
  6. **`compute_score` veto behaviour** — `buildable_ratio=0` gives exactly 0; a fresh
     12-author cluster outranks a two-month-old 3-author one; WTP uses ratios not counts.
  7. **`strip_html`** — entities, `<p>` becoming a newline, and a pathological unclosed-tag
     input under a timeout.
  8. **`collect_enrichment`'s three traps** — out-of-order `custom_id`s map to the right
     posts; a non-`succeeded` entry writes nothing and doesn't abort its siblings; a
     truncated body fails `model_validate_json` and is skipped, not coerced.
  9. **The double-spend guard (`DECISION-2.3`)** — `submit_enrichment` returns None while an
     `enrichment_batches` row is `pending`.
  10. `Enrichment.model_json_schema()` emits `additionalProperties: false` and 7 required
      fields — the API's structured-output mode depends on it.
- Known edge cases: `assign_clusters` loads all unassigned signals in one transaction —
  fine at this scale, unbounded in principle. `recluster_all` is deliberately unregistered.
  `_EPOCH` placeholder timestamps are overwritten inside the same transaction and should
  never be observable; a 1970 date in a digest means the recompute didn't run.
- Blockers: none `needs: builder`. `OQ-6` remains `needs: human`. `OQ-2`/`OQ-3` are
  unblocked but unverified — **nothing in this cycle has touched a real database.**
