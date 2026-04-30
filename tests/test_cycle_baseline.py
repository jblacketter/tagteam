"""Regression tests for cycle baseline capture + scope-diff.

Covers Issue #2 from docs/handoff-cycle-issues-2026-04-24.md:
- Plan-init captures baseline (sha + dirty_paths) in the cycle status.
- Impl-init propagates baseline forward from the matching plan cycle,
  or falls back to fresh capture (with stderr warning) when absent.
- `tagteam cycle scope-diff` reports phase-attributable paths, filtering
  pre-existing dirty paths from the uncommitted set but never from
  committed-since-baseline (so committed phase work surfaces even when
  the touched path was dirty at baseline).
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

import tagteam.state as state_mod
from tagteam.cycle import (
    _GIT_EMPTY_TREE,
    _capture_baseline,
    _cli_scope_diff,
    cycle_command,
    init_cycle,
)


@pytest.fixture(autouse=True)
def _reset_root_cache():
    state_mod._cached_project_root = None
    state_mod._warned_outer = False
    yield
    state_mod._cached_project_root = None
    state_mod._warned_outer = False


def _git(cwd: Path, *args: str) -> str:
    """Run a git command in cwd; return stdout. Raise on failure."""
    r = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, check=True,
    )
    return r.stdout


def _seed_repo(path: Path, *, with_initial_commit: bool = True) -> Path:
    """Initialize a git repo at path. Returns path. Configures user.* so
    commits work in CI/sandboxed environments."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    if with_initial_commit:
        (path / "seed.txt").write_text("seed\n")
        _git(path, "add", "seed.txt")
        _git(path, "commit", "-q", "-m", "seed")
    return path


def _make_tagteam_project(tmp_path: Path, monkeypatch,
                          *, with_git: bool = True,
                          with_initial_commit: bool = True) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    if with_git:
        _seed_repo(proj, with_initial_commit=with_initial_commit)
    (proj / "tagteam.yaml").write_text("agents: {}\n")
    (proj / "docs" / "handoffs").mkdir(parents=True)
    if with_git and with_initial_commit:
        # Commit project scaffolding so the repo starts clean from the
        # baseline-capture POV. Tests can dirty the tree as needed.
        _git(proj, "add", "tagteam.yaml")
        _git(proj, "commit", "-q", "-m", "scaffold")
    monkeypatch.chdir(proj)
    return proj


# --- 1-4: plan-init baseline capture ---

class TestPlanInitCapturesBaseline:
    def test_clean_repo(self, tmp_path, monkeypatch):
        proj = _make_tagteam_project(tmp_path, monkeypatch)
        init_cycle("p", "plan", "L", "R", "c", project_dir=str(proj), updated_by="L")
        status = json.loads(
            (proj / "docs/handoffs/p_plan_status.json").read_text()
        )
        b = status["baseline"]
        assert b is not None
        assert isinstance(b["sha"], str) and len(b["sha"]) == 40
        assert b["dirty_paths"] == []
        assert b["source"] == "plan-init"

    def test_pre_existing_drift(self, tmp_path, monkeypatch):
        proj = _make_tagteam_project(tmp_path, monkeypatch)
        (proj / "drift.txt").write_text("dirt\n")
        init_cycle("p", "plan", "L", "R", "c", project_dir=str(proj), updated_by="L")
        b = json.loads(
            (proj / "docs/handoffs/p_plan_status.json").read_text()
        )["baseline"]
        assert b["sha"] is not None
        assert any("drift.txt" in line for line in b["dirty_paths"])
        assert b["source"] == "plan-init"

    def test_outside_git_repo(self, tmp_path, monkeypatch):
        proj = _make_tagteam_project(tmp_path, monkeypatch, with_git=False)
        init_cycle("p", "plan", "L", "R", "c", project_dir=str(proj), updated_by="L")
        b = json.loads(
            (proj / "docs/handoffs/p_plan_status.json").read_text()
        )["baseline"]
        assert b is None

    def test_repo_with_no_commits(self, tmp_path, monkeypatch):
        proj = _make_tagteam_project(
            tmp_path, monkeypatch, with_initial_commit=False
        )
        (proj / "x.txt").write_text("x\n")
        init_cycle("p", "plan", "L", "R", "c", project_dir=str(proj), updated_by="L")
        b = json.loads(
            (proj / "docs/handoffs/p_plan_status.json").read_text()
        )["baseline"]
        assert b is not None
        assert b["sha"] is None
        assert any("x.txt" in line for line in b["dirty_paths"])
        assert b["source"] == "plan-init"


