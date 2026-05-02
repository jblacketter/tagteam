"""
State file management for handoff orchestration.

Provides atomic read/write of handoff-state.json, which tracks
whose turn it is and what command the next agent should run.
"""

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = "handoff-state.json"
STATE_TMP = ".handoff-state.tmp"
DIAGNOSTICS_LOG = "handoff-diagnostics.jsonl"

VALID_STATUSES = {"ready", "working", "done", "escalated", "aborted"}
VALID_TURNS = {"lead", "reviewer"}
VALID_RUN_MODES = {"single-phase", "full-roadmap"}

# Cache the resolved project root so we only shell out once per process.
_cached_project_root: str | None = None
_warned_outer: bool = False


def _resolve_project_root() -> str:
    """Resolve the tagteam project root.

    Discovery order:
      1. Walk up from cwd looking for the nearest `tagteam.yaml`.
      2. Fall back to `git rev-parse --show-toplevel`.
      3. Fall back to cwd (".").

    The walk-up rule fixes the silent nested-project-write bug from
    docs/handoff-cycle-issues-2026-04-24.md (Issue #1): a nested git repo
    that shadows the outer tagteam project would otherwise capture all
    cycle/state writes via `git rev-parse`.

    If a parent of the resolved root also contains `tagteam.yaml`, a
    one-time warning is emitted to stderr.
    """
    import sys
    global _cached_project_root
    if _cached_project_root is not None:
        return _cached_project_root

    cwd = Path.cwd().resolve()
    found: Path | None = None
    for ancestor in [cwd, *cwd.parents]:
        if (ancestor / "tagteam.yaml").exists():
            found = ancestor
            break

    if found is not None:
        _warn_outer_tagteam(found)
        _cached_project_root = str(found)
        return _cached_project_root

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            _cached_project_root = result.stdout.strip()
            return _cached_project_root
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    _cached_project_root = "."
    return _cached_project_root


def _warn_outer_tagteam(root: Path) -> None:
    """If any ancestor of `root` also has tagteam.yaml, warn once to stderr."""
    import sys
    global _warned_outer
    if _warned_outer:
        return
    for ancestor in root.parents:
        if (ancestor / "tagteam.yaml").exists():
            print(
                f"[tagteam] warning: resolved project root {root} is nested "
                f"inside another tagteam project at {ancestor}.\n"
                f"          Cycle/state writes will go to {root}. "
                f"cd to {ancestor} if that is wrong.",
                file=sys.stderr,
            )
            _warned_outer = True
            return


def normalize_phase_key(phase: str) -> str:
    """Normalize phase name to slug format for roadmap operations.

    Extracts the slug portion from 'phase-N-slug' format.
    Returns input unchanged if it doesn't match the pattern.

    This ensures consistent comparison and storage in roadmap queues,
    which use short slug format.

    Examples:
        'phase-9-security-compliance-automation' -> 'security-compliance-automation'
        'security-compliance-automation' -> 'security-compliance-automation'
        'custom-phase' -> 'custom-phase'

    Args:
        phase: Phase name in any format

    Returns:
        Normalized slug for roadmap operations
    """
    if not phase:
        return phase
    match = re.match(r'^phase-\d+-(.+)$', phase)
    if match:
        return match.group(1)
    return phase


def get_state_path(project_dir: str | None = None) -> Path:
    if project_dir is None:
        project_dir = _resolve_project_root()
    return Path(project_dir) / STATE_FILE


