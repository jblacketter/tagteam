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

from tagteam.config import read_config, get_agent_names
from tagteam.state import read_state, update_state, get_state_path, normalize_phase_key


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
    from tagteam.iterm import get_session_contents
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
    from tagteam.iterm import write_text_to_session, session_id_is_valid

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

    Includes a staleness guard: re-reads state before writing to ensure
    the lead hasn't already advanced past this transition.
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
        # Plan approved → hand to lead to implement.
        # STALENESS GUARD: If the state has already moved to type=impl,
        # the lead already started the impl cycle — skip this transition.
        fresh = read_state(project_dir)
        if fresh and fresh.get("type") == "impl":
            _log("   SKIP: plan→impl advance already happened (type is impl)")
            return None
        if fresh and fresh.get("status") == "ready" and fresh.get("turn") == "reviewer":
            _log("   SKIP: lead already submitted for review")
            return None

        phase = state.get("phase", "?")
        seq = state.get("seq", 0)
        updates = {
            "turn": "lead",
            "status": "ready",
            "result": None,
            "command": f"/handoff start {phase} impl",
        }
        new_state = update_state(updates, project_dir, expected_seq=seq)
        if new_state is None:
            _log("   SKIP: state changed since approval detected (seq mismatch)")
            return None
        _log(f"   AUTO-ADVANCE: plan approved → lead implements"
             f" (phase: {phase})")
        return new_state

    if current_type == "impl":
        # Impl approved → advance to next phase or complete.
        current_phase = state.get("phase")

        # STALENESS GUARD: If state already shows a different phase,
        # the lead already started the next phase — skip.
        fresh = read_state(project_dir)
        if fresh:
            fresh_phase = fresh.get("phase")
            if fresh_phase and current_phase:
                # Normalize both sides to avoid false positives when formats differ
                if normalize_phase_key(fresh_phase) != normalize_phase_key(current_phase):
                    _log(f"   SKIP: impl advance already happened"
                         f" (phase moved to {fresh_phase})")
                    return None
            if (fresh.get("status") == "ready"
                    and fresh.get("turn") == "reviewer"
                    and fresh.get("type") == "plan"):
                _log("   SKIP: lead already submitted next phase for review")
                return None

        # Normalize existing completed list to clean up any corruption from previous runs
        # (state["phase"] might be full phase-N-slug format from cycle commands)
        completed_normalized = [normalize_phase_key(p) for p in completed]

        if current_phase:
            phase_slug = normalize_phase_key(current_phase)
            if phase_slug not in completed_normalized:
                completed = completed_normalized + [phase_slug]
            else:
                # Already completed, but update to normalized list anyway to fix corruption
                completed = completed_normalized

        seq = state.get("seq", 0)

        if idx + 1 < len(queue):
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
            new_state = update_state(updates, project_dir, expected_seq=seq)
            if new_state is None:
                _log("   SKIP: state changed since approval detected (seq mismatch)")
                return None
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
            new_state = update_state(updates, project_dir, expected_seq=seq)
            if new_state is None:
                _log("   SKIP: state changed since approval detected (seq mismatch)")
                return None
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


