"""Phase 40: phase worktrees (tagteam.worktree)."""

import json
import subprocess
from pathlib import Path

import pytest

from tagteam import registry as registry_mod
from tagteam import worktree as wt
from tagteam.roadmap import RoadmapGraphError
from tagteam.state import write_state, read_state, _resolve_project_root


ROADMAP = """\
# Roadmap
### Phase 1: Alpha
- **Status:** Complete
### Phase 2: Beta
- **Status:** Not Started
- **Depends on:** alpha
### Phase 3: Gamma
- **Status:** Not Started
- **Depends on:** beta
### Phase 4: Delta
- **Status:** Not Started
"""


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout


def _commit_all(repo, msg):
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", msg)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repo that is a tagteam project, with the registry + sidecar
    redirected under tmp_path."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(registry_mod, "REGISTRY_DIR", home)
    monkeypatch.setattr(registry_mod, "REGISTRY_FILE", home / "projects.json")
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main", ".")
    _git(r, "config", "user.email", "t@t")      # merges need an identity on CI
    _git(r, "config", "user.name", "t")
    (r / "docs").mkdir()
    (r / "docs" / "roadmap.md").write_text(ROADMAP)
    (r / "tagteam.yaml").write_text("agents:\n  lead: {name: Claude}\n  reviewer: {name: Codex}\n")
    (r / ".gitignore").write_text("handoff-state.json\n")
    _commit_all(r, "init")
    import tagteam.state as st
    monkeypatch.setattr(st, "_cached_project_root", None, raising=False)
    return r


def _set_status(repo, phase_heading, status):
    p = repo / "docs" / "roadmap.md"
    text = p.read_text()
    marker = f"### Phase {phase_heading}\n- **Status:** "
    i = text.index(marker) + len(marker)
    j = text.index("\n", i)
    p.write_text(text[:i] + status + text[j:])


class TestCreate:
    def test_creates_branch_files_registry_sidecar_kickoff(self, repo, capsys):
        info = wt.create_worktree(repo, "beta")
        path = Path(info.path)
        assert path == repo.parent / "repo-beta"
        assert path.is_dir() and (path / "tagteam.yaml").exists()
        assert _git(path, "symbolic-ref", "--short", "HEAD").strip() == "phase-beta"
        assert info.branch == "phase-beta" and info.target == "main"
        assert info.base == _git(repo, "rev-parse", "HEAD").strip()
        assert str(path) in registry_mod.read_registry_raw()
        (entry,) = wt._read_sidecar()
        assert entry["phase"] == "beta" and entry["parent"] == str(repo) and entry["base"] == info.base
        text = wt.kickoff_text(info)
        assert f"cd {path}" in text and "/handoff start beta" in text
        assert "worktree beta --remove" in text
        # framework files were seeded only where missing
        assert (path / ".claude" / "skills" / "handoff" / "SKILL.md").exists()
        # runtime state was not copied
        assert not (path / "handoff-state.json").exists()

    def test_hub_row_labels_worktree(self, repo):
        info = wt.create_worktree(repo, "beta")
        from tagteam import hub_api
        row = hub_api.project_summary(info.path)
        assert row["worktree"] == {"phase": "beta", "parent": str(repo), "branch": "phase-beta"}
        assert hub_api._label(row).endswith("(worktree: beta)")
        assert hub_api.project_summary(repo)["worktree"] is None

    def test_refuse_unknown_terminal_notready_duplicate(self, repo):
        with pytest.raises(wt.WorktreeError, match="not found"):
            wt.create_worktree(repo, "nope")
        with pytest.raises(wt.WorktreeError, match="already complete"):
            wt.create_worktree(repo, "alpha")
        with pytest.raises(wt.WorktreeError, match="not ready .* depends on beta"):
            wt.create_worktree(repo, "gamma")
        wt.create_worktree(repo, "beta")
        with pytest.raises(wt.WorktreeError, match="already has a worktree"):
            wt.create_worktree(repo, "beta")

    def test_refuse_invalid_graph(self, repo):
        (repo / "docs" / "roadmap.md").write_text(ROADMAP + "### Phase 5: Beta\n- **Status:** Not Started\n")
        _commit_all(repo, "dup")
        with pytest.raises(RoadmapGraphError, match="duplicate slug 'beta'"):
            wt.create_worktree(repo, "delta")

    def test_accepts_phase_number_and_name(self, repo):
        info = wt.create_worktree(repo, "Phase 4")
        assert info.phase == "delta"

    def test_two_worktrees_are_independent_projects(self, repo, monkeypatch):
        a = wt.create_worktree(repo, "beta")
        b = wt.create_worktree(repo, "delta")
        write_state({"phase": "beta", "status": "ready", "turn": "lead"}, a.path)
        assert read_state(b.path) is None
        assert read_state(a.path)["phase"] == "beta"
        import tagteam.state as st
        for info in (a, b):
            monkeypatch.setattr(st, "_cached_project_root", None, raising=False)
            monkeypatch.chdir(Path(info.path) / "docs")
            assert _resolve_project_root() == str(Path(info.path).resolve())
        rows = wt.list_worktrees(repo)
        assert [(r["phase"], (r["state"] or {}).get("phase")) for r in rows] == [
            ("beta", "beta"), ("delta", None)]


