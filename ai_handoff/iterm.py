"""
iTerm2 AppleScript integration for handoff orchestration.

Creates iTerm2 tabs (one per agent role) and sends commands via
AppleScript's `write text`. Each tab is an independent terminal session --
no pane sharing, no nesting, no corruption.
"""

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

SESSION_FILE = ".handoff-session.json"
_ITERM_LAUNCH_TIMEOUT_S = 10.0
_ITERM_POLL_INTERVAL_S = 0.2
_ITERM_READY_PROBE = 'tell application "iTerm2" to count windows'
_ITERM_BUNDLE_ID = "com.googlecode.iterm2"


def _osascript(script: str) -> str:
    """Execute AppleScript and return stdout."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"osascript failed: {result.stderr.strip()}")
    return result.stdout.strip()


def iterm_is_running() -> bool:
    """Check if iTerm2 is currently running."""
    try:
        out = _osascript(
            'tell application "System Events" to '
            '(name of processes) contains "iTerm2"'
        )
        return out == "true"
    except Exception:
        return False


def _launch_iterm_via_launchservices() -> None:
    """Launch iTerm2 via macOS LaunchServices (`open -b <bundle-id>`).

    AppleScript's `launch` verb can fail with -1728 (errAENoSuchObject) when
    iTerm2 isn't registered in the osascript process's terminology cache —
    typically after a full quit. `open -b` goes through LaunchServices, which
    uses a system-wide bundle index and works regardless of whether the app
    bundle on disk is named `iTerm.app` or `iTerm2.app`. Errors are swallowed:
    the probe in `_ensure_iterm_ready` is the authoritative readiness signal.
    """
    try:
        subprocess.run(
            ["open", "-b", _ITERM_BUNDLE_ID],
            capture_output=True, timeout=10, check=False,
        )
    except (subprocess.SubprocessError, OSError):
        pass


def _ensure_iterm_ready() -> None:
    """Ensure iTerm2 is running AND its scripting dictionary is loaded.

    Checking process existence is not enough on cold launch: iTerm2 registers
    its AppleScript dictionary with macOS after the process appears, and
    compiling a script against iTerm2's terms will fail until the dictionary
    is registered. We poll with a trivial scripted command that exercises
    the dictionary; once it compiles and runs, the main script will too.
    """
    if not iterm_is_running():
        _launch_iterm_via_launchservices()

    deadline = time.monotonic() + _ITERM_LAUNCH_TIMEOUT_S
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            _osascript(_ITERM_READY_PROBE)
            return
        except RuntimeError as e:
            last_error = e
            time.sleep(_ITERM_POLL_INTERVAL_S)
    raise RuntimeError(
        f"iTerm2 scripting did not become ready within "
        f"{_ITERM_LAUNCH_TIMEOUT_S:.0f}s: {last_error}"
    )


def _session_file_path(project_dir: str) -> Path:
    return Path(project_dir) / SESSION_FILE


def _find_session_file(project_dir: str) -> Path | None:
    """Find .handoff-session.json in project_dir or any parent directory."""
    current = Path(project_dir).resolve()
    for _ in range(20):  # safety limit
        candidate = current / SESSION_FILE
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _read_session_file(project_dir: str) -> dict | None:
    """Read the session file, searching parent directories if needed."""
    path = _find_session_file(project_dir)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_session_file(project_dir: str, data: dict) -> None:
    path = _session_file_path(project_dir)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def create_session(project_dir: str, launch: bool = False) -> bool:
    """Create an iTerm2 window with 3 named tabs: Lead, Watcher, Reviewer.

    Each tab cd's to project_dir. Session IDs are saved to
    .handoff-session.json for the watcher to discover.

    If launch=True, auto-starts agents and watcher using commands from
    ai-handoff.yaml (falls back to agent name lowercase).
    """
    existing = _read_session_file(project_dir)
    if existing:
        if _any_session_alive(existing):
            print(f"Session file already exists: {_session_file_path(project_dir)}")
            print("  Kill first:  ai-handoff session kill")
            return False
        stale_path = _find_session_file(project_dir) or _session_file_path(project_dir)
        print(f"Stale session file found (no live iTerm2 tabs): {stale_path}")
        print("  Removing and creating a fresh session.")
        try:
            stale_path.unlink()
        except OSError:
            pass

    # Cold-launched iTerm2 opens its own default window, so we need to reuse
    # it. When iTerm2 is already running, we must NOT reuse the current
    # window — it may contain the user's unrelated work.
    was_running = iterm_is_running()
    try:
        _ensure_iterm_ready()
    except RuntimeError as e:
        print(f"iTerm2 failed to launch: {e}")
        print("  Is iTerm2 installed? Alternatives:")
        print("    ai-handoff session start --backend tmux")
        print("    ai-handoff session start --backend manual")
        return False

    # Resolve launch commands if requested
    lead_cmd = reviewer_cmd = None
    if launch:
        from ai_handoff.session import _read_launch_commands
        cmds = _read_launch_commands(project_dir)
        if cmds:
            lead_cmd, reviewer_cmd = cmds
        else:
            launch = False  # Config missing — fall back to no-launch

    abs_dir = str(Path(project_dir).resolve())

    if was_running:
        # Always create a fresh window so we don't clobber the user's other work
        window_clause = '    create window with default profile\n'
    else:
        # Cold launch: iTerm2 created a default window; reuse it if present
        window_clause = (
            '    if (count of windows) = 0 then\n'
            '        create window with default profile\n'
            '    end if\n'
        )

    # AppleScript: activate iTerm2, get a window, and set up 3 tabs.
    # Launch commands are sent AFTER session file is written to avoid the
    # watcher starting before .handoff-session.json exists.
    script = (
        'tell application "iTerm2"\n'
        '    activate\n'
        + window_clause +
        '    tell current window\n'
        '        tell current session of current tab\n'
        '            set name to "Lead"\n'
        '            write text "cd ' + abs_dir + '"\n'
        '        end tell\n'
        '        set leadId to unique ID of current session of current tab\n'
        '        set watcherTab to (create tab with default profile)\n'
        '        tell current session of watcherTab\n'
        '            set name to "Watcher"\n'
        '            write text "cd ' + abs_dir + '"\n'
        '        end tell\n'
        '        set watcherId to unique ID of current session of watcherTab\n'
        '        set reviewerTab to (create tab with default profile)\n'
        '        tell current session of reviewerTab\n'
        '            set name to "Reviewer"\n'
        '            write text "cd ' + abs_dir + '"\n'
        '        end tell\n'
        '        set reviewerId to unique ID of current session of reviewerTab\n'
        '        select first tab\n'
        '    end tell\n'
        '    return leadId & "," & watcherId & "," & reviewerId\n'
        'end tell'
    )

    try:
        result = _osascript(script)
    except RuntimeError as e:
        print(f"Error creating iTerm2 session: {e}")
        return False

    ids = [s.strip() for s in result.split(",")]
    if len(ids) != 3:
        print(f"Unexpected response from iTerm2: {result}")
        return False

    session_data = {
        "backend": "iterm2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_dir": abs_dir,
        "tabs": {
            "lead": {"session_id": ids[0]},
            "watcher": {"session_id": ids[1]},
            "reviewer": {"session_id": ids[2]},
        },
    }
    _write_session_file(project_dir, session_data)

    # Send launch commands AFTER session file exists so the watcher
    # can find .handoff-session.json on startup.
    # Pass raw commands — write_text_to_session() handles escaping internally.
    if launch:
        from ai_handoff.session import PRIME_MESSAGE
        import time

        write_text_to_session(ids[0], lead_cmd)
        write_text_to_session(ids[2], reviewer_cmd)
        # Start watcher last — agents need a moment to initialize
        watcher_cmd = "python -m ai_handoff watch --mode iterm2"
        write_text_to_session(ids[1], watcher_cmd)
        # Auto-prime agents after they boot
        print("  Waiting for agents to start before priming...")
        time.sleep(3)
        write_text_to_session(ids[0], PRIME_MESSAGE)
        time.sleep(1)
        write_text_to_session(ids[2], PRIME_MESSAGE)

    launched = " (launched)" if launch else ""
    print(f"Created iTerm2 session with 3 tabs{launched}:")
    print()
    if launch:
        print(f"  Tab 1: Lead     - {lead_cmd}")
        print("  Tab 2: Watcher  - running")
        print(f"  Tab 3: Reviewer - {reviewer_cmd}")
    else:
        print("  Tab 1: Lead     - start your lead agent here")
        print("  Tab 2: Watcher  - start the watcher here")
        print("  Tab 3: Reviewer - start your reviewer agent here")
    print()
    print(f"  Session file: {_session_file_path(project_dir)}")
    return True


def write_text_to_session(session_id: str, text: str) -> bool:
    """Send a command to a specific iTerm2 session.

    We send text with `newline NO` and then explicitly send ASCII 13
    (carriage return). This is more reliable for TUIs like Codex, which
    may not always treat iTerm2's implicit newline as a submit keypress.
    """
    # Escape backslashes and double quotes for AppleScript string
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
    tell application "iTerm2"
        repeat with w in windows
            repeat with t in tabs of w
                repeat with s in sessions of t
                    if unique ID of s is "{session_id}" then
                        tell s to write text "{escaped}" newline NO
                        delay 0.05
                        tell s to write text (ASCII character 13) newline NO
                        return "ok"
                    end if
                end repeat
            end repeat
        end repeat
        return "not_found"
    end tell
    '''
    try:
        result = _osascript(script)
        return result == "ok"
    except Exception:
        return False


def get_session_contents(session_id: str, last_n_lines: int = 5) -> str:
    """Get the visible text from an iTerm2 session (last N lines)."""
    script = f'''
    tell application "iTerm2"
        repeat with w in windows
            repeat with t in tabs of w
                repeat with s in sessions of t
                    if unique ID of s is "{session_id}" then
                        return contents of s
                    end if
                end repeat
            end repeat
        end repeat
        return ""
    end tell
    '''
    try:
        content = _osascript(script)
        if not content:
            return ""
        lines = content.splitlines()
        return "\n".join(lines[-last_n_lines:])
    except Exception:
        return ""


def _any_session_alive(session_data: dict) -> bool:
    """Return True if at least one tab's session ID is still a live iTerm2 tab.

    Used to distinguish a real live session from a stale .handoff-session.json
    (e.g. one restored by `git checkout` or left behind after iTerm2 quit).
    If iTerm2 itself isn't running, the session is definitely not alive.
    """
    if not iterm_is_running():
        return False
    tabs = (session_data or {}).get("tabs", {})
    for info in tabs.values():
        sid = info.get("session_id") if isinstance(info, dict) else None
        if sid and session_id_is_valid(sid):
            return True
    return False


def session_id_is_valid(session_id: str) -> bool:
    """Check if a session ID still corresponds to a live iTerm2 tab."""
    script = f'''
    tell application "iTerm2"
        repeat with w in windows
            repeat with t in tabs of w
                repeat with s in sessions of t
                    if unique ID of s is "{session_id}" then
                        return "found"
                    end if
                end repeat
            end repeat
        end repeat
        return "not_found"
    end tell
    '''
    try:
        return _osascript(script) == "found"
    except Exception:
        return False


def get_session_id(role: str, project_dir: str) -> str | None:
    """Read a session ID for a role from .handoff-session.json."""
    data = _read_session_file(project_dir)
    if not data:
        return None
    tabs = data.get("tabs", {})
    tab = tabs.get(role, {})
    return tab.get("session_id")


def kill_session(project_dir: str) -> bool:
    """Close all iTerm2 tabs from this session and delete the session file."""
    data = _read_session_file(project_dir)
    if not data:
        print("No session file found.")
        return False

    tabs = data.get("tabs", {})
    for role, info in tabs.items():
        sid = info.get("session_id")
        if sid:
            try:
                _osascript(f'''
                tell application "iTerm2"
                    repeat with w in windows
                        repeat with t in tabs of w
                            repeat with s in sessions of t
                                if unique ID of s is "{sid}" then
                                    tell s to close
                                end if
                            end repeat
                        end repeat
                    end repeat
                end tell
                ''')
            except Exception:
                pass  # Tab may already be closed

    # Remove session file
    path = _session_file_path(project_dir)
    try:
        path.unlink()
    except OSError:
        pass

    print("iTerm2 session killed.")
    return True