class _StateProcessor:
    """Encapsulates per-tick processing logic for the watcher.

    A single instance is reused across all loop iterations so it carries
    the small bit of mutable state the loop needs (last seq, idle/send
    timing). Both the polling loop and the future event-driven loop call
    the same `tick()` method, ensuring identical behavior regardless of
    what triggered the tick.
    """

    RESEND_TIMEOUT = 300  # seconds — re-send command if still 'ready'

    def __init__(
        self,
        *,
        mode: str,
        lead_name: str,
        reviewer_name: str,
        lead_pane: str,
        reviewer_pane: str,
        lead_session_id: str | None,
        reviewer_session_id: str | None,
        confirm: bool,
        timeout_minutes: int,
        project_dir: str,
        max_retries: int,
        retry_delay: float,
        pre_send_delay: float,
    ):
        self.mode = mode
        self.lead_name = lead_name
        self.reviewer_name = reviewer_name
        self.lead_pane = lead_pane
        self.reviewer_pane = reviewer_pane
        self.lead_session_id = lead_session_id
        self.reviewer_session_id = reviewer_session_id
        self.confirm = confirm
        self.timeout_minutes = timeout_minutes
        self.project_dir = project_dir
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.pre_send_delay = pre_send_delay

        self.last_processed_seq: int | None = None
        self.last_processed_at: str | None = None
        self.idle_since: float = time.time()
        self.last_ready_send_time: float | None = None

    def try_repair(self) -> None:
        """Opportunistic shadow-DB repair, bounded by repair's own backoff."""
        try:
            from tagteam import repair as _repair
            if _repair.should_attempt_repair(self.project_dir):
                res = _repair.attempt_repair(self.project_dir)
                if res["success"]:
                    _log("[repair] db_invalid cleared after successful "
                         "rebuild + parity check")
                else:
                    if _repair.needs_louder_signal(self.project_dir):
                        _log(f"[repair] WARN: db_invalid set for >24h "
                             f"without recovery (last reason: "
                             f"{res.get('reason')})")
        except Exception as e:
            _log(f"[repair] error during opportunistic repair: {e}")

    def tick(self, state: dict) -> None:
        """Process one state observation. No-op if seq hasn't advanced
        (unless the watchdog re-send window has elapsed)."""
        current_seq = state.get("seq", 0)
        updated_at = state.get("updated_at") or "__missing__"

        # First-poll bootstrap: if the existing state isn't actionable,
        # just record and wait. If it IS actionable (ready), pick it up.
        if self.last_processed_seq is None:
            if state.get("status") != "ready":
                self.last_processed_seq = current_seq
                self.last_processed_at = updated_at
                self.idle_since = time.time()
                _log(f"Current state: {state.get('status', '?')}"
                     f" (turn: {state.get('turn', '?')},"
                     f" phase: {state.get('phase', '?')})")
                return
            _log("Picking up active turn from existing state")

        # Seq dedup with stuck-agent + watchdog re-send logic
        if current_seq == self.last_processed_seq:
            elapsed = time.time() - self.idle_since
            if (elapsed > self.timeout_minutes * 60
                    and state.get("status") == "working"):
                _log(f"Warning: no state change for {self.timeout_minutes}m"
                     " - agent may be stuck")
                notify_macos("Tagteam",
                             f"No activity for {self.timeout_minutes}m")
                self.idle_since = time.time()

            if (state.get("status") == "ready"
                    and self.last_ready_send_time is not None
                    and (time.time() - self.last_ready_send_time
                         > self.RESEND_TIMEOUT)):
                _log("Watchdog: state still 'ready' after 5m"
                     " — re-sending command")
                self.last_ready_send_time = None  # avoid rapid re-sends
                # fall through to re-process
            else:
                return

        # New state (or watchdog re-send) — record and dispatch
        self.last_processed_seq = current_seq
        self.last_processed_at = updated_at
        self.idle_since = time.time()
        self._dispatch(state)

    def _dispatch(self, state: dict) -> None:
        current_status = state.get("status")
        current_turn = state.get("turn")
        command = state.get("command", "")
        phase = state.get("phase", "?")
        round_num = state.get("round", "?")

        agent_name = (self.lead_name if current_turn == "lead"
                      else self.reviewer_name)
        pane = self.lead_pane if current_turn == "lead" else self.reviewer_pane
        session_id = (self.lead_session_id if current_turn == "lead"
                      else self.reviewer_session_id)

        if current_status == "ready" and command:
            self._handle_ready(agent_name, pane, session_id,
                               command, phase, round_num)
        elif current_status == "working":
            _log(f"   {agent_name} is working...")
        elif current_status == "done":
            self._handle_done(state)
        elif current_status == "escalated":
            self._handle_escalated(state)
        elif current_status == "aborted":
            reason = state.get("reason", "unknown")
            _log(f"-- Cycle aborted: {reason}")
            notify_macos("Tagteam", f"Cycle aborted: {reason}")

    def _handle_ready(self, agent_name, pane, session_id,
                      command, phase, round_num):
        _log(f">> {agent_name}'s turn"
             f" (phase: {phase}, round: {round_num})")
        send_success = False

        if self.mode == "iterm2":
            if self.confirm:
                try:
                    input(f"[{_ts()}]    Press Enter to send"
                          f" '{command}' to {agent_name}...")
                except EOFError:
                    return
            send_success = send_iterm_command(
                session_id, command,
                max_retries=self.max_retries,
                retry_delay=self.retry_delay,
            )
            if send_success:
                _log(f"   Sent to {agent_name}: {command}")
            else:
                _log(f"   FAILED: Could not send to"
                     f" {agent_name} after {self.max_retries} attempts")
                notify_macos("Tagteam", f"Failed to send to {agent_name}")

        elif self.mode == "tmux":
            if self.confirm:
                try:
                    input(f"[{_ts()}]    Press Enter to send"
                          f" '{command}' to {pane}...")
                except EOFError:
                    return
            send_success = send_tmux_keys(
                pane, command,
                max_retries=self.max_retries,
                retry_delay=self.retry_delay,
                pre_send_delay=self.pre_send_delay,
            )
            if send_success:
                _log(f"   Sent to {pane}: {command}")
            else:
                _log(f"   FAILED: Could not send to '{pane}'"
                     f" after {self.max_retries} attempts")
                notify_macos("Tagteam",
                             f"Failed to send to {pane} after retries")

        elif self.mode == "notify":
            send_success = True
            _log(f"   Command: {command}")
            notify_macos("Tagteam", f"{agent_name}'s turn: {command}")

        # Track send time for watchdog re-send (success OR failure;
        # failure also gets a retry window via watchdog).
        self.last_ready_send_time = time.time()

    def _handle_done(self, state: dict) -> None:
        result = state.get("result", "completed")

        advanced = _try_roadmap_advance(state, self.project_dir)
        if advanced:
            self.last_processed_at = None
            self.idle_since = time.time()
            return

        done_msg = "/handoff"
        if result == "roadmap-complete":
            _log("** Roadmap complete: all phases finished!")
            notify_macos("Tagteam", "Roadmap complete!")
        else:
            _log(f"** Cycle complete: {result}")
            notify_macos("Tagteam", f"Cycle complete: {result}")

        _log(f"   Sending completion notice to {self.lead_name}...")
        if self.mode == "iterm2":
            send_iterm_command(
                self.lead_session_id, done_msg,
                max_retries=self.max_retries,
                retry_delay=self.retry_delay,
            )
        elif self.mode == "tmux":
            send_tmux_keys(
                self.lead_pane, done_msg,
                max_retries=self.max_retries,
                retry_delay=self.retry_delay,
                pre_send_delay=self.pre_send_delay,
            )

    def _handle_escalated(self, state: dict) -> None:
        roadmap = state.get("roadmap") or {}
        pause_reason = roadmap.get("pause_reason") or state.get("reason")
        if pause_reason:
            _log(f"!! Paused: {pause_reason}")
            _log("   Resume with: python -m tagteam state set"
                 " --status ready --turn <lead|reviewer>")
            notify_macos("Tagteam", f"Paused: {pause_reason}")
        else:
            _log("!! Escalated to human arbiter")
            notify_macos("Tagteam", "Escalated to human arbiter!")


