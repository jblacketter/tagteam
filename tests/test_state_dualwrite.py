"""Tests for the Phase 28 Step A integration of dual-write into
`tagteam.state`.

Covers `update_state`, `write_state`, `clear_state`,
`_log_seq_mismatch`, and `clear_diagnostics_log`. The tricky case
is the `update_state` -> `write_state` layering: `update_state`
must own the DB state-history append; the inner `write_state`'s
own dual-write hook must short-circuit so history isn't written
twice.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from tagteam import db, dualwrite, state


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Project dir whose `tagteam.yaml` causes _resolve_project_root
    to return tmp_path. Necessary because state.py functions resolve
    the root themselves rather than taking it as an argument in
    every call."""
    (tmp_path / "tagteam.yaml").write_text(
        "agents:\n  lead: {name: L}\n  reviewer: {name: R}\n"
    )
    # Reset the cached resolution and chdir so the walk-up finds tmp_path.
    monkeypatch.setattr(state, "_cached_project_root", None, raising=False)
    monkeypatch.chdir(tmp_path)
    yield tmp_path


# ---------- write_state ----------

class TestWriteStateDualWrite:
    def test_writes_to_db(self, project):
        state.write_state(
            {"phase": "p", "type": "plan", "round": 1, "status": "ready",
             "command": "go", "seq": 1},
            project_dir=str(project),
        )
        conn = db.connect(project_dir=str(project))
        try:
            row = db.get_state(conn)
        finally:
            conn.close()
        assert row is not None
        assert row["phase"] == "p"
        assert row["status"] == "ready"
        assert row["seq"] == 1

    def test_no_history_entry_added(self, project):
        """Direct write_state callers bypass history; DB side does
        the same."""
        state.write_state(
            {"phase": "p", "type": "plan", "round": 1, "status": "ready"},
            project_dir=str(project),
        )
        conn = db.connect(project_dir=str(project))
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM state_history"
            ).fetchone()[0]
        finally:
            conn.close()
        assert n == 0

    def test_db_failure_marks_invalid(self, project):
        with patch("tagteam.db.set_state", side_effect=RuntimeError("boom")):
            state.write_state(
                {"phase": "p", "round": 1, "status": "ready"},
                project_dir=str(project),
            )
        # File still written
        assert (project / "handoff-state.json").exists()
        # DB invalid flag set
        assert dualwrite.is_db_invalid(project)


# ---------- update_state ----------

class TestUpdateStateDualWrite:
    def test_first_update_writes_state(self, project):
        result = state.update_state(
            {"phase": "p", "type": "plan", "round": 1, "status": "ready",
             "updated_by": "Claude"},
            project_dir=str(project),
        )
        assert result is not None
        conn = db.connect(project_dir=str(project))
        try:
            row = db.get_state(conn)
        finally:
            conn.close()
        assert row["phase"] == "p"
        assert row["status"] == "ready"
        assert row["seq"] == 1

    def test_history_entry_appears_in_db(self, project):
        """First update has nothing to put in history (no prior state).
        Second update should append a history row to the DB matching
        the file-side append."""
        state.update_state(
            {"phase": "p", "type": "plan", "round": 1, "status": "ready",
             "turn": "lead", "updated_by": "Claude"},
            project_dir=str(project),
        )
        state.update_state(
            {"phase": "p", "type": "plan", "round": 1, "status": "working",
             "turn": "lead", "updated_by": "Claude"},
            project_dir=str(project),
        )
        conn = db.connect(project_dir=str(project))
        try:
            history = db.get_history(conn)
        finally:
            conn.close()
        assert len(history) == 1
        assert history[0]["status"] == "ready"
        assert history[0]["turn"] == "lead"

    def test_no_double_history_via_inner_write_state(self, project):
        """The layering trap: update_state calls write_state; both
        have dual-write hooks. update_state owns the history append;
        write_state must not also append. Each update_state call
        produces EXACTLY ONE DB history row, not two."""
        state.update_state(
            {"phase": "p", "type": "plan", "round": 1, "status": "ready",
             "turn": "lead", "updated_by": "Claude"},
            project_dir=str(project),
        )
        state.update_state(
            {"phase": "p", "type": "plan", "round": 1, "status": "working",
             "turn": "lead", "updated_by": "Claude"},
            project_dir=str(project),
        )
        state.update_state(
            {"phase": "p", "type": "plan", "round": 1, "status": "done",
             "turn": "lead", "updated_by": "Claude"},
            project_dir=str(project),
        )
        # 3 update_state calls. First has no prior state, so no history
        # entry. Second and third each contribute one. Expected: 2 rows.
        conn = db.connect(project_dir=str(project))
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM state_history"
            ).fetchone()[0]
        finally:
            conn.close()
        assert n == 2, (
            "Expected 2 history rows but got "
            f"{n} — likely the inner write_state's hook double-wrote."
        )

    def test_seq_mismatch_logs_diagnostic_and_returns_none(self, project):
        # Seed state with seq=1
        state.update_state(
            {"phase": "p", "type": "plan", "round": 1, "status": "ready",
             "updated_by": "Claude"},
            project_dir=str(project),
        )
        # Try with stale expected_seq
        result = state.update_state(
            {"updated_by": "Claude"},
            project_dir=str(project),
            expected_seq=99,
        )
        assert result is None
        # Diagnostic logged in both file and DB
        diag_path = project / state.DIAGNOSTICS_LOG
        assert diag_path.exists()
        assert "seq_mismatch" in diag_path.read_text()
        conn = db.connect(project_dir=str(project))
        try:
            kinds = [r[0] for r in conn.execute(
                "SELECT kind FROM diagnostics"
            ).fetchall()]
        finally:
            conn.close()
        assert "seq_mismatch" in kinds


