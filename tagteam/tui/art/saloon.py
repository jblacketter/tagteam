"""Saloon background ASCII art.

The background is rendered as a grid. Characters are overlaid at fixed positions.
Width target: 80 chars (including border). Height target: ~22 lines.

Layout zones (right side):
  - Rows 3-5:  Bottle shelves on the back wall
  - Rows 6-7:  Gap (rabbit's head pokes up here)
  - Rows 8-16: Open space for rabbit body
  - Row 15:    Bar counter (thin line, rabbit's waist level)
  - Rows 16+:  Below counter (rabbit legs hidden by background)

Layout zones (left side):
  - Rows 4-16: Open space for Mayor
  - Rows 14-17: Table for the map
"""

_W = 78


def _line(content: str = "") -> str:
    """Pad interior to exactly _W chars and wrap with border."""
    padded = content + " " * max(0, _W - len(content))
    return "║" + padded[:_W] + "║"


SALOON = "\n".join([
    "╔" + "═" * _W + "╗",
    _line("  THE HANDOFF SALOON"),
    "║" + "─" * _W + "║",
    # --- back wall: bottle shelves (right side, rows 3-5) ---
    _line("                                            ┌─────────────────────────┐"),
    _line("                                            │ ░ WHISKEY  RYE  CORN  ░ │"),
    _line("                                            └─────────────────────────┘"),
    # --- open space (rows 6-8) ---
    _line(),
    _line(),
    _line(),
    # --- open space (rows 9-11) ---
    _line(),
    _line(),
    _line(),
    # --- open space (rows 12-13) ---
    _line(),
    _line(),
    # --- bar counter surface (row 14) ---
    _line("                                            ══════════════════════════"),
    # --- below counter + table (rows 15-17) ---
    _line("              ┌──────────┐"),
    _line("              │          │"),
    _line("              └──────────┘"),
    # --- floor (row 18) ---
    _line("  ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄"),
    "╚" + "═" * _W + "╝",
])

# Overlay positions (row, col) — must land in open-space regions of SALOON.
MAYOR_POS = (4, 3)     # left side, rows 4-16
CLOCK_POS = (4, 24)    # center wall, rows 4-11
RABBIT_POS = (6, 54)   # right side, rows 6-16 (head above counter, legs below)
TABLE_CONTENT_POS = (16, 16)  # interior of table, 10 chars wide (cols 16-25)
