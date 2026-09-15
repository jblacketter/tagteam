"""Phase 56 — submission snapshots (tagteam/snapshots.py + the CLI capture hook)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tagteam import cycle as cycle_mod
from tagteam import db
from tagteam import snapshots as snap
from tagteam import state as state_mod


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def gitproject(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "tagteam.yaml").write_text("agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\n",
                                       encoding="utf-8")
    (root / "docs" / "handoffs").mkdir(parents=True)
    (root / ".gitignore").write_text("ignored.txt\n.tagteam/\nhandoff-state.json\n", encoding="utf-8")
    (root / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
         "commit", "-q", "-m", "init")
    monkeypatch.setattr(state_mod, "_cached_project_root", None, raising=False)
    monkeypatch.chdir(root)
    return root


def _rows(root):
    conn = db.connect(project_dir=str(root))
    try:
        return db.submission_snapshots(conn)
    finally:
        conn.close()


def _tree_files(root, commit):
    return sorted(_git(root, "ls-tree", "-r", "--name-only", commit).splitlines())


class TestTakeSnapshot:
    def test_captures_tracked_and_untracked_leaves_index_alone(self, gitproject):
        root = gitproject
        (root / "app.py").write_text("print('v2')\n", encoding="utf-8")
        (root / "new.py").write_text("x = 1\n", encoding="utf-8")
        (root / "ignored.txt").write_text("secret\n", encoding="utf-8")
        _git(root, "stash", "list")
        head_before = _git(root, "rev-parse", "HEAD")
        status_before = _git(root, "status", "--porcelain")
        s = snap.take_snapshot(root, "test")
        assert s["head_sha"] == head_before
        files = _tree_files(root, s["commit_sha"])
        assert "new.py" in files and "app.py" in files and "ignored.txt" not in files
        assert _git(root, "show", f"{s['commit_sha']}:app.py") == "print('v2')"
        assert _git(root, "rev-parse", f"{s['commit_sha']}^") == head_before
        assert _git(root, "rev-parse", "HEAD") == head_before
        assert _git(root, "status", "--porcelain") == status_before
        assert _git(root, "stash", "list") == ""

    def test_not_a_git_repo(self, tmp_path):
        with pytest.raises(snap.SnapshotError, match="not a git repository"):
            snap.take_snapshot(tmp_path, "x")

    def test_ref_name_is_valid(self, gitproject):
        ref = snap.snapshot_ref("feat-x", "impl", 2, "2026-09-15T05:23:37.048750+00:00")
        assert ref == "refs/tagteam/snapshots/feat-x/impl/r2/20260915T0523370487500000"
        subprocess.run(["git", "check-ref-format", ref], cwd=gitproject, check=True)


class TestCliCapture:
    def test_init_submit_and_amend_each_snapshot(self, gitproject, capsys):
        root = gitproject
        assert cycle_mod._cli_init(["--phase", "feat-x", "--type", "impl", "--content", "r1"]) == 0
        (root / "app.py").write_text("print('amended')\n", encoding="utf-8")
        assert cycle_mod._cli_add(["--phase", "feat-x", "--type", "impl", "--role", "lead",
                                   "--action", "AMEND", "--round", "1", "--content", "amend"]) == 0
        rows = _rows(root)
        assert [r["action"] for r in rows] == ["SUBMIT_FOR_REVIEW", "AMEND"]
        entries = cycle_mod.read_rounds("feat-x", "impl", str(root))
        lead_ts = [e["ts"] for e in entries if e["role"] == "lead"]
        assert [r["entry_ts"] for r in rows] == lead_ts
        base = cycle_mod.read_status("feat-x", "impl", str(root))["baseline"]["sha"]
        assert all(r["base_sha"] == base for r in rows)
        assert _git(root, "show", f"{rows[0]['commit_sha']}:app.py") == "print('v1')"
        assert _git(root, "show", f"{rows[1]['commit_sha']}:app.py") == "print('amended')"
        for r in rows:
            assert _git(root, "rev-parse", r["ref"]) == r["commit_sha"]
        assert "snapshot not captured" not in capsys.readouterr().err

        cycle_mod._cli_add(["--phase", "feat-x", "--type", "impl", "--role", "reviewer",
                            "--action", "REQUEST_CHANGES", "--round", "1", "--content", "fix"])
        cycle_mod._cli_add(["--phase", "feat-x", "--type", "impl", "--role", "lead",
                            "--action", "SUBMIT_FOR_REVIEW", "--round", "2", "--content", "r2"])
        rows = _rows(root)
        assert [(r["round"], r["action"]) for r in rows][-1] == (2, "SUBMIT_FOR_REVIEW")
        assert len(rows) == 3

    def test_failure_prints_note_and_submission_proceeds(self, gitproject, capsys, monkeypatch):
        def boom(*a, **k):
            raise snap.SnapshotError("git write-tree failed: simulated")
        monkeypatch.setattr(snap, "take_snapshot", boom)
        assert cycle_mod._cli_init(["--phase", "feat-y", "--content", "r1"]) == 0
        err = capsys.readouterr().err
        assert err.count("submission snapshot not captured") == 1 and "simulated" in err
        st = state_mod.read_state(str(gitproject))
        assert st["turn"] == "reviewer" and st["phase"] == "feat-y"
        assert _rows(gitproject) == []
        conn = db.connect(project_dir=str(gitproject))
        try:
            kinds = [r[0] for r in conn.execute("SELECT kind FROM diagnostics")]
        finally:
            conn.close()
        assert "snapshot_failed" in kinds

    def test_reviewer_entries_do_not_snapshot(self, gitproject):
        cycle_mod._cli_init(["--phase", "feat-z", "--content", "r1"])
        cycle_mod._cli_add(["--phase", "feat-z", "--type", "plan", "--role", "reviewer",
                            "--action", "APPROVE", "--round", "1", "--content", "ok"])
        assert [r["action"] for r in _rows(gitproject)] == ["SUBMIT_FOR_REVIEW"]
