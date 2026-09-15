"""
`tagteam report --phase P` (Phase 55): what a phase took, from data tagteam
already stores — rounds, change requests, gate bounces and minutes, elapsed
turn time per role, start-to-approve wall clock, the usage rows stored under
the phase, and how many workflow turns those rows can be matched to.

Read-only in both modes: the DB is opened through `db.connect_for_read`
(never creates, migrates or checkpoints; older schemas readable) and the
cycle readers get that connection, or `cycle.FILES_ONLY` when there is none.
No dollar figures in text or JSON. Missing data degrades per field.
See docs/phases/measure-the-loop.md.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CYCLE_TYPES = ("plan", "impl")
REVIEWER_VERDICTS = ("APPROVE", "REQUEST_CHANGES", "ESCALATE", "NEED_HUMAN")
TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
TURN_STATUSES = ("matched", "no_token_data", "unmatched", "unknown")
_WORKFLOW_ROLES = ("lead", "reviewer")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _parse_ts(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _seconds(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return max(0.0, (b - a).total_seconds())


def _has_tokens(row: dict) -> bool:
    return any(isinstance(row.get(k), (int, float)) for k in TOKEN_KEYS)


def _is_panel(row: dict) -> bool:
    return str(row.get("kind") or "").startswith("panel:")


def _is_ruling(entry: dict) -> bool:
    """An arbiter ruling (`cycle.add_ruling`): stored in the reviewer's seat
    with the `[ARBITER RULING by …]` prefix. It moves the cycle but is not an
    agent turn, a reviewer request or reviewer time."""
    from tagteam.cycle import RULING_PREFIX
    return entry.get("role") == "reviewer" and str(entry.get("content") or "").startswith(RULING_PREFIX)


def _is_agent_turn(entry: dict) -> bool:
    role, action = entry.get("role"), entry.get("action")
    if role == "lead":
        return action == "SUBMIT_FOR_REVIEW"
    return role == "reviewer" and action in REVIEWER_VERDICTS and not _is_ruling(entry)


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Per-cycle figures
# ---------------------------------------------------------------------------

def _cycle_counts(entries: list[dict], status: dict | None, gates: list[dict] | None,
                  interjections: list[dict] | None) -> dict:
    agent = [e for e in entries if not _is_ruling(e)]

    def count(role: str | None, action: str) -> int:
        return sum(1 for e in agent
                   if e.get("action") == action and (role is None or e.get("role") == role))

    rounds = _int((status or {}).get("round"))
    if not rounds:
        rounds = max((_int(e.get("round")) or 0 for e in entries), default=0)
    out = {
        "state": (status or {}).get("state") or ("open" if entries else None),
        "rounds": rounds,
        "change_requests": count("reviewer", "REQUEST_CHANGES"),
        "gate_bounces": count(None, "GATE_BOUNCE"),
        "amendments": count("lead", "AMEND"),
        "escalations": count(None, "ESCALATE"),
        "need_human": count(None, "NEED_HUMAN"),
        "rulings": sum(1 for e in entries if _is_ruling(e)),
        # None = not available (no DB / no interjections table), never 0
        "interjections": len(interjections) if interjections is not None else None,
        "gate_runs": None,
        "gate_seconds": None,
    }
    if gates is not None:
        decided = [g for g in gates if g.get("status") in ("pass", "bounce")]
        out["gate_runs"] = len(decided)
        durations = [float(g["duration_s"]) for g in decided
                     if isinstance(g.get("duration_s"), (int, float))]
        out["gate_seconds"] = round(sum(durations), 1) if durations else (0.0 if not decided else None)
    return out


def _turn_times(entries: list[dict]) -> dict:
    """Elapsed spans per role from consecutive entry timestamps.

    lead: previous hand-back (REQUEST_CHANGES / GATE_BOUNCE) → SUBMIT_FOR_REVIEW
    gate: SUBMIT_FOR_REVIEW → GATE_PASS / GATE_BOUNCE
    reviewer: GATE_PASS, or SUBMIT_FOR_REVIEW when no gate ran → verdict
    A span whose start is not one of those (round 1 authoring, a ruling, a
    missing timestamp) is counted as unknown, never guessed. An arbiter
    ruling is never reviewer time; a REQUEST_CHANGES ruling still hands the
    turn back to the lead, so it can start a lead span.
    """
    times = {r: {"seconds": 0.0, "spans": 0, "unknown": 0} for r in ("lead", "reviewer", "gate")}
    flow = [e for e in entries if e.get("action") != "AMEND"]
    for i, e in enumerate(flow):
        action, role = e.get("action"), e.get("role")
        prev = flow[i - 1] if i > 0 else None
        if role == "lead" and action == "SUBMIT_FOR_REVIEW":
            key, starts = "lead", ("REQUEST_CHANGES", "GATE_BOUNCE")
        elif role == "gatekeeper" and action in ("GATE_PASS", "GATE_BOUNCE"):
            key, starts = "gate", ("SUBMIT_FOR_REVIEW",)
        elif role == "reviewer" and action in REVIEWER_VERDICTS and not _is_ruling(e):
            key, starts = "reviewer", ("GATE_PASS", "SUBMIT_FOR_REVIEW")
        else:
            continue
        span = None
        if prev is not None and prev.get("action") in starts:
            span = _seconds(_parse_ts(prev.get("ts")), _parse_ts(e.get("ts")))
        if span is None:
            times[key]["unknown"] += 1
        else:
            times[key]["seconds"] += span
            times[key]["spans"] += 1
    for t in times.values():
        t["seconds"] = round(t["seconds"], 1)
    return times


# ---------------------------------------------------------------------------
# Usage: stored consumption and turn coverage
# ---------------------------------------------------------------------------

def _consumption(rows: list[dict]) -> dict:
    from tagteam import usage as usage_mod
    agg = usage_mod.aggregate(rows, ("cycle", "role", "kind", "model"))

    def strip(bucket: dict) -> dict:
        return {k: v for k, v in bucket.items() if not k.startswith("cost")}

    return {
        "rows": len(rows),
        "with_tokens": sum(1 for r in rows if _has_tokens(r)),
        "without_token_data": sum(1 for r in rows if not _has_tokens(r)),
        "non_ok": sum(1 for r in rows if r.get("status") != "ok"),
        "totals": strip(agg["totals"]),
        "by_cycle": {k: strip(v) for k, v in agg["by_cycle"].items()},
        "by_role": {k: strip(v) for k, v in agg["by_role"].items()},
        "by_kind": {k: strip(v) for k, v in agg["by_kind"].items()},
        "by_model": {k: strip(v) for k, v in agg["by_model"].items()},
    }


def _attribution(row: dict, phase: str) -> tuple[str, int, str] | None:
    """(type, round, role) of the workflow turn a row can be attributed to by
    an exact rule, or None. Rule 1: a row with a target matches only that
    target. Rule 2 (null target only): owed-state identity — reviewer N → N,
    lead N → N+1."""
    role = row.get("role")
    if role not in _WORKFLOW_ROLES:
        return None
    if row.get("target_phase") is not None:
        ttype, tround = row.get("target_type"), _int(row.get("target_round"))
        if row.get("target_phase") != phase or ttype not in CYCLE_TYPES or tround is None:
            return None
        return ttype, tround, role
    if row.get("phase") != phase or row.get("type") not in CYCLE_TYPES:
        return None
    rnd = _int(row.get("round"))
    if rnd is None:
        return None
    return row["type"], (rnd + 1 if role == "lead" else rnd), role


def _coverage(cycles: dict[str, list[dict]], rows: list[dict], phase: str) -> dict:
    """Agent turns (never arbiter rulings) matched to usage rows. Two agent
    turns with the same (type, round, role) — a reviewer answering again in
    the same round after NEED_HUMAN → `rule answer` — cannot be told apart by
    stored identity: when rows exist for that key every such turn is
    `unknown` (rows listed as `ambiguous_row_ids`), never matched twice."""
    turns = []
    for ctype in CYCLE_TYPES:
        for e in cycles.get(ctype) or []:
            if _is_agent_turn(e):
                turns.append({"type": ctype, "round": _int(e.get("round")), "role": e.get("role"),
                              "panel": str(e.get("updated_by") or "").endswith(" panel")})
    keys = {(t["type"], t["round"], t["role"]) for t in turns}
    repeats = {k for k in keys
               if sum(1 for t in turns if (t["type"], t["round"], t["role"]) == k) > 1}
    by_key: dict[tuple, list[dict]] = {}
    unattributed = 0
    for r in rows:
        key = _attribution(r, phase)
        if key is None:
            continue
        if key not in keys:
            unattributed += 1
            continue
        by_key.setdefault(key, []).append(r)

    counts = {s: 0 for s in TURN_STATUSES}
    with_non_ok = 0
    out_turns = []
    for t in turns:
        key = (t["type"], t["round"], t["role"])
        candidates = by_key.get(key, [])
        if t["role"] == "reviewer":
            candidates = [r for r in candidates if _is_panel(r) == t["panel"]]
        else:
            candidates = [r for r in candidates if not _is_panel(r) and not r.get("kind")]
        ambiguous: list = []
        if candidates and key in repeats:
            status, ambiguous, candidates = "unknown", [r.get("id") for r in candidates], []
        elif candidates:
            status = "matched" if any(_has_tokens(r) for r in candidates) else "no_token_data"
        elif t["role"] == "lead" and t["round"] == 1:
            status = "unknown"      # start-command turn; no target row to prove it
        else:
            status = "unmatched"
        non_ok = sum(1 for r in candidates if r.get("status") != "ok")
        counts[status] += 1
        if non_ok:
            with_non_ok += 1
        out_turns.append({"type": t["type"], "round": t["round"], "role": t["role"],
                          "status": status, "row_ids": [r.get("id") for r in candidates],
                          "ambiguous_row_ids": ambiguous, "non_ok_rows": non_ok})
    return {"turns": len(turns), **counts, "turns_with_non_ok_rows": with_non_ok,
            "unattributed_rows": unattributed, "per_turn": out_turns}


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def _interjections(conn, phase: str) -> list[dict] | None:
    from tagteam import db
    try:
        return db.get_interjections(conn, phase=phase)
    except Exception:
        return None             # an older table shape: unavailable, not zero


def phase_report(project_root: str | Path, phase: str) -> dict | None:
    """Pure read of one phase. None when the phase has no cycle and no usage."""
    from tagteam import cycle, db
    root = str(project_root)
    conn, db_note = db.connect_for_read(project_dir=root)
    reader = conn if conn is not None else cycle.FILES_ONLY
    try:
        entries: dict[str, list[dict]] = {}
        cycles: dict[str, dict] = {}
        has_gates = conn is not None and bool(db.table_columns(conn, "gates"))
        notes_cols = db.table_columns(conn, "interjections") if conn is not None else set()
        has_notes = {"phase", "type"} <= notes_cols
        all_notes = _interjections(conn, phase) if has_notes else None
        for ctype in CYCLE_TYPES:
            status = cycle.read_status(phase, ctype, root, conn=reader)
            rounds = cycle.read_rounds(phase, ctype, root, conn=reader)
            if status is None and not rounds:
                continue
            entries[ctype] = rounds
            gates = db.gates_for_cycle(conn, phase, ctype) if has_gates else None
            notes = ([n for n in all_notes if n.get("type") == ctype]
                     if all_notes is not None else None)
            cycles[ctype] = {**_cycle_counts(rounds, status, gates, notes), "time": _turn_times(rounds)}
        stored: list[dict] = []
        candidates: list[dict] = []
        limits: list[dict] | None = None
        usage_available = conn is not None and bool(db.table_columns(conn, "usage"))
        if usage_available:
            stored = db.get_usage(conn, phase=phase)
            seen = {r["id"] for r in stored}
            candidates = list(stored) + [r for r in db.get_usage(conn, target_phase=phase)
                                         if r["id"] not in seen]
        if conn is not None and db.table_columns(conn, "rate_limits"):
            limits = db.latest_rate_limits(conn)
    finally:
        if conn is not None:
            conn.close()

    if not cycles and not candidates:
        return None

    all_entries = [e for t in CYCLE_TYPES for e in entries.get(t, [])]
    first = _parse_ts(all_entries[0].get("ts")) if all_entries else None
    impl = entries.get("impl") or []
    approve = next((e for e in reversed(impl) if e.get("action") == "APPROVE"), None)
    end_entry = approve or (all_entries[-1] if all_entries else None)
    end = _parse_ts(end_entry.get("ts")) if end_entry else None
    plan_approve = next((e for e in reversed(entries.get("plan") or [])
                         if e.get("action") == "APPROVE"), None)
    impl_first = impl[0] if impl else None

    report: dict = {
        "phase": phase,
        "cycles": cycles,
        "wall_clock": {
            "start": first.isoformat() if first else None,
            "end": end.isoformat() if end else None,
            "approved": approve is not None,
            "seconds": _seconds(first, end),
            "implementation_before_first_submit_seconds": _seconds(
                _parse_ts((plan_approve or {}).get("ts")), _parse_ts((impl_first or {}).get("ts")))
                if plan_approve and impl_first else None,
        },
        "database": "ok" if conn is not None else db_note,
        # every note written while this phase was owed, any cycle type; None = unavailable
        "interjections": len(all_notes) if all_notes is not None else None,
        "usage": None,
        "coverage": None,
        "rate_limits": None,
    }
    if usage_available:
        report["usage"] = _consumption(stored)
        report["coverage"] = _coverage(entries, candidates, phase)
    if limits is not None and first is not None:
        span_end = end or first
        report["rate_limits"] = [
            {"provider": lim.get("provider"), "kind": lim.get("kind"),
             "status": lim.get("status"), "ts": lim.get("ts"), "resets_at": lim.get("resets_at")}
            for lim in limits
            if (lambda t: t is not None and first <= t <= span_end)(_parse_ts(lim.get("ts")))
        ]
    return report


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def _fmt_dur(seconds) -> str:
    if not isinstance(seconds, (int, float)):
        return "unknown"
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def _fmt_int(v) -> str:
    return f"{int(v):,}" if isinstance(v, (int, float)) else "-"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def render_text(rep: dict) -> str:
    cycles = rep["cycles"]
    head = " · ".join(f"{t} {c['state'] or 'open'} r{c['rounds']}" for t, c in cycles.items())
    lines = [f"Phase report: {rep['phase']}" + (f" — {head}" if head else "")]
    for t, c in cycles.items():
        parts = [_plural(c["rounds"], "round"), _plural(c["change_requests"], "change request"),
                 _plural(c["gate_bounces"], "bounce")]
        if c["gate_runs"]:
            parts.append(f"gate {_plural(c['gate_runs'], 'run')}, {_fmt_dur(c['gate_seconds'])}")
        if c["amendments"]:
            parts.append(_plural(c["amendments"], "amendment"))
        if c["escalations"] or c["need_human"]:
            parts.append(f"{c['escalations']} escalated, {c['need_human']} need-human")
        if c["rulings"]:
            parts.append(_plural(c["rulings"], "arbiter ruling"))
        if c["interjections"] is None:
            parts.append("interjections unavailable")
        elif c["interjections"]:
            parts.append(_plural(c["interjections"], "interjection"))
        lines.append(f"  {t:<6} " + " · ".join(parts))
    wc = rep["wall_clock"]
    label = "start→approve" if wc["approved"] else "start→last entry"
    time_parts = [f"{label} {_fmt_dur(wc['seconds'])}"]
    if wc["implementation_before_first_submit_seconds"] is not None:
        time_parts.append(f"implementation before first submit "
                          f"{_fmt_dur(wc['implementation_before_first_submit_seconds'])}")
    lines.append("  time   " + " · ".join(time_parts))
    role_parts = []
    for role in ("lead", "reviewer", "gate"):
        spans = [c["time"][role] for c in cycles.values()]
        n = sum(s["spans"] for s in spans)
        unknown = sum(s["unknown"] for s in spans)
        if not n and not unknown:
            continue
        secs = sum(s["seconds"] for s in spans) if n else None
        extra = f", {unknown} unknown" if unknown else ""
        role_parts.append(f"{role} {_fmt_dur(secs)} ({_plural(n, 'span')}{extra})")
    if role_parts:
        lines.append("         " + " · ".join(role_parts) + "  (elapsed; includes relay wait)")
    u, cov = rep["usage"], rep["coverage"]
    if u is None:
        lines.append(f"  usage  not available ({rep['database'] or 'no usage table'})")
    elif not u["rows"]:
        lines.append("  usage  no usage rows stored under this phase")
    else:
        tot = u["totals"]
        lines.append(f"  usage  {u['rows']} rows stored under this phase · with tokens "
                     f"{u['with_tokens']} · without token data {u['without_token_data']} · "
                     f"non-ok {u['non_ok']}")
        lines.append(f"         in {_fmt_int(tot['input_tokens'])} · out {_fmt_int(tot['output_tokens'])}"
                     f" · cache_r {_fmt_int(tot['cache_read_tokens'])}"
                     f" · cache_w {_fmt_int(tot['cache_write_tokens'])}")
        for name, b in u["by_role"].items():
            lines.append(f"         {name:<10} {b['turns']} rows · in {_fmt_int(b['input_tokens'])}"
                         f" · out {_fmt_int(b['output_tokens'])} · cache_r {_fmt_int(b['cache_read_tokens'])}")
        for name, b in u["by_model"].items():
            lines.append(f"         {name} ({b['turns']} rows using it) · in {_fmt_int(b['input_tokens'])}"
                         f" · out {_fmt_int(b['output_tokens'])} · cache_r {_fmt_int(b['cache_read_tokens'])}")
    if cov is not None:
        text = (f"  turns  matched {cov['matched']} of {cov['turns']} · no token data "
                f"{cov['no_token_data']} · unmatched {cov['unmatched']} · unknown {cov['unknown']}")
        if cov["turns_with_non_ok_rows"]:
            text += f" · turns with non-ok rows {cov['turns_with_non_ok_rows']}"
        if cov["unattributed_rows"]:
            text += f" · unattributed rows {cov['unattributed_rows']}"
        lines.append(text)
    if rep["rate_limits"]:
        sig = " · ".join(f"{r['provider']} {r['kind']} {r['status']} @ {str(r['ts'])[:16]}"
                         for r in rep["rate_limits"])
        lines.append(f"  limits last signal: {sig}")
    if rep["database"] != "ok":
        lines.append(f"  note   {rep['database']}; figures from cycle files only")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

USAGE = "Usage: tagteam report --phase P [--json]"


def report_command(args: list[str], project_root: str | Path | None = None, out=None) -> int:
    out = out or sys.stdout
    phase = None
    as_json = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--phase" and i + 1 < len(args):
            phase = args[i + 1]; i += 2
        elif a == "--json":
            as_json = True; i += 1
        elif a in ("-h", "--help"):
            print(USAGE, file=out)
            return 0
        else:
            print(f"Unknown argument: {a}\n{USAGE}", file=out)
            return 1
    if not phase:
        print(USAGE, file=out)
        return 1
    if project_root is None:
        from tagteam.state import _resolve_project_root
        project_root = _resolve_project_root()
    from tagteam.cycle import CycleReadError
    try:
        rep = phase_report(project_root, phase)
    except CycleReadError as e:
        print(f"report: {e}", file=out)
        return 1
    if rep is None:
        print(f"report: no cycles or usage rows found for phase {phase!r}", file=out)
        return 1
    if as_json:
        print(json.dumps(rep, indent=2), file=out)
    else:
        print(render_text(rep), file=out)
    return 0
