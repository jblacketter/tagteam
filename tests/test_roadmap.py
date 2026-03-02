"""Tests for ai_handoff.roadmap and related state/watcher changes."""

import json
import textwrap
from pathlib import Path

import pytest

from ai_handoff.roadmap import (
    RoadmapPhase,
    _slugify,
    parse_roadmap,
    get_incomplete_phases,
    build_queue,
    roadmap_command,
)
from ai_handoff.state import (
    VALID_RUN_MODES,
    format_state,
    read_state,
    update_state,
    write_state,
    _state_set,
)
from ai_handoff.watcher import _try_roadmap_advance


# ── Helpers ──────────────────────────────────────────────────────


def _write_roadmap(tmp_path: Path, content: str) -> Path:
    roadmap = tmp_path / "docs" / "roadmap.md"
    roadmap.parent.mkdir(parents=True, exist_ok=True)
    roadmap.write_text(textwrap.dedent(content))
    return roadmap


SAMPLE_ROADMAP = """\
# Project Roadmap

## Phases

### Phase 1: Auth System
- **Status:** Complete
- **Description:** Authentication

### Phase 2: API Gateway
- **Status:** In Progress
- **Description:** API layer

### Phase 3: Dashboard
- **Status:** Not Started
- **Description:** Dashboard UI

### Phase 4: CI Integration
- **Status:** Not Started
- **Description:** CI pipeline
"""


# ── slugify ──────────────────────────────────────────────────────


class TestSlugify:
    def test_basic(self):
        assert _slugify("Auth System") == "auth-system"

    def test_special_chars(self):
        assert _slugify("CI/CD & Deploy!") == "cicd-deploy"

    def test_extra_spaces(self):
        assert _slugify("  Extra   Spaces  ") == "extra-spaces"

    def test_already_slug(self):
        assert _slugify("already-a-slug") == "already-a-slug"


# ── parse_roadmap ────────────────────────────────────────────────


class TestParseRoadmap:
    def test_parses_all_phases(self, tmp_path):
        roadmap = _write_roadmap(tmp_path, SAMPLE_ROADMAP)
        phases = parse_roadmap(roadmap)

        assert len(phases) == 4
        assert phases[0] == RoadmapPhase(
            slug="auth-system", name="Auth System", status="Complete"
        )
        assert phases[1].slug == "api-gateway"
        assert phases[1].status == "In Progress"
        assert phases[3].slug == "ci-integration"

    def test_missing_file_raises(self, tmp_path):
        missing = tmp_path / "docs" / "roadmap.md"
        with pytest.raises(FileNotFoundError, match="not found"):
            parse_roadmap(missing)

    def test_no_headings_raises(self, tmp_path):
        roadmap = _write_roadmap(tmp_path, "# Just a title\nNo phases here.\n")
        with pytest.raises(ValueError, match="No phases found"):
            parse_roadmap(roadmap)

    def test_missing_status_defaults_to_unknown(self, tmp_path):
        content = """\
        # Roadmap

        ### Phase 1: No Status Phase
        - **Description:** Has no status line
        """
        roadmap = _write_roadmap(tmp_path, content)
        phases = parse_roadmap(roadmap)

        assert len(phases) == 1
        assert phases[0].status == "Unknown"

    def test_preserves_order(self, tmp_path):
        roadmap = _write_roadmap(tmp_path, SAMPLE_ROADMAP)
        phases = parse_roadmap(roadmap)
        names = [p.name for p in phases]
        assert names == ["Auth System", "API Gateway", "Dashboard", "CI Integration"]


# ── get_incomplete_phases ────────────────────────────────────────


class TestGetIncompletePhases:
    def test_filters_complete(self, tmp_path):
        roadmap = _write_roadmap(tmp_path, SAMPLE_ROADMAP)
        incomplete = get_incomplete_phases(roadmap)

        slugs = [p.slug for p in incomplete]
        assert "auth-system" not in slugs
        assert "api-gateway" in slugs
        assert "dashboard" in slugs
        assert "ci-integration" in slugs

    def test_all_complete_raises(self, tmp_path):
        content = """\
        # Roadmap

        ### Phase 1: Done Thing
        - **Status:** Complete

        ### Phase 2: Also Done
        - **Status:** Complete
        """
        roadmap = _write_roadmap(tmp_path, content)
        with pytest.raises(ValueError, match="All roadmap phases are complete"):
            get_incomplete_phases(roadmap)


# ── build_queue ──────────────────────────────────────────────────


