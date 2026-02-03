"""Entry point for `python -m ai_handoff.tui`."""

from ai_handoff.tui import tui_command
import sys


if __name__ == "__main__":
    sys.exit(tui_command(sys.argv[1:]))
