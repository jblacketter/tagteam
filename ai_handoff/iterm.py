"""
iTerm2 AppleScript integration for handoff orchestration.

Creates iTerm2 tabs (one per agent role) and sends commands via
AppleScript's `write text`. Each tab is an independent terminal session --
no pane sharing, no nesting, no corruption.
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SESSION_FILE = ".handoff-session.json"


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


def _session_file_path(project_dir: str) -> Path:
    return Path(project_dir) / SESSION_FILE


def _read_session_file(project_dir: str) -> dict | None:
    """Read the session file, returning None if missing or invalid."""
    path = _session_file_path(project_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_session_file(project_dir: str, data: dict) -> None:
    path = _session_file_path(project_dir)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def create_session(project_dir: str) -> bool:
    """Create an iTerm2 window with 3 named tabs: Lead, Watcher, Reviewer.

    Each tab cd's to project_dir. Session IDs are saved to
    .handoff-session.json for the watcher to discover.
    """
    existing = _read_session_file(project_dir)
    if existing:
        print(f"Session file already exists: {_session_file_path(project_dir)}")
        print("  Kill first:  python -m ai_handoff session kill")
        return False

    abs_dir = str(Path(project_dir).resolve())

    # AppleScript to create window + 3 tabs, capture session IDs.
    # Key details:
    # - `create tab` must be inside `tell current window` to avoid new windows
    # - Tab title is set via `set name` on the session, not the tab
    script = f'''
    tell application "iTerm2"
        activate

        -- Create a new window (comes with one tab)
        create window with default profile
        tell current window
            -- Configure first tab as Lead
            tell current session of current tab
                set name to "Lead"
                write text "cd {abs_dir}"
            end tell
            set leadId to unique ID of current session of current tab

            -- Create Watcher tab (in same window)
            set watcherTab to (create tab with default profile)
            tell current session of watcherTab
                set name to "Watcher"
                write text "cd {abs_dir}"
            end tell
            set watcherId to unique ID of current session of watcherTab

            -- Create Reviewer tab (in same window)
            set reviewerTab to (create tab with default profile)
            tell current session of reviewerTab
                set name to "Reviewer"
                write text "cd {abs_dir}"
            end tell
            set reviewerId to unique ID of current session of reviewerTab

            -- Switch back to Lead tab
            select first tab
        end tell

        return leadId & "," & watcherId & "," & reviewerId
    end tell
    '''

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

    print("Created iTerm2 session with 3 tabs:")
    print()
    print("  Tab 1: Lead     - start Claude Code here")
    print("  Tab 2: Watcher  - press Enter to start")
    print("  Tab 3: Reviewer - start Codex here")
    print()
    print(f"  Session file: {_session_file_path(project_dir)}")
    return True


def write_text_to_session(session_id: str, text: str) -> bool:
    """Send a command to a specific iTerm2 session.

    iTerm2's `write text` automatically appends a newline, so no C-m hack
    is needed. The text is sent directly -- no input clearing required.
    """
    # Escape backslashes and double quotes for AppleScript string
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
    tell application "iTerm2"
        repeat with w in windows
            repeat with t in tabs of w
                repeat with s in sessions of t
                    if unique ID of s is "{session_id}" then
                        tell s to write text "{escaped}"
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
