"""Tests for the cycle storage module."""

import json
import os
from pathlib import Path

import pytest

from tagteam.cycle import (
    init_cycle, add_round, read_status, read_rounds,
    render_cycle, list_cycles, VALID_ACTIONS, cycle_command,
)


@pytest.fixture
def project(tmp_path):
    """Create a minimal project directory with docs/handoffs/."""
    (tmp_path / "docs" / "handoffs").mkdir(parents=True)
    return str(tmp_path)


class TestInitCycle:
    def test_creates_status_and_rounds(self, project):
        status = init_cycle("my-phase", "plan", "Claude", "Codex",
                            "Initial submission.", project)
        assert status["state"] == "in-progress"
        assert status["ready_for"] == "reviewer"
        assert status["round"] == 1
        assert status["lead"] == "Claude"

        # Files exist
        handoffs = Path(project) / "docs" / "handoffs"
        assert (handoffs / "my-phase_plan_status.json").exists()
        assert (handoffs / "my-phase_plan_rounds.jsonl").exists()

    def test_first_round_in_jsonl(self, project):
        init_cycle("p", "plan", "A", "B", "Content here.", project)
        rounds = read_rounds("p", "plan", project)
        assert len(rounds) == 1
        assert rounds[0]["role"] == "lead"
        assert rounds[0]["action"] == "SUBMIT_FOR_REVIEW"
        assert rounds[0]["content"] == "Content here."
        assert rounds[0]["round"] == 1

    def test_creates_handoffs_dir_if_missing(self, tmp_path):
        project = str(tmp_path)
        # No docs/handoffs/ yet
        init_cycle("p", "plan", "A", "B", "test", project)
        assert (tmp_path / "docs" / "handoffs" / "p_plan_status.json").exists()


class TestAddRound:
    def test_appends_entry(self, project):
        init_cycle("p", "plan", "A", "B", "init", project)
        add_round("p", "plan", "reviewer", "REQUEST_CHANGES", 1, "Fix it.", project)

        rounds = read_rounds("p", "plan", project)
        assert len(rounds) == 2
        assert rounds[1]["role"] == "reviewer"
        assert rounds[1]["action"] == "REQUEST_CHANGES"

    def test_submit_transitions(self, project):
        init_cycle("p", "plan", "A", "B", "init", project)
        add_round("p", "plan", "reviewer", "REQUEST_CHANGES", 1, "Fix.", project)
        status = add_round("p", "plan", "lead", "SUBMIT_FOR_REVIEW", 2, "Fixed.", project)
        assert status["state"] == "in-progress"
        assert status["ready_for"] == "reviewer"
        assert status["round"] == 2

    def test_approve_transition(self, project):
        init_cycle("p", "plan", "A", "B", "init", project)
        status = add_round("p", "plan", "reviewer", "APPROVE", 1, "LGTM.", project)
        assert status["state"] == "approved"
        assert status["ready_for"] is None

    def test_escalate_transition(self, project):
        init_cycle("p", "plan", "A", "B", "init", project)
        status = add_round("p", "plan", "reviewer", "ESCALATE", 1, "Need help.", project)
        assert status["state"] == "escalated"
        assert status["ready_for"] == "human"

    def test_need_human_transition(self, project):
        init_cycle("p", "plan", "A", "B", "init", project)
        status = add_round("p", "plan", "reviewer", "NEED_HUMAN", 1, "Question.", project)
        assert status["state"] == "needs-human"
        assert status["ready_for"] == "human"

    def test_reviewer_does_not_advance_round(self, project):
        init_cycle("p", "plan", "A", "B", "init", project)
        status = add_round("p", "plan", "reviewer", "REQUEST_CHANGES", 1, "Fix.", project)
        assert status["round"] == 1  # unchanged

    def test_invalid_action_raises(self, project):
        init_cycle("p", "plan", "A", "B", "init", project)
        with pytest.raises(ValueError, match="Invalid action"):
            add_round("p", "plan", "lead", "INVALID", 2, "oops", project)

    def test_invalid_role_raises(self, project):
        init_cycle("p", "plan", "A", "B", "init", project)
        with pytest.raises(ValueError, match="Invalid role"):
            add_round("p", "plan", "boss", "APPROVE", 1, "no", project)


class TestReadStatus:
    def test_reads_existing(self, project):
        init_cycle("p", "plan", "A", "B", "test", project)
        status = read_status("p", "plan", project)
        assert status is not None
        assert status["phase"] == "p"

    def test_returns_none_for_missing(self, project):
        assert read_status("nonexistent", "plan", project) is None


