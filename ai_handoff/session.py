"""
tmux session management for handoff orchestration.

Creates a tmux session with named panes for the lead agent,
reviewer agent, and watcher daemon.
"""

import subprocess
import sys

SESSION_NAME = "ai-handoff"


def _tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a tmux command."""
    return subprocess.run(
        ["tmux", *args],
        capture_output=True, text=True,
        check=check,
    )


def session_exists() -> bool:
    result = _tmux("has-session", "-t", SESSION_NAME, check=False)
    return result.returncode == 0


def create_session() -> bool:
    """Create tmux session with lead, reviewer, and watcher panes.

    Layout:
        ┌──────────────┬──────────────┐
        │   0: lead    │  1: reviewer │
        ├──────────────┴──────────────┤
        │         2: watcher          │
        └─────────────────────────────┘
    """
    if session_exists():
        print(f"Session '{SESSION_NAME}' already exists.")
        print(f"  Attach: tmux attach -t {SESSION_NAME}")
        print(f"  Kill:   tmux kill-session -t {SESSION_NAME}")
        return False

    try:
        # Create session with lead pane (pane 0)
        _tmux("new-session", "-d", "-s", SESSION_NAME, "-n", "handoff")

        # Split bottom for watcher pane (pane 1, 20% height)
        _tmux("split-window", "-v", "-t", f"{SESSION_NAME}:0.0",
              "-l", "20%")

        # Go back to top pane and split right for reviewer (pane 2)
        _tmux("split-window", "-h", "-t", f"{SESSION_NAME}:0.0")

        # After splits, pane indices are:
        #   0 = lead (top-left)
        #   1 = watcher (bottom, from the vertical split)
        #   2 = reviewer (top-right, from the horizontal split)

        # Pre-type the watcher command in the bottom pane (don't auto-start)
        _tmux("send-keys", "-t", f"{SESSION_NAME}:0.1",
              "python -m ai_handoff watch --mode tmux", "")

        # Select the lead pane
        _tmux("select-pane", "-t", f"{SESSION_NAME}:0.0")

        print(f"Created tmux session '{SESSION_NAME}'")
        print()
        print("  Pane 0 (top-left):  Lead agent      - start Claude Code here")
        print("  Pane 1 (bottom):    Watcher daemon   - press Enter to start")
        print("  Pane 2 (top-right): Reviewer agent   - start Codex here")
        print()
        print(f"  Attach: tmux attach -t {SESSION_NAME}")
        return True

    except FileNotFoundError:
        print("Error: tmux is not installed.")
        print("  Install: brew install tmux")
        return False
    except subprocess.CalledProcessError as e:
        print(f"Error creating session: {e.stderr.strip()}")
        return False


# --- CLI entry point ---

def session_command(args: list[str]) -> int:
    """Handle `python -m ai_handoff session [subcommand]`."""
    if not args:
        print("Usage: python -m ai_handoff session <command>")
        print()
        print("Commands:")
        print("  start   Create tmux session with lead/reviewer/watcher panes")
        print("  attach  Attach to existing session")
        print("  kill    Kill the session")
        return 1

    subcmd = args[0]

    if subcmd == "start":
        create_session()
        return 0

    if subcmd == "attach":
        if not session_exists():
            print(f"No session '{SESSION_NAME}' found. Run 'session start' first.")
            return 1
        subprocess.run(["tmux", "attach", "-t", SESSION_NAME])
        return 0

    if subcmd == "kill":
        if not session_exists():
            print(f"No session '{SESSION_NAME}' found.")
            return 0
        _tmux("kill-session", "-t", SESSION_NAME)
        print(f"Session '{SESSION_NAME}' killed.")
        return 0

    print(f"Unknown session subcommand: {subcmd}")
    return 1
