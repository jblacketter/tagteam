"""Tests for iTerm2 integration and shared idle pattern detection."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_handoff.iterm import (
    _read_session_file,
    _write_session_file,
    get_session_id,
    write_text_to_session,
    get_session_contents,
    session_id_is_valid,
    iterm_is_running,
)
from ai_handoff.watcher import _check_idle_patterns


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
    @patch("ai_handoff.iterm._osascript")
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

    @patch("ai_handoff.iterm._osascript")
    def test_write_text_not_found(self, mock_osascript):
        mock_osascript.return_value = "not_found"
        result = write_text_to_session("bad-id", "/handoff")
        assert result is False

    @patch("ai_handoff.iterm._osascript")
    def test_write_text_exception(self, mock_osascript):
        mock_osascript.side_effect = RuntimeError("fail")
        result = write_text_to_session("session-123", "/handoff")
        assert result is False

    @patch("ai_handoff.iterm._osascript")
    def test_write_text_escapes_quotes(self, mock_osascript):
        mock_osascript.return_value = "ok"
        write_text_to_session("s1", 'say "hello"')
        call_script = mock_osascript.call_args[0][0]
        assert '\\"hello\\"' in call_script

    @patch("ai_handoff.iterm._osascript")
    def test_get_session_contents(self, mock_osascript):
        mock_osascript.return_value = "line1\nline2\nline3\nline4\nline5\nline6"
        result = get_session_contents("session-123", last_n_lines=3)
        assert result == "line4\nline5\nline6"

    @patch("ai_handoff.iterm._osascript")
    def test_get_session_contents_empty(self, mock_osascript):
        mock_osascript.return_value = ""
        result = get_session_contents("session-123")
        assert result == ""

    @patch("ai_handoff.iterm._osascript")
    def test_session_id_is_valid_found(self, mock_osascript):
        mock_osascript.return_value = "found"
        assert session_id_is_valid("session-123") is True

    @patch("ai_handoff.iterm._osascript")
    def test_session_id_is_valid_not_found(self, mock_osascript):
        mock_osascript.return_value = "not_found"
        assert session_id_is_valid("bad-id") is False

    @patch("ai_handoff.iterm._osascript")
    def test_iterm_is_running(self, mock_osascript):
        mock_osascript.return_value = "true"
        assert iterm_is_running() is True

        mock_osascript.return_value = "false"
        assert iterm_is_running() is False


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
