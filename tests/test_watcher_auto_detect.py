"""Tests for Phase 29 watcher mode auto-detection."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tagteam.watcher import _auto_detect_mode


@pytest.fixture
def project(tmp_path):
    return tmp_path


class TestAutoDetectMode:
    def test_returns_notify_when_nothing_configured(self, project, monkeypatch):
        # No session file, no tmux session.
        monkeypatch.setattr(
            "tagteam.session.session_exists", lambda: False
        )
        mode, reason = _auto_detect_mode(str(project))
        assert mode == "notify"
        assert "session start" in reason.lower()

    def test_returns_iterm2_when_session_file_has_both_roles(
        self, project, monkeypatch
    ):
        # Simulate `tagteam session start --backend iterm2 --launch` having run.
        session_file = project / ".handoff-session.json"
        session_file.write_text(json.dumps({
            "tabs": {
                "lead": {"session_id": "abc-lead-123"},
                "reviewer": {"session_id": "def-rev-456"},
            }
        }))
        monkeypatch.setattr(
            "tagteam.session.session_exists", lambda: False
        )
        mode, reason = _auto_detect_mode(str(project))
        assert mode == "iterm2"
        assert "iterm2" in reason.lower()

    def test_returns_notify_when_session_file_has_only_one_role(
        self, project, monkeypatch
    ):
        # Partial session file (e.g., one role failed to launch) shouldn't
        # claim iterm2 — the watcher would error trying to find the
        # missing role's session.
        session_file = project / ".handoff-session.json"
        session_file.write_text(json.dumps({
            "tabs": {"lead": {"session_id": "abc-lead-123"}}
        }))
        monkeypatch.setattr(
            "tagteam.session.session_exists", lambda: False
        )
        mode, reason = _auto_detect_mode(str(project))
        assert mode == "notify"

    def test_returns_tmux_when_tmux_session_exists(self, project, monkeypatch):
        # No iterm session file; tmux session present.
        monkeypatch.setattr(
            "tagteam.session.session_exists", lambda: True
        )
        mode, reason = _auto_detect_mode(str(project))
        assert mode == "tmux"
        assert "tmux" in reason.lower()

    def test_iterm2_takes_priority_over_tmux(self, project, monkeypatch):
        # If both are set up (rare but possible), prefer iterm2 — that's
        # the more featureful backend per the project's defaults.
        session_file = project / ".handoff-session.json"
        session_file.write_text(json.dumps({
            "tabs": {
                "lead": {"session_id": "abc"},
                "reviewer": {"session_id": "def"},
            }
        }))
        monkeypatch.setattr(
            "tagteam.session.session_exists", lambda: True
        )
        mode, reason = _auto_detect_mode(str(project))
        assert mode == "iterm2"

    def test_handles_session_module_failure_gracefully(
        self, project, monkeypatch
    ):
        # If session_exists() raises (rare but possible — tmux subprocess
        # weirdness), auto-detect should fall back to notify, not crash.
        def boom():
            raise RuntimeError("tmux died")
        monkeypatch.setattr("tagteam.session.session_exists", boom)
        mode, reason = _auto_detect_mode(str(project))
        assert mode == "notify"
