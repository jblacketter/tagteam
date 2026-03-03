"""
Watcher daemon for automated handoff orchestration.

Polls handoff-state.json and triggers agents via desktop notifications
or tmux send-keys when it's their turn.
"""

import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ai_handoff.config import read_config, get_agent_names
from ai_handoff.state import read_state, update_state, get_state_path


def notify_macos(title: str, message: str) -> None:
    """Send macOS desktop notification."""
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{message}" with title "{title}"'],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def pane_exists(pane_target: str) -> bool:
    """Check if a tmux pane exists."""
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-t", pane_target, "-p", "#{pane_id}"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() != ""
    except Exception:
        return False


def capture_pane(pane_target: str, last_n_lines: int = 5) -> str:
    """Capture the last N lines of a tmux pane's visible content."""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", pane_target, "-p",
             "-S", str(-last_n_lines)],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout
        return ""
    except Exception:
        return ""


BUSY_PATTERNS = [
    "esc to interrupt",
    "thinking",
    "Running",
    "Do you want to proceed",
    "Do you want to make this edit",
]

IDLE_PATTERNS = [
    # Claude Code
    "? for shortcuts",
    "context left",
    "for help",
    "> ",
    "\u276f",           # ❯ — Claude Code's actual prompt character
    "accept edits",     # status bar: "⏵⏵ accept edits on (shift+tab to cycle)"
    "shift+tab",        # alternate match for the same status bar
    # Codex
    "/skills to list",
    "/model to change",
    "type a message",
    "enter a command",
    # Shell prompt (agent not yet started)
    "$ ",
    "% ",
    "\ue0b0",          # Powerline prompt separator
    "@",               # user@hostname in shell prompts
]


def _check_idle_patterns(content: str) -> bool:
    """Check terminal content for idle/busy patterns.

    Returns True if the agent appears idle (at input prompt),
    False if busy or content is empty.
    """
    if not content.strip():
        return False

    lines = content.strip().splitlines()
    tail = "\n".join(lines[-4:]).lower()

    for pattern in BUSY_PATTERNS:
        if pattern.lower() in tail:
            return False

    for pattern in IDLE_PATTERNS:
        if pattern.lower() in tail:
            return True

    return False


def is_agent_idle(pane_target: str) -> bool:
    """Check if an agent TUI in a tmux pane is idle (at input prompt)."""
    content = capture_pane(pane_target, last_n_lines=5)
    return _check_idle_patterns(content)


def is_agent_idle_iterm(session_id: str, debug: bool = False) -> bool:
    """Check if an agent TUI in an iTerm2 session is idle."""
    from ai_handoff.iterm import get_session_contents
    content = get_session_contents(session_id, last_n_lines=5)
    idle = _check_idle_patterns(content)
    if debug and not idle:
        tail = content.strip().splitlines()[-2:] if content.strip() else []
        _log(f"   (not idle yet, last lines: {tail!r})")
    return idle


def wait_for_idle(
    pane_target: str,
    timeout: float = 300.0,
    poll_interval: float = 5.0,
) -> bool:
    """Wait until the agent in the given pane is idle, up to timeout seconds."""
    start = time.time()
    while time.time() - start < timeout:
        if is_agent_idle(pane_target):
            return True
        time.sleep(poll_interval)
    return False


