"""Tests for the migrate module."""

import json
import pytest
from pathlib import Path

from tagteam.migrate import (
    detect_agent_names, migrate_to_sqlite_command, migrate_to_step_b_command,
)
from tagteam import db


class TestDetectAgentNames:
    """Tests for detect_agent_names function."""

    def test_returns_defaults_when_no_docs(self, tmp_path):
        lead, reviewer = detect_agent_names(tmp_path)
        assert lead == "Claude"
        assert reviewer == "Codex"

    def test_returns_defaults_when_empty_handoffs(self, tmp_path):
        (tmp_path / "docs" / "handoffs").mkdir(parents=True)
        lead, reviewer = detect_agent_names(tmp_path)
        assert lead == "Claude"
        assert reviewer == "Codex"

    def test_detects_lead_name(self, tmp_path):
        handoffs = tmp_path / "docs" / "handoffs"
        handoffs.mkdir(parents=True)
        (handoffs / "test.md").write_text("**From:** Alice (Lead)\n**To:** Bob (Reviewer)")

        lead, reviewer = detect_agent_names(tmp_path)
        assert lead == "Alice"
        assert reviewer == "Bob"

    def test_detects_names_with_spaces(self, tmp_path):
        handoffs = tmp_path / "docs" / "handoffs"
        handoffs.mkdir(parents=True)
        (handoffs / "test.md").write_text("**From:** Claude 3 Opus (Lead)\n**To:** GPT-4 Turbo (Reviewer)")

        lead, reviewer = detect_agent_names(tmp_path)
        assert lead == "Claude 3 Opus"
        assert reviewer == "GPT-4 Turbo"

    def test_detects_names_with_punctuation(self, tmp_path):
        handoffs = tmp_path / "docs" / "handoffs"
        handoffs.mkdir(parents=True)
        (handoffs / "test.md").write_text("**From:** O'Brien-Smith (Lead)\n**To:** Dr. Watson (Reviewer)")

        lead, reviewer = detect_agent_names(tmp_path)
        assert lead == "O'Brien-Smith"
        assert reviewer == "Dr. Watson"

    def test_partial_detection_returns_default_for_missing(self, tmp_path):
        handoffs = tmp_path / "docs" / "handoffs"
        handoffs.mkdir(parents=True)
        (handoffs / "test.md").write_text("**From:** Alice (Lead)\nSome other content")

        lead, reviewer = detect_agent_names(tmp_path)
        assert lead == "Alice"
        assert reviewer == "Codex"  # default

    def test_scans_multiple_files(self, tmp_path):
        handoffs = tmp_path / "docs" / "handoffs"
        handoffs.mkdir(parents=True)
        (handoffs / "file1.md").write_text("**From:** Alice (Lead)")
        (handoffs / "file2.md").write_text("**To:** Bob (Reviewer)")

        lead, reviewer = detect_agent_names(tmp_path)
        # Should find both across files
        assert lead == "Alice"
        assert reviewer == "Bob"

    def test_stops_after_finding_both(self, tmp_path):
        handoffs = tmp_path / "docs" / "handoffs"
        handoffs.mkdir(parents=True)
        # First file has both
        (handoffs / "aaa.md").write_text("**From:** Alice (Lead)\n**To:** Bob (Reviewer)")
        # Second file has different names (should be ignored)
        (handoffs / "zzz.md").write_text("**From:** Charlie (Lead)\n**To:** Dave (Reviewer)")

        lead, reviewer = detect_agent_names(tmp_path)
        assert lead == "Alice"
        assert reviewer == "Bob"


# ---------- migrate --to-sqlite ----------