# --- 5-7: impl-init baseline propagation ---

class TestImplInitPropagation:
    def test_copies_from_plan(self, tmp_path, monkeypatch):
        proj = _make_tagteam_project(tmp_path, monkeypatch)
        (proj / "drift.txt").write_text("dirt\n")
        init_cycle("p", "plan", "L", "R", "c", project_dir=str(proj), updated_by="L")
        plan_b = json.loads(
            (proj / "docs/handoffs/p_plan_status.json").read_text()
        )["baseline"]

        init_cycle("p", "impl", "L", "R", "c", project_dir=str(proj), updated_by="L")
        impl_b = json.loads(
            (proj / "docs/handoffs/p_impl_status.json").read_text()
        )["baseline"]

        assert impl_b["sha"] == plan_b["sha"]
        assert impl_b["dirty_paths"] == plan_b["dirty_paths"]
        assert impl_b["captured_at"] == plan_b["captured_at"]
        assert impl_b["source"] == "copied-from-plan"

    def test_fallback_when_no_plan(self, tmp_path, monkeypatch, capsys):
        proj = _make_tagteam_project(tmp_path, monkeypatch)
        # No plan cycle exists.
        init_cycle("p", "impl", "L", "R", "c", project_dir=str(proj), updated_by="L")
        impl_b = json.loads(
            (proj / "docs/handoffs/p_impl_status.json").read_text()
        )["baseline"]

        captured = capsys.readouterr()
        assert "no plan-cycle baseline" in captured.err
        assert impl_b is not None
        assert impl_b["source"] == "impl-init-fallback"

    def test_fallback_when_plan_baseline_is_null(self, tmp_path, monkeypatch, capsys):
        # Plan ran outside a git repo, so its baseline is None as a whole block.
        proj = _make_tagteam_project(tmp_path, monkeypatch, with_git=False)
        init_cycle("p", "plan", "L", "R", "c", project_dir=str(proj), updated_by="L")
        plan_b = json.loads(
            (proj / "docs/handoffs/p_plan_status.json").read_text()
        )["baseline"]
        assert plan_b is None

        # Now add git so impl has a fallback target.
        _seed_repo(proj)
        capsys.readouterr()  # discard prior buffer

        init_cycle("p", "impl", "L", "R", "c", project_dir=str(proj), updated_by="L")
        impl_b = json.loads(
            (proj / "docs/handoffs/p_impl_status.json").read_text()
        )["baseline"]

        captured = capsys.readouterr()
        assert "no plan-cycle baseline" in captured.err
        assert impl_b is not None
        assert impl_b["source"] == "impl-init-fallback"


# --- 8-10: scope-diff correctness ---