def send_tmux_keys(
    pane_target: str,
    command: str,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    pre_send_delay: float = 1.0,
) -> bool:
    """Send keys to a tmux pane with retry logic.

    Steps:
    1. Verify pane exists
    2. Wait for agent to be idle (at input prompt)
    3. Clear any partial input (Escape x3 + C-c)
    4. Send command as literal text + C-m
    5. Retry on failure
    """
    if not pane_exists(pane_target):
        _log(f"   ERROR: Pane '{pane_target}' does not exist")
        return False

    for attempt in range(1, max_retries + 1):
        try:
            # Wait for agent to be idle before sending
            _log(f"   Checking if agent in {pane_target} is idle...")
            if not wait_for_idle(pane_target, timeout=15.0, poll_interval=3.0):
                _log(f"   Idle detection inconclusive for {pane_target}, proceeding after 15s")

            if pre_send_delay > 0:
                time.sleep(pre_send_delay)

            # Clear any partial input — different TUIs need different keys:
            # Escape x3 clears Claude Code, C-c clears Codex
            for _ in range(3):
                subprocess.run(
                    ["tmux", "send-keys", "-t", pane_target, "Escape"],
                    capture_output=True, timeout=5,
                )
                time.sleep(0.15)
            time.sleep(0.3)
            subprocess.run(
                ["tmux", "send-keys", "-t", pane_target, "C-c"],
                capture_output=True, timeout=5,
            )
            time.sleep(0.5)

            # Send command as literal text (-l flag prevents key name interpretation)
            subprocess.run(
                ["tmux", "send-keys", "-t", pane_target, "-l", command],
                capture_output=True, text=True, timeout=5, check=True,
            )
            time.sleep(1.0)

            # Send C-m (carriage return) to submit — more reliable
            # than "Enter" with TUI agents like Claude Code and Codex
            result = subprocess.run(
                ["tmux", "send-keys", "-t", pane_target, "C-m"],
                capture_output=True, text=True, timeout=5,
            )

            if result.returncode == 0:
                return True

            _log(f"   Attempt {attempt}/{max_retries} failed"
                 f" (rc={result.returncode})")

        except subprocess.CalledProcessError as e:
            _log(f"   Attempt {attempt}/{max_retries} error:"
                 f" {e.stderr.strip() if e.stderr else e}")
        except Exception as e:
            _log(f"   Attempt {attempt}/{max_retries} error: {e}")

        if attempt < max_retries:
            _log(f"   Retrying in {retry_delay}s...")
            time.sleep(retry_delay)

    return False


def wait_for_idle_iterm(
    session_id: str,
    timeout: float = 300.0,
    poll_interval: float = 5.0,
) -> bool:
    """Wait until the agent in the given iTerm2 session is idle."""
    start = time.time()
    while time.time() - start < timeout:
        if is_agent_idle_iterm(session_id, debug=True):
            return True
        time.sleep(poll_interval)
    return False


def send_iterm_command(
    session_id: str,
    command: str,
    max_retries: int = 3,
    retry_delay: float = 2.0,
) -> bool:
    """Send a command to an iTerm2 session with retry logic.

    Simpler than tmux: no pre-send input clearing is needed. Submission
    is handled inside write_text_to_session() with an explicit CR.
    """
    from ai_handoff.iterm import write_text_to_session, session_id_is_valid

    if not session_id_is_valid(session_id):
        _log(f"   ERROR: Session '{session_id}' does not exist")
        return False

    for attempt in range(1, max_retries + 1):
        _log(f"   Checking if agent is idle...")
        if not wait_for_idle_iterm(session_id, timeout=10.0, poll_interval=2.0):
            _log("   Idle detection inconclusive, proceeding after 10s")

        if write_text_to_session(session_id, command):
            return True

        _log(f"   Attempt {attempt}/{max_retries} failed")
        if attempt < max_retries:
            _log(f"   Retrying in {retry_delay}s...")
            time.sleep(retry_delay)

    return False