class TestPublicationBoundary:
    def test_dirty_parent_refused(self, repo):
        (repo / "tagteam.yaml").write_text("agents: {}\n")
        with pytest.raises(wt.WorktreeError, match="not clean .*tagteam.yaml"):
            wt.create_worktree(repo, "beta")
        _git(repo, "checkout", "--", "tagteam.yaml")
        (repo / "scratch.txt").write_text("x")
        with pytest.raises(wt.WorktreeError, match="not clean .*scratch.txt"):
            wt.create_worktree(repo, "beta")
        (repo / "scratch.txt").unlink()
        wt.create_worktree(repo, "beta")

    def test_completed_dep_requires_base_containing_head(self, repo):
        # gamma depends on beta; beta approved in the active run (not terminal on disk)
        old = _git(repo, "rev-parse", "HEAD").strip()
        (repo / "README.md").write_text("beta impl\n")
        _commit_all(repo, "beta impl")
        write_state({"run_mode": "full-roadmap", "status": "done", "result": "approved",
                     "type": "impl", "phase": "beta",
                     "roadmap": {"queue": ["beta", "gamma"], "current_index": 0,
                                 "completed": ["beta"], "pause_reason": None}}, str(repo))
        with pytest.raises(wt.WorktreeError, match="does not contain HEAD"):
            wt.create_worktree(repo, "gamma", from_ref=old)
        # descendant of HEAD is fine
        _git(repo, "checkout", "-q", "-b", "later")
        (repo / "later.txt").write_text("y")
        _commit_all(repo, "later")
        _git(repo, "checkout", "-q", "main")
        info = wt.create_worktree(repo, "gamma", from_ref="later")
        assert info.base == _git(repo, "rev-parse", "later").strip()
        wt.remove_worktree(repo, "gamma", force=True)
        info = wt.create_worktree(repo, "gamma")             # no --from → HEAD
        assert info.base == _git(repo, "rev-parse", "HEAD").strip()

    def test_readiness_is_evaluated_on_roadmap_at_base(self, repo):
        old = _git(repo, "rev-parse", "HEAD").strip()
        _set_status(repo, "2: Beta", "✅ Complete")
        _commit_all(repo, "beta complete")
        with pytest.raises(wt.WorktreeError, match="not ready at " + old[:12]):
            wt.create_worktree(repo, "gamma", from_ref=old)
        info = wt.create_worktree(repo, "gamma")             # terminal at HEAD → ok
        assert info.phase == "gamma"
        # target may be overridden
        wt.remove_worktree(repo, "gamma", force=True)
        info = wt.create_worktree(repo, "gamma", target="develop")
        assert info.target == "develop"

    def test_cross_worktree_publication_unblocks_parent_after_merge(self, repo):
        from tagteam.roadmap import check_graph, ready_phases
        info = wt.create_worktree(repo, "beta")
        path = Path(info.path)
        _set_status(path, "2: Beta", "✅ Complete")
        _commit_all(path, "beta done")
        rp = repo / "docs" / "roadmap.md"
        assert [p.slug for p in ready_phases(check_graph(rp))] == ["beta", "delta"]  # not before merge
        assert wt.merged_state(wt._read_sidecar()[0]) == "unmerged"
        _git(repo, "merge", "-q", "phase-beta")
        assert [p.slug for p in ready_phases(check_graph(rp))] == ["gamma", "delta"]
        assert wt.merged_state(wt._read_sidecar()[0]) == "merged"


