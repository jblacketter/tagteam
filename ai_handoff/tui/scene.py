"""Scene rendering widget.

Composites the saloon background with character sprites and the cuckoo clock
at fixed positions. Renders as a Textual Static widget with Rich markup for colors.

The clock can be updated dynamically to reflect handoff state.
Supports cuckoo pop-out animation and pigeon handoff animation.
"""

from __future__ import annotations

from rich.text import Text
from textual.timer import Timer
from textual.widgets import Static

from ai_handoff.tui.art.saloon import CLOCK_POS, MAYOR_POS, RABBIT_POS, SALOON, TABLE_CONTENT_POS
from ai_handoff.tui.characters import MAYOR_CHARACTER, RABBIT_CHARACTER
from ai_handoff.tui.clock_widget import clock_color, generate_clock
from ai_handoff.tui.map_data import PhaseInfo, compact_indicator
from ai_handoff.tui.state_watcher import HandoffState

# Pigeon art by direction
_PIGEON_RIGHT = ">o>\u2261"  # >o>≡
_PIGEON_LEFT = "\u2261<o<"   # ≡<o<

# Pigeon row — flies along the bar counter area
_PIGEON_ROW = 12

# Cuckoo status messages
_CUCKOO_MESSAGES = {
    "ready": "Turn!",
    "done": "Done!",
    "escalated": "Help!",
    "aborted": "Stop!",
}


def _overlay(base_lines: list[list[str]], sprite: str, row: int, col: int) -> None:
    """Overlay a sprite onto the base grid at (row, col).

    Characters in the sprite replace base characters, except spaces
    which are treated as transparent.
    """
    for dy, line in enumerate(sprite.splitlines()):
        target_row = row + dy
        if target_row < 0 or target_row >= len(base_lines):
            continue
        for dx, ch in enumerate(line):
            target_col = col + dx
            if target_col < 0 or target_col >= len(base_lines[target_row]):
                continue
            if ch != " ":
                base_lines[target_row][target_col] = ch


def compose_scene(
    state: HandoffState | None = None,
    tick: bool = False,
    phases: list[PhaseInfo] | None = None,
    cuckoo_frame: int | None = None,
    cuckoo_message: str = "",
    pigeon_col: int | None = None,
    pigeon_right: bool = True,
) -> str:
    """Build the full saloon scene as a plain string with overlays applied."""
    lines = [list(line) for line in SALOON.splitlines()]

    _overlay(lines, MAYOR_CHARACTER.sprite, *MAYOR_POS)
    clock_art = generate_clock(
        state, tick=tick, cuckoo_frame=cuckoo_frame, cuckoo_message=cuckoo_message
    )
    _overlay(lines, clock_art, *CLOCK_POS)
    _overlay(lines, RABBIT_CHARACTER.sprite, *RABBIT_POS)

    # Compact table indicator
    if phases:
        indicator = compact_indicator(phases)
        _overlay(lines, indicator, *TABLE_CONTENT_POS)

    # Pigeon overlay
    if pigeon_col is not None:
        pigeon_art = _PIGEON_RIGHT if pigeon_right else _PIGEON_LEFT
        _overlay(lines, pigeon_art, _PIGEON_ROW, pigeon_col)

    return "\n".join("".join(row) for row in lines)


class SceneWidget(Static):
    """Textual widget that displays the composed saloon scene."""

    DEFAULT_CSS = """
    SceneWidget {
        width: 1fr;
        height: auto;
        color: #d4a04a;
        background: #1a1207;
        padding: 0;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._state: HandoffState | None = None
        self._tick = False
        self._phases: list[PhaseInfo] | None = None
        # Cuckoo animation
        self._cuckoo_frame: int | None = None
        self._cuckoo_message: str = ""
        self._cuckoo_timer: Timer | None = None
        self._cuckoo_animating = False
        # Pigeon animation
        self._pigeon_col: int | None = None
        self._pigeon_right = True
        self._pigeon_target: int = 0
        self._pigeon_timer: Timer | None = None

    def on_mount(self) -> None:
        self.update(self._render_scene())

    def update_state(self, state: HandoffState | None) -> None:
        """Update the scene with new handoff state (re-renders the clock)."""
        self._state = state
        self._tick = not self._tick
        self.update(self._render_scene())

    def update_phases(self, phases: list[PhaseInfo]) -> None:
        """Update the scene with new phase data (re-renders the table indicator)."""
        self._phases = phases
        self.update(self._render_scene())

    # --- Cuckoo animation ---

    def animate_cuckoo(self, status: str) -> None:
        """Trigger the cuckoo pop-out animation for a state change."""
        message = _CUCKOO_MESSAGES.get(status)
        if not message or self._cuckoo_animating:
            return

        self._cuckoo_animating = True
        self._cuckoo_frame = 0
        self._cuckoo_message = message
        self.update(self._render_scene())

        # Schedule retract after hold
        if self._cuckoo_timer is not None:
            self._cuckoo_timer.stop()
        self._cuckoo_timer = self.set_timer(1.5, self._cuckoo_retract)

    def _cuckoo_retract(self) -> None:
        """Retract the cuckoo back into the clock."""
        self._cuckoo_frame = None
        self._cuckoo_message = ""
        self._cuckoo_animating = False
        self._cuckoo_timer = None
        self.update(self._render_scene())

    # --- Pigeon animation ---

    def fly_pigeon(self, from_col: int, to_col: int) -> None:
        """Start a pigeon flying across the scene."""
        if self._pigeon_timer is not None:
            self._pigeon_timer.stop()
            self._pigeon_timer = None

        self._pigeon_right = to_col > from_col
        self._pigeon_col = from_col
        self._pigeon_target = to_col
        self.update(self._render_scene())

        self._pigeon_timer = self.set_interval(0.15, self._pigeon_advance)

    def _pigeon_advance(self) -> None:
        """Move the pigeon one step toward its target."""
        if self._pigeon_col is None:
            self._pigeon_stop()
            return

        step = 6 if self._pigeon_right else -6
        self._pigeon_col += step

        # Check if pigeon reached or passed the target
        if self._pigeon_right and self._pigeon_col >= self._pigeon_target:
            self._pigeon_stop()
        elif not self._pigeon_right and self._pigeon_col <= self._pigeon_target:
            self._pigeon_stop()
        else:
            self.update(self._render_scene())

    def _pigeon_stop(self) -> None:
        """Stop the pigeon animation."""
        self._pigeon_col = None
        if self._pigeon_timer is not None:
            self._pigeon_timer.stop()
            self._pigeon_timer = None
        self.update(self._render_scene())

    # --- Rendering ---

    def _render_scene(self) -> Text:
        scene_str = compose_scene(
            self._state,
            tick=self._tick,
            phases=self._phases,
            cuckoo_frame=self._cuckoo_frame,
            cuckoo_message=self._cuckoo_message,
            pigeon_col=self._pigeon_col,
            pigeon_right=self._pigeon_right,
        )
        color = clock_color(self._state)
        text = Text()
        text.append(scene_str, style=f"bold {color if self._state and not self._state.is_empty else '#c8a45a'}")
        return text
