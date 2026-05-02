"""Tests for the Phase 28 Step A integration of dual-write into
`tagteam.cycle.init_cycle` and `tagteam.cycle.add_round` (including
AMEND).

The contract under test:
  - File writes happen exactly as before (existing test_cycle.py
    tests already cover this).
  - A shadow DB is populated under `.tagteam/tagteam.db` after each
    cycle write.
  - A divergence check runs after each dual-write; on parity it
    leaves no diagnostic; on mismatch it logs one.
  - DB-write failures mark `db_invalid`, log a diagnostic, and do
    NOT raise — the file path is canonical during Step A.
  - The writer lock serializes dual-writes; the lock file is created
    under `.tagteam/`.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tagteam import cycle, db, divergence, dualwrite


@pytest.fixture
def project(tmp_path):
    """Project dir with docs/handoffs/. Existing test_cycle.py uses
    str() return; we use Path here for `.tagteam/` introspection."""
    (tmp_path / "docs" / "handoffs").mkdir(parents=True)
    return tmp_path


# ---------- init_cycle ----------

class TestInitCycleDualWrite:
    def test_creates_shadow_db(self, project):
        cycle.init_cycle("p", "plan", "L", "R", "first draft", str(project))
        assert (project / ".tagteam" / "tagteam.db").exists()

    def test_db_has_cycle_row(self, project):
        cycle.init_cycle("p", "plan", "L", "R", "first draft", str(project))
        conn = db.connect(project_dir=str(project))
        try:
            cycles = db.list_cycles(conn)
            assert len(cycles) == 1
            assert cycles[0]["phase"] == "p"
            assert cycles[0]["type"] == "plan"
            cycle_row = db.get_cycle(conn, "p", "plan")
            assert cycle_row["lead"] == "L"
            assert cycle_row["reviewer"] == "R"
            assert cycle_row["state"] == "in-progress"
            assert cycle_row["ready_for"] == "reviewer"
            assert cycle_row["round"] == 1
        finally:
            conn.close()

    def test_db_has_round_one(self, project):
        cycle.init_cycle("p", "plan", "L", "R", "first draft", str(project))
        conn = db.connect(project_dir=str(project))
        try:
            rounds = db.get_rounds(conn, "p", "plan")
            assert len(rounds) == 1
            assert rounds[0]["round"] == 1
            assert rounds[0]["role"] == "lead"
            assert rounds[0]["action"] == "SUBMIT_FOR_REVIEW"
            assert rounds[0]["content"] == "first draft"
        finally:
            conn.close()

    def test_no_divergence_after_init(self, project):
        cycle.init_cycle("p", "plan", "L", "R", "first draft", str(project))
        conn = db.connect(project_dir=str(project))
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM diagnostics WHERE kind='render_mismatch'"
            ).fetchone()[0]
            assert n == 0
        finally:
            conn.close()

    def test_writer_lock_file_created(self, project):
        cycle.init_cycle("p", "plan", "L", "R", "first draft", str(project))
        assert (project / ".tagteam" / ".write.lock").exists()

    def test_db_write_failure_marks_invalid(self, project):
        """If the shadow DB write raises, the file path still
        completes and `db_invalid` gets set."""
        with patch("tagteam.cycle._shadow_db_after_cycle_write") as p:
            # Simulate the helper raising — but we want the file path
            # to have already completed. So have the patched helper
            # delegate to mark_db_invalid manually.
            def fail_marking_invalid(project_dir, phase, cycle_type):
                dualwrite.mark_db_invalid(
                    project_dir,
                    reason="simulated db write failure",
                )
            p.side_effect = fail_marking_invalid

            status = cycle.init_cycle(
                "p", "plan", "L", "R", "first draft", str(project)
            )

        # File side is fine
        assert status["state"] == "in-progress"
        assert (project / "docs" / "handoffs" / "p_plan_status.json").exists()
        assert (project / "docs" / "handoffs" / "p_plan_rounds.jsonl").exists()
        # DB invalid flag is set
        assert dualwrite.is_db_invalid(project)
        info = dualwrite.get_db_invalid_info(project)
        assert "simulated" in info["reason"]

    def test_real_db_write_failure_does_not_raise(self, project):
        """End-to-end: when the underlying db.upsert_cycle raises,
        init_cycle still returns the status dict and the file path
        is intact."""
        with patch("tagteam.db.upsert_cycle", side_effect=RuntimeError("boom")):
            status = cycle.init_cycle(
                "p", "plan", "L", "R", "first draft", str(project)
            )
        assert status["state"] == "in-progress"
        assert (project / "docs" / "handoffs" / "p_plan_status.json").exists()
        assert dualwrite.is_db_invalid(project)


# ---------- add_round (non-AMEND) ----------

class TestAddRoundDualWrite:
    def test_db_round_added(self, project):
        cycle.init_cycle("p", "plan", "L", "R", "v1", str(project))
        cycle.add_round("p", "plan", "reviewer", "REQUEST_CHANGES",
                        1, "fix it", str(project))
        conn = db.connect(project_dir=str(project))
        try:
            rounds = db.get_rounds(conn, "p", "plan")
            assert len(rounds) == 2
            assert rounds[1]["role"] == "reviewer"
            assert rounds[1]["action"] == "REQUEST_CHANGES"
            assert rounds[1]["content"] == "fix it"
        finally:
            conn.close()

    def test_db_status_updated(self, project):
        cycle.init_cycle("p", "plan", "L", "R", "v1", str(project))
        cycle.add_round("p", "plan", "reviewer", "REQUEST_CHANGES",
                        1, "fix it", str(project))
        conn = db.connect(project_dir=str(project))
        try:
            cycle_row = db.get_cycle(conn, "p", "plan")
            assert cycle_row["state"] == "in-progress"
            assert cycle_row["ready_for"] == "lead"
        finally:
            conn.close()

    def test_db_status_after_approve(self, project):
        cycle.init_cycle("p", "plan", "L", "R", "v1", str(project))
        cycle.add_round("p", "plan", "reviewer", "APPROVE",
                        1, "ok", str(project))
        conn = db.connect(project_dir=str(project))
        try:
            cycle_row = db.get_cycle(conn, "p", "plan")
            assert cycle_row["state"] == "approved"
            assert cycle_row["ready_for"] is None
        finally:
            conn.close()

    def test_no_duplicate_rounds_across_calls(self, project):
        """Each add_round call must add EXACTLY ONE row to the DB,
        not re-insert all rounds. Regression guard against the
        quadratic-growth shape that `import_from_files`-style
        wholesale mirroring would produce."""
        cycle.init_cycle("p", "plan", "L", "R", "v1", str(project))
        cycle.add_round("p", "plan", "reviewer", "REQUEST_CHANGES",
                        1, "fix1", str(project))
        cycle.add_round("p", "plan", "lead", "SUBMIT_FOR_REVIEW",
                        2, "v2", str(project))
        cycle.add_round("p", "plan", "reviewer", "APPROVE",
                        2, "ok", str(project))
        conn = db.connect(project_dir=str(project))
        try:
            n = conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0]
        finally:
            conn.close()
        # 4 file rounds = 4 DB rounds. Not 1+2+3+4 = 10.
        assert n == 4

    def test_no_divergence_after_normal_flow(self, project):
        cycle.init_cycle("p", "plan", "L", "R", "v1", str(project))
        cycle.add_round("p", "plan", "reviewer", "REQUEST_CHANGES",
                        1, "fix1", str(project))
        cycle.add_round("p", "plan", "lead", "SUBMIT_FOR_REVIEW",
                        2, "v2", str(project))
        cycle.add_round("p", "plan", "reviewer", "APPROVE",
                        2, "ok", str(project))
        conn = db.connect(project_dir=str(project))
        try:
            rows = conn.execute(
                "SELECT kind FROM diagnostics"
            ).fetchall()
        finally:
            conn.close()
        kinds = [r[0] for r in rows]
        assert "render_mismatch" not in kinds
        assert "file_inconsistent" not in kinds

    def test_db_write_failure_during_add_round(self, project):
        cycle.init_cycle("p", "plan", "L", "R", "v1", str(project))
        # Mid-flow DB failure on the second add_round
        with patch("tagteam.db.add_round", side_effect=RuntimeError("boom")):
            status = cycle.add_round(
                "p", "plan", "reviewer", "REQUEST_CHANGES",
                1, "fix1", str(project)
            )
        # File path completed
        assert status["state"] == "in-progress"
        assert status["ready_for"] == "lead"
        # DB invalid
        assert dualwrite.is_db_invalid(project)


# ---------- AMEND ----------

class TestAmendDualWrite:
    def _setup_mid_review(self, project):
        cycle.init_cycle("p", "plan", "L", "R", "v1", str(project))

    def test_amend_adds_round_to_db(self, project):
        self._setup_mid_review(project)
        cycle.add_round("p", "plan", "lead", "AMEND",
                        1, "addendum", str(project))
        conn = db.connect(project_dir=str(project))
        try:
            rounds = db.get_rounds(conn, "p", "plan")
            assert len(rounds) == 2
            actions = [r["action"] for r in rounds]
            assert actions == ["SUBMIT_FOR_REVIEW", "AMEND"]
            # Both rounds carry round=1
            assert all(r["round"] == 1 for r in rounds)
        finally:
            conn.close()

    def test_amend_does_not_change_db_status(self, project):
        self._setup_mid_review(project)
        before_conn = db.connect(project_dir=str(project))
        try:
            before = db.get_cycle(before_conn, "p", "plan")
        finally:
            before_conn.close()

        cycle.add_round("p", "plan", "lead", "AMEND",
                        1, "addendum", str(project))

        after_conn = db.connect(project_dir=str(project))
        try:
            after = db.get_cycle(after_conn, "p", "plan")
        finally:
            after_conn.close()
        # Status fields unchanged
        assert before["state"] == after["state"]
        assert before["ready_for"] == after["ready_for"]
        assert before["round"] == after["round"]

    def test_amend_no_divergence(self, project):
        self._setup_mid_review(project)
        cycle.add_round("p", "plan", "lead", "AMEND",
                        1, "addendum", str(project))
        conn = db.connect(project_dir=str(project))
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM diagnostics WHERE kind='render_mismatch'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert n == 0

    def test_amend_db_failure_does_not_break_file_amend(self, project):
        self._setup_mid_review(project)
        with patch("tagteam.db.add_round", side_effect=RuntimeError("boom")):
            status = cycle.add_round(
                "p", "plan", "lead", "AMEND",
                1, "addendum", str(project)
            )
        # File-side AMEND completed: rounds.jsonl has the new line
        rp = project / "docs" / "handoffs" / "p_plan_rounds.jsonl"
        assert "addendum" in rp.read_text()
        # AMEND returns the unchanged status
        assert status["state"] == "in-progress"
        assert status["ready_for"] == "reviewer"
        # DB invalid
        assert dualwrite.is_db_invalid(project)