@pytest.fixture
def populated_project(tmp_path):
    """A project dir with a couple of cycles and some handoff state."""
    handoffs = tmp_path / "docs" / "handoffs"
    handoffs.mkdir(parents=True)
    # Cycle 1: approved plan
    (handoffs / "alpha_plan_status.json").write_text(json.dumps({
        "state": "approved", "ready_for": None, "round": 1,
        "phase": "alpha", "type": "plan",
        "lead": "L", "reviewer": "R", "date": "2026-05-01",
    }))
    (handoffs / "alpha_plan_rounds.jsonl").write_text(
        json.dumps({"round": 1, "role": "lead",
                    "action": "SUBMIT_FOR_REVIEW", "content": "v1",
                    "ts": "2026-05-01T00:00:00+00:00"}) + "\n" +
        json.dumps({"round": 1, "role": "reviewer",
                    "action": "APPROVE", "content": "ok",
                    "ts": "2026-05-01T00:01:00+00:00"}) + "\n"
    )
    # Cycle 2: in-progress impl
    (handoffs / "alpha_impl_status.json").write_text(json.dumps({
        "state": "in-progress", "ready_for": "reviewer", "round": 1,
        "phase": "alpha", "type": "impl",
        "lead": "L", "reviewer": "R", "date": "2026-05-02",
    }))
    (handoffs / "alpha_impl_rounds.jsonl").write_text(
        json.dumps({"round": 1, "role": "lead",
                    "action": "SUBMIT_FOR_REVIEW", "content": "impl v1",
                    "ts": "2026-05-02T00:00:00+00:00"}) + "\n"
    )
    # State file
    (tmp_path / "handoff-state.json").write_text(json.dumps({
        "phase": "alpha", "type": "impl", "round": 1, "status": "ready",
        "history": [{"timestamp": "2026-05-02T00:00:00+00:00",
                     "turn": "reviewer", "phase": "alpha"}],
    }))
    return tmp_path


