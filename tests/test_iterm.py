"""Tests for iTerm2 integration and shared idle pattern detection."""

import json
from pathlib import Path
from unittest.mock import patch, call

import pytest

from tagteam.iterm import (
    _ensure_iterm_ready,
    _read_session_file,
    _write_session_file,
    get_session_id,
    write_text_to_session,
    get_session_contents,
    session_id_is_valid,
    iterm_is_running,
    create_session,
)
from tagteam.watcher import _check_idle_patterns


class TestSessionFile:
    def test_write_and_read(self, tmp_path):
        data = {
            "backend": "iterm2",
            "tabs": {
                "lead": {"session_id": "abc-123"},
                "watcher": {"session_id": "def-456"},
                "reviewer": {"session_id": "ghi-789"},
            },
        }
        _write_session_file(str(tmp_path), data)

        result = _read_session_file(str(tmp_path))
        assert result is not None
        assert result["backend"] == "iterm2"
        assert result["tabs"]["lead"]["session_id"] == "abc-123"

    def test_read_missing_file(self, tmp_path):
        result = _read_session_file(str(tmp_path))
        assert result is None

    def test_read_invalid_json(self, tmp_path):
        (tmp_path / ".handoff-session.json").write_text("not json {{{")
        result = _read_session_file(str(tmp_path))
        assert result is None

    def test_get_session_id(self, tmp_path):
        data = {
            "tabs": {
                "lead": {"session_id": "lead-id"},
                "reviewer": {"session_id": "reviewer-id"},
            },
        }
        _write_session_file(str(tmp_path), data)

        assert get_session_id("lead", str(tmp_path)) == "lead-id"
        assert get_session_id("reviewer", str(tmp_path)) == "reviewer-id"
        assert get_session_id("watcher", str(tmp_path)) is None

    def test_get_session_id_no_file(self, tmp_path):
        assert get_session_id("lead", str(tmp_path)) is None


class TestOsascriptCalls:
    @patch("tagteam.iterm._osascript")
    def test_write_text_success(self, mock_osascript):
        mock_osascript.return_value = "ok"
        result = write_text_to_session("session-123", "/handoff")
        assert result is True
        mock_osascript.assert_called_once()
        call_script = mock_osascript.call_args[0][0]
        assert "session-123" in call_script
        assert "/handoff" in call_script
        assert 'write text "/handoff" newline NO' in call_script
        assert "ASCII character 13" in call_script

    @patch("tagteam.iterm._osascript")
    def test_write_text_not_found(self, mock_osascript):
        mock_osascript.return_value = "not_found"
        result = write_text_to_session("bad-id", "/handoff")
        assert result is False

    @patch("tagteam.iterm._osascript")
    def test_write_text_exception(self, mock_osascript):
        mock_osascript.side_effect = RuntimeError("fail")
        result = write_text_to_session("session-123", "/handoff")
        assert result is False

    @patch("tagteam.iterm._osascript")
    def test_write_text_escapes_quotes(self, mock_osascript):
        mock_osascript.return_value = "ok"
        write_text_to_session("s1", 'say "hello"')
        call_script = mock_osascript.call_args[0][0]
        assert '\\"hello\\"' in call_script

    @patch("tagteam.iterm._osascript")
    def test_get_session_contents(self, mock_osascript):
        mock_osascript.return_value = "line1\nline2\nline3\nline4\nline5\nline6"
        result = get_session_contents("session-123", last_n_lines=3)
        assert result == "line4\nline5\nline6"

    @patch("tagteam.iterm._osascript")
    def test_get_session_contents_empty(self, mock_osascript):
        mock_osascript.return_value = ""
        result = get_session_contents("session-123")
        assert result == ""

    @patch("tagteam.iterm._osascript")
    def test_session_id_is_valid_found(self, mock_osascript):
        mock_osascript.return_value = "found"
        assert session_id_is_valid("session-123") is True

    @patch("tagteam.iterm._osascript")
    def test_session_id_is_valid_not_found(self, mock_osascript):
        mock_osascript.return_value = "not_found"
        assert session_id_is_valid("bad-id") is False

    @patch("tagteam.iterm._osascript")
    def test_iterm_is_running(self, mock_osascript):
        mock_osascript.return_value = "true"
        assert iterm_is_running() is True

        mock_osascript.return_value = "false"
        assert iterm_is_running() is False