def _build_processor(
    *,
    mode: str,
    lead_pane: str,
    reviewer_pane: str,
    confirm: bool,
    timeout_minutes: int,
    project_dir: str,
    max_retries: int,
    retry_delay: float,
    pre_send_delay: float,
) -> _StateProcessor | None:
    """Resolve config + iTerm session IDs into a ready processor.

    Returns None if iterm2 mode is requested but session IDs are missing
    (caller should bail out — error already logged here).
    """
    config_path = Path(project_dir) / "tagteam.yaml"
    config = read_config(config_path)
    if config:
        lead_name, reviewer_name = get_agent_names(config)
        lead_name = lead_name or "lead"
        reviewer_name = reviewer_name or "reviewer"
    else:
        lead_name = "lead"
        reviewer_name = "reviewer"

    lead_session_id = None
    reviewer_session_id = None
    if mode == "iterm2":
        from tagteam.iterm import get_session_id
        lead_session_id = get_session_id("lead", project_dir)
        reviewer_session_id = get_session_id("reviewer", project_dir)
        if not lead_session_id or not reviewer_session_id:
            _log("ERROR: Could not find session IDs in .handoff-session.json")
            _log("  Run 'python -m tagteam session start' first.")
            return None

    return _StateProcessor(
        mode=mode,
        lead_name=lead_name,
        reviewer_name=reviewer_name,
        lead_pane=lead_pane,
        reviewer_pane=reviewer_pane,
        lead_session_id=lead_session_id,
        reviewer_session_id=reviewer_session_id,
        confirm=confirm,
        timeout_minutes=timeout_minutes,
        project_dir=project_dir,
        max_retries=max_retries,
        retry_delay=retry_delay,
        pre_send_delay=pre_send_delay,
    )


