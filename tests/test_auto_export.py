"""Tests for `tagteam.auto_export.render_cycle_to_file`.

The function is the building block for Phase 28 Step B's
auto-rendered markdown export. The hook that calls it from cycle
writers lands in a subsequent commit; these tests exercise the
helper in isolation.
"""

import sqlite3
from pathlib import Path

import pytest

from tagteam import auto_export, cycle, db


@pytest.fixture
def project(tmp_path):
    (tmp_path / "docs" / "handoffs").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def conn_with_cycle(project):
    cycle.init_cycle("p", "plan", "L", "R", "first draft", str(project))
    cycle.add_round("p", "plan", "reviewer", "APPROVE", 1,
                    "Approved.", str(project))
    c = db.connect(project_dir=str(project))
    yield c, project
    c.close()


class TestRenderCycleToFile:
    def test_writes_md_file(self, conn_with_cycle):
        c, project = conn_with_cycle
        ok = auto_export.render_cycle_to_file(c, project, "p", "plan")
        assert ok is True
        out = project / "docs" / "handoffs" / "p_plan.md"
        assert out.exists()
        content = out.read_text()
        assert "# Plan Review Cycle: p" in content
        assert "**Action:** SUBMIT_FOR_REVIEW" in content
        assert "**Action:** APPROVE" in content

    def test_byte_identical_to_db_render(self, conn_with_cycle):
        """The auto-export's content must equal db.render_cycle's
        output (modulo trailing newline). This is the parity contract
        that makes auto-export non-regressive vs the existing file
        renderer."""
        c, project = conn_with_cycle
        auto_export.render_cycle_to_file(c, project, "p", "plan")
        from_disk = (project / "docs" / "handoffs" / "p_plan.md").read_text()
        from_db = db.render_cycle(c, "p", "plan")
        # Auto-export normalizes to a single trailing newline; strip
        # for comparison.
        assert from_disk.rstrip("\n") == from_db.rstrip("\n")

    def test_atomic_write_no_tmp_left_behind(self, conn_with_cycle):
        c, project = conn_with_cycle
        auto_export.render_cycle_to_file(c, project, "p", "plan")
        # No `.tmp` sibling should remain after success.
        tmps = list(
            (project / "docs" / "handoffs").glob(".*.tmp")
        )
        assert tmps == [], f"left tmp files: {tmps}"

    def test_returns_false_for_missing_cycle(self, conn_with_cycle):
        c, project = conn_with_cycle
        ok = auto_export.render_cycle_to_file(
            c, project, "nonexistent", "plan"
        )
        assert ok is False
        assert not (
            project / "docs" / "handoffs" / "nonexistent_plan.md"
        ).exists()

    def test_idempotent(self, conn_with_cycle):
        """Calling twice produces the same content and doesn't
        accumulate .tmp files."""
        c, project = conn_with_cycle
        auto_export.render_cycle_to_file(c, project, "p", "plan")
        first = (project / "docs" / "handoffs" / "p_plan.md").read_text()
        auto_export.render_cycle_to_file(c, project, "p", "plan")
        second = (project / "docs" / "handoffs" / "p_plan.md").read_text()
        assert first == second

    def test_creates_handoffs_dir_if_missing(self, tmp_path):
        """Auto-export should not require the caller to pre-create
        the handoffs/ directory (Step B activation may run before
        any other writer)."""
        # No docs/handoffs/ pre-created.
        cycle.init_cycle("p", "plan", "L", "R", "first", str(tmp_path))
        c = db.connect(project_dir=str(tmp_path))
        try:
            ok = auto_export.render_cycle_to_file(
                c, tmp_path, "p", "plan"
            )
        finally:
            c.close()
        assert ok is True
        assert (tmp_path / "docs" / "handoffs" / "p_plan.md").exists()

    def test_returns_false_on_write_failure(self, conn_with_cycle):
        """File-system errors must not propagate — auto-export is
        best-effort. Simulate failure by making the handoffs dir
        a regular file (so mkdir fails)."""
        c, project = conn_with_cycle
        target = project / "docs" / "handoffs"
        # Replace the directory with a regular file
        import shutil
        shutil.rmtree(target)
        target.write_text("not a dir")
        ok = auto_export.render_cycle_to_file(c, project, "p", "plan")
        assert ok is False
        # Recovery: caller didn't see an exception