def read_state(project_dir: str | None = None) -> dict | None:
    """Read the current state from handoff-state.json. Returns None if missing."""
    path = get_state_path(project_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_state(state: dict, project_dir: str | None = None) -> None:
    """Atomic write: write to temp file, then rename.

    Phase 28 Step A: also acquires the project writer lock and
    mirrors the state to the shadow DB. The DB write is skipped when
    called from inside `update_state` (via `skip_inner_dualwrite`)
    so the outer `update_state` owns the state-history append.
    """
    from tagteam import dualwrite

    if project_dir is None:
        project_dir = _resolve_project_root()
    path = get_state_path(project_dir)
    tmp_path = Path(project_dir) / STATE_TMP

    state["updated_at"] = datetime.now(timezone.utc).isoformat()

    with dualwrite.writer_lock(project_dir):
        tmp_path.write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
        tmp_path.replace(path)

        # Inner-call short-circuit: when update_state calls write_state,
        # update_state will mirror state + history afterwards. write_state's
        # own dual-write would otherwise double-write state.
        if not dualwrite.should_skip_inner_dualwrite():
            # Direct callers of write_state bypass history-tracking
            # (it lives in update_state), so the DB side does the same.
            _shadow_db_write_state(state, project_dir, history_entry=None)


def update_state(updates: dict, project_dir: str | None = None,
                 expected_seq: int | None = None,
                 clear_keys: list[str] | None = None,
                 replace: bool = False) -> dict | None:
    """Read current state, record history, apply updates, write back.

    If expected_seq is provided, only write if the current sequence number
    matches. Returns None if the write was skipped due to staleness.

    If clear_keys is provided, those keys will be deleted from the state
    before applying updates.

    If replace is True, the new state consists of exactly `updates` plus
    seq, history, and updated_at. No fields from the previous state carry
    forward. Use this when rewriting state authoritatively (e.g. `state sync`)
    so stale fields cannot leak via shallow merge.

    Phase 28 Step A: holds the project writer lock for the whole
    operation, mirrors state + (one) history entry to the shadow DB
    after the file write succeeds. The inner `write_state` call's
    own dual-write hook is short-circuited via `skip_inner_dualwrite`
    so we don't double-write state.
    """
    from tagteam import dualwrite

    if project_dir is None:
        project_dir = _resolve_project_root()

    with dualwrite.writer_lock(project_dir):
        state = read_state(project_dir) or {}

        current_seq = state.get("seq", 0)
        if expected_seq is not None and current_seq != expected_seq:
            _log_seq_mismatch(expected_seq, current_seq,
                              updates.get("updated_by", "unknown"),
                              project_dir)
            return None

        history = state.get("history", [])
        prev = {
            "turn": state.get("turn"),
            "status": state.get("status"),
            "timestamp": state.get("updated_at"),
            "phase": state.get("phase"),
            "round": state.get("round"),
            "updated_by": state.get("updated_by"),
        }
        history_entry_added = any(v is not None for v in prev.values())
        if history_entry_added:
            history.append(prev)
        history = history[-20:]

        if replace:
            new_state = dict(updates)
            new_state["history"] = history
            new_state["seq"] = current_seq + 1
            with dualwrite.skip_inner_dualwrite():
                write_state(new_state, project_dir)
            _shadow_db_write_state(
                new_state, project_dir,
                history_entry=prev if history_entry_added else None,
            )
            return new_state

        state["history"] = history
        if clear_keys:
            for key in clear_keys:
                state.pop(key, None)
        state["seq"] = current_seq + 1
        state.update(updates)
        with dualwrite.skip_inner_dualwrite():
            write_state(state, project_dir)
        _shadow_db_write_state(
            state, project_dir,
            history_entry=prev if history_entry_added else None,
        )
        return state


def clear_state(project_dir: str | None = None) -> None:
    """Delete the state file.

    Phase 28 Step A: also removes the singleton state row from the
    shadow DB and records a "cleared" history entry. The flag-file
    `db_invalid` sentinel is NOT cleared by this call — clearing
    state is unrelated to repair status.
    """
    from tagteam import db, dualwrite

    if project_dir is None:
        project_dir = _resolve_project_root()

    with dualwrite.writer_lock(project_dir):
        path = get_state_path(project_dir)
        if path.exists():
            path.unlink()
        tmp_path = Path(project_dir) / STATE_TMP
        if tmp_path.exists():
            tmp_path.unlink()

        conn = None
        try:
            conn = db.connect(project_dir=project_dir)
            conn.execute("DELETE FROM state WHERE id=1")
            db.add_history_entry(conn, {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "turn": None,
                "status": "cleared",
                "phase": None,
                "round": None,
                "updated_by": None,
            })
            conn.commit()
        except Exception as e:
            dualwrite.mark_db_invalid(
                project_dir, reason=f"clear_state dual-write failed: {e}"
            )
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def _log_seq_mismatch(expected: int, actual: int, caller: str,
                      project_dir: str | None = None) -> None:
    """Append seq mismatch event to side-channel diagnostics log.

    Does NOT modify the live state file — no seq bump, no updated_at change.

    Phase 28 Step A: also appends a `seq_mismatch` row to the shadow
    DB's diagnostics table.
    """
    from tagteam import db, dualwrite

    if project_dir is None:
        project_dir = _resolve_project_root()
    log_path = Path(project_dir) / DIAGNOSTICS_LOG
    now_iso = datetime.now(timezone.utc).isoformat()
    entry = {
        "event": "seq_mismatch",
        "expected": expected,
        "actual": actual,
        "caller": caller,
        "timestamp": now_iso,
    }

    with dualwrite.writer_lock(project_dir):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        conn = None
        try:
            conn = db.connect(project_dir=project_dir)
            db.add_diagnostic(
                conn,
                kind="seq_mismatch",
                payload={
                    "expected": expected,
                    "actual": actual,
                    "caller": caller,
                },
                ts=now_iso,
            )
            conn.commit()
        except Exception as e:
            dualwrite.mark_db_invalid(
                project_dir,
                reason=f"_log_seq_mismatch dual-write failed: {e}",
            )
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def _read_diagnostics_log(project_dir: str | None = None) -> list[dict]:
    """Read all entries from the diagnostics side-channel log."""
    if project_dir is None:
        project_dir = _resolve_project_root()
    log_path = Path(project_dir) / DIAGNOSTICS_LOG
    if not log_path.exists():
        return []
    entries = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def clear_diagnostics_log(project_dir: str | None = None) -> None:
    """Truncate the diagnostics side-channel log.

    Phase 28 Step A: also clears the shadow DB's diagnostics table.
    """
    from tagteam import db, dualwrite

    if project_dir is None:
        project_dir = _resolve_project_root()
    log_path = Path(project_dir) / DIAGNOSTICS_LOG

    with dualwrite.writer_lock(project_dir):
        if log_path.exists():
            log_path.write_text("", encoding="utf-8")

        conn = None
        try:
            conn = db.connect(project_dir=project_dir)
            conn.execute("DELETE FROM diagnostics")
            conn.commit()
        except Exception as e:
            dualwrite.mark_db_invalid(
                project_dir,
                reason=f"clear_diagnostics_log dual-write failed: {e}",
            )
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def _check_agent_health(lines: list[str], project_dir: str) -> None:
    """Check agent responsiveness via session discovery."""
    # Try iTerm first
    try:
        from tagteam.iterm import (
            _read_session_file, get_session_contents, session_id_is_valid,
        )
        from tagteam.watcher import _check_idle_patterns

        session_data = _read_session_file(project_dir)
        if session_data:
            tabs = session_data.get("tabs", {})
            for role in ("lead", "reviewer"):
                sid = tabs.get(role, {}).get("session_id")
                if not sid:
                    lines.append(f"  {role}: no session ID in session file")
                    continue
                if not session_id_is_valid(sid):
                    lines.append(f"  {role}: session {sid} not found"
                                 " (tab may be closed)")
                    continue
                content = get_session_contents(sid, last_n_lines=5)
                if not content:
                    lines.append(f"  {role}: no terminal output")
                elif _check_idle_patterns(content):
                    lines.append(f"  {role}: IDLE (at prompt)")
                else:
                    lines.append(f"  {role}: BUSY or unknown state")
            return
    except ImportError:
        pass

    # Try tmux
    try:
        from tagteam.session import session_exists, SESSION_NAME
        from tagteam.watcher import pane_exists, is_agent_idle

        if session_exists():
            for role, pane in [("lead", f"{SESSION_NAME}:0.0"),
                               ("reviewer", f"{SESSION_NAME}:0.2")]:
                if not pane_exists(pane):
                    lines.append(f"  {role}: pane {pane} not found")
                elif is_agent_idle(pane):
                    lines.append(f"  {role}: IDLE (at prompt)")
                else:
                    lines.append(f"  {role}: BUSY or unknown state")
            return
    except ImportError:
        pass

    lines.append("  No session found — cannot check agent health.")


def diagnose_state(project_dir: str | None = None,
                   check_agents: bool = False) -> str:
    """Analyze handoff state and produce a diagnostic report.

    Checks for: stuck-in-ready, stale completion metadata,
    cycle-state sync mismatches, history anomalies, seq mismatch log,
    and optionally agent responsiveness.
    """
    if project_dir is None:
        project_dir = _resolve_project_root()

    lines = []
    lines.append("Handoff Diagnostic Report")
    lines.append("=" * 25)

    state = read_state(project_dir)
    if state is None:
        lines.append("[INFO] No handoff-state.json found — no active handoff.")
        return "\n".join(lines)

    phase = state.get("phase", "?")
    stype = state.get("type", "?")
    turn = state.get("turn", "?")
    status = state.get("status", "?")
    rnd = state.get("round", "?")
    seq = state.get("seq", 0)
    result = state.get("result")
    updated_at = state.get("updated_at")

    lines.append(f"Phase: {phase} | Type: {stype} | Round: {rnd}"
                 f" | Turn: {turn} | Status: {status}")
    lines.append("")

    # Check 1: State file readable
    lines.append("[OK]  State file readable")
    lines.append(f"[OK]  Seq: {seq}")

    # Check 2: Stuck in ready
    if status == "ready" and updated_at:
        try:
            updated_dt = datetime.fromisoformat(updated_at)
            now = datetime.now(timezone.utc)
            age_seconds = (now - updated_dt).total_seconds()
            age_minutes = age_seconds / 60

            if age_minutes > 30:
                lines.append(f"[FAIL] State has been 'ready' for"
                             f" {int(age_minutes)} minutes — agent is"
                             " likely unresponsive")
            elif age_minutes > 5:
                lines.append(f"[WARN] State has been 'ready' for"
                             f" {int(age_minutes)} minutes — agent may"
                             " have missed command")
            else:
                lines.append(f"[OK]  State age: {int(age_seconds)}s"
                             " (recent)")
        except (ValueError, TypeError):
            lines.append("[WARN] Could not parse updated_at timestamp")

    # Check 3: Stale completion metadata
    if status != "done" and result:
        lines.append(f"[WARN] Stale metadata: result=\"{result}\""
                     f" persists but status is \"{status}\"")
        lines.append("       Fix: python -m tagteam state set"
                     f" --result \"\" --phase {phase}")
    elif status == "done" and result:
        lines.append(f"[OK]  Result: {result} (status is done — expected)")

    # Check 4: Cycle-state sync
    try:
        from tagteam.cycle import read_status as cycle_read_status
        cycle_status = cycle_read_status(phase, stype, project_dir)
        if cycle_status:
            cycle_round = cycle_status.get("round")
            cycle_ready_for = cycle_status.get("ready_for")
            mismatches = []
            if cycle_round is not None and cycle_round != rnd:
                mismatches.append(
                    f"round: state={rnd} vs cycle={cycle_round}")
            if cycle_ready_for and cycle_ready_for != turn:
                mismatches.append(
                    f"turn: state={turn} vs cycle ready_for="
                    f"{cycle_ready_for}")
            if mismatches:
                lines.append(f"[WARN] Cycle-state mismatch ({phase}/{stype}):"
                             f" {'; '.join(mismatches)}")
            else:
                lines.append(f"[OK]  Cycle doc ({phase}/{stype}) matches"
                             " state")
        else:
            lines.append(f"[INFO] Cycle doc ({phase}/{stype}) not found"
                         " — cannot verify sync")
    except Exception:
        lines.append("[INFO] Could not read cycle status — skipping sync"
                     " check")

    # Check 5: History anomalies
    history = state.get("history", [])
    if history:
        # Rapid oscillation: 4+ turn changes in last 5 entries
        recent_turns = [h.get("turn") for h in history[-5:] if h.get("turn")]
        oscillations = sum(1 for i in range(1, len(recent_turns))
                          if recent_turns[i] != recent_turns[i - 1])
        if oscillations >= 4:
            lines.append("[WARN] Rapid turn oscillation detected in"
                         " recent history (4+ switches in 5 entries)")

        # Repeated escalations
        escalation_count = sum(1 for h in history
                               if h.get("status") == "escalated")
        if escalation_count >= 2:
            lines.append(f"[WARN] {escalation_count} escalation(s) in"
                         " history — review may be contentious")

        if oscillations < 4 and escalation_count < 2:
            lines.append("[OK]  History patterns normal")
    else:
        lines.append("[OK]  No history entries")

    # Check 6: Seq mismatch log
    mismatch_entries = _read_diagnostics_log(project_dir)
    if mismatch_entries:
        recent = mismatch_entries[-5:]  # last 5
        lines.append(f"[WARN] {len(mismatch_entries)} seq mismatch(es)"
                     " in diagnostics log:")
        for entry in recent:
            lines.append(
                f"       expected={entry.get('expected')}"
                f" actual={entry.get('actual')}"
                f" caller={entry.get('caller')}"
                f" @ {entry.get('timestamp', '?')}")
    else:
        lines.append("[OK]  No seq mismatches in diagnostics log")

    # Check 7: Agent health (optional)
    if check_agents:
        lines.append("")
        lines.append("Agent Health:")
        _check_agent_health(lines, project_dir)

    # Recommendation
    lines.append("")
    if status == "ready" and updated_at:
        try:
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(updated_at)).total_seconds()
            if age > 300:
                lines.append(f"Recommendation: Re-send command to {turn},"
                             " or run /handoff status in their terminal.")
        except (ValueError, TypeError):
            pass

    return "\n".join(lines)


