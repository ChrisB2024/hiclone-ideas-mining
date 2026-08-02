#!/usr/bin/env python3
"""Retrofit `.agent/ledger.jsonl` from the existing DADP prose logs and router.

Purpose
    DADP's per-session artifacts (`claude_log.md`, `codex_log.md`) are append-only
    prose, and `handoff.json` only ever holds the *last* build and the *last*
    validation. Nothing in the protocol spans cycles, so "is this bug converging or
    are we going in circles?" is a 1,500-line read. This script recovers the missing
    history layer by parsing what the agents already emit into a uniform event
    ledger whose `traces_to` arrays form the edge list of a finding-lifecycle graph.

Inputs
    .agent/claude_log.md   Builder sessions: `#### [DECISION-N.M] Title — FINDING-X.Y`
    .agent/codex_log.md    Validator sessions: `#### [FINDING-N.M] [STATUS] — Title`
    .agent/handoff.json    Richer structured data for the most recent cycle, and the
                           authoritative list of still-open blockers.

Outputs
    .agent/ledger.jsonl    One JSON object per line, ordered by `seq`.
    stdout                 A parse summary (events by cycle and kind) plus an
                           explicit list of anything that could not be parsed.

Invariants maintained
    - Deterministic: identical inputs produce a byte-identical ledger. No clocks,
      no randomness, no dict-ordering dependence.
    - Idempotent: re-running regenerates the whole file from scratch.
    - Lossless-by-alarm: anything the parser does not understand is reported on
      stdout, never silently dropped.
    - Non-destructive: refuses to clobber a ledger that contains event ids this
      run would not reproduce (i.e. rows appended live by an agent) unless --force.

Security notes
    Reads and writes only inside the repository's `.agent/` directory. It parses
    trusted, repo-local markdown written by the two agents; no network, no shell,
    no untrusted input. It never edits either agent log.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = REPO_ROOT / ".agent"
CLAUDE_LOG = AGENT_DIR / "claude_log.md"
CODEX_LOG = AGENT_DIR / "codex_log.md"
HANDOFF = AGENT_DIR / "handoff.json"
LEDGER = AGENT_DIR / "ledger.jsonl"

KINDS = ("decision", "finding", "blocker", "deviation", "result")
STATUSES = ("PASSED", "FAILED", "BLOCKER")

# --------------------------------------------------------------------------- #
# Low-level markdown helpers
# --------------------------------------------------------------------------- #

DASH = r"[—–-]"  # em dash, en dash, hyphen — all three appear in the logs

SESSION_RE = re.compile(
    r"^## Session (\d+)\s*" + DASH + r"\s*(\S+)\s*" + DASH + r"\s*(.+?)\s*$", re.M
)
SESSION_LINE_RE = re.compile(r"^## Session\b.*$", re.M)
H4_RE = re.compile(r"^####\s+(.*?)\s*$", re.M)
H3_RE = re.compile(r"^###\s+(.*?)\s*$", re.M)
STOP_H_RE = re.compile(r"^#{1,4}\s", re.M)
STOP_H123_RE = re.compile(r"^#{1,3}\s", re.M)

# `— FINDING-2.6/2.7` and `OQ-9/FINDING-1.9` both appear; the bare `/2.7`
# continuation inherits the prefix of the reference it hangs off.
REF_RE = re.compile(
    r"\b(DECISION|FINDING|INV|OQ|TEST-CONFLICT)-(\d+(?:\.\d+)?)((?:/\d+(?:\.\d+)?)+)?"
)

DECISION_H_RE = re.compile(r"^\[DECISION-(\d+\.\d+)\]\s*(.*)$")
FINDING_H_RE = re.compile(
    r"^\[FINDING-(\d+\.\d+)\]\s*\[?(PASSED|FAILED|BLOCKER)\]?\s*"
    + DASH
    + r"\s*(.*)$"
)
CHECKBOX_RE = re.compile(r"^\s*-\s*\[([x~X ])\]\s*(.*)$")
COUNTS_RE = re.compile(
    r"(\d+)\s+passed(?:,|\s)\s*(\d+)\s+failed(?:(?:,|\s)\s*(\d+)\s+skipped)?"
)


class Session:
    """One `## Session N — DATE — PHASE` block of an agent log."""

    def __init__(self, number: int, date: str, phase: str, body: str) -> None:
        self.number = number
        self.date = date
        self.phase = phase
        self.body = body

    @property
    def ts(self) -> str:
        return f"{self.date}T00:00:00Z"


def parse_sessions(text: str, problems: list, source: str) -> list:
    """Split an agent log into Session blocks.

    Any `## Session` line that does not match the canonical header shape is
    reported as a problem rather than skipped silently.
    """
    matches = list(SESSION_RE.finditer(text))
    matched_spans = {m.start() for m in matches}
    for line in SESSION_LINE_RE.finditer(text):
        if line.start() not in matched_spans:
            problems.append(f"{source}: unparsed session header: {line.group(0)!r}")
    sessions = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sessions.append(Session(int(m.group(1)), m.group(2), m.group(3), text[m.end():end]))
    return sessions


def iter_h4(body: str):
    """Yield `(heading_text, block_body)` for every `####` heading in `body`."""
    for m in H4_RE.finditer(body):
        nxt = STOP_H_RE.search(body, m.end())
        yield m.group(1), body[m.end(): nxt.start() if nxt else len(body)]


def section(body: str, name: str) -> str:
    """Return the body of the `### <name>` section, or '' if absent."""
    for m in H3_RE.finditer(body):
        if m.group(1).strip().lower() == name.lower():
            nxt = STOP_H123_RE.search(body, m.end())
            return body[m.end(): nxt.start() if nxt else len(body)]
    return ""


def bullets(block: str) -> list:
    """Flatten a markdown block into top-level bullets with continuations joined."""
    items, cur = [], None
    for line in block.splitlines():
        if re.match(r"^\s{0,3}-\s+", line):
            if cur is not None:
                items.append(cur)
            cur = line.strip()[1:].strip()
        elif cur is not None and line.strip():
            cur += " " + line.strip()
        elif cur is not None:
            items.append(cur)
            cur = None
    if cur is not None:
        items.append(cur)
    return items


def refs(text: str) -> list:
    """Extract protocol identifiers from prose, in first-appearance order.

    Handles the `FINDING-2.6/2.7` continuation shorthand used in decision titles.
    """
    out = OrderedDict()
    for m in REF_RE.finditer(text):
        prefix, num, cont = m.group(1), m.group(2), m.group(3)
        out[f"{prefix}-{num}"] = None
        if cont:
            for part in cont.strip("/").split("/"):
                if part:
                    out[f"{prefix}-{part}"] = None
    return list(out)


def clean(text: str, limit: int = 320) -> str:
    """Collapse markdown emphasis and whitespace into a one-line summary."""
    text = re.sub(r"[`*]", "", text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


# --------------------------------------------------------------------------- #
# Event construction
# --------------------------------------------------------------------------- #


def event(**kw) -> dict:
    """Build a ledger row with a stable key order and the full core key set."""
    row = OrderedDict()
    row["cycle"] = kw["cycle"]
    row["seq"] = 0  # assigned at write time
    row["ts"] = kw["ts"]
    row["agent"] = kw["agent"]
    row["kind"] = kw["kind"]
    row["id"] = kw["id"]
    row["title"] = kw.get("title") or ""
    row["status"] = kw.get("status")
    row["target"] = kw.get("target")
    row["modules"] = kw.get("modules") or []
    row["traces_to"] = kw.get("traces_to") or []
    row["needs"] = kw.get("needs")
    row["log_anchor"] = kw["log_anchor"]
    for extra in ("detail", "test_implication", "counts",
                  "invariants_partial", "invariants_unverified", "first_seen_cycle"):
        if kw.get(extra) not in (None, "", [], {}):
            row[extra] = kw[extra]
    assert row["kind"] in KINDS, row["kind"]
    assert row["status"] in (None,) + STATUSES, row["status"]
    return row


def parse_builder(sessions, handoff, problems) -> list:
    """Turn Builder sessions into decision / deviation / build-result events."""
    hb = handoff.get("last_build") or {}
    hb_cycle = hb.get("cycle")
    hb_decisions = {d.get("id"): d for d in (hb.get("decisions") or []) if d.get("id")}

    events = []
    for s in sessions:
        anchor = f"claude_log.md#session-{s.number}"
        for heading, block in iter_h4(s.body):
            m = DECISION_H_RE.match(heading)
            if not m:
                problems.append(
                    f"claude_log.md#session-{s.number}: unparsed h4 heading: {heading!r}"
                )
                continue
            did = f"DECISION-{m.group(1)}"
            title = clean(m.group(2), 200)

            # traces_to: every identifier named in the title (the repo's linking
            # convention) plus any INV-* referenced in the decision body.
            trace = OrderedDict((r, None) for r in refs(heading) if r != did)
            for r in refs(block):
                if r.startswith("INV-"):
                    trace.setdefault(r, None)

            body_bullets = bullets(block)
            what = next(
                (b for b in body_bullets if re.match(r"^\*\*What:?\*\*", b)),
                body_bullets[0] if body_bullets else "",
            )
            detail = clean(re.sub(r"^\*\*What:?\*\*\s*", "", what))

            extra = hb_decisions.get(did) if s.number == hb_cycle else None
            modules = list(extra.get("affects") or []) if extra else []
            test_impl = clean(extra.get("test_implication") or "", 240) if extra else None
            if extra and extra.get("summary"):
                detail = clean(extra["summary"])
            if extra:
                for r in extra.get("traces_to") or []:
                    if r != did:
                        trace.setdefault(r, None)

            events.append(event(
                cycle=s.number, ts=s.ts, agent="claude", kind="decision", id=did,
                title=title, modules=modules, traces_to=list(trace),
                log_anchor=anchor, detail=detail, test_implication=test_impl,
            ))

            if "[SPEC_DEVIATION]" in heading or "[SPEC_DEVIATION]" in block:
                events.append(event(
                    cycle=s.number, ts=s.ts, agent="claude", kind="deviation",
                    id=f"DEVIATION-{m.group(1)}",
                    title=f"spec deviation declared in {did}",
                    modules=modules, traces_to=[did], log_anchor=anchor,
                    detail=detail,
                ))

        # ---- build result: asserted invariants and the Builder's own suite run ----
        inv_sec = section(s.body, "Invariants Verified")
        confirmed, partial, unverified = [], [], []
        for line in inv_sec.splitlines():
            cb = CHECKBOX_RE.match(line)
            if not cb:
                continue
            bucket = {"x": confirmed, "X": confirmed, "~": partial, " ": unverified}[cb.group(1)]
            for r in refs(cb.group(2)):
                if r.startswith("INV-") and r not in bucket:
                    bucket.append(r)
        if s.number == hb_cycle:
            for inv in hb.get("invariants_asserted") or []:
                if inv not in confirmed:
                    confirmed.append(inv)

        counts = None
        cm = list(COUNTS_RE.finditer(s.body))
        if cm:
            last = cm[-1]
            counts = OrderedDict(
                passed=int(last.group(1)),
                failed=int(last.group(2)),
                skipped=int(last.group(3)) if last.group(3) else 0,
            )

        if confirmed or partial or unverified or counts:
            events.append(event(
                cycle=s.number, ts=s.ts, agent="claude", kind="result",
                id=f"BUILD-{s.number}", title=f"Builder session {s.number} — {s.phase}",
                traces_to=confirmed, log_anchor=f"claude_log.md#session-{s.number}",
                counts=counts, invariants_partial=partial,
                invariants_unverified=unverified,
            ))
    return events


def parse_validator(sessions, handoff, problems) -> list:
    """Turn Validator sessions into finding events and a validation-result event."""
    hv = handoff.get("last_validation") or {}
    hv_cycle = hv.get("cycle")
    hv_findings = {f.get("id"): f for f in (hv.get("findings") or []) if f.get("id")}

    events = []
    for s in sessions:
        anchor = f"codex_log.md#session-{s.number}"
        blockers = 0
        for heading, block in iter_h4(s.body):
            m = FINDING_H_RE.match(heading)
            if not m:
                problems.append(
                    f"codex_log.md#session-{s.number}: unparsed h4 heading: {heading!r}"
                )
                continue
            fid, status, title = f"FINDING-{m.group(1)}", m.group(2), clean(m.group(3), 200)
            if status == "BLOCKER":
                blockers += 1

            body_bullets = bullets(block)
            trace_bullet = next(
                (b for b in body_bullets if re.match(r"^Traces to\s*:", b, re.I)), ""
            )
            target_bullet = next(
                (b for b in body_bullets if re.match(r"^Target\s*:", b, re.I)), ""
            )
            detail_bullets = [
                b for b in body_bullets
                if b is not trace_bullet and b is not target_bullet
            ]
            trace = refs(trace_bullet)
            leftover = re.sub(REF_RE, "", re.sub(r"^Traces to\s*:", "", trace_bullet, flags=re.I))
            leftover = clean(leftover.replace(",", " "), 200)
            if leftover and len(leftover) > 3:
                problems.append(
                    f"{anchor} {fid}: non-identifier text in 'Traces to' kept out of "
                    f"traces_to: {leftover!r}"
                )

            target = clean(re.sub(r"^Target\s*:", "", target_bullet, flags=re.I), 120) or None
            detail = clean(
                re.sub(r"^Detail\s*:\s*", "", " ".join(detail_bullets), flags=re.I)
            ) or None

            hf = hv_findings.get(fid) if s.number == hv_cycle else None
            if hf:
                target = hf.get("target") or target
                detail = clean(hf.get("detail") or "") or detail
                for r in hf.get("traces_to") or []:
                    if r not in trace:
                        trace.append(r)

            # `needs` is not stated per finding in the prose; it is derived from
            # severity, matching every "Builder action" / "needs: human" line in
            # the Handoff sections of both logs.
            needs = {"FAILED": "builder", "BLOCKER": "human"}.get(status)

            events.append(event(
                cycle=s.number, ts=s.ts, agent="codex", kind="finding", id=fid,
                title=title, status=status, target=target, traces_to=trace,
                needs=needs, log_anchor=anchor, detail=detail,
            ))

        # ---- validation result: prefer the router's counts for its own cycle ----
        head = s.body.split("### Findings")[0]
        counts = None
        cm = list(COUNTS_RE.finditer(head))
        if cm:
            final = [m for m in cm if "final" in head[max(0, m.start() - 120):m.start()].lower()]
            pick = final[-1] if final else cm[-1]
            counts = OrderedDict(
                passed=int(pick.group(1)),
                failed=int(pick.group(2)),
                skipped=int(pick.group(3)) if pick.group(3) else 0,
                blockers=blockers,
            )
        if s.number == hv_cycle and hv.get("results"):
            counts = counts or OrderedDict()
            for k in ("passed", "failed", "blockers"):
                if hv["results"].get(k) is not None:
                    counts[k] = hv["results"][k]
            counts.setdefault("skipped", 0)
            counts = OrderedDict(
                passed=counts.get("passed", 0), failed=counts.get("failed", 0),
                skipped=counts.get("skipped", 0), blockers=counts.get("blockers", blockers),
            )
        if counts:
            events.append(event(
                cycle=s.number, ts=s.ts, agent="codex", kind="result",
                id=f"VALIDATION-{s.number}",
                title=f"Validator session {s.number} — {s.phase}",
                log_anchor=anchor, counts=counts,
            ))
    return events


def parse_open_blockers(handoff, events) -> list:
    """Emit one `blocker` row per still-open router blocker.

    The router is the authority on what is open. `cycle` is set to the earliest
    cycle in which the blocker id appears anywhere in the ledger, so that
    "how many cycles has this been open" is answerable.
    """
    out = []
    ts = handoff.get("updated_at") or "1970-01-01T00:00:00Z"
    current = handoff.get("cycle", 0)
    for b in handoff.get("open_blockers") or []:
        bid = b.get("id")
        if not bid:
            continue
        touching = [e for e in events if bid in e["traces_to"] or e["id"] == bid]
        first = min((e["cycle"] for e in touching), default=current)
        agent = next((e["agent"] for e in sorted(touching, key=lambda e: e["cycle"])), "codex")
        trace = list(b.get("traces_to") or [])
        legacy = b.get("traces_to_decision")
        if legacy and legacy not in trace:
            trace.append(legacy)
        out.append(event(
            cycle=first, ts=ts, agent=agent, kind="blocker", id=bid,
            title=clean(b.get("detail") or bid, 160),
            status=b.get("severity") or "BLOCKER", target=b.get("target"),
            traces_to=trace, needs=b.get("needs"),
            log_anchor="handoff.json#open_blockers",
            detail=clean(b.get("detail") or ""), first_seen_cycle=first,
        ))
    return out


# --------------------------------------------------------------------------- #
# Ordering, writing, reporting
# --------------------------------------------------------------------------- #

KIND_ORDER = {"decision": 0, "deviation": 1, "result": 2, "finding": 3, "blocker": 4}
AGENT_ORDER = {"claude": 0, "codex": 1, "human": 2}


def sort_key(e: dict):
    """Deterministic total order: cycle, then Builder before Validator, then id."""
    nums = [int(x) for x in re.findall(r"\d+", e["id"])] or [0]
    return (e["cycle"], AGENT_ORDER.get(e["agent"], 9), KIND_ORDER[e["kind"]], nums, e["id"])


def load_existing_ids(path: Path) -> set:
    if not path.exists():
        return set()
    ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ids.add(json.loads(line).get("id"))
        except json.JSONDecodeError:
            ids.add(None)
    return ids


def summarise(events: list, problems: list) -> None:
    by_cycle = Counter((e["cycle"], e["kind"]) for e in events)
    kinds = sorted({k for _, k in by_cycle}, key=lambda k: KIND_ORDER[k])
    cycles = sorted({c for c, _ in by_cycle})

    print(f"\nWrote {len(events)} events to {LEDGER.relative_to(REPO_ROOT)}\n")
    header = ["cycle"] + kinds + ["total"]
    width = max(9, *(len(h) for h in header))
    print("  ".join(h.rjust(width) for h in header))
    print("  ".join("-" * width for _ in header))
    for c in cycles:
        row = [str(c)] + [str(by_cycle.get((c, k), 0)) for k in kinds]
        row.append(str(sum(by_cycle.get((c, k), 0) for k in kinds)))
        print("  ".join(v.rjust(width) for v in row))
    totals = ["all"] + [str(sum(by_cycle.get((c, k), 0) for c in cycles)) for k in kinds]
    totals.append(str(len(events)))
    print("  ".join(v.rjust(width) for v in totals))

    status = Counter(e["status"] for e in events if e["kind"] == "finding")
    print("\nfindings by status: " + ", ".join(
        f"{k}={status[k]}" for k in STATUSES if status[k]
    ))
    edges = sum(len(e["traces_to"]) for e in events)
    inv_edges = sum(1 for e in events for r in e["traces_to"] if r.startswith("INV-"))
    print(f"trace edges: {edges} total ({inv_edges} to invariants, "
          f"{edges - inv_edges} between decisions/findings/blockers)")

    if problems:
        print(f"\n!! {len(problems)} item(s) the parser could not fully extract:")
        for p in problems:
            print(f"   - {p}")
    else:
        print("\nNothing was dropped: every heading and 'Traces to' token parsed.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="overwrite a ledger that contains rows this run cannot reproduce")
    ap.add_argument("--stdout", action="store_true",
                    help="print the ledger instead of writing it")
    args = ap.parse_args(argv)

    for p in (CLAUDE_LOG, CODEX_LOG, HANDOFF):
        if not p.exists():
            print(f"missing required input: {p}", file=sys.stderr)
            return 2

    problems: list = []
    handoff = json.loads(HANDOFF.read_text(encoding="utf-8"))
    builder_sessions = parse_sessions(CLAUDE_LOG.read_text(encoding="utf-8"), problems, "claude_log.md")
    validator_sessions = parse_sessions(CODEX_LOG.read_text(encoding="utf-8"), problems, "codex_log.md")

    events = parse_builder(builder_sessions, handoff, problems)
    events += parse_validator(validator_sessions, handoff, problems)
    events += parse_open_blockers(handoff, events)

    events.sort(key=sort_key)
    for i, e in enumerate(events, start=1):
        e["seq"] = i

    # Dangling references are worth surfacing: they are either a typo in a log or
    # a link to something that was never recorded.
    known = {e["id"] for e in events}
    for e in events:
        for r in e["traces_to"]:
            if r.startswith(("INV-", "OQ-")):
                continue
            if r not in known:
                problems.append(f"{e['id']} traces to unknown id {r}")

    payload = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)

    if args.stdout:
        sys.stdout.write(payload)
        return 0

    existing = load_existing_ids(LEDGER)
    unreproducible = {i for i in existing if i not in known}
    if unreproducible and not args.force:
        print(f"refusing to overwrite {LEDGER}: it contains {len(unreproducible)} row(s) "
              f"this backfill cannot reproduce (e.g. {sorted(map(str, unreproducible))[:5]}). "
              f"Re-run with --force only if you mean to discard them.", file=sys.stderr)
        return 1

    LEDGER.write_text(payload, encoding="utf-8")
    summarise(events, problems)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