def _log_startup_banner(processor: _StateProcessor, interval: int) -> None:
    _log(f"Watching handoff-state.json"
         f" (interval: {interval}s, mode: {processor.mode})")
    _log(f"Lead: {processor.lead_name} | Reviewer: {processor.reviewer_name}")
    if processor.mode == "tmux":
        _log(f"Panes: lead={processor.lead_pane},"
             f" reviewer={processor.reviewer_pane}")
        for name, pane in [("lead", processor.lead_pane),
                           ("reviewer", processor.reviewer_pane)]:
            if pane_exists(pane):
                _log(f"  {name} pane OK: {pane}")
            else:
                _log(f"  WARNING: {name} pane '{pane}' not found")
    elif processor.mode == "iterm2":
        from tagteam.iterm import session_id_is_valid
        for name, sid in [("lead", processor.lead_session_id),
                          ("reviewer", processor.reviewer_session_id)]:
            if session_id_is_valid(sid):
                _log(f"  {name} session OK: {sid}")
            else:
                _log(f"  WARNING: {name} session '{sid}'"
                     " not found in iTerm2")
    if processor.confirm:
        _log("Confirm mode: will pause before sending commands")
    print(flush=True)


def watch(
    interval: int = 10,
    mode: str = "notify",
    lead_pane: str = "tagteam:0.0",
    reviewer_pane: str = "tagteam:0.2",
    confirm: bool = False,
    timeout_minutes: int = 30,
    project_dir: str = ".",
    max_retries: int = 3,
    retry_delay: float = 2.0,
    pre_send_delay: float = 1.0,
    force_poll: bool = False,
) -> None:
    """Main watch loop. Blocks until interrupted with Ctrl-C.

    Delegates per-tick work to _StateProcessor. Trigger source is either
    polling (default fallback when ``watchdog`` isn't installed, or when
    ``force_poll=True``) or watchdog filesystem events (when available).
    """
    processor = _build_processor(
        mode=mode,
        lead_pane=lead_pane,
        reviewer_pane=reviewer_pane,
        confirm=confirm,
        timeout_minutes=timeout_minutes,
        project_dir=project_dir,
        max_retries=max_retries,
        retry_delay=retry_delay,
        pre_send_delay=pre_send_delay,
    )
    if processor is None:
        return

    _log_startup_banner(processor, interval)

    if not force_poll:
        from tagteam import watcher_events
        if watcher_events.is_available():
            _log("[trigger] event-driven (watchdog) with 30s heartbeat")
            if _run_event_loop(processor, project_dir):
                return
            # Event loop failed at startup — fall through to poll mode.
            _log(f"[trigger] falling back to poll mode"
                 f" (interval={interval}s)")
        else:
            _log("[trigger] poll mode (install `tagteam[event]`"
                 " to enable event-driven mode)")
    else:
        _log(f"[trigger] poll mode (forced via --poll, interval={interval}s)")

    _run_poll_loop(processor, project_dir, interval)


def _run_poll_loop(processor: "_StateProcessor",
                   project_dir: str, interval: int) -> None:
    try:
        while True:
            processor.try_repair()
            state = read_state(project_dir)
            if state is not None:
                processor.tick(state)
            time.sleep(interval)
    except KeyboardInterrupt:
        _log("Watcher stopped.")


