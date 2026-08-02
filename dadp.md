You are setting up an existing repository to run under DADP (Dual-Agent Development
Protocol). You are NOT building features and you are NOT writing tests. Your only job
is to install the protocol scaffolding and backfill it against what already exists in
this repo.

## The protocol you are installing

Two agents work the same repo in an alternating loop, communicating only through files:

- **Claude = Builder.** Writes source code only. Never writes or edits test files.
  After each build pass it logs what it built, why, the tradeoffs, and the concepts
  involved, then flips the router to hand off.
- **Codex = Validator.** Writes test files only. Never edits source. It reads the
  router, then Claude's log, then the source Claude touched — so it tests against the
  *stated intent and decisions*, not just the visible code. It logs findings and flips
  the router back.
- **Human (me).** Reads both logs to understand and steer. The logs are an audit trail
  AND a learning channel, so they must be written for a human reader.

Neither agent ever edits the other's log. Both always read `handoff.json` first.

## Step 1 — Survey before you touch anything

Map the repo: languages, frameworks, entry points, module boundaries, existing test
setup and runner, CI config, existing docs. Write nothing until this is done. If the
repo already has a CLAUDE.md, AGENTS.md, or any test suite, you are integrating with
it, not replacing it.

## Step 2 — Create this structure

```
.spec/
  system_spec.md          # the blueprint — invariants, contracts, module map
  modules/                # per-module specs (create dir, populate what you can)
.agent/
  claude_log.md           # Builder log — append-only
  codex_log.md            # Validator log — append-only
  handoff.json            # the router — structured state between agents
  ledger.jsonl            # append-only cross-cycle event ledger — BOTH agents write
  DASHBOARD.md            # generated from the ledger — never hand-edited
  prompts/
    claude_session.md     # session prompt for Builder
    codex_session.md      # session prompt for Validator
tools/                    # protocol tooling — Builder-owned source
  dadp_backfill.py        # rebuild ledger.jsonl from the logs + router
  dadp_report.py          # render DASHBOARD.md from ledger.jsonl + router
CLAUDE.md                 # Builder's standing instructions (repo root)
AGENTS.md                 # Validator's standing instructions (repo root)
```

Backfill `.spec/system_spec.md` from the code that already exists: what the system
does, its modules and responsibilities, data model, external dependencies, trust
boundaries, and the invariants you can infer. Mark anything you inferred rather than
confirmed as `[INFERRED — NEEDS HUMAN CONFIRMATION]`. Do not invent requirements.

Per-module spec sections: Purpose / Responsibilities / Non-Responsibilities /
Inputs-Outputs / State Machine / Data Model / API Contract / Invariants / Trust
Boundaries / Failure Modes / Security / Dependencies / Testing Requirements / Open
Questions.

## Step 3 — The router: `.agent/handoff.json`

This is the coordination primitive. Both agents read it first and both must update it
before ending a session. Create it with this exact schema, seeded to the current repo
state:

```json
{
  "protocol": "DADP",
  "version": "1.0",
  "project": "<repo name>",
  "cycle": 0,
  "turn": "claude",
  "status": "AWAITING_BUILD",
  "updated_at": "<ISO-8601>",
  "spec_ref": ".spec/system_spec.md",
  "last_build": {
    "cycle": null,
    "modules": [],
    "files_written": [],
    "files_modified": [],
    "decisions": [
      {
        "id": "DECISION-N.M",
        "traces_to": [],
        "summary": "",
        "rationale": "",
        "tradeoff": "",
        "affects": [],
        "test_implication": ""
      }
    ],
    "invariants_asserted": [],
    "spec_deviations": [],
    "test_targets": [],
    "known_edge_cases": [],
    "log_anchor": "claude_log.md#session-N"
  },
  "last_validation": {
    "cycle": null,
    "test_files": [],
    "results": { "passed": 0, "failed": 0, "blockers": 0 },
    "findings": [
      { "id": "FINDING-N.M", "severity": "PASSED|FAILED|BLOCKER",
        "target": "", "detail": "", "traces_to": [] }
    ],
    "log_anchor": "codex_log.md#session-N"
  },
  "open_blockers": [
    { "id": "OQ-N", "severity": "BLOCKER", "target": "", "detail": "",
      "traces_to": [], "needs": "builder|human" }
  ]
}
```

