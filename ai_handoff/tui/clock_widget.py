"""Dynamic cuckoo clock — generates clock ASCII art from handoff state.

The clock face shows a status symbol, round number, and color-codes
by status. Replaces the static CLOCK constant from art/clock.py.
"""

from __future__ import annotations

from ai_handoff.tui.state_watcher import HandoffState

# Status symbol shown on the clock face
_STATUS_SYMBOLS = {
    "ready": "●",
    "working": "◷",
    "done": "✓",
    "escalated": "!",
    "aborted": "×",
}

# Color per status
STATUS_COLORS = {
    "ready": "#d4a04a",
    "working": "#6a8eae",
    "done": "#7a9e6b",
    "escalated": "#ae6a6a",
    "aborted": "#5a5a5a",
}

# Turn indicator: arrow direction
_TURN_INDICATORS = {
    "lead": "◀",     # points left (toward Mayor)
    "reviewer": "▶",  # points right (toward Rabbit)
}


def generate_clock(
    state: HandoffState | None,
    tick: bool = False,
    cuckoo_frame: int | None = None,
    cuckoo_message: str = "",
) -> str:
    """Generate clock ASCII art reflecting the current handoff state.

    Args:
        state: Current handoff state, or None for idle clock.
        tick: Alternates the pendulum position for animation.
        cuckoo_frame: Animation frame — ``0`` for pop-out, ``None`` for idle.
        cuckoo_message: Short text shown in the cuckoo speech bubble.
    """
    if state is None or state.is_empty:
        # Idle clock — same as the original static art
        return (
            "  ╔════╗\n"
            "  ║ ⌂  ║\n"
            "  ║────║\n"
            "  ║ ◷  ║\n"
            "  ║    ║\n"
            "  ╚╤══╤╝\n"
            "   │  │\n"
            "   ◯  ◯"
        )

    symbol = _STATUS_SYMBOLS.get(state.status, "◷")
    turn_arrow = _TURN_INDICATORS.get(state.turn, " ")
    round_str = f"R{state.round}" if state.round else "  "

    # Pad round to 2 chars, center in 4-char space
    round_display = round_str.center(4)

    # Cuckoo: pop-out animation when cuckoo_frame == 0
    if cuckoo_frame == 0 and cuckoo_message:
        cuckoo_line = f"  ⌂ {cuckoo_message}"
        clock_top = f"{cuckoo_line}\n  ╔════╗\n"
    else:
        clock_top = f"  ╔════╗\n  ║ ⌂  ║\n"

    # Pendulum animation
    if tick:
        pendulum = "   ◯  ◯"
    else:
        pendulum = "  ◯  ◯ "

    return (
        f"{clock_top}"
        f"  ║────║\n"
        f"  ║ {symbol}{turn_arrow} ║\n"
        f"  ║{round_display}║\n"
        f"  ╚╤══╤╝\n"
        f"   │  │\n"
        f"{pendulum}"
    )


def clock_color(state: HandoffState | None) -> str:
    """Return the color for the clock based on current status."""
    if state is None or state.is_empty:
        return "#c8a45a"
    return STATUS_COLORS.get(state.status, "#c8a45a")
