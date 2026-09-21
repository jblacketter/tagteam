"""Tests for tagteam.roadmap and related state/watcher changes."""

import json
import textwrap
from pathlib import Path

import pytest

from tagteam.roadmap import (
    RoadmapPhase,
    _resolve_ref,
    _slugify,
    parse_roadmap,
    get_incomplete_phases,
    build_queue,
    is_terminal_status,
    roadmap_command,
    unparsed_phase_headings,
    validate_graph,
    validate_identities,
)
from tagteam.state import (
    VALID_RUN_MODES,
    format_state,
    read_state,
    update_state,
    write_state,
    _state_set,
)
from tagteam.watcher import _try_roadmap_advance


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
            slug="auth-system", name="Auth System", status="Complete", number=1
        )
        assert phases[0].depends_on == []
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
        assert new_state["command"] == "/tagteam:handoff start api-gateway impl"

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
        assert new_state["command"] == "/tagteam:handoff start dashboard"
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


# ═══════════════════════════════════════════════════════════════
# Phase 40: roadmap as a DAG
# ═══════════════════════════════════════════════════════════════

from tagteam.roadmap import (  # noqa: E402
    RoadmapGraphError,
    validate_identities,
    validate_graph,
    check_graph,
    graph_problems,
    dep_satisfied,
    unmet_dependencies,
    ready_phases,
    blocked_phases,
    topological_queue,
    build_queue_with_notes,
    graph_text,
    has_edges,
)
from tagteam.watcher import roadmap_resume  # noqa: E402


DAG_ROADMAP = """\
# Roadmap

### Phase 1: Alpha
- **Status:** Complete

### Phase 2: Beta
- **Status:** Not Started
- **Depends on:** Phase 1

### Phase 3: Gamma
- **Status:** Not Started
- **Depends on:** beta

### Phase 4: Delta
- **Status:** Not Started
- **Depends on:** `Beta`, Gamma

### Phase 5: Epsilon
- **Status:** Not Started
"""

DIAMOND_ROADMAP = """\
### Phase 1: A
- **Status:** Not Started
### Phase 2: B
- **Status:** Not Started
- **Depends on:** a
### Phase 3: C
- **Status:** Not Started
- **Depends on:** a
### Phase 4: D
- **Status:** Not Started
- **Depends on:** b, c
"""


def _phases(tmp_path, text):
    return parse_roadmap(_write_roadmap(tmp_path, text))


class TestDependsOnParsing:
    def test_no_line_means_no_edges_and_number_is_set(self, tmp_path):
        phases = _phases(tmp_path, SAMPLE_ROADMAP)
        assert all(p.depends_on == [] for p in phases)
        assert [p.number for p in phases] == [1, 2, 3, 4]
        assert not has_edges(phases)

    def test_all_reference_forms_resolve_to_slugs(self, tmp_path):
        phases = _phases(tmp_path, DAG_ROADMAP)
        by = {p.slug: p for p in phases}
        assert by["beta"].depends_on == ["alpha"]          # Phase N
        assert by["gamma"].depends_on == ["beta"]          # slug
        assert by["delta"].depends_on == ["beta", "gamma"]  # `Beta` (name in backticks), name
        assert by["epsilon"].depends_on == []

    def test_multiple_lines_and_separators_merge_and_dedupe(self, tmp_path):
        text = DAG_ROADMAP + """
### Phase 6: Zeta
- **Status:** Not Started
- **Depends on:** alpha; beta, alpha
- **Depends on**: phase-3, Phase 3, phase-4-delta
"""
        by = {p.slug: p for p in _phases(tmp_path, text)}
        assert by["zeta"].depends_on == ["alpha", "beta", "gamma", "delta"]

    def test_none_words_ignored(self, tmp_path):
        text = "### Phase 1: One\n- **Status:** Not Started\n- **Depends on:** none\n" \
               "### Phase 2: Two\n- **Status:** Not Started\n- **Depends on:** —\n"
        assert all(p.depends_on == [] for p in _phases(tmp_path, text))

    def test_unknown_reference_kept_verbatim(self, tmp_path):
        text = "### Phase 1: One\n- **Status:** Not Started\n- **Depends on:** nope\n"
        (p,) = _phases(tmp_path, text)
        assert p.depends_on == ["nope"]

    def test_phases_column_only_with_edges(self, tmp_path, monkeypatch, capsys):
        _write_roadmap(tmp_path, SAMPLE_ROADMAP)
        monkeypatch.chdir(tmp_path)
        assert roadmap_command(["phases"]) == 0
        out = capsys.readouterr().out
        assert out.splitlines()[0].count("\t") == 2
        _write_roadmap(tmp_path, DAG_ROADMAP)
        assert roadmap_command(["phases"]) == 0
        out = capsys.readouterr().out
        assert "beta\tNot Started\tBeta\talpha" in out
        assert "delta\tNot Started\tDelta\tbeta,gamma" in out


class TestIdentityValidation:
    def test_clean(self):
        assert validate_identities(DAG_ROADMAP) == []
        assert validate_identities(SAMPLE_ROADMAP) == []

    def test_duplicate_numbers_and_slugs_all_listed(self):
        text = ("### Phase 1: One\n- **Status:** Not Started\n"
                "### Phase 1: Two\n- **Status:** Not Started\n"
                "### Phase 3: Foo Bar\n- **Status:** Not Started\n"
                "### Phase 4: foo   bar!\n- **Status:** Not Started\n")
        problems = validate_identities(text)
        assert any("duplicate phase number 1" in p for p in problems)
        assert any("duplicate slug 'foo-bar'" in p for p in problems)
        assert len(problems) == 2

    def test_empty_heading_both_spellings(self):
        bare = "### Phase 1:\n- **Status:** Not Started\n### Phase 2: Real\n- **Status:** Not Started\n"
        ws = "### Phase 1:    \n- **Status:** Not Started\n### Phase 2: Real\n- **Status:** Not Started\n"
        for text in (bare, ws):
            problems = validate_identities(text)
            assert problems == ["Phase 1: empty name"], (text, problems)

    def test_refused_by_check_queue_ready_graph(self, tmp_path, monkeypatch, capsys):
        text = ("### Phase 1: Same\n- **Status:** Not Started\n"
                "### Phase 2: Same\n- **Status:** Not Started\n")
        _write_roadmap(tmp_path, text)
        monkeypatch.chdir(tmp_path)
        assert roadmap_command(["check"]) == 1
        out = capsys.readouterr().out
        assert "roadmap invalid" in out and "duplicate slug 'same'" in out
        for sub in (["queue"], ["ready"], ["graph"]):
            assert roadmap_command(sub) == 1
            assert "duplicate slug" in capsys.readouterr().out
        with pytest.raises(RoadmapGraphError):
            build_queue(tmp_path / "docs" / "roadmap.md")

    def test_this_repo_roadmap_is_well_formed(self):
        repo_roadmap = Path(__file__).resolve().parents[1] / "docs" / "roadmap.md"
        if not repo_roadmap.exists():
            pytest.skip("repo roadmap not present")
        phases, problems = graph_problems(repo_roadmap)
        assert problems == []
        assert len(phases) >= 40


