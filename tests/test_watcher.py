"""Tests for the _StateProcessor class — the per-tick processing logic
extracted from watch() so it can be reused by event-driven watcher modes.

These tests exercise tick() with crafted state dicts and verify the right
notifications, send-keys, and roadmap-advance calls happen. We do not
test the polling loop itself (it's a thin wrapper); we test the processor
that both polling and event modes call.
"""
from unittest.mock import MagicMock, patch

import pytest

from tagteam.watcher import _StateProcessor


def _make_processor(mode="notify", **overrides):
    defaults = dict(
        mode=mode,
        lead_name="Claude",
        reviewer_name="Codex",
        lead_pane="tagteam:0.0",
        reviewer_pane="tagteam:0.2",
        lead_session_id="lead-sid" if mode == "iterm2" else None,
        reviewer_session_id="rev-sid" if mode == "iterm2" else None,
        confirm=False,
        timeout_minutes=30,
        project_dir=".",
        max_retries=3,
        retry_delay=2.0,
        pre_send_delay=1.0,
    )
    defaults.update(overrides)
    return _StateProcessor(**defaults)


def _state(seq, status="ready", turn="lead", command="/handoff",
           phase="p1", round_=1, **extra):
    s = {
        "seq": seq,
        "status": status,
        "turn": turn,
        "command": command,
        "phase": phase,
        "round": round_,
        "updated_at": f"2026-05-03T00:00:{seq:02d}+00:00",
    }
    s.update(extra)
    return s


# --- First-poll bootstrap ---

def test_first_tick_with_non_ready_state_records_seq_and_returns():
    """First tick on a non-actionable state should just record seq."""
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=5, status="done"))
    assert p.last_processed_seq == 5
    notify.assert_not_called()


def test_first_tick_with_ready_state_processes_immediately():
    """First tick on a ready state should dispatch (watcher restart mid-cycle)."""
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=5, status="ready", turn="lead", command="/handoff"))
    assert p.last_processed_seq == 5
    notify.assert_called_once()
    args = notify.call_args[0]
    assert "Claude" in args[1]


# --- Seq dedup ---

def test_unchanged_seq_is_a_noop():
    """Same seq twice should not re-dispatch."""
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=5, status="ready"))
        notify.reset_mock()
        p.tick(_state(seq=5, status="ready"))
    notify.assert_not_called()


def test_advancing_seq_redispatches():
    """A new seq with status=ready should re-dispatch."""
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=5, status="ready"))
        p.tick(_state(seq=6, status="ready", turn="reviewer"))
    assert notify.call_count == 2
    assert "Codex" in notify.call_args[0][1]


# --- Status dispatch (notify mode) ---

def test_done_status_notifies_lead_via_completion_message():
    """Transition to done after a ready state should notify completion.
    (First-poll bootstrap on done would just record seq and skip notify —
    notifications fire only when status TRANSITIONS, not on initial pickup.)"""
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=1, status="ready"))  # establish baseline
        notify.reset_mock()
        p.tick(_state(seq=2, status="done", result="approved"))
    notify.assert_called_once()
    assert "complete" in notify.call_args[0][1].lower()


def test_escalated_status_with_pause_reason_logs_pause():
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=1, status="ready"))
        notify.reset_mock()
        p.tick(_state(seq=2, status="escalated", reason="needs human"))
    notify.assert_called_once()
    assert "needs human" in notify.call_args[0][1].lower()


def test_aborted_status_notifies_with_reason():
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=1, status="ready"))
        notify.reset_mock()
        p.tick(_state(seq=2, status="aborted", reason="user-killed"))
    notify.assert_called_once()
    assert "user-killed" in notify.call_args[0][1]


def test_working_status_does_not_notify():
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=1, status="working", turn="lead"))
    notify.assert_not_called()


# --- Watchdog re-send ---

def test_watchdog_resends_after_resend_timeout():
    """If state stays 'ready' for >RESEND_TIMEOUT, re-dispatch on next tick."""
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=5, status="ready"))
        # Simulate time passing past the resend timeout
        p.last_ready_send_time = (
            p.last_ready_send_time - p.RESEND_TIMEOUT - 1
        )
        notify.reset_mock()
        p.tick(_state(seq=5, status="ready"))  # same seq
    notify.assert_called_once()


def test_watchdog_does_not_resend_within_timeout():
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=5, status="ready"))
        notify.reset_mock()
        p.tick(_state(seq=5, status="ready"))  # immediate, same seq
    notify.assert_not_called()


# --- iterm2 mode dispatch ---

def test_iterm2_mode_calls_send_iterm_command():
    p = _make_processor(mode="iterm2")
    with patch("tagteam.watcher.send_iterm_command",
               return_value=True) as send:
        p.tick(_state(seq=1, status="ready", turn="lead",
                      command="/handoff"))
    send.assert_called_once()
    assert send.call_args[0][0] == "lead-sid"
    assert send.call_args[0][1] == "/handoff"


def test_iterm2_mode_uses_reviewer_session_when_turn_is_reviewer():
    p = _make_processor(mode="iterm2")
    with patch("tagteam.watcher.send_iterm_command",
               return_value=True) as send:
        p.tick(_state(seq=1, status="ready", turn="reviewer"))
    assert send.call_args[0][0] == "rev-sid"


# --- tmux mode dispatch ---

def test_tmux_mode_calls_send_tmux_keys():
    p = _make_processor(mode="tmux")
    with patch("tagteam.watcher.send_tmux_keys", return_value=True) as send:
        p.tick(_state(seq=1, status="ready", turn="lead"))
    send.assert_called_once()
    assert send.call_args[0][0] == "tagteam:0.0"


# --- Roadmap advance ---

def test_done_status_with_roadmap_advance_skips_completion_message():
    """If _try_roadmap_advance returns a new state, no completion notify."""
    p = _make_processor()
    with patch("tagteam.watcher._try_roadmap_advance",
               return_value={"phase": "next", "type": "plan"}), \
         patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=1, status="ready"))  # baseline
        notify.reset_mock()
        p.tick(_state(seq=2, status="done", result="approved"))
    notify.assert_not_called()