def format_state(state: dict) -> str:
    """Format state for human-readable display."""
    if not state:
        return "No active state."

    turn = state.get("turn", "?")
    status = state.get("status", "?")
    phase = state.get("phase", "?")
    step_type = state.get("type", "?")
    round_num = state.get("round", "?")
    command = state.get("command", "")
    updated_at = state.get("updated_at", "?")
    updated_by = state.get("updated_by", "?")
    run_mode = state.get("run_mode", "single-phase")

    lines = [
        f"Phase:      {phase}",
        f"Type:       {step_type}",
        f"Turn:       {turn}",
        f"Status:     {status}",
        f"Round:      {round_num}",
        f"Mode:       {run_mode}",
        f"Command:    {command}",
        f"Updated by: {updated_by}",
        f"Updated at: {updated_at}",
    ]

    # Show roadmap progress if in full-roadmap mode
    roadmap = state.get("roadmap")
    if roadmap and run_mode == "full-roadmap":
        queue = roadmap.get("queue", [])
        idx = roadmap.get("current_index", 0)
        completed = roadmap.get("completed", [])
        pause_reason = roadmap.get("pause_reason")
        total = len(queue)
        progress = len(completed)
        next_phase = queue[idx + 1] if idx + 1 < total else "(last)"

        lines.append(f"Progress:   {progress}/{total}")
        lines.append(f"Next phase: {next_phase}")
        if pause_reason:
            lines.append(f"Paused:     {pause_reason}")

    return "\n".join(lines)