class TestGraphValidation:
    def test_unknown_self_cycle_all_listed(self, tmp_path):
        text = ("### Phase 1: A\n- **Status:** Not Started\n- **Depends on:** b, ghost, a\n"
                "### Phase 2: B\n- **Status:** Not Started\n- **Depends on:** a\n")
        phases = _phases(tmp_path, text)
        problems = validate_graph(phases)
        assert "a: unknown dependency 'ghost' (line 3)" in problems        # Phase 66: parsed phases know their lines
        assert "a: depends on itself (line 3)" in problems
        assert any(p.startswith("cycle: ") and "a" in p and "b" in p for p in problems)
        with pytest.raises(RoadmapGraphError) as ei:
            check_graph(tmp_path / "docs" / "roadmap.md")
        assert len(ei.value.problems) == 3

    def test_check_ok(self, tmp_path, monkeypatch, capsys):
        _write_roadmap(tmp_path, DAG_ROADMAP)
        monkeypatch.chdir(tmp_path)
        assert roadmap_command(["check"]) == 0
        assert "roadmap ok: 5 phase(s), 4 dependency edge(s)" in capsys.readouterr().out


class TestSatisfactionRule:
    def test_terminal_on_disk_or_completed(self, tmp_path):
        phases = _phases(tmp_path, DAG_ROADMAP)
        by = {p.slug: p for p in phases}
        assert dep_satisfied("alpha", by, None)                 # terminal on disk
        assert not dep_satisfied("beta", by, None)
        assert dep_satisfied("beta", by, ["beta"])              # active run
        assert dep_satisfied("beta", by, ["phase-2-beta"])      # normalized
        assert unmet_dependencies(by["delta"], by, ["beta"]) == ["gamma"]

    def test_ready_and_blocked(self, tmp_path):
        phases = _phases(tmp_path, DAG_ROADMAP)
        assert [p.slug for p in ready_phases(phases)] == ["beta", "epsilon"]
        assert [(p.slug, u) for p, u in blocked_phases(phases)] == [
            ("gamma", ["beta"]), ("delta", ["beta", "gamma"])]
        # same-run approval unblocks without a roadmap edit
        assert [p.slug for p in ready_phases(phases, ["beta"])] == ["gamma", "epsilon"]
        assert [p.slug for p in ready_phases(phases, ["beta", "gamma"])] == ["delta", "epsilon"]

    def test_ready_cli_uses_active_run_unless_roadmap_only(self, tmp_path, monkeypatch, capsys):
        _write_roadmap(tmp_path, DAG_ROADMAP)
        (tmp_path / "tagteam.yaml").write_text("agents: {}\n")
        monkeypatch.chdir(tmp_path)
        import tagteam.state as st
        monkeypatch.setattr(st, "_cached_project_root", None, raising=False)
        write_state({"run_mode": "full-roadmap", "status": "ready", "turn": "lead",
                     "roadmap": {"queue": ["beta", "gamma"], "current_index": 1,
                                 "completed": ["beta"], "pause_reason": None}}, str(tmp_path))
        assert roadmap_command(["ready"]) == 0
        cap = capsys.readouterr()
        assert cap.out.splitlines() == ["gamma\tNot Started\tGamma", "epsilon\tNot Started\tEpsilon"]
        assert "(+ 1 phase(s) completed in the active run: beta)" in cap.err
        assert roadmap_command(["ready", "--roadmap-only"]) == 0
        cap = capsys.readouterr()
        assert cap.out.splitlines()[0].startswith("beta\t")
        assert roadmap_command(["ready", "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert [r["slug"] for r in data["ready"]] == ["gamma", "epsilon"]
        assert data["blocked"][0]["unmet"] == ["gamma"]
        assert data["completed_in_run"] == ["beta"]


class TestTopologicalQueue:
    def test_edge_free_equals_flat_list_and_repo_roadmap(self, tmp_path):
        phases = _phases(tmp_path, SAMPLE_ROADMAP)
        assert topological_queue(phases) == (["api-gateway", "dashboard", "ci-integration"], [])
        assert topological_queue(phases, start="dashboard") == (["dashboard", "ci-integration"], [])
        repo_roadmap = Path(__file__).resolve().parents[1] / "docs" / "roadmap.md"
        if repo_roadmap.exists():
            all_phases = parse_roadmap(repo_roadmap)
            flat = [p.slug for p in all_phases if not is_terminal(p.status)]
            # The repo roadmap is live data: when every phase is complete
            # there is nothing to queue (topological_queue raises), so only
            # compare when something is still actionable.
            if flat and not has_edges(all_phases):
                queue, _ = topological_queue(all_phases)
                assert queue == flat

    def test_diamond_and_ties_are_roadmap_order(self, tmp_path):
        phases = _phases(tmp_path, DIAMOND_ROADMAP)
        assert topological_queue(phases)[0] == ["a", "b", "c", "d"]
        # a dependency listed later in the file is emitted first
        text = ("### Phase 1: Late\n- **Status:** Not Started\n- **Depends on:** early\n"
                "### Phase 2: Early\n- **Status:** Not Started\n"
                "### Phase 3: Other\n- **Status:** Not Started\n")
        assert topological_queue(_phases(tmp_path, text))[0] == ["early", "late", "other"]

    def test_start_pulls_in_unmet_ancestors_and_drops_unneeded(self, tmp_path):
        phases = _phases(tmp_path, DAG_ROADMAP)
        # start at delta: beta and gamma are unmet ancestors → pulled in first
        queue, pulled = topological_queue(phases, start="delta")
        assert queue == ["beta", "gamma", "delta", "epsilon"]
        assert pulled == ["beta", "gamma"]
        # start at epsilon: nothing after it needs beta/gamma/delta → dropped
        assert topological_queue(phases, start="epsilon") == (["epsilon"], [])
        # completed in the active run is not pulled in
        queue, pulled = topological_queue(phases, start="delta", completed=["beta"])
        assert queue == ["gamma", "delta", "epsilon"] and pulled == ["gamma"]

    def test_queue_cli_note_on_stderr(self, tmp_path, monkeypatch, capsys):
        _write_roadmap(tmp_path, DAG_ROADMAP)
        monkeypatch.chdir(tmp_path)
        assert roadmap_command(["queue", "delta"]) == 0
        cap = capsys.readouterr()
        assert cap.out.strip() == "beta,gamma,delta,epsilon"
        assert "pulled in 2 dependency ancestor(s) ahead of 'delta': beta, gamma" in cap.err
        assert roadmap_command(["queue"]) == 0
        cap = capsys.readouterr()
        assert cap.out.strip() == "beta,gamma,delta,epsilon" and cap.err == ""

    def test_start_errors_keep_wording(self, tmp_path):
        rp = _write_roadmap(tmp_path, DAG_ROADMAP)
        with pytest.raises(ValueError, match="already complete"):
            build_queue(rp, start_phase="alpha")
        with pytest.raises(ValueError, match="not found in"):
            build_queue(rp, start_phase="nope")
        assert build_queue_with_notes(rp, "delta") == (["beta", "gamma", "delta", "epsilon"], ["beta", "gamma"])


def is_terminal(status):
    from tagteam.roadmap import is_terminal_status
    return is_terminal_status(status)


class TestGraphText:
    def test_tree_marks(self, tmp_path):
        phases = _phases(tmp_path, DAG_ROADMAP)
        text = graph_text(phases, completed=["beta"])
        assert "✓ alpha" in text and "✓* beta  ← alpha" in text
        assert "▶ gamma  ← beta" in text and "⏸ delta  ← beta, gamma" in text and "▶ epsilon" in text

    def test_mermaid(self, tmp_path, monkeypatch, capsys):
        _write_roadmap(tmp_path, DAG_ROADMAP)
        monkeypatch.chdir(tmp_path)
        assert roadmap_command(["graph", "--mermaid"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("flowchart LR") and "p_beta --> p_gamma" in out and 'p_alpha["✓ Alpha"]' in out


# ── dynamic advance ─────────────────────────────────────────────


def _roadmap_state(tmp_path, *, phase, queue, index, completed, type_="impl",
                   result="approved", status="done"):
    write_state({
        "turn": "reviewer", "status": status, "result": result, "type": type_,
        "phase": phase, "run_mode": "full-roadmap",
        "roadmap": {"queue": queue, "current_index": index,
                    "completed": completed, "pause_reason": None},
    }, str(tmp_path))
    return read_state(str(tmp_path))


class TestDynamicAdvance:
    def test_same_run_approval_unblocks_dependent_without_roadmap_edit(self, tmp_path):
        _write_roadmap(tmp_path, DAG_ROADMAP)
        state = _roadmap_state(tmp_path, phase="beta", queue=["beta", "gamma", "delta", "epsilon"],
                               index=0, completed=[])
        new = _try_roadmap_advance(state, str(tmp_path))
        assert new["phase"] == "gamma" and new["roadmap"]["current_index"] == 1
        assert new["command"] == "/tagteam:handoff start gamma"
        assert new["roadmap"]["completed"] == ["beta"]
        assert new["roadmap"]["pause_reason"] is None

    def test_diamond_middle_completed_externally(self, tmp_path):
        text = DIAMOND_ROADMAP.replace("### Phase 2: B\n- **Status:** Not Started",
                                       "### Phase 2: B\n- **Status:** ✅ Complete (merged from a worktree)")
        _write_roadmap(tmp_path, text)
        state = _roadmap_state(tmp_path, phase="a", queue=["a", "b", "c", "d"], index=0, completed=[])
        new = _try_roadmap_advance(state, str(tmp_path))
        assert new["phase"] == "c" and new["roadmap"]["current_index"] == 2
        assert new["roadmap"]["completed"] == ["a"]           # b is NOT recorded as completed-in-run
        # after c: d is ready (b terminal on disk, c in completed)
        state = _roadmap_state(tmp_path, phase="c", queue=["a", "b", "c", "d"], index=2, completed=["a"])
        new = _try_roadmap_advance(state, str(tmp_path))
        assert new["phase"] == "d" and new["roadmap"]["current_index"] == 3
        # after d: complete even though b never ran here
        state = _roadmap_state(tmp_path, phase="d", queue=["a", "b", "c", "d"], index=3, completed=["a", "c"])
        new = _try_roadmap_advance(state, str(tmp_path))
        assert new["result"] == "roadmap-complete"

    def test_mixed_case_blocked_entry_is_reconsidered_and_index_moves_back(self, tmp_path):
        # A approved, B blocked (dep X runs elsewhere), C ready → C; X done → B; then complete.
        text = ("### Phase 1: X\n- **Status:** Not Started\n"
                "### Phase 2: A\n- **Status:** Not Started\n"
                "### Phase 3: B\n- **Status:** Not Started\n- **Depends on:** x\n"
                "### Phase 4: C\n- **Status:** Not Started\n")
        rp = _write_roadmap(tmp_path, text)
        queue = ["a", "b", "c"]  # x is being run in another worktree
        state = _roadmap_state(tmp_path, phase="a", queue=queue, index=0, completed=[])
        new = _try_roadmap_advance(state, str(tmp_path))
        assert new["phase"] == "c" and new["roadmap"]["current_index"] == 2
        # C approved while X still not merged → paused, B not started
        state = _roadmap_state(tmp_path, phase="c", queue=queue, index=2, completed=["a"])
        new = _try_roadmap_advance(state, str(tmp_path))
        assert new["status"] == "escalated"
        assert new["roadmap"]["pause_reason"] == "blocked: b depends on x"
        assert new["roadmap"]["current_index"] == 2
        assert new["command"] == "tagteam roadmap resume"
        assert new["roadmap"]["completed"] == ["a", "c"]
        # X merged (terminal on disk) → resume selects B with a LOWER index
        rp.write_text(rp.read_text().replace("### Phase 1: X\n- **Status:** Not Started",
                                             "### Phase 1: X\n- **Status:** Complete"))
        assert roadmap_resume(str(tmp_path)) == 0
        st = read_state(str(tmp_path))
        assert st["phase"] == "b" and st["roadmap"]["current_index"] == 1
        assert st["status"] == "ready" and st["turn"] == "lead" and st["roadmap"]["pause_reason"] is None
        # B approved → complete without re-running A or C
        state = _roadmap_state(tmp_path, phase="b", queue=queue, index=1, completed=["a", "c"])
        new = _try_roadmap_advance(state, str(tmp_path))
        assert new["result"] == "roadmap-complete"
        assert sorted(new["roadmap"]["completed"]) == ["a", "b", "c"]

    def test_all_blocked_pause_then_resume_still_paused_then_ok(self, tmp_path):
        text = ("### Phase 1: X\n- **Status:** Not Started\n"
                "### Phase 2: A\n- **Status:** Not Started\n"
                "### Phase 3: B\n- **Status:** Not Started\n- **Depends on:** x\n")
        rp = _write_roadmap(tmp_path, text)
        state = _roadmap_state(tmp_path, phase="a", queue=["a", "b"], index=0, completed=[])
        new = _try_roadmap_advance(state, str(tmp_path))
        assert new["status"] == "escalated" and new["roadmap"]["pause_reason"].startswith("blocked: b")
        assert roadmap_resume(str(tmp_path)) == 2            # still paused
        assert read_state(str(tmp_path))["roadmap"]["pause_reason"].startswith("blocked:")
        rp.write_text(rp.read_text().replace("X\n- **Status:** Not Started", "X\n- **Status:** Done"))
        assert roadmap_resume(str(tmp_path)) == 0
        assert read_state(str(tmp_path))["phase"] == "b"

    def test_externally_completed_tail_is_roadmap_complete(self, tmp_path):
        text = ("### Phase 1: A\n- **Status:** Not Started\n"
                "### Phase 2: B\n- **Status:** Complete\n")
        _write_roadmap(tmp_path, text)
        state = _roadmap_state(tmp_path, phase="a", queue=["a", "b"], index=0, completed=[])
        new = _try_roadmap_advance(state, str(tmp_path))
        assert new["result"] == "roadmap-complete"

    def test_invalid_roadmap_pauses(self, tmp_path):
        text = ("### Phase 1: A\n- **Status:** Not Started\n"
                "### Phase 2: B\n- **Status:** Not Started\n- **Depends on:** ghost\n")
        _write_roadmap(tmp_path, text)
        state = _roadmap_state(tmp_path, phase="a", queue=["a", "b"], index=0, completed=[])
        new = _try_roadmap_advance(state, str(tmp_path))
        assert new["status"] == "escalated"
        assert new["roadmap"]["pause_reason"] == "roadmap invalid: b: unknown dependency 'ghost' (line 5)"

    def test_missing_roadmap_file_behaves_like_before(self, tmp_path):
        state = _roadmap_state(tmp_path, phase="a", queue=["a", "b"], index=0, completed=[])
        new = _try_roadmap_advance(state, str(tmp_path))
        assert new["phase"] == "b" and new["roadmap"]["current_index"] == 1

    def test_resume_noops(self, tmp_path, capsys):
        assert roadmap_resume(str(tmp_path)) == 1
        write_state({"status": "ready", "turn": "lead", "run_mode": "single-phase"}, str(tmp_path))
        assert roadmap_resume(str(tmp_path)) == 1
        assert "Not in full-roadmap mode" in capsys.readouterr().out
        _roadmap_state(tmp_path, phase="a", queue=["a", "b"], index=0, completed=[], status="ready", result=None)
        assert roadmap_resume(str(tmp_path)) == 1
        assert "not paused" in capsys.readouterr().out

    def test_resume_on_approved_impl_advances(self, tmp_path):
        _write_roadmap(tmp_path, SAMPLE_ROADMAP)
        _roadmap_state(tmp_path, phase="api-gateway", queue=["api-gateway", "dashboard"], index=0, completed=[])
        assert roadmap_resume(str(tmp_path)) == 0
        st = read_state(str(tmp_path))
        assert st["phase"] == "dashboard" and st["roadmap"]["completed"] == ["api-gateway"]


class TestImplRound2Fixes:
    """Impl round 1 findings: stale queue entries, empty normalized slugs."""

    def test_stale_queue_entry_pauses_never_starts(self, tmp_path):
        text = ("### Phase 1: A\n- **Status:** Not Started\n"
                "### Phase 2: B\n- **Status:** Not Started\n")
        _write_roadmap(tmp_path, text)
        # queue recorded when a phase "deleted-phase" still existed
        state = _roadmap_state(tmp_path, phase="a", queue=["a", "deleted-phase", "b"], index=0, completed=[])
        new = _try_roadmap_advance(state, str(tmp_path))
        assert new["status"] == "escalated"
        assert new["roadmap"]["pause_reason"] == \
            "stale queue: deleted-phase not in docs/roadmap.md (removed or renamed?)"
        assert new["phase"] == "a"                      # nothing started
        assert new["roadmap"]["completed"] == ["a"]
        # roadmap resume while still stale → still paused (2); a renamed phase too
        assert roadmap_resume(str(tmp_path)) == 2
        # arbiter fixes the roadmap (adds the phase back) → resume starts it
        (tmp_path / "docs" / "roadmap.md").write_text(
            text + "### Phase 3: Deleted Phase\n- **Status:** Not Started\n")
        assert roadmap_resume(str(tmp_path)) == 0
        st = read_state(str(tmp_path))
        assert st["phase"] == "deleted-phase" and st["roadmap"]["current_index"] == 1

    def test_renamed_queue_entry_pauses(self, tmp_path):
        text = ("### Phase 1: A\n- **Status:** Not Started\n"
                "### Phase 2: B Renamed\n- **Status:** Not Started\n")
        _write_roadmap(tmp_path, text)
        state = _roadmap_state(tmp_path, phase="a", queue=["a", "b"], index=0, completed=[])
        new = _try_roadmap_advance(state, str(tmp_path))
        assert new["status"] == "escalated" and new["roadmap"]["pause_reason"].startswith("stale queue: b ")

    def test_select_next_phase_direct_stale(self, tmp_path):
        from tagteam.watcher import _select_next_phase
        _write_roadmap(tmp_path, "### Phase 1: A\n- **Status:** Not Started\n")
        write_state({"status": "done", "result": "approved", "type": "impl", "phase": "a",
                     "run_mode": "full-roadmap",
                     "roadmap": {"queue": ["deleted-phase"], "current_index": 0,
                                 "completed": [], "pause_reason": None}}, str(tmp_path))
        seq = read_state(str(tmp_path)).get("seq", 0)
        new = _select_next_phase(["deleted-phase"], 0, [], seq, str(tmp_path))
        assert new["status"] == "escalated" and "stale queue: deleted-phase" in new["roadmap"]["pause_reason"]

    def test_missing_roadmap_file_still_falls_back(self, tmp_path):
        state = _roadmap_state(tmp_path, phase="a", queue=["a", "whatever"], index=0, completed=[])
        new = _try_roadmap_advance(state, str(tmp_path))
        assert new["phase"] == "whatever" and new["status"] == "ready"

    def test_empty_normalized_slug_is_identity_error(self, tmp_path, monkeypatch, capsys):
        assert validate_identities("### Phase 1: !!!\n- **Status:** Not Started\n") == \
            ["Phase 1: empty normalized slug ('!!!')"]
        assert validate_identities("### Phase 1: ***\n### Phase 2: (…)\n") == [
            "Phase 1: empty normalized slug ('***')", "Phase 2: empty normalized slug ('(…)')"]
        assert validate_identities("### Phase 1: A-1\n") == []
        text = "### Phase 1: !!!\n- **Status:** Not Started\n### Phase 2: Real\n- **Status:** Not Started\n"
        _write_roadmap(tmp_path, text)
        monkeypatch.chdir(tmp_path)
        for sub in (["check"], ["queue"], ["ready"], ["graph"]):
            assert roadmap_command(sub) == 1
            assert "empty normalized slug" in capsys.readouterr().out
        state = _roadmap_state(tmp_path, phase="real", queue=["real", "x"], index=0, completed=[])
        new = _try_roadmap_advance(state, str(tmp_path))
        assert new["status"] == "escalated" and "empty normalized slug" in new["roadmap"]["pause_reason"]


# ── Phase 59: suffixed phase numbers ─────────────────────────────

SUFFIX_ROADMAP = """\
# Roadmap

## Phases

### Phase 5: Deployment Readiness
- **Status:** Complete

### Phase 5b: Dashboard UX
- **Status:** Complete

### Phase 6: Integrations
- **Status:** Not Started
- **Depends on:** Phase 5b

### Phase 7: Revive
- **Status:** Not Started
- **Depends on:** Phase 5
"""


class TestSuffixedPhaseNumbers:
    """A `### Phase 58a:` heading used to match nothing: `(\\d+):` took the
    digits and then demanded a colon. The phase vanished from the parse —
    unqueueable, unstartable, and invalid as a dependency target."""

    def test_suffixed_heading_is_parsed(self, tmp_path):
        phases = _phases(tmp_path, SUFFIX_ROADMAP)
        assert [(p.number, p.suffix) for p in phases] == [
            (5, ""), (5, "b"), (6, ""), (7, ""),
        ]
        assert [p.slug for p in phases] == [
            "deployment-readiness", "dashboard-ux", "integrations", "revive",
        ]

    def test_dependency_on_a_suffixed_phase_resolves(self, tmp_path):
        """The bugalizer shape: a valid `- **Depends on:** Phase 5b` was
        reported as an unknown dependency because 5b was never parsed."""
        phases = _phases(tmp_path, SUFFIX_ROADMAP)
        by = {p.slug: p for p in phases}
        assert by["integrations"].depends_on == ["dashboard-ux"]
        assert validate_graph(phases) == []

    def test_suffixed_and_unsuffixed_do_not_cross_resolve(self, tmp_path):
        phases = _phases(tmp_path, SUFFIX_ROADMAP)
        assert _resolve_ref("Phase 5", phases).slug == "deployment-readiness"
        assert _resolve_ref("Phase 5b", phases).slug == "dashboard-ux"
        # `Phase 7` depends on `Phase 5`, not on `Phase 5b`.
        by = {p.slug: p for p in phases}
        assert by["revive"].depends_on == ["deployment-readiness"]

    def test_suffix_case_is_normalized(self, tmp_path):
        phases = _phases(tmp_path, SUFFIX_ROADMAP.replace(
            "### Phase 5b: Dashboard UX", "### Phase 5B: Dashboard UX"))
        assert [(p.number, p.suffix) for p in phases][1] == (5, "b")
        for ref in ("Phase 5b", "Phase 5B", "phase-5b", "phase 5B"):
            assert _resolve_ref(ref, phases).slug == "dashboard-ux", ref

    def test_state_format_reference_accepts_a_suffix(self, tmp_path):
        phases = _phases(tmp_path, SUFFIX_ROADMAP)
        assert _resolve_ref("phase-5b-dashboard-ux", phases).slug == "dashboard-ux"
        assert _resolve_ref("phase-5-deployment-readiness", phases).slug == (
            "deployment-readiness")

    def test_number_and_suffix_pair_is_the_identity(self, tmp_path):
        """Keying duplicates on the bare number would refuse this roadmap for
        `duplicate phase number 5` — worse than the bug being fixed."""
        assert validate_identities(textwrap.dedent(SUFFIX_ROADMAP)) == []

    def test_a_real_duplicate_suffixed_number_is_still_reported(self, tmp_path):
        text = textwrap.dedent(SUFFIX_ROADMAP) + (
            "\n### Phase 5b: Something Else\n- **Status:** Not Started\n")
        problems = validate_identities(text)
        assert any("duplicate phase number 5b" in p for p in problems), problems

    def test_empty_suffixed_heading_is_labelled_with_its_suffix(self):
        problems = validate_identities("### Phase 5b:\n")
        assert problems == ["Phase 5b: empty name"]

    def test_queue_order_is_document_order(self, tmp_path):
        """`number` is not a sort key and must not become one."""
        roadmap = _write_roadmap(tmp_path, SUFFIX_ROADMAP)
        assert build_queue(roadmap) == ["integrations", "revive"]

    def test_unsuffixed_roadmaps_are_unchanged(self, tmp_path):
        phases = _phases(tmp_path, SAMPLE_ROADMAP)
        assert [p.number for p in phases] == [1, 2, 3, 4]
        assert all(p.suffix == "" for p in phases)

    def test_positional_construction_still_works(self):
        """`suffix` is appended last so existing positional callers keep
        working."""
        p = RoadmapPhase("slug", "Name", "Not Started", 5)
        assert (p.number, p.suffix) == (5, "")


# ── Phase 60: decimals, deployed, no silent drops ────────────────

DECIMAL_ROADMAP = """\
# Roadmap

## Phases

### Phase 9: Ordinary
- **Status:** Not Started

### Phase 9a: Letter Child
- **Status:** Not Started

### Phase 9.1: Decimal Child
- **Status:** Not Started

### Phase 10: Consumer
- **Status:** Not Started
- **Depends on:** Phase 9.1
"""


class TestDecimalPhaseNumbers:
    """`### Phase 9.1:` matched neither the strict nor the lenient pattern:
    `\\d+` took the `9`, the optional letter matched empty, and the pattern
    then demanded `:` and found `.`. Missing from the lenient scan too, so the
    phase was absent from identity validation as well as from the parse —
    nothing anywhere reported it. Found on Liminal: 28 headings, 26 parsed."""

    def test_decimal_heading_is_parsed(self, tmp_path):
        phases = _phases(tmp_path, DECIMAL_ROADMAP)
        assert [(p.number, p.suffix) for p in phases] == [
            (9, ""), (9, "a"), (9, ".1"), (10, ""),
        ]
        assert [p.slug for p in phases] == [
            "ordinary", "letter-child", "decimal-child", "consumer",
        ]

    def test_dependency_on_a_decimal_phase_resolves(self, tmp_path):
        phases = _phases(tmp_path, DECIMAL_ROADMAP)
        by = {p.slug: p for p in phases}
        assert by["consumer"].depends_on == ["decimal-child"]
        assert validate_graph(phases) == []

    def test_decimal_letter_and_bare_are_three_distinct_phases(self, tmp_path):
        """Keying on the bare number would refuse this roadmap outright."""
        assert validate_identities(textwrap.dedent(DECIMAL_ROADMAP)) == []
        phases = _phases(tmp_path, DECIMAL_ROADMAP)
        assert _resolve_ref("Phase 9", phases).slug == "ordinary"
        assert _resolve_ref("Phase 9a", phases).slug == "letter-child"
        assert _resolve_ref("Phase 9.1", phases).slug == "decimal-child"

    def test_decimal_state_format_reference_resolves(self, tmp_path):
        phases = _phases(tmp_path, DECIMAL_ROADMAP)
        assert _resolve_ref("phase-9.1-decimal-child", phases).slug == "decimal-child"

    def test_a_repeated_decimal_identity_is_reported(self, tmp_path):
        text = textwrap.dedent(DECIMAL_ROADMAP) + (
            "\n### Phase 9.1: Something Else\n- **Status:** Not Started\n")
        problems = validate_identities(text)
        assert any("duplicate phase number 9.1" in p for p in problems), problems

    def test_decimal_phase_is_queueable(self, tmp_path):
        roadmap = _write_roadmap(tmp_path, DECIMAL_ROADMAP)
        assert build_queue(roadmap) == [
            "ordinary", "letter-child", "decimal-child", "consumer"]

    def test_letter_suffixes_are_unchanged(self, tmp_path):
        """Phase 59 behaviour must survive the widening."""
        phases = _phases(tmp_path, SUFFIX_ROADMAP)
        assert [(p.number, p.suffix) for p in phases] == [
            (5, ""), (5, "b"), (6, ""), (7, ""),
        ]
        assert _resolve_ref("Phase 5b", phases).slug == "dashboard-ux"


class TestDeployedIsTerminal:
    """A phase marked "Deployed" never left `roadmap ready`."""

    def test_deployed_is_terminal(self):
        assert is_terminal_status("Deployed") is True
        assert is_terminal_status("Deployed to prod 2026-09-15") is True
        assert is_terminal_status("✅ Deployed") is True

    def test_deployed_phase_drops_out_of_incomplete(self, tmp_path):
        roadmap = _write_roadmap(tmp_path, SAMPLE_ROADMAP.replace(
            "### Phase 3: Dashboard\n- **Status:** Not Started",
            "### Phase 3: Dashboard\n- **Status:** Deployed to prod"))
        assert [p.slug for p in get_incomplete_phases(roadmap)] == [
            "api-gateway", "ci-integration"]

    def test_non_terminal_words_are_unaffected(self):
        assert is_terminal_status("Not started") is False
        assert is_terminal_status("In progress") is False
        assert is_terminal_status("Deploying") is False


class TestUnparsedHeadingWarnings:
    """The class defect under both bugs: a heading that looks like a phase and
    does not parse was dropped without a word. That silence is why the decimal
    bug survived two months, and why Phase 59 existed at all."""

    def test_unsupported_shapes_are_reported_with_line_numbers(self):
        text = (
            "# Roadmap\n"              # 1
            "\n"                        # 2
            "### Phase 9: Fine\n"       # 3
            "\n"                        # 4
            "### Phase 9.1.2: Deep\n"   # 5
            "\n"                        # 6
            "### Phase 9-1: Dashed\n"   # 7
            "\n"                        # 8
            "### Phase IX: Roman\n"     # 9
        )
        assert unparsed_phase_headings(text) == [
            (5, "### Phase 9.1.2: Deep"),
            (7, "### Phase 9-1: Dashed"),
            (9, "### Phase IX: Roman"),
        ]

    def test_supported_shapes_are_not_reported(self):
        text = textwrap.dedent(DECIMAL_ROADMAP) + textwrap.dedent(SUFFIX_ROADMAP)
        assert unparsed_phase_headings(text) == []

    def test_bare_heading_without_a_name_is_not_a_warning(self):
        """The lenient pattern understands it; `validate_identities` already
        reports the empty name. The warning is only for shapes nothing parses."""
        assert unparsed_phase_headings("### Phase 5b:\n") == []

    def test_section_headings_are_not_flagged(self):
        assert unparsed_phase_headings("### Phases\n## Phases\n") == []
        assert unparsed_phase_headings(
            "### ~~Old thing~~ → promoted to Phase 43\n") == []

    def test_check_warns_and_still_exits_zero(self, tmp_path, monkeypatch, capsys):
        _write_roadmap(tmp_path, textwrap.dedent(DECIMAL_ROADMAP)
                       + "\n### Phase IX: Roman\n- **Status:** Not Started\n")
        monkeypatch.chdir(tmp_path)

        assert roadmap_command(["check"]) == 0
        out = capsys.readouterr().out
        assert "warn: unparsed phase heading (line 18): ### Phase IX: Roman" in out
        assert "roadmap ok: 4 phase(s), 1 dependency edge(s)" in out

    def test_clean_roadmap_prints_no_warning(self, tmp_path, monkeypatch, capsys):
        _write_roadmap(tmp_path, SAMPLE_ROADMAP)
        monkeypatch.chdir(tmp_path)

        assert roadmap_command(["check"]) == 0
        assert "warn:" not in capsys.readouterr().out

    def test_warning_shows_when_every_heading_is_unsupported(
            self, tmp_path, monkeypatch, capsys):
        """`graph_problems` raises "No phases found" here. The headings are the
        cause, so they must be on screen — and the exit code stays 1."""
        _write_roadmap(tmp_path, "# Roadmap\n\n### Phase IX: Roman\n")
        monkeypatch.chdir(tmp_path)

        assert roadmap_command(["check"]) == 1
        out = capsys.readouterr().out
        assert "warn: unparsed phase heading (line 3): ### Phase IX: Roman" in out
        assert "Error:" in out

    def test_warning_shows_alongside_an_unrelated_graph_error(
            self, tmp_path, monkeypatch, capsys):
        _write_roadmap(tmp_path, textwrap.dedent(SAMPLE_ROADMAP)
                       + "\n### Phase 5: Broken\n- **Depends on:** Phase 99\n"
                       + "\n### Phase IX: Roman\n")
        monkeypatch.chdir(tmp_path)

        assert roadmap_command(["check"]) == 1
        out = capsys.readouterr().out
        assert "warn: unparsed phase heading" in out
        assert "roadmap invalid" in out

    def test_warnings_do_not_block_ready_or_queue(self, tmp_path, monkeypatch, capsys):
        """The whole point of the separate channel: an unsupported heading is
        visible but never refuses the roadmap."""
        roadmap = _write_roadmap(tmp_path, textwrap.dedent(SAMPLE_ROADMAP)
                                 + "\n### Phase IX: Roman\n")
        monkeypatch.chdir(tmp_path)

        assert build_queue(roadmap) == ["api-gateway", "dashboard", "ci-integration"]
        assert roadmap_command(["ready"]) == 0


# ---------------------------------------------------------------------------
# Phase 63 — a heading whose whole title is a bracketed placeholder is not a
# phase; the roadmap `tagteam setup` seeds is valid out of the box
# ---------------------------------------------------------------------------

from tagteam.roadmap import (  # noqa: E402
    graph_problems, has_real_phase, placeholder_phase_headings)

_DATA = Path(__file__).resolve().parents[1] / "tagteam" / "data"
SEED = (_DATA / "seeds" / "roadmap.md").read_text(encoding="utf-8")
OLD_SEED = (_DATA / "templates" / "roadmap.md").read_text(encoding="utf-8")     # what 3.14.1 projects have


class TestPlaceholderPhases:
    def _check(self, tmp_path, monkeypatch, capsys, text, sub=("check",)):
        _write_roadmap(tmp_path, text)
        monkeypatch.chdir(tmp_path)
        capsys.readouterr()
        code = roadmap_command(list(sub))
        return code, capsys.readouterr().out

    @pytest.mark.parametrize("seed", [SEED, OLD_SEED], ids=["seed", "pre-63-seed"])
    def test_seeded_roadmap_is_valid_with_warnings(self, tmp_path, monkeypatch, capsys, seed):
        code, out = self._check(tmp_path, monkeypatch, capsys, seed)
        assert code == 0, out
        assert out.count("warn: placeholder phase heading (line ") == 3
        assert "### Phase 1: [Name] — rename it to make it a phase" in out
        assert "roadmap ok: 0 phase(s) — 3 placeholder heading(s) to rename" in out
        assert "roadmap invalid" not in out and "Error" not in out

    def test_fresh_setup_passes_check(self, tmp_path, monkeypatch, capsys):
        """The regression as the owner meets it: setup, then check."""
        from tagteam import framework as fw, setup as su
        monkeypatch.setenv("TAGTEAM_CLAUDE_BIN", "")
        fw.apply(fw.build_plan(tmp_path, data_dir=su.get_data_dir()))
        assert (tmp_path / "docs" / "roadmap.md").read_text(encoding="utf-8") == SEED
        monkeypatch.chdir(tmp_path)
        capsys.readouterr()
        assert roadmap_command(["check"]) == 0
        assert "roadmap ok: 0 phase(s)" in capsys.readouterr().out

    def test_placeholder_between_real_phases_bounds_sections_and_is_no_phase(self, tmp_path, monkeypatch, capsys):
        text = ("### Phase 1: Alpha\n- **Status:** Not Started\n"
                "### Phase 2: [Name]\n- **Status:** Complete\n- **Depends on:** Phase 3\n"
                "### Phase 3: Gamma\n- **Status:** Not Started\n")
        path = _write_roadmap(tmp_path, text)
        phases = parse_roadmap(path)
        assert [(p.slug, p.status, p.depends_on) for p in phases] == [
            ("alpha", "Not Started", []), ("gamma", "Not Started", [])]      # nothing bled into Alpha
        code, out = self._check(tmp_path, monkeypatch, capsys, text)
        assert code == 0 and "roadmap ok: 2 phase(s), 0 dependency edge(s)" in out
        assert out.count("warn: placeholder phase heading") == 1 and "(line 3)" in out
        code, out = self._check(tmp_path, monkeypatch, capsys, text, sub=("ready",))
        assert code == 0 and "alpha" in out and "gamma" in out and "name" not in out.split()

    def test_only_a_whole_bracketed_title_is_a_placeholder(self):
        text = ("### Phase 1: []\n### Phase 2: [ ]\n### Phase 3: [First phase]\n"
                "### Phase 4: Parser [v2]\n### Phase 5: [a] and [b]\n")
        assert [l for _, l in placeholder_phase_headings(text)] == [
            "### Phase 1: []", "### Phase 2: [ ]", "### Phase 3: [First phase]"]
        assert has_real_phase(text) and validate_identities(text) == []

    def test_real_titles_with_brackets_stay_phases(self, tmp_path):
        path = _write_roadmap(tmp_path, "### Phase 4: Parser [v2]\n- **Status:** Not Started\n"
                                        "### Phase 5: [a] and [b]\n- **Status:** Not Started\n")
        assert [p.slug for p in parse_roadmap(path)] == ["parser-v2", "a-and-b"]

    def test_real_phase_depending_on_a_placeholder_is_an_unknown_dependency(self, tmp_path, monkeypatch, capsys):
        text = ("### Phase 1: Alpha\n- **Status:** Not Started\n- **Depends on:** Phase 2\n"
                "### Phase 2: [Name]\n- **Status:** Not Started\n")
        code, out = self._check(tmp_path, monkeypatch, capsys, text)
        assert code == 1 and "unknown dependency 'Phase 2'" in out

    def test_placeholders_claim_no_number_and_no_slug(self):
        text = ("### Phase 1: Alpha\n### Phase 1: [Name]\n### Phase 2: [Name]\n### Phase 2: [Name]\n")
        assert validate_identities(text) == []

    def test_identity_problems_are_never_masked_by_placeholder_success(self, tmp_path, monkeypatch, capsys):
        """Plan review: with no real phase `graph_problems` raises and would
        drop the identity list; `check` must report it, not print ok."""
        code, out = self._check(tmp_path, monkeypatch, capsys,
                                "### Phase 1: [Name]\n- **Status:** Not Started\n### Phase 2:\n")
        assert code == 1, out
        assert "roadmap invalid (1 problem(s)):" in out and "Phase 2: empty name" in out
        assert "roadmap ok" not in out and "warn: placeholder phase heading (line 1)" in out
        code, out = self._check(tmp_path, monkeypatch, capsys,
                                "### Phase 1: [Name]\n### Phase 2:\n### Phase 2:\n")
        assert code == 1 and out.count("Phase 2: empty name") == 2
        assert "duplicate phase number 2" in out and "roadmap ok" not in out

    def test_no_headings_at_all_is_the_same_error_as_before(self, tmp_path, monkeypatch, capsys):
        code, out = self._check(tmp_path, monkeypatch, capsys, "# Roadmap\n\nnothing yet\n")
        assert code == 1 and "Error: No phases found in" in out
        assert "Expected '### Phase N: <name>' headings." in out and "placeholder" not in out

    def test_ready_queue_graph_on_the_seed_say_why_there_is_nothing(self, tmp_path, monkeypatch, capsys):
        for sub in (("ready",), ("queue",), ("graph",)):
            code, out = self._check(tmp_path, monkeypatch, capsys, SEED, sub=sub)
            assert code == 1, (sub, out)
            assert "3 placeholder heading(s) ([Name]); rename them to make them phases." in out

    def test_callers_see_the_seed_exactly_as_a_roadmap_without_phases(self, tmp_path):
        """launch.py / watcher.py / worktree.py catch ValueError today; the
        seed must keep arriving as that, not as a new empty-list shape."""
        from tagteam import launch
        seeded = tmp_path / "seeded"; empty = tmp_path / "empty"
        _write_roadmap(seeded, SEED); _write_roadmap(empty, "# Roadmap\n")
        for root in (seeded, empty):
            assert launch._actionable_phases(root) == []
            assert launch._next_after(root, None) == (None, True)
            with pytest.raises(ValueError, match="No phases found"):
                graph_problems(root / "docs" / "roadmap.md")           # what the watcher catches
            with pytest.raises(ValueError, match="No phases found"):
                parse_roadmap(root / "docs" / "roadmap.md")


# ---------------------------------------------------------------------------
# Phase 66 — `unknown dependency` and `depends on itself` say which line
# ---------------------------------------------------------------------------

class TestDependencyProblemLines:
    BUGALIZER = (
        "# Roadmap\n\n## Phases\n\n"                                                    # lines 1-4
        "### Phase 8: open-pr (B2)\n- **Status:** Implemented\n\n"                       # 5-7
        "### Phase 9: private-repo-access (proposed)\n- **Status:** Proposed\n"           # 8-9
        "- **Open question:** one repo only\n"                                            # 10
        "- **Depends on:** Phase 8 (credential code), Dan's token for acceptance\n")       # 11

    def test_the_line_that_cost_a_grep(self, tmp_path, monkeypatch, capsys):
        path = _write_roadmap(tmp_path, self.BUGALIZER)
        phases = parse_roadmap(path)
        assert [p.line for p in phases] == [5, 8]
        assert validate_graph(phases) == [
            "private-repo-access-proposed: unknown dependency 'Phase 8 (credential code)' (line 11)",
            "private-repo-access-proposed: unknown dependency 'Dan's token for acceptance' (line 11)"]
        monkeypatch.chdir(tmp_path)
        assert roadmap_command(["check"]) == 1
        out = capsys.readouterr().out
        assert out.count("(line 11)") == 2 and "roadmap invalid (2 problem(s)):" in out

    def test_self_dependency_and_resolved_references_carry_their_lines(self, tmp_path):
        text = ("### Phase 1: Alpha\n- **Status:** Not Started\n\n- **Depends on:** Phase 1\n"
                "### Phase 2: Beta\n- **Status:** Not Started\n- **Depends on:** alpha\n")
        phases = parse_roadmap(_write_roadmap(tmp_path, text))
        assert validate_graph(phases) == ["alpha: depends on itself (line 4)"]
        assert phases[1].depends_on == ["alpha"] and phases[1].dep_lines == {"alpha": 7}

    def test_first_line_wins_for_repeats_and_for_aliases_of_one_phase(self, tmp_path):
        text = ("### Phase 1: Alpha\n- **Status:** Done\n"
                "### Phase 2: Beta\n- **Status:** Not Started\n"
                "- **Depends on:** ghost, Phase 1\n"            # line 5
                "- **Depends on:** ghost\n"                     # line 6: repeated verbatim
                "- **Depends on:** Alpha, alpha, other\n")      # line 7: two more spellings of Phase 1
        beta = parse_roadmap(_write_roadmap(tmp_path, text))[1]
        assert beta.depends_on == ["ghost", "alpha", "other"]
        assert beta.dep_lines == {"ghost": 5, "alpha": 5, "other": 7}
        assert validate_graph(parse_roadmap(tmp_path / "docs" / "roadmap.md")) == [
            "beta: unknown dependency 'ghost' (line 5)", "beta: unknown dependency 'other' (line 7)"]

    def test_crlf_and_placeholder_headings_do_not_shift_the_numbers(self, tmp_path):
        text = ("### Phase 1: Alpha\n- **Status:** Done\n"
                "### Phase 2: [Name]\n- **Status:** Not Started\n- **Depends on:** nowhere\n"   # a placeholder: no phase, no problem
                "### Phase 3: Gamma\n- **Status:** Not Started\n- **Depends on:** ghost\n")     # line 8
        for body in (text, text.replace("\n", "\r\n")):
            path = tmp_path / "docs" / "roadmap.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body.encode("utf-8"))
            phases = parse_roadmap(path)
            assert [(p.slug, p.line) for p in phases] == [("alpha", 1), ("gamma", 6)]
            assert validate_graph(phases) == ["gamma: unknown dependency 'ghost' (line 8)"]

    def test_hand_built_phases_keep_the_exact_old_strings(self):
        a = RoadmapPhase("a", "A", "Not Started", 1, ["ghost", "a"], "")          # positional, as before Phase 66
        assert a.line == 0 and a.dep_lines == {}
        assert validate_graph([a]) == ["a: unknown dependency 'ghost'", "a: depends on itself"]
        b = RoadmapPhase("b", "B", "Not Started", 2, ["ghost"], dep_lines={"ghost": 0})
        assert validate_graph([b]) == ["b: unknown dependency 'ghost'"]          # 0 = unknown, never "(line 0)"

    def test_location_is_not_part_of_a_phases_identity(self, tmp_path):
        one = parse_roadmap(_write_roadmap(tmp_path / "one", "### Phase 1: A\n- **Depends on:** ghost\n"))
        two = parse_roadmap(_write_roadmap(tmp_path / "two", "\n\n### Phase 1: A\n\n- **Depends on:** ghost\n"))
        assert one == two and (one[0].line, two[0].line) == (1, 3)
        assert one[0].dep_lines != two[0].dep_lines
        assert one[0] == RoadmapPhase(slug="a", name="A", status="Unknown", number=1, depends_on=["ghost"])
