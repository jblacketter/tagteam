"""Tests for state-layer drift healing (sync command + replace=True).

These cover the handoff-drift bug class where per-cycle status and
top-level handoff-state.json disagreed: the lead agent would see
`turn: reviewer` in top-level while the cycle said `ready_for: lead`,
and have no way to resolve the mismatch without hand-editing files.
"""

import json
from pathlib import Path

import pytest

from tagteam.cycle import add_round, init_cycle
from tagteam.state import (
    read_state, state_command, update_state, write_state,
)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "docs" / "handoffs").mkdir(parents=True)
    return str(tmp_path)


class TestUpdateStateReplace:
    def test_replace_drops_fields_not_in_updates(self, project):
        write_state({
            "phase": "old",
            "type": "plan",
            "turn": "lead",
            "status": "done",
            "result": "approved",
            "stale_field": "should-be-gone",
        }, project)
        update_state({
            "phase": "new",
            "type": "impl",
            "turn": "reviewer",
            "status": "ready",
        }, project, replace=True)
        state = read_state(project)
        assert state["phase"] == "new"
        assert state["type"] == "impl"
        assert state["turn"] == "reviewer"
        assert state["status"] == "ready"
        # Stale fields from previous state must not carry forward
        assert "result" not in state
        assert "stale_field" not in state

    def test_replace_preserves_history(self, project):
        """History is metadata that must survive replace=True."""
        write_state({"phase": "a", "turn": "lead", "status": "ready"}, project)
        update_state({"phase": "a", "turn": "reviewer", "status": "ready"}, project)
        update_state({"phase": "a", "turn": "reviewer", "status": "ready"},
                     project, replace=True)
        state = read_state(project)
        assert "history" in state
        assert len(state["history"]) >= 1

    def test_replace_bumps_seq(self, project):
        write_state({"phase": "a", "turn": "lead", "status": "ready"}, project)
        seq_before = read_state(project).get("seq", 0)
        update_state({"phase": "a", "turn": "reviewer", "status": "ready"},
                     project, replace=True)
        seq_after = read_state(project).get("seq", 0)
        assert seq_after == seq_before + 1


class TestStateSync:
    """`tagteam state sync` heals drift between per-cycle status and top-level."""

    def test_sync_heals_stale_turn(self, project, monkeypatch, capsys):
        """If top-level disagrees with cycle, sync rewrites from cycle."""
        monkeypatch.chdir(project)
        init_cycle("phase-x", "plan", "Claude", "Codex", "draft", project)
        add_round("phase-x", "plan", "reviewer", "REQUEST_CHANGES", 1,
                  "Fix.", project)

        # Corrupt top-level to simulate drift: claim it's reviewer's turn,
        # even though the cycle ended round 1 with ready_for=lead.
        state = read_state(project)
        state["turn"] = "reviewer"
        state["status"] = "ready"
        write_state(state, project)

        # Per-cycle status (the truth)
        cycle_path = Path(project) / "docs" / "handoffs" / "phase-x_plan_status.json"
        cycle = json.loads(cycle_path.read_text())
        assert cycle["ready_for"] == "lead"

        # Sync
        rc = state_command(["sync"])
        assert rc == 0

        healed = read_state(project)
        assert healed["turn"] == "lead"
        assert healed["status"] == "ready"
        assert healed["phase"] == "phase-x"
        assert healed["round"] == 1

    def test_sync_targets_most_recent_cycle_by_default(self, project, monkeypatch):
        """With multiple cycles, sync picks the most recently updated one."""
        import time
        monkeypatch.chdir(project)

        init_cycle("older", "plan", "A", "B", "x", project)
        time.sleep(0.02)  # ensure distinguishable mtime
        init_cycle("newer", "impl", "A", "B", "y", project)

        # Drift both top-level fields so sync has something to fix
        state = read_state(project)
        state["phase"] = "WRONG"
        write_state(state, project)

        state_command(["sync"])

        healed = read_state(project)
        assert healed["phase"] == "newer"
        assert healed["type"] == "impl"

    def test_sync_with_explicit_phase_and_type(self, project, monkeypatch):
        import time
        monkeypatch.chdir(project)
        init_cycle("a", "plan", "A", "B", "x", project)
        time.sleep(0.02)
        init_cycle("b", "impl", "A", "B", "y", project)

        state_command(["sync", "--phase", "a", "--type", "plan"])

        healed = read_state(project)
        assert healed["phase"] == "a"
        assert healed["type"] == "plan"

    def test_sync_rejects_partial_targeting(self, project, monkeypatch, capsys):
        monkeypatch.chdir(project)
        init_cycle("a", "plan", "A", "B", "x", project)
        rc = state_command(["sync", "--phase", "a"])
        assert rc == 1
        assert "together" in capsys.readouterr().out

    def test_sync_clears_stale_result(self, project, monkeypatch):
        """Synced state must not retain result from a previous completed cycle."""
        monkeypatch.chdir(project)

        # Write a drifted top-level with stale result=approved
        write_state({
            "phase": "new-phase",
            "type": "plan",
            "turn": "lead",
            "status": "ready",
            "result": "approved",  # stale from old cycle
            "round": 1,
            "run_mode": "single-phase",
        }, project)

        # Start a fresh cycle and drive it to lead's turn
        init_cycle("new-phase", "plan", "A", "B", "draft", project)
        add_round("new-phase", "plan", "reviewer", "REQUEST_CHANGES", 1,
                  "Fix.", project)

        # Sync should rewrite authoritatively — stale result gone
        state_command(["sync"])
        healed = read_state(project)
        assert healed.get("result") is None
