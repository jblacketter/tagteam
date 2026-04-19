"""
Session management for handoff orchestration.

Supports three backends:
- iterm2: Creates iTerm2 tabs via AppleScript on macOS
- tmux: Creates a tmux session with named panes
- manual: Prints the commands for a manual multi-terminal workflow
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

SESSION_NAME = "tagteam"
SUPPORTED_BACKENDS = ("iterm2", "tmux", "manual")
PRIME_MESSAGE = (
    "Read tagteam.yaml to see your role, then read"
    " .claude/skills/handoff/SKILL.md for the workflow."
)


def _backend_choices_text() -> str:
    return "'iterm2', 'tmux', or 'manual'"


def _tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a tmux command."""
    return subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
        check=check,
    )


_ITERM_APP_PATHS = (
    "/Applications/iTerm.app",
    str(Path.home() / "Applications" / "iTerm.app"),
)


def _iterm2_supported() -> bool:
    if sys.platform != "darwin" or shutil.which("osascript") is None:
        return False
    # Only claim iTerm2 support when the app is actually installed,
    # so default_backend() can honestly fall through to tmux on Macs
    # without iTerm2.
    return any(Path(p).exists() for p in _ITERM_APP_PATHS)


def _tmux_supported() -> bool:
    return shutil.which("tmux") is not None


def default_backend() -> str:
    """Choose the best available session backend for this machine."""
    if _iterm2_supported():
        return "iterm2"
    if _tmux_supported():
        return "tmux"
    return "manual"


def session_exists() -> bool:
    """Return True when the default tmux session exists."""
    try:
        result = _tmux("has-session", "-t", SESSION_NAME, check=False)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _print_invalid_backend(backend: str) -> None:
    print(f"Invalid backend: {backend}. Use {_backend_choices_text()}.")


def _print_backend_unavailable(backend: str) -> None:
    if backend == "iterm2":
        if sys.platform != "darwin":
            print("iTerm2 session management is only available on macOS.")
        else:
            print("iTerm2 session management requires AppleScript and iTerm2.")
        print("  Use '--backend manual' for the manual workflow.")
        if _tmux_supported():
            print("  Or use '--backend tmux' if you prefer tmux.")
        return

    if backend == "tmux":
        print("tmux session management is not available on this platform.")
        print("  Use '--backend manual' for manual coordination.")
        if sys.platform.startswith("win"):
            print("  For full automation on Windows today, run under WSL with tmux.")
        else:
            print("  Or install tmux and retry.")


def _validate_backend(backend: str) -> bool:
    if backend not in SUPPORTED_BACKENDS:
        _print_invalid_backend(backend)
        return False

    if backend == "iterm2" and not _iterm2_supported():
        _print_backend_unavailable("iterm2")
        return False

    if backend == "tmux" and not _tmux_supported():
        _print_backend_unavailable("tmux")
        return False

    return True


def _read_launch_commands(project_dir: str | None) -> tuple[str, str] | None:
    """Read launch commands from tagteam.yaml via centralized config."""
    try:
        from tagteam.config import get_launch_commands, read_config
    except ImportError:
        return None

    config_path = Path(project_dir or ".") / "tagteam.yaml"
    config = read_config(config_path)
    if not config:
        print("Warning: tagteam.yaml not found; skipping auto-launch.")
        print("  Run 'python -m tagteam init' to create it.")
        return None
    return get_launch_commands(config)


