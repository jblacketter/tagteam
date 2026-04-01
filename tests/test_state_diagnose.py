"""Tests for state diagnostics, enriched history, and seq mismatch logging."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_handoff.state import (
    read_state, write_state, update_state, clear_state,
    diagnose_state, _log_seq_mismatch, _read_diagnostics_log,
    clear_diagnostics_log, DIAGNOSTICS_LOG,
)


# --- Enriched history ---

class TestEnrichedHistory:
    def test_history_includes_phase_round_updated_by(self, tmp_path):
        # Write initial state
        write_state({
            "turn": "lead", "status": "ready", "phase": "my-phase",
            "round": 1, "updated_by": "Claude", "seq": 0,
        }, str(tmp_path))

        # Update state
        update_state({
            "turn": "reviewer", "status": "ready",
            "updated_by": "Codex", "phase": "my-phase", "round": 2,
        }, str(tmp_path))

        state = read_state(str(tmp_path))
        assert len(state["history"]) == 1
        entry = state["history"][0]
        assert entry["turn"] == "lead"
        assert entry["status"] == "ready"
        assert entry["phase"] == "my-phase"
        assert entry["round"] == 1
        assert entry["updated_by"] == "Claude"

    def test_history_backward_compat(self, tmp_path):
        """Old history entries without new fields are handled gracefully."""
        write_state({
            "turn": "lead", "status": "ready", "seq": 0,
            "history": [
                {"turn": "reviewer", "status": "done", "timestamp": "2026-01-01T00:00:00+00:00"},
            ],
        }, str(tmp_path))

        # diagnose should not crash on old entries
        report = diagnose_state(str(tmp_path))
        assert "State file readable" in report


# --- Seq mismatch side-channel ---

class TestSeqMismatchSideChannel:
    def test_seq_mismatch_writes_diagnostics_log(self, tmp_path):
        write_state({"turn": "lead", "status": "ready", "seq": 5}, str(tmp_path))

        # Try update with wrong expected_seq
        result = update_state(
            {"turn": "reviewer", "updated_by": "watcher"},
            str(tmp_path), expected_seq=3,
        )
        assert result is None  # rejected

        log_path = tmp_path / DIAGNOSTICS_LOG
        assert log_path.exists()
        entries = _read_diagnostics_log(str(tmp_path))
        assert len(entries) == 1
        assert entries[0]["event"] == "seq_mismatch"
        assert entries[0]["expected"] == 3
        assert entries[0]["actual"] == 5

    def test_seq_mismatch_does_not_modify_state(self, tmp_path):
        write_state({
            "turn": "lead", "status": "ready", "seq": 5,
            "updated_at": "2026-01-01T00:00:00+00:00",
        }, str(tmp_path))

        original = read_state(str(tmp_path))
        original_seq = original["seq"]
        original_updated_at = original["updated_at"]

        # Rejected write
        update_state(
            {"turn": "reviewer", "updated_by": "watcher"},
            str(tmp_path), expected_seq=3,
        )

        after = read_state(str(tmp_path))
        assert after["seq"] == original_seq
        assert after["updated_at"] == original_updated_at
        assert after["turn"] == "lead"  # unchanged
        assert after["status"] == "ready"  # unchanged

    def test_seq_mismatch_log_content(self, tmp_path):
        _log_seq_mismatch(10, 15, "watcher", str(tmp_path))

        entries = _read_diagnostics_log(str(tmp_path))
        assert len(entries) == 1
        e = entries[0]
        assert e["event"] == "seq_mismatch"
        assert e["expected"] == 10
        assert e["actual"] == 15
        assert e["caller"] == "watcher"
        assert "timestamp" in e

    def test_diagnose_reads_mismatch_log(self, tmp_path):
        write_state({"turn": "lead", "status": "ready", "seq": 5}, str(tmp_path))
        _log_seq_mismatch(3, 5, "watcher", str(tmp_path))

        report = diagnose_state(str(tmp_path))
        assert "seq mismatch" in report.lower()
        assert "expected=3" in report
        assert "actual=5" in report

    def test_diagnose_clean_truncates_log(self, tmp_path):
        _log_seq_mismatch(1, 2, "test", str(tmp_path))
        assert len(_read_diagnostics_log(str(tmp_path))) == 1

        clear_diagnostics_log(str(tmp_path))
        assert len(_read_diagnostics_log(str(tmp_path))) == 0


# --- Diagnostic checks ---

class TestDiagnoseState:
    def test_diagnose_no_state(self, tmp_path):
        report = diagnose_state(str(tmp_path))
        assert "No handoff-state.json found" in report

    def test_diagnose_stuck_in_ready(self, tmp_path):
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        write_state({
            "turn": "reviewer", "status": "ready", "seq": 1,
            "updated_at": old_time, "phase": "test", "type": "plan",
        }, str(tmp_path))
        # Force the updated_at to the old value (write_state overwrites it)
        state = read_state(str(tmp_path))
        state["updated_at"] = old_time
        (tmp_path / "handoff-state.json").write_text(
            json.dumps(state, indent=2), encoding="utf-8")

        report = diagnose_state(str(tmp_path))
        assert "WARN" in report
        assert "ready" in report.lower()
        assert "minutes" in report

    def test_diagnose_not_stuck(self, tmp_path):
        write_state({
            "turn": "reviewer", "status": "ready", "seq": 1,
            "phase": "test", "type": "plan",
        }, str(tmp_path))

        report = diagnose_state(str(tmp_path))
        assert "FAIL" not in report
        # Should show OK for recent state
        assert "recent" in report.lower() or "OK" in report

    def test_diagnose_stale_result(self, tmp_path):
        write_state({
            "turn": "lead", "status": "ready", "seq": 1,
            "result": "approved", "phase": "test", "type": "plan",
        }, str(tmp_path))

        report = diagnose_state(str(tmp_path))
        assert "Stale metadata" in report
        assert "result=\"approved\"" in report

    def test_diagnose_no_stale_result(self, tmp_path):
        write_state({
            "turn": "reviewer", "status": "done", "seq": 1,
            "result": "approved", "phase": "test", "type": "plan",
        }, str(tmp_path))

        report = diagnose_state(str(tmp_path))
        assert "Stale metadata" not in report
        assert "expected" in report.lower()

    def test_diagnose_cycle_sync_mismatch(self, tmp_path):
        write_state({
            "turn": "lead", "status": "ready", "seq": 1,
            "phase": "test", "type": "plan", "round": 3,
        }, str(tmp_path))

        # Create a cycle status file with different round
        handoffs = tmp_path / "docs" / "handoffs"
        handoffs.mkdir(parents=True)
        status_file = handoffs / "test_plan_status.json"
        status_file.write_text(json.dumps({
            "round": 2, "ready_for": "reviewer",
            "phase": "test", "type": "plan",
        }))

        report = diagnose_state(str(tmp_path))
        assert "mismatch" in report.lower()

    def test_diagnose_cycle_not_found(self, tmp_path):
        write_state({
            "turn": "lead", "status": "ready", "seq": 1,
            "phase": "nonexistent", "type": "plan",
        }, str(tmp_path))

        report = diagnose_state(str(tmp_path))
        assert "not found" in report.lower()


class TestHistoryAnomalies:
    def test_rapid_oscillation_detected(self, tmp_path):
        write_state({
            "turn": "lead", "status": "ready", "seq": 10,
            "phase": "test", "type": "plan",
            "history": [
                {"turn": "lead", "status": "ready"},
                {"turn": "reviewer", "status": "ready"},
                {"turn": "lead", "status": "ready"},
                {"turn": "reviewer", "status": "ready"},
                {"turn": "lead", "status": "ready"},
            ],
        }, str(tmp_path))

        report = diagnose_state(str(tmp_path))
        assert "oscillation" in report.lower()

    def test_no_oscillation_normal_history(self, tmp_path):
        write_state({
            "turn": "reviewer", "status": "ready", "seq": 5,
            "phase": "test", "type": "plan",
            "history": [
                {"turn": "lead", "status": "ready"},
                {"turn": "reviewer", "status": "ready"},
                {"turn": "reviewer", "status": "done"},
            ],
        }, str(tmp_path))

        report = diagnose_state(str(tmp_path))
        assert "oscillation" not in report.lower()
        assert "normal" in report.lower()

    def test_repeated_escalations_detected(self, tmp_path):
        write_state({
            "turn": "lead", "status": "ready", "seq": 10,
            "phase": "test", "type": "plan",
            "history": [
                {"turn": "reviewer", "status": "escalated"},
                {"turn": "lead", "status": "ready"},
                {"turn": "reviewer", "status": "escalated"},
            ],
        }, str(tmp_path))

        report = diagnose_state(str(tmp_path))
        assert "escalation" in report.lower()


class TestAgentHealthCheck:
    @patch("ai_handoff.state._check_agent_health")
    def test_check_agents_flag_calls_health_check(self, mock_check, tmp_path):
        write_state({
            "turn": "lead", "status": "ready", "seq": 1,
            "phase": "test", "type": "plan",
        }, str(tmp_path))

        report = diagnose_state(str(tmp_path), check_agents=True)
        mock_check.assert_called_once()
        assert "Agent Health" in report

    def test_no_check_agents_skips_health(self, tmp_path):
        write_state({
            "turn": "lead", "status": "ready", "seq": 1,
            "phase": "test", "type": "plan",
        }, str(tmp_path))

        report = diagnose_state(str(tmp_path), check_agents=False)
        assert "Agent Health" not in report
