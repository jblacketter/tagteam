"""Phase 39 — Reviewer panels (3.3).

An opt-in panel takes the reviewer's turn as 2–3 independent LENS reviews
(each a fresh reviewer process with a lens brief, writing a structured
`verdict.json` under a fixed contract) merged deterministically into
exactly ONE ordinary reviewer entry — one `REQUEST_CHANGES` with findings
grouped by lens, `APPROVE` only when every configured lens succeeded and
approved, `ESCALATE` / `NEED_HUMAN` when a lens asks for the human. Any
lens failure with no objecting lens → `fallback`: no decision, the ordinary
reviewer turn is dispatched (never a partial approval, never a stall).

Sequence (`run_panel`, the gate's shape):

    peek: decided row → merged: do NOT dispatch (the entry is the turn);
          fallback: dispatch the ordinary reviewer
    claim the TURN SLOT (kind=panel) — busy → deferred (no DB row)
    under the writer lock: sweep/reconcile, claim the `panels` row
          (at-most-once; ≤ 2 FAILED attempts, superseded never counts) —
          refused → release slot, branch on the persisted decision /
          live-other / attempts exhausted → forced decided `fallback`
    take the interjection snapshot ONCE (recorded on the row); run the
          lenses sequentially (rogue-write detector around each)
    merge → decision
    under the writer lock: pinned + entry-first → `cycle.add_round(role=
          reviewer, …, updated_by="<Reviewer> panel", meta={panel_*})` →
          finish the row + stamp exactly the snapshot delivered
    release slot; merged → dispatch=False, fallback → dispatch=True
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tagteam import cycle as _cycle
from tagteam.contract import CONTRACT_HOWTO
from tagteam import db as _db
from tagteam import dualwrite
from tagteam import headless as h
from tagteam import procs
from tagteam.config import (get_panel_spec, validate_panel_config, get_headless_spec,
                            get_agent_names, read_config)
from tagteam.gatekeeper import (Submission, current_submission, _pinned, _runner_gone, _age_s,
                                _fmt_dur, _stamp, _now_iso)

PANEL_MAX_ATTEMPTS = 2
PANEL_GRACE_S = 120.0
VERDICTS = ("APPROVE", "REQUEST_CHANGES", "ESCALATE", "NEED_HUMAN")
_PRECEDENCE = {"NEED_HUMAN": 3, "ESCALATE": 2, "REQUEST_CHANGES": 1, "APPROVE": 0}
SEVERITIES = ("blocker", "major", "minor")
BUILTIN_BRIEFS_DIR = Path(__file__).parent / "data" / "panels"
OVERRIDE_BRIEFS_REL = Path(".tagteam") / "panels" / "lenses"

PANEL_CONTRACT = """=== PANEL CONTRACT ===
You are ONE lens of a review panel: `{lens}` (lens {index} of {count}). Your
verdict is merged with the other lenses into a single reviewer response —
you speak for your axis only.

Rules:
- Do NOT run `tagteam cycle add`, `tagteam cycle init`, `tagteam rule`, or any
  other tagteam command that writes the cycle or state. The panel writes the
  reviewer's entry from the merged verdicts. Reading (`tagteam cycle rounds`,
  `tagteam gate status`, git, tests) is fine.
- When you are done, WRITE your verdict as JSON to exactly this path and stop:
    {verdict_path}
  Shape:
    {{"verdict": "APPROVE" | "REQUEST_CHANGES" | "ESCALATE" | "NEED_HUMAN",
      "summary": "<one line for your axis>",
      "findings": [{{"title": "...", "detail": "...", "where": "file:line or area",
                    "severity": "blocker" | "major" | "minor"}}],
      "question": "<required for NEED_HUMAN: the question only the human can answer>"}}
  - REQUEST_CHANGES needs at least one blocker/major finding.
  - APPROVE must have no blocker/major findings (minor notes are fine).
  - ESCALATE needs a non-empty summary (the reason). NEED_HUMAN needs `question`.
- Keep findings concrete: what is wrong, where, and what would fix it.
"""


# ---------------------------------------------------------------------------
# spec

@dataclass
class Lens:
    name: str
    brief_path: Path
    source: str                 # builtin | override | config


@dataclass
class PanelSpec:
    enabled: bool
    on: list
    phases: list
    lenses: list                # [Lens]
    reviewer_name: str | None = None
    provider: str | None = None
    executable: str | None = None
    argv: list | None = None
    timeout_s: float = float(h.DEFAULT_TURN_TIMEOUT_MINUTES) * 60.0
    problems: list = field(default_factory=list)
    tail_n: int = h.DEFAULT_TAIL_ROUNDS      # the watcher's reviewer tail bound (--tail-rounds)

    def applies_to(self, phase: str, cycle_type: str) -> bool:
        if not self.enabled or cycle_type not in (self.on or []):
            return False
        return not self.phases or phase in self.phases

    @property
    def lens_names(self) -> list[str]:
        return [l.name for l in self.lenses]


def _resolve_brief(name: str, configured: str | None, project_root: Path) -> tuple[Path | None, str]:
    if configured:
        p = Path(configured)
        if not p.is_absolute():
            p = project_root / p
        return (p if p.is_file() else None), "config"
    override = project_root / OVERRIDE_BRIEFS_REL / f"{name}.md"
    if override.is_file():
        return override, "override"
    builtin = BUILTIN_BRIEFS_DIR / f"{name}.md"
    if builtin.is_file():
        return builtin, "builtin"
    return None, "missing"


def resolve_panel(config: dict | None, project_root: str | Path, *, tail_n: int | None = None) -> PanelSpec:
    """Validate + resolve the panel for a run. Never raises: problems are
    returned so the watcher can warn and disable (briefer/gate contract).
    `tail_n` is the watcher's bounded reviewer tail (`--tail-rounds`)."""
    config = config or {}
    tail_n = int(tail_n) if tail_n is not None else h.DEFAULT_TAIL_ROUNDS
    root = Path(project_root)
    try:
        problems = list(validate_panel_config(config))
        spec = get_panel_spec(config)
    except Exception as e:  # contract: never raise
        return PanelSpec(False, ["impl"], [], [], problems=[f"panel config unreadable: {e}"])
    lenses: list[Lens] = []
    for entry in spec["lenses"]:
        path, source = _resolve_brief(entry["name"], entry.get("brief"), root)
        if path is None:
            problems.append(f"panel lens {entry['name']!r}: brief not found "
                            f"({entry.get('brief') or 'no built-in brief of that name'})")
            continue
        lenses.append(Lens(entry["name"], path, source))
    if not spec["enabled"]:
        return PanelSpec(False, spec["on"], spec["phases"], lenses, problems=problems)
    reviewer_name = None
    provider = executable = None
    argv = None
    timeout_s = float(h.DEFAULT_TURN_TIMEOUT_MINUTES) * 60.0
    try:
        _, reviewer_name = get_agent_names(config)
        hs = get_headless_spec(config, "reviewer")
        provider = hs.get("provider")
        if provider not in h.ADAPTERS:
            problems.append("panel: the reviewer must validate for headless turns "
                            "(set agents.reviewer.headless.provider to claude or codex)")
        else:
            executable = h.resolve_executable(provider, hs.get("executable"))
            argv = h.build_argv(h.ADAPTERS[provider], executable, hs.get("args") or [], str(root))
            tmo = hs.get("timeout_minutes")
            if tmo:
                timeout_s = float(tmo) * 60.0
    except Exception as e:
        problems.append(f"panel: reviewer headless spec invalid: {e}")
    return PanelSpec(not problems, spec["on"], spec["phases"], lenses, reviewer_name, provider,
                     executable, argv, timeout_s, problems, tail_n)


