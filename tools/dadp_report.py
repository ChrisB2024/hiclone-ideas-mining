#!/usr/bin/env python3
"""Render `.agent/DASHBOARD.md` from `.agent/ledger.jsonl` and `.agent/handoff.json`.

Purpose
    The two agent logs are append-only prose and the router only remembers one
    cycle. Neither answers the question the human actually asks between sessions:
    *is this bug converging, or are we going in circles?* This script derives that
    answer from data the agents already emit — it authors no facts of its own.

Inputs
    .agent/ledger.jsonl     Cross-cycle event ledger (see tools/dadp_backfill.py).
    .agent/handoff.json     The router: current cycle and the authoritative list of
                            still-open blockers.
    .spec/system_spec.md    Optional; used only for invariant display names.

Output
    .agent/DASHBOARD.md     Four sections, actionable first:
                              1. Open right now
                              2. Finding lifecycle chains  (Mermaid `flowchart LR`)
                              3. Invariant coverage
                              4. Cycle health

Invariants maintained
    - Derived only. Every row traces to a ledger event; nothing is hand-authored,
      so the dashboard cannot drift from the router the way a hand-written diagram
      would.
    - Deterministic: no clock is read, so re-running without a ledger change
      produces a byte-identical file and no spurious diff.
    - `INV-*` references are deliberately excluded from the chain graph: they are
      cross-cutting hubs that would collapse every component into one blob. They
      get their own section instead.

Security notes
    Reads and writes only repo-local files. Text lifted from the logs is sanitised
    before being embedded in Mermaid labels so a stray quote or angle bracket in a
    finding title cannot corrupt the diagram.

Stdlib only — no dependencies (dadp.md forbids adding them).
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = REPO_ROOT / ".agent"
LEDGER = AGENT_DIR / "ledger.jsonl"
HANDOFF = AGENT_DIR / "handoff.json"
SYSTEM_SPEC = REPO_ROOT / ".spec" / "system_spec.md"
DASHBOARD = AGENT_DIR / "DASHBOARD.md"

GREEN, RED, GREY = "resolved", "open", "neutral"


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def load_ledger() -> list:
    """Read the ledger, one JSON object per line, in seq order."""
    if not LEDGER.exists():
        sys.exit(f"missing {LEDGER}; run tools/dadp_backfill.py first")
    rows = []
    for n, line in enumerate(LEDGER.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            sys.exit(f"{LEDGER}:{n}: not valid JSON ({exc})")
    rows.sort(key=lambda e: e.get("seq", 0))
    return rows


def load_invariant_names() -> dict:
    """Pull `INV-N` display names out of the system spec's invariant table."""
    names = {}
    if not SYSTEM_SPEC.exists():
        return names
    row_re = re.compile(r"^\|\s*`?(INV-\d+)`?\s*\|\s*(.+?)\s*\|", re.M)
    for m in row_re.finditer(SYSTEM_SPEC.read_text(encoding="utf-8")):
        text = re.sub(r"[`*]", "", m.group(2)).strip()
        text = re.split(r"(?<=\.)\s", text)[0].strip().rstrip(".")
        names[m.group(1)] = text
    return names


# --------------------------------------------------------------------------- #
# Graph over the ledger's traces_to edges
# --------------------------------------------------------------------------- #


def is_invariant(ref: str) -> bool:
    return ref.startswith("INV-")


def build_graph(events: list):
    """Return (nodes, forward_edges) over non-invariant trace references.

    An edge runs *chronologically*: `traces_to` points backwards in time, so a
    reference `E -> R` becomes the edge `R -> E`. Invariants are excluded; see the
    module docstring.
    """
    by_id = {e["id"]: e for e in events}
    nodes = OrderedDict()
    edges = OrderedDict()  # src -> [dst]

    def touch(node_id):
        if node_id not in nodes:
            nodes[node_id] = by_id.get(node_id)

    for e in events:
        if e["kind"] not in ("decision", "finding", "blocker"):
            continue
        for ref in e["traces_to"]:
            if is_invariant(ref):
                continue
            touch(ref)
            touch(e["id"])
            edges.setdefault(ref, [])
            if e["id"] not in edges[ref]:
                edges[ref].append(e["id"])
    return nodes, edges


