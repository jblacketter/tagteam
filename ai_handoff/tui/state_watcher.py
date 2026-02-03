"""State watcher — polls handoff-state.json and emits change messages.

Reads the state file on a timer and posts a StateChanged message
when the state differs from the last known snapshot.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from textual.message import Message


@dataclass(frozen=True)
class HandoffState:
    """Immutable snapshot of handoff-state.json."""

    turn: str = ""
    status: str = ""
    phase: str = ""
    step_type: str = ""
    round: int = 0
    updated_at: str = ""
    updated_by: str = ""
    result: str = ""
    reason: str = ""
    command: str = ""
    history: tuple = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, data: dict) -> HandoffState:
        return cls(
            turn=data.get("turn", ""),
            status=data.get("status", ""),
            phase=data.get("phase", ""),
            step_type=data.get("type", ""),
            round=data.get("round", 0),
            updated_at=data.get("updated_at", ""),
            updated_by=data.get("updated_by", ""),
            result=data.get("result", "") or "",
            reason=data.get("reason", "") or "",
            command=data.get("command", "") or "",
            history=tuple(
                (h.get("turn"), h.get("status"), h.get("timestamp"))
                for h in data.get("history", [])
            ),
        )

    @property
    def fingerprint(self) -> str:
        """Content hash for detecting changes when updated_at is missing."""
        content = f"{self.turn}|{self.status}|{self.phase}|{self.step_type}|{self.round}|{self.result}|{self.reason}"
        return hashlib.md5(content.encode()).hexdigest()[:12]

    @property
    def is_empty(self) -> bool:
        return not self.turn and not self.status


class StateChanged(Message):
    """Posted when handoff-state.json changes."""

    def __init__(self, state: HandoffState, previous: HandoffState | None) -> None:
        self.state = state
        self.previous = previous
        super().__init__()


def find_state_path(project_dir: str | None = None) -> Path:
    """Resolve the path to handoff-state.json.

    If project_dir is given, uses that directly. Otherwise checks:
    1. HANDOFF_STATE_PATH environment variable
    2. Current working directory
    """
    if project_dir is not None:
        return Path(project_dir) / "handoff-state.json"

    env_path = os.environ.get("HANDOFF_STATE_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    cwd_path = Path.cwd() / "handoff-state.json"
    return cwd_path


def read_handoff_state(path: Path | None = None) -> HandoffState | None:
    """Read and parse handoff-state.json. Returns None if missing or invalid."""
    if path is None:
        path = find_state_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return HandoffState.from_dict(data)
    except (json.JSONDecodeError, OSError):
        return None


def state_has_changed(current: HandoffState | None, previous: HandoffState | None) -> bool:
    """Detect whether the state has meaningfully changed."""
    if current is None and previous is None:
        return False
    if current is None or previous is None:
        return True
    # Primary: compare updated_at timestamps
    if current.updated_at and previous.updated_at:
        return current.updated_at != previous.updated_at
    # Fallback: content hash
    return current.fingerprint != previous.fingerprint