def create_tmux_session(project_dir: str | None = None, launch: bool = False) -> bool:
    """Create a tmux session with lead, watcher, and reviewer panes."""
    if session_exists():
        print(f"Session '{SESSION_NAME}' already exists.")
        print(f"  Attach: tmux attach -t {SESSION_NAME}")
        print(f"  Kill:   tmux kill-session -t {SESSION_NAME}")
        return False

    start_dir = project_dir or "."

    try:
        _tmux("new-session", "-d", "-s", SESSION_NAME, "-n", "handoff", "-c", start_dir)
        _tmux("split-window", "-h", "-t", f"{SESSION_NAME}:0.0", "-c", start_dir)
        _tmux("split-window", "-h", "-t", f"{SESSION_NAME}:0.1", "-c", start_dir)
        _tmux("select-layout", "-t", f"{SESSION_NAME}:0", "even-horizontal")

        _tmux("select-pane", "-t", f"{SESSION_NAME}:0.0", "-T", "CLAUDE (Lead)")
        _tmux("select-pane", "-t", f"{SESSION_NAME}:0.1", "-T", "WATCHER")
        _tmux("select-pane", "-t", f"{SESSION_NAME}:0.2", "-T", "CODEX (Reviewer)")

        _tmux("set-option", "-t", SESSION_NAME, "pane-border-status", "top")
        _tmux("set-option", "-t", SESSION_NAME, "pane-border-format", " #{pane_title} ")
        _tmux("set-option", "-t", SESSION_NAME, "mouse", "on")

        cmds = None
        if launch:
            cmds = _read_launch_commands(project_dir)
            if cmds:
                lead_cmd, reviewer_cmd = cmds
                _tmux("send-keys", "-t", f"{SESSION_NAME}:0.0", lead_cmd, "Enter")
                _tmux(
                    "send-keys",
                    "-t",
                    f"{SESSION_NAME}:0.1",
                    "python -m tagteam watch --mode tmux",
                    "Enter",
                )
                _tmux("send-keys", "-t", f"{SESSION_NAME}:0.2", reviewer_cmd, "Enter")
                print("  Waiting for agents to start before priming...")
                time.sleep(3)
                _tmux("send-keys", "-t", f"{SESSION_NAME}:0.0", PRIME_MESSAGE, "Enter")
                time.sleep(1)
                _tmux("send-keys", "-t", f"{SESSION_NAME}:0.2", PRIME_MESSAGE, "Enter")
            else:
                _tmux(
                    "send-keys",
                    "-t",
                    f"{SESSION_NAME}:0.1",
                    "python -m tagteam watch --mode tmux",
                    "",
                )
        else:
            _tmux(
                "send-keys",
                "-t",
                f"{SESSION_NAME}:0.1",
                "python -m tagteam watch --mode tmux",
                "",
            )

        _tmux("select-pane", "-t", f"{SESSION_NAME}:0.0")

        launched = " (launched)" if launch else ""
        print(f"Created tmux session '{SESSION_NAME}'{launched}")
        print()
        if launch and cmds:
            print(f"  Pane 0 (left):   Lead agent   - {cmds[0]}")
            print("  Pane 1 (center): Watcher      - running")
            print(f"  Pane 2 (right):  Reviewer     - {cmds[1]}")
        else:
            print("  Pane 0 (left):   Lead agent   - start your lead agent here")
            print("  Pane 1 (center): Watcher      - press Enter to start")
            print("  Pane 2 (right):  Reviewer     - start your reviewer agent here")
        print()
        print("  Mouse mode is ON; click a pane to switch to it")
        print()
        print(f"  Attach: tmux attach -t {SESSION_NAME}")
        return True

    except FileNotFoundError:
        _print_backend_unavailable("tmux")
        return False
    except subprocess.CalledProcessError as exc:
        print(f"Error creating session: {exc.stderr.strip()}")
        return False


def create_manual_session(project_dir: str | None = None, launch: bool = False) -> bool:
    """Print instructions for a manual multi-terminal workflow."""
    start_dir = str(Path(project_dir or ".").resolve())
    cmds = _read_launch_commands(project_dir) if launch else None

    print("Manual session backend selected.")
    print(f"  Project: {start_dir}")
    if launch:
        print("  Auto-launch is not available for the manual backend.")
    print()
    print("Open three terminals in this project directory and run:")

    if cmds:
        lead_cmd, reviewer_cmd = cmds
        print(f"  Lead terminal:     {lead_cmd}")
        print("  Watcher terminal:  python -m tagteam watch --mode notify")
        print(f"  Reviewer terminal: {reviewer_cmd}")
        print()
        print("Then send both agents this priming message:")
        print(f'  "{PRIME_MESSAGE}"')
    else:
        print("  Lead terminal:     start your lead agent")
        print("  Watcher terminal:  python -m tagteam watch --mode notify")
        print("  Reviewer terminal: start your reviewer agent")

    print()
    print("The watcher will log turn changes and the command to run next.")
    print("For automated terminal orchestration, use macOS + iTerm2 or tmux on PATH.")
    if sys.platform.startswith("win"):
        print("On Windows today, WSL + tmux is the supported automation path.")
    return True



