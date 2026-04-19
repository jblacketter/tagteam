"""Entry point for `python -m tagteam.tui`."""

from tagteam.tui import tui_command
import sys


if __name__ == "__main__":
    sys.exit(tui_command(sys.argv[1:]))
