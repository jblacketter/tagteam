"""
Session management for handoff orchestration.

Supports four backends:
- iterm2: Creates iTerm2 tabs via AppleScript on macOS
- tmux: Creates a tmux session with named panes
- terminal: Creates Terminal.app windows via AppleScript on macOS (opt-in;
  no install needed)
- manual: Prints the commands for a manual multi-terminal workflow
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

SESSION_NAME = "tagteam"
SUPPORTED_BACKENDS = ("iterm2", "tmux", "terminal", "manual")
_TAB_BACKEND_LABELS = {"iterm2": "iTerm2", "terminal": "Terminal.app"}
TAB_BACKENDS_FOR_MESSAGES = tuple(_TAB_BACKEND_LABELS)
from tagteam.contract import CONTRACT_HOWTO  # noqa: E402

PRIME_MESSAGE = (
    "Read tagteam.yaml to see your role, project instructions in AGENTS.md "
    "and CLAUDE.md if present, and docs/workflows.md, "
    f"then read {CONTRACT_HOWTO} for the workflow."
)

# Markers that an agent TUI (not the shell) has drawn its input prompt and
# will accept keystrokes. Claude Code discards anything typed before its
# prompt is painted, so priming must wait for one of these rather than a
# fixed sleep. Deliberately excludes shell-prompt markers ("$ ", "% ", "@")
# that watcher.IDLE_PATTERNS accepts — a shell prompt means the agent has
# NOT started yet.
AGENT_PROMPT_PATTERNS = [
    # Claude Code
    "\u276f",           # ❯ input prompt
    "? for shortcuts",
    "shift+tab",        # status bar: "⏵⏵ auto mode on (shift+tab to cycle)"
    "context left",
    # Codex
    "\u203a",           # › input prompt
    "/model to change",
    "/skills to list",
    "type a message",
    "enter a command",
]
AGENT_READY_TIMEOUT_S = 60.0
AGENT_READY_POLL_S = 0.5
AGENT_READY_SETTLE_S = 0.5
AGENT_READY_TAIL_LINES = 8


def agent_prompt_visible(content: str) -> bool:
    """True if the tail of a terminal capture shows an agent input prompt."""
    if not content or not content.strip():
        return False
    tail = "\n".join(content.strip().splitlines()[-AGENT_READY_TAIL_LINES:]).lower()
    return any(p.lower() in tail for p in AGENT_PROMPT_PATTERNS)


def wait_for_agent_ready(
    read_contents,
    label: str = "agent",
    timeout: float = AGENT_READY_TIMEOUT_S,
    poll: float = AGENT_READY_POLL_S,
) -> bool:
    """Poll ``read_contents()`` until an agent prompt is visible.

    Returns True once ready (after a short settle so the TUI finishes
    painting), False on timeout — callers should still send the prime and
    tell the user it may need re-sending.
    """
    deadline = time.monotonic() + timeout
    while True:
        if agent_prompt_visible(read_contents()):
            time.sleep(AGENT_READY_SETTLE_S)
            return True
        if time.monotonic() >= deadline:
            print(
                f"  Warning: {label} prompt not detected after {int(timeout)}s;"
                " sending priming message anyway. If it did not arrive, paste:"
            )
            print(f'    "{PRIME_MESSAGE}"')
            return False
        time.sleep(poll)


def _backend_choices_text() -> str:
    return "'iterm2', 'tmux', 'terminal', or 'manual'"


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


def _terminal_supported() -> bool:
    """Terminal.app ships with every Mac; claim it only on macOS with
    osascript and the app bundle present."""
    if sys.platform != "darwin" or shutil.which("osascript") is None:
        return False
    from tagteam.terminal import _TERMINAL_APP_PATHS
    return any(Path(p).exists() for p in _TERMINAL_APP_PATHS)


def default_backend() -> str:
    """Choose the best available session backend for this machine.

    iTerm2, then tmux, then (macOS only) Terminal.app — so a Mac with
    nothing installed still gets an automated backend — then manual.
    """
    if _iterm2_supported():
        return "iterm2"
    if _tmux_supported():
        return "tmux"
    if _terminal_supported():
        return "terminal"
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
        return

    if backend == "terminal":
        if sys.platform != "darwin":
            print("Terminal.app session management is only available on macOS.")
        else:
            print("Terminal.app session management requires AppleScript and Terminal.app.")
        print("  Use '--backend manual' for the manual workflow.")
        if _tmux_supported():
            print("  Or use '--backend tmux' if you prefer tmux.")


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

    if backend == "terminal" and not _terminal_supported():
        _print_backend_unavailable("terminal")
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
    try:
        return get_launch_commands(config)
    except ValueError as exc:
        print(f"Cannot launch agents: {exc}")
        return None


def _quote_shell_arg(value: str) -> str:
    """Quote one command argument for the user's shell."""
    if sys.platform.startswith("win"):
        return subprocess.list2cmdline([value])
    return shlex.quote(value)