class TestBuildQueue:
    def test_full_queue(self, tmp_path):
        roadmap = _write_roadmap(tmp_path, SAMPLE_ROADMAP)
        queue = build_queue(roadmap)
        assert queue == ["api-gateway", "dashboard", "ci-integration"]

    def test_with_start_phase(self, tmp_path):
        roadmap = _write_roadmap(tmp_path, SAMPLE_ROADMAP)
        queue = build_queue(roadmap, start_phase="dashboard")
        assert queue == ["dashboard", "ci-integration"]

    def test_start_phase_not_found(self, tmp_path):
        roadmap = _write_roadmap(tmp_path, SAMPLE_ROADMAP)
        with pytest.raises(ValueError, match="not found"):
            build_queue(roadmap, start_phase="nonexistent")

    def test_start_phase_already_complete(self, tmp_path):
        roadmap = _write_roadmap(tmp_path, SAMPLE_ROADMAP)
        with pytest.raises(ValueError, match="already complete"):
            build_queue(roadmap, start_phase="auth-system")


# ── State CLI flags ──────────────────────────────────────────────


class TestStateCLIFlags:
    def test_valid_run_modes(self):
        assert "single-phase" in VALID_RUN_MODES
        assert "full-roadmap" in VALID_RUN_MODES

    def test_set_run_mode(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Create initial state
        write_state({"turn": "lead", "status": "ready"}, str(tmp_path))

        result = _state_set(["--run-mode", "full-roadmap"])
        assert result == 0

        state = read_state(str(tmp_path))
        assert state["run_mode"] == "full-roadmap"

    def test_invalid_run_mode(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        write_state({"turn": "lead", "status": "ready"}, str(tmp_path))

        result = _state_set(["--run-mode", "invalid"])
        assert result == 1
        assert "Invalid run_mode" in capsys.readouterr().out

    def test_set_roadmap_queue(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_state({"turn": "lead", "status": "ready"}, str(tmp_path))

        result = _state_set([
            "--roadmap-queue", "phase-a,phase-b,phase-c",
            "--roadmap-index", "0",
        ])
        assert result == 0

        state = read_state(str(tmp_path))
        assert state["roadmap"]["queue"] == ["phase-a", "phase-b", "phase-c"]
        assert state["roadmap"]["current_index"] == 0

    def test_set_roadmap_pause_reason(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_state({
            "turn": "lead",
            "status": "escalated",
            "roadmap": {
                "queue": ["a"],
                "current_index": 0,
                "completed": [],
                "pause_reason": None,
            },
        }, str(tmp_path))

        result = _state_set([
            "--roadmap-pause-reason", "needs-human: clarification needed",
        ])
        assert result == 0

        state = read_state(str(tmp_path))
        assert state["roadmap"]["pause_reason"] == "needs-human: clarification needed"


# ── format_state ─────────────────────────────────────────────────


class TestFormatState:
    def test_single_phase_mode(self):
        state = {"turn": "lead", "status": "ready", "phase": "auth"}
        output = format_state(state)
        assert "Mode:       single-phase" in output

    def test_roadmap_mode_shows_progress(self):
        state = {
            "turn": "reviewer",
            "status": "ready",
            "phase": "dashboard",
            "run_mode": "full-roadmap",
            "roadmap": {
                "queue": ["api-gateway", "dashboard", "ci-integration"],
                "current_index": 1,
                "completed": ["api-gateway"],
                "pause_reason": None,
            },
        }
        output = format_state(state)
        assert "Mode:       full-roadmap" in output
        assert "Progress:   1/3" in output
        assert "Next phase: ci-integration" in output

    def test_roadmap_mode_last_phase(self):
        state = {
            "turn": "reviewer",
            "status": "ready",
            "phase": "ci-integration",
            "run_mode": "full-roadmap",
            "roadmap": {
                "queue": ["api-gateway", "dashboard", "ci-integration"],
                "current_index": 2,
                "completed": ["api-gateway", "dashboard"],
                "pause_reason": None,
            },
        }
        output = format_state(state)
        assert "Next phase: (last)" in output

    def test_roadmap_mode_shows_pause(self):
        state = {
            "turn": "lead",
            "status": "escalated",
            "phase": "dashboard",
            "run_mode": "full-roadmap",
            "roadmap": {
                "queue": ["dashboard"],
                "current_index": 0,
                "completed": [],
                "pause_reason": "needs-human: design review required",
            },
        }
        output = format_state(state)
        assert "Paused:     needs-human: design review required" in output


# ── _try_roadmap_advance ─────────────────────────────────────────


class TestRoadmapAdvance:
    def test_noop_single_phase(self, tmp_path):
        write_state({
            "turn": "reviewer",
            "status": "done",
            "result": "approved",
            "type": "impl",
        }, str(tmp_path))
        state = read_state(str(tmp_path))
        assert _try_roadmap_advance(state, str(tmp_path)) is None

    def test_plan_approved_hands_to_lead(self, tmp_path):
        write_state({
            "turn": "reviewer",
            "status": "done",
            "result": "approved",
            "type": "plan",
            "phase": "api-gateway",
            "run_mode": "full-roadmap",
            "roadmap": {
                "queue": ["api-gateway", "dashboard"],
                "current_index": 0,
                "completed": [],
                "pause_reason": None,
            },
        }, str(tmp_path))
        state = read_state(str(tmp_path))
        new_state = _try_roadmap_advance(state, str(tmp_path))

        assert new_state is not None
        # Lead must implement and run `/handoff start [phase] impl`
        assert new_state["turn"] == "lead"
        assert new_state["status"] == "ready"
        assert new_state["result"] is None
        assert new_state["command"] == "/handoff start api-gateway impl"

    def test_impl_approved_hands_to_lead_for_next_phase(self, tmp_path):
        write_state({
            "turn": "reviewer",
            "status": "done",
            "result": "approved",
            "type": "impl",
            "phase": "api-gateway",
            "run_mode": "full-roadmap",
            "roadmap": {
                "queue": ["api-gateway", "dashboard", "ci-integration"],
                "current_index": 0,
                "completed": [],
                "pause_reason": None,
            },
        }, str(tmp_path))
        state = read_state(str(tmp_path))
        new_state = _try_roadmap_advance(state, str(tmp_path))

        assert new_state is not None
        assert new_state["phase"] == "dashboard"
        assert new_state["type"] == "plan"
        assert new_state["round"] == 1
        # Lead must create plan/cycle docs via `/handoff start [phase]`
        assert new_state["turn"] == "lead"
        assert new_state["command"] == "/handoff start dashboard"
        assert new_state["roadmap"]["current_index"] == 1
        assert "api-gateway" in new_state["roadmap"]["completed"]

    def test_impl_approved_last_phase_completes(self, tmp_path):
        write_state({
            "turn": "reviewer",
            "status": "done",
            "result": "approved",
            "type": "impl",
            "phase": "ci-integration",
            "run_mode": "full-roadmap",
            "roadmap": {
                "queue": ["api-gateway", "ci-integration"],
                "current_index": 1,
                "completed": ["api-gateway"],
                "pause_reason": None,
            },
        }, str(tmp_path))
        state = read_state(str(tmp_path))
        new_state = _try_roadmap_advance(state, str(tmp_path))

        assert new_state is not None
        assert new_state["status"] == "done"
        assert new_state["result"] == "roadmap-complete"
        assert "ci-integration" in new_state["roadmap"]["completed"]

    def test_noop_on_non_approved_result(self, tmp_path):
        write_state({
            "turn": "reviewer",
            "status": "done",
            "result": "rejected",
            "type": "plan",
            "run_mode": "full-roadmap",
            "roadmap": {
                "queue": ["a"],
                "current_index": 0,
                "completed": [],
                "pause_reason": None,
            },
        }, str(tmp_path))
        state = read_state(str(tmp_path))
        assert _try_roadmap_advance(state, str(tmp_path)) is None


# ── roadmap CLI command ─────────────────────────────────────────


class TestRoadmapCommand:
    def test_queue_prints_slugs(self, tmp_path, monkeypatch, capsys):
        _write_roadmap(tmp_path, SAMPLE_ROADMAP)
        monkeypatch.chdir(tmp_path)

        result = roadmap_command(["queue"])
        assert result == 0
        assert capsys.readouterr().out.strip() == "api-gateway,dashboard,ci-integration"

    def test_queue_with_start_phase(self, tmp_path, monkeypatch, capsys):
        _write_roadmap(tmp_path, SAMPLE_ROADMAP)
        monkeypatch.chdir(tmp_path)

        result = roadmap_command(["queue", "dashboard"])
        assert result == 0
        assert capsys.readouterr().out.strip() == "dashboard,ci-integration"

    def test_queue_missing_roadmap(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)

        result = roadmap_command(["queue"])
        assert result == 1
        assert "not found" in capsys.readouterr().out

    def test_phases_lists_all(self, tmp_path, monkeypatch, capsys):
        _write_roadmap(tmp_path, SAMPLE_ROADMAP)
        monkeypatch.chdir(tmp_path)

        result = roadmap_command(["phases"])
        assert result == 0
        output = capsys.readouterr().out
        assert "auth-system\tComplete\tAuth System" in output
        assert "api-gateway\tIn Progress\tAPI Gateway" in output
        assert "ci-integration\tNot Started\tCI Integration" in output

    def test_no_args_shows_usage(self, capsys):
        result = roadmap_command([])
        assert result == 1
        assert "Usage" in capsys.readouterr().out

    def test_unknown_subcommand(self, capsys):
        result = roadmap_command(["foobar"])
        assert result == 1
        assert "Unknown" in capsys.readouterr().out
