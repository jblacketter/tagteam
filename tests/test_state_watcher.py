"""Tests for the TUI state watcher module."""

import json
from pathlib import Path

import pytest

pytest.importorskip("textual", reason="textual not installed; run `pip install tagteam[tui]`")

from tagteam.tui.state_watcher import (
    HandoffState,
    find_state_path,
    read_handoff_state,
    state_has_changed,
)


class TestHandoffState:
    def test_from_dict_basic(self):
        data = {
            "turn": "reviewer",
            "status": "ready",
            "phase": "test-phase",
            "type": "plan",
            "round": 2,
            "updated_at": "2026-01-01T00:00:00Z",
            "updated_by": "Claude",
        }
        state = HandoffState.from_dict(data)
        assert state.turn == "reviewer"
        assert state.status == "ready"
        assert state.phase == "test-phase"
        assert state.step_type == "plan"
        assert state.round == 2
        assert state.updated_by == "Claude"

    def test_from_dict_defaults(self):
        state = HandoffState.from_dict({})
        assert state.turn == ""
        assert state.status == ""
        assert state.phase == ""
        assert state.round == 0
        assert state.is_empty

    def test_from_dict_null_fields(self):
        data = {"turn": "lead", "status": "done", "result": None, "reason": None}
        state = HandoffState.from_dict(data)
        assert state.result == ""
        assert state.reason == ""

    def test_is_empty(self):
        empty = HandoffState()
        assert empty.is_empty is True

        non_empty = HandoffState(turn="lead", status="ready")
        assert non_empty.is_empty is False

    def test_fingerprint_changes_with_fields(self):
        state1 = HandoffState(turn="lead", status="ready", phase="p1", round=1)
        state2 = HandoffState(turn="reviewer", status="ready", phase="p1", round=1)
        assert state1.fingerprint != state2.fingerprint

    def test_fingerprint_same_for_same_data(self):
        state1 = HandoffState(turn="lead", status="ready", phase="p1", round=1)
        state2 = HandoffState(turn="lead", status="ready", phase="p1", round=1)
        assert state1.fingerprint == state2.fingerprint

    def test_history_parsed(self):
        data = {
            "turn": "lead",
            "status": "ready",
            "history": [
                {"turn": "reviewer", "status": "ready", "timestamp": "2026-01-01"},
                {"turn": "lead", "status": "ready", "timestamp": "2026-01-02"},
            ],
        }
        state = HandoffState.from_dict(data)
        assert len(state.history) == 2
        assert state.history[0] == ("reviewer", "ready", "2026-01-01")


class TestReadHandoffState:
    def test_reads_valid_state(self, tmp_path):
        state_file = tmp_path / "handoff-state.json"
        state_file.write_text(json.dumps({
            "turn": "lead",
            "status": "ready",
            "phase": "test",
            "type": "plan",
            "round": 1,
        }))

        state = read_handoff_state(state_file)
        assert state is not None
        assert state.turn == "lead"
        assert state.phase == "test"

    def test_returns_none_for_missing(self, tmp_path):
        missing = tmp_path / "nonexistent.json"
        result = read_handoff_state(missing)
        assert result is None

    def test_returns_none_for_invalid_json(self, tmp_path):
        bad_file = tmp_path / "handoff-state.json"
        bad_file.write_text("not json {{{")

        result = read_handoff_state(bad_file)
        assert result is None

    def test_returns_none_for_empty_file(self, tmp_path):
        empty = tmp_path / "handoff-state.json"
        empty.write_text("")

        result = read_handoff_state(empty)
        assert result is None


class TestStateHasChanged:
    def test_both_none(self):
        assert state_has_changed(None, None) is False

    def test_current_none_previous_exists(self):
        prev = HandoffState(turn="lead", status="ready")
        assert state_has_changed(None, prev) is True

    def test_current_exists_previous_none(self):
        curr = HandoffState(turn="lead", status="ready")
        assert state_has_changed(curr, None) is True

    def test_same_timestamp_no_change(self):
        s1 = HandoffState(turn="lead", status="ready", updated_at="2026-01-01T00:00:00Z")
        s2 = HandoffState(turn="lead", status="ready", updated_at="2026-01-01T00:00:00Z")
        assert state_has_changed(s1, s2) is False

    def test_different_timestamp_changed(self):
        s1 = HandoffState(turn="lead", status="ready", updated_at="2026-01-01T00:00:00Z")
        s2 = HandoffState(turn="lead", status="ready", updated_at="2026-01-02T00:00:00Z")
        assert state_has_changed(s1, s2) is True

    def test_fingerprint_fallback_no_change(self):
        s1 = HandoffState(turn="lead", status="ready", phase="p1", round=1)
        s2 = HandoffState(turn="lead", status="ready", phase="p1", round=1)
        assert state_has_changed(s1, s2) is False

    def test_fingerprint_fallback_changed(self):
        s1 = HandoffState(turn="lead", status="ready", phase="p1", round=1)
        s2 = HandoffState(turn="reviewer", status="ready", phase="p1", round=1)
        assert state_has_changed(s1, s2) is True


class TestFindStatePath:
    def test_with_project_dir(self, tmp_path):
        path = find_state_path(str(tmp_path))
        assert path == tmp_path / "handoff-state.json"

    def test_without_project_dir(self):
        path = find_state_path(None)
        assert path.name == "handoff-state.json"
