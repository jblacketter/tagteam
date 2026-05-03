"""Stage 2 (DB-backed runtime readers) regression and contract tests.

Covers:
- db_invalid sentinel policy in cycle.read_status / read_rounds
- DB-vs-file equivalence of read shapes
- post-migration read access (the bug Step B activation hit)
- impl baseline propagation through the new reader after Step B migrate
- divergence still detects drift now that cycle.render_cycle is DB-backed
- divergence finds files in .tagteam/legacy/ after Step B migrate
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tagteam import cycle, db, dualwrite
from tagteam.cycle import (
    CycleReadError, init_cycle, add_round, read_status, read_rounds,
    render_cycle, render_cycle_from_files,
)
from tagteam.migrate import migrate_to_step_b_command


@pytest.fixture
def project(tmp_path):
    (tmp_path / "docs" / "handoffs").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def populated(project):
    """A project with one approved plan cycle."""
    init_cycle("alpha", "plan", "Lead", "Reviewer", "first", str(project))
    add_round("alpha", "plan", "reviewer", "APPROVE", 1, "ok", str(project))
    return project


# ---------- db_invalid policy ----------

class TestDbInvalidPolicy:
    def test_falls_back_to_legacy_files_when_sentinel_set(self, populated):
        dualwrite.mark_db_invalid(populated, reason="simulated")
        # Even with sentinel set, file source still works.
        status = read_status("alpha", "plan", str(populated))
        assert status is not None
        assert status["state"] == "approved"
        rounds = read_rounds("alpha", "plan", str(populated))
        assert len(rounds) == 2

    def test_raises_when_sentinel_set_and_no_legacy_anywhere(self, project):
        # Empty project + sentinel set + asking for any cycle → raises.
        dualwrite.mark_db_invalid(project, reason="simulated")
        with pytest.raises(CycleReadError):
            read_status("nonexistent", "plan", str(project))
        with pytest.raises(CycleReadError):
            read_rounds("nonexistent", "plan", str(project))

    def test_returns_none_when_sentinel_set_but_other_cycles_exist(
        self, populated
    ):
        # Other cycle exists in legacy, so asking for a different cycle
        # returns None rather than raising — distinguishes "DB broken"
        # from "this specific cycle never existed."
        dualwrite.mark_db_invalid(populated, reason="simulated")
        assert read_status("nonexistent", "plan", str(populated)) is None
        assert read_rounds("nonexistent", "plan", str(populated)) == []


# ---------- DB-vs-file shape equivalence ----------

class TestDbBackedReadsMatchFileReads:
    def test_status_matches_file_shape(self, populated):
        # Read via the new (DB-backed) helper.
        db_status = read_status("alpha", "plan", str(populated))
        # Read the file directly.
        file_status = json.loads(
            (populated / "docs" / "handoffs" / "alpha_plan_status.json")
            .read_text()
        )
        # Every value present in file should be present in DB read with
        # the same value. DB intentionally omits keys when value is None
        # (mirrors db.export_to_files), so file-side null entries can be
        # absent from db_status; that's fine for consumers.
        for k, v in file_status.items():
            if v is None:
                assert db_status.get(k) is None
            else:
                assert db_status.get(k) == v, k

    def test_rounds_match_file_shape(self, populated):
        db_rounds = read_rounds("alpha", "plan", str(populated))
        file_lines = (
            populated / "docs" / "handoffs" / "alpha_plan_rounds.jsonl"
        ).read_text().splitlines()
        file_rounds = [json.loads(l) for l in file_lines if l.strip()]
        assert len(db_rounds) == len(file_rounds)
        for d, f in zip(db_rounds, file_rounds):
            assert set(d) == set(f), (set(d), set(f))
            for k in f:
                assert d[k] == f[k], k


# ---------- The regression test that should have caught Step B activation ----------

class TestPostMigrationReads:
    def test_reads_still_work_after_migrate_to_step_b(self, populated):
        """After Step B activation moves _rounds.jsonl/_status.json out
        of docs/handoffs/, runtime CLI reads must still succeed.
        This is the test that would have caught the bug we hit
        during the first activation attempt on this repo."""
        rc = migrate_to_step_b_command(
            ["--to-step-b", "--dir", str(populated)]
        )
        assert rc == 0
        # Files should be in .tagteam/legacy/, not docs/handoffs/.
        assert not (
            populated / "docs" / "handoffs" / "alpha_plan_rounds.jsonl"
        ).exists()
        assert (
            populated / ".tagteam" / "legacy" / "alpha_plan_rounds.jsonl"
        ).exists()

        # And reads still work.
        status = read_status("alpha", "plan", str(populated))
        assert status is not None
        assert status["state"] == "approved"
        rounds = read_rounds("alpha", "plan", str(populated))
        assert len(rounds) == 2

        # And render still works (DB-backed).
        md = render_cycle("alpha", "plan", str(populated))
        assert md is not None
        assert "## Round 1" in md
        assert "### Lead" in md
        assert "### Reviewer" in md


# ---------- Baseline propagation post Step B migrate ----------

class TestBaselinePropagationAfterStepB:
    def test_impl_init_preserves_plan_baseline_after_migrate(self, populated):
        """After `migrate --to-step-b` moves plan status to .tagteam/legacy/,
        starting an impl cycle for the same phase must still pull the
        plan's baseline (not fall back to fresh impl-init capture)."""
        # Capture the plan baseline before the migrate.
        plan_baseline = read_status(
            "alpha", "plan", str(populated)
        ).get("baseline")

        # Activate Step B.
        rc = migrate_to_step_b_command(
            ["--to-step-b", "--dir", str(populated)]
        )
        assert rc == 0

        # Now init the impl cycle.
        init_cycle("alpha", "impl", "Lead", "Reviewer", "impl start",
                   str(populated))
        impl_status = read_status("alpha", "impl", str(populated))
        assert impl_status is not None
        impl_baseline = impl_status.get("baseline")

        if plan_baseline is None:
            # Test fixture didn't create a baseline (e.g. not in git).
            # Then impl baseline is also None or freshly captured.
            return

        # Plan baseline propagated, marked as copied-from-plan.
        assert impl_baseline is not None
        assert impl_baseline.get("source") == "copied-from-plan"


