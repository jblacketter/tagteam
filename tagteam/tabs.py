"""
Tab-driver dispatch for the terminal-window backends (iTerm2, Terminal.app).

Both drivers (`tagteam.iterm`, `tagteam.terminal`) expose the same surface —
``create_session``, ``write_text_to_session``, ``submit``, ``get_session_contents``,
``session_id_is_valid``, ``get_session_id``, ``kill_session``,
``_any_session_alive``, ``list_sessions`` — and share one on-disk record,
``.handoff-session.json``. This module owns that record and picks the driver
from its ``backend`` field so the watcher, the dashboard log tail and the
health check never import a specific driver directly.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import ModuleType

SESSION_FILE = ".handoff-session.json"

# Backends whose agents live in terminal windows/tabs addressed by an id
# stored in .handoff-session.json (as opposed to tmux panes or nothing).
TAB_BACKENDS = ("iterm2", "terminal")

# Pre-3.6 session files carry no "backend" key; they were always iTerm2.
_DEFAULT_TAB_BACKEND = "iterm2"

# Pause between typing a message and the submitting CR/newline, in both tab
# drivers (seconds, AppleScript `delay`). Codex takes an Enter that arrives
# right after a burst of typed text as a newline in its composer, not as
# submit (Phase 74c, measured on codex-cli 0.157.0 in iTerm2: a 0 or 20 ms gap
# left the message unsubmitted 10/10, 40 ms 1/5; 50 ms, the old value, is too
# close to that edge). 0.5 s is well past it and still short next to a turn.
SUBMIT_DELAY_S = 0.5


def _osascript(script: str, timeout: float = 10) -> str:
    """Execute AppleScript and return stdout (raises RuntimeError on failure)."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"osascript failed: {result.stderr.strip()}")
    return result.stdout.strip()


# -- session file -----------------------------------------------------------

def _session_file_path(project_dir: str) -> Path:
    return Path(project_dir) / SESSION_FILE


def _find_session_file(project_dir: str) -> Path | None:
    """Find .handoff-session.json in project_dir or any parent directory."""
    current = Path(project_dir).resolve()
    for _ in range(20):  # safety limit
        candidate = current / SESSION_FILE
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _read_session_file(project_dir: str) -> dict | None:
    """Read the session file, searching parent directories if needed."""
    path = _find_session_file(project_dir)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_session_file(project_dir: str, data: dict) -> None:
    path = _session_file_path(project_dir)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def session_backend(project_dir: str) -> str | None:
    """The tab backend recorded in .handoff-session.json, or None if there
    is no readable session file. A file without a ``backend`` key (written
    before 3.6) is an iTerm2 session."""
    data = _read_session_file(project_dir)
    if not data:
        return None
    backend = data.get("backend") or _DEFAULT_TAB_BACKEND
    return str(backend)


# -- drivers -----------------------------------------------------------------

def driver_for(backend: str) -> ModuleType:
    """Return the driver module for a tab backend ('iterm2' | 'terminal')."""
    if backend == "iterm2":
        from tagteam import iterm
        return iterm
    if backend == "terminal":
        from tagteam import terminal
        return terminal
    raise ValueError(f"not a tab backend: {backend!r} (expected one of {TAB_BACKENDS})")


def session_driver(project_dir: str) -> tuple[str, ModuleType] | None:
    """(backend, driver) for the project's session file, or None when there
    is no session file or it names something that is not a tab backend."""
    backend = session_backend(project_dir)
    if backend not in TAB_BACKENDS:
        return None
    return backend, driver_for(backend)


def get_session_id(role: str, project_dir: str) -> str | None:
    """Read a role's session id from .handoff-session.json (any backend)."""
    data = _read_session_file(project_dir)
    if not data:
        return None
    tab = (data.get("tabs") or {}).get(role) or {}
    return tab.get("session_id")