# ---------- clear_state ----------

class TestClearStateDualWrite:
    def test_clear_removes_db_state_row(self, project):
        state.update_state(
            {"phase": "p", "round": 1, "status": "ready"},
            project_dir=str(project),
        )
        conn = db.connect(project_dir=str(project))
        try:
            assert db.get_state(conn) is not None
        finally:
            conn.close()

        state.clear_state(project_dir=str(project))

        conn = db.connect(project_dir=str(project))
        try:
            assert db.get_state(conn) is None
        finally:
            conn.close()

    def test_clear_records_history_entry(self, project):
        state.update_state(
            {"phase": "p", "round": 1, "status": "ready"},
            project_dir=str(project),
        )
        state.clear_state(project_dir=str(project))
        conn = db.connect(project_dir=str(project))
        try:
            history = db.get_history(conn)
        finally:
            conn.close()
        # The "cleared" history entry exists in the DB even after
        # the state row is gone.
        cleared = [h for h in history if h["status"] == "cleared"]
        assert len(cleared) == 1


# ---------- diagnostics ----------

class TestDiagnosticsDualWrite:
    def test_seq_mismatch_dual_write(self, project):
        state._log_seq_mismatch(
            expected=5, actual=4, caller="test",
            project_dir=str(project),
        )
        # File side
        diag = project / state.DIAGNOSTICS_LOG
        assert diag.exists()
        text = diag.read_text()
        assert "seq_mismatch" in text
        # DB side
        conn = db.connect(project_dir=str(project))
        try:
            rows = conn.execute(
                "SELECT kind, payload_json FROM diagnostics"
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "seq_mismatch"
        payload = json.loads(rows[0][1])
        assert payload == {"expected": 5, "actual": 4, "caller": "test"}

    def test_clear_diagnostics_clears_both(self, project):
        state._log_seq_mismatch(
            expected=5, actual=4, caller="test",
            project_dir=str(project),
        )
        # Both populated
        assert (project / state.DIAGNOSTICS_LOG).exists()
        conn = db.connect(project_dir=str(project))
        try:
            assert conn.execute(
                "SELECT COUNT(*) FROM diagnostics"
            ).fetchone()[0] >= 1
        finally:
            conn.close()

        state.clear_diagnostics_log(project_dir=str(project))

        # File truncated
        assert (project / state.DIAGNOSTICS_LOG).read_text() == ""
        # DB cleared
        conn = db.connect(project_dir=str(project))
        try:
            assert conn.execute(
                "SELECT COUNT(*) FROM diagnostics"
            ).fetchone()[0] == 0
        finally:
            conn.close()


# ---------- Cycle integration: no deadlock through the lock chain ----------

class TestCycleStateNoDeadlock:
    """`cycle.add_round` holds the writer lock while calling
    `_derive_top_level_state` -> `update_state` -> `write_state`,
    each of which now also acquires the lock. The reentrant lock
    must allow this without deadlock; this test makes that
    explicit rather than relying on the existing test_cycle_dualwrite
    tests passing."""

    def test_full_cycle_to_state_chain_completes(self, project):
        from tagteam import cycle
        cycle.init_cycle("p", "plan", "L", "R", "first", str(project))
        cycle.add_round(
            "p", "plan", "reviewer", "REQUEST_CHANGES",
            1, "fix it", str(project),
        )
        cycle.add_round(
            "p", "plan", "lead", "SUBMIT_FOR_REVIEW",
            2, "v2", str(project),
        )
        cycle.add_round(
            "p", "plan", "reviewer", "APPROVE",
            2, "ok", str(project),
        )
        # If we got here without deadlock and tests pass, the
        # reentrant lock is doing its job. Assert end state matches.
        conn = db.connect(project_dir=str(project))
        try:
            cycle_row = db.get_cycle(conn, "p", "plan")
            n_rounds = conn.execute(
                "SELECT COUNT(*) FROM rounds"
            ).fetchone()[0]
            n_history = conn.execute(
                "SELECT COUNT(*) FROM state_history"
            ).fetchone()[0]
        finally:
            conn.close()
        assert cycle_row["state"] == "approved"
        assert n_rounds == 4
        # State history grew over the chain — exact count depends on
        # _derive_top_level_state's update pattern, but it must be
        # nonzero.
        assert n_history > 0