def _try_roadmap_advance(state: dict, project_dir: str = ".") -> dict | None:
    """Attempt to auto-advance to the next phase in full-roadmap mode.

    Called when status is "done" and result is "approved".
    Returns the new state if advanced, or None if no advance needed.
    """
    run_mode = state.get("run_mode", "single-phase")
    if run_mode != "full-roadmap":
        return None

    roadmap = state.get("roadmap")
    if not roadmap:
        return None

    result = state.get("result")
    current_type = state.get("type")

    if result != "approved":
        return None

    queue = roadmap.get("queue", [])
    idx = roadmap.get("current_index", 0)
    completed = roadmap.get("completed", [])

    if current_type == "plan":
        # Plan approved → hand to lead to implement and create impl cycle.
        # Lead must run `/handoff start [phase] impl` to create cycle docs
        # and submit for review — we do NOT skip to reviewer directly.
        phase = state.get("phase", "?")
        updates = {
            "turn": "lead",
            "status": "ready",
            "result": None,
            "command": f"/handoff start {phase} impl",
        }
        new_state = update_state(updates, project_dir)
        _log(f"   AUTO-ADVANCE: plan approved → lead implements"
             f" (phase: {phase})")
        return new_state

    if current_type == "impl":
        # Impl approved → advance to next phase or complete
        current_phase = state.get("phase")
        if current_phase and current_phase not in completed:
            completed = completed + [current_phase]

        if idx + 1 < len(queue):
            # More phases remain — hand to lead to create the next
            # plan cycle. Lead must run `/handoff start [next-phase]`
            # to create plan docs before reviewer sees anything.
            next_idx = idx + 1
            next_phase = queue[next_idx]
            roadmap_update = {
                "queue": queue,
                "current_index": next_idx,
                "completed": completed,
                "pause_reason": None,
            }
            updates = {
                "phase": next_phase,
                "type": "plan",
                "round": 1,
                "turn": "lead",
                "status": "ready",
                "result": None,
                "roadmap": roadmap_update,
                "command": f"/handoff start {next_phase}",
            }
            new_state = update_state(updates, project_dir)
            _log(f"   AUTO-ADVANCE: impl approved → lead starts next phase"
                 f" ({next_phase})")
            return new_state
        else:
            # Last phase — roadmap complete
            roadmap_update = {
                "queue": queue,
                "current_index": idx,
                "completed": completed,
                "pause_reason": None,
            }
            updates = {
                "status": "done",
                "result": "roadmap-complete",
                "roadmap": roadmap_update,
            }
            new_state = update_state(updates, project_dir)
            _log("   ROADMAP COMPLETE: all phases finished!")
            return new_state

    return None