class TestListRemove:
    def test_merged_against_recorded_target_not_checked_out_branch(self, repo):
        info = wt.create_worktree(repo, "beta")
        path = Path(info.path)
        (path / "work.txt").write_text("w")
        _commit_all(path, "work")
        _git(repo, "checkout", "-q", "-b", "other")       # parent switches away from main
        (repo / "o.txt").write_text("o")
        _commit_all(repo, "other")
        _git(repo, "merge", "-q", "phase-beta")           # merged into OTHER, not the target
        rows = wt.list_worktrees(repo)
        assert rows[0]["merged"] == "unmerged"
        with pytest.raises(wt.WorktreeError, match="unmerged relative to target 'main'"):
            wt.remove_worktree(repo, "beta")
        _git(repo, "checkout", "-q", "main")
        _git(repo, "merge", "-q", "phase-beta")
        _git(repo, "checkout", "-q", "other")             # still evaluated against main
        assert wt.list_worktrees(repo)[0]["merged"] == "merged"
        res = wt.remove_worktree(repo, "beta")
        assert res["merged"] == "merged" and not path.exists()
        assert wt._read_sidecar() == []
        assert str(path) not in registry_mod.read_registry_raw()
        assert "phase-beta" not in _git(repo, "branch")

    def test_force_removes_unmerged_and_missing_target(self, repo):
        info = wt.create_worktree(repo, "beta", target="release")
        path = Path(info.path)
        (path / "w").write_text("w")
        _commit_all(path, "w")
        assert wt.merged_state(wt._read_sidecar()[0]) == "target missing"
        with pytest.raises(wt.WorktreeError, match="target missing"):
            wt.remove_worktree(repo, "beta")
        res = wt.remove_worktree(repo, "beta", force=True)
        assert res["merged"] == "target missing" and not path.exists()
        assert "phase-beta" not in _git(repo, "branch")

    def test_remove_unknown(self, repo):
        with pytest.raises(wt.WorktreeError, match="no worktree recorded"):
            wt.remove_worktree(repo, "beta")

    def test_cli(self, repo, monkeypatch, capsys):
        monkeypatch.chdir(repo)
        from tagteam.roadmap import roadmap_command
        assert roadmap_command(["worktrees"]) == 0
        assert "No phase worktrees" in capsys.readouterr().out
        assert roadmap_command(["worktree", "beta"]) == 0
        out = capsys.readouterr().out
        assert "Worktree ready:" in out and "/handoff start beta" in out
        assert roadmap_command(["worktree", "gamma"]) == 1
        assert "not ready" in capsys.readouterr().out
        assert roadmap_command(["worktrees", "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data[0]["phase"] == "beta" and data[0]["merged"] == "merged"
        assert roadmap_command(["worktrees"]) == 0
        assert "phase-beta" in capsys.readouterr().out
        assert roadmap_command(["worktree", "beta", "--remove"]) == 0
        assert "Removed worktree" in capsys.readouterr().out
        assert roadmap_command(["worktree"]) == 1
        assert roadmap_command(["worktree", "beta", "--bogus"]) == 1


class TestRuntimePaths:
    def test_runtime_paths_do_not_count_as_dirty(self, repo):
        (repo / ".tagteam").mkdir()
        (repo / ".tagteam" / "tagteam.db").write_text("x")
        (repo / "handoff-state.json").write_text("{}")
        (repo / "handoff-diagnostics.jsonl").write_text("")
        assert wt._dirty_paths(repo) == []
        (repo / "notes.md").write_text("n")
        assert wt._dirty_paths(repo) == ["notes.md"]
        assert wt._is_runtime_path(".tagteam/x/y") and not wt._is_runtime_path("docs/handoffs/x.jsonl")


class TestImplRound2Fixes:
    """Impl round 1 findings: transactional creation, -D removal."""

    def _nothing_remains(self, repo, path):
        assert not path.exists()
        assert "phase-beta" not in _git(repo, "branch")
        assert str(path) not in registry_mod.read_registry_raw()
        assert wt._read_sidecar() == []
        assert str(path) not in _git(repo, "worktree", "list")

    def test_setup_failure_rolls_back_everything(self, repo, monkeypatch):
        import tagteam.setup as setup_mod

        def boom(target):
            from tagteam.registry import register_project
            register_project(target)              # setup registers before failing
            raise RuntimeError("disk full")
        monkeypatch.setattr(setup_mod, "main", boom)
        path = repo.parent / "repo-beta"
        with pytest.raises(wt.WorktreeError, match="could not be seeded.*disk full"):
            wt.create_worktree(repo, "beta")
        self._nothing_remains(repo, path)

    def test_incomplete_seeding_is_fatal(self, repo, monkeypatch):
        import tagteam.setup as setup_mod
        monkeypatch.setattr(setup_mod, "main", lambda target: None)   # "succeeds" but seeds nothing
        path = repo.parent / "repo-beta"
        with pytest.raises(wt.WorktreeError, match="still missing .* after setup"):
            wt.create_worktree(repo, "beta")
        self._nothing_remains(repo, path)

    def test_user_scoped_plugin_does_not_trigger_rollback(self, repo, monkeypatch):
        """Phase 48: a migrated project (plugin serves the skill) must still
        create worktrees — setup leaves no local skill, and needs_setup
        accepts that."""
        from tests._plugin_env import fake_plugin
        fake_plugin(repo.parent, monkeypatch)
        info = wt.create_worktree(repo, "beta")
        assert Path(info.path).exists()
        assert not (Path(info.path) / ".claude" / "skills" / "handoff").exists()
        assert (Path(info.path) / "docs" / "workflows.md").is_file()

    def test_project_scoped_plugin_vendors_into_worktree(self, repo, monkeypatch):
        """A project-scope record names the main checkout, not the worktree,
        so the worktree is vendored a skill as before — and still succeeds."""
        from tests._plugin_env import fake_plugin
        fake_plugin(repo.parent, monkeypatch, scope="project", project_path=repo)
        info = wt.create_worktree(repo, "beta")
        assert (Path(info.path) / ".claude" / "skills" / "handoff" / "SKILL.md").is_file()

    def test_sidecar_write_failure_rolls_back_including_registry(self, repo, monkeypatch):
        def fail(entries):
            raise OSError("read-only home")
        monkeypatch.setattr(wt, "_write_sidecar", fail)
        path = repo.parent / "repo-beta"
        with pytest.raises(OSError, match="read-only home"):
            wt.create_worktree(repo, "beta")
        # the failing sidecar writer is still patched; check the rest directly
        assert not path.exists()
        assert "phase-beta" not in _git(repo, "branch")
        assert str(path) not in registry_mod.read_registry_raw()

    def test_registry_failure_rolls_back(self, repo, monkeypatch):
        monkeypatch.setattr(registry_mod, "register_project",
                            lambda p: (_ for _ in ()).throw(OSError("registry locked")))
        path = repo.parent / "repo-beta"
        # setup registers the project itself, so this surfaces as a seeding failure
        with pytest.raises(wt.WorktreeError, match="registry locked"):
            wt.create_worktree(repo, "beta")
        self._nothing_remains(repo, path)

    def test_normal_remove_deletes_branch_when_parent_on_unrelated_branch(self, repo):
        info = wt.create_worktree(repo, "beta")
        path = Path(info.path)
        (path / "work.txt").write_text("w")
        _commit_all(path, "work")
        _git(repo, "merge", "-q", "phase-beta")           # merged into recorded target main
        _git(repo, "checkout", "-q", "-b", "unrelated", "HEAD~1")  # does not contain the phase
        (repo / "u.txt").write_text("u")
        _commit_all(repo, "unrelated")
        # git branch -d judges "merged" against the checked-out branch and would
        # refuse here ("not fully merged"); removal must still succeed
        assert not wt._is_ancestor(repo, "phase-beta", "HEAD")
        res = wt.remove_worktree(repo, "beta")
        assert res["merged"] == "merged"
        assert "phase-beta" not in _git(repo, "branch")
        assert wt._read_sidecar() == [] and str(path) not in registry_mod.read_registry_raw()

    def test_branch_deletion_failure_is_not_swallowed(self, repo, monkeypatch):
        info = wt.create_worktree(repo, "beta")
        path = Path(info.path)
        real_git = wt._git

        def fake_git(r, *args, check=True):
            if args[:2] == ("branch", "-D"):
                raise wt.WorktreeError("git branch -D failed: simulated")
            return real_git(r, *args, check=check)
        monkeypatch.setattr(wt, "_git", fake_git)
        with pytest.raises(wt.WorktreeError, match="simulated"):
            wt.remove_worktree(repo, "beta")
        # rows stay so a re-run can finish; nothing claimed success
        assert wt._read_sidecar()[0]["phase"] == "beta"
        assert str(path) in registry_mod.read_registry_raw()
        monkeypatch.setattr(wt, "_git", real_git)
        assert wt.remove_worktree(repo, "beta")["merged"] == "merged"
        assert wt._read_sidecar() == []
