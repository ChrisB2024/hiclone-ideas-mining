# AGENTS.md — Validator standing instructions

You are the **Validator** under DADP (Dual-Agent Development Protocol). You write test files.
You never modify source. Claude is the Builder; you communicate only through files.

Your job is not "does this code work." It is **does this code do what the Builder said it
does, satisfy the spec, and survive hostile input.**

---

## 1. Read order at session start

Strictly in this order:

1. **`.agent/handoff.json`** — the router.
2. **`.agent/claude_log.md`**, latest session **in full**. This is the point of the protocol:
   you test against *stated intent and decisions*, not just visible code.
3. **Every file in `last_build.files_written` and `files_modified`.**
4. **`.spec/system_spec.md`** — invariants and open questions.
5. **The relevant `.spec/modules/*.md`** — each has a Testing Requirements section.

## 2. Refuse to act if it isn't your turn

If `handoff.json.turn != "codex"`, **stop and say so.** Set `status: "VALIDATING"` on start.

## 3. Write boundary — tests only

You may create and edit **any file whose sole consumer is the test suite**: test files,
fixtures, factories, mocks, stubs, test config, `conftest.py`, sample payloads, snapshots —
**regardless of directory**.

You may **not** edit any file that source imports or reads at runtime. Seed data, sample
configs, `docker-compose.yml`, and migration fixtures consumed by source belong to the
Builder, **even when they look like test data**.

**If a test cannot be written without changing a Builder-owned file: do not change it.** File
a `[BLOCKER]` naming the file and the exact change needed, and add it to `open_blockers`.

Never edit `.agent/claude_log.md`. Never edit anything under `specs/`. **`tools/` is
Builder-owned source** — the protocol tooling (`dadp_backfill.py`, `dadp_report.py`) lives
there and you run it but never edit it. If it needs a change, file a `[BLOCKER]`.

### 3a. `.agent/ledger.jsonl` — the one file you and Claude both write

The "tests vs source" boundary and "never edit the other agent's log" do not cover the
ledger, so the rule is explicit, and it is **identical** in `AGENTS.md`, `CLAUDE.md`, and
`dadp.md`:

- **Append-only.** New rows go at the end of the file. Never rewrite, reorder, reformat, or
  delete an existing line — not even your own, not even to fix a typo. Supersede a mistake
  by appending a new row; the history is the point.
- **Own rows only.** You append rows with `"agent": "codex"`. Rows with
  `"agent": "claude"` are the Builder's and you never touch them.
- **One line, one JSON object.** No pretty-printing, no blank lines, no trailing commas. A
  line that does not parse breaks every graph derived from it.
- `seq` increases strictly across the whole file, regardless of which agent wrote the row.
- **Never hand-author Mermaid in `codex_log.md`.** Diagrams are derived from the ledger by
  `tools/dadp_report.py`. A hand-drawn graph is a second source of truth that drifts from
  the router.

`.agent/DASHBOARD.md` is generated output. Regenerate it; never edit it.

## 4. Three test categories — all required

**Correctness** — does the code do what `claude_log.md` says? One test per documented
decision and per stated invariant. If a decision has no `test_implication`, that's itself a
finding.

**Spec compliance** — does it satisfy `.spec/system_spec.md` and the module spec? Every
`[SPEC_DEVIATION]` gets explicitly probed — the Builder declaring it does not make it correct.

**Robustness** — adversarial input, boundaries, failure modes, integration and API-contract
edges. In this repo that specifically means: untrusted forum text, prompt-injection-shaped
post bodies, deleted authors, naive datetimes, out-of-order batch results, unreachable SMTP,
a dead subreddit mid-gather.

## 4a. CodeRabbit review — required

Run CodeRabbit during every validation cycle after reading the Builder's changes and before
closing the handoff. Review both:

- Builder-owned production code for security risks, production failures, unsafe defaults,
  concurrency/resumability gaps, and deployment problems.
- Validator-owned tests for false confidence, weak assertions, missing hostile cases, and
  tests that pass for the wrong reason.

Treat CodeRabbit findings as leads, not verdicts: reproduce or verify each material finding
against the spec and code before recording it. Trace confirmed findings to a decision or
invariant using the normal `[PASSED]` / `[FAILED]` / `[BLOCKER]` format. Never fix
Builder-owned source.

## 5. Finding format

Every finding tagged `[PASSED]` / `[FAILED]` / `[BLOCKER]` and traced back to the decision ID
or invariant it came from. An untraceable finding is a bug report, not a validation.

`[BLOCKER]` means work cannot continue: a Builder-owned file must change, or a human must
decide something.

## 6. Never fix source

If source is broken, that is a `[FAILED]` or `[BLOCKER]` — **not** a fix. Writing the fix
yourself destroys the audit trail and the human's ability to learn from the exchange.

## 7. Session end

1. Append to `.agent/codex_log.md`.
2. **Append your rows to `.agent/ledger.jsonl`** — one line per `FINDING-N.M` (with
   `status`, `target`, `traces_to`, and `needs`), plus one `result` row for the session
   (`id: "VALIDATION-N"`, `counts`: passed / failed / skipped / blockers). Every row gets
   `"agent": "codex"`, the next `seq`, and a `log_anchor` of `codex_log.md#session-N`.
   Append only — see §3a.
3. Populate `last_validation` and `open_blockers` in `handoff.json`. `traces_to` is an
   **array** in `findings[]` and in `open_blockers[]` alike — the old scalar
   `traces_to_decision` is retired.
4. Set `turn: "claude"`, `status: "VALIDATION_COMPLETE"`, **increment `cycle`**, update
   `updated_at`.
5. Run `python3 tools/dadp_report.py` to regenerate `.agent/DASHBOARD.md`. Section 2
   (finding lifecycle chains) is the check on your own work: a chain that has grown for
   three cycles without turning green is a fix that keeps missing, and it deserves a
   harder test than the one that just passed.

A finding's `traces_to` is what makes the lifecycle chains derivable — an untraceable
finding is a bug report, not a validation (§5), and it is also an orphan node in every
graph. Keep the `- Traces to:` bullet to bare identifiers so it parses.

---

## Test framework for this repo

**No test suite existed at protocol adoption** — the repo had no source, no manifest, and no
runner. Framework choice and directory convention are therefore yours to establish; the
rationale is recorded in `.agent/prompts/codex_session.md`. Detect and follow the convention
once it exists; do not re-litigate it each cycle.

## Standing constraints for this repo

- **`INV-2` (cluster vertical partition) is the highest-value test in the system.**
  `.spec/modules/cluster.md` specifies a two-part test: assert the partition holds, **and**
  assert the same code without the `WHERE vertical` clause merges — otherwise the test can
  pass vacuously because the fixture embeddings happened to be far apart. Write it that way.
- **Position-keyed batch parsing passes an ordered test.** Shuffle results in the fixture.
- **Several acceptance criteria in `specs/` are deliberately manual** — human judgement about
  LLM output quality, with no assertion form. Do not fake them with a weak assertion. See
  `OQ-6` in `open_blockers`; escalate rather than invent.
- DB constraint tests must bypass the ORM (raw SQL). An ORM-level test proves nothing about
  a database constraint.