def _log(msg: str) -> None:
    """Print with timestamp and flush (required for tmux pane output)."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        if value.endswith("Z"):
            try:
                parsed = datetime.fromisoformat(value[:-1] + "+00:00")
            except ValueError:
                return None
        else:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def watch(
    interval: int = 10,
    mode: str = "notify",
    lead_pane: str = "ai-handoff:0.0",
    reviewer_pane: str = "ai-handoff:0.2",
    confirm: bool = False,
    timeout_minutes: int = 30,
    project_dir: str = ".",
    max_retries: int = 3,
    retry_delay: float = 2.0,
    pre_send_delay: float = 1.0,
) -> None:
    """Main watch loop. Blocks until interrupted with Ctrl-C."""

    config_path = Path(project_dir) / "ai-handoff.yaml"
    config = read_config(config_path)
    if config:
        lead_name, reviewer_name = get_agent_names(config)
        lead_name = lead_name or "lead"
        reviewer_name = reviewer_name or "reviewer"
    else:
        lead_name = "lead"
        reviewer_name = "reviewer"

    # For iterm2 mode, discover session IDs from .handoff-session.json
    lead_session_id = None
    reviewer_session_id = None
    if mode == "iterm2":
        from ai_handoff.iterm import get_session_id, session_id_is_valid
        lead_session_id = get_session_id("lead", project_dir)
        reviewer_session_id = get_session_id("reviewer", project_dir)
        if not lead_session_id or not reviewer_session_id:
            _log("ERROR: Could not find session IDs in .handoff-session.json")
            _log("  Run 'python -m ai_handoff session start' first.")
            return

    _log(f"Watching handoff-state.json (interval: {interval}s, mode: {mode})")
    _log(f"Lead: {lead_name} | Reviewer: {reviewer_name}")
    if mode == "tmux":
        _log(f"Panes: lead={lead_pane}, reviewer={reviewer_pane}")
        # Verify panes exist at startup
        for name, pane in [("lead", lead_pane), ("reviewer", reviewer_pane)]:
            if pane_exists(pane):
                _log(f"  {name} pane OK: {pane}")
            else:
                _log(f"  WARNING: {name} pane '{pane}' not found")
    elif mode == "iterm2":
        for name, sid in [("lead", lead_session_id), ("reviewer", reviewer_session_id)]:
            if session_id_is_valid(sid):
                _log(f"  {name} session OK: {sid}")
            else:
                _log(f"  WARNING: {name} session '{sid}' not found in iTerm2")
    if confirm:
        _log("Confirm mode: will pause before sending commands")
    print(flush=True)

    last_processed_at = None
    idle_since = time.time()

    try:
        while True:
            state = read_state(project_dir)

            if state is None:
                time.sleep(interval)
                continue

            updated_at_raw = state.get("updated_at")
            updated_at = updated_at_raw or "__missing__"

            # On first poll: process current state if it's actionable (ready).
            # This ensures a watcher restart mid-cycle picks up the active turn.
            if last_processed_at is None:
                if state.get("status") != "ready":
                    # Not actionable — just record and wait for changes
                    last_processed_at = updated_at
                    idle_since = time.time()
                    _log(f"Current state: {state.get('status', '?')}"
                         f" (turn: {state.get('turn', '?')},"
                         f" phase: {state.get('phase', '?')})")
                    time.sleep(interval)
                    continue
                # State is ready — fall through to process it
                _log("Picking up active turn from existing state")

            if updated_at == last_processed_at:
                # Check for stuck agent
                elapsed = time.time() - idle_since
                if (elapsed > timeout_minutes * 60
                        and state.get("status") == "working"):
                    _log(f"Warning: no state change for {timeout_minutes}m"
                         " - agent may be stuck")
                    notify_macos("AI Handoff", f"No activity for {timeout_minutes}m")
                    idle_since = time.time()  # avoid spamming

                time.sleep(interval)
                continue

            # New state change detected
            last_processed_at = updated_at
            idle_since = time.time()

            current_status = state.get("status")
            current_turn = state.get("turn")
            command = state.get("command", "")
            phase = state.get("phase", "?")
            round_num = state.get("round", "?")

            agent_name = lead_name if current_turn == "lead" else reviewer_name
            pane = lead_pane if current_turn == "lead" else reviewer_pane
            session_id = (lead_session_id if current_turn == "lead"
                          else reviewer_session_id)

            if current_status == "ready" and command:
                _log(f">> {agent_name}'s turn"
                     f" (phase: {phase}, round: {round_num})")

                if mode == "iterm2":
                    if confirm:
                        try:
                            input(f"[{_ts()}]    Press Enter to send"
                                  f" '{command}' to {agent_name}...")
                        except EOFError:
                            break
                    success = send_iterm_command(
                        session_id, command,
                        max_retries=max_retries,
                        retry_delay=retry_delay,
                    )
                    if success:
                        _log(f"   Sent to {agent_name}: {command}")
                    else:
                        _log(f"   FAILED: Could not send to"
                             f" {agent_name} after {max_retries} attempts")
                        notify_macos("AI Handoff",
                                     f"Failed to send to {agent_name}")

                elif mode == "tmux":
                    if confirm:
                        try:
                            input(f"[{_ts()}]    Press Enter to send"
                                  f" '{command}' to {pane}...")
                        except EOFError:
                            break
                    success = send_tmux_keys(
                        pane, command,
                        max_retries=max_retries,
                        retry_delay=retry_delay,
                        pre_send_delay=pre_send_delay,
                    )
                    if success:
                        _log(f"   Sent to {pane}: {command}")
                    else:
                        _log(f"   FAILED: Could not send to"
                             f" '{pane}' after {max_retries} attempts")
                        notify_macos("AI Handoff",
                                     f"Failed to send to {pane} after retries")

                elif mode == "notify":
                    _log(f"   Command: {command}")
                    notify_macos("AI Handoff",
                                 f"{agent_name}'s turn: {command}")

            elif current_status == "working":
                _log(f"   {agent_name} is working...")

            elif current_status == "done":
                result = state.get("result", "completed")

                # In full-roadmap mode, try to auto-advance
                advanced = _try_roadmap_advance(state, project_dir)
                if advanced:
                    # State was updated — reset tracking so next poll
                    # picks up the new "ready" state
                    last_processed_at = None
                    idle_since = time.time()
                    continue

                # Notify lead agent so it knows the cycle finished
                done_msg = "/handoff"
                if result == "roadmap-complete":
                    _log("** Roadmap complete: all phases finished!")
                    notify_macos("AI Handoff", "Roadmap complete!")
                else:
                    _log(f"** Cycle complete: {result}")
                    notify_macos("AI Handoff", f"Cycle complete: {result}")

                _log(f"   Sending completion notice to {lead_name}...")
                if mode == "iterm2":
                    send_iterm_command(
                        lead_session_id, done_msg,
                        max_retries=max_retries,
                        retry_delay=retry_delay,
                    )
                elif mode == "tmux":
                    send_tmux_keys(
                        lead_pane, done_msg,
                        max_retries=max_retries,
                        retry_delay=retry_delay,
                        pre_send_delay=pre_send_delay,
                    )

            elif current_status == "escalated":
                # Show structured reason if available
                roadmap = state.get("roadmap") or {}
                pause_reason = roadmap.get("pause_reason") or state.get("reason")
                if pause_reason:
                    _log(f"!! Paused: {pause_reason}")
                    _log("   Resume with: python -m ai_handoff state set"
                         " --status ready --turn <lead|reviewer>")
                    notify_macos("AI Handoff",
                                 f"Paused: {pause_reason}")
                else:
                    _log("!! Escalated to human arbiter")
                    notify_macos("AI Handoff", "Escalated to human arbiter!")

            elif current_status == "aborted":
                reason = state.get("reason", "unknown")
                _log(f"-- Cycle aborted: {reason}")
                notify_macos("AI Handoff", f"Cycle aborted: {reason}")

            time.sleep(interval)

    except KeyboardInterrupt:
        _log("Watcher stopped.")


# --- CLI entry point ---

def watch_command(args: list[str]) -> int:
    """Parse CLI args and start the watcher."""
    interval = 10
    mode = "notify"
    lead_pane = "ai-handoff:0.0"
    reviewer_pane = "ai-handoff:0.2"
    confirm = False
    timeout_minutes = 30
    max_retries = 3
    retry_delay = 2.0
    pre_send_delay = 1.0

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--interval" and i + 1 < len(args):
            interval = int(args[i + 1])
            i += 2
        elif arg == "--mode" and i + 1 < len(args):
            mode = args[i + 1]
            if mode not in ("notify", "tmux", "iterm2"):
                print(f"Invalid mode: {mode}. Use 'notify', 'tmux', or 'iterm2'.")
                return 1
            i += 2
        elif arg == "--lead-pane" and i + 1 < len(args):
            lead_pane = args[i + 1]
            i += 2
        elif arg == "--reviewer-pane" and i + 1 < len(args):
            reviewer_pane = args[i + 1]
            i += 2
        elif arg == "--confirm":
            confirm = True
            i += 1
        elif arg == "--timeout" and i + 1 < len(args):
            timeout_minutes = int(args[i + 1])
            i += 2
        elif arg == "--retries" and i + 1 < len(args):
            max_retries = int(args[i + 1])
            i += 2
        elif arg == "--retry-delay" and i + 1 < len(args):
            retry_delay = float(args[i + 1])
            i += 2
        elif arg == "--send-delay" and i + 1 < len(args):
            pre_send_delay = float(args[i + 1])
            i += 2
        elif arg in ("-h", "--help"):
            print("Usage: python -m ai_handoff watch [options]")
            print()
            print("Options:")
            print("  --interval N       Poll interval in seconds (default: 10)")
            print("  --mode MODE        'notify', 'tmux', or 'iterm2' (default: notify)")
            print("  --lead-pane TARGET tmux pane target for lead (default: ai-handoff:0.0)")
            print("  --reviewer-pane T  tmux pane target for reviewer (default: ai-handoff:0.2)")
            print("  --confirm          Pause for confirmation before sending commands")
            print("  --timeout N        Alert after N minutes of inactivity (default: 30)")
            print("  --retries N        Max send retries on failure (default: 3)")
            print("  --retry-delay N    Seconds between retries (default: 2.0)")
            print("  --send-delay N     Seconds to wait before sending (default: 1.0)")
            return 0
        else:
            print(f"Unknown argument: {arg}")
            return 1

    watch(
        interval=interval,
        mode=mode,
        lead_pane=lead_pane,
        reviewer_pane=reviewer_pane,
        confirm=confirm,
        timeout_minutes=timeout_minutes,
        max_retries=max_retries,
        retry_delay=retry_delay,
        pre_send_delay=pre_send_delay,
    )
    return 0