class TestCreateSessionLaunch:
    """Tests for create_session with --launch flag."""

    @patch("tagteam.iterm.iterm_is_running", return_value=True)
    @patch("tagteam.iterm._ensure_iterm_ready")
    @patch("tagteam.iterm.write_text_to_session")
    @patch("tagteam.iterm._osascript")
    def test_launch_sends_raw_commands_after_session_file(
        self, mock_osascript, mock_write_text, mock_ensure, mock_running, tmp_path
    ):
        """Launch commands must be sent after session file exists,
        and must NOT be double-escaped (regression test)."""
        mock_osascript.return_value = "lead-id,watcher-id,reviewer-id"
        mock_write_text.return_value = True

        # Write config with quoted command args
        config_file = tmp_path / "tagteam.yaml"
        config_file.write_text(
            "agents:\n"
            "  lead:\n"
            "    name: Claude\n"
            '    command: claude --model "opus"\n'
            "  reviewer:\n"
            "    name: Codex\n"
            '    command: codex --approval-mode "full-auto"\n'
        )

        result = create_session(str(tmp_path), launch=True)
        assert result is True

        # Session file must exist before write_text calls
        session_file = tmp_path / ".handoff-session.json"
        assert session_file.exists()

        # Verify raw commands were passed (no pre-escaping)
        # 5 calls: lead launch, reviewer launch, watcher launch, lead prime, reviewer prime
        calls = mock_write_text.call_args_list
        assert len(calls) == 5

        # Lead gets raw command with quotes intact
        assert calls[0] == call("lead-id", 'claude --model "opus"')
        # Reviewer gets raw command with quotes intact
        assert calls[1] == call("reviewer-id", 'codex --approval-mode "full-auto"')
        # Watcher
        assert calls[2] == call(
            "watcher-id", "python -m tagteam watch --mode iterm2"
        )
        # Auto-prime messages sent to lead and reviewer
        from tagteam.session import PRIME_MESSAGE
        assert calls[3] == call("lead-id", PRIME_MESSAGE)
        assert calls[4] == call("reviewer-id", PRIME_MESSAGE)

    @patch("tagteam.iterm.iterm_is_running", return_value=True)
    @patch("tagteam.iterm._ensure_iterm_ready")
    @patch("tagteam.iterm.write_text_to_session")
    @patch("tagteam.iterm._osascript")
    def test_launch_false_does_not_send_commands(
        self, mock_osascript, mock_write_text, mock_ensure, mock_running, tmp_path
    ):
        mock_osascript.return_value = "lead-id,watcher-id,reviewer-id"

        result = create_session(str(tmp_path), launch=False)
        assert result is True
        mock_write_text.assert_not_called()

    @patch("tagteam.iterm.iterm_is_running", return_value=True)
    @patch("tagteam.iterm._ensure_iterm_ready")
    @patch("tagteam.iterm._osascript")
    def test_launch_without_config_falls_back(
        self, mock_osascript, mock_ensure, mock_running, tmp_path
    ):
        """If tagteam.yaml is missing, launch degrades to no-launch."""
        mock_osascript.return_value = "lead-id,watcher-id,reviewer-id"

        result = create_session(str(tmp_path), launch=True)
        assert result is True
        # Session created but no launch commands sent (only the creation script)
        assert mock_osascript.call_count == 1