class TestScopeDiff:
    def _seed_plan(self, proj: Path, **kw):
        init_cycle("p", "plan", "L", "R", "c",
                   project_dir=str(proj), updated_by="L", **kw)

    def _seed_impl(self, proj: Path):
        init_cycle("p", "impl", "L", "R", "c",
                   project_dir=str(proj), updated_by="L")

    def _scope_diff(self, capsys) -> list[str]:
        capsys.readouterr()  # discard prior buffer
        rc = _cli_scope_diff(["--phase", "p", "--type", "impl"])
        out = capsys.readouterr().out.strip().splitlines()
        return out, rc

    def test_committed_phase_work_on_baseline_dirty_path(
        self, tmp_path, monkeypatch, capsys
    ):
        # Codex round-1 correctness case: a.py is dirty at baseline.
        # During the phase, we commit changes to a.py. scope-diff must
        # still surface a.py.
        proj = _make_tagteam_project(tmp_path, monkeypatch)
        (proj / "a.py").write_text("v1\n")  # tracked (we'll commit) — but first add+commit clean version
        _git(proj, "add", "a.py")
        _git(proj, "commit", "-q", "-m", "add a")
        # Now make a.py dirty for the plan-init baseline:
        (proj / "a.py").write_text("v1-dirty\n")

        self._seed_plan(proj)
        # Confirm a.py is in baseline_dirty:
        plan_b = json.loads(
            (proj / "docs/handoffs/p_plan_status.json").read_text()
        )["baseline"]
        assert any("a.py" in line for line in plan_b["dirty_paths"])

        # Phase work: commit the change to a.py.
        _git(proj, "add", "a.py")
        _git(proj, "commit", "-q", "-m", "phase work on a")

        self._seed_impl(proj)

        out, rc = self._scope_diff(capsys)
        assert rc == 0
        assert "a.py" in out, f"expected a.py in output, got {out}"

    def test_uncommitted_filters_baseline_dirty(
        self, tmp_path, monkeypatch, capsys
    ):
        proj = _make_tagteam_project(tmp_path, monkeypatch)
        # Track and commit b.py first so it can be "dirty" via modification.
        (proj / "b.py").write_text("v1\n")
        _git(proj, "add", "b.py")
        _git(proj, "commit", "-q", "-m", "add b")
        (proj / "b.py").write_text("v1-dirty\n")  # now dirty

        self._seed_plan(proj)
        # Phase: leave b.py still dirty (no further change), but add a new file c.py untracked.
        (proj / "c.py").write_text("c\n")

        self._seed_impl(proj)
        out, rc = self._scope_diff(capsys)
        assert rc == 0
        assert "c.py" in out
        assert "b.py" not in out, (
            f"b.py was already dirty at baseline and not committed; should be filtered. got {out}"
        )

    def test_no_commit_baseline_then_first_commit(
        self, tmp_path, monkeypatch, capsys
    ):
        # Codex round-2 case: plan-init in a git repo with no commits;
        # phase introduces the first commit; scope-diff must surface
        # that committed path via the empty-tree branch.
        proj = _make_tagteam_project(
            tmp_path, monkeypatch, with_initial_commit=False
        )
        # Pre-existing dirty file to ensure dirty_paths is non-empty:
        (proj / "predirt.txt").write_text("p\n")
        self._seed_plan(proj)
        plan_b = json.loads(
            (proj / "docs/handoffs/p_plan_status.json").read_text()
        )["baseline"]
        assert plan_b["sha"] is None

        # Phase work: commit a brand-new file (the first commit in this repo).
        (proj / "first.txt").write_text("first\n")
        _git(proj, "add", "first.txt")
        _git(proj, "commit", "-q", "-m", "first")

        self._seed_impl(proj)
        impl_b = json.loads(
            (proj / "docs/handoffs/p_impl_status.json").read_text()
        )["baseline"]
        assert impl_b["source"] == "copied-from-plan"
        assert impl_b["sha"] is None  # propagated as-is

        out, rc = self._scope_diff(capsys)
        assert rc == 0
        assert "first.txt" in out, (
            f"expected first.txt (first commit) in scope-diff output via empty-tree branch; got {out}"
        )


# --- 11b: tagteam artifacts excluded ---

def test_scope_diff_excludes_tagteam_artifacts(tmp_path, monkeypatch, capsys):
    """scope-diff must filter out tagteam-managed paths (handoff-state.json,
    docs/handoffs/*, etc.) — they are review-system output, not phase work.
    Codex round-1 impl-review concern."""
    proj = _make_tagteam_project(tmp_path, monkeypatch)
    init_cycle("p", "plan", "L", "R", "c", project_dir=str(proj), updated_by="L")

    # Phase work: one new committed file.
    (proj / "real-work.py").write_text("phase\n")
    _git(proj, "add", "real-work.py")
    _git(proj, "commit", "-q", "-m", "real")

    init_cycle("p", "impl", "L", "R", "c", project_dir=str(proj), updated_by="L")

    capsys.readouterr()
    rc = _cli_scope_diff(["--phase", "p", "--type", "impl"])
    out_lines = capsys.readouterr().out.strip().splitlines()
    assert rc == 0
    assert "real-work.py" in out_lines
    # Tagteam bookkeeping must not appear.
    assert not any("handoff-state.json" in p for p in out_lines), out_lines
    assert not any(p.startswith("docs/handoffs/") for p in out_lines), out_lines
    assert not any(".handoff-state.tmp" in p for p in out_lines), out_lines


# --- 12: legacy cycles ---

def test_legacy_cycle_without_baseline_errors(tmp_path, monkeypatch, capsys):
    proj = _make_tagteam_project(tmp_path, monkeypatch)
    # Manually write a status file without a baseline key (simulating
    # a cycle created before this phase).
    sp = proj / "docs/handoffs/legacy_impl_status.json"
    sp.write_text(json.dumps({
        "state": "in-progress",
        "ready_for": "reviewer",
        "round": 1,
        "phase": "legacy",
        "type": "impl",
    }) + "\n")

    capsys.readouterr()
    rc = _cli_scope_diff(["--phase", "legacy", "--type", "impl"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "no baseline" in out.lower()