class TestReadRounds:
    def test_returns_empty_for_missing(self, project):
        assert read_rounds("nonexistent", "plan", project) == []


class TestRenderCycle:
    def test_renders_markdown(self, project):
        init_cycle("my-phase", "plan", "Claude", "Codex", "First submission.", project)
        add_round("my-phase", "plan", "reviewer", "APPROVE", 1, "Looks good.", project)

        md = render_cycle("my-phase", "plan", project)
        assert md is not None
        assert "# Plan Review Cycle: my-phase" in md
        assert "## Round 1" in md
        assert "### Lead" in md
        assert "### Reviewer" in md
        assert "First submission." in md
        assert "Looks good." in md
        assert "STATE: approved" in md

    def test_returns_none_for_missing(self, project):
        assert render_cycle("nonexistent", "plan", project) is None


class TestListCycles:
    def test_lists_jsonl_cycles(self, project):
        init_cycle("p1", "plan", "A", "B", "test", project)
        init_cycle("p2", "impl", "A", "B", "test", project)

        cycles = list_cycles(project)
        assert len(cycles) == 2
        ids = {c["id"] for c in cycles}
        assert "p1_plan" in ids
        assert "p2_impl" in ids
        assert all(c["format"] == "jsonl" for c in cycles)

    def test_lists_legacy_md_cycles(self, project):
        # Create a legacy .md cycle
        handoffs = Path(project) / "docs" / "handoffs"
        (handoffs / "old-phase_plan_cycle.md").write_text("# Legacy cycle\n")

        cycles = list_cycles(project)
        assert len(cycles) == 1
        assert cycles[0]["id"] == "old-phase_plan"
        assert cycles[0]["format"] == "markdown"

    def test_jsonl_takes_precedence(self, project):
        # Create both formats for the same cycle
        init_cycle("both", "plan", "A", "B", "test", project)
        handoffs = Path(project) / "docs" / "handoffs"
        (handoffs / "both_plan_cycle.md").write_text("# Legacy\n")

        cycles = list_cycles(project)
        matching = [c for c in cycles if c["id"] == "both_plan"]
        assert len(matching) == 1
        assert matching[0]["format"] == "jsonl"

    def test_empty_dir(self, tmp_path):
        assert list_cycles(str(tmp_path)) == []


class TestMultiRoundCycle:
    def test_full_review_cycle(self, project):
        """Integration test: full 3-round cycle with approval."""
        init_cycle("test", "plan", "Claude", "Codex", "Plan v1.", project)
        add_round("test", "plan", "reviewer", "REQUEST_CHANGES", 1, "Needs work.", project)
        add_round("test", "plan", "lead", "SUBMIT_FOR_REVIEW", 2, "Plan v2.", project)
        add_round("test", "plan", "reviewer", "REQUEST_CHANGES", 2, "Almost.", project)
        add_round("test", "plan", "lead", "SUBMIT_FOR_REVIEW", 3, "Plan v3.", project)
        status = add_round("test", "plan", "reviewer", "APPROVE", 3, "Ship it.", project)

        assert status["state"] == "approved"
        assert status["round"] == 3

        rounds = read_rounds("test", "plan", project)
        assert len(rounds) == 6  # 3 lead + 3 reviewer entries

        md = render_cycle("test", "plan", project)
        assert "## Round 1" in md
        assert "## Round 2" in md
        assert "## Round 3" in md
        assert "STATE: approved" in md


