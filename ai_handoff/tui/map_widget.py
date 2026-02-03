"""Map overlay widget — ASCII trail showing project phase progress.

Toggled with the 'm' key. Floats over the scene as an overlay.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from ai_handoff.tui.map_data import STATUS_COLORS, STATUS_SYMBOLS, PhaseInfo

_MAP_WIDTH = 40
_BORDER_H = "\u2550"  # ═
_BORDER_TL = "\u2554"  # ╔
_BORDER_TR = "\u2557"  # ╗
_BORDER_BL = "\u255a"  # ╚
_BORDER_BR = "\u255d"  # ╝
_BORDER_ML = "\u2560"  # ╠
_BORDER_MR = "\u2563"  # ╣
_BORDER_V = "\u2551"   # ║
_TRAIL = "\u2502"      # │


def _bordered_line(content: str, width: int = _MAP_WIDTH) -> str:
    """Wrap content in border characters, padded to width."""
    padded = content + " " * max(0, width - len(content))
    return f"{_BORDER_V}{padded[:width]}{_BORDER_V}"


class MapWidget(Static):
    """ASCII map overlay showing project phase progress."""

    DEFAULT_CSS = """
    MapWidget {
        layer: overlay;
        width: 44;
        height: auto;
        offset: 2 3;
        background: #1a1207;
        border: heavy #5a4a2a;
        display: none;
    }

    MapWidget.-visible {
        display: block;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._phases: list[PhaseInfo] = []

    def update_phases(self, phases: list[PhaseInfo]) -> None:
        """Re-render the map with updated phase data."""
        self._phases = phases
        self.update(self._render_map())

    def toggle(self) -> None:
        """Show or hide the map overlay."""
        self.toggle_class("-visible")

    @property
    def is_visible(self) -> bool:
        return self.has_class("-visible")

    def _render_map(self) -> Text:
        """Build the ASCII trail map with colored symbols."""
        text = Text()

        if not self._phases:
            text.append(f"{_BORDER_TL}{_BORDER_H * _MAP_WIDTH}{_BORDER_TR}\n")
            text.append(_bordered_line("  No roadmap found.") + "\n")
            text.append(f"{_BORDER_BL}{_BORDER_H * _MAP_WIDTH}{_BORDER_BR}")
            return text

        # Title
        text.append(f"{_BORDER_TL}{_BORDER_H * _MAP_WIDTH}{_BORDER_TR}\n", style="#5a4a2a")

        title = "HANDOFF HOLLOW"
        title_padded = title.center(_MAP_WIDTH)
        text.append(f"{_BORDER_V}", style="#5a4a2a")
        text.append(title_padded, style="bold #c8a45a")
        text.append(f"{_BORDER_V}\n", style="#5a4a2a")

        text.append(f"{_BORDER_ML}{_BORDER_H * _MAP_WIDTH}{_BORDER_MR}\n", style="#5a4a2a")

        # Empty line
        text.append(f"{_BORDER_V}", style="#5a4a2a")
        text.append(" " * _MAP_WIDTH)
        text.append(f"{_BORDER_V}\n", style="#5a4a2a")

        # Phase trail
        for i, phase in enumerate(self._phases):
            symbol = STATUS_SYMBOLS.get(phase.status, "\u25cb")
            color = STATUS_COLORS.get(phase.status, "#5a5a5a")

            # Phase line: "  ✓ foundation          [10/10]"
            # or:         "  ◷ map-artifact  ◀── you are here"
            text.append(f"{_BORDER_V}", style="#5a4a2a")
            text.append("  ")
            text.append(symbol, style=f"bold {color}")
            text.append(" ")

            name_str = phase.name
            text.append(name_str, style=f"{'bold ' if phase.is_current else ''}{color}")

            # Right side annotation
            remaining = _MAP_WIDTH - 4 - len(name_str)
            if phase.is_current:
                if phase.criteria_total > 0:
                    marker = f" [{phase.criteria_done}/{phase.criteria_total}] \u25c0"
                else:
                    marker = " \u25c0"
                pad = remaining - len(marker)
                text.append(" " * max(1, pad))
                text.append(marker, style=f"bold {color}")
            elif phase.status not in ("not-started",) and phase.criteria_total > 0:
                count_str = f" [{phase.criteria_done}/{phase.criteria_total}]"
                pad = remaining - len(count_str)
                text.append(" " * max(1, pad))
                text.append(count_str, style=f"dim {color}")
            else:
                text.append(" " * max(0, remaining))

            text.append(f"{_BORDER_V}\n", style="#5a4a2a")

            # Trail connector (except after last phase)
            if i < len(self._phases) - 1:
                text.append(f"{_BORDER_V}", style="#5a4a2a")
                text.append(f"  {_TRAIL}")
                text.append(" " * (_MAP_WIDTH - 3))
                text.append(f"{_BORDER_V}\n", style="#5a4a2a")

        # Empty line + bottom border
        text.append(f"{_BORDER_V}", style="#5a4a2a")
        text.append(" " * _MAP_WIDTH)
        text.append(f"{_BORDER_V}\n", style="#5a4a2a")

        text.append(f"{_BORDER_BL}{_BORDER_H * _MAP_WIDTH}{_BORDER_BR}", style="#5a4a2a")

        return text
