"""Status bar widget — shows handoff state as a text HUD.

Docked between the scene and the dialogue panel. Hidden when
no handoff state exists.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from tagteam.tui.clock_widget import STATUS_COLORS
from tagteam.tui.state_watcher import HandoffState

# Map agent role to display name
_AGENT_NAMES = {
    "lead": "Mayor",
    "reviewer": "Rabbit",
}

_MAX_ACTION_LEN = 25


class StatusBar(Static):
    """Thin status bar showing handoff state."""

    DEFAULT_CSS = """
    StatusBar {
        width: 1fr;
        height: 1;
        background: #0f0a04;
        color: #8a7a5a;
        padding: 0 1;
        dock: bottom;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._stale = False

    def on_mount(self) -> None:
        self.display = False

    def set_stale(self, stale: bool) -> None:
        """Mark the status bar as showing stale data."""
        self._stale = stale

    def update_state(
        self, state: HandoffState | None, last_action: str | None = None
    ) -> None:
        """Update the status bar with new handoff state."""
        if state is None or state.is_empty:
            self.display = False
            return

        self.display = True

        color = STATUS_COLORS.get(state.status, "#8a7a5a")
        turn_name = _AGENT_NAMES.get(state.turn, state.turn)

        text = Text()

        if self._stale:
            text.append(" [STALE] ", style="bold #cc4444")

        text.append(" Phase: ", style="#5a4a2a")
        text.append(state.phase or "—", style="#8a7a5a")
        text.append("  │  ", style="#3a2a1a")
        text.append("Round: ", style="#5a4a2a")
        round_str = str(state.round) if state.round else "—"
        text.append(round_str, style="#8a7a5a")
        text.append("  │  ", style="#3a2a1a")
        text.append("Turn: ", style="#5a4a2a")
        text.append(turn_name, style=color)
        text.append("  │  ", style="#3a2a1a")
        text.append("Status: ", style="#5a4a2a")
        text.append(state.status, style=f"bold {color}")

        if state.result:
            text.append("  │  ", style="#3a2a1a")
            text.append(state.result, style=f"italic {color}")

        if last_action:
            truncated = last_action if len(last_action) <= _MAX_ACTION_LEN else last_action[:_MAX_ACTION_LEN - 3] + "..."
            text.append("  │  ", style="#3a2a1a")
            text.append("Last: ", style="#5a4a2a")
            text.append(truncated, style="#8a7a5a")

        self.update(text)
