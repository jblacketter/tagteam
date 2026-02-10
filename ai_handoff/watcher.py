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
from ai_handoff.state import read_state, get_state_path


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


def is_agent_idle(pane_target: str) -> bool:
    """Check if an agent TUI is at its input prompt (idle, ready for input).

    Looks for prompt indicators in the last few lines of the pane:
    - Claude Code: ❯ or ›  followed by placeholder text or empty input
    - Codex: › followed by placeholder or empty
    - Also checks for "? for shortcuts" which appears when idle
    """
    content = capture_pane(pane_target, last_n_lines=5)
    if not content.strip():
        return False

    lines = content.strip().splitlines()
    # Check last few lines for idle indicators
    tail = "\n".join(lines[-4:])

    # Agent is busy if it shows these patterns
    busy_patterns = [
        "esc to interrupt",
        "thinking",
        "Running",
        "Do you want to proceed",
        "Do you want to make this edit",
    ]
    for pattern in busy_patterns:
        if pattern.lower() in tail.lower():
            return False

    # Agent is idle if showing prompt indicators
    idle_patterns = [
        "? for shortcuts",
        "context left",
    ]
    for pattern in idle_patterns:
        if pattern.lower() in tail.lower():
            return True

    return False


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
            _log(f"   Waiting for agent in {pane_target} to be idle...")
            if not wait_for_idle(pane_target, timeout=300.0, poll_interval=5.0):
                _log(f"   WARNING: Agent in {pane_target} not idle after 5m, sending anyway")

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

            if current_status == "ready" and command:
                _log(f">> {agent_name}'s turn"
                     f" (phase: {phase}, round: {round_num})")

                if mode == "tmux":
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
                _log(f"** Cycle complete: {result}")
                notify_macos("AI Handoff", f"Cycle complete: {result}")

            elif current_status == "escalated":
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
            if mode not in ("notify", "tmux"):
                print(f"Invalid mode: {mode}. Use 'notify' or 'tmux'.")
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
            print("  --mode MODE        'notify' or 'tmux' (default: notify)")
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