**`traces_to` is an array, everywhere it appears** — in `last_build.decisions[]`,
`last_validation.findings[]`, and `open_blockers[]`. It holds the ids this item came from:
`DECISION-*`, `FINDING-*`, `INV-*`, `OQ-*`. It is the edge list from which the cross-cycle
graphs are derived, so a scalar or a missing field silently deletes a link. (Earlier
revisions used a scalar `traces_to_decision` on blockers; that name is retired.)

`status` enum: `AWAITING_BUILD` → `BUILDING` → `READY_FOR_VALIDATION` → `VALIDATING`
→ `VALIDATION_COMPLETE` → (next cycle) `AWAITING_BUILD`, plus `BLOCKED`.
`turn` is `"claude"` or `"codex"` and is the only thing an agent needs to check to know
whether it may act. `test_targets` and `decisions[].test_implication` are how Claude
tells Codex what to test without writing tests itself.

## Step 3b — The history layer: `.agent/ledger.jsonl`

The router remembers exactly one cycle. The two logs are append-only prose. So nothing in
the protocol answers *"is this bug converging, or are we going in circles?"* without a
full re-read of both logs. `.agent/ledger.jsonl` is that missing layer: one JSON object per
line, one line per protocol event, spanning every cycle.

```json
{"cycle":3,"seq":12,"ts":"2026-08-01T00:00:00Z","agent":"claude","kind":"decision",
 "id":"DECISION-3.3","title":"validate_model_output gate before persistence",
 "status":null,"target":"ideas_mining/digest.py","modules":["digest"],
 "traces_to":["FINDING-2.6","INV-10"],"needs":null,
 "log_anchor":"claude_log.md#session-3"}
```

`kind` ∈ `decision | finding | blocker | deviation | result`.
`status` ∈ `PASSED | FAILED | BLOCKER | null`. `traces_to` is the same edge list as in
`handoff.json`, and is what the lifecycle graphs are built from.

### The ledger write rule — identical for both agents

