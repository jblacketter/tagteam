"""Regression tests for tagteam project-root resolution.

Covers the silent nested-project-write bug from
docs/handoff-cycle-issues-2026-04-24.md (Issue #1):
- Walk-up discovery via `tagteam.yaml`.
- Warning when a parent directory also has `tagteam.yaml`.
- Banner emission from every `tagteam cycle` subcommand.
- Read-path consistency for `parser.read_cycle_rounds()`.
"""

import json
from pathlib import Path

import pytest

import tagteam.state as state_mod
from tagteam.cycle import cycle_command, init_cycle
from tagteam.parser import read_cycle_rounds


@pytest.fixture(autouse=True)
def _reset_root_cache():
    state_mod._cached_project_root = None
    state_mod._warned_outer = False
    yield
    state_mod._cached_project_root = None
    state_mod._warned_outer = False


class TestResolveProjectRoot:
    def test_walks_up_to_tagteam_yaml(self, tmp_path, monkeypatch):
        outer = tmp_path / "outer"
        sub = outer / "sub" / "sub2"
        sub.mkdir(parents=True)
        (outer / "tagteam.yaml").write_text("agents: {}\n")
        monkeypatch.chdir(sub)

        assert state_mod._resolve_project_root() == str(outer)

    def test_nested_tagteam_yaml_wins_with_warning(self, tmp_path, monkeypatch, capsys):
        outer = tmp_path / "outer"
        inner = outer / "inner"
        sub = inner / "sub"
        sub.mkdir(parents=True)
        (outer / "tagteam.yaml").write_text("agents: {}\n")
        (inner / "tagteam.yaml").write_text("agents: {}\n")
        monkeypatch.chdir(sub)

        resolved = state_mod._resolve_project_root()
        captured = capsys.readouterr()

        assert resolved == str(inner)
        assert "nested inside another tagteam project" in captured.err
        assert str(outer) in captured.err

    def test_no_tagteam_yaml_falls_back_to_git(self, tmp_path, monkeypatch):
        # No tagteam.yaml anywhere; mock git to return a known toplevel.
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        class _Result:
            returncode = 0
            stdout = str(repo) + "\n"

        def _fake_run(*args, **kwargs):
            return _Result()

        monkeypatch.setattr(state_mod.subprocess, "run", _fake_run)

        assert state_mod._resolve_project_root() == str(repo)

    def test_no_tagteam_yaml_no_git_falls_back_to_dot(self, tmp_path, monkeypatch):
        repo = tmp_path / "lonely"
        repo.mkdir()
        monkeypatch.chdir(repo)

        class _Result:
            returncode = 1
            stdout = ""

        monkeypatch.setattr(state_mod.subprocess, "run",
                            lambda *a, **k: _Result())

        assert state_mod._resolve_project_root() == "."

    def test_cache_pins_first_resolution(self, tmp_path, monkeypatch):
        outer = tmp_path / "outer"
        other = tmp_path / "other"
        outer.mkdir()
        other.mkdir()
        (outer / "tagteam.yaml").write_text("agents: {}\n")
        (other / "tagteam.yaml").write_text("agents: {}\n")

        monkeypatch.chdir(outer)
        first = state_mod._resolve_project_root()
        # Move to a different tagteam project; cached value must persist.
        monkeypatch.chdir(other)
        second = state_mod._resolve_project_root()

        assert first == second == str(outer)


class TestCycleBannerOnAllSubcommands:
    """The banner must be emitted by every cycle subcommand via cycle_command."""

    def _bootstrap_project(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "tagteam.yaml").write_text("agents: {}\n")
        (proj / "docs" / "handoffs").mkdir(parents=True)
        monkeypatch.chdir(proj)
        return proj

    def test_banner_on_every_subcommand(self, tmp_path, monkeypatch, capsys):
        proj = self._bootstrap_project(tmp_path, monkeypatch)

        # Seed a real cycle so status/rounds/render have something to read.
        init_cycle(
            phase="p", cycle_type="plan",
            lead="L", reviewer="R", content="seed",
            project_dir=str(proj), updated_by="L",
        )
        # Reset cache after init_cycle (which used explicit project_dir; should be fine, but be safe).
        state_mod._cached_project_root = None
        state_mod._warned_outer = False

        invocations = [
            ["init", "--phase", "x", "--type", "plan",
             "--lead", "L", "--reviewer", "R",
             "--updated-by", "L", "--content", "c"],
            ["add", "--phase", "p", "--type", "plan",
             "--role", "reviewer", "--action", "APPROVE",
             "--round", "1", "--updated-by", "R", "--content", "ok"],
            ["status", "--phase", "p", "--type", "plan"],
            ["rounds", "--phase", "p", "--type", "plan"],
            ["render", "--phase", "p", "--type", "plan"],
        ]

        for args in invocations:
            # Reset cache so the banner re-resolves and we exercise the path each time.
            state_mod._cached_project_root = None
            state_mod._warned_outer = False
            capsys.readouterr()  # discard prior buffer
            cycle_command(args)
            captured = capsys.readouterr()
            assert "[tagteam] project root:" in captured.err, \
                f"missing banner for {args[0]}"
            assert str(proj) in captured.err, \
                f"banner did not contain resolved root for {args[0]}"


class TestRoundsReadPathFromSubdir:
    def test_read_cycle_rounds_resolves_dot_from_subdir(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"
        sub = proj / "sub"
        sub.mkdir(parents=True)
        (proj / "tagteam.yaml").write_text("agents: {}\n")
        handoffs = proj / "docs" / "handoffs"
        handoffs.mkdir(parents=True)

        # Seed a JSONL rounds file directly.
        rounds_path = handoffs / "phaseX_plan_rounds.jsonl"
        seed = {
            "round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
            "content": "hello", "updated_by": "L",
            "timestamp": "2026-04-30T00:00:00+00:00",
        }
        rounds_path.write_text(json.dumps(seed) + "\n")

        monkeypatch.chdir(sub)
        rounds = read_cycle_rounds("phaseX", "plan")  # default project_dir="."

        assert rounds is not None
        assert len(rounds) >= 1
        # parse_jsonl_rounds may shape the dict; just confirm content survives.
        assert any("hello" in (r.get("content", "") + r.get("lead_text", ""))
                   for r in rounds)