# --- CLI entry points ---

def state_command(args: list[str]) -> int:
    """Handle `python -m tagteam state [subcommand]`."""
    if not args:
        # View current state
        state = read_state()
        if state is None:
            print("No handoff-state.json found.")
            return 0
        print(format_state(state))
        return 0

    subcmd = args[0]

    if subcmd == "reset":
        clear_state()
        print("State cleared.")
        return 0

    if subcmd == "diagnose":
        clean = "--clean" in args
        check_agents = "--check-agents" in args
        if clean:
            clear_diagnostics_log()
            print("Diagnostics log cleared.")
        else:
            print(diagnose_state(check_agents=check_agents))
        return 0

    if subcmd == "set":
        return _state_set(args[1:])

    if subcmd == "sync":
        return _state_sync(args[1:])

    print(f"Unknown state subcommand: {subcmd}")
    print("Usage: python -m tagteam state [set|reset|diagnose|sync]")
    return 1


def _state_sync(args: list[str]) -> int:
    """Rewrite handoff-state.json from per-cycle status (source of truth).

    Escape hatch when top-level state has drifted out of sync with the
    active cycle (e.g. because a tool edited cycle files without going
    through tagteam). Picks a cycle, derives the correct top-level
    state from its status JSON, and writes it.

    Usage:
        tagteam state sync                        # most recently modified cycle
        tagteam state sync --phase P --type T     # specific cycle
    """
    from tagteam.cycle import (
        _derive_top_level_state, _handoffs_dir, list_cycles,
    )

    allowed = {"--phase", "--type"}
    parsed: dict[str, str] = {}
    i = 0
    while i < len(args):
        if args[i] in allowed and i + 1 < len(args):
            parsed[args[i]] = args[i + 1]
            i += 2
        else:
            print(f"Unknown or malformed flag: {args[i]}")
            return 1

    project_dir = _resolve_project_root()

    phase = parsed.get("--phase")
    cycle_type = parsed.get("--type")

    if phase and cycle_type:
        target = (phase, cycle_type)
    elif phase or cycle_type:
        print("--phase and --type must be passed together")
        return 1
    else:
        # Pick the most recently modified cycle status file.
        handoffs = _handoffs_dir(project_dir)
        if not handoffs.is_dir():
            print("No docs/handoffs/ directory found.")
            return 1
        cycles = list_cycles(project_dir)
        jsonl_cycles = [c for c in cycles if c["format"] == "jsonl"]
        if not jsonl_cycles:
            print("No JSONL-format cycles found to sync from.")
            return 1

        def mtime(c: dict) -> float:
            p = handoffs / f"{c['phase']}_{c['type']}_status.json"
            return p.stat().st_mtime if p.exists() else 0.0

        latest = max(jsonl_cycles, key=mtime)
        target = (latest["phase"], latest["type"])

    new_state = _derive_top_level_state(
        target[0], target[1], project_dir,
        updated_by="state-sync",
    )
    if new_state is None:
        print(f"Cycle {target[0]}_{target[1]} not found or has an "
              "unrecognized state/ready_for combination.")
        return 1

    print(f"Synced from {target[0]}_{target[1]}: "
          f"turn={new_state.get('turn')} "
          f"status={new_state.get('status')} "
          f"round={new_state.get('round')}")
    return 0


