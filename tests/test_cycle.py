"""Tests for the cycle storage module."""

import json
import os
from pathlib import Path

import pytest

from ai_handoff.cycle import (
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
        from ai_handoff.state import read_state
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
        from ai_handoff.state import read_state
        init_cycle("p", "plan", "A", "B", "init", project, updated_by="A")
        add_round("p", "plan", "reviewer", "REQUEST_CHANGES", 1, "Fix.",
                  project, updated_by="B")
        state = read_state(project)
        assert state["turn"] == "lead"
        assert state["status"] == "ready"
        assert state["updated_by"] == "B"

    def test_approve_updates_state(self, project):
        from ai_handoff.state import read_state
        init_cycle("p", "plan", "A", "B", "init", project, updated_by="A")
        add_round("p", "plan", "reviewer", "APPROVE", 1, "LGTM.",
                  project, updated_by="B")
        state = read_state(project)
        assert state["status"] == "done"
        assert state["result"] == "approved"

    def test_no_state_update_without_flag(self, project):
        from ai_handoff.state import read_state
        init_cycle("p", "plan", "A", "B", "init", project)
        state = read_state(project)
        # State file should not have been created/updated by cycle init
        # (it may exist from prior test state, so check it wasn't updated by us)
        if state:
            assert state.get("updated_by") != "A"

    def test_round5_request_changes_auto_escalates(self, project):
        """Regression: REQUEST_CHANGES at round 5 must escalate, not hand back to lead."""
        from ai_handoff.state import read_state
        init_cycle("p", "plan", "A", "B", "init", project, updated_by="A")
        # Simulate 4 rounds of back-and-forth
        for r in range(1, 5):
            add_round("p", "plan", "reviewer", "REQUEST_CHANGES", r, f"Fix {r}.",
                      project, updated_by="B")
            add_round("p", "plan", "lead", "SUBMIT_FOR_REVIEW", r + 1, f"v{r+1}.",
                      project, updated_by="A")
        # Round 5: REQUEST_CHANGES should auto-escalate
        add_round("p", "plan", "reviewer", "REQUEST_CHANGES", 5, "Still broken.",
                  project, updated_by="B")

        # Handoff state should be escalated, not ready/lead
        state = read_state(project)
        assert state["status"] == "escalated"
        assert "turn" not in state or state.get("turn") != "lead"

        # Cycle status should also be escalated
        cycle_status = read_status("p", "plan", project)
        assert cycle_status["state"] == "escalated"
        assert cycle_status["ready_for"] == "human"


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


class TestExtractLastRoundWithProjectDir:
    """extract_last_round must work with JSONL cycles outside cwd."""

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("textual"),
        reason="textual not installed"
    )
    def test_jsonl_outside_cwd(self, tmp_path, monkeypatch):
        from ai_handoff.tui.handoff_reader import extract_last_round, find_cycle_doc

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
