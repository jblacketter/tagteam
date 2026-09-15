"""
Escalation briefer (Phase 33, 3.0 arc).

When a cycle enters `escalated` or `needs-human`, the watcher (or
`tagteam brief --generate`) runs ONE headless turn whose only job is to
write the human arbiter a decision brief. The runner:

  1. resolves the escalation *event* from canonical, file-backed data
     (event_key = phase|type|round|role|action|ts of the latest entry);
  2. atomically claims an attempt row in `briefs` (status running) under
     the project writer lock — at most one automatic attempt per event, at
     most one running attempt per event across kinds, prior success
     satisfies the event;
  3. owns `.tagteam/turns/inflight.json` for the attempt (same keys as
     engine turns so `tagteam tail`/`cancel-turn` work);
  4. spawns the briefer with a size-bounded prompt naming the exact output
     path and required headings, forbids any cycle/state writes;
  5. verifies the output file (ok / partial / failed), records usage
     (role `briefer`), finishes the row, rewrites the `_latest.md` alias
     (enabled mode only), notifies.

Opt-in: `briefer.enabled: true` in tagteam.yaml. Design:
docs/phases/escalation-briefer-30-arc.md.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from tagteam import headless as h
from tagteam import procs
from tagteam.config import (get_briefer_spec, validate_briefer_config,
                            BRIEFER_DEFAULT_TIMEOUT_MINUTES)

ESCALATIONS_RELDIR = Path("docs") / "escalations"
BRIEF_HEADINGS = ["## Positions", "## Crux", "## Evidence", "## Recommendation", "## Rulings"]
BRIEF_STATES = ("escalated", "needs-human")

# --- prompt-size policy (chars, inclusive of markers/separators) -----------
TOTAL_BUDGET = 60_000
ESCALATION_BUDGET = 8_000          # head 5 800 + marker + tail 2 000
ESCALATION_MIN_HEAD, ESCALATION_MIN_TAIL = 1_000, 500
NEWEST_ENTRIES = 6
NEWEST_ENTRY_BUDGET = 4_000
NEWEST_ENTRY_MIN_HEAD, NEWEST_ENTRY_MIN_TAIL = 600, 200
OLDER_ENTRY_CHARS = 400
PLAN_BUDGET = 20_000
INTERJECTIONS_BUDGET = 4_000
STATE_BUDGET = 4_000
ABANDON_GRACE = timedelta(minutes=5)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def event_stamp(ts: str) -> str:
    """Compact an ISO ts to YYYYMMDDTHHMMSSffffff (stable per entry)."""
    try:
        d = datetime.fromisoformat(ts)
        return d.strftime("%Y%m%dT%H%M%S%f")
    except Exception:
        return re.sub(r"[^0-9A-Za-z]", "", ts)[:24] or "unknown"


# ---------------------------------------------------------------------------
# Event identity
# ---------------------------------------------------------------------------

@dataclass
class Event:
    phase: str
    type: str
    round: int
    cycle_state: str
    role: str
    action: str
    ts: str
    content: str
    event_key: str
    event_row_id: int | None = None
    stamp: str = ""


def current_event(project_root: str | Path) -> tuple[Event | None, str]:
    """(event, reason). Event is None unless the state's cycle is escalated
    or needs-human per the canonical per-cycle status."""
    from tagteam.state import read_state
    from tagteam.cycle import read_status, read_rounds
    root = str(project_root)
    st = read_state(root) or {}
    phase, ctype = st.get("phase"), st.get("type")
    if not phase or not ctype:
        return None, "no active cycle in state"
    return event_for_cycle(root, phase, ctype)


def event_for_cycle(project_root: str | Path, phase: str, ctype: str) -> tuple[Event | None, str]:
    from tagteam.cycle import read_status, read_rounds
    root = str(project_root)
    try:
        status = read_status(phase, ctype, root) or {}
    except Exception as e:
        return None, f"cannot read cycle status: {e}"
    cstate = status.get("state")
    if cstate not in BRIEF_STATES:
        return None, f"cycle {phase}_{ctype} is {cstate!r}, not escalated/needs-human"
    try:
        entries = read_rounds(phase, ctype, root) or []
    except Exception as e:
        return None, f"cannot read rounds: {e}"
    if not entries:
        return None, "cycle has no round entries"
    last = entries[-1]
    rnd = int(status.get("round") or last.get("round") or 0)
    key = f"{phase}|{ctype}|{rnd}|{last.get('role')}|{last.get('action')}|{last.get('ts')}"
    row_id = None
    try:
        from tagteam import db
        conn = db.connect(project_dir=root)
        try:
            rows = db.get_rounds_since(conn, phase, ctype, 0)
            if rows:
                row_id = rows[-1]["id"]
        finally:
            conn.close()
    except Exception:
        row_id = None
    ev = Event(phase=phase, type=ctype, round=rnd, cycle_state=cstate,
               role=str(last.get("role")), action=str(last.get("action")),
               ts=str(last.get("ts")), content=str(last.get("content") or ""),
               event_key=key, event_row_id=row_id, stamp=event_stamp(str(last.get("ts"))))
    return ev, "ok"


# ---------------------------------------------------------------------------
# Prompt composition (budgeted)
# ---------------------------------------------------------------------------

def _clip(text: str, budget: int, head_min: int = 0, tail_min: int = 0,
          head_share: float = 0.75) -> tuple[str, bool]:
    """Head+marker+tail clip so the result (marker included) fits `budget`.
    Returns (text, was_clipped)."""
    text = text or ""
    if len(text) <= budget:
        return text, False
    marker_len = 40
    avail = max(budget - marker_len, head_min + tail_min)
    head = max(head_min, int(avail * head_share))
    tail = max(tail_min, avail - head)
    if head + tail > avail:
        head, tail = max(head_min, avail - tail_min), tail_min
    elided = len(text) - head - tail
    marker = f"\n[… {elided} chars elided …]\n"
    out = text[:head] + marker + (text[-tail:] if tail else "")
    return out, True


def _entry_line(e: dict) -> str:
    return (f"[{e.get('ts')}] {e.get('role')} {e.get('action')} "
            f"(by {e.get('updated_by') or '?'})")


def render_brief_prompt_parts(*, event: Event, rounds: list[dict], plan_text: str | None,
                              interjections: list[dict], state: dict, output_path: str,
                              attempt: int, kind: str) -> dict:
    """Return the components before assembly (used by compose + tests)."""
    banner = (
        "You are the ESCALATION BRIEFER for a tagteam handoff cycle. You are NOT a\n"
        "participant: you do not take the lead's or the reviewer's side, you do not\n"
        f"write rounds, and you must NOT run `tagteam cycle add`, `tagteam cycle init`,\n"
        "`tagteam state set`, `tagteam rule`, or anything that changes handoff state.\n"
        "You MAY read files, run the project's tests, and inspect diffs (read-only).\n"
        f"Cycle: {event.phase} / {event.type}, round {event.round}, state "
        f"{event.cycle_state}. Attempt {attempt} ({kind}). Event key: {event.event_key}\n"
    )
    path_block = (
        "=== OUTPUT PATH ===\n"
        f"Write the brief as Markdown to exactly this file (create parent dirs):\n{output_path}\n"
        f"First line of the file: `<!-- tagteam brief | event: {event.event_key} | attempt: {attempt} -->`\n"
        "Print DONE when the file is written, then stop.\n"
    )
    headings_block = (
        "=== REQUIRED HEADINGS (in this order) ===\n"
        + "\n".join(BRIEF_HEADINGS) + "\n"
        "Positions: each side in its own terms. Crux: what is actually in dispute,\n"
        "separated from points already resolved in earlier rounds (for needs-human:\n"
        "what is being asked and why the reviewer could not decide). Evidence: what\n"
        "you checked (files, tests, diffs) and what you found. Recommendation: ONE\n"
        "ruling + confidence high/medium/low + why. Rulings: the exact commands for\n"
        "each option — `tagteam rule approve --content \"...\"`, `tagteam rule\n"
        "request-changes --content \"...\"`, `tagteam rule answer --to lead|reviewer\n"
        "--content \"...\"`.\n"
    )
    esc_body, esc_clipped = _clip(event.content, ESCALATION_BUDGET,
                                  ESCALATION_MIN_HEAD, ESCALATION_MIN_TAIL)
    escalation = (f"=== ESCALATION ENTRY ({event.action} by {event.role} at round "
                  f"{event.round}, {event.ts}) ===\n{esc_body}\n")
    st_text, _ = _clip(json.dumps(state, indent=2), STATE_BUDGET, 800, 200)
    state_block = f"=== CURRENT STATE ===\n{st_text}\n"
    plan_block = ""
    if plan_text:
        pt, pc = _clip(plan_text, PLAN_BUDGET, 2000, 0)
        plan_block = f"=== PHASE PLAN (docs/phases/{event.phase}.md{' — truncated' if pc else ''}) ===\n{pt}\n"
    inter_block = ""
    if interjections:
        lines = [f"[{i.get('ts')}] {i.get('by')} (→ {i.get('target_role') or 'next turn'}): {i.get('note')}"
                 for i in interjections]
        it, ic = _clip("\n".join(lines), INTERJECTIONS_BUDGET, 800, 200)
        inter_block = f"=== ARBITER INTERJECTIONS ({'truncated' if ic else 'pending'}) ===\n{it}\n"
    # history: flatten grouped rounds' `entries` (nothing hidden), oldest first
    flat: list[dict] = []
    for r in rounds:
        for e in r.get("entries") or []:
            flat.append(e)
        if not r.get("entries"):
            # legacy grouped view without entries: synthesize
            if r.get("lead_text"):
                flat.append({"role": "lead", "action": r.get("lead_action"), "ts": "",
                             "updated_by": None, "content": r["lead_text"]})
            if r.get("reviewer_text"):
                flat.append({"role": "reviewer", "action": r.get("action"), "ts": "",
                             "updated_by": None, "content": r["reviewer_text"]})
    return {
        "banner": banner, "path_block": path_block, "headings_block": headings_block,
        "escalation": escalation, "escalation_clipped": esc_clipped,
        "state_block": state_block, "plan_block": plan_block, "inter_block": inter_block,
        "history": flat,
    }


def compose_brief_prompt(*, event: Event, rounds: list[dict], plan_text: str | None,
                         interjections: list[dict], state: dict, output_path: str,
                         attempt: int = 1, kind: str = "auto") -> tuple[str, list[str]]:
    """Assemble the prompt under TOTAL_BUDGET. Returns (prompt, notices)."""
    parts = render_brief_prompt_parts(event=event, rounds=rounds, plan_text=plan_text,
                                      interjections=interjections, state=state,
                                      output_path=output_path, attempt=attempt, kind=kind)
    notices: list[str] = []
    if parts["escalation_clipped"]:
        notices.append("escalation entry abbreviated (head+tail kept)")
    history = parts["history"]
    n = len(history)
    newest_idx = set(range(max(0, n - NEWEST_ENTRIES), n))

    def render_history(older_mode: str, newest_budget: int) -> str:
        lines = [f"=== ROUND HISTORY ({n} entries, oldest first) ==="]
        for i, e in enumerate(history):
            head = _entry_line(e)
            content = e.get("content") or ""
            if i in newest_idx:
                body, _ = _clip(content, newest_budget, NEWEST_ENTRY_MIN_HEAD, NEWEST_ENTRY_MIN_TAIL)
                lines.append(f"{head}\n{body}\n")
            elif older_mode == "short":
                lines.append(f"{head}\n{content[:OLDER_ENTRY_CHARS]}"
                             f"{' …' if len(content) > OLDER_ENTRY_CHARS else ''}\n")
            else:
                lines.append(head)
        return "\n".join(lines) + "\n"

    def assemble(older_mode, newest_budget, plan_block, inter_block, esc):
        return "\n".join(x for x in [
            parts["banner"], parts["path_block"], parts["headings_block"], esc,
            parts["state_block"], plan_block, inter_block,
            render_history(older_mode, newest_budget),
            ("=== NOTICES ===\n" + "\n".join(notices) + "\n") if notices else "",
        ] if x)

    esc = parts["escalation"]
    plan_block, inter_block = parts["plan_block"], parts["inter_block"]
    prompt = assemble("short", NEWEST_ENTRY_BUDGET, plan_block, inter_block, esc)
    if len(prompt) > TOTAL_BUDGET and n > NEWEST_ENTRIES:
        notices.append(f"{n - len(newest_idx)} older entries abbreviated to header lines — "
                       f"read `tagteam cycle rounds --phase {event.phase} --type {event.type}` for the full text")
        prompt = assemble("header", NEWEST_ENTRY_BUDGET, plan_block, inter_block, esc)
    if len(prompt) > TOTAL_BUDGET and plan_block:
        notices.append(f"plan omitted — read docs/phases/{event.phase}.md")
        plan_block = f"=== PHASE PLAN === omitted for size; read docs/phases/{event.phase}.md\n"
        prompt = assemble("header", NEWEST_ENTRY_BUDGET, plan_block, inter_block, esc)
    if len(prompt) > TOTAL_BUDGET and inter_block:
        notices.append("interjections omitted — run `tagteam interject --list`")
        inter_block = "=== ARBITER INTERJECTIONS === omitted for size; run `tagteam interject --list`\n"
        prompt = assemble("header", NEWEST_ENTRY_BUDGET, plan_block, inter_block, esc)
    if len(prompt) > TOTAL_BUDGET:
        notices.append("newest entries reduced to head+tail")
        prompt = assemble("header", NEWEST_ENTRY_MIN_HEAD + NEWEST_ENTRY_MIN_TAIL + 60,
                          plan_block, inter_block, esc)
    if len(prompt) > TOTAL_BUDGET:
        notices.append("escalation entry reduced to minimum head+tail")
        body, _ = _clip(event.content, ESCALATION_MIN_HEAD + ESCALATION_MIN_TAIL + 60,
                        ESCALATION_MIN_HEAD, ESCALATION_MIN_TAIL)
        esc = (f"=== ESCALATION ENTRY ({event.action} by {event.role} at round "
               f"{event.round}, {event.ts}) ===\n{body}\n")
        prompt = assemble("header", NEWEST_ENTRY_MIN_HEAD + NEWEST_ENTRY_MIN_TAIL + 60,
                          plan_block, inter_block, esc)
    if len(prompt) > TOTAL_BUDGET:
        # Final deterministic clamp: truncate from the end. Banner, output
        # path, headings and the (minimal) escalation entry come first, so
        # they survive.
        notices.append("prompt clamped to the total budget")
        keep = TOTAL_BUDGET - 200
        prompt = prompt[:keep] + "\n[… prompt clamped to size budget …]\n=== NOTICES ===\n" + "\n".join(notices) + "\n"
        prompt = prompt[:TOTAL_BUDGET]
    return prompt, notices


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

@dataclass
class BriefSpec:
    enabled: bool
    provider: str | None
    executable: str | None
    argv: list[str] | None
    timeout_s: float
    problems: list[str] = field(default_factory=list)


def resolve_briefer(config: dict | None, project_root: str | Path) -> BriefSpec:
    """Validate + resolve the briefer for a run. Never raises: problems are
    returned so the watcher can warn and disable."""
    config = config or {}
    try:
        problems = validate_briefer_config(config)
        spec = get_briefer_spec(config)
    except Exception as e:   # contract: never raise
        return BriefSpec(False, None, None, None, BRIEFER_DEFAULT_TIMEOUT_MINUTES * 60.0,
                         [f"briefer config unreadable: {e}"])
    # The validated/fallback timeout is carried on EVERY return, so callers
    # (e.g. `tagteam brief`'s abandoned sweep) never see 0.0 for a disabled
    # or invalid block — that would abandon a live attempt after the grace.
    timeout_s = float(spec["timeout_minutes"]) * 60
    if not spec["enabled"]:
        return BriefSpec(False, spec["provider"], None, None, timeout_s, problems)
    if problems:
        return BriefSpec(False, spec["provider"], None, None, timeout_s, problems)
    provider = spec["provider"]
    if provider not in h.ADAPTERS:
        return BriefSpec(False, provider, None, None, timeout_s,
                         [f"briefer provider {provider!r} unknown/uninferable — set briefer.provider"])
    try:
        exe = h.resolve_executable(provider, spec["executable"])
        argv = h.build_argv(h.ADAPTERS[provider], exe, spec["args"], project_root)
    except h.HeadlessConfigError as e:
        return BriefSpec(False, provider, None, None, timeout_s, [f"briefer: {e}"])
    return BriefSpec(True, provider, exe, argv, timeout_s, [])


@dataclass
class BriefResult:
    status: str                # ok | partial | failed | refused | skipped
    reason: str
    brief_id: int | None = None
    attempt: int | None = None
    path: str | None = None
    stem: str | None = None
    duration_ms: int | None = None
    event_key: str | None = None


def escalations_dir(project_root: str | Path) -> Path:
    return Path(project_root) / ESCALATIONS_RELDIR


def brief_path(project_root: str | Path, ev: Event, attempt: int) -> Path:
    return escalations_dir(project_root) / f"{ev.phase}_{ev.type}_r{ev.round}_{ev.stamp}-a{attempt}.md"


def alias_path(project_root: str | Path, phase: str, ctype: str) -> Path:
    return escalations_dir(project_root) / f"{phase}_{ctype}_latest.md"


def _write_alias(project_root, ev: Event, content: str) -> None:
    p = alias_path(project_root, ev.phase, ev.type)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def write_alias_stub(project_root, ev: Event, previous_path: str | None) -> None:
    body = (f"<!-- tagteam brief alias | event: {ev.event_key} | status: none -->\n"
            f"# No brief yet for the current escalation event\n\n"
            f"Event: `{ev.event_key}`\n\n"
            f"Run `tagteam brief` for the current state, or `tagteam brief --generate` "
            f"to (re)try.\n\n"
            + (f"Previous brief (older event): `{previous_path}`\n" if previous_path else ""))
    _write_alias(project_root, ev, body)


def _inflight_live(inflight: dict) -> bool:
    """A pointer is live if its child or its runner binds to a live process."""
    pid = inflight.get("pid")
    if isinstance(pid, int) and pid > 0 and procs.pid_alive(pid):
        ident = procs.identity(pid)
        if inflight.get("child_ident") and ident == inflight.get("child_ident"):
            return True
    rpid = inflight.get("watcher_pid")
    if isinstance(rpid, int) and rpid > 0 and procs.pid_alive(rpid):
        ident = procs.identity(rpid)
        if inflight.get("watcher_ident") and ident == inflight.get("watcher_ident"):
            return True
    return False


def verify_brief_file(path: Path) -> tuple[str, str]:
    """(status, reason) for the written file: ok / partial / failed."""
    if not path.exists():
        return "failed", "brief file was not written"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return "failed", f"cannot read brief file: {e}"
    if not text.strip():
        return "failed", "brief file is empty"
    missing = [hd for hd in BRIEF_HEADINGS if hd not in text]
    if missing:
        return "partial", f"missing headings: {', '.join(missing)}"
    return "ok", "brief written with all required sections"


def sweep_abandoned(project_root: str | Path, timeout_s: float,
                    log: Callable[[str], None] | None = None) -> list[int]:
    """Mark running rows whose runner is dead / mismatched, or hung past
    timeout+grace with no inflight for their stem, as abandoned."""
    from tagteam import db
    root = str(project_root)
    marked: list[int] = []
    try:
        conn = db.connect(project_dir=root)
    except Exception:
        return marked
    try:
        rows = db.running_briefs(conn)
        inflight = h.read_inflight(root)
        for r in rows:
            rpid, rident = r.get("runner_pid"), r.get("runner_ident")
            reason = None
            alive = isinstance(rpid, int) and rpid > 0 and procs.pid_alive(rpid)
            if not alive or (rident and procs.identity(rpid) != rident):
                reason = "runner process gone or identity mismatch"
            else:
                try:
                    started = datetime.fromisoformat(r.get("started_at") or r.get("ts"))
                except Exception:
                    started = datetime.now(timezone.utc)
                if datetime.now(timezone.utc) - started > timedelta(seconds=timeout_s) + ABANDON_GRACE:
                    if not (inflight and inflight.get("stem") == r.get("stem")):
                        reason = "runner alive but attempt hung past timeout+grace with no inflight"
            if reason and db.mark_brief_abandoned(conn, r["id"], ts=_now_iso(), reason=reason):
                marked.append(r["id"])
                if log:
                    log(f"   briefer: attempt #{r['id']} marked abandoned ({reason}); "
                        f"retry with `tagteam brief --generate`")
    finally:
        conn.close()
    return marked


def run_briefer(project_root: str | Path, *, kind: str, spec: BriefSpec,
                config: dict | None = None, by: str | None = None,
                log: Callable[[str], None] | None = None,
                notify: Callable[[str, str], None] | None = None,
                skill_unused: None = None) -> BriefResult:
    """Claim → inflight → spawn → verify → finish for the current event.

    The event is resolved and re-validated INSIDE the writer-lock critical
    section that performs the claim ("latest entry at claim time"). Every
    exception after the claim finalizes the claim row as failed and removes
    the inflight pointer; only a hard runner crash leaves them behind for
    `sweep_abandoned`.
    """
    from tagteam import db, dualwrite
    log = log or (lambda m: None)
    notify = notify or (lambda t, m: None)
    root = str(Path(project_root).resolve())

    if not spec.enabled:
        return BriefResult("skipped", "briefer disabled")
    if dualwrite.is_db_invalid(root):
        log("   briefer skipped: DB invalid — run `tagteam state repair-db`")
        return BriefResult("skipped", "db_invalid")

    runner_pid = os.getpid()
    runner_ident = procs.identity(runner_pid)
    with dualwrite.writer_lock(root):
        # Resolve the escalation event under the lock: a concurrent `rule`
        # / rearm cannot change it between resolution and claim.
        ev, why = current_event(root)
        if ev is None:
            return BriefResult("skipped", why)
        sweep_abandoned(root, spec.timeout_s, log)
        # Phase 37: the slot rule is shared with every spawner (fail closed);
        # a stale marker is recovered by the claim itself, never unlinked here.
        slot = h.slot_status(root)
        if slot["held"]:
            inflight = slot["marker"]
            msg = (f"another turn is in flight ({inflight.get('stem')}; {slot['reason']}) "
                   f"— wait or `tagteam cancel-turn`")
            log(f"   briefer: claim refused: {msg}")
            return BriefResult("refused", msg, event_key=ev.event_key)
        conn = db.connect(project_dir=root)
        try:
            claim = db.claim_brief(conn, ts=_now_iso(), phase=ev.phase, cycle_type=ev.type,
                                   round_=ev.round, cycle_state=ev.cycle_state,
                                   event_key=ev.event_key, kind=kind, runner_pid=runner_pid,
                                   runner_ident=runner_ident, event_row_id=ev.event_row_id,
                                   provider=spec.provider)
            prior = db.successful_brief_for_event(conn, ev.event_key)
            history_prev = [r for r in db.brief_history(conn, ev.phase, ev.type)
                            if r["status"] in ("ok", "partial") and r["event_key"] != ev.event_key]
        finally:
            conn.close()
        if claim is None:
            if prior is not None:
                return BriefResult("skipped", f"event already briefed (#{prior['id']} {prior['path']})",
                                   brief_id=prior["id"], path=prior["path"], event_key=ev.event_key)
            try:
                alias = alias_path(root, ev.phase, ev.type)
                if not (alias.exists() and ev.event_key in alias.read_text(encoding="utf-8", errors="replace")):
                    write_alias_stub(root, ev, history_prev[0]["path"] if history_prev else None)
            except OSError as e:
                log(f"   briefer: alias stub failed: {e}")
            return BriefResult("refused", "an attempt is running or the automatic attempt "
                               "already ran for this event — `tagteam brief --generate` to retry",
                               event_key=ev.event_key)
        brief_id, attempt = claim
        stem = f"{ev.phase}_{ev.type}_r{ev.round}_briefer_{_stamp()}_a{attempt}"
        # From here on, every failure must finalize the claim.
        try:
            conn = db.connect(project_dir=root)
            try:
                db.set_brief_stem(conn, brief_id, stem)
            finally:
                conn.close()
            out_path = brief_path(root, ev, attempt)
            d = h.turns_dir(root); d.mkdir(parents=True, exist_ok=True)
            log_path, events_path = d / f"{stem}.log", d / f"{stem}.events.jsonl"
            started_at = _now_iso()
            inflight = {
                "phase": ev.phase, "type": ev.type, "round": ev.round, "role": "briefer",
                "agent": "briefer", "provider": spec.provider, "stem": stem,
                "log_path": str(log_path), "events_path": str(events_path),
                "started_at": started_at, "pid": None, "child_ident": None,
                "watcher_pid": runner_pid, "watcher_ident": runner_ident,
                "brief_id": brief_id, "event_key": ev.event_key, "brief_kind": kind, "attempt": attempt,
            }
            # Phase 37: atomic slot claim (kind=briefer); a live cycle or
            # conversation turn makes the attempt fail cleanly, never a
            # clobbered marker.
            try:
                slot_claim = h.claim_turn_slot(root, kind=h.SLOT_KIND_BRIEFER, role="briefer",
                                               fields=inflight)
            except h.SlotBusy as busy:
                raise RuntimeError(f"turn slot busy: {busy.reason}")
        except Exception as e:
            _finalize_failed(root, brief_id, stem, f"setup failed: {type(e).__name__}: {e}",
                             ev, kind, attempt, log)
            return BriefResult("failed", f"setup failed: {e}", brief_id=brief_id, attempt=attempt,
                               stem=stem, event_key=ev.event_key)

    # ---- compose (outside the lock) — still finalize on any error
    try:
        from tagteam.cycle import tail_rounds
        from tagteam.state import read_state
        try:
            rounds = tail_rounds(ev.phase, ev.type, None, root)
        except Exception:
            rounds = []
        plan_file = Path(root) / "docs" / "phases" / f"{ev.phase}.md"
        plan_text = plan_file.read_text(encoding="utf-8", errors="replace") if plan_file.exists() else None
        inter: list[dict] = []
        try:
            conn = db.connect(project_dir=root)
            try:
                inter = list(db.get_interjections(conn, phase=ev.phase, cycle_type=ev.type,
                                                  undelivered_only=True, include_retired=False))
            finally:
                conn.close()
        except Exception:
            inter = []
        prompt, notices = compose_brief_prompt(event=ev, rounds=rounds, plan_text=plan_text,
                                               interjections=inter, state=read_state(root) or {},
                                               output_path=str(out_path), attempt=attempt, kind=kind)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        _finalize_failed(root, brief_id, stem, f"compose failed: {type(e).__name__}: {e}",
                         ev, kind, attempt, log)
        return BriefResult("failed", f"compose failed: {e}", brief_id=brief_id, attempt=attempt,
                           stem=stem, event_key=ev.event_key)

    def _on_spawn(pid: int) -> None:
        h.update_turn_slot(slot_claim, pid=pid, child_ident=procs.identity(pid))

    env = dict(os.environ)
    for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(k, None)
    # Phase 50: the child writes only its brief file; claim/finish bookkeeping
    # is parent-side, so the child runs read-only.
    from tagteam.dualwrite import READ_ONLY_ENV
    env[READ_ONLY_ENV] = "1"
    log(f"   briefer: spawning {spec.provider} for event {ev.event_key} (attempt {attempt}, "
        f"{kind}) — log: {log_path}")
    spawn_error = None
    try:
        out = h.run_process(spec.argv, prompt, root, events_path=events_path, log_path=log_path,
                            provider=spec.provider, timeout_s=spec.timeout_s,
                            on_spawn=_on_spawn, env=env)
    except h.SpawnError as e:
        spawn_error = str(e)
        out = h.RunOutput(exit_code=None, timed_out=False, duration_ms=0)
    except BaseException as e:   # KeyboardInterrupt / unexpected: finalize, re-raise
        h.release_turn_slot(slot_claim)
        _finalize_failed(root, brief_id, stem, f"runner interrupted: {type(e).__name__}",
                         ev, kind, attempt, log)
        raise
    finally:
        h.release_turn_slot(slot_claim)

    # ---- finalize (any exception here → failed row, never a stranded claim)
    try:
        cancel = h.read_cancel(root)
        cancelled_by = None
        if cancel is not None and cancel.get("stem") == stem:
            h.clear_cancel(root)
            cancelled_by = cancel.get("by") or "arbiter"
        if cancelled_by:
            status, reason, usage_status = "failed", f"cancelled by {cancelled_by}", "cancelled"
        elif spawn_error:
            status, reason, usage_status = "failed", f"could not start {spec.provider}: {spawn_error}", "spawn_failed"
        elif out.timed_out:
            status, reason, usage_status = "failed", f"briefer exceeded {spec.timeout_s/60:.0f} min timeout", "timeout"
        elif out.exit_code != 0:
            status, reason, usage_status = "failed", f"{spec.provider} exited {out.exit_code}", "nonzero_exit"
        else:
            status, reason = verify_brief_file(out_path)
            usage_status = "ok" if status in ("ok", "partial") else "no_round"
        try:
            lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        usage = h.parse_usage(spec.provider, lines) or {}
        h.record_rate_limits(root, spec.provider, lines, log=log)
        usage_row_id = None
        content = None
        conn = db.connect(project_dir=root)
        try:
            try:
                usage_row_id = db.add_usage(conn, ts=_now_iso(), phase=ev.phase, type=ev.type,
                                            round=ev.round, role="briefer", agent="briefer",
                                            provider=spec.provider, status=usage_status,
                                            exit_code=out.exit_code, duration_ms=out.duration_ms,
                                            log_path=str(log_path),
                                            **{k: usage.get(k) for k in ("model", "input_tokens",
                                               "output_tokens", "cache_read_tokens",
                                               "cache_write_tokens", "cost_usd", "num_turns",
                                               "session_id", "model_usage_json")})
            except Exception as e:
                log(f"   briefer: usage row failed: {e}")
            if status in ("ok", "partial") and out_path.exists():
                content = out_path.read_text(encoding="utf-8", errors="replace")
            db.finish_brief(conn, brief_id, status=status, ts=_now_iso(), stem=stem,
                            path=str(out_path) if out_path.exists() else None, content=content,
                            model=usage.get("model"), usage_row_id=usage_row_id,
                            duration_ms=out.duration_ms, reason=reason)
            if status == "failed":
                db.add_diagnostic(conn, "briefer_failed", {
                    "brief_id": brief_id, "event_key": ev.event_key, "phase": ev.phase,
                    "type": ev.type, "round": ev.round, "kind": kind, "attempt": attempt,
                    "reason": reason, "log_path": str(log_path)}, _now_iso())
                conn.commit()
        finally:
            conn.close()
    except Exception as e:
        _finalize_failed(root, brief_id, stem, f"finalize failed: {type(e).__name__}: {e}",
                         ev, kind, attempt, log)
        return BriefResult("failed", f"finalize failed: {e}", brief_id=brief_id, attempt=attempt,
                           stem=stem, event_key=ev.event_key)

    # ---- alias + notify (best-effort; never changes the recorded status)
    try:
        with open(log_path, "ab") as f:
            f.write(f"[tagteam] brief {status}: {reason}\n".encode("utf-8", "replace"))
        if status in ("ok", "partial") and content:
            _write_alias(root, ev, content)
            log(f"   briefer: brief {status} — {out_path}" + (f" ({reason})" if status == "partial" else ""))
            notify("Tagteam", f"Escalation brief ready: {out_path.name}")
        else:
            prev = None
            conn = db.connect(project_dir=root)
            try:
                hist = [r for r in db.brief_history(conn, ev.phase, ev.type)
                        if r["status"] in ("ok", "partial") and r["event_key"] != ev.event_key]
                prev = hist[0]["path"] if hist else None
            finally:
                conn.close()
            write_alias_stub(root, ev, prev)
            log(f"   briefer: brief failed — {reason}; run `tagteam brief --generate` to retry")
            notify("Tagteam", f"Escalation brief failed: {reason}")
    except Exception as e:
        log(f"   briefer: post-processing (alias/notify) failed: {e}")
    return BriefResult(status, reason, brief_id=brief_id, attempt=attempt,
                       path=str(out_path) if out_path.exists() else None, stem=stem,
                       duration_ms=out.duration_ms, event_key=ev.event_key)


def _finalize_failed(root: str, brief_id: int, stem: str, reason: str, ev: Event,
                     kind: str, attempt: int, log: Callable[[str], None]) -> None:
    """Best-effort: mark the claim failed, write a diagnostic, drop the pointer."""
    from tagteam import db
    try:
        p = h.inflight_path(root)
        if p.exists():
            try:
                cur = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                cur = {}
            if cur.get("stem") in (None, stem):
                p.unlink()
    except OSError:
        pass
    try:
        conn = db.connect(project_dir=root)
        try:
            db.finish_brief(conn, brief_id, status="failed", ts=_now_iso(), stem=stem,
                            reason=reason)
            db.add_diagnostic(conn, "briefer_failed", {
                "brief_id": brief_id, "event_key": ev.event_key, "phase": ev.phase,
                "type": ev.type, "round": ev.round, "kind": kind, "attempt": attempt,
                "reason": reason}, _now_iso())
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log(f"   briefer: could not finalize failed claim #{brief_id}: {e}")
    log(f"   briefer: attempt #{brief_id} failed — {reason}")


# ---------------------------------------------------------------------------
# `tagteam brief`
# ---------------------------------------------------------------------------

def brief_command(args: list[str], project_root: str | Path | None = None, out=None) -> int:
    from tagteam import db
    from tagteam.config import read_config
    from tagteam.state import read_state
    out = out or sys.stdout
    phase = ctype = event_key = None
    do_list = as_json = generate = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--phase" and i + 1 < len(args):
            phase = args[i + 1]; i += 2
        elif a == "--type" and i + 1 < len(args):
            ctype = args[i + 1]; i += 2
        elif a == "--event" and i + 1 < len(args):
            event_key = args[i + 1]; i += 2
        elif a == "--list":
            do_list = True; i += 1
        elif a == "--json":
            as_json = True; i += 1
        elif a == "--generate":
            generate = True; i += 1
        elif a in ("-h", "--help"):
            print("Usage: tagteam brief [--phase P --type T] [--list] [--json] [--event KEY] [--generate]", file=out)
            print("  Show the decision brief for the CURRENT escalation event; --list for history;", file=out)
            print("  --generate to run the briefer now (manual attempt).", file=out)
            return 0
        else:
            print(f"Unknown argument: {a}", file=out); return 1
    if project_root is None:
        from tagteam.state import _resolve_project_root
        project_root = _resolve_project_root()
    root = str(project_root)
    st = read_state(root) or {}
    phase = phase or st.get("phase")
    ctype = ctype or st.get("type")
    if not phase or not ctype:
        print("No cycle selected (use --phase/--type).", file=out); return 1

    if generate:
        cfg = read_config(Path(root) / "tagteam.yaml") or {}
        spec = resolve_briefer(cfg, root)
        if not spec.enabled:
            probs = "; ".join(spec.problems) or "set `briefer.enabled: true` in tagteam.yaml"
            print(f"Briefer is not enabled: {probs}", file=out); return 1
        res = run_briefer(root, kind="manual", spec=spec, config=cfg,
                          log=lambda m: print(m, file=out),
                          notify=lambda t, m: None)
        print(f"{res.status}: {res.reason}" + (f" → {res.path}" if res.path else ""), file=out)
        return 0 if res.status in ("ok", "partial") else 1

    from tagteam.dualwrite import DatabaseMissing
    try:
        conn = db.connect(project_dir=root)
    except DatabaseMissing:
        # Phase 50: read-only and no DB — no briefs exist.
        if as_json:
            print("[]" if do_list else "null", file=out); return 0
        print(f"No briefs for {phase}/{ctype}." if do_list else "No brief for the current event.",
              file=out)
        return 1
    try:
        if do_list:
            rows = db.brief_history(conn, phase, ctype)
            if as_json:
                print(json.dumps(rows, indent=2), file=out); return 0
            if not rows:
                print(f"No briefs for {phase}/{ctype}.", file=out); return 1
            for r in rows:
                print(f"#{r['id']} [{r['ts']}] {r['kind']} a{r['attempt']} {r['status']:<9} "
                      f"event={r['event_key']} path={r['path'] or '-'}", file=out)
            return 0
        if event_key:
            row = db.successful_brief_for_event(conn, event_key)
            if row is None:
                rows = db.briefs_for_event(conn, event_key)
                print(f"No successful brief for event {event_key}"
                      + (f" (attempts: {', '.join(r['status'] for r in rows)})" if rows else ""), file=out)
                return 1
        else:
            ev, why = event_for_cycle(root, phase, ctype)
            if ev is None:
                print(f"No current escalation: {why}", file=out); return 1
            row = db.successful_brief_for_event(conn, ev.event_key)
            if row is None:
                cfg = read_config(Path(root) / "tagteam.yaml") or {}
                sweep_abandoned(root, resolve_briefer(cfg, root).timeout_s)
                rows = db.briefs_for_event(conn, ev.event_key)
                state_txt = ", ".join(f"a{r['attempt']} {r['status']}" for r in rows) or "no attempt yet"
                print(f"No brief yet for the current event {ev.event_key} ({state_txt}). "
                      f"Run `tagteam brief --generate` to (re)try. Older events: `tagteam brief --list`.",
                      file=out)
                return 1
        if as_json:
            print(json.dumps(row, indent=2), file=out); return 0
        print(f"# Brief #{row['id']} ({row['kind']} a{row['attempt']}, {row['status']}) — {row['path']}",
              file=out)
        print(f"event: {row['event_key']}", file=out)
        print("", file=out)
        print(row.get("content") or "(no content stored)", file=out)
        return 0
    finally:
        conn.close()