def _state_set(args: list[str]) -> int:
    """Parse --key value pairs and update state."""
    allowed_keys = {
        "--turn", "--status", "--command", "--phase",
        "--type", "--round", "--updated-by", "--result", "--reason",
        "--run-mode", "--roadmap-queue", "--roadmap-index",
        "--roadmap-completed", "--roadmap-pause-reason",
    }

    # Roadmap sub-fields are collected separately then merged into
    # the top-level "roadmap" dict before writing.
    ROADMAP_FIELDS = {
        "roadmap_queue", "roadmap_index",
        "roadmap_completed", "roadmap_pause_reason",
    }

    updates = {}
    roadmap_updates = {}
    i = 0
    while i < len(args):
        key = args[i]
        if key not in allowed_keys:
            print(f"Unknown flag: {key}")
            print(f"Allowed: {', '.join(sorted(allowed_keys))}")
            return 1
        if i + 1 >= len(args):
            print(f"Missing value for {key}")
            return 1

        field = key.lstrip("-").replace("-", "_")
        value = args[i + 1]

        # Validate specific fields
        if field == "turn" and value not in VALID_TURNS:
            print(f"Invalid turn: {value}. Must be one of: {', '.join(VALID_TURNS)}")
            return 1
        if field == "status" and value not in VALID_STATUSES:
            print(f"Invalid status: {value}. Must be one of: {', '.join(VALID_STATUSES)}")
            return 1
        if field == "run_mode" and value not in VALID_RUN_MODES:
            print(f"Invalid run_mode: {value}. Must be one of: {', '.join(VALID_RUN_MODES)}")
            return 1
        if field == "round":
            try:
                value = int(value)
            except ValueError:
                print(f"Round must be an integer, got: {value}")
                return 1

        # Roadmap sub-fields go into the nested roadmap object
        if field in ROADMAP_FIELDS:
            roadmap_key = field.removeprefix("roadmap_")
            if roadmap_key == "queue":
                value = [s.strip() for s in value.split(",") if s.strip()]
            elif roadmap_key == "index":
                try:
                    value = int(value)
                except ValueError:
                    print(f"Roadmap index must be an integer, got: {value}")
                    return 1
            elif roadmap_key == "completed":
                value = [s.strip() for s in value.split(",") if s.strip()]
            elif roadmap_key == "pause_reason":
                value = value if value else None
            roadmap_updates[roadmap_key] = value
        else:
            updates[field] = value
        i += 2

    if not updates and not roadmap_updates:
        print("No fields to update. Use --turn, --status, --command, etc.")
        return 1

    # Merge roadmap sub-fields into the state's roadmap object
    if roadmap_updates:
        current = read_state() or {}
        roadmap = current.get("roadmap") or {
            "queue": [],
            "current_index": 0,
            "completed": [],
            "pause_reason": None,
        }
        roadmap.update(roadmap_updates)
        updates["roadmap"] = roadmap

    state = update_state(updates)
    print(f"State updated: turn={state.get('turn')}, status={state.get('status')}")
    return 0