class TestUpdatedByStateIntegration:
    """--updated-by should update handoff-state.json alongside cycle."""

    def test_init_updates_state(self, project):
        from tagteam.state import read_state
        init_cycle("p", "plan", "Claude", "Codex", "init", project,
                   updated_by="Claude")
        state = read_state(project)
        assert state is not None
        assert state["turn"] == "reviewer"
        assert state["status"] == "ready"
        assert state["phase"] == "p"
        assert state["type"] == "plan"
        assert state["updated_by"] == "Claude"

    def test_add_round_updates_state(self, project):
        from tagteam.state import read_state
        init_cycle("p", "plan", "A", "B", "init", project, updated_by="A")
        add_round("p", "plan", "reviewer", "REQUEST_CHANGES", 1, "Fix.",
                  project, updated_by="B")
        state = read_state(project)
        assert state["turn"] == "lead"
        assert state["status"] == "ready"
        assert state["updated_by"] == "B"

    def test_approve_updates_state(self, project):
        from tagteam.state import read_state
        init_cycle("p", "plan", "A", "B", "init", project, updated_by="A")
        add_round("p", "plan", "reviewer", "APPROVE", 1, "LGTM.",
                  project, updated_by="B")
        state = read_state(project)
        assert state["status"] == "done"
        assert state["result"] == "approved"

    def test_state_updates_even_without_explicit_updated_by(self, project):
        """Top-level state must sync from cycle regardless of --updated-by.

        Regression for handoff-drift bug: previously, omitting
        --updated-by caused per-cycle status to move but top-level to
        stay stale. Now top-level is always derived from per-cycle.
        """
        from tagteam.state import read_state
        init_cycle("p", "plan", "Claude", "Codex", "init", project)
        state = read_state(project)
        assert state is not None
        assert state["turn"] == "reviewer"
        assert state["status"] == "ready"
        # updated_by defaults to the lead's name when not given
        assert state["updated_by"] == "Claude"

    def test_add_round_infers_updated_by_from_role(self, project):
        """add_round without updated_by should infer from role and roster."""
        from tagteam.state import read_state
        init_cycle("p", "plan", "Claude", "Codex", "init", project)
        add_round("p", "plan", "reviewer", "REQUEST_CHANGES", 1, "Fix.", project)
        state = read_state(project)
        assert state["turn"] == "lead"
        assert state["status"] == "ready"
        # Inferred from cycle.reviewer field
        assert state["updated_by"] == "Codex"

    def test_round5_with_progress_does_not_escalate(self, project):
        """Cycles with progress (changing content) should NOT auto-escalate at round 5."""
        from tagteam.state import read_state
        init_cycle("p", "plan", "A", "B", "init", project, updated_by="A")
        # Simulate 4 rounds — each lead submission has different content (progress)
        for r in range(1, 5):
            add_round("p", "plan", "reviewer", "REQUEST_CHANGES", r, f"Fix {r}.",
                      project, updated_by="B")
            add_round("p", "plan", "lead", "SUBMIT_FOR_REVIEW", r + 1, f"v{r+1}.",
                      project, updated_by="A")
        # Round 5: REQUEST_CHANGES should NOT auto-escalate (content changed each time)
        status = add_round("p", "plan", "reviewer", "REQUEST_CHANGES", 5,
                           "One more thing.", project, updated_by="B")

        # Cycle should still be in-progress, not escalated
        assert status["state"] == "in-progress"
        assert status["ready_for"] == "lead"

        state = read_state(project)
        assert state["status"] == "ready"
        assert state["turn"] == "lead"

    def test_stale_rounds_auto_escalate(self, project):
        """Cycles with no progress (identical submissions) SHOULD auto-escalate."""
        from tagteam.cycle import STALE_ROUND_LIMIT
        from tagteam.state import read_state
        init_cycle("p", "plan", "A", "B", "same content", project, updated_by="A")
        # Simulate rounds where the lead keeps submitting identical content.
        # Need at least STALE_ROUND_LIMIT stale rounds before the next
        # REQUEST_CHANGES trips the auto-escalate guard.
        stale_rounds = STALE_ROUND_LIMIT + 1
        for r in range(1, stale_rounds + 1):
            add_round("p", "plan", "reviewer", "REQUEST_CHANGES", r, f"Fix {r}.",
                      project, updated_by="B")
            add_round("p", "plan", "lead", "SUBMIT_FOR_REVIEW", r + 1,
                      "same content",
                      project, updated_by="A")
        status = add_round("p", "plan", "reviewer", "REQUEST_CHANGES",
                           stale_rounds + 1, "Still the same.",
                           project, updated_by="B")

        # Handoff state should be escalated
        state = read_state(project)
        assert state["status"] == "escalated"
        assert "turn" not in state or state.get("turn") != "lead"

        # Cycle status should also be escalated
        assert status["state"] == "escalated"
        assert status["ready_for"] == "human"


