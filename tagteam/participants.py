"""Shared participant checks for configured projects, without state mutation."""
from pathlib import Path
import sqlite3

from tagteam.config import read_config, validate_config, get_agent_names


class ParticipantMismatch(ValueError):
    pass


def _owed_start(state, phase, kind):
    """True when the state is a full-roadmap run handing the lead the next
    phase's plan cycle, which does not exist yet (watcher._try_roadmap_advance
    after an impl approval). Nothing is recorded for that cycle, so there is
    nothing to reinterpret: the lead's `cycle init` creates it and records the
    configured names (still checked there through ``proposed``)."""
    if (state.get("turn"), state.get("status"), state.get("run_mode")) != ("lead", "ready", "full-roadmap"):
        return False
    if kind != "plan" or (phase, kind) != (state.get("phase"), state.get("type")):
        return False
    roadmap = state.get("roadmap")
    if not isinstance(roadmap, dict):
        return False
    queue, index = roadmap.get("queue") or [], roadmap.get("current_index")
    return isinstance(index, int) and 0 <= index < len(queue) and queue[index] == phase


def check_participants(project_dir, *, proposed=None, cycle=None):
    """Return fresh config; refuse to reinterpret an unfinished cycle.

    Unconfigured library callers retain explicit-name operation. Production
    launch entry points require configuration independently of this helper.
    """
    from tagteam.state import read_state
    from tagteam.cycle import _legacy_status_path, _read_status_from_file
    from tagteam import db, dualwrite

    path = Path(project_dir) / "tagteam.yaml"
    if not path.exists():
        return None
    config = read_config(path)
    errors = validate_config(config)
    if errors:
        raise ParticipantMismatch("Invalid role configuration: " + "; ".join(errors))
    names = get_agent_names(config)

    def compare(recorded):
        if not all(recorded) or tuple(recorded) != names:
            raise ParticipantMismatch(
                f"Participant mismatch: cycle lead/reviewer={recorded}; "
                f"configured lead/reviewer={names}. Restore the recorded "
                "assignment and finish the cycle before switching roles. "
                "Stop and recreate agent sessions and the watcher after a switch. "
                "Read/status and human recovery remain available."
            )

    if proposed is not None:
        compare(proposed)
    state = read_state(str(project_dir)) or {}
    targets = []
    if state.get("phase") and state.get("type"):
        targets.append((state["phase"], state["type"]))
    if cycle and cycle not in targets:
        targets.append(cycle)
    for phase, kind in targets:
        status = None
        if not dualwrite.is_db_invalid(project_dir):
            try:
                conn = db.read_only_connect(project_dir=project_dir)
                try:
                    status = db.get_cycle(conn, phase, kind)
                finally:
                    conn.close()
            except (dualwrite.ReadOnlyError, sqlite3.Error):
                pass
        if status is None:
            legacy = _legacy_status_path(phase, kind, str(project_dir))
            status = _read_status_from_file(legacy) if legacy is not None else None
        if status is None:
            if _owed_start(state, phase, kind):
                continue
            if state.get("status") in ("ready", "working", "escalated"):
                compare((None, None))
        elif status.get("state") not in ("approved", "aborted"):
            compare((status.get("lead"), status.get("reviewer")))
    return config
