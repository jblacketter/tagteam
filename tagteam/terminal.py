"""
Terminal.app AppleScript integration for handoff orchestration (macOS).

The `terminal` session backend: the same shape as `tagteam.iterm` (one
function per operation, one `.handoff-session.json` record) driving Apple's
built-in Terminal.app instead of iTerm2, so a Mac needs nothing installed.

Differences from iTerm2 that shape this module:

* Terminal.app's dictionary cannot create *tabs* without GUI scripting (an
  Accessibility permission), so each role gets its own **window** —
  Lead / Watcher / Reviewer.
* There is no persistent session UUID; a tab's ``tty`` (``/dev/ttys004``)
  is unique among open tabs and stable for the tab's lifetime, so it is the
  ``session_id``. Windows are identified by ``id`` for bookkeeping only.
* Text is sent with ``do script <text> in <tab>`` (Terminal writes the text
  into the tab's tty followed by a newline) and then a second, empty
  ``do script "" in <tab>`` — a lone newline. Measured 2026-08-17: Claude
  Code submits on the first call already (the extra newline on its now-empty
  input is a no-op, and does not dismiss an open dialog); Codex keeps the
  first call's text *in its composer* (Terminal delivers `do script` text as
  a paste, so the trailing newline — even an explicit ``& return`` — is a
  literal newline to it) and submits only on the separate newline. Same
  two-step shape as iTerm2's ``write text … newline NO`` + CR.
"""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from tagteam.tabs import (  # noqa: F401  (re-exported: patch points + callers)
    SESSION_FILE,
    SUBMIT_DELAY_S,
    _find_session_file,
    _osascript,
    _read_session_file,
    _session_file_path,
    _write_session_file,
)

_TERMINAL_LAUNCH_TIMEOUT_S = 10.0
_TERMINAL_POLL_INTERVAL_S = 0.2
_TERMINAL_READY_PROBE = 'tell application "Terminal" to count windows'
_TERMINAL_BUNDLE_ID = "com.apple.Terminal"
_TERMINAL_APP_PATHS = (
    "/System/Applications/Utilities/Terminal.app",
    "/Applications/Utilities/Terminal.app",
)

_ROLES = ("lead", "watcher", "reviewer")
_ROLE_TITLES = {"lead": "Lead", "watcher": "Watcher", "reviewer": "Reviewer"}