# ---------- Divergence still works ----------

class TestDivergenceStillDetectsDrift:
    def test_file_drift_detected_after_stage_2(self, populated):
        """cycle.render_cycle is now DB-backed, so divergence must use
        cycle.render_cycle_from_files for the file side. If it doesn't,
        divergence becomes DB-vs-DB and stops detecting actual drift."""
        from tagteam import divergence

        # Mutate the file side without touching the DB (simulate drift)
        # by changing the content of an existing round. Adding new
        # rounds would trip the file-consistency sanity check first
        # (max-round-must-match-status); content drift bypasses sanity
        # and goes straight to render-mismatch.
        rounds_path = (
            populated / "docs" / "handoffs" / "alpha_plan_rounds.jsonl"
        )
        lines = rounds_path.read_text().splitlines()
        first = json.loads(lines[0])
        first["content"] = first["content"] + " DRIFT"
        lines[0] = json.dumps(first)
        rounds_path.write_text("\n".join(lines) + "\n")

        # Divergence check should report a render mismatch.
        conn = db.connect(project_dir=str(populated))
        try:
            result = divergence.check_cycle_divergence(
                conn, populated, "alpha", "plan"
            )
        finally:
            conn.close()
        assert result["kind"] == divergence.CHECK_RENDER_MISMATCH

    def test_render_from_files_finds_legacy_dir(self, populated):
        """After migrate --to-step-b moves files to .tagteam/legacy/,
        render_cycle_from_files must still find them there."""
        # Pre-migrate baseline.
        pre_md = render_cycle_from_files("alpha", "plan", str(populated))
        assert pre_md is not None

        # Activate Step B.
        rc = migrate_to_step_b_command(
            ["--to-step-b", "--dir", str(populated)]
        )
        assert rc == 0

        # Files moved — render_from_files still finds them in .tagteam/legacy/.
        post_md = render_cycle_from_files("alpha", "plan", str(populated))
        assert post_md is not None
        assert post_md == pre_md