def ensure_session(
    project_dir: str,
    backend: str | None = None,
    launch: bool = False,
) -> str:
    """Create or reuse a session. Returns one of: created, exists, manual, error."""
    backend = backend or default_backend()
    if not _validate_backend(backend):
        return "error"

    if backend == "manual":
        ok = create_manual_session(project_dir=project_dir, launch=launch)
        return "manual" if ok else "error"

    if backend == "tmux":
        if session_exists():
            print(f"Session '{SESSION_NAME}' already exists; attaching.")
            subprocess.run(["tmux", "attach", "-t", SESSION_NAME])
            return "exists"
        ok = create_tmux_session(project_dir=project_dir, launch=launch)
        return "created" if ok else "error"

    from tagteam.iterm import (
        _any_session_alive,
        _find_session_file,
        _read_session_file,
        _session_file_path,
        create_session as create_iterm_session,
    )

    existing = _read_session_file(project_dir)
    if existing:
        if _any_session_alive(existing):
            print("iTerm2 session already exists; skipping session creation.")
            return "exists"
        stale_path = _find_session_file(project_dir) or _session_file_path(project_dir)
        print(f"Stale iTerm2 session file (no live tabs): {stale_path}")
        print("  Removing and creating a fresh session.")
        try:
            stale_path.unlink()
        except OSError:
            pass

    ok = create_iterm_session(project_dir, launch=launch)
    return "created" if ok else "error"


def _parse_backend(args: list[str]) -> tuple[str | None, list[str]]:
    """Extract --backend flag from args, return (backend, remaining_args)."""
    backend = None
    remaining = []
    i = 0
    while i < len(args):
        if args[i] == "--backend":
            if i + 1 >= len(args):
                print("--backend requires a value.")
                sys.exit(1)
            backend = args[i + 1]
            if backend not in SUPPORTED_BACKENDS:
                _print_invalid_backend(backend)
                sys.exit(1)
            i += 2
        else:
            remaining.append(args[i])
            i += 1
    return backend, remaining


def session_command(args: list[str]) -> int:
    """Handle `python -m tagteam session [subcommand]`."""
    if not args:
        print("Usage: python -m tagteam session <command> [options]")
        print()
        print("Commands:")
        print("  start   Create or describe an orchestration session")
        print("  kill    Kill the managed tmux/iTerm2 session")
        print("  attach  Attach to an existing tmux session")
        print()
        print("Options:")
        print(
            "  --backend iterm2|tmux|manual  Backend to use"
            " (default: auto-detect)"
        )
        print("  --dir PATH                    Project directory (default: .)")
        print("  --no-launch                   Skip auto-starting agents")
        return 1

    backend, remaining = _parse_backend(args)
    subcmd = remaining[0] if remaining else ""

    if subcmd == "start":
        project_dir = None
        launch = True
        i = 1
        while i < len(remaining):
            if remaining[i] == "--dir" and i + 1 < len(remaining):
                project_dir = remaining[i + 1]
                i += 2
            elif remaining[i] == "--launch":
                i += 1
            elif remaining[i] == "--no-launch":
                launch = False
                i += 1
            else:
                i += 1

        if launch:
            resolved_dir = project_dir or "."
            from tagteam.setup import needs_setup, run_setup

            if needs_setup(resolved_dir):
                run_setup(resolved_dir)

            from tagteam.cli import needs_init, run_init

            if needs_init(resolved_dir):
                if not run_init(resolved_dir):
                    print("Continuing without --launch (no config).")
                    launch = False

        outcome = ensure_session(project_dir or ".", backend, launch=launch)
        return 0 if outcome != "error" else 1

    effective_backend = backend or default_backend()

    if subcmd == "attach":
        if effective_backend == "iterm2":
            print("The 'attach' command is not needed for iTerm2 (tabs are already visible).")
            return 0
        if effective_backend == "manual":
            print("The manual backend does not manage terminal sessions to attach to.")
            return 0
        if not _validate_backend(effective_backend):
            return 1
        if not session_exists():
            print(f"No session '{SESSION_NAME}' found. Run 'session start' first.")
            return 1
        subprocess.run(["tmux", "attach", "-t", SESSION_NAME])
        return 0

    if subcmd == "kill":
        if effective_backend == "iterm2":
            from tagteam.iterm import kill_session

            project_dir = None
            if len(remaining) > 2 and remaining[1] == "--dir":
                project_dir = remaining[2]
            kill_session(project_dir or ".")
            return 0

        if effective_backend == "manual":
            print("The manual backend does not create managed sessions to kill.")
            return 0

        if not _validate_backend(effective_backend):
            return 1
        if not session_exists():
            print(f"No session '{SESSION_NAME}' found.")
            return 0
        _tmux("kill-session", "-t", SESSION_NAME)
        print(f"Session '{SESSION_NAME}' killed.")
        return 0

    print(f"Unknown session subcommand: {subcmd}")
    return 1