def _applescript_string(text: str) -> str:
    """Escape *text* for use inside an AppleScript double-quoted literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def terminal_is_running() -> bool:
    """Check if Terminal.app is currently running."""
    try:
        out = _osascript(
            'tell application "System Events" to '
            '(name of processes) contains "Terminal"'
        )
        return out == "true"
    except Exception:
        return False


def _launch_terminal_via_launchservices() -> None:
    """Launch Terminal.app via LaunchServices (`open -b com.apple.Terminal`).

    Errors are swallowed: the probe in `_ensure_terminal_ready` is the
    authoritative readiness signal.
    """
    try:
        subprocess.run(
            ["open", "-b", _TERMINAL_BUNDLE_ID],
            capture_output=True, timeout=10, check=False,
        )
    except (subprocess.SubprocessError, OSError):
        pass


def _ensure_terminal_ready() -> None:
    """Ensure Terminal.app is running AND answering AppleEvents.

    Same shape as `iterm._ensure_iterm_ready`: launch if needed, then poll a
    trivial scripted command until it compiles and runs.
    """
    if not terminal_is_running():
        _launch_terminal_via_launchservices()

    deadline = time.monotonic() + _TERMINAL_LAUNCH_TIMEOUT_S
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            _osascript(_TERMINAL_READY_PROBE)
            return
        except RuntimeError as e:
            last_error = e
            time.sleep(_TERMINAL_POLL_INTERVAL_S)
    raise RuntimeError(
        f"Terminal.app scripting did not become ready within "
        f"{_TERMINAL_LAUNCH_TIMEOUT_S:.0f}s: {last_error}"
    )


# -- window bookkeeping ------------------------------------------------------

def _parse_id_list(raw: str) -> list[int]:
    """Parse osascript's rendering of `id of every window` ("12, 15")."""
    ids: list[int] = []
    for part in raw.replace("{", "").replace("}", "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    return ids


def _window_ids() -> list[int]:
    return _parse_id_list(_osascript('tell application "Terminal" to id of every window'))


def _tab_busy(window_id: int) -> bool:
    """`busy of selected tab of window id N` — True unless clearly idle."""
    try:
        out = _osascript(
            f'tell application "Terminal" to busy of selected tab of window id {window_id}'
        )
    except RuntimeError:
        return True
    return out.strip().lower() != "false"


def _do_script(text: str, window_id: int | None) -> None:
    """`do script text` — targeted at a window's selected tab, or untargeted
    (opens a new window)."""
    esc = _applescript_string(text)
    if window_id is None:
        _osascript(f'tell application "Terminal" to do script "{esc}"')
    else:
        _osascript(
            f'tell application "Terminal" to do script "{esc}" in window id {window_id}'
        )


def _tty_of_window(window_id: int) -> str:
    return _osascript(
        f'tell application "Terminal" to tty of selected tab of window id {window_id}'
    ).strip()


def _set_title(window_id: int, title: str) -> None:
    try:
        _osascript(
            f'tell application "Terminal" to set custom title of selected tab of '
            f'window id {window_id} to "{_applescript_string(title)}"'
        )
    except RuntimeError:
        pass  # cosmetic


def _close_windows(window_ids: list[int]) -> None:
    for wid in window_ids:
        try:
            _osascript(f'tell application "Terminal" to close window id {wid} saving no')
        except RuntimeError:
            pass


def _arrange_windows(window_ids: list[int]) -> None:
    """Best-effort: place the role windows side by side, left to right,
    keeping the Lead window's own size. Any failure is ignored."""
    if not window_ids:
        return
    try:
        raw = _osascript(f'tell application "Terminal" to bounds of window id {window_ids[0]}')
        x1, y1, x2, y2 = _parse_id_list(raw)[:4]
    except (RuntimeError, ValueError):
        return
    width = max(x2 - x1, 200)
    for i, wid in enumerate(window_ids):
        left = x1 + i * width
        try:
            _osascript(
                f'tell application "Terminal" to set bounds of window id {wid} '
                f'to {{{left}, {y1}, {left + width}, {y2}}}'
            )
        except RuntimeError:
            return


def _create_role_windows(abs_dir: str, was_running: bool) -> dict[str, int] | None:
    """Open the three role windows per the deterministic accounting rule.

    Returns {role: window_id} or None after printing an error (windows we
    created are closed again). Never targets or closes a pre-existing window
    except the single idle launch window in the cold-launch reuse case.
    """
    cd_cmd = f"cd {abs_dir}"
    before = _window_ids()
    seen = set(before)
    created: list[int] = []
    windows: dict[str, int] = {}

    # Lead: reuse Terminal's own launch window when it is unambiguous.
    reuse = (not was_running) and len(before) == 1 and not _tab_busy(before[0])
    if reuse:
        launch_id = before[0]
        _do_script(cd_cmd, launch_id)
        after = _window_ids()
        new = [w for w in after if w not in seen]
        if not new:
            windows["lead"] = launch_id
        else:
            # Terminal ignored the target and opened a window anyway.
            windows["lead"] = new[0]
            created.append(new[0])
            seen.update(new)
            print("  note: Terminal.app opened a new window for the Lead;"
                  " its launch window was left alone.")
    else:
        _do_script(cd_cmd, None)
        after = _window_ids()
        new = [w for w in after if w not in seen]
        if len(new) != 1:
            _close_windows(new)
            print(f"Error creating Terminal.app session: expected one new window"
                  f" for the Lead, saw {len(new)} (ids {new})")
            return None
        windows["lead"] = new[0]
        created.append(new[0])
        seen.add(new[0])

    for role in ("watcher", "reviewer"):
        _do_script(cd_cmd, None)
        after = _window_ids()
        new = [w for w in after if w not in seen]
        if len(new) != 1:
            _close_windows(created + new)
            print(f"Error creating Terminal.app session: expected one new window"
                  f" for the {_ROLE_TITLES[role]}, saw {len(new)} (ids {new})")
            return None
        windows[role] = new[0]
        created.append(new[0])
        seen.add(new[0])

    return windows


def create_session(project_dir: str, launch: bool = False) -> bool:
    """Create three Terminal.app windows: Lead, Watcher, Reviewer.

    Each window cd's to project_dir. Tab ttys (the session ids) and window
    ids are saved to .handoff-session.json for the watcher to discover.

    If launch=True, auto-starts agents and watcher using commands from
    tagteam.yaml, then primes each agent once its prompt is visible.
    """
    existing = _read_session_file(project_dir)
    if existing:
        if _any_session_alive(existing):
            print(f"Session file already exists: {_session_file_path(project_dir)}")
            print("  Kill first:  tagteam session kill")
            return False
        stale_path = _find_session_file(project_dir) or _session_file_path(project_dir)
        print(f"Stale session file found (no live Terminal.app windows): {stale_path}")
        print("  Removing and creating a fresh session.")
        try:
            stale_path.unlink()
        except OSError:
            pass

    was_running = terminal_is_running()
    try:
        _ensure_terminal_ready()
    except RuntimeError as e:
        print(f"Terminal.app failed to launch: {e}")
        print("  If macOS asked whether this app may control Terminal, allow it"
              " (System Settings → Privacy & Security → Automation) and retry.")
        print("  Alternatives:")
        print("    tagteam session start --backend iterm2")
        print("    tagteam session start --backend tmux")
        print("    tagteam session start --backend manual")
        return False

    lead_cmd = reviewer_cmd = None
    if launch:
        from tagteam.session import _read_launch_commands
        cmds = _read_launch_commands(project_dir)
        if cmds:
            lead_cmd, reviewer_cmd = cmds
        else:
            launch = False  # Config missing — fall back to no-launch

    abs_dir = str(Path(project_dir).resolve())

    try:
        _osascript('tell application "Terminal" to activate')
    except RuntimeError:
        pass

    try:
        windows = _create_role_windows(abs_dir, was_running)
    except RuntimeError as e:
        print(f"Error creating Terminal.app session: {e}")
        return False
    if windows is None:
        return False

    ttys: dict[str, str] = {}
    try:
        for role in _ROLES:
            ttys[role] = _tty_of_window(windows[role])
    except RuntimeError as e:
        _close_windows(list(windows.values()))
        print(f"Error creating Terminal.app session: {e}")
        return False
    if len(set(ttys.values())) != 3 or not all(ttys.values()):
        _close_windows(list(windows.values()))
        print(f"Unexpected response from Terminal.app: ttys {ttys}")
        return False

    for role in _ROLES:
        _set_title(windows[role], _ROLE_TITLES[role])
    _arrange_windows([windows[r] for r in _ROLES])

    session_data = {
        "backend": "terminal",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_dir": abs_dir,
        "tabs": {
            role: {"session_id": ttys[role], "window_id": windows[role]}
            for role in _ROLES
        },
    }
    _write_session_file(project_dir, session_data)

    # Launch commands go AFTER the session file exists so the watcher can
    # find .handoff-session.json on startup (same order as iTerm2).
    if launch:
        from tagteam.session import (
            AGENT_READY_TAIL_LINES,
            PRIME_MESSAGE,
            _watcher_command,
            wait_for_agent_ready,
        )

        write_text_to_session(ttys["lead"], lead_cmd)
        write_text_to_session(ttys["reviewer"], reviewer_cmd)
        write_text_to_session(ttys["watcher"], _watcher_command(project_dir, "terminal"))
        print("  Waiting for agents to start before priming...")
        for role in ("lead", "reviewer"):
            sid = ttys[role]
            wait_for_agent_ready(
                lambda sid=sid: get_session_contents(sid, last_n_lines=AGENT_READY_TAIL_LINES),
                label=role,
            )
            write_text_to_session(sid, PRIME_MESSAGE)

    launched = " (launched)" if launch else ""
    print(f"Created Terminal.app session with 3 windows{launched}:")
    print()
    if launch:
        print(f"  Window 1: Lead     - {lead_cmd}")
        print("  Window 2: Watcher  - running")
        print(f"  Window 3: Reviewer - {reviewer_cmd}")
    else:
        print("  Window 1: Lead     - start your lead agent here")
        print("  Window 2: Watcher  - start the watcher here")
        print("  Window 3: Reviewer - start your reviewer agent here")
    print()
    print(f"  Session file: {_session_file_path(project_dir)}")
    return True


# -- per-tab operations (session_id == tty) ---------------------------------

def _tab_script(session_id: str, body: str, not_found: str = "not_found") -> str:
    """Wrap *body* in a loop over every window `w` / tab index `i` that
    stops at the tab whose tty is *session_id*; *body* must `return` and must
    address the tab as ``tab i of w`` (never a loop variable: in AppleScript
    ``contents of <variable>`` is the dereference operator, so ``contents of
    t`` yields the tab object, not its screen text)."""
    sid = _applescript_string(session_id)
    return f'''
    tell application "Terminal"
        repeat with w in windows
            repeat with i from 1 to (count of tabs of w)
                if tty of tab i of w is "{sid}" then
{body}
                end if
            end repeat
        end repeat
        return "{not_found}"
    end tell
    '''


def write_text_to_session(session_id: str, text: str) -> bool:
    """Type *text* into the tab whose tty is *session_id* and submit it.

    Two `do script`s in one AppleScript: the text (Terminal appends a
    newline, which Claude Code already takes as submit) and, after a short
    delay, an empty one — the lone newline Codex needs to submit what is
    sitting in its composer. See the module docstring for the measurement;
    the pause is SUBMIT_DELAY_S (tagteam.tabs).
    """
    literal = f'"{_applescript_string(text)}"'
    body = (
        f'                    do script {literal} in tab i of w\n'
        f'                    delay {SUBMIT_DELAY_S}\n'
        f'                    do script "" in tab i of w\n'
        f'                    return "ok"'
    )
    try:
        return _osascript(_tab_script(session_id, body)) == "ok"
    except Exception:
        return False


def submit(session_id: str) -> bool:
    """Send one lone newline to the tab whose tty is *session_id* (the
    watcher's recovery for a message Codex left in its composer)."""
    body = (
        '                    do script "" in tab i of w\n'
        '                    return "ok"'
    )
    try:
        return _osascript(_tab_script(session_id, body)) == "ok"
    except Exception:
        return False


def get_session_contents(session_id: str, last_n_lines: int = 5) -> str:
    """Visible text of the tab whose tty is *session_id* (last N lines)."""
    body = '                    return contents of tab i of w'
    try:
        content = _osascript(_tab_script(session_id, body, not_found=""))
        if not content:
            return ""
        lines = content.splitlines()
        return "\n".join(lines[-last_n_lines:])
    except Exception:
        return ""


def session_id_is_valid(session_id: str) -> bool:
    """True if some open Terminal.app tab has this tty."""
    body = '                    return "found"'
    try:
        return _osascript(_tab_script(session_id, body)) == "found"
    except Exception:
        return False


def _any_session_alive(session_data: dict) -> bool:
    """True if at least one role's tty is still an open Terminal.app tab."""
    if not terminal_is_running():
        return False
    tabs = (session_data or {}).get("tabs", {})
    for info in tabs.values():
        sid = info.get("session_id") if isinstance(info, dict) else None
        if sid and session_id_is_valid(sid):
            return True
    return False


def list_sessions() -> list[dict]:
    """Currently-open Terminal.app tabs, same row shape as
    `iterm.list_iterm_sessions`: unique_id (the tty), tab_title, window_id."""
    script = '''
    tell application "Terminal"
      set out to ""
      repeat with w in windows
        repeat with t in tabs of w
          set ttl to ""
          try
            set ttl to custom title of t
          end try
          if ttl is "" then
            try
              set ttl to name of w
            end try
          end if
          set out to out & (tty of t) & "|" & ttl & "|" & (id of w) & linefeed
        end repeat
      end repeat
      return out
    end tell
    '''
    try:
        raw = _osascript(script)
    except Exception:
        return []
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        out.append({"unique_id": parts[0], "tab_title": parts[1], "window_id": parts[2]})
    return out


def get_session_id(role: str, project_dir: str) -> str | None:
    """Read a role's session id (tty) from .handoff-session.json."""
    data = _read_session_file(project_dir)
    if not data:
        return None
    tab = (data.get("tabs") or {}).get(role) or {}
    return tab.get("session_id")


def _hangup_tty(session_id: str) -> None:
    """SIGHUP every process on the tab's tty (agent + shell) — what closing
    the window by hand does. Terminal.app will not close a tab whose
    processes are still running (it would ask first), so this goes first."""
    name = session_id.rsplit("/", 1)[-1]
    if not name.startswith("tty"):
        return
    try:
        subprocess.run(["pkill", "-HUP", "-t", name],
                       capture_output=True, timeout=5, check=False)
    except (subprocess.SubprocessError, OSError):
        pass


def kill_session(project_dir: str) -> bool:
    """Close the recorded Terminal.app tabs (by tty) and delete the session file."""
    data = _read_session_file(project_dir)
    if not data:
        print("No session file found.")
        return False

    for info in (data.get("tabs") or {}).values():
        sid = info.get("session_id") if isinstance(info, dict) else None
        if not sid:
            continue
        _hangup_tty(sid)
        body = '                    close tab i of w saving no\n                    return "ok"'
        try:
            _osascript(_tab_script(sid, body))
        except Exception:
            pass  # already closed

    path = _session_file_path(project_dir)
    try:
        path.unlink()
    except OSError:
        pass

    print("Terminal.app session killed.")
    return True