def reverse(edges: dict) -> dict:
    rev = defaultdict(list)
    for src, dsts in edges.items():
        for d in dsts:
            rev[d].append(src)
    return rev


def resolution_map(events: list, edges: dict, open_blocker_ids: set) -> dict:
    """Decide, per finding, whether the protocol has since closed it.

    Rules, in order:
      1. A `BLOCKER` finding that names a router blocker still listed in
         `open_blockers` is **open**, and is folded into that blocker's row as a
         recurrence rather than listed on its own.
      2. A `BLOCKER` finding whose named `OQ-*` items have all left
         `open_blockers` is **resolved** — the router is the authority on that.
      3. Any other `FAILED`/`BLOCKER` finding is **resolved** iff some later
         `PASSED` finding is reachable from it by following trace edges forward
         (fix decision -> re-validation).
    Returns {finding_id: ("resolved"|"open"|"recurrence-of:<OQ>")}.
    """
    by_id = {e["id"]: e for e in events}
    out = {}
    for e in events:
        if e["kind"] != "finding" or e["status"] == "PASSED":
            continue
        oq = [r for r in e["traces_to"] if r.startswith("OQ-")]
        still_open = [r for r in oq if r in open_blocker_ids]
        if e["status"] == "BLOCKER" and still_open:
            out[e["id"]] = f"recurrence-of:{still_open[0]}"
            continue
        if e["status"] == "BLOCKER" and oq:
            out[e["id"]] = GREEN
            continue
        # forward reachability to a PASSED finding
        seen, stack, resolved = {e["id"]}, list(edges.get(e["id"], [])), False
        while stack:
            nid = stack.pop()
            if nid in seen:
                continue
            seen.add(nid)
            n = by_id.get(nid)
            if n and n["kind"] == "finding" and n["status"] == "PASSED":
                resolved = True
                break
            stack.extend(edges.get(nid, []))
        out[e["id"]] = GREEN if resolved else RED
    return out


def components(nodes: dict, edges: dict) -> list:
    """Connected components of the undirected projection of the trace graph."""
    adj = defaultdict(set)
    for src, dsts in edges.items():
        for d in dsts:
            adj[src].add(d)
            adj[d].add(src)
    seen, comps = set(), []
    for n in nodes:
        if n in seen:
            continue
        stack, group = [n], []
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            group.append(cur)
            stack.extend(sorted(adj[cur]))
        comps.append(sorted(group, key=node_sort_key))
    return comps


def node_sort_key(node_id: str):
    nums = [int(x) for x in re.findall(r"\d+", node_id)] or [0]
    return (nums, node_id)


def longest_path(group: list, edges: dict) -> list:
    """Longest chronological path inside one component (the convergence story)."""
    members = set(group)
    memo = {}

    def walk(node, stack):
        if node in memo:
            return memo[node]
        best = [node]
        for nxt in edges.get(node, []):
            if nxt not in members or nxt in stack:
                continue
            cand = [node] + walk(nxt, stack | {node})
            if len(cand) > len(best):
                best = cand
        memo[node] = best
        return best

    best = []
    for n in sorted(group, key=node_sort_key):
        cand = walk(n, frozenset())
        if len(cand) > len(best):
            best = cand
    return best


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #


def mm_id(node_id: str) -> str:
    """Mermaid-safe node identifier."""
    return re.sub(r"[^0-9A-Za-z]", "_", node_id)


