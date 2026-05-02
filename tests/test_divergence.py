"""Tests for the divergence detector (`tagteam.divergence`).

Covers each result kind (ok, db_invalid, file_inconsistent across all
five sub-checks, render_mismatch), the project-level state-file
sanity helper, and the convenience `log_divergence_if_needed` wrapper.
"""

import json
import os
from pathlib import Path

import pytest

from tagteam import db, divergence, dualwrite


# ---------- Fixtures ----------

@pytest.fixture
def project(tmp_path):
    """Project dir with docs/handoffs/."""
    (tmp_path / "docs" / "handoffs").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def conn(project):
    """Open a DB at the project's default location and yield it."""
    c = db.connect(project_dir=str(project))
    yield c
    c.close()


def _write_status(project, _phase, _ctype, **overrides):
    """Write a docs/handoffs/<_phase>_<_ctype>_status.json with given fields.

    Positional args use leading underscores so callers can pass
    `phase=...`/`type=...` overrides without colliding with the
    filename-determining args.
    """
    base = {
        "state": "approved", "ready_for": None, "round": 1,
        "phase": _phase, "type": _ctype,
        "lead": "L", "reviewer": "R", "date": "2026-05-01",
    }
    base.update(overrides)
    path = project / "docs" / "handoffs" / f"{_phase}_{_ctype}_status.json"
    path.write_text(json.dumps(base, indent=2) + "\n")
    return path


def _write_rounds(project, phase, ctype, rounds):
    """Write a rounds.jsonl file with the given list of round dicts."""
    path = project / "docs" / "handoffs" / f"{phase}_{ctype}_rounds.jsonl"
    with path.open("w") as f:
        for r in rounds:
            f.write(json.dumps(r) + "\n")
    return path


def _seed_db_from_files(project, conn):
    """Run the importer so the DB matches the file-side state."""
    db.import_from_files(project, conn)


# ---------- check_cycle_divergence ----------

