"""Phase 38 — Gatekeeper pre-checks (3.2).

A deterministic gate that runs between the lead's `SUBMIT_FOR_REVIEW` and
the reviewer's turn: the project's test command, the implementation-work
scope check and a plan-doc check. PASS attaches a short report to the
current round and the reviewer is dispatched in the same watcher tick;
BOUNCE hands the turn straight back to the lead with the failing output.
No model is involved.

Sequence (`run_gate`, mirrors the briefer's slot-first claim):

    peek: decided row for this submission → PASS: hand off if the state is
          still that reviewer-ready submission; BOUNCE: ensure/observe the
          lead-ready transition, never dispatch
    claim the TURN SLOT (kind=gate) — busy → deferred (no DB row)
    under the writer lock: sweep/reconcile rows, then claim the gate row
          (at-most-once per event, ≤ 2 attempts) — refused → release slot,
          branch on the persisted decision / live-other / attempts exhausted
    run checks (scope snapshot FROZEN first, then plan-doc, then tests)
    under the writer lock: cycle.ensure_gate_applied(decision) (entry-first,
          idempotent, pinned to the submission) + finish the gate row in the
          same hold
    release slot; PASS → dispatch=True (caller hands off), BOUNCE → False

Every exit path after the slot claim releases the slot. Test output beyond
what the round entry carries goes to `.tagteam/gates/<stem>.log`.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tagteam import cycle as _cycle
from tagteam.contract import CONTRACT_HOWTO
from tagteam import db as _db
from tagteam import headless as h
from tagteam import procs
from tagteam.config import get_gatekeeper_spec, validate_gatekeeper_config

GATE_ROLE_DISPLAY = "Gatekeeper"
GATE_GRACE_S = 120.0                    # sweep: timeout + grace before a live runner is questioned
GATE_MAX_ATTEMPTS = 2                   # one automatic retry per event


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------
# spec

@dataclass
class GateSpec:
    enabled: bool
    on: list
    tests_command: object          # str (shell) | list | None
    tests_timeout_s: float
    scope: bool
    max_bounces: int
    max_output_chars: int
    problems: list = field(default_factory=list)
    on_submit: bool = False        # Phase 41: gate synchronously from `cycle add/init`

    def applies_to(self, cycle_type: str) -> bool:
        return self.enabled and cycle_type in (self.on or [])


def resolve_gatekeeper(config: dict | None) -> GateSpec:
    """Validate + resolve the gate for a run. Never raises: problems are
    returned so the watcher can warn and disable (briefer contract)."""
    config = config or {}
    try:
        problems = validate_gatekeeper_config(config)
        spec = get_gatekeeper_spec(config)
    except Exception as e:  # contract: never raise
        return GateSpec(False, ["impl"], None, 15 * 60.0, True, 2, 4000,
                        [f"gatekeeper config unreadable: {e}"])
    enabled = bool(spec["enabled"]) and not problems
    return GateSpec(enabled, list(spec["on"]), spec["tests_command"], float(spec["tests_timeout_s"]),
                    bool(spec["scope"]), int(spec["max_bounces"]), int(spec["max_output_chars"]),
                    problems, on_submit=bool(spec.get("on_submit")))


def load_spec(project_root: str | Path) -> GateSpec:
    from tagteam.config import read_config
    return resolve_gatekeeper(read_config(Path(project_root) / "tagteam.yaml") or {})


# ---------------------------------------------------------------------------
# checks

@dataclass
class CheckResult:
    id: str
    status: str                 # ok | fail | skip
    summary: str                # one short phrase for the report line
    detail: str = ""            # body text (failure tail, paths, reason)
    duration_s: float = 0.0
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "summary": self.summary,
                "detail": self.detail, "duration_s": round(self.duration_s, 3), "data": self.data}


def _fmt_dur(s: float) -> str:
    s = max(0.0, float(s))
    if s < 60:
        return f"{s:.0f}s" if s >= 10 else f"{s:.1f}s"
    m, sec = divmod(int(round(s)), 60)
    return f"{m}m{sec:02d}s"


def check_scope(spec: GateSpec, phase: str, cycle_type: str, project_root: str) -> CheckResult:
    """Implementation work since the implementation boundary (impl only)."""
    if cycle_type != "impl":
        return CheckResult("scope", "skip", "scope n/a (plan cycle)", "scope applies to impl cycles only")
    if not spec.scope:
        return CheckResult("scope", "skip", "scope skipped (disabled)", "gatekeeper.scope: false")
    t0 = time.monotonic()
    try:
        work = _cycle.compute_impl_work(phase, project_root)
    except _cycle.ImplWorkUnavailable as e:
        return CheckResult("scope", "skip", "scope skipped", str(e), time.monotonic() - t0)
    except Exception as e:  # never let a git hiccup bounce a submission
        return CheckResult("scope", "skip", "scope skipped", f"scope check error: {type(e).__name__}: {e}",
                           time.monotonic() - t0)
    paths = work.get("paths") or []
    n = len(paths)
    if n == 0:
        b = work.get("boundary") or {}
        return CheckResult(
            "scope", "fail", "scope FAILED (no implementation work since the plan was approved)",
            "no path outside tagteam/plan artifacts changed content since the implementation boundary "
            f"(captured {b.get('captured_at', '?')}, {b.get('source', '?')}, HEAD {str(b.get('sha') or 'none')[:12]})"
            + (f"; excluded: {', '.join(work.get('excluded') or [])}" if work.get("excluded") else ""),
            time.monotonic() - t0, {"paths": [], "excluded": work.get("excluded") or []})
    listing = "\n".join(f"  {p}  ({work['detail'].get(p, '')})" for p in paths[:50])
    if n > 50:
        listing += f"\n  … {n - 50} more"
    return CheckResult("scope", "ok", f"scope {n} path{'s' if n != 1 else ''}", listing,
                       time.monotonic() - t0, {"paths": paths, "excluded": work.get("excluded") or []})


def check_plan_doc(phase: str, project_root: str) -> CheckResult:
    rel = f"docs/phases/{phase}.md"
    p = Path(project_root) / rel
    try:
        if p.is_file() and p.stat().st_size > 0 and p.read_text(encoding="utf-8", errors="replace").strip():
            return CheckResult("plan-doc", "ok", "plan-doc ok", rel)
    except OSError as e:
        return CheckResult("plan-doc", "fail", "plan-doc FAILED (unreadable)", f"{rel}: {e}")
    return CheckResult("plan-doc", "fail", f"plan-doc FAILED ({rel} missing or empty)",
                       f"the phase plan {rel} must exist and be non-empty")


# pytest's final line, in both spellings: the equals-delimited default
# ("===== 12 passed, 1 skipped in 0.5s =====") and the bare `-q` form
# ("1265 passed, 5 skipped in 247.01s (0:04:07)"). Anchored at a line start
# (after optional bars/whitespace) so a stray "…passed in 3s" inside a test's
# own output is not mistaken for the summary.
_PYTEST_SUMMARY = re.compile(
    r"^\s*(?:=+\s*)?((?:\d+ (?:passed|failed|errors?|skipped|xfailed|xpassed|warnings?|deselected|rerun)"
    r"(?:, )?)+|no tests ran) in ([\d.]+)s")


def _summarize_test_output(output: str) -> str | None:
    """A compact 'N passed, M skipped' if the runner printed a pytest-style
    summary (with or without the `===` bars, i.e. also under `-q`); else None."""
    for line in reversed(output.splitlines()[-40:]):
        m = _PYTEST_SUMMARY.search(line)
        if m:
            return m.group(1).strip().rstrip(",")
    return None


def _run_command(command, cwd: str, timeout_s: float, env: dict) -> tuple[int | None, str, bool]:
    """Run the test command with stderr merged; kill the whole process
    group on timeout. Returns (exit_code|None, output, timed_out)."""
    shell = isinstance(command, str)
    popen_kwargs = dict(cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                        stdin=subprocess.DEVNULL, shell=shell)
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    else:  # pragma: no cover - Windows
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(command, **popen_kwargs)
    try:
        out_b, _ = proc.communicate(timeout=timeout_s)
        return proc.returncode, (out_b or b"").decode("utf-8", "replace"), False
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:  # pragma: no cover - Windows
                proc.kill()
        except OSError:
            pass
        try:
            out_b, _ = proc.communicate(timeout=10)
        except Exception:
            out_b = b""
        return None, (out_b or b"").decode("utf-8", "replace"), True


def check_tests(spec: GateSpec, project_root: str, *, log_path: Path | None = None) -> CheckResult:
    if not spec.tests_command:
        return CheckResult("tests", "skip", "tests skipped (no command)",
                           "no gatekeeper.tests.command configured")
    env = dict(os.environ)
    env["TAGTEAM_GATE"] = "1"
    cmd_text = spec.tests_command if isinstance(spec.tests_command, str) else " ".join(map(str, spec.tests_command))
    t0 = time.monotonic()
    try:
        code, output, timed_out = _run_command(spec.tests_command, project_root, spec.tests_timeout_s, env)
    except (OSError, ValueError) as e:
        dur = time.monotonic() - t0
        return CheckResult("tests", "fail", f"tests FAILED (could not start: {e})",
                           f"$ {cmd_text}\ncould not start the test command: {e}", dur,
                           {"exit_code": None, "command": cmd_text})
    dur = time.monotonic() - t0
    if log_path is not None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"$ {cmd_text}\n{output}\n[exit={code} timed_out={timed_out} duration={_fmt_dur(dur)}]\n")
        except OSError:
            pass
    tail = output[-spec.max_output_chars:] if spec.max_output_chars > 0 else ""
    if len(output) > len(tail):
        tail = "…\n" + tail
    data = {"exit_code": code, "command": cmd_text, "timed_out": timed_out, "duration_s": round(dur, 1)}
    if timed_out:
        return CheckResult("tests", "fail", f"tests FAILED (timed out after {spec.tests_timeout_s / 60:.0f} min)",
                           f"--- tests: last {spec.max_output_chars} chars ---\n{tail}", dur, data)
    summ = _summarize_test_output(output)
    if summ:
        data["summary"] = summ
    if code == 0:
        return CheckResult("tests", "ok", f"tests ok ({summ + ', ' if summ else ''}{_fmt_dur(dur)})",
                           "", dur, data)
    return CheckResult("tests", "fail", f"tests FAILED ({summ + ', ' if summ else ''}exit {code}, {_fmt_dur(dur)})",
                       f"--- tests: last {spec.max_output_chars} chars ---\n{tail}", dur, data)


def _head_sha(project_root: str) -> str | None:
    """Short sha of the checked-out commit (None outside git)."""
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=project_root,
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def run_checks(spec: GateSpec, phase: str, cycle_type: str, project_root: str, *,
               log_path: Path | None = None, log=None, skip_tests: bool = False) -> dict:
    """Run every applicable check. ORDER MATTERS: the implementation-work
    snapshot is computed and frozen first (before any subprocess can touch
    the tree), then plan-doc, then the test command. `skip_tests` (Phase 41,
    `gate check --skip-tests`) records the tests check as `skip` instead of
    running the command; the result also carries the checked `head` sha."""
    project_root = str(Path(project_root).resolve())
    t0 = time.monotonic()
    head = _head_sha(project_root)
    checks: list[CheckResult] = []
    scope = check_scope(spec, phase, cycle_type, project_root)
    checks.append(scope)
    if log:
        log(f"   gate: {scope.summary}")
    plan = check_plan_doc(phase, project_root)
    checks.append(plan)
    if log:
        log(f"   gate: {plan.summary}")
    if skip_tests:
        tests = CheckResult("tests", "skip", "tests skipped (--skip-tests)",
                            "not run: pre-flight with --skip-tests")
    else:
        if log and spec.tests_command:
            log(f"   gate: running tests ({spec.tests_command if isinstance(spec.tests_command, str) else ' '.join(map(str, spec.tests_command))}) …")
        tests = check_tests(spec, project_root, log_path=log_path)
    checks.append(tests)
    if log:
        log(f"   gate: {tests.summary}")
    return {"checks": [c.as_dict() for c in checks], "phase": phase, "type": cycle_type,
            "duration_s": round(time.monotonic() - t0, 3), "finished_at": _now_iso(),
            "head": head}


# ---------------------------------------------------------------------------
# decision + report

def gate_entries(rounds: list[dict]) -> list[dict]:
    return [e for e in rounds if e.get("role") == _cycle.ROLE_GATEKEEPER
            or e.get("action") in _cycle.GATE_ACTIONS]


def consecutive_bounces(rounds: list[dict], decided: dict | None = None) -> int:
    """Trailing count of APPLIED bounces among the cycle's gate entries.
    `decided` maps `gate_event` → terminal gate-row status; an entry
    whose row applied as `bounce` counts, an applied `pass` resets the
    streak, and an entry whose row is `superseded` (a retained audit entry
    from a recovery race), `error`/`abandoned` or unknown is ignored — it
    never consumes the cap. Without `decided` (no DB), entries count by
    action (legacy view)."""
    n = 0
    for e in reversed(gate_entries(rounds)):
        if decided is not None:
            row_status = decided.get(e.get("gate_event"))
            if row_status == "bounce":
                n += 1
            elif row_status == "pass":
                break
            continue
        if e.get("action") == _cycle.GATE_BOUNCE:
            n += 1
        else:
            break
    return n


def decided_by_event(conn, phase: str, cycle_type: str) -> dict:
    """{event_key: 'pass'|'bounce'|'superseded'|'error'|'abandoned'|'running'}
    for a cycle — the LATEST row per event decides (a decided row is unique
    per event; otherwise the highest attempt)."""
    out: dict = {}
    for r in _db.gates_for_cycle(conn, phase, cycle_type):
        cur = out.get(r["event_key"])
        if cur in ("pass", "bounce"):
            continue
        out[r["event_key"]] = r["status"]
    return out


def applied_bounce_streak(project_root: str, phase: str, cycle_type: str, conn=None) -> int:
    """`consecutive_bounces` against the canonical rounds file + gate rows."""
    try:
        rounds = _cycle.read_rounds_file(phase, cycle_type, project_root)
    except Exception:
        rounds = []
    own = conn is None
    try:
        if own:
            conn = _db.connect(project_dir=project_root)
        decided = decided_by_event(conn, phase, cycle_type)
    except Exception:
        decided = None
    finally:
        if own and conn is not None:
            conn.close()
    return consecutive_bounces(rounds, decided)


def _report_line(verdict: str, checks: list[dict]) -> str:
    return " | ".join([f"GATE: {verdict}"] + [c["summary"] for c in checks])


def decide(results: dict, spec: GateSpec, prior_bounces: int) -> dict:
    """{'action', 'content', 'verdict', 'cap_hit', 'failed': [ids]}.
    A `fail` on any check → BOUNCE; otherwise PASS. After `max_bounces`
    consecutive bounces the next failing submission passes-with-findings."""
    checks = results["checks"]
    failed = [c["id"] for c in checks if c["status"] == "fail"]
    bodies = [c["detail"] for c in checks if c["status"] == "fail" and c["detail"]]
    skipped = [f"{c['id']}: {c['detail'] or c['summary']}" for c in checks if c["status"] == "skip"]
    body_extra = ""
    if skipped:
        body_extra = "not checked — " + "; ".join(skipped)
    # Phase 41: the entry names the checked commit so the reviewer can tie
    # the result to the tree without re-running anything.
    checked = [f"checked: HEAD {results['head']}"] if results.get("head") else []
    if not failed:
        content = "\n".join([_report_line("PASS", checks)] + checked + ([body_extra] if body_extra else []))
        return {"action": _cycle.GATE_PASS, "content": content, "verdict": "PASS", "cap_hit": False,
                "failed": []}
    if prior_bounces >= spec.max_bounces:
        head = (f"GATE: checks failed but bounce cap ({spec.max_bounces}) reached — reviewer, see report\n"
                + _report_line("PASS-WITH-FINDINGS", checks))
        content = "\n".join([head] + checked + bodies + ([body_extra] if body_extra else []))
        return {"action": _cycle.GATE_PASS, "content": content, "verdict": "PASS-WITH-FINDINGS",
                "cap_hit": True, "failed": failed}
    content = "\n".join([_report_line("BOUNCE", checks)] + checked + bodies + ([body_extra] if body_extra else []))
    return {"action": _cycle.GATE_BOUNCE, "content": content, "verdict": "BOUNCE", "cap_hit": False,
            "failed": failed}


def format_check_report(results: dict, decision: dict | None = None) -> str:
    """Human report for `tagteam gate check` / `status`."""
    lines = []
    if decision:
        lines.append(decision["content"].splitlines()[0])
    for c in results["checks"]:
        mark = {"ok": "✓", "fail": "✗", "skip": "–"}.get(c["status"], "?")
        lines.append(f"  {mark} {c['id']:<9} {c['summary']}")
        if c["status"] != "ok" and c["detail"]:
            for dl in c["detail"].splitlines()[:60]:
                lines.append(f"      {dl}")
        elif c["id"] == "scope" and c["status"] == "ok" and c["detail"]:
            for dl in c["detail"].splitlines()[:20]:
                lines.append(f"      {dl}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# submission identity

@dataclass
class Submission:
    phase: str
    type: str
    round: int
    submission_seq: int

    @property
    def event_key(self) -> str:
        return f"{self.phase}/{self.type}/r{self.round}/{self.submission_seq}"


def current_submission(project_root: str, *, phase: str | None = None,
                       cycle_type: str | None = None) -> Submission | None:
    """The reviewer-ready submission the gate would act on, from the FRESH
    top-level state + cycle status, or None."""
    from tagteam.state import read_state
    st = read_state(project_root) or {}
    if not st:
        return None
    if phase is None:
        phase = st.get("phase")
    if cycle_type is None:
        cycle_type = st.get("type")
    if not phase or not cycle_type:
        return None
    if st.get("phase") != phase or st.get("type") != cycle_type:
        return None
    if st.get("turn") != "reviewer" or st.get("status") != "ready":
        return None
    status = _cycle.read_status(phase, cycle_type, project_root) or {}
    if status.get("state") != "in-progress" or status.get("ready_for") != "reviewer":
        return None
    return Submission(phase, cycle_type, int(status.get("round") or st.get("round") or 0),
                      int(st.get("seq") or 0))


def _pinned(sub: Submission, project_root: str) -> bool:
    """Is the freshly re-read state still exactly this reviewer-ready
    submission?"""
    cur = current_submission(project_root, phase=sub.phase, cycle_type=sub.type)
    return cur is not None and cur.round == sub.round and cur.submission_seq == sub.submission_seq


# ---------------------------------------------------------------------------
# reconciliation

def _entry_for_event(project_root: str, phase: str, cycle_type: str, event_key: str) -> dict | None:
    try:
        rounds = _cycle.read_rounds_file(phase, cycle_type, project_root)
    except Exception:
        return None
    for e in rounds:
        if e.get("gate_event") == event_key:
            return e
    return None


def _decision_from_entry(entry: dict, sub: Submission | None, row: dict | None) -> dict:
    return {"action": entry.get("action"), "content": entry.get("content", ""),
            "round": int(entry.get("round") or (row or {}).get("round") or (sub.round if sub else 0)),
            "gate_event": entry.get("gate_event"), "gate_id": entry.get("gate_id") or (row or {}).get("id"),
            "gate_attempt": entry.get("gate_attempt") or (row or {}).get("attempt"),
            "submission_seq": int((row or {}).get("submission_seq") if row else (sub.submission_seq if sub else -1)),
            "applied_seq": (row or {}).get("applied_seq")}


def _finish_from_apply(conn, row_id: int, action: str, applied: dict, *, ts: str,
                       result_json: str | None = None, stem: str | None = None,
                       duration_s: float | None = None) -> str:
    """Translate an `ensure_gate_applied` result into the terminal row status
    and write it. Returns the status."""
    if applied["applied"] == "superseded":
        status = "superseded"
    elif action == _cycle.GATE_PASS:
        status = "pass"
    else:                                   # "applied" | "already"
        status = "bounce"
    reason = None if status in ("pass", "bounce") else "submission advanced before the decision could apply"
    _db.finish_gate(conn, row_id, status=status, ts=ts, duration_s=duration_s, result_json=result_json,
                    stem=stem, reason=reason, applied_seq=applied.get("applied_seq"))
    return status


def _row_sub(row: dict) -> Submission:
    return Submission(row["phase"], row["type"], int(row["round"]), int(row["submission_seq"]))


def reconcile_row(conn, row: dict, project_root: str) -> dict:
    """Complete a `running` / `abandoned` / `error` row whose decision entry
    already exists (crash windows b–d): finish it from the entry and ensure
    any missing BOUNCE transition — never re-run the checks. Returns
    {'status', 'applied'} or {'status': None} when the row has no entry.
    Caller holds the writer lock."""
    entry = _entry_for_event(project_root, row["phase"], row["type"], row["event_key"])
    if entry is None:
        return {"status": None}
    # The entry names the attempt that wrote it (`gate_id`); only THAT row is
    # completed from it — an earlier abandoned/error attempt for the same
    # event stays what it is, and a later running attempt falls back to
    # the normal runner policy (it will find the entry itself).
    if entry.get("gate_id") is not None and int(entry["gate_id"]) != int(row["id"]):
        return {"status": None}
    sub = _row_sub(row)
    decision = _decision_from_entry(entry, sub, row)
    applied = _cycle.ensure_gate_applied(row["phase"], row["type"], decision, project_root)
    status = _finish_from_apply(conn, row["id"], decision["action"], applied, ts=_now_iso())
    return {"status": status, "applied": applied}


def _runner_gone(row: dict) -> tuple[bool | None, str]:
    """(gone, reason): True definitively gone, False alive-and-verified,
    None unverifiable (fail closed)."""
    pid = row.get("runner_pid")
    if not isinstance(pid, int) or pid <= 0:
        return None, "row without a runner pid (fail closed)"
    if not procs.pid_alive(pid):
        return True, f"runner pid {pid} is dead"
    rec = row.get("runner_ident")
    if not rec:
        return None, f"runner pid {pid} alive; row without identity (fail closed)"
    now = procs.identity(pid)
    if now is None:
        return None, f"runner pid {pid} alive but identity unavailable (fail closed)"
    if now != rec:
        return True, f"runner pid {pid} identity mismatch (pid reuse)"
    return False, f"runner pid {pid} alive"


def _age_s(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds()
    except ValueError:
        return None


def sweep_abandoned_gates(project_root: str, spec: GateSpec | None = None, *,
                         conn=None, log=None) -> dict:
    """Under the writer lock (caller-held or taken here): reconcile rows that
    already have a decision entry; mark `running` rows `abandoned` when the
    runner is definitively gone, or timed out (timeout + grace) with no
    turn-slot marker carrying the row's stem; leave live-and-verifiable and
    live-but-unverifiable runners alone (the latter reported).
    Returns {'reconciled': [ids], 'abandoned': [ids], 'unverifiable': [rows]}."""
    from tagteam import dualwrite
    project_root = str(Path(project_root).resolve())
    spec = spec or load_spec(project_root)
    out = {"reconciled": [], "abandoned": [], "unverifiable": []}
    own = conn is None
    with dualwrite.writer_lock(project_root):
        if own:
            conn = _db.connect(project_dir=project_root)
        try:
            for row in _db.unfinished_gates(conn):
                r = reconcile_row(conn, row, project_root)
                if r["status"] is not None:
                    out["reconciled"].append(row["id"])
                    if log:
                        log(f"   gate: completed row {row['id']} ({row['event_key']}) from its entry → {r['status']}")
                    continue
                if row["status"] != "running":
                    continue
                gone, why = _runner_gone(row)
                if gone is True:
                    _db.finish_gate(conn, row["id"], status="abandoned", ts=_now_iso(),
                                    reason=f"runner gone: {why}")
                    out["abandoned"].append(row["id"])
                    if log:
                        log(f"   gate: abandoned row {row['id']} ({why})")
                    continue
                age = _age_s(row.get("started_at"))
                limit = spec.tests_timeout_s + GATE_GRACE_S
                if age is not None and age > limit:
                    marker = h.read_inflight(project_root) or {}
                    if row.get("stem") and marker.get("stem") == row.get("stem"):
                        out["unverifiable"].append({**row, "sweep_reason": f"over time but slot marker present ({why})"})
                        continue
                    _db.finish_gate(conn, row["id"], status="abandoned", ts=_now_iso(),
                                    reason=f"timed out ({age:.0f}s > {limit:.0f}s) with no slot marker; {why}")
                    out["abandoned"].append(row["id"])
                    if log:
                        log(f"   gate: abandoned row {row['id']} (timed out, no slot marker)")
                    continue
                if gone is None:
                    out["unverifiable"].append({**row, "sweep_reason": why})
        finally:
            if own:
                conn.close()
    return out


# ---------------------------------------------------------------------------
# run

@dataclass
class GateResult:
    status: str                 # pass | bounce | deferred | superseded | error | not-applicable | not-ready | stale
    dispatch: bool              # caller may hand the reviewer off now
    reason: str = ""
    event_key: str | None = None
    gate_id: int | None = None
    attempt: int | None = None
    decision: dict | None = None
    results: dict | None = None
    log_path: str | None = None


def _gates_dir(project_root: str) -> Path:
    return Path(project_root) / ".tagteam" / "gates"


def _handle_decided(conn, row: dict, project_root: str, log) -> GateResult:
    """Branch on a PERSISTED decision (peek / refused claim): PASS hands
    off only if the state is still that submission; BOUNCE ensures the
    lead-ready transition and never dispatches."""
    sub = _row_sub(row)
    if row["status"] == "pass":
        if _pinned(sub, project_root):
            return GateResult("pass", True, "decided pass (recorded)", sub.event_key, row["id"], row["attempt"])
        return GateResult("pass", False, "decided pass but the submission has advanced — no hand-off",
                          sub.event_key, row["id"], row["attempt"])
    entry = _entry_for_event(project_root, sub.phase, sub.type, sub.event_key)
    if entry is not None:
        decision = _decision_from_entry(entry, sub, row)
        applied = _cycle.ensure_gate_applied(sub.phase, sub.type, decision, project_root)
        if applied.get("applied") == "applied" and log:
            log(f"   gate: completed the pending bounce for {sub.event_key}")
        if applied.get("applied_seq") is not None and row.get("applied_seq") is None:
            try:
                _db.update_gate(conn, row["id"], ts=_now_iso(), applied_seq=applied["applied_seq"])
            except Exception:
                pass
    return GateResult("bounce", False, "decided bounce (recorded) — reviewer not dispatched",
                      sub.event_key, row["id"], row["attempt"])


def run_gate(project_root: str, *, kind: str = "auto", spec: GateSpec | None = None,
             phase: str | None = None, cycle_type: str | None = None, state: dict | None = None,
             log=None) -> GateResult:
    """Gate the current reviewer-ready submission (see module doc). Never
    raises for the caller's sake beyond programming errors: any unexpected
    exception after the row claim finishes the row `error` and releases the
    slot."""
    from tagteam import dualwrite
    project_root = str(Path(project_root).resolve())
    log = log or (lambda *_: None)
    spec = spec or load_spec(project_root)
    sub = current_submission(project_root, phase=phase, cycle_type=cycle_type)
    if sub is None:
        # Nothing reviewer-ready right now: either the caller's observation
        # is stale (a decided BOUNCE already moved the turn) or the state
        # cannot be read — never a reason to hand the reviewer off.
        return GateResult("not-ready", False, "no reviewer-ready submission to gate (fresh state)")
    if state is not None and int(state.get("seq") or 0) != sub.submission_seq:
        # The caller observed an OLDER seq than the live one: its hand-off is
        # for a submission that no longer exists; the fresh seq gets its own
        # tick (or a fresh gate) — do not gate or dispatch from a stale view.
        return GateResult("stale", False, f"observed seq {state.get('seq')} != live seq {sub.submission_seq}",
                          sub.event_key)
    if not spec.applies_to(sub.type):
        return GateResult("not-applicable", True, f"gate not enabled for {sub.type} cycles", sub.event_key)

    # 0. peek: a decided row for this submission needs no slot at all
    with dualwrite.writer_lock(project_root):
        conn = _db.connect(project_dir=project_root)
        try:
            decided = _db.decided_gate_for_event(conn, sub.event_key)
            if decided is not None:
                return _handle_decided(conn, decided, project_root, log)
        finally:
            conn.close()

    # 1. slot first (kind=gate) — busy → deferred, no DB row written
    me = os.getpid()
    ident = procs.identity(me)
    stem = f"{sub.phase}_{sub.type}_r{sub.round}_gate_{_stamp()}"
    inflight = {"phase": sub.phase, "type": sub.type, "round": sub.round, "role": _cycle.ROLE_GATEKEEPER,
                "agent": "gatekeeper", "provider": None, "stem": stem, "log_path": None, "events_path": None,
                "started_at": _now_iso(), "pid": None, "child_ident": None,
                "watcher_pid": me, "watcher_ident": ident, "event_key": sub.event_key, "gate_kind": kind}
    try:
        slot = h.claim_turn_slot(project_root, kind=h.SLOT_KIND_GATE, role=_cycle.ROLE_GATEKEEPER,
                                 fields=inflight)
    except h.SlotBusy as busy:
        log(f"   gate: turn slot busy ({busy.reason}) — will retry")
        return GateResult("deferred", False, f"turn slot busy: {busy.reason}", sub.event_key)

    row_id = attempt = None
    try:
        # 2. locked sweep + claim
        with dualwrite.writer_lock(project_root):
            conn = _db.connect(project_dir=project_root)
            try:
                sweep_abandoned_gates(project_root, spec, conn=conn, log=log)
                claimed = _db.claim_gate(conn, ts=_now_iso(), phase=sub.phase, cycle_type=sub.type,
                                         round_=sub.round, submission_seq=sub.submission_seq,
                                         event_key=sub.event_key, kind=kind, runner_pid=me,
                                         runner_ident=ident, max_attempts=GATE_MAX_ATTEMPTS)
                if claimed is None:
                    decided = _db.decided_gate_for_event(conn, sub.event_key)
                    if decided is not None:
                        return _handle_decided(conn, decided, project_root, log)
                    rows = _db.gates_for_event(conn, sub.event_key)
                    if any(r["status"] == "running" for r in rows):
                        return GateResult("deferred", False, "another gate runner holds this submission",
                                          sub.event_key)
                    # attempts exhausted → PASS-with-findings so the loop never stalls
                    forced = _db.claim_gate(conn, ts=_now_iso(), phase=sub.phase, cycle_type=sub.type,
                                            round_=sub.round, submission_seq=sub.submission_seq,
                                            event_key=sub.event_key, kind=kind, runner_pid=me,
                                            runner_ident=ident, max_attempts=len(rows) + 1)
                    if forced is None:
                        return GateResult("deferred", False, "gate row could not be claimed", sub.event_key)
                    row_id, attempt = forced
                    reasons = "; ".join(f"a{r['attempt']}: {r['status']} ({r.get('reason') or '-'})" for r in rows)
                    content = (f"GATE: PASS | gate could not complete after {len(rows)} attempts — reviewer, "
                               f"see report\nprevious attempts: {reasons}")
                    decision = {"action": _cycle.GATE_PASS, "content": content, "round": sub.round,
                                "gate_event": sub.event_key, "gate_id": row_id, "gate_attempt": attempt,
                                "submission_seq": sub.submission_seq}
                    applied = _cycle.ensure_gate_applied(sub.phase, sub.type, decision, project_root)
                    st = _finish_from_apply(conn, row_id, _cycle.GATE_PASS, applied, ts=_now_iso(),
                                            result_json=json.dumps({"forced_pass": True, "attempts": rows}))
                    return GateResult(st, st == "pass" and _pinned(sub, project_root),
                                      "attempts exhausted — passed with findings", sub.event_key, row_id, attempt,
                                      decision)
                row_id, attempt = claimed
                stem = f"{stem}_a{attempt}"
                _db.update_gate(conn, row_id, ts=_now_iso(), stem=stem)
            finally:
                conn.close()
        h.update_turn_slot(slot, stem=stem, gate_id=row_id, attempt=attempt)
        log_path = _gates_dir(project_root) / f"{stem}.log"
        log(f"   gate: checking {sub.event_key} (attempt {attempt}, {kind})")

        # 3. checks (outside the lock; slot held). The round log's length is
        # pinned first: an AMEND during the checks is rounds-only (no seq
        # bump) and must still supersede this decision.
        try:
            pre_entries = len(_cycle.read_rounds_file(sub.phase, sub.type, project_root))
        except Exception:
            pre_entries = None
        t0 = time.monotonic()
        results = run_checks(spec, sub.phase, sub.type, project_root, log_path=log_path, log=log)
        decision = decide(results, spec, applied_bounce_streak(project_root, sub.phase, sub.type))
        decision.update({"round": sub.round, "gate_event": sub.event_key, "gate_id": row_id,
                         "gate_attempt": attempt, "submission_seq": sub.submission_seq,
                         "pre_entries": pre_entries})
        duration = time.monotonic() - t0
        results["decision"] = {k: decision[k] for k in ("action", "verdict", "cap_hit", "failed")}
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n" + decision["content"] + "\n")
        except OSError:
            pass

        # 4. locked apply + finish (same hold)
        with dualwrite.writer_lock(project_root):
            applied = _cycle.ensure_gate_applied(sub.phase, sub.type, decision, project_root)
            conn = _db.connect(project_dir=project_root)
            try:
                st = _finish_from_apply(conn, row_id, decision["action"], applied, ts=_now_iso(),
                                        result_json=json.dumps(results), stem=stem, duration_s=duration)
            finally:
                conn.close()
        log(f"   gate: {decision['content'].splitlines()[0]}" if st != "superseded"
            else f"   gate: submission {sub.event_key} advanced before the decision applied — superseded")
        dispatch = (st == "pass" and _pinned(sub, project_root))
        return GateResult(st, dispatch, decision["verdict"] if st != "superseded" else "superseded",
                          sub.event_key, row_id, attempt, decision, results, str(log_path))
    except BaseException as e:
        if row_id is not None:
            try:
                with dualwrite.writer_lock(project_root):
                    conn = _db.connect(project_dir=project_root)
                    try:
                        cur = _db.get_gate(conn, row_id)
                        if cur and cur["status"] == "running":
                            _db.finish_gate(conn, row_id, status="error", ts=_now_iso(),
                                            reason=f"{type(e).__name__}: {e}")
                    finally:
                        conn.close()
            except Exception:
                pass
        if isinstance(e, Exception):
            log(f"   gate: error {type(e).__name__}: {e}")
            return GateResult("error", False, f"{type(e).__name__}: {e}", sub.event_key, row_id, attempt)
        raise
    finally:
        h.release_turn_slot(slot)


# ---------------------------------------------------------------------------
# status views (CLI / cockpit)

def gate_status(project_root: str, phase: str | None = None, cycle_type: str | None = None) -> dict:
    """{'enabled', 'phase', 'type', 'last': row|None, 'rows': [...],
    'unverifiable': [...]}."""
    from tagteam.state import read_state
    project_root = str(Path(project_root).resolve())
    spec = load_spec(project_root)
    st = read_state(project_root) or {}
    phase = phase or st.get("phase")
    cycle_type = cycle_type or st.get("type")
    rows: list[dict] = []
    running: list[dict] = []
    if phase and cycle_type:
        # The approved contract: sweep/reconcile (owner-safe) before the
        # read, exactly like `run` — a dead runner's row is abandoned and a
        # row whose decision entry exists is completed, so what is shown is
        # what is true. Best-effort: a sweep failure never hides the rows.
        try:
            sweep_abandoned_gates(project_root, spec)
        except Exception:
            pass
        try:
            conn = _db.connect(project_dir=project_root)
            try:
                rows = _db.gates_for_cycle(conn, phase, cycle_type)
                running = [r for r in _db.running_gates(conn)]
            finally:
                conn.close()
        except Exception:
            rows = []
    unverifiable = []
    for r in running:
        gone, why = _runner_gone(r)
        if gone is None:
            unverifiable.append({"id": r["id"], "event_key": r["event_key"], "reason": why})
    last = rows[-1] if rows else None
    return {"enabled": spec.enabled, "on": spec.on, "problems": spec.problems, "phase": phase,
            "type": cycle_type, "last": last, "rows": rows, "unverifiable": unverifiable}


def last_gate_summary(project_root: str, phase: str, cycle_type: str) -> dict | None:
    """Cockpit: {'status', 'round', 'ts', 'attempt'} of the most recent
    decided gate for the cycle (from the round entries — no DB needed)."""
    try:
        rounds = _cycle.read_rounds_file(phase, cycle_type, project_root)
    except Exception:
        return None
    ents = gate_entries(rounds)
    if not ents:
        return None
    # the most recent DECIDED entry (a retained superseded audit entry is
    # not the last decision); no DB → the last entry by action
    try:
        conn = _db.connect(project_dir=str(project_root))
        try:
            decided = decided_by_event(conn, phase, cycle_type)
        finally:
            conn.close()
        applied = [x for x in ents if decided.get(x.get("gate_event")) in ("pass", "bounce")]
        if applied:
            ents = applied
    except Exception:
        pass
    e = ents[-1]
    return {"status": "pass" if e.get("action") == _cycle.GATE_PASS else "bounce",
            "round": e.get("round"), "ts": e.get("ts"), "attempt": e.get("gate_attempt"),
            "headline": (e.get("content") or "").splitlines()[0] if e.get("content") else ""}


# ---------------------------------------------------------------------------
# CLI: tagteam gate check | run | status | list

# ---------------------------------------------------------------------------
# Phase 41: on-submit gate (called by `tagteam cycle add/init`)

def on_submit_gate(project_root: str | Path, phase: str, cycle_type: str, *,
                   reviewer: str | None = None, out=None, err=None) -> GateResult | None:
    """Gate the submission that `cycle add/init` just wrote, synchronously,
    when `gatekeeper.on_submit` is on and the gate applies to this cycle
    type. Same at-most-once claim path as the watcher / `gate run` (kind
    `manual`): a gate already claimed elsewhere for this submission is
    observed, not re-run. Prints the verdict and the next step; never
    raises (a broken gate must not turn a written round into an error).
    Returns None when nothing was attempted (off / not applicable)."""
    out = out or sys.stdout
    err = err or sys.stderr
    root = str(Path(project_root).resolve())
    try:
        spec = load_spec(root)
    except Exception as e:  # pragma: no cover - defensive
        print(f"gate (on submit): config unreadable ({e}) — not run", file=err)
        return None
    if not spec.on_submit or not spec.applies_to(cycle_type):
        return None
    for pr in spec.problems:
        print(f"gate (on submit): config: {pr}", file=err)
    who = reviewer or "the reviewer"
    print("gate (on submit): running the checks now — this is the round's one full-suite run", file=err)
    progress = lambda m: print(m.strip(), file=err)  # noqa: E731
    try:
        res = run_gate(root, kind="manual", spec=spec, phase=phase, cycle_type=cycle_type, log=progress)
    except Exception as e:  # the round is written; report and let the watcher / `gate run` decide
        print(f"gate (on submit): error {type(e).__name__}: {e} — the watcher, or `tagteam gate run`, decides", file=err)
        return None
    if res.decision:
        print(res.decision["content"], file=out)
    tag = f" ({res.event_key})" if res.event_key else ""
    if res.status == "pass":
        print(f"gate: pass{tag}", file=out)
        print(f"next: {who}'s turn — tell {who} to read {CONTRACT_HOWTO}, then act on their turn", file=out)
    elif res.status == "bounce":
        print(f"gate: bounce{tag}", file=out)
        print("next: the lead's turn — the turn is already back with you; fix and re-submit with --round N+1", file=out)
    elif res.status == "not-applicable":
        pass
    else:
        print(f"gate: {res.status} — {res.reason}{tag}", file=out)
        print("next: the gate is still owed — the watcher, or `tagteam gate run`, decides", file=out)
    return res


_GATE_USAGE = """Usage: tagteam gate <check|run|status|list> [--phase P --type T] [--json] [--skip-tests]
  check   run the checks against the working tree, print the report, write nothing
          (lead pre-flight before SUBMIT_FOR_REVIEW; exit 0 = would pass, 1 = would bounce;
          --skip-tests = scope + plan-doc only — the pre-flight when `gatekeeper.on_submit` is on)
  run     gate the current reviewer-ready submission and record PASS/BOUNCE
          (manual-mode substitute for the watcher; same at-most-once claim path)
  status  last gate result for the current cycle (--json for the raw rows)
  list    every gate row for a cycle"""


def _fmt_row(r: dict) -> str:
    dur = f" {_fmt_dur(r['duration_s'])}" if r.get("duration_s") is not None else ""
    extra = f" — {r['reason']}" if r.get("reason") else ""
    seq = f" applied_seq={r['applied_seq']}" if r.get("applied_seq") is not None else ""
    return (f"  #{r['id']} {r['event_key']} a{r['attempt']} {r['kind']:<6} {r['status']:<10}"
            f"{dur}{seq} started {r.get('started_at', '?')}{extra}")


def gate_command(args: list[str], project_root: str | Path | None = None, out=None) -> int:
    from tagteam.state import read_state
    out = out or sys.stdout
    if not args or args[0] in ("-h", "--help"):
        print(_GATE_USAGE, file=out)
        return 0 if args else 1
    sub = args[0]
    if sub not in ("check", "run", "status", "list"):
        print(f"Unknown gate subcommand: {sub}\n{_GATE_USAGE}", file=out)
        return 1
    phase = ctype = None
    as_json = False
    skip_tests = False
    i = 1
    while i < len(args):
        a = args[i]
        if a == "--phase" and i + 1 < len(args):
            phase = args[i + 1]; i += 2
        elif a == "--type" and i + 1 < len(args):
            ctype = args[i + 1]; i += 2
        elif a == "--json":
            as_json = True; i += 1
        elif a == "--skip-tests" and sub == "check":
            skip_tests = True; i += 1
        else:
            print(f"Unknown argument: {a}", file=out); return 1
    if project_root is None:
        from tagteam.state import _resolve_project_root
        project_root = _resolve_project_root()
    root = str(project_root)
    st = read_state(root) or {}
    phase = phase or st.get("phase")
    ctype = ctype or st.get("type")
    spec = load_spec(root)

    if sub == "check":
        if not phase or not ctype:
            print("No cycle selected (use --phase/--type).", file=out); return 1
        if spec.problems:
            for pr in spec.problems:
                print(f"gatekeeper config: {pr}", file=out)
        if not spec.enabled:
            print("gatekeeper is not enabled (tagteam.yaml `gatekeeper: {enabled: true}`) — running the checks anyway:",
                  file=out)
        progress = (lambda m: print(m.strip(), file=sys.stderr)) if as_json else (lambda m: print(m.strip(), file=out))
        if spec.on_submit and not skip_tests and spec.applies_to(ctype):
            # Phase 41: the explicit exception to the one-run rule — say so first.
            print("note: on_submit is on — the submit will run the suite again; use --skip-tests for the pre-flight",
                  file=sys.stderr if as_json else out)
        results = run_checks(spec, phase, ctype, root, log=progress, skip_tests=skip_tests)
        decision = decide(results, spec, applied_bounce_streak(root, phase, ctype))
        if as_json:
            print(json.dumps({"results": results, "decision": decision}, indent=2), file=out)
        else:
            print(format_check_report(results, decision), file=out)
            if decision["cap_hit"]:
                print(f"  (bounce cap {spec.max_bounces} reached — the gate would pass this with findings)", file=out)
        return 0 if decision["action"] == _cycle.GATE_PASS and not decision["cap_hit"] else 1

    if sub == "run":
        if not spec.enabled:
            print("gatekeeper is not enabled — set `gatekeeper: {enabled: true}` in tagteam.yaml", file=out)
            for pr in spec.problems:
                print(f"  - {pr}", file=out)
            return 1
        progress = (lambda m: print(m.strip(), file=sys.stderr)) if as_json else (lambda m: print(m.strip(), file=out))
        res = run_gate(root, kind="manual", spec=spec, phase=phase, cycle_type=ctype, log=progress)
        if as_json:
            print(json.dumps({"status": res.status, "dispatch": res.dispatch, "reason": res.reason,
                              "event_key": res.event_key, "gate_id": res.gate_id, "attempt": res.attempt,
                              "decision": res.decision}, indent=2), file=out)
        else:
            if res.decision:
                print(res.decision["content"], file=out)
            print(f"gate: {res.status} — {res.reason}" + (f" ({res.event_key})" if res.event_key else ""), file=out)
            if res.status == "pass":
                print(f"next: the reviewer's turn (the watcher, if running, hands off; otherwise tell the reviewer to read {CONTRACT_HOWTO}, then act on their turn)", file=out)
            elif res.status == "bounce":
                print("next: the lead's turn (turn handed back with the failing report)", file=out)
        return 0 if res.status in ("pass", "bounce", "not-applicable") else 1

    info = gate_status(root, phase, ctype)
    if sub == "list":
        if as_json:
            print(json.dumps(info["rows"], indent=2), file=out)
            return 0
        if not info["rows"]:
            print(f"No gate rows for {info['phase']}_{info['type']}.", file=out)
            return 0
        print(f"Gate rows for {info['phase']}_{info['type']}:", file=out)
        for r in info["rows"]:
            print(_fmt_row(r), file=out)
        return 0

    # status
    if as_json:
        print(json.dumps({k: info[k] for k in ("enabled", "on", "problems", "phase", "type", "last", "unverifiable")},
                         indent=2), file=out)
        return 0
    print(f"Gatekeeper: {'on' if info['enabled'] else 'off'}"
          + (f" ({', '.join(info['on'])} cycles)" if info['enabled'] else ""), file=out)
    for pr in info["problems"]:
        print(f"  config: {pr}", file=out)
    if not info["phase"] or not info["type"]:
        print("No cycle selected (use --phase/--type).", file=out)
        return 0
    last = info["last"]
    if last is None:
        print(f"No gate has run for {info['phase']}_{info['type']}.", file=out)
    else:
        print(f"Last gate for {info['phase']}_{info['type']}:", file=out)
        print(_fmt_row(last), file=out)
        try:
            rj = json.loads(last.get("result_json") or "{}")
        except ValueError:
            rj = {}
        if rj.get("checks"):
            print(format_check_report(rj), file=out)
        summ = last_gate_summary(root, info["phase"], info["type"])
        if summ and summ.get("headline"):
            print(f"  entry: r{summ['round']} {summ['headline']}", file=out)
    for u in info["unverifiable"]:
        print(f"  note: gate row #{u['id']} ({u['event_key']}) is running but its runner cannot be verified — {u['reason']}",
              file=out)
    return 0