def mm_label(text: str, limit: int = 44) -> str:
    """Mermaid-safe quoted-label text: no quotes, no angle brackets, no pipes."""
    text = re.sub(r"[\"'`“”‘’<>|{}\[\]()#;]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text.strip(" —–-,")


def md_cell(text) -> str:
    """Escape a value for a Markdown table cell."""
    if text is None:
        return "—"
    return re.sub(r"\s+", " ", str(text)).replace("|", "\\|").strip() or "—"


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #


def section_open(events, handoff, resolutions, edges, out):
    current = handoff.get("cycle", 0)
    by_id = {e["id"]: e for e in events}
    rev = reverse(edges)
    open_blockers = {b["id"]: b for b in (handoff.get("open_blockers") or []) if b.get("id")}

    recurrences = defaultdict(list)
    for fid, state in resolutions.items():
        if state.startswith("recurrence-of:"):
            recurrences[state.split(":", 1)[1]].append(fid)

    rows = []
    for bid, b in open_blockers.items():
        ev = by_id.get(bid)
        first = ev["cycle"] if ev else current
        rec = sorted(recurrences.get(bid, []), key=node_sort_key)
        rows.append({
            "id": bid,
            "severity": (b.get("severity") or "BLOCKER"),
            "needs": b.get("needs") or "human",
            "target": b.get("target"),
            "since": first,
            "open": current - first,
            "addressed_by": None,
            "detail": (ev or b).get("detail") or b.get("detail"),
            "note": (f"re-raised every cycle since — {', '.join(rec)}"
                     if rec else "no decision recorded against it"),
        })

    for e in events:
        if e["kind"] != "finding" or resolutions.get(e["id"], GREEN) != RED:
            continue
        # Forward edges out of a finding are the decisions taken *against* it.
        fixes = [n for n in sorted(edges.get(e["id"], []), key=node_sort_key)
                 if by_id.get(n, {}).get("kind") == "decision"
                 and by_id[n]["cycle"] >= e["cycle"]]
        rows.append({
            "id": e["id"],
            "severity": e["status"],
            "needs": e.get("needs") or "builder",
            "target": e.get("target"),
            "since": e["cycle"],
            "open": current - e["cycle"],
            "addressed_by": ", ".join(fixes) if fixes else None,
            "detail": e.get("detail") or e.get("title"),
            "note": "fix landed, awaiting re-validation" if fixes else "not yet addressed",
        })

    rows.sort(key=lambda r: (-r["open"], node_sort_key(r["id"])))

    out.append("## 1. Open right now\n")
    if not rows:
        out.append("Nothing unresolved. Every `FAILED` finding has a later `PASSED` "
                   "descendant and `open_blockers` is empty.\n")
        return
    out.append(f"{len(rows)} unresolved item(s) as of cycle {current} "
               f"(`turn: {handoff.get('turn')}`, `status: {handoff.get('status')}`).\n")
    out.append("| id | severity | needs | since cycle | cycles open | target | "
               "addressed by | state |")
    out.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        out.append("| `{id}` | {sev} | **{needs}** | {since} | {open} | {target} | "
                   "{fix} | {note} |".format(
                       id=r["id"], sev=md_cell(r["severity"]), needs=md_cell(r["needs"]),
                       since=r["since"], open=r["open"], target=md_cell(r["target"]),
                       fix=md_cell(r["addressed_by"]), note=md_cell(r["note"] or "—")))
    out.append("")
    for r in rows:
        out.append(f"- **{r['id']}** — {md_cell(r['detail'])}")
    out.append("")


def section_chains(events, resolutions, nodes, edges, open_blocker_ids, out):
    by_id = {e["id"]: e for e in events}
    comps = [c for c in components(nodes, edges) if len(c) >= 2]

    def node_class(node_id):
        e = by_id.get(node_id)
        if node_id.startswith("OQ-"):
            return RED if node_id in open_blocker_ids else GREEN
        if e is None:
            return GREY
        if e["kind"] == "finding":
            if e["status"] == "PASSED":
                return GREEN
            return RED if resolutions.get(node_id) == RED else GREEN
        if e["kind"] == "blocker":
            return RED if node_id in open_blocker_ids else GREEN
        return GREY

    def has_open(group):
        return any(node_class(n) == RED for n in group)

    comps.sort(key=lambda g: (not has_open(g), -len(g), node_sort_key(g[0])))

    out.append("## 2. Finding lifecycle chains\n")
    out.append("Each component below is one thread of cause and effect: a finding, the "
               "decision taken against it, and what the next validation found. A chain "
               "that keeps growing is a bug that is circling; a chain ending green has "
               "converged. Edges are derived from `traces_to` in the ledger — "
               "`INV-*` references are excluded here because they connect everything "
               "(see section 3).\n")
    out.append("Legend: green = resolved, red = still open, grey = a Builder decision.\n")

    for group in comps:
        path = longest_path(group, edges)
        title = " → ".join(path)
        flag = "OPEN" if has_open(group) else "closed"
        out.append(f"### {path[0]} … {path[-1]} — {flag}\n")
        out.append(f"Longest chain ({len(path)} steps): `{title}`\n")
        out.append("```mermaid")
        out.append("flowchart LR")
        out.append("    classDef resolved fill:#1b5e20,stroke:#66bb6a,color:#ffffff;")
        out.append("    classDef open fill:#b71c1c,stroke:#ef5350,color:#ffffff;")
        out.append("    classDef neutral fill:#37474f,stroke:#90a4ae,color:#ffffff;")
        for n in group:
            e = by_id.get(n)
            label = mm_label(e["title"]) if e and e.get("title") else ""
            text = f"{n}<br/>{label}" if label else n
            out.append(f'    {mm_id(n)}["{text}"]')
        for src in group:
            for dst in edges.get(src, []):
                if dst in group:
                    out.append(f"    {mm_id(src)} --> {mm_id(dst)}")
        for n in group:
            out.append(f"    class {mm_id(n)} {node_class(n)};")
        out.append("```\n")

    orphans = [e["id"] for e in events
               if e["kind"] in ("decision", "finding") and e["id"] not in nodes]
    if orphans:
        out.append(
            f"**{len(orphans)} event(s) are in no chain at all** — nothing traces to them "
            f"and they trace to nothing but invariants, so they are invisible to this "
            f"graph: " + ", ".join(f"`{o}`" for o in sorted(orphans, key=node_sort_key))
            + ".\n"
        )


def section_invariants(events, inv_names, out):
    asserted = Counter()
    partial = Counter()
    referenced = Counter()
    traced = Counter()
    passed = Counter()

    for e in events:
        if e["kind"] == "result" and e["agent"] == "claude":
            for inv in e["traces_to"]:
                if is_invariant(inv):
                    asserted[inv] += 1
            for inv in e.get("invariants_partial", []):
                partial[inv] += 1
        elif e["kind"] == "decision":
            for inv in e["traces_to"]:
                if is_invariant(inv):
                    referenced[inv] += 1
        elif e["kind"] in ("finding", "blocker"):
            for inv in e["traces_to"]:
                if not is_invariant(inv):
                    continue
                traced[inv] += 1
                if e.get("status") == "PASSED":
                    passed[inv] += 1

    known = set(inv_names) | set(asserted) | set(referenced) | set(traced) | set(partial)
    ordered = sorted(known, key=lambda i: int(i.split("-")[1]))

    out.append("## 3. Invariant coverage\n")
    out.append("How often each system invariant was **asserted** by the Builder "
               "(a ticked box in *Invariants Verified*), **referenced** in a Builder "
               "decision, **traced** by a Validator finding, and — the column that "
               "matters — actually **confirmed by a `PASSED` finding**.\n")
    out.append("| invariant | asserted | referenced | traced | PASSED | signal |")
    out.append("|---|---|---|---|---|---|")

    flags = []
    for inv in ordered:
        a, r, t, p = asserted[inv], referenced[inv], traced[inv], passed[inv]
        signal = []
        if p == 0:
            signal.append("**never confirmed by a PASSED finding**")
            flags.append((inv, "no `PASSED` finding has ever confirmed it"))
        elif t <= 1:
            signal.append("**thin coverage**")
            flags.append((inv, f"traced by only {t} finding across all cycles"))
        if a == 0 and partial[inv] == 0:
            signal.append("never asserted by the Builder")
        if partial[inv]:
            signal.append(f"{partial[inv]}× partial")
        name = inv_names.get(inv, "")
        out.append(f"| `{inv}` — {md_cell(name)} | {a} | {r} | {t} | {p} | "
                   f"{md_cell(', '.join(signal)) if signal else '—'} |")
    out.append("")

    if flags:
        out.append("**Coverage gaps worth acting on:**\n")
        for inv, why in flags:
            name = inv_names.get(inv, "")
            out.append(f"- `{inv}` ({name}) — {why}.")
        out.append("")


def section_health(events, handoff, resolutions, out):
    current = handoff.get("cycle", 0)
    cycles = sorted({e["cycle"] for e in events} | {current})
    findings = defaultdict(Counter)
    decisions = Counter()
    deviations = Counter()
    suite = {}
    build_suite = {}
    for e in events:
        if e["kind"] == "finding":
            findings[e["cycle"]][e["status"]] += 1
        elif e["kind"] == "decision":
            decisions[e["cycle"]] += 1
        elif e["kind"] == "deviation":
            deviations[e["cycle"]] += 1
        elif e["kind"] == "result" and e["agent"] == "codex":
            suite[e["cycle"]] = e.get("counts") or {}
        elif e["kind"] == "result" and e["agent"] == "claude":
            build_suite[e["cycle"]] = e.get("counts") or {}

    carried = Counter()
    for e in events:
        if e["kind"] == "finding" and resolutions.get(e["id"], "").startswith(("open", RED)):
            carried[e["cycle"]] += 1

    out.append("## 4. Cycle health\n")
    out.append("Rendered as a table rather than a Mermaid `xychart-beta`: xychart is "
               "a newer Mermaid block whose availability depends on which Mermaid "
               "version the Markdown preview bundles, and a chart that silently fails "
               "to render is worse than a table that always does. `flowchart` in "
               "section 2 is core Mermaid and has no such caveat.\n")
    out.append("| cycle | decisions | deviations | findings P/F/B | suite passed | "
               "suite failed | still open | trend |")
    out.append("|---|---|---|---|---|---|---|---|")
    for c in cycles:
        f = findings[c]
        s = suite.get(c) or build_suite.get(c) or {}
        p, fl, b = f.get("PASSED", 0), f.get("FAILED", 0), f.get("BLOCKER", 0)
        bar = "█" * fl + "░" * b if (fl or b) else "·"
        src = "" if c in suite else (" *(builder run)*" if c in build_suite else "")
        out.append(
            f"| {c} | {decisions[c]} | {deviations[c] or '—'} | {p}/{fl}/{b} | "
            f"{s.get('passed', '—')}{src} | {s.get('failed', '—')} | "
            f"{carried[c] or '—'} | `{bar}` |"
        )
    out.append("")
    out.append("`still open` counts findings raised in that cycle that no later "
               "`PASSED` finding has yet closed. Bars: █ = FAILED, ░ = BLOCKER.\n")


# --------------------------------------------------------------------------- #


def main() -> int:
    events = load_ledger()
    handoff = json.loads(HANDOFF.read_text(encoding="utf-8"))
    inv_names = load_invariant_names()
    open_blocker_ids = {b.get("id") for b in (handoff.get("open_blockers") or [])}

    nodes, edges = build_graph(events)
    resolutions = resolution_map(events, edges, open_blocker_ids)

    out = []
    out.append("# DADP dashboard — ideas-mining\n")
    out.append(
        f"Generated by `tools/dadp_report.py` from `.agent/ledger.jsonl` "
        f"({len(events)} events) and `.agent/handoff.json`. "
        f"**Do not hand-edit** — it is regenerated at every session end and any "
        f"manual change is lost.\n"
    )
    out.append(
        f"Cycle **{handoff.get('cycle')}** · turn **{handoff.get('turn')}** · status "
        f"**{handoff.get('status')}** · router last updated `{handoff.get('updated_at')}`.\n"
    )
    out.append("---\n")
    section_open(events, handoff, resolutions, edges, out)
    out.append("---\n")
    section_chains(events, resolutions, nodes, edges, open_blocker_ids, out)
    out.append("---\n")
    section_invariants(events, inv_names, out)
    out.append("---\n")
    section_health(events, handoff, resolutions, out)

    DASHBOARD.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    open_count = sum(1 for v in resolutions.values() if v == RED) + len(open_blocker_ids)
    print(f"wrote {DASHBOARD.relative_to(REPO_ROOT)} — {len(events)} events, "
          f"{len([c for c in components(nodes, edges) if len(c) >= 2])} chains, "
          f"{open_count} open item(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
