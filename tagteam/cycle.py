"""
Cycle storage and CLI for structured handoff rounds.

Replaces markdown-based cycle documents with append-only JSONL rounds
and a small JSON status file, updated via CLI commands.

File structure per cycle:
    docs/handoffs/{phase}_{type}_status.json   — cycle metadata + state
    docs/handoffs/{phase}_{type}_rounds.jsonl   — append-only round log
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

VALID_ACTIONS = {
    "SUBMIT_FOR_REVIEW", "REQUEST_CHANGES", "APPROVE",
    "ESCALATE", "NEED_HUMAN",
}
VALID_ROLES = {"lead", "reviewer"}
VALID_TYPES = {"plan", "impl"}

# Auto-escalate after this many consecutive rounds with no progress
# (lead re-submitting identical content = stuck, not converging)
STALE_ROUND_LIMIT = 10

# Status transitions keyed by action (cycle status.json)
_TRANSITIONS = {
    "SUBMIT_FOR_REVIEW": {"state": "in-progress", "ready_for": "reviewer"},
    "REQUEST_CHANGES":   {"state": "in-progress", "ready_for": "lead"},
    "APPROVE":           {"state": "approved",    "ready_for": None},
    "ESCALATE":          {"state": "escalated",   "ready_for": "human"},
    "NEED_HUMAN":        {"state": "needs-human",  "ready_for": "human"},
}

_STATE_COMMAND = "Read .claude/skills/handoff/SKILL.md and handoff-state.json, then act on your turn"


def _resolve(project_dir: str) -> str:
    """Resolve "." to the git repo root so cycle writes always target the
    repo's docs/handoffs/ regardless of cwd. Explicit paths are honored.

    Fixes the nested-project silent-write bug (Issue #1, 2026-04-24): running
    `tagteam cycle init` from a subdir of a tagteam project used to write
    into that subdir instead of the repo root.
    """
    if project_dir == ".":
        from tagteam.state import _resolve_project_root
        return _resolve_project_root()
    return project_dir


def _handoffs_dir(project_dir: str) -> Path:
    return Path(_resolve(project_dir)) / "docs" / "handoffs"


def _status_path(phase: str, cycle_type: str, project_dir: str) -> Path:
    return _handoffs_dir(project_dir) / f"{phase}_{cycle_type}_status.json"


def _rounds_path(phase: str, cycle_type: str, project_dir: str) -> Path:
    return _handoffs_dir(project_dir) / f"{phase}_{cycle_type}_rounds.jsonl"


# --- Core functions ---

def _count_stale_rounds(phase: str, cycle_type: str, project_dir: str) -> int:
    """Count consecutive recent rounds with no progress.

    Progress means the lead's SUBMIT_FOR_REVIEW content changed from
    their previous submission.  If the lead keeps re-submitting identical
    content, those rounds are "stale" — the cycle is stuck, not converging.

    Returns the number of consecutive stale rounds (from most recent backward).
    """
    rounds = read_rounds(phase, cycle_type, project_dir)

    # Extract lead submissions in order
    submissions = [
        r["content"] for r in rounds
        if r["role"] == "lead" and r["action"] == "SUBMIT_FOR_REVIEW"
    ]

    if len(submissions) < 2:
        return 0

    # Count consecutive identical submissions from the end
    stale = 0
    for i in range(len(submissions) - 1, 0, -1):
        if submissions[i] == submissions[i - 1]:
            stale += 1
        else:
            break

    return stale


def init_cycle(phase: str, cycle_type: str, lead: str, reviewer: str,
               content: str, project_dir: str = ".",
               updated_by: str | None = None) -> dict:
    """Create a new cycle atomically with the lead's first submission.

    Writes rounds JSONL + status JSON, then derives handoff-state.json
    from the cycle status so the top-level state is always in sync.
    `updated_by` defaults to `lead` (since init always submits as lead).
    """
    project_dir = _resolve(project_dir)
    handoffs = _handoffs_dir(project_dir)
    handoffs.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()

    status = {
        "state": "in-progress",
        "ready_for": "reviewer",
        "round": 1,
        "phase": phase,
        "type": cycle_type,
        "lead": lead,
        "reviewer": reviewer,
        "date": now[:10],
    }

    entry = {
        "round": 1,
        "role": "lead",
        "action": "SUBMIT_FOR_REVIEW",
        "content": content,
        "ts": now,
    }

    sp = _status_path(phase, cycle_type, project_dir)
    rp = _rounds_path(phase, cycle_type, project_dir)
    rp.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    sp.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    _derive_top_level_state(
        phase, cycle_type, project_dir,
        updated_by=updated_by or lead,
    )

    return status


def add_round(phase: str, cycle_type: str, role: str, action: str,
              round_num: int, content: str, project_dir: str = ".",
              updated_by: str | None = None) -> dict:
    """Append a round entry to the JSONL log, update cycle status,
    and derive handoff-state.json from the new cycle status.

    If `updated_by` is not provided, it is inferred from the cycle
    status (`lead` field for role=lead, `reviewer` field for
    role=reviewer). This keeps the top-level state in sync even when
    a caller forgets to pass `--updated-by`.
    """
    if action not in VALID_ACTIONS:
        raise ValueError(f"Invalid action: {action}. Must be one of: {', '.join(sorted(VALID_ACTIONS))}")
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role}. Must be one of: {', '.join(sorted(VALID_ROLES))}")

    project_dir = _resolve(project_dir)
    now = datetime.now(timezone.utc).isoformat()

    entry = {
        "round": round_num,
        "role": role,
        "action": action,
        "content": content,
        "ts": now,
    }

    # Append to JSONL
    rp = _rounds_path(phase, cycle_type, project_dir)
    with open(rp, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    # Update status
    status = read_status(phase, cycle_type, project_dir) or {}
    transition = _TRANSITIONS[action]
    status["state"] = transition["state"]
    status["ready_for"] = transition["ready_for"]

    # Auto-escalate only when the cycle is stuck (no progress),
    # not merely because it reached a certain round number.
    auto_escalate = False
    if action == "REQUEST_CHANGES":
        stale = _count_stale_rounds(phase, cycle_type, project_dir)
        if stale >= STALE_ROUND_LIMIT:
            auto_escalate = True
            status["state"] = "escalated"
            status["ready_for"] = "human"

    # Only advance round when caller provides a higher value
    if round_num > status.get("round", 0):
        status["round"] = round_num

    sp = _status_path(phase, cycle_type, project_dir)
    sp.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    # Infer updated_by from the cycle roster when the caller didn't supply it,
    # so the top-level state always stays in sync with the per-cycle source
    # of truth.
    resolved_updated_by = updated_by
    if not resolved_updated_by:
        if role == "lead":
            resolved_updated_by = status.get("lead") or role
        else:
            resolved_updated_by = status.get("reviewer") or role

    _derive_top_level_state(
        phase, cycle_type, project_dir,
        updated_by=resolved_updated_by,
    )

    return status


_CYCLE_STATE_TO_TOP_LEVEL = {
    # Maps per-cycle (state, ready_for) → (turn, status, result)
    # `None` for turn means leave unset (e.g. escalated, done).
    ("in-progress", "reviewer"):  ("reviewer", "ready",     None),
    ("in-progress", "lead"):      ("lead",     "ready",     None),
    ("approved",    None):        (None,       "done",      "approved"),
    ("escalated",   "human"):     (None,       "escalated", None),
    ("needs-human", "human"):     (None,       "escalated", None),
}


def _derive_top_level_state(phase: str, cycle_type: str,
                            project_dir: str,
                            updated_by: str | None = None) -> dict | None:
    """Rewrite handoff-state.json to reflect the given cycle's current status.

    Per-cycle status is the source of truth. This reads the cycle's
    status JSON, maps it into top-level fields via the invertible
    mapping above, preserves roadmap context when the phase matches the
    active roadmap phase, and writes atomically. Uses replace=True so
    stale fields from prior cycles cannot leak via shallow merge.

    Returns the new state dict, or None if the cycle status is missing.
    """
    from tagteam.state import (
        read_state, update_state, normalize_phase_key, VALID_TURNS,
    )

    project_dir = _resolve(project_dir)
    cycle_status = read_status(phase, cycle_type, project_dir)
    if cycle_status is None:
        return None

    cstate = cycle_status.get("state")
    ready_for = cycle_status.get("ready_for")
    mapping = _CYCLE_STATE_TO_TOP_LEVEL.get((cstate, ready_for))
    if mapping is None:
        # Unknown combination — fail safe: leave state alone, surface via
        # diagnose rather than writing a broken top-level.
        return None
    turn, status, result = mapping

    updates: dict = {
        "phase": phase,
        "type": cycle_type,
        "round": cycle_status.get("round"),
        "status": status,
        "command": _STATE_COMMAND,
    }
    if turn in VALID_TURNS:
        updates["turn"] = turn
    if result is not None:
        updates["result"] = result
    if updated_by:
        updates["updated_by"] = updated_by

    # Preserve roadmap context only when this phase is the current roadmap phase.
    current_state = read_state(project_dir) or {}
    roadmap = current_state.get("roadmap")
    if roadmap and current_state.get("run_mode") == "full-roadmap":
        queue = roadmap.get("queue") or []
        idx = roadmap.get("current_index", 0)
        if 0 <= idx < len(queue):
            if normalize_phase_key(phase) == normalize_phase_key(queue[idx]):
                updates["roadmap"] = roadmap
                updates["run_mode"] = "full-roadmap"

    if "run_mode" not in updates:
        updates["run_mode"] = "single-phase"

    return update_state(updates, project_dir, replace=True)


def _update_handoff_state(phase: str, cycle_type: str, action: str,
                          round_num: int, updated_by: str,
                          project_dir: str = ".",
                          auto_escalate: bool = False) -> None:
    """Update handoff-state.json to match the current per-cycle status.

    Thin wrapper around _derive_top_level_state. Kept for call-site
    compatibility; action/round_num/auto_escalate are no longer needed
    for the derivation itself (the cycle status file already reflects
    the outcome) but remain for backward compatibility with callers.
    """
    _derive_top_level_state(phase, cycle_type, project_dir, updated_by)


def read_status(phase: str, cycle_type: str, project_dir: str = ".") -> dict | None:
    """Read status JSON for a cycle. Returns None if not found."""
    project_dir = _resolve(project_dir)
    sp = _status_path(phase, cycle_type, project_dir)
    if not sp.exists():
        return None
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def read_rounds(phase: str, cycle_type: str, project_dir: str = ".") -> list[dict]:
    """Read all round entries from JSONL. Returns empty list if not found."""
    project_dir = _resolve(project_dir)
    rp = _rounds_path(phase, cycle_type, project_dir)
    if not rp.exists():
        return []
    entries = []
    for line in rp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def render_cycle(phase: str, cycle_type: str, project_dir: str = ".") -> str | None:
    """Synthesize human-readable markdown from JSONL + status.

    Returns None if the cycle doesn't exist.
    """
    status = read_status(phase, cycle_type, project_dir)
    if status is None:
        return None

    entries = read_rounds(phase, cycle_type, project_dir)

    step_label = "Plan" if cycle_type == "plan" else "Implementation"
    lines = [
        f"# {step_label} Review Cycle: {phase}",
        "",
        f"- **Phase:** {phase}",
        f"- **Type:** {cycle_type}",
        f"- **Date:** {status.get('date', '?')}",
        f"- **Lead:** {status.get('lead', '?')}",
        f"- **Reviewer:** {status.get('reviewer', '?')}",
        "",
    ]

    # Group entries by round
    rounds: dict[int, list[dict]] = {}
    for e in entries:
        r = e.get("round", 0)
        rounds.setdefault(r, []).append(e)

    for round_num in sorted(rounds.keys()):
        lines.append(f"## Round {round_num}")
        lines.append("")
        for e in rounds[round_num]:
            role_label = "Lead" if e["role"] == "lead" else "Reviewer"
            lines.append(f"### {role_label}")
            lines.append("")
            lines.append(f"**Action:** {e.get('action', '?')}")
            lines.append("")
            lines.append(e.get("content", ""))
            lines.append("")

    # Status footer
    lines.append("---")
    lines.append("")
    lines.append("<!-- CYCLE_STATUS -->")
    lines.append(f"READY_FOR: {status.get('ready_for', '?')}")
    lines.append(f"ROUND: {status.get('round', '?')}")
    lines.append(f"STATE: {status.get('state', '?')}")

    return "\n".join(lines)


def list_cycles(project_dir: str = ".") -> list[dict]:
    """List all cycles, de-duplicating JSONL and legacy .md formats.

    JSONL takes precedence when both formats exist for the same cycle.
    Returns list of {id, format, phase, type} dicts.
    """
    project_dir = _resolve(project_dir)
    handoffs = _handoffs_dir(project_dir)
    if not handoffs.is_dir():
        return []

    cycles = {}

    # Scan for JSONL-backed cycles (status.json files)
    for f in handoffs.iterdir():
        m = re.match(r"^(.+)_(plan|impl)_status\.json$", f.name)
        if m:
            phase, cycle_type = m.group(1), m.group(2)
            cycle_id = f"{phase}_{cycle_type}"
            cycles[cycle_id] = {
                "id": cycle_id,
                "format": "jsonl",
                "phase": phase,
                "type": cycle_type,
            }

    # Scan for legacy markdown cycles (only if no JSONL version exists)
    for f in handoffs.iterdir():
        m = re.match(r"^(.+)_(plan|impl)_cycle\.md$", f.name)
        if m:
            phase, cycle_type = m.group(1), m.group(2)
            cycle_id = f"{phase}_{cycle_type}"
            if cycle_id not in cycles:  # JSONL takes precedence
                cycles[cycle_id] = {
                    "id": cycle_id,
                    "format": "markdown",
                    "phase": phase,
                    "type": cycle_type,
                }

    return sorted(cycles.values(), key=lambda c: c["id"])


# --- CLI ---

def cycle_command(args: list[str]) -> int:
    """Handle `python -m tagteam cycle <subcommand>`."""
    if not args:
        print("Usage: python -m tagteam cycle <init|add|status|rounds|render>")
        return 1

    subcmd = args[0]
    if subcmd == "init":
        return _cli_init(args[1:])
    elif subcmd == "add":
        return _cli_add(args[1:])
    elif subcmd == "status":
        return _cli_status(args[1:])
    elif subcmd == "rounds":
        return _cli_rounds(args[1:])
    elif subcmd == "render":
        return _cli_render(args[1:])
    else:
        print(f"Unknown cycle subcommand: {subcmd}")
        return 1


def _parse_args(args: list[str], allowed: set[str]) -> dict[str, str]:
    """Parse --key value pairs from args."""
    result = {}
    i = 0
    while i < len(args):
        key = args[i]
        if key.startswith("--") and key in allowed:
            if i + 1 >= len(args):
                print(f"Missing value for {key}")
                sys.exit(1)
            result[key] = args[i + 1]
            i += 2
        else:
            print(f"Unknown flag: {key}")
            sys.exit(1)
    return result


def _read_content(parsed: dict[str, str]) -> str:
    """Get content from --content flag or stdin."""
    if "--content" in parsed:
        return parsed["--content"]
    # Read from stdin
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    print("Error: --content required (or pipe via stdin)")
    sys.exit(1)


def _cli_init(args: list[str]) -> int:
    allowed = {"--phase", "--type", "--lead", "--reviewer", "--content", "--updated-by"}
    parsed = _parse_args(args, allowed)

    phase = parsed.get("--phase")
    cycle_type = parsed.get("--type")
    lead = parsed.get("--lead")
    reviewer = parsed.get("--reviewer")
    updated_by = parsed.get("--updated-by")

    if not all([phase, cycle_type, lead, reviewer]):
        print("Required: --phase, --type, --lead, --reviewer")
        return 1
    if cycle_type not in VALID_TYPES:
        print(f"Invalid type: {cycle_type}. Must be 'plan' or 'impl'.")
        return 1

    content = _read_content(parsed)
    status = init_cycle(phase, cycle_type, lead, reviewer, content,
                        updated_by=updated_by)
    print(f"Cycle created: {phase}_{cycle_type} (round 1, ready_for: reviewer)"
          " + state updated")
    return 0


def _cli_add(args: list[str]) -> int:
    allowed = {"--phase", "--type", "--role", "--action", "--round", "--content", "--updated-by"}
    parsed = _parse_args(args, allowed)

    phase = parsed.get("--phase")
    cycle_type = parsed.get("--type")
    role = parsed.get("--role")
    action = parsed.get("--action")
    round_str = parsed.get("--round")

    if not all([phase, cycle_type, role, action, round_str]):
        print("Required: --phase, --type, --role, --action, --round")
        return 1
    if cycle_type not in VALID_TYPES:
        print(f"Invalid type: {cycle_type}. Must be 'plan' or 'impl'.")
        return 1
    if role not in VALID_ROLES:
        print(f"Invalid role: {role}. Must be 'lead' or 'reviewer'.")
        return 1
    if action not in VALID_ACTIONS:
        print(f"Invalid action: {action}. Must be one of: {', '.join(sorted(VALID_ACTIONS))}")
        return 1
    try:
        round_num = int(round_str)
    except ValueError:
        print(f"Round must be an integer, got: {round_str}")
        return 1

    updated_by = parsed.get("--updated-by")
    content = _read_content(parsed)
    status = add_round(phase, cycle_type, role, action, round_num, content,
                       updated_by=updated_by)
    print(f"Round added: {phase}_{cycle_type} round={status['round']} "
          f"state={status['state']} ready_for={status.get('ready_for')}"
          " + state updated")
    return 0


def _cli_status(args: list[str]) -> int:
    allowed = {"--phase", "--type"}
    parsed = _parse_args(args, allowed)

    phase = parsed.get("--phase")
    cycle_type = parsed.get("--type")
    if not phase or not cycle_type:
        print("Required: --phase, --type")
        return 1

    status = read_status(phase, cycle_type)
    if status is not None:
        for k, v in status.items():
            print(f"{k}: {v}")
        return 0

    # Fall back to legacy markdown — extract status from CYCLE_STATUS block
    md_path = _handoffs_dir(".") / f"{phase}_{cycle_type}_cycle.md"
    if md_path.exists():
        import re as _re
        content = md_path.read_text(encoding="utf-8")
        state_m = _re.search(r"STATE:\s*(\S+)", content)
        ready_m = _re.search(r"READY_FOR:\s*(\S+)", content)
        round_m = _re.search(r"ROUND:\s*(\S+)", content)
        print(f"state: {state_m.group(1) if state_m else '?'}")
        print(f"ready_for: {ready_m.group(1) if ready_m else '?'}")
        print(f"round: {round_m.group(1) if round_m else '?'}")
        print(f"format: markdown (legacy)")
        return 0

    print(f"No cycle found: {phase}_{cycle_type}")
    return 1


def _cli_rounds(args: list[str]) -> int:
    from tagteam.parser import read_cycle_rounds

    allowed = {"--phase", "--type"}
    parsed = _parse_args(args, allowed)

    phase = parsed.get("--phase")
    cycle_type = parsed.get("--type")
    if not phase or not cycle_type:
        print("Required: --phase, --type")
        return 1

    # Use dispatcher (checks JSONL first, falls back to markdown)
    rounds = read_cycle_rounds(phase, cycle_type)
    if rounds:
        for r in rounds:
            print(json.dumps(r))
        return 0

    # Also try JSONL-only read_rounds for raw entries
    entries = read_rounds(phase, cycle_type)
    if entries:
        for e in entries:
            print(json.dumps(e))
        return 0

    print(f"No rounds found for: {phase}_{cycle_type}")
    return 1


def _cli_render(args: list[str]) -> int:
    allowed = {"--phase", "--type"}
    parsed = _parse_args(args, allowed)

    phase = parsed.get("--phase")
    cycle_type = parsed.get("--type")
    if not phase or not cycle_type:
        print("Required: --phase, --type")
        return 1

    # Try JSONL render first
    md = render_cycle(phase, cycle_type)
    if md is not None:
        print(md)
        return 0

    # Fall back to legacy markdown — just cat the file
    md_path = _handoffs_dir(".") / f"{phase}_{cycle_type}_cycle.md"
    if md_path.exists():
        print(md_path.read_text(encoding="utf-8"))
        return 0

    print(f"No cycle found: {phase}_{cycle_type}")
    return 1