class TestStaleStateClearing:
    """Regression tests for stale state overlay bug (2026-03-22).

    When transitioning between cycles, stale completion state like
    result="approved" or roadmap metadata from previous cycles should
    be cleared, not carried forward via shallow overlay.
    """

    def test_submit_for_review_clears_stale_result(self, project):
        """SUBMIT_FOR_REVIEW should clear stale result from previous cycle."""
        from tagteam.state import read_state, write_state

        # Create a synthetic completed state with result="roadmap-complete"
        write_state({
            "phase": "old-phase",
            "type": "plan",
            "turn": "lead",
            "status": "done",
            "result": "roadmap-complete",
            "round": 3,
        }, project)

        # Start a new cycle and submit for review
        init_cycle("new-phase", "plan", "A", "B", "New plan.", project,
                   updated_by="A")

        # Verify stale result is cleared
        state = read_state(project)
        assert state["phase"] == "new-phase"
        assert state["turn"] == "reviewer"
        assert state["status"] == "ready"
        assert state.get("result") is None  # Should be explicitly cleared

    def test_request_changes_clears_stale_result(self, project):
        """REQUEST_CHANGES should clear stale completion state."""
        from tagteam.state import read_state, write_state

        # Create a synthetic completed state
        write_state({
            "phase": "completed-phase",
            "type": "impl",
            "status": "done",
            "result": "approved",
            "round": 2,
        }, project)

        # Start new cycle and have reviewer request changes
        init_cycle("active-phase", "impl", "A", "B", "Implementation.", project,
                   updated_by="A")
        add_round("active-phase", "impl", "reviewer", "REQUEST_CHANGES", 1,
                  "Needs fixes.", project, updated_by="B")

        # Verify stale result is cleared
        state = read_state(project)
        assert state["phase"] == "active-phase"
        assert state["turn"] == "lead"
        assert state["status"] == "ready"
        assert state.get("result") is None  # Should be explicitly cleared

    def test_roadmap_preserved_during_active_roadmap_transitions(self, project):
        """Roadmap context should be preserved when cycling within active roadmap."""
        from tagteam.state import read_state, write_state

        # Create a state with active roadmap where phase-2 is the current phase
        roadmap = {
            "queue": ["phase-1", "phase-2", "phase-3"],
            "current_index": 1,  # phase-2 is at index 1
            "completed": ["phase-1"],
            "pause_reason": None,
        }
        write_state({
            "phase": "phase-2",
            "type": "plan",
            "turn": "reviewer",
            "status": "ready",
            "round": 1,
            "run_mode": "full-roadmap",
            "roadmap": roadmap,
        }, project)

        # Reviewer requests changes for phase-2
        add_round("phase-2", "plan", "reviewer", "REQUEST_CHANGES", 1,
                  "Fix this.", project, updated_by="B")

        # Verify roadmap is preserved because phase-2 matches current roadmap phase
        state = read_state(project)
        assert state["roadmap"] == roadmap
        assert state["run_mode"] == "full-roadmap"
        assert state.get("result") is None  # But result should be cleared

    def test_single_phase_cycle_clears_stale_roadmap(self, project):
        """Starting a new single-phase cycle should clear stale roadmap context."""
        from tagteam.state import read_state, write_state

        # Create a completed full-roadmap state
        write_state({
            "phase": "old-phase-c",
            "type": "impl",
            "status": "done",
            "result": "roadmap-complete",
            "round": 1,
            "run_mode": "full-roadmap",
            "roadmap": {
                "queue": ["old-phase-a", "old-phase-b", "old-phase-c"],
                "current_index": 2,
                "completed": ["old-phase-a", "old-phase-b", "old-phase-c"],
                "pause_reason": None,
            },
        }, project)

        # Start a brand new single-phase impl cycle for a different phase
        init_cycle("new-unrelated-phase", "impl", "A", "B",
                   "New implementation.", project, updated_by="A")

        # Verify stale roadmap context is cleared
        state = read_state(project)
        assert state["phase"] == "new-unrelated-phase"
        assert state["type"] == "impl"
        assert state["run_mode"] == "single-phase"
        assert "roadmap" not in state  # Roadmap should be completely removed
        assert state.get("result") is None  # Result should be cleared

    def test_roadmap_watcher_cannot_advance_after_single_phase_approval(self, project):
        """After approving a single-phase cycle, watcher should not auto-advance stale roadmap."""
        from tagteam.state import read_state, write_state

        # Create a completed full-roadmap state with stale queue
        write_state({
            "phase": "old-b",
            "type": "impl",
            "status": "done",
            "result": "approved",
            "round": 1,
            "run_mode": "full-roadmap",
            "roadmap": {
                "queue": ["old-a", "old-b", "old-c"],
                "current_index": 1,
                "completed": ["old-a", "old-b"],
                "pause_reason": None,
            },
        }, project)

        # Start a new single-phase cycle for a completely different phase
        init_cycle("fresh-phase", "impl", "A", "B",
                   "Fresh implementation.", project, updated_by="A")

        # Approve the fresh cycle
        add_round("fresh-phase", "impl", "reviewer", "APPROVE", 1,
                  "LGTM.", project, updated_by="B")

        # Verify the state shows approval for fresh-phase, not old-c
        state = read_state(project)
        assert state["phase"] == "fresh-phase"
        assert state["status"] == "done"
        assert state["result"] == "approved"
        assert state["run_mode"] == "single-phase"
        assert "roadmap" not in state  # No stale roadmap to advance



