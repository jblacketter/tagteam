"""Tests for the Step B auto-export hook wired into cycle writers."""

import json
from datetime import datetime, timezone, timedelta

import pytest

from tagteam import cycle, db, dualwrite
from tagteam.state import DIAGNOSTICS_LOG


@pytest.fixture
def project(tmp_path):
    (tmp_path / "docs" / "handoffs").mkdir(parents=True)
    return tmp_path


def _md(project, phase="p", cycle_type="plan"):
    return project / "docs" / "handoffs" / f"{phase}_{cycle_type}.md"


def _diagnostics(project):
    path = project / DIAGNOSTICS_LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


class TestAutoExportHook:
    def test_flag_off_init_writes_no_md(self, project, monkeypatch):
        monkeypatch.delenv("TAGTEAM_STEP_B", raising=False)
        cycle.init_cycle("p", "plan", "L", "R", "v1", str(project))
        assert not _md(project).exists()

    def test_flag_on_init_writes_md_equal_to_db_render(self, project, monkeypatch):
        monkeypatch.setenv("TAGTEAM_STEP_B", "1")
        cycle.init_cycle("p", "plan", "L", "R", "v1", str(project))
        conn = db.connect(project_dir=str(project))
        try:
            assert _md(project).read_text() == db.render_cycle(conn, "p", "plan")
        finally:
            conn.close()

    def test_flag_on_add_round_updates_md(self, project, monkeypatch):
        monkeypatch.setenv("TAGTEAM_STEP_B", "1")
        cycle.init_cycle("p", "plan", "L", "R", "v1", str(project))
        cycle.add_round(
            "p", "plan", "reviewer", "REQUEST_CHANGES", 1,
            "fix this", str(project),
        )
        assert "fix this" in _md(project).read_text()
        conn = db.connect(project_dir=str(project))
        try:
            assert _md(project).read_text() == db.render_cycle(conn, "p", "plan")
        finally:
            conn.close()

    def test_flag_on_amend_updates_md(self, project, monkeypatch):
        monkeypatch.setenv("TAGTEAM_STEP_B", "1")
        cycle.init_cycle("p", "plan", "L", "R", "v1", str(project))
        cycle.add_round(
            "p", "plan", "lead", "AMEND", 1,
            "important addendum", str(project),
        )
        assert "important addendum" in _md(project).read_text()

    def test_db_invalid_skips_export_and_logs(self, project, monkeypatch):
        monkeypatch.setenv("TAGTEAM_STEP_B", "1")
        cycle.init_cycle("p", "plan", "L", "R", "v1", str(project))
        before = _md(project).read_text()
        dualwrite.mark_db_invalid(project, reason="simulated")
        flag = project / ".tagteam" / "DB_INVALID"
        info = json.loads(flag.read_text())
        info["next_attempt_at"] = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()
        flag.write_text(json.dumps(info))

        cycle.add_round(
            "p", "plan", "reviewer", "REQUEST_CHANGES", 1,
            "fix this", str(project),
        )

        assert _md(project).read_text() == before
        entries = _diagnostics(project)
        assert entries[-1]["kind"] == "auto_export_skipped_db_invalid"
        assert entries[-1]["phase"] == "p"

    def test_render_false_logs_without_raising(self, project, monkeypatch):
        monkeypatch.setenv("TAGTEAM_STEP_B", "1")
        cycle.init_cycle("p", "plan", "L", "R", "v1", str(project))
        monkeypatch.setattr(
            "tagteam.auto_export.render_cycle_to_file",
            lambda *args, **kwargs: False,
        )

        cycle.add_round(
            "p", "plan", "reviewer", "REQUEST_CHANGES", 1,
            "fix this", str(project),
        )

        entries = _diagnostics(project)
        assert entries[-1]["kind"] == "auto_export_failed"
        assert entries[-1]["reason"] == "render_returned_false"

    def test_render_exception_logs_without_raising(self, project, monkeypatch):
        monkeypatch.setenv("TAGTEAM_STEP_B", "1")
        cycle.init_cycle("p", "plan", "L", "R", "v1", str(project))

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("tagteam.auto_export.render_cycle_to_file", boom)

        cycle.add_round(
            "p", "plan", "reviewer", "REQUEST_CHANGES", 1,
            "fix this", str(project),
        )

        entries = _diagnostics(project)
        assert entries[-1]["kind"] == "auto_export_failed"
        assert "RuntimeError: boom" in entries[-1]["reason"]
