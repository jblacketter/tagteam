"""Tests for the migrate module."""

import json
import pytest
from pathlib import Path

from tagteam.migrate import detect_agent_names, migrate_to_sqlite_command
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