class TestCheckCycleDivergence:
    def test_ok_when_renders_match(self, project, conn):
        _write_status(project, "p", "plan")
        _write_rounds(project, "p", "plan", [
            {"round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "v1", "ts": "2026-05-01T00:00:00+00:00"},
        ])
        _seed_db_from_files(project, conn)
        result = divergence.check_cycle_divergence(conn, project, "p", "plan")
        assert result["kind"] == divergence.CHECK_OK

    def test_db_invalid_short_circuits(self, project, conn):
        _write_status(project, "p", "plan")
        _write_rounds(project, "p", "plan", [
            {"round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "v1", "ts": "2026-05-01T00:00:00+00:00"},
        ])
        # Note: DB intentionally NOT seeded — proves the gate runs
        # before any DB read.
        dualwrite.mark_db_invalid(project, reason="test")

        result = divergence.check_cycle_divergence(conn, project, "p", "plan")
        assert result["kind"] == divergence.CHECK_DB_INVALID
        assert result["reason"] == "test"
        assert "since" in result

    def test_render_mismatch_when_renders_differ(self, project, conn):
        # File says round=1 with content "file_only"
        _write_status(project, "p", "plan", round=1)
        _write_rounds(project, "p", "plan", [
            {"round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "file_only", "ts": "2026-05-01T00:00:00+00:00"},
        ])
        # DB seeded from files, then we mutate the DB directly so the
        # two sides diverge.
        _seed_db_from_files(project, conn)
        conn.execute(
            "UPDATE rounds SET content='db_only' WHERE 1=1"
        )
        conn.commit()

        result = divergence.check_cycle_divergence(conn, project, "p", "plan")
        assert result["kind"] == divergence.CHECK_RENDER_MISMATCH
        assert "file_sha" in result and "db_sha" in result
        assert result["file_sha"] != result["db_sha"]
        assert result["ndiff_lines"] >= 1

    def test_render_mismatch_missing_on_one_side(self, project, conn):
        # File side has the cycle, DB does not.
        _write_status(project, "p", "plan")
        _write_rounds(project, "p", "plan", [
            {"round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "x", "ts": "2026-05-01T00:00:00+00:00"},
        ])
        # No _seed_db_from_files — DB is empty.

        result = divergence.check_cycle_divergence(conn, project, "p", "plan")
        assert result["kind"] == divergence.CHECK_RENDER_MISMATCH
        assert result["detail"] == "missing_on_one_side"
        assert result["file_present"] is True
        assert result["db_present"] is False

    def test_ok_when_cycle_missing_on_both_sides(self, project, conn):
        result = divergence.check_cycle_divergence(conn, project, "p", "plan")
        assert result["kind"] == divergence.CHECK_OK

    def test_full_diff_under_env_var(self, project, conn, monkeypatch):
        _write_status(project, "p", "plan")
        _write_rounds(project, "p", "plan", [
            {"round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "file_only", "ts": "2026-05-01T00:00:00+00:00"},
        ])
        _seed_db_from_files(project, conn)
        conn.execute("UPDATE rounds SET content='db_only' WHERE 1=1")
        conn.commit()

        monkeypatch.setenv(divergence.ENV_FULL_DIFF, "1")
        result = divergence.check_cycle_divergence(conn, project, "p", "plan")
        assert result["kind"] == divergence.CHECK_RENDER_MISMATCH
        assert "full_diff" in result
        # The diff should mention both contents
        assert "file_only" in result["full_diff"]
        assert "db_only" in result["full_diff"]

    def test_no_full_diff_by_default(self, project, conn, monkeypatch):
        _write_status(project, "p", "plan")
        _write_rounds(project, "p", "plan", [
            {"round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "x", "ts": "2026-05-01T00:00:00+00:00"},
        ])
        _seed_db_from_files(project, conn)
        conn.execute("UPDATE rounds SET content='y' WHERE 1=1")
        conn.commit()
        monkeypatch.delenv(divergence.ENV_FULL_DIFF, raising=False)
        result = divergence.check_cycle_divergence(conn, project, "p", "plan")
        assert "full_diff" not in result
        # Hash + line count are present
        assert "file_sha" in result and "ndiff_lines" in result


# ---------- file_side_sanity (5 sub-checks) ----------

class TestFileSideSanity:
    def test_passes_for_clean_cycle(self, project):
        _write_status(project, "p", "plan")
        _write_rounds(project, "p", "plan", [
            {"round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "x", "ts": "2026-05-01T00:00:00+00:00"},
        ])
        assert divergence.file_side_sanity(project, "p", "plan") is None

    def test_passes_for_nonexistent_cycle(self, project):
        # No files → no inconsistency
        assert divergence.file_side_sanity(project, "p", "plan") is None

    def test_rounds_jsonl_unparseable(self, project):
        _write_status(project, "p", "plan")
        path = project / "docs" / "handoffs" / "p_plan_rounds.jsonl"
        path.write_text(
            '{"round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW", '
            '"content": "ok", "ts": "2026-05-01T00:00:00+00:00"}\n'
            'this line is not json\n'
        )
        result = divergence.file_side_sanity(project, "p", "plan")
        assert result is not None
        assert result["check"] == "rounds_jsonl_parseable"
        assert "line 2" in result["detail"]

    def test_status_json_unparseable(self, project):
        path = project / "docs" / "handoffs" / "p_plan_status.json"
        path.write_text("{not json")
        result = divergence.file_side_sanity(project, "p", "plan")
        assert result is not None
        assert result["check"] == "status_json_parseable"

    def test_status_missing_required_fields(self, project):
        path = project / "docs" / "handoffs" / "p_plan_status.json"
        # Missing lead/reviewer/etc.
        path.write_text(json.dumps({"state": "approved"}))
        result = divergence.file_side_sanity(project, "p", "plan")
        assert result is not None
        assert result["check"] == "status_required_fields"
        # Detail names which fields are missing
        assert "lead" in result["detail"]
        assert "reviewer" in result["detail"]

    def test_status_round_mismatch_with_rounds_max(self, project):
        # status.round=1 but rounds has round=2 entries
        _write_status(project, "p", "plan", round=1)
        _write_rounds(project, "p", "plan", [
            {"round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "x", "ts": "2026-05-01T00:00:00+00:00"},
            {"round": 2, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "y", "ts": "2026-05-01T00:01:00+00:00"},
        ])
        result = divergence.file_side_sanity(project, "p", "plan")
        assert result is not None
        assert result["check"] == "status_round_matches_rounds_max"
        assert "1" in result["detail"] and "2" in result["detail"]

    def test_status_phase_does_not_match_filename(self, project):
        # Filename is p_plan_status.json but the JSON says phase=other
        _write_status(project, "p", "plan", phase="other")
        result = divergence.file_side_sanity(project, "p", "plan")
        assert result is not None
        assert result["check"] == "status_phase_matches_filename"

    def test_status_type_does_not_match_filename(self, project):
        # Filename is p_plan but the JSON says type=impl
        _write_status(project, "p", "plan", type="impl")
        result = divergence.file_side_sanity(project, "p", "plan")
        assert result is not None
        assert result["check"] == "status_type_matches_filename"


# ---------- check_state_file_integrity ----------

class TestCheckStateFileIntegrity:
    def test_returns_none_when_state_file_missing(self, project):
        assert divergence.check_state_file_integrity(project) is None

    def test_returns_none_when_state_references_existing_cycle(self, project):
        _write_status(project, "p", "plan")
        (project / "handoff-state.json").write_text(json.dumps({
            "phase": "p", "type": "plan", "round": 1, "status": "ready",
        }))
        assert divergence.check_state_file_integrity(project) is None

    def test_state_json_unparseable(self, project):
        (project / "handoff-state.json").write_text("{not json")
        result = divergence.check_state_file_integrity(project)
        assert result is not None
        assert result["check"] == "state_json_parseable"

    def test_state_references_missing_cycle(self, project):
        # State points at phase=p type=plan but no _status.json exists
        (project / "handoff-state.json").write_text(json.dumps({
            "phase": "p", "type": "plan", "round": 1, "status": "ready",
        }))
        result = divergence.check_state_file_integrity(project)
        assert result is not None
        assert result["check"] == "state_references_existing_cycle"
        assert "p_plan_status.json" in result["detail"]

    def test_state_with_no_phase_passes(self, project):
        # State file exists but has no phase/type — fresh state, no
        # cycle reference to validate.
        (project / "handoff-state.json").write_text(json.dumps({
            "status": "ready",
        }))
        assert divergence.check_state_file_integrity(project) is None


# ---------- log_divergence_if_needed ----------

class TestLogDivergenceIfNeeded:
    def test_does_not_log_on_ok(self, project, conn):
        _write_status(project, "p", "plan")
        _write_rounds(project, "p", "plan", [
            {"round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "x", "ts": "2026-05-01T00:00:00+00:00"},
        ])
        _seed_db_from_files(project, conn)
        result = divergence.log_divergence_if_needed(conn, project, "p", "plan")
        assert result["kind"] == divergence.CHECK_OK
        n = conn.execute("SELECT COUNT(*) FROM diagnostics").fetchone()[0]
        assert n == 0

    def test_logs_on_render_mismatch(self, project, conn):
        _write_status(project, "p", "plan")
        _write_rounds(project, "p", "plan", [
            {"round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "file", "ts": "2026-05-01T00:00:00+00:00"},
        ])
        _seed_db_from_files(project, conn)
        conn.execute("UPDATE rounds SET content='db' WHERE 1=1")
        conn.commit()
        result = divergence.log_divergence_if_needed(conn, project, "p", "plan")
        assert result["kind"] == divergence.CHECK_RENDER_MISMATCH
        rows = conn.execute(
            "SELECT kind, payload_json FROM diagnostics"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "render_mismatch"
        payload = json.loads(rows[0][1])
        assert payload["phase"] == "p"
        assert "file_sha" in payload

    def test_logs_on_file_inconsistent(self, project, conn):
        path = project / "docs" / "handoffs" / "p_plan_status.json"
        path.write_text("{not json")
        result = divergence.log_divergence_if_needed(conn, project, "p", "plan")
        assert result["kind"] == divergence.CHECK_FILE_INCONSISTENT
        n = conn.execute("SELECT COUNT(*) FROM diagnostics").fetchone()[0]
        assert n == 1

    def test_logs_on_db_invalid(self, project, conn):
        dualwrite.mark_db_invalid(project, reason="test")
        result = divergence.log_divergence_if_needed(conn, project, "p", "plan")
        assert result["kind"] == divergence.CHECK_DB_INVALID
        n = conn.execute("SELECT COUNT(*) FROM diagnostics").fetchone()[0]
        assert n == 1

    def test_diagnostic_log_failure_does_not_propagate(self, project):
        """If the DB itself is unavailable (caller passed a broken
        connection), the divergence check still returns its result —
        logging failure is swallowed."""
        class BrokenConn:
            def execute(self, *a, **kw):
                raise RuntimeError("DB unavailable")

        # File-inconsistent path doesn't touch the conn for the check
        # itself, only for the diagnostic log. Force inconsistency:
        path = project / "docs" / "handoffs" / "p_plan_status.json"
        path.write_text("{not json")
        result = divergence.log_divergence_if_needed(
            BrokenConn(), project, "p", "plan"
        )
        # Result still classified correctly even though logging failed.
        assert result["kind"] == divergence.CHECK_FILE_INCONSISTENT


# ---------- Helpers ----------

class TestHelpers:
    def test_hash_deterministic(self):
        assert divergence._hash("foo") == divergence._hash("foo")
        assert divergence._hash("foo") != divergence._hash("bar")
        assert len(divergence._hash("foo")) == 16

    def test_count_diff_lines_zero_for_identical(self):
        assert divergence._count_diff_lines("a\nb\nc", "a\nb\nc") == 0

    def test_count_diff_lines_nonzero_for_different(self):
        assert divergence._count_diff_lines("a\nb\nc", "a\nX\nc") >= 2

    def test_full_diff_enabled_respects_env(self, monkeypatch):
        monkeypatch.delenv(divergence.ENV_FULL_DIFF, raising=False)
        assert divergence._full_diff_enabled() is False
        monkeypatch.setenv(divergence.ENV_FULL_DIFF, "1")
        assert divergence._full_diff_enabled() is True
        monkeypatch.setenv(divergence.ENV_FULL_DIFF, "0")
        assert divergence._full_diff_enabled() is False
