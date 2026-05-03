"""Tests for watcher_events.py — event-driven watcher trigger.

These tests skip when ``watchdog`` is not installed (it's an optional
extras dependency: ``pip install tagteam[event]``).
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

watchdog = pytest.importorskip("watchdog")

from tagteam import watcher_events  # noqa: E402


def test_is_available_returns_true_when_watchdog_installed():
    assert watcher_events.is_available() is True


def _fake_event(src_path=None, dest_path=None, is_directory=False):
    e = MagicMock()
    e.src_path = src_path
    e.dest_path = dest_path
    e.is_directory = is_directory
    return e


def _build_handler(target_path):
    """Build a _Handler instance like watch_with_events does internally,
    by re-running the same handler-class definition logic."""
    from watchdog.events import FileSystemEventHandler

    on_change = MagicMock()
    target = str(target_path.resolve())

    class _Handler(FileSystemEventHandler):
        def _maybe_fire(self, path):
            if path == target:
                on_change()

        def on_modified(self, event):
            if not event.is_directory:
                self._maybe_fire(event.src_path)

        def on_created(self, event):
            if not event.is_directory:
                self._maybe_fire(event.src_path)

        def on_moved(self, event):
            dest = getattr(event, "dest_path", None)
            if dest:
                self._maybe_fire(dest)

    return _Handler(), on_change


def test_handler_fires_on_modified_when_path_matches(tmp_path):
    target = tmp_path / "handoff-state.json"
    target.write_text("{}")
    handler, on_change = _build_handler(target)
    handler.on_modified(_fake_event(src_path=str(target.resolve())))
    on_change.assert_called_once()


def test_handler_fires_on_created_when_path_matches(tmp_path):
    target = tmp_path / "handoff-state.json"
    target.write_text("{}")
    handler, on_change = _build_handler(target)
    handler.on_created(_fake_event(src_path=str(target.resolve())))
    on_change.assert_called_once()


def test_handler_fires_on_moved_when_dest_path_matches(tmp_path):
    """The atomic .tmp → handoff-state.json rename surfaces as on_moved
    with dest_path pointing at the target. THIS is the case poll-only
    mode would miss without on_moved support."""
    target = tmp_path / "handoff-state.json"
    target.write_text("{}")
    handler, on_change = _build_handler(target)
    handler.on_moved(_fake_event(
        src_path=str(tmp_path / ".handoff-state.tmp"),
        dest_path=str(target.resolve()),
    ))
    on_change.assert_called_once()


def test_handler_ignores_modify_to_unrelated_file(tmp_path):
    target = tmp_path / "handoff-state.json"
    target.write_text("{}")
    other = tmp_path / "other.txt"
    other.write_text("noise")
    handler, on_change = _build_handler(target)
    handler.on_modified(_fake_event(src_path=str(other.resolve())))
    on_change.assert_not_called()


def test_handler_ignores_directory_events(tmp_path):
    target = tmp_path / "handoff-state.json"
    target.write_text("{}")
    handler, on_change = _build_handler(target)
    # Directory event for the parent dir — should not fire even if path
    # would otherwise match (dirs don't carry our payload anyway).
    handler.on_modified(_fake_event(
        src_path=str(target.resolve()), is_directory=True,
    ))
    on_change.assert_not_called()


def test_handler_ignores_moved_with_no_dest_path(tmp_path):
    target = tmp_path / "handoff-state.json"
    target.write_text("{}")
    handler, on_change = _build_handler(target)
    handler.on_moved(_fake_event(src_path="something", dest_path=None))
    on_change.assert_not_called()


def test_seq_dedup_prevents_double_processing_via_processor(tmp_path):
    """End-to-end: simulate two events firing with no actual seq change.
    The processor's tick() should no-op on the second call."""
    from tagteam.watcher import _StateProcessor
    from unittest.mock import patch

    p = _StateProcessor(
        mode="notify",
        lead_name="A", reviewer_name="B",
        lead_pane="x", reviewer_pane="y",
        lead_session_id=None, reviewer_session_id=None,
        confirm=False, timeout_minutes=30, project_dir=str(tmp_path),
        max_retries=3, retry_delay=2.0, pre_send_delay=1.0,
    )
    state = {"seq": 1, "status": "ready", "turn": "lead",
             "command": "/handoff", "phase": "p", "round": 1,
             "updated_at": "2026-05-03T00:00:01+00:00"}
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(state)
        p.tick(state)  # same seq — should no-op
    notify.assert_called_once()


