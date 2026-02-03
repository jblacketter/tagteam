"""Handoff Saloon TUI — A Monkey Island-inspired visual frontend for AI agent collaboration."""

# Import textual at package level so that `from ai_handoff.tui import tui_command`
# raises ImportError immediately when textual is not installed. This lets the
# try/except in cli.py print a friendly install message instead of a stack trace.
import textual as _textual  # noqa: F401

import os
import sys


def tui_command(args: list[str]) -> int:
    """Handle `python -m ai_handoff tui [--dir PATH] [--sound]`."""
    project_dir = None
    sound = False

    i = 0
    while i < len(args):
        if args[i] == "--dir" and i + 1 < len(args):
            project_dir = os.path.expanduser(args[i + 1])
            i += 2
        elif args[i] == "--sound":
            sound = True
            i += 1
        elif args[i] in ("-h", "--help"):
            print("Usage: python -m ai_handoff tui [--dir DIR] [--sound]")
            print()
            print("  --dir DIR   Project directory to watch (default: current directory)")
            print("  --sound     Enable sound effects")
            return 0
        else:
            print(f"Unknown argument: {args[i]}")
            return 1

    if sound:
        os.environ["GAMERFY_SOUND"] = "1"

    # Resolve project directory
    if project_dir is None:
        # Check if cwd has ai-handoff.yaml
        if os.path.exists("ai-handoff.yaml"):
            project_dir = os.getcwd()
        # Otherwise leave as None — the TUI intro will handle first-time setup

    if project_dir is not None:
        project_dir = os.path.abspath(project_dir)

    from ai_handoff.tui.app import SaloonApp

    app = SaloonApp(project_dir=project_dir)
    app.run()
    return 0
