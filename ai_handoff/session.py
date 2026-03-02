"""
Session management for handoff orchestration.

Supports two backends:
- iterm2 (default): Creates iTerm2 tabs via AppleScript
- tmux: Creates tmux session with named panes (legacy)
"""

import subprocess
import sys

SESSION_NAME = "ai-handoff"


# --- tmux backend ---

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


def create_tmux_session(project_dir: str | None = None) -> bool:
    """Create tmux session with lead, watcher, and reviewer panes.

    Layout (3 equal columns):
        ┌──────────┬──────────┬──────────┐
        │ 0: lead  │ 1: watch │ 2: review│
        └──────────┴──────────┴──────────┘

    Enables mouse mode so you can click to switch panes
    (TUI agents capture Ctrl-b, making keyboard navigation unreliable).
    """
    if session_exists():
        print(f"Session '{SESSION_NAME}' already exists.")
        print(f"  Attach: tmux attach -t {SESSION_NAME}")
        print(f"  Kill:   tmux kill-session -t {SESSION_NAME}")
        return False

    start_dir = project_dir or "."

    try:
        # Create session with lead pane (pane 0)
        _tmux("new-session", "-d", "-s", SESSION_NAME, "-n", "handoff",
              "-c", start_dir)

        # Split right for watcher (pane 1)
        _tmux("split-window", "-h", "-t", f"{SESSION_NAME}:0.0",
              "-c", start_dir)

        # Split right again for reviewer (pane 2)
        _tmux("split-window", "-h", "-t", f"{SESSION_NAME}:0.1",
              "-c", start_dir)

        # Even out the columns
        _tmux("select-layout", "-t", f"{SESSION_NAME}:0", "even-horizontal")

        # Label panes
        _tmux("select-pane", "-t", f"{SESSION_NAME}:0.0", "-T", "CLAUDE (Lead)")
        _tmux("select-pane", "-t", f"{SESSION_NAME}:0.1", "-T", "WATCHER")
        _tmux("select-pane", "-t", f"{SESSION_NAME}:0.2", "-T", "CODEX (Reviewer)")

        # Show pane titles in borders
        _tmux("set-option", "-t", SESSION_NAME, "pane-border-status", "top")
        _tmux("set-option", "-t", SESSION_NAME, "pane-border-format",
              " #{pane_title} ")

        # Enable mouse mode — click to switch panes
        # (TUI agents like Claude Code/Codex capture Ctrl-b)
        _tmux("set-option", "-t", SESSION_NAME, "mouse", "on")

        # Pre-type the watcher command (don't auto-start)
        _tmux("send-keys", "-t", f"{SESSION_NAME}:0.1",
              "python -m ai_handoff watch --mode tmux", "")

        # Select the lead pane
        _tmux("select-pane", "-t", f"{SESSION_NAME}:0.0")

        print(f"Created tmux session '{SESSION_NAME}'")
        print()
        print("  Pane 0 (left):   Lead agent   - start Claude Code here")
        print("  Pane 1 (center): Watcher      - press Enter to start")
        print("  Pane 2 (right):  Reviewer     - start Codex here")
        print()
        print("  Mouse mode is ON — click a pane to switch to it")
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


# Keep old name as alias for backward compatibility
create_session = create_tmux_session


# --- CLI entry point ---

def _parse_backend(args: list[str]) -> tuple[str, list[str]]:
    """Extract --backend flag from args, return (backend, remaining_args)."""
    backend = "iterm2"
    remaining = []
    i = 0
    while i < len(args):
        if args[i] == "--backend" and i + 1 < len(args):
            backend = args[i + 1]
            if backend not in ("iterm2", "tmux"):
                print(f"Invalid backend: {backend}. Use 'iterm2' or 'tmux'.")
                sys.exit(1)
            i += 2
        else:
            remaining.append(args[i])
            i += 1
    return backend, remaining


def session_command(args: list[str]) -> int:
    """Handle `python -m ai_handoff session [subcommand]`."""
    if not args:
        print("Usage: python -m ai_handoff session <command> [--backend iterm2|tmux]")
        print()
        print("Commands:")
        print("  start   Create session with lead/reviewer/watcher tabs (default: iTerm2)")
        print("  kill    Kill the session")
        print("  attach  Attach to existing tmux session (tmux backend only)")
        print()
        print("Options:")
        print("  --backend iterm2|tmux  Backend to use (default: iterm2)")
        print("  --dir PATH            Project directory")
        return 1

    backend, remaining = _parse_backend(args)
    subcmd = remaining[0] if remaining else ""

    if subcmd == "start":
        project_dir = None
        if len(remaining) > 1 and remaining[1] == "--dir" and len(remaining) > 2:
            project_dir = remaining[2]

        if backend == "iterm2":
            from ai_handoff.iterm import create_session as create_iterm_session
            create_iterm_session(project_dir or ".")
        else:
            create_tmux_session(project_dir=project_dir)
        return 0

    if subcmd == "attach":
        if backend == "iterm2":
            print("The 'attach' command is not needed for iTerm2 (tabs are already visible).")
            return 0
        if not session_exists():
            print(f"No session '{SESSION_NAME}' found. Run 'session start' first.")
            return 1
        subprocess.run(["tmux", "attach", "-t", SESSION_NAME])
        return 0

    if subcmd == "kill":
        if backend == "iterm2":
            from ai_handoff.iterm import kill_session
            project_dir = None
            if len(remaining) > 1 and remaining[1] == "--dir" and len(remaining) > 2:
                project_dir = remaining[2]
            kill_session(project_dir or ".")
        else:
            if not session_exists():
                print(f"No session '{SESSION_NAME}' found.")
                return 0
            _tmux("kill-session", "-t", SESSION_NAME)
            print(f"Session '{SESSION_NAME}' killed.")
        return 0

    print(f"Unknown session subcommand: {subcmd}")
    return 1