def test_event_loop_failure_falls_back_to_poll(tmp_path, monkeypatch):
    """Regression: if the watchdog observer raises at runtime (e.g. macOS
    FSEvents 'Cannot start fsevents stream' SystemError, or inotify
    ENOSPC), watch() must catch it and call _run_poll_loop instead of
    crashing the watcher process."""
    from tagteam.watcher import (
        _run_event_loop, _StateProcessor, watch,
    )
    from unittest.mock import patch, MagicMock

    # Force watch() through the event-driven branch and make
    # watch_with_events blow up at startup the way FSEvents does.
    monkeypatch.setattr("tagteam.watcher_events.is_available",
                        lambda: True)

    def _boom(*a, **kw):
        raise SystemError("Cannot start fsevents stream. "
                          "Use a kqueue or polling observer instead.")

    monkeypatch.setattr(
        "tagteam.watcher_events.watch_with_events", _boom,
    )

    poll_called = MagicMock(return_value=None)

    with patch("tagteam.watcher._run_poll_loop", poll_called):
        # mode=notify so no iterm/tmux setup is needed
        watch(interval=1, mode="notify", project_dir=str(tmp_path))

    poll_called.assert_called_once()
    # First positional arg should be the processor
    args, _ = poll_called.call_args
    assert isinstance(args[0], _StateProcessor)


def test_run_event_loop_returns_false_on_observer_startup_error(
        tmp_path, monkeypatch):
    """Direct test of the helper: any non-KeyboardInterrupt exception
    from watch_with_events should be caught and surface as False
    (signal to watch() to fall back to poll)."""
    from tagteam.watcher import _StateProcessor, _run_event_loop

    def _boom(*a, **kw):
        raise SystemError("fsevents fail")

    monkeypatch.setattr(
        "tagteam.watcher_events.watch_with_events", _boom,
    )

    p = _StateProcessor(
        mode="notify",
        lead_name="A", reviewer_name="B",
        lead_pane="x", reviewer_pane="y",
        lead_session_id=None, reviewer_session_id=None,
        confirm=False, timeout_minutes=30, project_dir=str(tmp_path),
        max_retries=3, retry_delay=2.0, pre_send_delay=1.0,
    )
    assert _run_event_loop(p, str(tmp_path)) is False


def test_run_event_loop_returns_true_on_keyboard_interrupt(
        tmp_path, monkeypatch):
    """KeyboardInterrupt is the clean-exit path; should return True so
    watch() does NOT then start the poll loop."""
    from tagteam.watcher import _StateProcessor, _run_event_loop

    def _interrupt(*a, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "tagteam.watcher_events.watch_with_events", _interrupt,
    )

    p = _StateProcessor(
        mode="notify",
        lead_name="A", reviewer_name="B",
        lead_pane="x", reviewer_pane="y",
        lead_session_id=None, reviewer_session_id=None,
        confirm=False, timeout_minutes=30, project_dir=str(tmp_path),
        max_retries=3, retry_delay=2.0, pre_send_delay=1.0,
    )
    assert _run_event_loop(p, str(tmp_path)) is True


def test_watch_with_events_fires_on_change_at_startup(tmp_path):
    """watch_with_events calls on_change() once at startup (mirrors
    poll mode's first-iteration behavior). Verified by stopping the
    observer immediately via KeyboardInterrupt during the heartbeat
    sleep."""
    from unittest.mock import patch

    target = tmp_path / "handoff-state.json"
    target.write_text("{}")
    on_change = MagicMock()

    def _interrupt(_):
        raise KeyboardInterrupt

    with patch("tagteam.watcher_events.time.sleep", side_effect=_interrupt):
        watcher_events.watch_with_events(
            target, on_change, heartbeat_s=60.0
        )

    # At least one call from startup
    assert on_change.call_count >= 1
