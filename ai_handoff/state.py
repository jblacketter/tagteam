"""
State file management for handoff orchestration.

Provides atomic read/write of handoff-state.json, which tracks
whose turn it is and what command the next agent should run.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = "handoff-state.json"
STATE_TMP = ".handoff-state.tmp"

VALID_STATUSES = {"ready", "working", "done", "escalated", "aborted"}
VALID_TURNS = {"lead", "reviewer"}
VALID_RUN_MODES = {"single-phase", "full-roadmap"}


def get_state_path(project_dir: str = ".") -> Path:
    return Path(project_dir) / STATE_FILE


def read_state(project_dir: str = ".") -> dict | None:
    """Read the current state from handoff-state.json. Returns None if missing."""
    path = get_state_path(project_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_state(state: dict, project_dir: str = ".") -> None:
    """Atomic write: write to temp file, then rename."""
    path = get_state_path(project_dir)
    tmp_path = Path(project_dir) / STATE_TMP

    state["updated_at"] = datetime.now(timezone.utc).isoformat()

    tmp_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp_path.rename(path)


def update_state(updates: dict, project_dir: str = ".",
                 expected_seq: int | None = None,
                 clear_keys: list[str] | None = None) -> dict | None:
    """Read current state, record history, apply updates, write back.

    If expected_seq is provided, only write if the current sequence number
    matches. Returns None if the write was skipped due to staleness.

    If clear_keys is provided, those keys will be deleted from the state
    before applying updates.
    """
    state = read_state(project_dir) or {}

    current_seq = state.get("seq", 0)
    if expected_seq is not None and current_seq != expected_seq:
        return None  # State has moved on

    if "history" not in state:
        state["history"] = []

    # Record the previous state in history before overwriting
    prev = {
        "turn": state.get("turn"),
        "status": state.get("status"),
        "timestamp": state.get("updated_at"),
    }
    if any(v is not None for v in prev.values()):
        state["history"].append(prev)

    # Keep history bounded
    state["history"] = state["history"][-20:]

    # Clear specified keys before applying updates
    if clear_keys:
        for key in clear_keys:
            state.pop(key, None)

    state["seq"] = current_seq + 1
    state.update(updates)
    write_state(state, project_dir)
    return state


def clear_state(project_dir: str = ".") -> None:
    """Delete the state file."""
    path = get_state_path(project_dir)
    if path.exists():
        path.unlink()
    tmp_path = Path(project_dir) / STATE_TMP
    if tmp_path.exists():
        tmp_path.unlink()


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
    """Handle `python -m ai_handoff state [subcommand]`."""
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

    if subcmd == "set":
        return _state_set(args[1:])

    print(f"Unknown state subcommand: {subcmd}")
    print("Usage: python -m ai_handoff state [set|reset]")
    return 1


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