def load_spec(project_root: str | Path, *, tail_n: int | None = None) -> PanelSpec:
    return resolve_panel(read_config(Path(project_root) / "tagteam.yaml") or {}, project_root, tail_n=tail_n)


# ---------------------------------------------------------------------------
# context (ONE builder for the real run and `panel preview`)

def build_lens_context(root: str, sub: Submission, spec: PanelSpec, *, notes: list[dict] | None = None,
                       conn=None) -> dict:
    """Everything a lens prompt needs beyond the brief/contract, built the
    same way for `run_panel` and `panel preview`: the fresh top-level state,
    the plan text, the reviewer's BOUNDED round tail (`spec.tail_n`, the
    watcher's `--tail-rounds`) with lead-only interjections stripped, the
    gate's entry for this round if any, and the reviewer-scoped pending
    notes (`notes` when the caller already snapshotted them)."""
    from tagteam.state import read_state
    st = read_state(root) or {}
    try:
        tail = _cycle.tail_rounds(sub.phase, sub.type, spec.tail_n, root)
    except Exception:
        tail = []
    for e in tail:
        if isinstance(e, dict) and e.get("interjections"):
            e["interjections"] = [i for i in e["interjections"] if i.get("target_role") in (None, "reviewer")]
    plan_file = Path(root) / "docs" / "phases" / f"{sub.phase}.md"
    plan_text = plan_file.read_text(encoding="utf-8", errors="replace") if plan_file.exists() else None
    gate_entry = None
    try:
        for e in reversed(_cycle.read_rounds_file(sub.phase, sub.type, root)):
            if e.get("role") == _cycle.ROLE_GATEKEEPER and int(e.get("round") or -1) == sub.round:
                gate_entry = e
                break
    except Exception:
        pass
    if notes is None:
        notes = []
        try:
            own = conn is None
            c = conn or _db.connect(project_dir=root)
            try:
                notes = list(_db.pending_interjections_for(c, "reviewer", sub.phase, sub.type))
            finally:
                if own:
                    c.close()
        except Exception:
            notes = []
    return {"state": st, "plan_text": plan_text, "tail": tail, "gate_entry": gate_entry, "interjections": notes}


# ---------------------------------------------------------------------------
# prompt

def compose_lens_prompt(spec: PanelSpec, lens: Lens, index: int, sub: Submission, *, state: dict,
                        plan_text: str | None, tail: list[dict], interjections: list[dict],
                        gate_entry: dict | None, verdict_path: Path) -> str:
    brief = lens.brief_path.read_text(encoding="utf-8", errors="replace").strip()
    parts = [
        f"You are {spec.reviewer_name or 'the reviewer'}, the reviewer in a tagteam handoff, acting as the "
        f"`{lens.name}` lens of a review panel for phase `{sub.phase}` ({sub.type} cycle, round {sub.round}).",
        "",
        PANEL_CONTRACT.format(lens=lens.name, index=index, count=len(spec.lenses), verdict_path=str(verdict_path)),
        "=== LENS BRIEF ===",
        brief,
        "",
    ]
    inter = h.render_interjections(interjections or [])
    if inter:
        parts.append(inter.rstrip())
        parts.append("")
    parts += ["=== CURRENT STATE (handoff-state.json) ===",
              json.dumps({k: state.get(k) for k in ("phase", "type", "round", "turn", "status", "seq")}, indent=2), ""]
    if gate_entry:
        parts += ["=== GATEKEEPER (already ran before this panel) ===",
                  (gate_entry.get("content") or "").strip(), ""]
    parts += [f"=== ROUND TAIL (tagteam cycle rounds --phase {sub.phase} --type {sub.type} --tail {len(tail)}) ===",
              "\n".join(json.dumps(e) for e in tail) or "(no rounds yet)", ""]
    parts += [f"=== PLAN (docs/phases/{sub.phase}.md) ===",
              (plan_text or "(no plan document found)").strip(), "",
              "Now do the review for your lens. Read the code and the plan yourself; the round tail is a "
              "summary, not evidence. Write the verdict JSON to the path above and stop."]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# verdicts

