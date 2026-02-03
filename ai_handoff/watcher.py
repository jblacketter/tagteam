"""
Watcher daemon for automated handoff orchestration.

Polls handoff-state.json and triggers agents via desktop notifications
or tmux send-keys when it's their turn.
"""

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


def send_tmux_keys(pane_target: str, command: str) -> bool:
    """Send keys to a tmux pane. Returns True on success."""
    try:
        result = subprocess.run(
            ["tmux", "send-keys", "-t", pane_target, command, "Enter"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


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

    print(f"[{_ts()}] Watching handoff-state.json (interval: {interval}s, mode: {mode})")
    print(f"[{_ts()}] Lead: {lead_name} | Reviewer: {reviewer_name}")
    if mode == "tmux":
        print(f"[{_ts()}] Panes: lead={lead_pane}, reviewer={reviewer_pane}")
    if confirm:
        print(f"[{_ts()}] Confirm mode: will pause before sending commands")
    print()

    watcher_start = datetime.now(timezone.utc)
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
            updated_dt = _parse_timestamp(updated_at_raw)

            # Skip if we already processed this state change (idempotency)
            if last_processed_at is None and updated_dt and updated_dt <= watcher_start:
                last_processed_at = updated_at
                idle_since = time.time()
                time.sleep(interval)
                continue

            if updated_at == last_processed_at:
                # Check for stuck agent
                elapsed = time.time() - idle_since
                if (elapsed > timeout_minutes * 60
                        and state.get("status") == "working"):
                    print(f"[{_ts()}] Warning: no state change for {timeout_minutes}m"
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
                print(f"[{_ts()}] >> {agent_name}'s turn"
                      f" (phase: {phase}, round: {round_num})")

                if mode == "tmux":
                    if confirm:
                        try:
                            input(f"[{_ts()}]    Press Enter to send"
                                  f" '{command}' to {pane}...")
                        except EOFError:
                            break
                    success = send_tmux_keys(pane, command)
                    if success:
                        print(f"[{_ts()}]    Sent to {pane}: {command}")
                    else:
                        print(f"[{_ts()}]    ERROR: Failed to send to"
                              f" tmux pane '{pane}'")
                        notify_macos("AI Handoff",
                                     f"Failed to send to {pane}")

                elif mode == "notify":
                    print(f"[{_ts()}]    Command: {command}")
                    notify_macos("AI Handoff",
                                 f"{agent_name}'s turn: {command}")

            elif current_status == "working":
                print(f"[{_ts()}]    {agent_name} is working...")

            elif current_status == "done":
                result = state.get("result", "completed")
                print(f"[{_ts()}] ** Cycle complete: {result}")
                notify_macos("AI Handoff", f"Cycle complete: {result}")

            elif current_status == "escalated":
                print(f"[{_ts()}] !! Escalated to human arbiter")
                notify_macos("AI Handoff", "Escalated to human arbiter!")

            elif current_status == "aborted":
                reason = state.get("reason", "unknown")
                print(f"[{_ts()}] -- Cycle aborted: {reason}")
                notify_macos("AI Handoff", f"Cycle aborted: {reason}")

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n[{_ts()}] Watcher stopped.")


# --- CLI entry point ---

def watch_command(args: list[str]) -> int:
    """Parse CLI args and start the watcher."""
    interval = 10
    mode = "notify"
    lead_pane = "ai-handoff:0.0"
    reviewer_pane = "ai-handoff:0.2"
    confirm = False
    timeout_minutes = 30

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
    )
    return 0