LEGACY_CYCLE_MD = """\
# Plan Review Cycle: legacy-phase

- **Phase:** legacy-phase
- **Type:** plan

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Legacy lead submission.

### Reviewer

**Action:** APPROVE

Legacy approval.

---

<!-- CYCLE_STATUS -->
READY_FOR: lead
ROUND: 1
STATE: approved
"""


class TestCLILegacyFallback:
    """CLI read commands must fall back to legacy markdown."""

    def test_cli_rounds_reads_legacy_md(self, project, capsys, monkeypatch):
        monkeypatch.chdir(project)
        handoffs = Path(project) / "docs" / "handoffs"
        (handoffs / "legacy-phase_plan_cycle.md").write_text(LEGACY_CYCLE_MD)

        result = cycle_command(["rounds", "--phase", "legacy-phase", "--type", "plan"])
        assert result == 0
        captured = capsys.readouterr()
        assert "SUBMIT_FOR_REVIEW" in captured.out

    def test_cli_status_reads_legacy_md(self, project, capsys, monkeypatch):
        monkeypatch.chdir(project)
        handoffs = Path(project) / "docs" / "handoffs"
        (handoffs / "legacy-phase_plan_cycle.md").write_text(LEGACY_CYCLE_MD)

        result = cycle_command(["status", "--phase", "legacy-phase", "--type", "plan"])
        assert result == 0
        captured = capsys.readouterr()
        assert "approved" in captured.out

    def test_cli_render_reads_legacy_md(self, project, capsys, monkeypatch):
        monkeypatch.chdir(project)
        handoffs = Path(project) / "docs" / "handoffs"
        (handoffs / "legacy-phase_plan_cycle.md").write_text(LEGACY_CYCLE_MD)

        result = cycle_command(["render", "--phase", "legacy-phase", "--type", "plan"])
        assert result == 0
        captured = capsys.readouterr()
        assert "Legacy lead submission." in captured.out


class TestGitRootResolution:
    """Running cycle CLI from a nested subdir should write to the repo
    root's docs/handoffs/, not cwd. Regression for Issue #1
    (2026-04-24): `tagteam cycle init` used to silently target cwd.
    """

    @pytest.fixture
    def reset_root_cache(self):
        """_resolve_project_root caches; reset around each test."""
        import tagteam.state as st
        saved = st._cached_project_root
        st._cached_project_root = None
        yield
        st._cached_project_root = saved

    def test_init_from_subdir_writes_to_git_root(self, tmp_path, monkeypatch, reset_root_cache):
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subdir = tmp_path / "subproject"
        subdir.mkdir()
        monkeypatch.chdir(subdir)

        # Default project_dir="." should resolve to the git root (tmp_path),
        # not the subdir cwd.
        init_cycle("p", "plan", "A", "B", "init")

        # Files land at the root
        assert (tmp_path / "docs" / "handoffs" / "p_plan_status.json").exists()
        assert (tmp_path / "handoff-state.json").exists()
        # Not in the subdir
        assert not (subdir / "docs" / "handoffs").exists()
        assert not (subdir / "handoff-state.json").exists()

    def test_explicit_project_dir_still_honored(self, tmp_path, reset_root_cache):
        """Explicit project_dir must not be overridden by git-root resolution."""
        other = tmp_path / "explicit"
        other.mkdir()
        init_cycle("p", "plan", "A", "B", "init", str(other))
        assert (other / "docs" / "handoffs" / "p_plan_status.json").exists()


class TestExtractLastRoundWithProjectDir:
    """extract_last_round must work with JSONL cycles outside cwd."""

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("textual"),
        reason="textual not installed"
    )
    def test_jsonl_outside_cwd(self, tmp_path, monkeypatch):
        from tagteam.tui.handoff_reader import extract_last_round, find_cycle_doc

        # Create project in a different directory than cwd
        project = str(tmp_path / "other-project")
        os.makedirs(f"{project}/docs/handoffs")
        init_cycle("test", "plan", "A", "B", "Content.", project)
        add_round("test", "plan", "reviewer", "APPROVE", 1, "OK.", project)

        # Set cwd to somewhere else
        monkeypatch.chdir(tmp_path)

        cycle_path = find_cycle_doc("test", "plan", project_dir=project)
        assert cycle_path is not None

        result = extract_last_round(cycle_path, phase="test",
                                    step_type="plan", project_dir=project)
        assert result is not None
        assert result["action"] == "APPROVE"