def _project_tagteam_python(project_dir: str | None) -> Path | None:
    """Find a project-local Python that has the tagteam console script.

    New terminal tabs do not inherit an activated virtualenv, so bare
    ``python -m tagteam`` can hit pyenv/system Python even when TagTeam is
    installed into the project. Prefer the local env when it clearly contains
    TagTeam, then fall back to the interpreter running this process.
    """
    project = Path(project_dir or ".").resolve()
    if sys.platform.startswith("win"):
        candidates = [
            (project / ".venv" / "Scripts" / "python.exe",
             project / ".venv" / "Scripts" / "tagteam.exe"),
            (project / "venv" / "Scripts" / "python.exe",
             project / "venv" / "Scripts" / "tagteam.exe"),
        ]
    else:
        candidates = [
            (project / ".venv" / "bin" / "python",
             project / ".venv" / "bin" / "tagteam"),
            (project / "venv" / "bin" / "python",
             project / "venv" / "bin" / "tagteam"),
        ]

    for python_path, tagteam_script in candidates:
        if python_path.exists() and tagteam_script.exists():
            return python_path
    return None


def _tagteam_module_command(project_dir: str | None) -> str:
    """Return a stable shell command prefix for invoking TagTeam."""
    executable = _project_tagteam_python(project_dir)
    if executable is None:
        executable = Path(sys.executable or shutil.which("python3") or "python")
    return f"{_quote_shell_arg(str(executable))} -m tagteam"


def _watcher_command(project_dir: str | None, mode: str) -> str:
    """Return the watcher command for a project and backend mode."""
    return f"{_tagteam_module_command(project_dir)} watch --mode {mode}"