def verify_verdict(path: Path) -> tuple[dict | None, str]:
    """(verdict, reason). verdict is the conforming dict or None."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None, "no verdict file written"
    try:
        d = json.loads(raw)
    except ValueError as e:
        return None, f"verdict is not valid JSON: {e}"
    if not isinstance(d, dict):
        return None, "verdict must be a JSON object"
    v = d.get("verdict")
    if v not in VERDICTS:
        return None, f"verdict must be one of {', '.join(VERDICTS)} (got {v!r})"
    summary = d.get("summary")
    if summary is not None and not isinstance(summary, str):
        return None, "summary must be a string"
    findings = d.get("findings")
    if findings is None:
        findings = []
    if not isinstance(findings, list):
        return None, "findings must be a list"
    norm = []
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            return None, f"finding {i + 1} must be an object"
        sev = f.get("severity") or "major"
        if sev not in SEVERITIES:
            return None, f"finding {i + 1}: severity must be blocker|major|minor"
        title = str(f.get("title") or f.get("detail") or "").strip()
        if not title:
            return None, f"finding {i + 1}: title is required"
        norm.append({"title": title, "detail": str(f.get("detail") or "").strip(),
                     "where": (str(f.get("where")).strip() if f.get("where") else None), "severity": sev})
    serious = [f for f in norm if f["severity"] in ("blocker", "major")]
    if v == "REQUEST_CHANGES" and not serious:
        return None, "REQUEST_CHANGES needs at least one blocker/major finding"
    if v == "APPROVE" and serious:
        return None, "APPROVE cannot carry blocker/major findings"
    if v == "NEED_HUMAN" and not (isinstance(d.get("question"), str) and d["question"].strip()):
        return None, "NEED_HUMAN requires a non-empty question"
    if v == "ESCALATE" and not (summary or "").strip():
        return None, "ESCALATE requires a non-empty summary (the reason)"
    return {"verdict": v, "summary": (summary or "").strip(), "findings": norm,
            "question": (d.get("question") or "").strip() or None}, "ok"


@dataclass
class LensResult:
    lens: str
    outcome: str                # ok | failed
    verdict: dict | None = None
    reason: str = ""
    stem: str | None = None
    usage_row_id: int | None = None
    duration_ms: int = 0
    exit_code: int | None = None

    def as_dict(self) -> dict:
        return {"lens": self.lens, "outcome": self.outcome,
                "verdict": self.verdict["verdict"] if self.verdict else None,
                "summary": self.verdict["summary"] if self.verdict else None,
                "reason": self.reason, "stem": self.stem, "usage_row_id": self.usage_row_id,
                "duration_ms": self.duration_ms, "exit_code": self.exit_code}


# ---------------------------------------------------------------------------
# merge

def _fmt_finding(i: int, f: dict) -> str:
    where = f" ({f['where']})" if f.get("where") else ""
    detail = f" — {f['detail']}" if f.get("detail") and f["detail"] != f["title"] else ""
    return f"{i}. [{f['severity']}] {f['title']}{detail}{where}"


def merge(results: list[LensResult], lens_order: list[str]) -> dict:
    """Deterministic merge. Returns {'decision': action|None, 'content',
    'fallback': bool, 'reason'} — `decision` None means fallback."""
    by = {r.lens: r for r in results}
    ordered = [by[n] for n in lens_order if n in by] + [r for r in results if r.lens not in lens_order]
    ok = [r for r in ordered if r.outcome == "ok"]
    failed = [r for r in ordered if r.outcome != "ok"]

    def lens_label(r: LensResult) -> str:
        if r.outcome != "ok":
            return f"{r.lens}: lens failed ({r.reason})"
        v = r.verdict
        n_b = sum(1 for f in v["findings"] if f["severity"] == "blocker")
        n_m = sum(1 for f in v["findings"] if f["severity"] == "major")
        counts = ", ".join(x for x in [f"{n_b} blocker{'s' if n_b != 1 else ''}" if n_b else "",
                                       f"{n_m} major" if n_m else ""] if x)
        return f"{r.lens}: {v['verdict']}" + (f" ({counts})" if counts else "")

    summary_line = " | ".join(lens_label(r) for r in ordered)
    if not ok:
        return {"decision": None, "content": "", "fallback": True,
                "reason": "every lens failed: " + "; ".join(f"{r.lens}: {r.reason}" for r in failed)}
    top = max(_PRECEDENCE[r.verdict["verdict"]] for r in ok)
    if top == 0:                                            # all ok lenses APPROVE
        if failed:
            return {"decision": None, "content": "", "fallback": True,
                    "reason": "all successful lenses approved but " +
                              ", ".join(f"{r.lens} failed ({r.reason})" for r in failed) +
                              " — never approve on a partial panel"}
        lines = [f"PANEL: APPROVE — {summary_line}",
                 " · ".join(f"{r.lens}: {r.verdict['summary'] or 'approved'}" for r in ok)]
        minors = [(r.lens, f) for r in ok for f in r.verdict["findings"]]
        if minors:
            lines.append("minor notes: " + "; ".join(f"[{l}] {f['title']}" for l, f in minors))
        return {"decision": "APPROVE", "content": "\n".join(lines), "fallback": False, "reason": "all lenses approved"}
    action = next(a for a, p in _PRECEDENCE.items() if p == top)
    leaders = [r for r in ok if _PRECEDENCE[r.verdict["verdict"]] == top]      # configured order
    lines = [f"PANEL: {action} — {summary_line}"]
    if action in ("NEED_HUMAN", "ESCALATE"):
        for r in leaders:
            v = r.verdict
            head = v["question"] if action == "NEED_HUMAN" else v["summary"]
            lines += [f"## {r.lens} — {action}", head or "(no reason given)"]
            for i, f in enumerate(v["findings"], 1):
                lines.append(_fmt_finding(i, f))
    # findings grouped by lens: objecting lenses first (configured order), blockers first inside
    objecting = [r for r in ok if r.verdict["verdict"] != "APPROVE" and r not in leaders] if action != "REQUEST_CHANGES" \
        else leaders
    for r in objecting:
        v = r.verdict
        lines.append(f"## {r.lens}" + (f" — {v['verdict']}" if action != "REQUEST_CHANGES" else ""))
        ordered_f = sorted(v["findings"], key=lambda f: SEVERITIES.index(f["severity"]))
        for i, f in enumerate(ordered_f, 1):
            lines.append(_fmt_finding(i, f))
        if not ordered_f and v["summary"]:
            lines.append(v["summary"])
    for r in ok:
        if r.verdict["verdict"] == "APPROVE":
            v = r.verdict
            lines.append(f"## {r.lens} — approved")
            tail = v["summary"] or "no findings"
            minors = [f["title"] for f in v["findings"]]
            if minors:
                tail += "; minors: " + "; ".join(minors)
            lines.append(tail)
    for r in failed:
        lines.append(f"## {r.lens} — lens failed ({r.reason}) — not assessed")
    return {"decision": action, "content": "\n".join(lines), "fallback": False,
            "reason": f"{action} by {', '.join(r.lens for r in leaders)}"}


# ---------------------------------------------------------------------------
# run

@dataclass
class PanelResult:
    status: str                 # merged | fallback | deferred | superseded | cancelled | error | not-applicable | not-ready | stale
    dispatch: bool
    reason: str = ""
    event_key: str | None = None
    panel_id: int | None = None
    attempt: int | None = None
    decision: str | None = None
    content: str | None = None
    lenses: list = field(default_factory=list)
    stem: str | None = None


def _panels_dir(root: str) -> Path:
    return Path(root) / ".tagteam" / "panels"


def _entry_for_event(root: str, phase: str, cycle_type: str, event_key: str) -> dict | None:
    try:
        rounds = _cycle.read_rounds_file(phase, cycle_type, root)
    except Exception:
        return None
    for e in rounds:
        if e.get("panel_event") == event_key:
            return e
    return None


def _round_log_len(root: str, phase: str, cycle_type: str) -> int:
    try:
        return len(_cycle.read_rounds_file(phase, cycle_type, root))
    except Exception:
        return -1


def _seq(root: str) -> int:
    from tagteam.state import read_state
    return int((read_state(root) or {}).get("seq") or 0)


def _stamp_delivered(conn, ids: list[int], *, round_: int, stem: str) -> int:
    if not ids:
        return 0
    return _db.mark_interjections_delivered(conn, [int(i) for i in ids], role="reviewer", round_=round_,
                                            stem=stem, ts=_now_iso())


def _finish_row_from_entry(conn, row: dict, entry: dict, root: str) -> str:
    """Crash after `add_round` before the row finish: complete the row from
    the entry. ORDER MATTERS — stamp exactly the entry's interjection
    snapshot delivered FIRST (idempotent: only undelivered ids change), then
    terminalise the row: a crash between the two leaves a still-running row
    that the next sweep completes; the reverse order could leave a terminal
    row with pending notes forever. `applied_seq` is the exact transition
    seq the entry carries (`panel_applied_seq`, = submission_seq + 1 under
    the pinned write) — never the current top-level seq."""
    ids = entry.get("panel_interjections") or []
    _stamp_delivered(conn, ids, round_=int(entry.get("round") or row["round"]), stem=row.get("stem") or "panel")
    applied_seq = entry.get("panel_applied_seq")
    lenses = entry.get("panel_lenses")
    _db.finish_panel(conn, row["id"], status="merged", ts=_now_iso(), decision=entry.get("action"),
                     lenses_json=json.dumps(lenses) if lenses is not None else None,
                     stem=row.get("stem"), reason=None,
                     applied_seq=int(applied_seq) if applied_seq is not None else None)
    return "merged"


def reconcile_row(conn, row: dict, root: str) -> dict:
    entry = _entry_for_event(root, row["phase"], row["type"], row["event_key"])
    if entry is None:
        return {"status": None}
    if entry.get("panel_id") is not None and int(entry["panel_id"]) != int(row["id"]):
        return {"status": None}
    return {"status": _finish_row_from_entry(conn, row, entry, root)}


def sweep_abandoned_panels(root: str, spec: PanelSpec | None = None, *, conn=None, log=None) -> dict:
    from tagteam import dualwrite
    root = str(Path(root).resolve())
    spec = spec or load_spec(root)
    out = {"reconciled": [], "abandoned": [], "unverifiable": []}
    own = conn is None
    with dualwrite.writer_lock(root):
        if own:
            conn = _db.connect(project_dir=root)
        try:
            for row in _db.unfinished_panels(conn):
                r = reconcile_row(conn, row, root)
                if r["status"] is not None:
                    out["reconciled"].append(row["id"])
                    if log:
                        log(f"   panel: completed row {row['id']} ({row['event_key']}) from its entry → merged")
                    continue
                if row["status"] != "running":
                    continue
                gone, why = _runner_gone(row)
                if gone is True:
                    _db.finish_panel(conn, row["id"], status="abandoned", ts=_now_iso(), reason=f"runner gone: {why}")
                    out["abandoned"].append(row["id"])
                    if log:
                        log(f"   panel: abandoned row {row['id']} ({why})")
                    continue
                age = _age_s(row.get("started_at"))
                limit = spec.timeout_s * max(1, len(spec.lenses)) + PANEL_GRACE_S
                if age is not None and age > limit:
                    marker = h.read_inflight(root) or {}
                    if row.get("stem") and marker.get("stem") == row.get("stem"):
                        out["unverifiable"].append({**row, "sweep_reason": f"over time but slot marker present ({why})"})
                        continue
                    _db.finish_panel(conn, row["id"], status="abandoned", ts=_now_iso(),
                                     reason=f"timed out ({age:.0f}s > {limit:.0f}s) with no slot marker; {why}")
                    out["abandoned"].append(row["id"])
                    continue
                if gone is None:
                    out["unverifiable"].append({**row, "sweep_reason": why})
        finally:
            if own:
                conn.close()
    return out


def _handle_decided(row: dict, root: str, log) -> PanelResult:
    sub = Submission(row["phase"], row["type"], int(row["round"]), int(row["submission_seq"]))
    if row["status"] == "merged":
        return PanelResult("merged", False, "decided merged (recorded) — the entry is the reviewer's turn",
                           sub.event_key, row["id"], row["attempt"], row.get("decision"))
    # fallback → the ordinary reviewer, but only if the state is still that submission
    ok = _pinned(sub, root)
    return PanelResult("fallback", ok, ("decided fallback (recorded)" if ok else
                                       "decided fallback but the submission has advanced — no hand-off"),
                       sub.event_key, row["id"], row["attempt"])


def run_lens(spec: PanelSpec, lens: Lens, index: int, sub: Submission, *, root: str, stem_dir: Path,
             prompt: str, slot, log) -> LensResult:
    stem = f"{stem_dir.name}/{lens.name}"
    log_path = stem_dir / f"{lens.name}.log"
    events_path = stem_dir / f"{lens.name}.events.jsonl"
    (stem_dir / f"{lens.name}.prompt").write_text(prompt, encoding="utf-8")
    verdict_path = stem_dir / f"{lens.name}.verdict.json"
    env = dict(os.environ)
    for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(k, None)
    env["TAGTEAM_PANEL_LENS"] = lens.name
    env[dualwrite.READ_ONLY_ENV] = "1"   # Phase 50: a lens reads the cycle, never writes it

    def _on_spawn(pid: int) -> None:
        h.update_turn_slot(slot, pid=pid, child_ident=procs.identity(pid), lens=lens.name)

    log(f"   panel: lens {index}/{len(spec.lenses)} `{lens.name}` — spawning {spec.provider} … (log: {log_path})")
    pre_len, pre_seq = _round_log_len(root, sub.phase, sub.type), _seq(root)
    spawn_error = None
    try:
        out = h.run_process(spec.argv, prompt, root, events_path=events_path, log_path=log_path,
                            provider=spec.provider, timeout_s=spec.timeout_s, on_spawn=_on_spawn, env=env)
    except h.SpawnError as e:
        spawn_error = str(e)
        out = h.RunOutput(exit_code=None, timed_out=False, duration_ms=0)
    cancel = h.read_cancel(root)
    cancelled_by = None
    if cancel is not None and cancel.get("stem") == slot.marker.get("stem"):
        h.clear_cancel(root)
        cancelled_by = cancel.get("by") or "arbiter"
    verdict, vreason = (None, "not run") if spawn_error else verify_verdict(verdict_path)
    if cancelled_by:
        outcome, reason, ustatus = "failed", f"cancelled by {cancelled_by}", "cancelled"
    elif spawn_error:
        outcome, reason, ustatus = "failed", f"could not start {spec.provider}: {spawn_error}", "spawn_failed"
    elif out.timed_out:
        outcome, reason, ustatus = "failed", f"timeout after {spec.timeout_s / 60:.0f} min", "timeout"
    elif _round_log_len(root, sub.phase, sub.type) != pre_len or _seq(root) != pre_seq:
        outcome, reason, ustatus = "failed", "wrote to the cycle (round log / state changed during the lens)", "no_round"
    elif verdict is None:
        outcome, reason, ustatus = "failed", vreason, ("nonzero_exit" if out.exit_code not in (0, None) else "no_round")
    else:
        outcome, reason, ustatus = "ok", "ok", "ok"
    try:
        lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    usage = h.parse_usage(spec.provider, lines) or {} if spec.provider else {}
    try:
        h.record_rate_limits(root, spec.provider, lines, log=log)
    except Exception:
        pass
    usage_row_id = None
    try:
        conn = _db.connect(project_dir=root)
        try:
            usage_row_id = _db.add_usage(conn, ts=_now_iso(), phase=sub.phase, type=sub.type, round=sub.round,
                                         role="reviewer", agent=spec.reviewer_name, provider=spec.provider,
                                         status=ustatus, exit_code=out.exit_code, duration_ms=out.duration_ms,
                                         log_path=str(log_path), kind=f"panel:{lens.name}",
                                         # Phase 55: a panel lens is part of the reviewer turn of this round
                                         target_phase=sub.phase, target_type=sub.type, target_round=sub.round,
                                         **{k: usage.get(k) for k in ("model", "input_tokens", "output_tokens",
                                                                      "cache_read_tokens", "cache_write_tokens",
                                                                      "cost_usd", "num_turns", "session_id",
                                                                      "model_usage_json")})
        finally:
            conn.close()
    except Exception as e:
        log(f"   panel: usage row failed: {e}")
    log(f"   panel: lens `{lens.name}` → {outcome}" + (f" {verdict['verdict']}" if verdict else f" ({reason})"))
    return LensResult(lens.name, outcome, verdict, reason, stem, usage_row_id, out.duration_ms, out.exit_code)


def run_panel(root: str, *, kind: str = "auto", spec: PanelSpec | None = None, phase: str | None = None,
              cycle_type: str | None = None, state: dict | None = None, log=None) -> PanelResult:
    from tagteam import dualwrite
    from tagteam.state import read_state
    root = str(Path(root).resolve())
    log = log or (lambda *_: None)
    spec = spec or load_spec(root)
    sub = current_submission(root, phase=phase, cycle_type=cycle_type)
    if sub is None:
        return PanelResult("not-ready", False, "no reviewer-ready submission (fresh state)")
    if state is not None and int(state.get("seq") or 0) != sub.submission_seq:
        return PanelResult("stale", False, f"observed seq {state.get('seq')} != live seq {sub.submission_seq}",
                           sub.event_key)
    if not spec.applies_to(sub.phase, sub.type):
        return PanelResult("not-applicable", True, "panel not enabled for this cycle", sub.event_key)

    # 0. peek
    with dualwrite.writer_lock(root):
        conn = _db.connect(project_dir=root)
        try:
            decided = _db.decided_panel_for_event(conn, sub.event_key)
            if decided is not None:
                return _handle_decided(decided, root, log)
        finally:
            conn.close()

    # 1. slot first
    me = os.getpid()
    ident = procs.identity(me)
    stem = f"{sub.phase}_{sub.type}_r{sub.round}_panel_{_stamp()}"
    inflight = {"phase": sub.phase, "type": sub.type, "round": sub.round, "role": "reviewer",
                "agent": f"{spec.reviewer_name or 'reviewer'} panel", "provider": spec.provider, "stem": stem,
                "log_path": None, "events_path": None, "started_at": _now_iso(), "pid": None, "child_ident": None,
                "watcher_pid": me, "watcher_ident": ident, "event_key": sub.event_key, "panel_kind": kind}
    try:
        slot = h.claim_turn_slot(root, kind=h.SLOT_KIND_PANEL, role="reviewer", fields=inflight)
    except h.SlotBusy as busy:
        log(f"   panel: turn slot busy ({busy.reason}) — will retry")
        return PanelResult("deferred", False, f"turn slot busy: {busy.reason}", sub.event_key)

    row_id = attempt = None
    try:
        # 2. locked sweep + claim (+ the interjection snapshot, taken once)
        with dualwrite.writer_lock(root):
            conn = _db.connect(project_dir=root)
            try:
                sweep_abandoned_panels(root, spec, conn=conn, log=log)
                try:
                    notes = list(_db.pending_interjections_for(conn, "reviewer", sub.phase, sub.type))
                except Exception:
                    notes = []
                note_ids = [n["id"] for n in notes]
                claimed = _db.claim_panel(conn, ts=_now_iso(), phase=sub.phase, cycle_type=sub.type,
                                          round_=sub.round, submission_seq=sub.submission_seq,
                                          event_key=sub.event_key, kind=kind, runner_pid=me, runner_ident=ident,
                                          interjection_ids=note_ids, max_attempts=PANEL_MAX_ATTEMPTS)
                if claimed is None:
                    decided = _db.decided_panel_for_event(conn, sub.event_key)
                    if decided is not None:
                        return _handle_decided(decided, root, log)
                    rows = _db.panels_for_event(conn, sub.event_key)
                    if any(r["status"] == "running" for r in rows):
                        return PanelResult("deferred", False, "another panel runner holds this submission",
                                           sub.event_key)
                    failed_n = sum(1 for r in rows if r["status"] in ("error", "abandoned"))
                    forced = _db.claim_panel(conn, ts=_now_iso(), phase=sub.phase, cycle_type=sub.type,
                                             round_=sub.round, submission_seq=sub.submission_seq,
                                             event_key=sub.event_key, kind=kind, runner_pid=me, runner_ident=ident,
                                             interjection_ids=[], max_attempts=failed_n + 1)
                    if forced is None:
                        return PanelResult("deferred", False, "panel row could not be claimed", sub.event_key)
                    row_id, attempt = forced
                    reasons = "; ".join(f"a{r['attempt']}: {r['status']} ({r.get('reason') or '-'})" for r in rows)
                    _db.finish_panel(conn, row_id, status="fallback", ts=_now_iso(),
                                     reason=f"panel could not complete after {failed_n} attempts — {reasons}")
                    log(f"   panel: could not complete after {failed_n} attempts — falling back to the ordinary reviewer turn")
                    return PanelResult("fallback", _pinned(sub, root), "attempts exhausted — ordinary reviewer turn",
                                       sub.event_key, row_id, attempt)
                row_id, attempt = claimed
                stem = f"{stem}_a{attempt}"
                _db.update_panel(conn, row_id, ts=_now_iso(), stem=stem)
            finally:
                conn.close()
        h.update_turn_slot(slot, stem=stem, panel_id=row_id, attempt=attempt)
        stem_dir = _panels_dir(root) / stem
        stem_dir.mkdir(parents=True, exist_ok=True)
        log(f"   panel: {sub.event_key} attempt {attempt} ({kind}) — lenses: {', '.join(spec.lens_names)}"
            + (f"; notes: {note_ids}" if note_ids else ""))

        # 3. context (once, the shared builder) + lenses (sequential)
        pre_entries = _round_log_len(root, sub.phase, sub.type)
        ctx = build_lens_context(root, sub, spec, notes=notes)
        t0 = time.monotonic()
        results: list[LensResult] = []
        rogue = False
        cancelled = None
        for i, lens in enumerate(spec.lenses, 1):
            prompt = compose_lens_prompt(spec, lens, i, sub, state=ctx["state"], plan_text=ctx["plan_text"],
                                         tail=ctx["tail"], interjections=ctx["interjections"],
                                         gate_entry=ctx["gate_entry"],
                                         verdict_path=stem_dir / f"{lens.name}.verdict.json")
            r = run_lens(spec, lens, i, sub, root=root, stem_dir=stem_dir, prompt=prompt, slot=slot, log=log)
            results.append(r)
            if r.outcome == "failed" and r.reason.startswith("cancelled by"):
                cancelled = r.reason
                break                                   # the human's cancel wins: no later lenses
            if r.outcome == "failed" and r.reason.startswith("wrote to the cycle"):
                rogue = True
                break
        duration = time.monotonic() - t0
        lenses_json = json.dumps([r.as_dict() for r in results])
        if cancelled:
            # `tagteam cancel-turn` on a lens: no reviewer transition, no
            # notes delivered, the attempt is recorded (`error`, reason
            # cancelled) and dispatch is PAUSED exactly like a cancelled
            # headless turn — the same reviewer turn stays owed and `tagteam
            # resume` retries it once (attempt 2).
            with dualwrite.writer_lock(root):
                conn = _db.connect(project_dir=root)
                try:
                    _db.finish_panel(conn, row_id, status="error", ts=_now_iso(), duration_s=duration,
                                     lenses_json=lenses_json, stem=stem, reason=cancelled)
                    payload = {"reason": f"panel lens {cancelled}", "outcome": h.OUTCOME_CANCELLED,
                               "phase": sub.phase, "type": sub.type, "round": sub.round, "role": "reviewer",
                               "agent": f"{spec.reviewer_name or 'reviewer'} panel", "provider": spec.provider,
                               "stem": stem, "panel_id": row_id, "attempt": attempt,
                               "log_path": str(stem_dir), "ts": _now_iso()}
                    try:
                        _db.add_diagnostic(conn, "panel_cancelled", payload, payload["ts"])
                        conn.commit()
                    except Exception:
                        pass
                finally:
                    conn.close()
            h.write_pause(root, payload)
            log(f"!! panel {cancelled} — dispatch PAUSED; `tagteam resume` retries the reviewer turn once")
            return PanelResult("cancelled", False, cancelled, sub.event_key, row_id, attempt,
                               lenses=[r.as_dict() for r in results], stem=stem)
        merged = merge(results, spec.lens_names) if not rogue else {"decision": None, "content": "", "fallback": False,
                                                                     "reason": "a lens wrote to the cycle"}

        # 4. locked pinned entry-first apply + row finish + delivery stamp
        with dualwrite.writer_lock(root):
            conn = _db.connect(project_dir=root)
            try:
                existing = _entry_for_event(root, sub.phase, sub.type, sub.event_key)
                pinned = _pinned(sub, root) and _round_log_len(root, sub.phase, sub.type) == pre_entries
                if rogue or (existing is None and not pinned):
                    _db.finish_panel(conn, row_id, status="superseded", ts=_now_iso(), duration_s=duration,
                                     lenses_json=lenses_json, stem=stem,
                                     reason=("a lens wrote to the cycle — its write stands as the reviewer's entry"
                                             if rogue else "submission advanced before the decision could apply"))
                    log(f"   panel: superseded — {merged['reason'] if rogue else 'submission advanced'}")
                    return PanelResult("superseded", False, "superseded", sub.event_key, row_id, attempt,
                                       lenses=[r.as_dict() for r in results], stem=stem)
                if existing is not None:
                    _finish_row_from_entry(conn, {"id": row_id, "round": sub.round, "stem": stem}, existing, root)
                    return PanelResult("merged", False, "entry already present (recovered)", sub.event_key, row_id,
                                       attempt, existing.get("action"), existing.get("content"),
                                       [r.as_dict() for r in results], stem)
                if merged["decision"] is None:                          # fallback
                    _db.finish_panel(conn, row_id, status="fallback", ts=_now_iso(), duration_s=duration,
                                     lenses_json=lenses_json, stem=stem, reason=merged["reason"])
                    log(f"   panel: fallback — {merged['reason']}")
                    return PanelResult("fallback", True, merged["reason"], sub.event_key, row_id, attempt,
                                       lenses=[r.as_dict() for r in results], stem=stem)
                action, content = merged["decision"], merged["content"]
                # the transition's seq is exact under the pinned write:
                # `_derive_top_level_state` bumps the top-level seq by one
                applied_seq = sub.submission_seq + 1
                meta = {"panel_event": sub.event_key, "panel_id": row_id,
                        "panel_lenses": [{"lens": r.lens, "outcome": r.outcome,
                                          "verdict": r.verdict["verdict"] if r.verdict else None} for r in results],
                        "panel_interjections": note_ids, "panel_applied_seq": applied_seq}
                _cycle.add_round(sub.phase, sub.type, "reviewer", action, sub.round, content, root,
                                 updated_by=f"{spec.reviewer_name or 'reviewer'} panel", meta=meta)
                new_seq = _seq(root)
                if new_seq != applied_seq:
                    log(f"   panel: note — top-level seq after the write is {new_seq}, expected {applied_seq}")
                # ORDER: stamp delivery first (idempotent), then terminalise the row —
                # a crash between the two leaves a running row that reconciles from
                # the entry; the reverse could strand pending notes behind a
                # terminal row.
                _stamp_delivered(conn, note_ids, round_=sub.round, stem=stem)
                _db.finish_panel(conn, row_id, status="merged", ts=_now_iso(), duration_s=duration,
                                 lenses_json=lenses_json, decision=action, stem=stem, applied_seq=new_seq)
            finally:
                conn.close()
        log(f"   panel: {content.splitlines()[0]}")
        return PanelResult("merged", False, merged["reason"], sub.event_key, row_id, attempt, action, content,
                           [r.as_dict() for r in results], stem)
    except BaseException as e:
        if row_id is not None:
            try:
                with dualwrite.writer_lock(root):
                    conn = _db.connect(project_dir=root)
                    try:
                        cur = _db.get_panel(conn, row_id)
                        if cur and cur["status"] == "running":
                            _db.finish_panel(conn, row_id, status="error", ts=_now_iso(),
                                             reason=f"{type(e).__name__}: {e}")
                    finally:
                        conn.close()
            except Exception:
                pass
        if isinstance(e, Exception):
            log(f"   panel: error {type(e).__name__}: {e}")
            return PanelResult("error", False, f"{type(e).__name__}: {e}", sub.event_key, row_id, attempt)
        raise
    finally:
        h.release_turn_slot(slot)


# ---------------------------------------------------------------------------
# status + CLI

def panel_status(root: str, phase: str | None = None, cycle_type: str | None = None) -> dict:
    from tagteam.state import read_state
    root = str(Path(root).resolve())
    spec = load_spec(root)
    st = read_state(root) or {}
    phase = phase or st.get("phase")
    cycle_type = cycle_type or st.get("type")
    rows: list[dict] = []
    running: list[dict] = []
    if phase and cycle_type:
        try:
            sweep_abandoned_panels(root, spec)
        except Exception:
            pass
        try:
            conn = _db.connect(project_dir=root)
            try:
                rows = _db.panels_for_cycle(conn, phase, cycle_type)
                running = _db.running_panels(conn)
            finally:
                conn.close()
        except Exception:
            rows = []
    unverifiable = [{"id": r["id"], "event_key": r["event_key"], "reason": why}
                    for r in running for gone, why in [_runner_gone(r)] if gone is None]
    return {"enabled": spec.enabled, "on": spec.on, "phases": spec.phases, "lenses": spec.lens_names,
            "problems": spec.problems, "phase": phase, "type": cycle_type,
            "last": rows[-1] if rows else None, "rows": rows, "unverifiable": unverifiable}


_USAGE = """Usage: tagteam panel <run|status|list|lenses|preview> [--phase P --type T] [--lens L] [--tail N] [--json]
  run      run the panel now on the current reviewer-ready submission (manual mode / no watcher)
  status   last panel for the current cycle: lens outcomes, decision, paths (--json for the raw rows)
  list     every panel row for a cycle
  lenses   the resolved lenses and which brief file each uses (built-in / override / config)
  preview  print the exact prompt lens L would get for the current submission (spawns nothing)"""


def _fmt_row(r: dict) -> str:
    dur = f" {_fmt_dur(r['duration_s'])}" if r.get("duration_s") is not None else ""
    dec = f" → {r['decision']}" if r.get("decision") else ""
    extra = f" — {r['reason']}" if r.get("reason") else ""
    return (f"  #{r['id']} {r['event_key']} a{r['attempt']} {r['kind']:<6} {r['status']:<10}{dec}{dur}"
            f" started {r.get('started_at', '?')}{extra}")


def panel_command(args: list[str], project_root: str | Path | None = None, out=None) -> int:
    from tagteam.state import read_state
    out = out or sys.stdout
    if not args or args[0] in ("-h", "--help"):
        print(_USAGE, file=out)
        return 0 if args else 1
    sub = args[0]
    if sub not in ("run", "status", "list", "lenses", "preview"):
        print(f"Unknown panel subcommand: {sub}\n{_USAGE}", file=out)
        return 1
    phase = ctype = lens_name = None
    as_json = False
    tail_n = None
    i = 1
    while i < len(args):
        a = args[i]
        if a == "--phase" and i + 1 < len(args):
            phase = args[i + 1]; i += 2
        elif a == "--type" and i + 1 < len(args):
            ctype = args[i + 1]; i += 2
        elif a == "--lens" and i + 1 < len(args):
            lens_name = args[i + 1]; i += 2
        elif a == "--tail" and i + 1 < len(args):
            try:
                tail_n = int(args[i + 1])
            except ValueError:
                print("--tail needs an integer", file=out); return 1
            i += 2
        elif a == "--json":
            as_json = True; i += 1
        else:
            print(f"Unknown argument: {a}", file=out); return 1
    if project_root is None:
        from tagteam.state import _resolve_project_root
        project_root = _resolve_project_root()
    root = str(project_root)
    st = read_state(root) or {}
    phase = phase or st.get("phase")
    ctype = ctype or st.get("type")
    spec = load_spec(root, tail_n=tail_n)

    if sub == "lenses":
        if as_json:
            print(json.dumps({"enabled": spec.enabled, "problems": spec.problems,
                              "lenses": [{"name": l.name, "brief": str(l.brief_path), "source": l.source}
                                         for l in spec.lenses]}, indent=2), file=out)
            return 0
        print(f"Panel: {'on' if spec.enabled else 'off'}" + (f" ({', '.join(spec.on)} cycles"
              + (f"; phases: {', '.join(spec.phases)}" if spec.phases else "") + ")" if spec.enabled else ""), file=out)
        for pr in spec.problems:
            print(f"  config: {pr}", file=out)
        for l in spec.lenses:
            print(f"  {l.name:<14} {l.source:<9} {l.brief_path}", file=out)
        if spec.enabled:
            print(f"  reviewer: {spec.reviewer_name} via {spec.provider} ({spec.executable}), "
                  f"timeout {spec.timeout_s / 60:.0f} min per lens", file=out)
        return 0

    if sub == "preview":
        if not lens_name:
            print("preview needs --lens L", file=out); return 1
        lens = next((l for l in spec.lenses if l.name == lens_name), None)
        if lens is None:
            print(f"unknown lens {lens_name!r} (configured: {', '.join(spec.lens_names) or 'none'})", file=out)
            return 1
        subm = current_submission(root, phase=phase, cycle_type=ctype)
        if subm is None:
            print("No reviewer-ready submission to preview against.", file=out); return 1
        ctx = build_lens_context(root, subm, spec)          # identical to the real run
        idx = spec.lens_names.index(lens.name) + 1
        print(compose_lens_prompt(spec, lens, idx, subm, state=ctx["state"], plan_text=ctx["plan_text"],
                                  tail=ctx["tail"], interjections=ctx["interjections"], gate_entry=ctx["gate_entry"],
                                  verdict_path=_panels_dir(root) / "<stem>" / f"{lens.name}.verdict.json"), file=out)
        return 0

    if sub == "run":
        if not spec.enabled:
            print("panel is not enabled — set `panel: {enabled: true}` in tagteam.yaml", file=out)
            for pr in spec.problems:
                print(f"  - {pr}", file=out)
            return 1
        progress = (lambda m: print(m.strip(), file=sys.stderr)) if as_json else (lambda m: print(m.strip(), file=out))
        res = run_panel(root, kind="manual", spec=spec, phase=phase, cycle_type=ctype, log=progress)
        if as_json:
            print(json.dumps({"status": res.status, "dispatch": res.dispatch, "reason": res.reason,
                              "event_key": res.event_key, "panel_id": res.panel_id, "attempt": res.attempt,
                              "decision": res.decision, "lenses": res.lenses}, indent=2), file=out)
        else:
            if res.content:
                print(res.content, file=out)
            print(f"panel: {res.status} — {res.reason}" + (f" ({res.event_key})" if res.event_key else ""), file=out)
            if res.status == "merged":
                print(f"next: the {'lead' if res.decision in ('REQUEST_CHANGES',) else 'arbiter' if res.decision in ('ESCALATE', 'NEED_HUMAN') else 'next phase'} — the panel's entry is the reviewer's response", file=out)
            elif res.status == "fallback":
                print(f"next: the ordinary reviewer turn (tell the reviewer to read {CONTRACT_HOWTO}, then act on their turn)", file=out)
        return 0 if res.status in ("merged", "fallback", "not-applicable") else 1

    info = panel_status(root, phase, ctype)
    if sub == "list":
        if as_json:
            print(json.dumps(info["rows"], indent=2), file=out); return 0
        if not info["rows"]:
            print(f"No panel rows for {info['phase']}_{info['type']}.", file=out); return 0
        print(f"Panel rows for {info['phase']}_{info['type']}:", file=out)
        for r in info["rows"]:
            print(_fmt_row(r), file=out)
        return 0
    if as_json:
        print(json.dumps({k: info[k] for k in ("enabled", "on", "phases", "lenses", "problems", "phase", "type",
                                                "last", "unverifiable")}, indent=2), file=out)
        return 0
    print(f"Panel: {'on' if info['enabled'] else 'off'}" + (f" ({', '.join(info['on'])} cycles; lenses: "
          f"{', '.join(info['lenses'])})" if info['enabled'] else ""), file=out)
    for pr in info["problems"]:
        print(f"  config: {pr}", file=out)
    if not info["phase"] or not info["type"]:
        print("No cycle selected (use --phase/--type).", file=out); return 0
    last = info["last"]
    if last is None:
        print(f"No panel has run for {info['phase']}_{info['type']}.", file=out)
    else:
        print(f"Last panel for {info['phase']}_{info['type']}:", file=out)
        print(_fmt_row(last), file=out)
        try:
            lenses = json.loads(last.get("lenses_json") or "[]")
        except ValueError:
            lenses = []
        for l in lenses:
            mark = "✓" if l.get("outcome") == "ok" else "✗"
            print(f"  {mark} {l['lens']:<14} {l.get('verdict') or '-':<16} {l.get('summary') or l.get('reason') or ''}", file=out)
        if last.get("stem"):
            print(f"  files: {_panels_dir(root) / last['stem']}", file=out)
    for u in info["unverifiable"]:
        print(f"  note: panel row #{u['id']} ({u['event_key']}) is running but its runner cannot be verified — {u['reason']}",
              file=out)
    return 0
