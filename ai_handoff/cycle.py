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

# Status transitions keyed by action (cycle status.json)
_TRANSITIONS = {
    "SUBMIT_FOR_REVIEW": {"state": "in-progress", "ready_for": "reviewer"},
    "REQUEST_CHANGES":   {"state": "in-progress", "ready_for": "lead"},
    "APPROVE":           {"state": "approved",    "ready_for": None},
    "ESCALATE":          {"state": "escalated",   "ready_for": "human"},
    "NEED_HUMAN":        {"state": "needs-human",  "ready_for": "human"},
}

# Handoff state transitions keyed by action (handoff-state.json)
_STATE_TRANSITIONS = {
    "SUBMIT_FOR_REVIEW": {"turn": "reviewer", "status": "ready"},
    "REQUEST_CHANGES":   {"turn": "lead",     "status": "ready"},
    "APPROVE":           {"status": "done",   "result": "approved"},
    "ESCALATE":          {"status": "escalated"},
    "NEED_HUMAN":        {"status": "escalated"},
}

_STATE_COMMAND = "Read .claude/skills/handoff/SKILL.md and handoff-state.json, then act on your turn"


def _handoffs_dir(project_dir: str) -> Path:
    return Path(project_dir) / "docs" / "handoffs"


def _status_path(phase: str, cycle_type: str, project_dir: str) -> Path:
    return _handoffs_dir(project_dir) / f"{phase}_{cycle_type}_status.json"


def _rounds_path(phase: str, cycle_type: str, project_dir: str) -> Path:
    return _handoffs_dir(project_dir) / f"{phase}_{cycle_type}_rounds.jsonl"


# --- Core functions ---

def init_cycle(phase: str, cycle_type: str, lead: str, reviewer: str,
               content: str, project_dir: str = ".",
               updated_by: str | None = None) -> dict:
    """Create a new cycle atomically with the lead's first submission.

    Writes both status JSON and the first JSONL round entry together.
    If updated_by is provided, also updates handoff-state.json.
    """
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

    # Write both files
    sp = _status_path(phase, cycle_type, project_dir)
    rp = _rounds_path(phase, cycle_type, project_dir)
    rp.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    sp.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    # Optionally update handoff-state.json
    if updated_by:
        _update_handoff_state(
            phase, cycle_type, "SUBMIT_FOR_REVIEW", 1,
            updated_by, project_dir,
        )

    return status


def add_round(phase: str, cycle_type: str, role: str, action: str,
              round_num: int, content: str, project_dir: str = ".",
              updated_by: str | None = None) -> dict:
    """Append a round entry to the JSONL log and update status.

    If updated_by is provided, also updates handoff-state.json.
    Returns the updated status dict.
    """
    if action not in VALID_ACTIONS:
        raise ValueError(f"Invalid action: {action}. Must be one of: {', '.join(sorted(VALID_ACTIONS))}")
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role}. Must be one of: {', '.join(sorted(VALID_ROLES))}")

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

    # Round 5 REQUEST_CHANGES auto-escalates
    if action == "REQUEST_CHANGES" and round_num >= 5:
        status["state"] = "escalated"
        status["ready_for"] = "human"

    # Only advance round when caller provides a higher value
    if round_num > status.get("round", 0):
        status["round"] = round_num

    sp = _status_path(phase, cycle_type, project_dir)
    sp.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    # Optionally update handoff-state.json
    if updated_by:
        _update_handoff_state(
            phase, cycle_type, action, round_num,
            updated_by, project_dir,
        )

    return status


def _update_handoff_state(phase: str, cycle_type: str, action: str,
                          round_num: int, updated_by: str,
                          project_dir: str = ".") -> None:
    """Update handoff-state.json based on cycle action.

    Handles round-5 auto-escalation: REQUEST_CHANGES at round 5
    escalates to human arbiter instead of handing back to lead.

    Normalizes state to prevent stale completion metadata from
    previous cycles from persisting.
    """
    from ai_handoff.state import update_state, read_state, normalize_phase_key

    transition = dict(_STATE_TRANSITIONS[action])

    # Round 5 REQUEST_CHANGES auto-escalates
    if action == "REQUEST_CHANGES" and round_num >= 5:
        transition = {"status": "escalated"}

    updates = {
        "phase": phase,
        "type": cycle_type,
        "round": round_num,
        "updated_by": updated_by,
        "command": _STATE_COMMAND,
    }
    updates.update(transition)

    # Explicitly clear stale completion state for in-progress transitions
    # to prevent result="approved" or result="roadmap-complete" from previous
    # cycles from persisting when a new cycle begins.
    if action in ("SUBMIT_FOR_REVIEW", "REQUEST_CHANGES"):
        if round_num < 5:  # Only clear if not auto-escalating
            updates["result"] = None

    # Normalize run_mode and roadmap context based on current state.
    # When starting a new cycle, check if we're continuing an active roadmap
    # or starting fresh.
    current_state = read_state(project_dir)
    should_preserve_roadmap = False
    clear_keys = []

    if current_state and "roadmap" in current_state and "run_mode" in current_state:
        # Preserve roadmap context only if this cycle matches the active roadmap phase
        roadmap = current_state["roadmap"]
        is_roadmap_mode = current_state.get("run_mode") == "full-roadmap"
        current_roadmap_phase = None
        if is_roadmap_mode and roadmap.get("queue"):
            idx = roadmap.get("current_index", 0)
            if 0 <= idx < len(roadmap["queue"]):
                current_roadmap_phase = roadmap["queue"][idx]

        # Preserve roadmap state only if this phase matches the current roadmap phase
        # Normalize both sides since queue may store slugs while phase param may be full name
        if is_roadmap_mode and current_roadmap_phase:
            phase_normalized = normalize_phase_key(phase)
            roadmap_phase_normalized = normalize_phase_key(current_roadmap_phase)
            if phase_normalized == roadmap_phase_normalized:
                updates["roadmap"] = roadmap
                updates["run_mode"] = "full-roadmap"
                should_preserve_roadmap = True

    if not should_preserve_roadmap:
        # Clear stale roadmap context when starting a new single-phase cycle
        # or when the phase doesn't match the active roadmap.
        updates["run_mode"] = "single-phase"
        if current_state and "roadmap" in current_state:
            clear_keys.append("roadmap")

    update_state(updates, project_dir, clear_keys=clear_keys or None)


def read_status(phase: str, cycle_type: str, project_dir: str = ".") -> dict | None:
    """Read status JSON for a cycle. Returns None if not found."""
    sp = _status_path(phase, cycle_type, project_dir)
    if not sp.exists():
        return None
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def read_rounds(phase: str, cycle_type: str, project_dir: str = ".") -> list[dict]:
    """Read all round entries from JSONL. Returns empty list if not found."""
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
    """Handle `python -m ai_handoff cycle <subcommand>`."""
    if not args:
        print("Usage: python -m ai_handoff cycle <init|add|status|rounds|render>")
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
    msg = f"Cycle created: {phase}_{cycle_type} (round 1, ready_for: reviewer)"
    if updated_by:
        msg += " + state updated"
    print(msg)
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
    msg = (f"Round added: {phase}_{cycle_type} round={status['round']} "
           f"state={status['state']} ready_for={status.get('ready_for')}")
    if updated_by:
        msg += " + state updated"
    print(msg)
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
    from ai_handoff.parser import read_cycle_rounds

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