def create_tmux_session(project_dir: str | None = None, launch: bool = False) -> bool:
    """Create a tmux session with lead, watcher, and reviewer panes."""
    if session_exists():
        print(f"Session '{SESSION_NAME}' already exists.")
        print(f"  Attach: tmux attach -t {SESSION_NAME}")
        print(f"  Kill:   tmux kill-session -t {SESSION_NAME}")
        return False

    start_dir = project_dir or "."
    from tagteam.config import read_config, get_agent_names
    names = get_agent_names(read_config(Path(start_dir) / "tagteam.yaml") or {})
    lead_title = f"{names[0]} (Lead)" if names[0] else "Lead"
    reviewer_title = f"{names[1]} (Reviewer)" if names[1] else "Reviewer"

    try:
        _tmux("new-session", "-d", "-s", SESSION_NAME, "-n", "handoff", "-c", start_dir)
        _tmux("split-window", "-h", "-t", f"{SESSION_NAME}:0.0", "-c", start_dir)
        _tmux("split-window", "-h", "-t", f"{SESSION_NAME}:0.1", "-c", start_dir)
        _tmux("select-layout", "-t", f"{SESSION_NAME}:0", "even-horizontal")

        _tmux("select-pane", "-t", f"{SESSION_NAME}:0.0", "-T", lead_title)
        _tmux("select-pane", "-t", f"{SESSION_NAME}:0.1", "-T", "WATCHER")
        _tmux("select-pane", "-t", f"{SESSION_NAME}:0.2", "-T", reviewer_title)

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
                    _watcher_command(project_dir, "tmux"),
                    "Enter",
                )
                _tmux("send-keys", "-t", f"{SESSION_NAME}:0.2", reviewer_cmd, "Enter")
                print("  Waiting for agents to start before priming...")
                from tagteam.watcher import capture_pane

                for pane, label in ((f"{SESSION_NAME}:0.0", "lead"), (f"{SESSION_NAME}:0.2", "reviewer")):
                    wait_for_agent_ready(
                        lambda pane=pane: capture_pane(pane, last_n_lines=AGENT_READY_TAIL_LINES),
                        label=label,
                    )
                    _tmux("send-keys", "-t", pane, PRIME_MESSAGE, "Enter")
            else:
                _tmux(
                    "send-keys",
                    "-t",
                    f"{SESSION_NAME}:0.1",
                    _watcher_command(project_dir, "tmux"),
                    "",
                )
        else:
            _tmux(
                "send-keys",
                "-t",
                f"{SESSION_NAME}:0.1",
                _watcher_command(project_dir, "tmux"),
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
        print(f"  Watcher terminal:  {_watcher_command(project_dir, 'notify')}")
        print(f"  Reviewer terminal: {reviewer_cmd}")
        print()
        print("Then send both agents this priming message:")
        print(f'  "{PRIME_MESSAGE}"')
    else:
        print("  Lead terminal:     start your lead agent")
        print(f"  Watcher terminal:  {_watcher_command(project_dir, 'notify')}")
        print("  Reviewer terminal: start your reviewer agent")

    print()
    print("The watcher will log turn changes and the command to run next.")
    print("For automated terminal orchestration, use macOS (iTerm2 or Terminal.app) or tmux on PATH.")
    if sys.platform.startswith("win"):
        print("On Windows today, WSL + tmux is the supported automation path.")
    return True



def ensure_session(
    project_dir: str,
    backend: str | None = None,
    launch: bool = False,
    *,
    attach_existing: bool = True,
) -> str:
    """Create or reuse a session. Returns one of: created, exists, manual, error.

    When attach_existing is False, an already-running tmux session is reported
    as 'exists' without invoking 'tmux attach' (which would block the caller).
    iTerm2 and manual backends never attach, so the flag is accepted for
    symmetry but is a no-op for them.
    """
    backend = backend or default_backend()
    if not _validate_backend(backend):
        return "error"

    if backend == "manual":
        ok = create_manual_session(project_dir=project_dir, launch=launch)
        return "manual" if ok else "error"

    if backend == "tmux":
        if session_exists():
            if attach_existing:
                print(f"Session '{SESSION_NAME}' already exists; attaching.")
                subprocess.run(["tmux", "attach", "-t", SESSION_NAME])
            return "exists"
        ok = create_tmux_session(project_dir=project_dir, launch=launch)
        return "created" if ok else "error"

    # Tab backends (iterm2 / terminal): one driver module each, same surface.
    from tagteam.tabs import (
        _find_session_file,
        _read_session_file,
        _session_file_path,
        driver_for,
        session_backend,
    )

    driver = driver_for(backend)
    label = _TAB_BACKEND_LABELS[backend]
    existing = _read_session_file(project_dir)
    if existing:
        file_backend = session_backend(project_dir)
        if file_backend == backend and driver._any_session_alive(existing):
            print(f"{label} session already exists; skipping session creation.")
            return "exists"
        if file_backend != backend and file_backend in TAB_BACKENDS_FOR_MESSAGES:
            other = driver_for(file_backend)
            if other._any_session_alive(existing):
                print(f"A live {_TAB_BACKEND_LABELS[file_backend]} session already exists"
                      f" ({_find_session_file(project_dir) or _session_file_path(project_dir)}).")
                print(f"  Kill it first (tagteam session kill --backend {file_backend})"
                      f" or use --backend {file_backend}.")
                return "error"
        stale_path = _find_session_file(project_dir) or _session_file_path(project_dir)
        print(f"Stale {label} session file (no live tabs): {stale_path}")
        print("  Removing and creating a fresh session.")
        try:
            stale_path.unlink()
        except OSError:
            pass

    ok = driver.create_session(project_dir, launch=launch)
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


def _print_session_usage() -> None:
    print("Usage: python -m tagteam session <command> [options]")
    print()
    print("Commands:")
    print("  start       Create or describe an orchestration session")
    print("  kill        Kill the managed tmux/iTerm2 session")
    print("  attach      Attach to an existing tmux session")
    print("  adopt       Register manually-opened iTerm2 tabs / Terminal.app windows")
    print("  list-iterm  List currently-open iTerm2 sessions and their IDs")
    print("  list-terminal  List currently-open Terminal.app tabs and their ttys")
    print()
    print("Options:")
    print(
        "  --backend iterm2|tmux|terminal|manual  Backend to use"
        " (default: auto-detect)"
    )
    print("  --dir PATH                    Project directory (default: .)")
    print("  --no-launch                   Skip auto-starting agents")


def _adopt_command(args: list[str], backend: str) -> int:
    """Register manually-opened iTerm2 tabs (or Terminal.app windows) as
    the watcher's panes.

    Writes ``.handoff-session.json`` in the same shape as
    ``session start --launch`` (so all existing consumers — watcher
    auto-detect, get_session_id, _any_session_alive, server log-tail —
    work unchanged). The ids are iTerm2 ``unique ID``s or Terminal.app
    ttys, validated through the selected backend's driver.
    """
    if backend not in TAB_BACKENDS_FOR_MESSAGES:
        print("'session adopt' is only supported for the iterm2 backend and the terminal backend.")
        return 1
    label = _TAB_BACKEND_LABELS[backend]
    id_word = "unique-id" if backend == "iterm2" else "tty"
    list_cmd = "list-iterm" if backend == "iterm2" else "list-terminal"

    lead_id = None
    reviewer_id = None
    watcher_id = None
    project_dir = "."
    force = False

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--lead" and i + 1 < len(args):
            lead_id = args[i + 1]; i += 2
        elif a == "--reviewer" and i + 1 < len(args):
            reviewer_id = args[i + 1]; i += 2
        elif a == "--watcher" and i + 1 < len(args):
            watcher_id = args[i + 1]; i += 2
        elif a == "--dir" and i + 1 < len(args):
            project_dir = args[i + 1]; i += 2
        elif a == "--force":
            force = True; i += 1
        elif a in ("-h", "--help"):
            print(f"Usage: tagteam session adopt [--backend {backend}] --lead <{id_word}>"
                  " [--reviewer <id>] [--watcher <id>] [--force]")
            print()
            print(f"Register {label} tabs you opened manually so the watcher")
            print(f"can send-keys to them. Use `tagteam session {list_cmd}`")
            print(f"to discover the {id_word}s of currently-open sessions.")
            return 0
        else:
            print(f"Unknown arg: {a}")
            return 1

    if not lead_id:
        print("--lead is required.")
        return 1

    from tagteam.tabs import driver_for

    session_id_is_valid = driver_for(backend).session_id_is_valid
    for role, sid in [("lead", lead_id), ("watcher", watcher_id),
                      ("reviewer", reviewer_id)]:
        if sid and not session_id_is_valid(sid):
            print(f"ERROR: {role} session id {sid!r}"
                  f" is not a live {label} session.")
            return 1

    path = Path(project_dir) / ".handoff-session.json"
    if path.exists() and not force:
        print(f"{path} already exists. Pass --force to overwrite.")
        return 1

    tabs = {"lead": {"session_id": lead_id}}
    if watcher_id:
        tabs["watcher"] = {"session_id": watcher_id}
    if reviewer_id:
        tabs["reviewer"] = {"session_id": reviewer_id}

    payload = {"backend": backend, "tabs": tabs}
    path.write_text(json.dumps(payload, indent=2))
    print(f"Adopted {label} sessions into {path}")
    for role, info in tabs.items():
        print(f"  {role}: {info['session_id']}")
    return 0


def _list_iterm_command() -> int:
    """List currently-open iTerm2 sessions and their unique IDs."""
    from tagteam.iterm import list_iterm_sessions

    sessions = list_iterm_sessions()
    if not sessions:
        print("No iTerm2 sessions found (is iTerm2 running?)")
        return 1

    print(f"{'unique-id':<40} {'tab-title':<30} window")
    print("-" * 80)
    for s in sessions:
        print(f"{s['unique_id']:<40} {s['tab_title']:<30}"
              f" {s['window_id']}")
    return 0


def _list_terminal_command() -> int:
    """List currently-open Terminal.app tabs and their ttys."""
    from tagteam.terminal import list_sessions

    sessions = list_sessions()
    if not sessions:
        print("No Terminal.app tabs found (is Terminal.app running?)")
        return 1

    print(f"{'tty':<40} {'tab-title':<30} window")
    print("-" * 80)
    for s in sessions:
        print(f"{s['unique_id']:<40} {s['tab_title']:<30}"
              f" {s['window_id']}")
    return 0


def session_command(args: list[str]) -> int:
    """Handle `python -m tagteam session [subcommand]`."""
    if not args:
        _print_session_usage()
        return 1

    backend, remaining = _parse_backend(args)
    subcmd = remaining[0] if remaining else ""

    if subcmd in ("--help", "-h"):
        _print_session_usage()
        return 0

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
        if effective_backend == "terminal":
            print("The 'attach' command is not needed for Terminal.app (windows are already visible).")
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

    if subcmd == "adopt":
        return _adopt_command(remaining[1:], effective_backend)

    if subcmd == "list-iterm":
        return _list_iterm_command()

    if subcmd == "list-terminal":
        return _list_terminal_command()

    if subcmd == "kill":
        if effective_backend in TAB_BACKENDS_FOR_MESSAGES:
            from tagteam.tabs import driver_for

            project_dir = None
            if len(remaining) > 2 and remaining[1] == "--dir":
                project_dir = remaining[2]
            driver_for(effective_backend).kill_session(project_dir or ".")
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