class TestMigrateToSqlite:
    def test_success(self, populated_project, capsys):
        rc = migrate_to_sqlite_command(["--to-sqlite", "--dir", str(populated_project)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Imported: 2 cycles" in out
        assert "3 rounds" in out
        # DB exists at default location
        assert (populated_project / ".tagteam" / "tagteam.db").exists()
        # Data round-trips
        conn = db.connect(db_path=populated_project / ".tagteam" / "tagteam.db")
        try:
            assert len(db.list_cycles(conn)) == 2
            assert db.get_cycle(conn, "alpha", "plan")["state"] == "approved"
            assert db.get_cycle(conn, "alpha", "impl")["state"] == "in-progress"
        finally:
            conn.close()

    def test_dry_run_writes_nothing(self, populated_project, capsys):
        rc = migrate_to_sqlite_command(["--to-sqlite", "--dry-run",
                                         "--dir", str(populated_project)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Dry run" in out
        assert "Would import: 2 cycles" in out
        assert not (populated_project / ".tagteam" / "tagteam.db").exists()

    def test_refuses_non_empty_db(self, populated_project, capsys):
        # First migration succeeds
        assert migrate_to_sqlite_command(
            ["--to-sqlite", "--dir", str(populated_project)]
        ) == 0
        capsys.readouterr()
        # Second migration without --force fails
        rc = migrate_to_sqlite_command(["--to-sqlite", "--dir", str(populated_project)])
        assert rc == 1
        err = capsys.readouterr().out
        assert "already contains data" in err
        assert "--force" in err

    def test_force_rebuilds(self, populated_project, capsys):
        assert migrate_to_sqlite_command(
            ["--to-sqlite", "--dir", str(populated_project)]
        ) == 0
        capsys.readouterr()
        db_path = populated_project / ".tagteam" / "tagteam.db"
        stale_wal = Path(f"{db_path}-wal")
        stale_shm = Path(f"{db_path}-shm")
        stale_wal.write_bytes(b"stale-wal")
        stale_shm.write_bytes(b"stale-shm")
        rc = migrate_to_sqlite_command(
            ["--to-sqlite", "--force", "--dir", str(populated_project)]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "removed existing DB" in out
        if stale_wal.exists():
            assert stale_wal.read_bytes() != b"stale-wal"
        if stale_shm.exists():
            assert stale_shm.read_bytes() != b"stale-shm"
        # Final DB has the right counts (not doubled).
        conn = db.connect(db_path=db_path)
        try:
            n_rounds = conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0]
        finally:
            conn.close()
        assert n_rounds == 3  # 2 + 1, not 6

    def test_missing_handoffs_dir(self, tmp_path, capsys):
        rc = migrate_to_sqlite_command(["--to-sqlite", "--dir", str(tmp_path)])
        assert rc == 1
        err = capsys.readouterr().out
        assert "not found" in err

    def test_db_override(self, populated_project, tmp_path, capsys):
        custom_db = tmp_path / "custom" / "weird.db"
        rc = migrate_to_sqlite_command([
            "--to-sqlite", "--dir", str(populated_project),
            "--db", str(custom_db),
        ])
        assert rc == 0
        assert custom_db.exists()
        # Default location was NOT used
        assert not (populated_project / ".tagteam" / "tagteam.db").exists()

    def test_routes_through_migrate_command(self, populated_project, capsys):
        """The dispatcher in migrate_command should detect --to-sqlite
        and route to migrate_to_sqlite_command."""
        from tagteam.migrate import migrate_command
        # migrate_command uses Path(".") for the legacy flow but
        # migrate_to_sqlite_command honors --dir. Routing works:
        rc = migrate_command(["--to-sqlite", "--dir", str(populated_project)])
        assert rc == 0
        assert (populated_project / ".tagteam" / "tagteam.db").exists()


# ---------- migrate --to-step-b ----------

def _cycle_file_paths(project, phase, cycle_type):
    handoffs = project / "docs" / "handoffs"
    return (
        handoffs / f"{phase}_{cycle_type}_rounds.jsonl",
        handoffs / f"{phase}_{cycle_type}_status.json",
        handoffs / f"{phase}_{cycle_type}.md",
    )


class TestMigrateToStepB:
    @pytest.fixture(autouse=True)
    def _enable_step_b_migration_for_tests(self, monkeypatch):
        monkeypatch.setattr("tagteam.migrate._step_b_readers_ready", lambda: True)

    def test_happy_path_renders_and_moves_legacy_files(
        self, populated_project, capsys
    ):
        rc = migrate_to_step_b_command(
            ["--to-step-b", "--dir", str(populated_project)]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "Step B migration complete" in out

        legacy = populated_project / ".tagteam" / "legacy"
        for phase, cycle_type in (("alpha", "plan"), ("alpha", "impl")):
            rounds, status, md = _cycle_file_paths(
                populated_project, phase, cycle_type
            )
            assert md.exists()
            assert not rounds.exists()
            assert not status.exists()
            assert (legacy / rounds.name).exists()
            assert (legacy / status.name).exists()

        conn = db.connect(project_dir=str(populated_project))
        try:
            assert len(db.list_cycles(conn)) == 2
            assert (
                populated_project / "docs" / "handoffs" / "alpha_plan.md"
            ).read_text() == db.render_cycle(conn, "alpha", "plan")
        finally:
            conn.close()

    def test_second_run_is_idempotent(self, populated_project, capsys):
        assert migrate_to_step_b_command(
            ["--to-step-b", "--dir", str(populated_project)]
        ) == 0
        capsys.readouterr()
        assert migrate_to_step_b_command(
            ["--to-step-b", "--dir", str(populated_project)]
        ) == 0
        out = capsys.readouterr().out
        assert "Step B migration complete" in out
        conn = db.connect(project_dir=str(populated_project))
        try:
            assert len(db.list_cycles(conn)) == 2
        finally:
            conn.close()

    def test_rerun_rewrites_drifted_markdown(self, populated_project, capsys):
        assert migrate_to_step_b_command(
            ["--to-step-b", "--dir", str(populated_project)]
        ) == 0
        md = populated_project / "docs" / "handoffs" / "alpha_plan.md"
        md.write_text("drifted")
        capsys.readouterr()

        assert migrate_to_step_b_command(
            ["--to-step-b", "--dir", str(populated_project)]
        ) == 0
        conn = db.connect(project_dir=str(populated_project))
        try:
            assert md.read_text() == db.render_cycle(conn, "alpha", "plan")
        finally:
            conn.close()

    def test_render_failure_aborts_that_cycle_move(
        self, populated_project, monkeypatch, capsys
    ):
        from tagteam import auto_export
        real_render = auto_export.render_cycle_to_file

        def fail_impl(conn, project_dir, phase, cycle_type):
            if phase == "alpha" and cycle_type == "impl":
                return False
            return real_render(conn, project_dir, phase, cycle_type)

        monkeypatch.setattr(
            "tagteam.auto_export.render_cycle_to_file", fail_impl
        )

        rc = migrate_to_step_b_command(
            ["--to-step-b", "--dir", str(populated_project)]
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert "alpha_impl: render_failed" in out

        rounds, status, md = _cycle_file_paths(populated_project, "alpha", "impl")
        assert rounds.exists()
        assert status.exists()
        assert not md.exists()

        plan_rounds, plan_status, plan_md = _cycle_file_paths(
            populated_project, "alpha", "plan"
        )
        assert not plan_rounds.exists()
        assert not plan_status.exists()
        assert plan_md.exists()

    def test_partial_move_failure_is_recoverable(
        self, populated_project, monkeypatch, capsys
    ):
        from tagteam import migrate as migrate_mod

        real_move = migrate_mod.shutil.move
        calls = {"n": 0}

        def fail_second(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated move failure")
            return real_move(src, dst)

        monkeypatch.setattr("tagteam.migrate.shutil.move", fail_second)
        rc = migrate_to_step_b_command(
            ["--to-step-b", "--dir", str(populated_project)]
        )
        assert rc == 1
        assert "move_failed" in capsys.readouterr().out

        monkeypatch.setattr("tagteam.migrate.shutil.move", real_move)
        rc = migrate_to_step_b_command(
            ["--to-step-b", "--dir", str(populated_project)]
        )
        assert rc == 0

        conn = db.connect(project_dir=str(populated_project))
        try:
            cycles = {
                (c["phase"], c["type"]) for c in db.list_cycles(conn)
            }
            assert cycles == {("alpha", "plan"), ("alpha", "impl")}
        finally:
            conn.close()

        legacy = populated_project / ".tagteam" / "legacy"
        for phase, cycle_type in (("alpha", "plan"), ("alpha", "impl")):
            rounds, status, md = _cycle_file_paths(
                populated_project, phase, cycle_type
            )
            assert md.exists()
            assert not rounds.exists()
            assert not status.exists()
            assert (legacy / rounds.name).exists()
            assert (legacy / status.name).exists()

    def test_step_b_malformed_source_reports_file_inconsistent(
        self, populated_project, capsys
    ):
        rounds, _status, _md = _cycle_file_paths(
            populated_project, "alpha", "plan"
        )
        rounds.write_text("1\n")

        rc = migrate_to_step_b_command(
            ["--to-step-b", "--dir", str(populated_project)]
        )

        assert rc == 1
        out = capsys.readouterr().out
        assert "file_inconsistent" in out
        assert "alpha_plan" in out
        assert "object_shape" in out


def test_step_b_migration_refuses_until_readers_are_db_backed(
    populated_project, capsys, monkeypatch
):
    """Regression guard for the Stage 2 readers-ready flag.

    Stage 2 flipped STEP_B_READERS_READY = True, but the guard logic
    must still work when the flag is False — this test proves the
    refusal path is intact in case someone accidentally reverts it.
    """
    monkeypatch.setattr("tagteam.migrate._step_b_readers_ready", lambda: False)

    rc = migrate_to_step_b_command(
        ["--to-step-b", "--dir", str(populated_project)]
    )

    assert rc == 1
    out = capsys.readouterr().out
    assert "requires Stage 2 DB-backed cycle readers" in out
    rounds, status, md = _cycle_file_paths(populated_project, "alpha", "plan")
    assert rounds.exists()
    assert status.exists()
    assert not md.exists()


def test_step_b_readers_ready_is_flipped_true():
    """Sanity check: Stage 2 flipped this to True. If it gets reverted
    accidentally, this test fails loudly."""
    from tagteam.migrate import STEP_B_READERS_READY, _step_b_readers_ready
    assert STEP_B_READERS_READY is True
    assert _step_b_readers_ready() is True