class TestEnsureItermReady:
    """Scripting-readiness probe: poll iTerm2 until its AppleScript
    dictionary is loaded, not just until the process appears."""

    @patch("tagteam.iterm.time.sleep")
    @patch("tagteam.iterm._osascript")
    @patch("tagteam.iterm.iterm_is_running")
    def test_ensure_iterm_ready_returns_after_probe_succeeds(
        self, mock_running, mock_osa, mock_sleep
    ):
        # Warm path: iTerm2 already running, first probe succeeds.
        mock_running.return_value = True
        mock_osa.return_value = "1"
        _ensure_iterm_ready()
        # Exactly one probe call, no launch.
        assert mock_osa.call_count == 1
        assert "count windows" in mock_osa.call_args_list[0][0][0]
        assert "launch" not in mock_osa.call_args_list[0][0][0]

    @patch("tagteam.iterm.time.sleep")
    @patch("tagteam.iterm._launch_iterm_via_launchservices")
    @patch("tagteam.iterm._osascript")
    @patch("tagteam.iterm.iterm_is_running")
    def test_ensure_iterm_ready_launches_then_polls(
        self, mock_running, mock_osa, mock_launch, mock_sleep
    ):
        # Cold path: LaunchServices launch is invoked; probe succeeds on 3rd try.
        mock_running.return_value = False
        mock_osa.side_effect = [
            RuntimeError("not ready"),
            RuntimeError("not ready"),
            "1",  # probe succeeds
        ]
        _ensure_iterm_ready()
        mock_launch.assert_called_once()
        # Every osascript call is a probe — launch no longer goes through osascript.
        for c in mock_osa.call_args_list:
            assert "count windows" in c[0][0]
        assert mock_osa.call_count == 3

    @patch("tagteam.iterm.time.monotonic")
    @patch("tagteam.iterm.time.sleep")
    @patch("tagteam.iterm._osascript")
    @patch("tagteam.iterm.iterm_is_running")
    def test_ensure_iterm_ready_times_out_on_probe_failure(
        self, mock_running, mock_osa, mock_sleep, mock_monotonic
    ):
        # Probe always raises → after the timeout window, helper raises
        # RuntimeError whose message includes the final probe error.
        mock_running.return_value = True
        mock_osa.side_effect = RuntimeError("bad-probe")
        # Force the while-loop to exit after a couple iterations.
        mock_monotonic.side_effect = [0.0, 0.1, 0.2, 999.0]
        with pytest.raises(RuntimeError, match="did not become ready") as exc_info:
            _ensure_iterm_ready()
        assert "bad-probe" in str(exc_info.value)

    @patch("tagteam.iterm.time.sleep")
    @patch("tagteam.iterm._launch_iterm_via_launchservices")
    @patch("tagteam.iterm._osascript")
    @patch("tagteam.iterm.iterm_is_running")
    def test_ensure_iterm_ready_cold_path_retries_until_probe_compiles(
        self, mock_running, mock_osa, mock_launch, mock_sleep
    ):
        # Cold path: LaunchServices launch + 5 failed probes + 1 success = 6 osascript calls.
        mock_running.return_value = False
        mock_osa.side_effect = [
            RuntimeError("boom 1"),
            RuntimeError("boom 2"),
            RuntimeError("boom 3"),
            RuntimeError("boom 4"),
            RuntimeError("boom 5"),
            "1",  # probe success
        ]
        _ensure_iterm_ready()
        mock_launch.assert_called_once()
        assert mock_osa.call_count == 6
        for c in mock_osa.call_args_list:
            script = c[0][0]
            assert "count windows" in script
            assert "launch" not in script

    @patch("tagteam.iterm.time.sleep")
    @patch("tagteam.iterm._osascript")
    @patch("tagteam.iterm.iterm_is_running")
    def test_ensure_iterm_ready_warm_path_retries_until_probe_compiles(
        self, mock_running, mock_osa, mock_sleep
    ):
        # Warm path: no launch, 5 failed probes + 1 successful probe = 6 calls.
        mock_running.return_value = True
        mock_osa.side_effect = [
            RuntimeError("boom 1"),
            RuntimeError("boom 2"),
            RuntimeError("boom 3"),
            RuntimeError("boom 4"),
            RuntimeError("boom 5"),
            "1",
        ]
        _ensure_iterm_ready()
        assert mock_osa.call_count == 6
        for c in mock_osa.call_args_list:
            script = c[0][0]
            assert "count windows" in script
            assert "launch" not in script

    @patch("tagteam.iterm._ensure_iterm_ready")
    @patch("tagteam.iterm._osascript")
    def test_create_session_catches_launch_failure(
        self, mock_osa, mock_ensure, tmp_path, capsys
    ):
        mock_ensure.side_effect = RuntimeError("iTerm2 did not start")
        result = create_session(str(tmp_path), launch=False)
        assert result is False
        out = capsys.readouterr().out
        assert "iTerm2 failed to launch" in out
        assert "backend tmux" in out or "backend manual" in out


class TestCheckIdlePatterns:
    def test_empty_content(self):
        assert _check_idle_patterns("") is False
        assert _check_idle_patterns("   \n  ") is False

    def test_idle_shortcuts(self):
        content = "some output\n> ? for shortcuts\n"
        assert _check_idle_patterns(content) is True

    def test_idle_context_left(self):
        content = "blah\nblah\n95% context left\n"
        assert _check_idle_patterns(content) is True

    def test_busy_esc_to_interrupt(self):
        content = "Working... esc to interrupt\n"
        assert _check_idle_patterns(content) is False

    def test_busy_thinking(self):
        content = "line1\nline2\nThinking...\nline4\n"
        assert _check_idle_patterns(content) is False

    def test_busy_running(self):
        content = "line1\nRunning tests\nline3\n"
        assert _check_idle_patterns(content) is False

    def test_busy_confirmation_prompt(self):
        content = "Do you want to proceed? (y/n)\n"
        assert _check_idle_patterns(content) is False

    def test_busy_overrides_idle(self):
        # If both busy and idle patterns appear, busy wins (checked first)
        content = "? for shortcuts\nesc to interrupt\n"
        assert _check_idle_patterns(content) is False

    def test_no_patterns_returns_false(self):
        content = "just some random output\nnothing special here\n"
        assert _check_idle_patterns(content) is False