`.agent/ledger.jsonl` is **the first file both agents write.** The existing write boundary
(source vs tests; never edit the other agent's log) does not cover it, so the rule is stated
explicitly and identically in `CLAUDE.md`, `AGENTS.md`, and here:

- **Append-only.** New rows go at the end. Never rewrite, reorder, reformat, or delete an
  existing line — not even your own, not even to fix a typo. Correct a mistake by appending
  a new row that supersedes it.
- **Own rows only.** Claude appends rows with `"agent":"claude"`; Codex appends rows with
  `"agent":"codex"`. Neither agent may touch a row belonging to the other.
- **One line, one object.** No pretty-printing, no trailing commas, no blank lines. A line
  that does not parse breaks every downstream graph.
- `seq` is a strictly increasing integer across the whole file, regardless of agent.

`tools/` holds the protocol tooling and is **Builder-owned source**:

- `tools/dadp_backfill.py` regenerates the whole ledger from the two logs and the router.
  It is a retrofit and a repair tool, not part of the session loop; it refuses to overwrite
  rows it cannot reproduce unless `--force`.
- `tools/dadp_report.py` renders `.agent/DASHBOARD.md` from the ledger and the router —
  open items, finding-lifecycle chains, invariant coverage, cycle health. Both agents run it
  at session end. `DASHBOARD.md` is generated; never hand-edit it.

Neither agent may hand-author Mermaid in a prose log. A diagram written by hand is a second
source of truth that drifts from the router; every graph must be derived from the ledger.

## Step 4 — Write CLAUDE.md (Builder)

Must state:
1. Read order at session start: `.agent/handoff.json` → `.agent/codex_log.md` (latest
   session, resolve all FAILED/BLOCKER first) → `.spec/system_spec.md` → relevant
   module specs.
2. Refuse to act if `turn != "claude"`.
3. Set `status: BUILDING` on start.
4. Write source only. Never create, edit, or delete files under the test directory.
5. Document every function: purpose, inputs, outputs, invariants, security notes.
6. Any divergence from spec gets tagged `[SPEC_DEVIATION]` with justification, in both
   the log and `handoff.json`.
7. Never edit `codex_log.md`.
8. At session end: append a session block to `.agent/claude_log.md`, append its own rows to
   `.agent/ledger.jsonl` (append-only, own rows only), regenerate the dashboard with
   `python3 tools/dadp_report.py`, populate `last_build` in `handoff.json`, set
   `turn: "codex"` and `status: "READY_FOR_VALIDATION"`.

Claude's log block format:

```markdown
## Session [N] — [DATE] — [PHASE]

### Context
Reading from: … | Building: … | Spec ref: … | Addressing from Codex: …

### Decisions
#### [DECISION-N.1] Title
- What / Why / [CONCEPT: plain-language explanation for the human] /
  Tradeoff / Alternatives considered

### Work Done
- path/to/file — what changed and why

### Invariants Verified
- [ ] invariant — how it was maintained

### Security Considerations
### Open Questions
### Handoff
- Status: READY_FOR_VALIDATION
- Codex should test: [explicit list, tied to decision IDs]
- Known edge cases: …
- Blockers: …
```

## Step 5 — Write AGENTS.md (Validator)

Must state:
1. Read order: `.agent/handoff.json` → `.agent/claude_log.md` (latest session, in full)
   → every file in `last_build.files_written` / `files_modified` → `.spec/system_spec.md`
   → relevant module specs.
2. Refuse to act if `turn != "codex"`. Set `status: VALIDATING` on start.
3. Write test files only. Never modify source. If source is broken, that is a
   `[FAILED]` or `[BLOCKER]` finding — not a fix.
4. Three test categories, all required:
   - **Correctness** — does the code do what `claude_log.md` says it does? One test per
     documented decision and stated invariant.
   - **Spec compliance** — does it satisfy `.spec/system_spec.md`? Every
     `[SPEC_DEVIATION]` gets explicitly probed.
   - **Robustness** — adversarial input, boundaries, failure modes, integration and
     API contract edges.
5. Every finding tagged `[PASSED]` / `[FAILED]` / `[BLOCKER]` and traced back to the
   decision ID or invariant it came from.
6. Never edit `claude_log.md`.
7. At session end: append to `.agent/codex_log.md`, append its own rows to
   `.agent/ledger.jsonl` (append-only, own rows only), regenerate the dashboard with
   `python3 tools/dadp_report.py`, populate `last_validation` and `open_blockers`, set
   `turn: "claude"`, `status: "VALIDATION_COMPLETE"`, increment `cycle`.

Use the repo's existing test framework and directory convention — detect it, don't
impose a new one. If none exists, pick the idiomatic one for the stack and document
the choice in `codex_session.md`.

## Step 6 — Session prompts

`.agent/prompts/claude_session.md` and `codex_session.md` are the pasteable per-session
prompts, each a condensed version of its agent's standing file plus "here is what to do
right now."

## Step 7 — Seed and verify

Seed both logs with a Session 0 block recording the repo's state at protocol adoption
(what exists, what test coverage exists today, what's unspecified). Then verify:
`handoff.json` parses, all paths referenced exist, CLAUDE.md and AGENTS.md have no
contradicting rules, and the write-boundaries (source vs tests) are unambiguous for
this repo's actual layout.

## Constraints

- Do not modify, refactor, or reformat any existing source or test file. Scaffolding
  only.
- Do not add dependencies.
- If the repo's structure makes any part of this ambiguous, write it as an
  `[OPEN QUESTION]` in `.spec/system_spec.md` rather than guessing.

## Report back

A tree of what you created, the inferred system model in five sentences, every
`[INFERRED]` and `[OPEN QUESTION]` you flagged, and anything about this repo that makes
the source/test write boundary hard to enforce.

### Codex.md: Write boundary

You may create and edit any file whose sole consumer is the test suite:
test files, fixtures, factories, mocks, stubs, test config, conftest/setup
files, sample payloads, snapshots — regardless of directory.

You may NOT edit any file that source code imports or reads at runtime.
Seed data, sample configs, and migration fixtures consumed by source belong
to the Builder, even if they look like test data.

If a test cannot be written without changing a Builder-owned file, do not
change it. File a [BLOCKER] naming the file and what change is needed, and
add it to `open_blockers` in handoff.json.

### Claude.md
CLAUDE.md: Test-only files (fixtures, mocks, factories, test config) belong to the
Validator. Do not create or edit them — if a build needs a new fixture,
declare it in `test_targets` and let Codex build it.