def _run_event_loop(processor: "_StateProcessor", project_dir: str) -> bool:
    """Run the event-driven loop. Returns True on clean exit (Ctrl-C),
    False if the watchdog observer failed to start (caller should fall
    back to poll mode).

    Filesystem-event backends can fail at runtime even when watchdog
    imports cleanly — for example macOS FSEvents can raise
    ``SystemError: Cannot start fsevents stream`` on certain volumes,
    and inotify can hit ``OSError(ENOSPC)`` when the user's
    inotify-watch quota is exhausted. In those cases we log the reason
    and let watch() drop back to polling so the watcher keeps running.
    """
    from tagteam import watcher_events

    state_path = get_state_path(project_dir)

    def on_change():
        processor.try_repair()
        state = read_state(project_dir)
        if state is not None:
            processor.tick(state)

    try:
        watcher_events.watch_with_events(state_path, on_change)
    except KeyboardInterrupt:
        _log("Watcher stopped.")
        return True
    except Exception as e:
        _log(f"[trigger] event mode failed:"
             f" {type(e).__name__}: {e}")
        return False
    return True


# --- CLI entry point ---

def _auto_detect_mode(project_dir: str = ".") -> tuple[str, str | None]:
    """Pick the best send-keys mode based on what's actually set up.

    Returns (mode, reason). `reason` is a human-readable one-liner the
    caller can log so the operator knows why this mode was chosen
    (or why we fell back to notify).

    Priority:
      1. iterm2 — if `.handoff-session.json` has session IDs for BOTH
         lead and reviewer roles. Means `tagteam session start
         --backend iterm2 --launch` ran successfully.
      2. tmux — if the default tmux session exists. Means
         `tagteam session start --backend tmux --launch` ran.
      3. notify — fallback. The watcher will pop macOS notifications
         but won't auto-type into either agent's terminal.
    """
    try:
        from tagteam.iterm import get_session_id
        lead_sid = get_session_id("lead", project_dir)
        reviewer_sid = get_session_id("reviewer", project_dir)
        if lead_sid and reviewer_sid:
            return "iterm2", "iterm2 session IDs found"
    except Exception:
        pass

    try:
        from tagteam.session import session_exists
        if session_exists():
            return "tmux", "tmux session 'tagteam' found"
    except Exception:
        pass

    return "notify", (
        "no iterm2 session file or tmux session detected — "
        "watcher will only post notifications. Run "
        "`tagteam session start --launch` to enable auto-send."
    )


def watch_command(args: list[str]) -> int:
    """Parse CLI args and start the watcher."""
    interval = 10
    mode = None  # None = auto-detect; explicit --mode overrides.
    lead_pane = "tagteam:0.0"
    reviewer_pane = "tagteam:0.2"
    confirm = False
    timeout_minutes = 30
    max_retries = 3
    retry_delay = 2.0
    pre_send_delay = 1.0
    force_poll = False

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
        elif arg == "--poll":
            force_poll = True
            i += 1
        elif arg in ("-h", "--help"):
            print("Usage: python -m tagteam watch [options]")
            print()
            print("Options:")
            print("  --interval N       Poll interval in seconds (default: 10)")
            print("  --mode MODE        'notify', 'tmux', or 'iterm2'")
            print("                     (default: auto-detect from session state)")
            print("  --lead-pane TARGET tmux pane target for lead (default: tagteam:0.0)")
            print("  --reviewer-pane T  tmux pane target for reviewer (default: tagteam:0.2)")
            print("  --confirm          Pause for confirmation before sending commands")
            print("  --timeout N        Alert after N minutes of inactivity (default: 30)")
            print("  --retries N        Max send retries on failure (default: 3)")
            print("  --retry-delay N    Seconds between retries (default: 2.0)")
            print("  --send-delay N     Seconds to wait before sending (default: 1.0)")
            print("  --poll             Force polling mode (skip watchdog event detection)")
            return 0
        else:
            print(f"Unknown argument: {arg}")
            return 1

    if mode is None:
        mode, reason = _auto_detect_mode(".")
        _log(f"[mode] auto-detected: {mode} ({reason})")

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
        force_poll=force_poll,
    )
    return 0