# --- Phase 28 Step A: shadow DB write helper for state writers ---

def _shadow_db_write_state(state_dict: dict,
                           project_dir: str,
                           *,
                           history_entry: dict | None = None) -> None:
    """Mirror the just-written `handoff-state.json` to the shadow DB.

    Called from `update_state` (with a history_entry — the row that
    `update_state` just appended to the file's history array) and
    from `write_state` (with no history_entry — direct callers of
    `write_state` bypass the history machinery on the file side and
    do the same on the DB side).

    Failures mark `db_invalid` and are swallowed. File path is
    canonical during Step A.
    """
    from tagteam import db, dualwrite

    conn = None
    try:
        conn = db.connect(project_dir=project_dir)
        known_top_level = {
            "phase", "type", "round", "status", "command", "result",
            "updated_by", "run_mode", "seq", "updated_at", "history",
        }
        extra = {
            k: v for k, v in state_dict.items() if k not in known_top_level
        }
        db.set_state(
            conn,
            phase=state_dict.get("phase"),
            type=state_dict.get("type"),
            round=state_dict.get("round"),
            status=state_dict.get("status"),
            command=state_dict.get("command"),
            result=state_dict.get("result"),
            updated_by=state_dict.get("updated_by"),
            run_mode=state_dict.get("run_mode"),
            seq=state_dict.get("seq"),
            updated_at=state_dict.get("updated_at"),
            extra_json=json.dumps(extra) if extra else None,
        )
        if history_entry is not None:
            db.add_history_entry(conn, history_entry)
        conn.commit()
    except Exception as e:
        dualwrite.mark_db_invalid(
            project_dir, reason=f"state dual-write failed: {e}"
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
