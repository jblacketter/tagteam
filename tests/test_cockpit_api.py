"""Phase 34 tests: cockpit payload builders (`tagteam.cockpit_api`) —
now / rounds / interjections / briefs / brief-current rule / usage series +
rate-limit signal / scope-diff per-file caps / tail / SSE signature — and
the write wrappers (`run_action`, `cli_preview`) over the Phase 32/33
command functions. No HTTP here (see test_server_cockpit.py)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tagteam import cockpit_api as capi
from tagteam import controls, db, headless as h, procs
from tagteam import cycle as cycle_mod
from tagteam import state as state_mod
from tagteam import watcher as watcher_mod

from tests.test_headless import project, fake_path, _init_cycle  # noqa: F401
from tests.test_controls import needs_proc_inspection  # noqa: F401


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=False).stdout


def _gitify(root: Path) -> None:
    (root / ".gitignore").write_text(".tagteam/\nhandoff-state.json\n")
    (root / "docs" / "handoffs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "handoffs" / ".gitkeep").write_text("")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")


def _escalate(project: Path, phase="feat-x", how="ESCALATE"):
    _init_cycle(project, phase=phase)
    cycle_mod.add_round(phase, "plan", "reviewer", how, 1, f"{how} because reasons",
                        str(project), updated_by="Codex")


# ---------------------------------------------------------------------------
# /api/now
# ---------------------------------------------------------------------------

class TestNow:
    def test_empty_project_shape(self, tmp_path, monkeypatch):
        monkeypatch.setattr(state_mod, "_cached_project_root", None, raising=False)
        (tmp_path / "tagteam.yaml").write_text("agents:\n  lead:\n    name: A\n  reviewer:\n    name: B\n")
        n = capi.now_payload(tmp_path)
        assert set(n) >= {"ts", "state", "cycle", "owed", "inflight", "paused", "watcher",
                          "briefer_enabled", "agents", "pending_notes", "project_dir"}
        assert n["owed"] is None and n["inflight"] is None and n["paused"] is None
        assert n["agents"] == {"lead": "A", "reviewer": "B"}
        assert n["briefer_enabled"] is False
        assert n["cycle"] is None
        assert n["gatekeeper"] == {"enabled": False, "last": None, "on": ["impl"]}   # Phase 38, opt-in

    def test_no_config_at_all(self, tmp_path):
        n = capi.now_payload(tmp_path)
        assert n["agents"] == {"lead": None, "reviewer": None}
        assert n["state"] == {}

    def test_owed_and_pause_and_inflight(self, project):
        _init_cycle(project)  # reviewer owed
        n = capi.now_payload(project)
        assert n["owed"]["role"] == "reviewer" and n["owed"]["agent"] == "Codex"
        assert n["owed"]["age_s"] is not None and n["owed"]["age_s"] >= 0
        assert n["cycle"]["state"] == "in-progress"
        controls.pause_command(["--reason", "hold it", "--by", "jack"], project_root=project)
        h.turns_dir(project).mkdir(parents=True, exist_ok=True)
        h.inflight_path(project).write_text(json.dumps({
            "stem": "s1", "pid": 999999, "watcher_pid": os.getpid(), "provider": "claude",
            "agent": "Codex", "role": "reviewer", "started_at": h._now_iso(),
            "log_path": str(h.turns_dir(project) / "s1.log"),
            "events_path": str(h.turns_dir(project) / "s1.events.jsonl")}))
        n = capi.now_payload(project)
        assert n["paused"]["reason"] == "hold it" and n["paused"]["age_s"] >= 0
        assert n["inflight"]["stem"] == "s1" and n["inflight"]["age_s"] >= 0
        assert n["inflight"]["pid_alive"] is False
        # the inflight watcher pid (this process, no ident recorded) counts as a live watcher
        assert n["watcher"]["running"] is True and n["watcher"]["pid"] == os.getpid()
        assert n["watcher"]["source"] == "inflight"

    def test_pending_notes_count_scoped(self, project):
        _init_cycle(project)
        controls.interject_command(["a"], project_root=project)
        controls.interject_command(["b", "--to", "lead"], project_root=project)
        n = capi.now_payload(project)
        assert n["pending_notes"] == 2
        conn = db.connect(project_dir=str(project))
        try:
            db.retire_interjection(conn, 1, by="x", ts="t")
        finally:
            conn.close()
        assert capi.now_payload(project)["pending_notes"] == 1


# ---------------------------------------------------------------------------
# rounds / interjections / briefs
# ---------------------------------------------------------------------------

class TestRoundsAndNotes:
    def test_rounds_payload_has_entries_rulings_interjections(self, project):
        _init_cycle(project)
        controls.interject_command(["note r1"], project_root=project)
        cycle_mod.add_round("feat-x", "plan", "reviewer", "REQUEST_CHANGES", 1, "fix",
                            str(project), updated_by="Codex")
        p = capi.rounds_payload(project, "feat-x", "plan")
        assert p["rounds"][0]["round"] == 1
        r = p["rounds"][0]
        assert [e["action"] for e in r["entries"]] == ["SUBMIT_FOR_REVIEW", "REQUEST_CHANGES"]
        assert r["rulings"] == []
        assert [i["note"] for i in r["interjections"]] == ["note r1"]
        assert "html" in p

    def test_rounds_payload_missing_cycle(self, project):
        assert capi.rounds_payload(project, "nope", "plan") == {"rounds": [], "html": ""}

    def test_interjections_payload_status(self, project):
        _init_cycle(project)
        controls.interject_command(["a"], project_root=project)
        controls.interject_command(["b"], project_root=project)
        conn = db.connect(project_dir=str(project))
        try:
            db.retire_interjection(conn, 2, by="x", ts="t")
            db.mark_interjections_delivered(conn, [1], role="reviewer", round_=1, stem="s", ts="t")
        finally:
            conn.close()
        p = capi.interjections_payload(project, "feat-x", "plan")
        assert [i["status"] for i in p["interjections"]] == ["delivered", "retired"]
        assert p["pending"] == 0
        # unscoped notes (no cycle owed) are included too
        cycle_mod.add_round("feat-x", "plan", "reviewer", "APPROVE", 1, "ok", str(project),
                            updated_by="Codex")
        controls.interject_command(["c"], project_root=project)
        p = capi.interjections_payload(project, "feat-x", "plan")
        assert [i["note"] for i in p["interjections"]] == ["a", "b", "c"]
        assert p["pending"] == 1

    def test_briefs_payload_strips_content(self, project):
        _escalate(project)
        conn = db.connect(project_dir=str(project))
        try:
            rid, _ = db.claim_brief(conn, ts="t", phase="feat-x", cycle_type="plan", round_=1,
                                    cycle_state="escalated", event_key="k", kind="manual",
                                    runner_pid=1, runner_ident="x")
            db.finish_brief(conn, rid, status="ok", ts="t2", content="## Positions\nx", path="/p")
        finally:
            conn.close()
        p = capi.briefs_payload(project, "feat-x", "plan")
        assert p["briefs"][0]["has_content"] is True and "content" not in p["briefs"][0]
        full = capi.brief_payload(project, rid)
        assert full["content"].startswith("## Positions")
        assert capi.brief_payload(project, 999) is None

    def test_brief_current_rule(self, project):
        _init_cycle(project)
        p = capi.brief_current_payload(project, "feat-x", "plan")
        assert p["event"] is None and "not escalated" in p["reason"]
        cycle_mod.add_round("feat-x", "plan", "reviewer", "ESCALATE", 1, "stuck", str(project),
                            updated_by="Codex")
        p = capi.brief_current_payload(project, "feat-x", "plan")
        assert p["event"]["cycle_state"] == "escalated" and p["event"]["content"] == "stuck"
        assert p["brief"] is None and p["attempts"] == []
        key = p["event"]["event_key"]
        conn = db.connect(project_dir=str(project))
        try:
            rid, _ = db.claim_brief(conn, ts="t", phase="feat-x", cycle_type="plan", round_=1,
                                    cycle_state="escalated", event_key=key, kind="auto",
                                    runner_pid=1, runner_ident="x")
            db.finish_brief(conn, rid, status="failed", ts="t2", reason="boom")
        finally:
            conn.close()
        p = capi.brief_current_payload(project, "feat-x", "plan")
        assert p["brief"] is None and [a["status"] for a in p["attempts"]] == ["failed"]
        conn = db.connect(project_dir=str(project))
        try:
            rid2, _ = db.claim_brief(conn, ts="t", phase="feat-x", cycle_type="plan", round_=1,
                                     cycle_state="escalated", event_key=key, kind="manual",
                                     runner_pid=1, runner_ident="x")
            db.finish_brief(conn, rid2, status="ok", ts="t3", content="## Positions", path="/x")
            # a brief for ANOTHER event must never be selected
            rid3, _ = db.claim_brief(conn, ts="t", phase="feat-x", cycle_type="plan", round_=1,
                                     cycle_state="escalated", event_key="other", kind="manual",
                                     runner_pid=1, runner_ident="x")
            db.finish_brief(conn, rid3, status="ok", ts="t4", content="wrong", path="/y")
        finally:
            conn.close()
        p = capi.brief_current_payload(project, "feat-x", "plan")
        assert p["brief"]["id"] == rid2
        assert capi.brief_current_payload(project, None, None)["event"] is None


# ---------------------------------------------------------------------------
# usage
# ---------------------------------------------------------------------------

class TestUsage:
    def _rows(self, project):
        conn = db.connect(project_dir=str(project))
        try:
            db.add_usage(conn, ts="t1", phase="feat-x", type="plan", round=1, role="reviewer",
                         agent="Codex", provider="codex", status="ok", input_tokens=10,
                         output_tokens=5, cost_usd=None, duration_ms=1000)
            db.add_usage(conn, ts="t2", phase="feat-x", type="plan", round=2, role="lead",
                         agent="Claude", provider="claude", status="ok", input_tokens=20,
                         output_tokens=7, cache_read_tokens=100, cost_usd=0.5, duration_ms=2000)
            db.add_usage(conn, ts="t3", phase="other", type="impl", round=1, role="briefer",
                         agent="briefer", provider="claude", status="timeout")
            db.upsert_rate_limit(conn, provider="claude", kind="five_hour", status="allowed",
                                 resets_at="2099-01-01T00:00:00+00:00", payload={"x": 1}, ts="t3")
        finally:
            conn.close()

    def test_usage_payload_shape_and_filters(self, project):
        self._rows(project)
        p = capi.usage_payload(project)
        assert p["totals"]["turns"] == 3
        assert set(p["by_role"]) == {"reviewer", "lead", "briefer"}
        assert "Claude (lead)" in p["by_agent"]
        assert [s["round"] for s in p["series"]] == [1, 2, 1]
        assert p["series"][1]["input"] == 20 and p["series"][1]["cache_read"] == 100
        assert p["rate_limits"][0]["kind"] == "five_hour" and p["rate_limits"][0]["payload"] == {"x": 1}
        assert p["rate_limits"][0]["resets_in_s"] > 0
        p = capi.usage_payload(project, phase="feat-x", ctype="plan")
        assert p["totals"]["turns"] == 2
        p = capi.usage_payload(project, phase="feat-x", ctype="plan", role="lead")
        assert p["totals"]["turns"] == 1 and p["filter"]["role"] == "lead"

    def test_usage_payload_empty(self, tmp_path):
        p = capi.usage_payload(tmp_path)
        assert p["totals"]["turns"] == 0 and p["series"] == [] and p["rate_limits"] == []


# ---------------------------------------------------------------------------
# scope diff
# ---------------------------------------------------------------------------

class TestScopeDiff:
    def _cycle_with_changes(self, project):
        _gitify(project)
        _init_cycle(project)                      # baseline = HEAD, clean-ish
        (project / "src").mkdir()
        (project / "src" / "a.py").write_text("print(1)\nprint(2)\n")     # untracked
        (project / "tagteam.yaml").write_text((project / "tagteam.yaml").read_text() + "# x\n")  # modified tracked
        (project / "bin.dat").write_bytes(b"\x00\x01\x02\xff\x00")         # binary untracked
        _git(project, "add", "src/a.py")
        _git(project, "commit", "-qm", "add a")                            # committed since baseline
        (project / "src" / "a.py").write_text("print(1)\nprint(3)\n")     # + uncommitted edit

    def test_per_file_structure(self, project):
        self._cycle_with_changes(project)
        p = capi.scope_diff_payload(project, "feat-x", "plan")
        assert p["error"] is None
        assert p["paths"] == ["bin.dat", "src/a.py", "tagteam.yaml"]
        by = {f["path"]: f for f in p["files"]}
        a = by["src/a.py"]
        assert a["binary"] is False and a["additions"] == 2 and a["deletions"] == 0
        assert "+print(3)" in a["patch"] and "+print(1)" in a["patch"]   # committed + working tree vs baseline
        y = by["tagteam.yaml"]
        assert y["status"] == "modified" and "+# x" in y["patch"]
        b = by["bin.dat"]
        assert b["binary"] is True and b["patch"] is None and b["status"] == "untracked"
        assert p["truncated"] is False and p["omitted_files"] == 0
        # matches the CLI's path list exactly
        cli = cycle_mod.compute_scope_diff("feat-x", "plan", str(project))["paths"]
        assert cli == p["paths"]

    def test_caps_bytes_and_files(self, project):
        self._cycle_with_changes(project)
        p = capi.scope_diff_payload(project, "feat-x", "plan", max_bytes=40)
        assert p["truncated"] is True
        sizes = [f["bytes"] for f in p["files"] if f["patch"] is not None]
        assert any(f["truncated"] for f in p["files"])
        assert p["bytes"] <= 40
        p = capi.scope_diff_payload(project, "feat-x", "plan", max_files=1)
        assert len(p["files"]) == 1 and p["omitted_files"] == 2 and p["truncated"] is True
        assert p["paths"] == ["bin.dat", "src/a.py", "tagteam.yaml"]  # the full list is still reported

    def test_untracked_directories_expand_to_files_and_artifacts_filtered(self, project):
        """Reviewer r1 #2: a new package shows up in `git status --porcelain` as
        a collapsed `newpkg/`; the cockpit must show its files, and never
        Tagteam's own `.tagteam/` / `docs/handoffs/` bookkeeping."""
        # No .gitignore on purpose: `.tagteam/` and `docs/` collapse as untracked dirs.
        _git(project, "init", "-q")
        _git(project, "config", "user.email", "t@example.com")
        _git(project, "config", "user.name", "T")
        (project / "keep.txt").write_text("k\n")
        _git(project, "add", "keep.txt", "tagteam.yaml")
        _git(project, "commit", "-qm", "init")
        _init_cycle(project)                            # writes docs/handoffs/*, .tagteam/*
        (project / "newpkg").mkdir()
        (project / "newpkg" / "a.py").write_text("print('a')\n")
        (project / "newpkg" / "b.py").write_text("print('b')\nprint('bb')\n")
        (project / "newpkg" / "img.bin").write_bytes(b"\x00\x01\xff\x00")
        (project / "docs" / "phases").mkdir(parents=True)
        (project / "docs" / "phases" / "feat-x.md").write_text("# plan\n")
        cli = cycle_mod.compute_scope_diff("feat-x", "plan", str(project))["paths"]
        assert cli == [".tagteam/", "docs/", "newpkg/"]  # CLI output unchanged (collapsed dirs)
        p = capi.scope_diff_payload(project, "feat-x", "plan")
        assert p["paths"] == cli
        assert p["file_paths"] == ["docs/phases/feat-x.md", "newpkg/a.py", "newpkg/b.py", "newpkg/img.bin"]
        by = {f["path"]: f for f in p["files"]}
        assert not any(k.startswith(".tagteam/") or k.startswith("docs/handoffs/") for k in by)
        assert by["newpkg/a.py"]["status"] == "untracked" and by["newpkg/a.py"]["additions"] == 1
        assert "+print('a')" in by["newpkg/a.py"]["patch"]
        assert by["newpkg/b.py"]["additions"] == 2 and by["newpkg/b.py"]["deletions"] == 0
        assert by["newpkg/img.bin"]["binary"] is True and by["newpkg/img.bin"]["patch"] is None
        assert by["docs/phases/feat-x.md"]["status"] == "untracked" and "+# plan" in by["docs/phases/feat-x.md"]["patch"]

    def test_statuses_added_modified_deleted(self, project):
        self._cycle_with_changes(project)               # src/a.py committed since baseline (+ edited)
        gone = project / h.SKILL_RELPATH                  # existed at baseline
        n_lines = len(gone.read_text(encoding="utf-8").splitlines())
        gone.unlink()                                     # deleted in the working tree (unstaged)
        (project / "docs" / "handoffs" / ".gitkeep").unlink()   # deleted tagteam artifact → filtered
        p = capi.scope_diff_payload(project, "feat-x", "plan")
        by = {f["path"]: f for f in p["files"]}
        assert by["src/a.py"]["status"] == "added"          # absent from the baseline tree
        assert by["tagteam.yaml"]["status"] == "modified"
        assert by["bin.dat"]["status"] == "untracked" and by["bin.dat"]["binary"] is True
        key = str(h.SKILL_RELPATH).replace(os.sep, "/")
        assert by[key]["status"] == "deleted" and by[key]["deletions"] == n_lines
        assert "-# Skill: /tagteam:handoff" in by[key]["patch"]
        # a staged deletion is still "deleted"
        _git(project, "add", "-A"); _git(project, "commit", "-qm", "rm skill")
        by = {f["path"]: f for f in capi.scope_diff_payload(project, "feat-x", "plan")["files"]}
        assert by[key]["status"] == "deleted" and by[key]["deletions"] == n_lines
        assert by["bin.dat"]["status"] == "added" and by["bin.dat"]["binary"] is True   # add -A committed it
        assert "docs/handoffs/.gitkeep" not in by

    def test_error_shape(self, project):
        p = capi.scope_diff_payload(project, "nope", "plan")
        assert p["error"] == "No cycle found: nope_plan" and p["files"] == []

    def test_cli_byte_identical(self, project, capsys):
        self._cycle_with_changes(project)
        rc = cycle_mod._cli_scope_diff(["--phase", "feat-x", "--type", "plan"])
        assert rc == 0
        assert capsys.readouterr().out == "bin.dat\nsrc/a.py\ntagteam.yaml\n"
        rc = cycle_mod._cli_scope_diff(["--phase", "nope", "--type", "plan"])
        assert rc == 1 and capsys.readouterr().out == "No cycle found: nope_plan\n"


# ---------------------------------------------------------------------------
# watcher liveness (reviewer r1 #1)
# ---------------------------------------------------------------------------

def _sleeper(cwd: Path, *extra_argv: str) -> subprocess.Popen:
    """A process with the console-script watcher shape — argv
    `python …/bin/tagteam watch --mode X` (no project path) — whose cwd is
    `cwd`: Tagteam's own launch shape."""
    fake_bin = cwd / ".fakebin"; fake_bin.mkdir(exist_ok=True)
    script = fake_bin / "tagteam"
    script.write_text("import time; time.sleep(120)\n")
    return subprocess.Popen([sys.executable, str(script), *extra_argv],
                            cwd=str(cwd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class TestWatcherLiveness:
    def test_pidfile_lifecycle(self, project):
        assert watcher_mod.read_pidfile(project) is None
        p = watcher_mod.write_pidfile(project, "iterm2")
        assert p is not None and p.name == "watcher.json"
        rec = watcher_mod.read_pidfile(project)
        assert rec["pid"] == os.getpid() and rec["mode"] == "iterm2" and rec["project_dir"] == str(project.resolve())
        assert watcher_mod.remove_pidfile(project, pid=12345) is False     # not ours → kept
        assert watcher_mod.read_pidfile(project) is not None
        assert watcher_mod.remove_pidfile(project) is True
        assert watcher_mod.read_pidfile(project) is None

    def test_pidfile_running_and_stale(self, project, monkeypatch):
        monkeypatch.setattr(procs, "list_processes", lambda *a, **k: [])   # no process scan
        # own pid, real identity → running via pidfile even with no process scan
        watcher_mod.write_pidfile(project, "headless")
        st = capi.watcher_status(project)
        assert st["running"] is True and st["pid"] == os.getpid() and st["mode"] == "headless"
        assert st["source"] == "pidfile" and st["stale_pidfile"] is False
        # dead pid → stale, not running
        watcher_mod.pidfile_path(project).write_text(json.dumps({"pid": 999999, "ident": "x", "mode": "tmux"}))
        st = capi.watcher_status(project)
        assert st["running"] is False and st["stale_pidfile"] is True and st["pid"] is None
        # identity mismatch (PID reuse) → stale, not running
        watcher_mod.pidfile_path(project).write_text(json.dumps({"pid": os.getpid(), "ident": "not-me:0", "mode": "tmux"}))
        monkeypatch.setattr(procs, "identity", lambda pid: "me:1")
        st = capi.watcher_status(project)
        assert st["running"] is False and st["stale_pidfile"] is True
        # now_payload surfaces the same record
        n = capi.now_payload(project)
        assert n["watcher"]["running"] is False and n["watcher"]["stale_pidfile"] is True

    @needs_proc_inspection
    @pytest.mark.skipif(sys.platform == "win32",
                        reason="process scan (/proc, ps, cwd) is POSIX-only; Windows binds via pidfile/inflight")
    def test_process_scan_binds_by_cwd_not_argv(self, project, tmp_path):
        """A `… tagteam watch --mode iterm2` process started from the project
        cwd (no project path on argv) is detected; the same shape started
        elsewhere is not."""
        other = tmp_path / "elsewhere"; other.mkdir()
        far = _sleeper(other, "watch", "--mode", "iterm2")
        try:
            st = capi.watcher_status(project)
            assert st["running"] is False, st
            near = _sleeper(project, "watch", "--mode", "iterm2")
            try:
                deadline = time.monotonic() + 5
                st = capi.watcher_status(project)
                while not st["running"] and time.monotonic() < deadline:
                    time.sleep(0.2); st = capi.watcher_status(project)
                diag = {"status": st, "near": near.pid, "near_cwd": procs.cwd(near.pid),
                        "project": str(project.resolve()),
                        "scan": procs.list_processes(capi.WATCH_ARGV_RE.pattern)}
                assert st["running"] is True and st["pid"] == near.pid, diag
                assert st["source"] == "process-scan" and st["mode"] == "iterm2"
                assert capi.now_payload(project)["watcher"]["pid"] == near.pid
            finally:
                near.kill(); near.wait()
        finally:
            far.kill(); far.wait()
        # gone → not running
        st = capi.watcher_status(project)
        assert st["running"] is False

    def test_watch_argv_shapes(self):
        rx = capi.WATCH_ARGV_RE
        assert rx.search("/Users/x/proj/.venv/bin/python -m tagteam watch --mode iterm2")
        assert rx.search("/usr/bin/python3.12 /Users/x/.local/bin/tagteam watch --mode headless --interval 5")
        assert rx.search("tagteam watch")
        assert rx.search(r"C:\py\python.exe -m tagteam watch --mode notify")
        # not watchers: a shell whose text mentions both words, `tagteam serve`, `tagteam tail`
        assert not rx.search('/bin/zsh -c pgrep -f "tagteam.*watch" && echo tagteam watching')
        assert not rx.search("/x/python -m tagteam serve --theme cockpit")
        assert not rx.search("/x/python -m tagteam tail --no-follow")
        assert not rx.search("/x/python -m pytest tests/test_watcher.py")

    def test_legacy_watcher_status_endpoint_unchanged(self, project):
        """The 0.10.0 `/api/watcher/status` helper keeps its argv-only rule
        (flag-off identity); the cockpit uses `watcher_status`."""
        from tagteam.server import _get_watcher_status
        watcher_mod.write_pidfile(project, "iterm2")
        assert set(_get_watcher_status(str(project))) == {"running", "pid"}


# ---------------------------------------------------------------------------
# tail + signature
# ---------------------------------------------------------------------------

class TestTailAndSignature:
    def test_tail_empty_then_latest(self, project):
        p = capi.tail_payload(project, 10)
        assert p["path"] is None and "No headless turn logs" in p["message"]
        d = h.turns_dir(project); d.mkdir(parents=True)
        (d / "a.log").write_text("\n".join(f"l{i}" for i in range(100)))
        p = capi.tail_payload(project, 5)
        assert p["lines"] == ["l95", "l96", "l97", "l98", "l99"] and p["inflight"] is False
        # capped
        assert len(capi.tail_payload(project, 10 ** 9)["lines"]) == 100

    def test_signature_changes_on_each_signal(self, project):
        _init_cycle(project)
        s0 = capi.events_signature(project); i0 = capi.signature_id(s0)
        assert set(s0) >= {"seq", "rounds", "interjections", "briefs", "usage", "paused", "inflight"}
        controls.interject_command(["n"], project_root=project)
        s1 = capi.events_signature(project); i1 = capi.signature_id(s1)
        assert i1 != i0 and s1["interjections"] == 1
        controls.pause_command([], project_root=project)
        i2 = capi.signature_id(capi.events_signature(project)); assert i2 != i1
        conn = db.connect(project_dir=str(project))
        try:
            db.add_usage(conn, ts="t", status="ok")
        finally:
            conn.close()
        i3 = capi.signature_id(capi.events_signature(project)); assert i3 != i2
        cycle_mod.add_round("feat-x", "plan", "reviewer", "REQUEST_CHANGES", 1, "x", str(project))
        i4 = capi.signature_id(capi.events_signature(project)); assert i4 != i3
        # unchanged → same id (inflight age excluded from the id)
        assert capi.signature_id(capi.events_signature(project)) == i4
        # a watcher pidfile appearing / its process dying is a change (strip liveness)
        watcher_mod.write_pidfile(project, "headless")
        s5 = capi.events_signature(project); i5 = capi.signature_id(s5)
        assert i5 != i4 and s5["watcher"] == {"pid": os.getpid(), "alive": True}
        watcher_mod.pidfile_path(project).write_text(json.dumps({"pid": 999999, "mode": "headless"}))
        s6 = capi.events_signature(project); i6 = capi.signature_id(s6)
        assert i6 != i5 and s6["watcher"]["alive"] is False
        # an in-flight pointer whose pid dies is a change too
        h.turns_dir(project).mkdir(parents=True, exist_ok=True)
        h.inflight_path(project).write_text(json.dumps({"stem": "s", "pid": os.getpid(), "started_at": h._now_iso()}))
        i7 = capi.signature_id(capi.events_signature(project)); assert i7 != i6
        h.inflight_path(project).write_text(json.dumps({"stem": "s", "pid": 999999, "started_at": h._now_iso()}))
        s8 = capi.events_signature(project); i8 = capi.signature_id(s8)
        assert i8 != i7 and s8["inflight"]["alive"] is False


# ---------------------------------------------------------------------------
# write wrappers
# ---------------------------------------------------------------------------

class TestActions:
    def test_pause_resume_by_web_user(self, project, monkeypatch):
        monkeypatch.setenv("TAGTEAM_ARBITER", "jack")
        r = capi.run_action("pause", {"reason": "from web"}, project)
        assert r["ok"] and "Paused" in r["message"] and r["cli"].startswith("tagteam pause")
        assert h.read_pause(project)["by"] == "web:jack" and h.read_pause(project)["reason"] == "from web"
        r = capi.run_action("resume", {}, project)
        assert r["ok"] and "Resumed" in r["message"] and h.read_pause(project) is None
        r = capi.run_action("resume", {}, project)
        assert not r["ok"] and "Not paused" in r["message"]

    def test_interject_and_retire(self, project):
        _init_cycle(project)
        r = capi.run_action("interject", {"note": "hi there", "to": "lead"}, project)
        assert r["ok"] and "Interjection #1" in r["message"]
        conn = db.connect(project_dir=str(project))
        try:
            row = db.get_interjections(conn)[0]
        finally:
            conn.close()
        assert row["note"] == "hi there" and row["target_role"] == "lead" and row["by"].startswith("web:")
        assert not capi.run_action("interject", {"note": ""}, project)["ok"]
        assert capi.run_action("interject", {"note": "x", "to": "nobody"}, project)["rc"] == 400
        r = capi.run_action("interject/retire", {"id": 1}, project)
        assert r["ok"] and "Retired" in r["message"]
        assert not capi.run_action("interject/retire", {"id": 1}, project)["ok"]
        assert capi.run_action("interject/retire", {"id": "x"}, project)["rc"] == 400

    def test_cancel_turn_nothing_inflight(self, project):
        r = capi.run_action("cancel-turn", {}, project)
        assert not r["ok"] and "Nothing in flight" in r["message"]

    def test_rule_paths(self, project):
        _init_cycle(project)
        r = capi.run_action("rule", {"ruling": "approve"}, project)
        assert not r["ok"] and "Nothing to rule on" in r["message"] and r["rc"] == 1
        assert capi.run_action("rule", {"ruling": "bogus"}, project)["rc"] == 400
        assert capi.run_action("rule", {"ruling": "request-changes"}, project)["rc"] == 400
        assert capi.run_action("rule", {"ruling": "approve", "to": "lead"}, project)["rc"] == 400
        cycle_mod.add_round("feat-x", "plan", "reviewer", "ESCALATE", 1, "stuck", str(project),
                            updated_by="Codex")
        r = capi.run_action("rule", {"ruling": "request-changes", "content": "try again"}, project)
        assert r["ok"] and "changes requested" in r["message"]
        st = cycle_mod.read_status("feat-x", "plan", str(project))
        assert st["state"] == "in-progress" and st["ready_for"] == "lead"
        conn = db.connect(project_dir=str(project))
        try:
            kinds = [json.loads(r["payload_json"]) for r in db._rows(conn.execute(
                "SELECT payload_json FROM diagnostics WHERE kind='arbiter_ruling'"))]
        finally:
            conn.close()
        assert kinds and kinds[-1]["by"].startswith("web:") and kinds[-1]["ruling"] == "request-changes"

    def test_rule_answer_defaults_to_reviewer(self, project):
        _init_cycle(project)
        cycle_mod.add_round("feat-x", "plan", "reviewer", "NEED_HUMAN", 1, "which db?", str(project),
                            updated_by="Codex")
        r = capi.run_action("rule", {"ruling": "answer", "content": "sqlite"}, project)
        assert r["ok"] and "reviewer's turn" in r["message"]
        st = state_mod.read_state(str(project))
        assert st["turn"] == "reviewer" and st["status"] == "ready"

    def test_brief_generate_when_disabled(self, project):
        _escalate(project)
        r = capi.run_action("brief/generate", {}, project)
        assert not r["ok"] and "not enabled" in r["message"]

    def test_unknown_action_and_preview(self, project, monkeypatch):
        assert capi.run_action("nope", {}, project)["rc"] == 400
        monkeypatch.setenv("TAGTEAM_ARBITER", "jack")
        assert capi.cli_preview("rule", {"ruling": "approve", "content": "fine by me"}) == \
            "tagteam rule approve --content 'fine by me' --by web:jack"
        assert capi.cli_preview("cancel-turn", {}) == "tagteam cancel-turn --by web:jack"
        assert capi.cli_preview("brief/generate", {}) == "tagteam brief --generate"
        with pytest.raises(ValueError):
            capi.cli_preview("rule", {"ruling": "answer"})

    def test_wrapper_never_raises(self, project, monkeypatch):
        def boom(args, project_root=None, out=None):
            raise RuntimeError("kaboom")
        monkeypatch.setattr(controls, "pause_command", boom)
        r = capi.run_action("pause", {}, project)
        assert not r["ok"] and "kaboom" in r["message"]


# ---------------------------------------------------------------------------
# Phase 68 — the headline: status_facts() (no database), inflight_liveness()
# over real marker shapes, and the first-match table in headline().

import itertools
from datetime import datetime, timedelta, timezone


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _marker_fields(**over):
    me = os.getpid()
    f = {"phase": "feat-x", "type": "impl", "round": 2, "role": "reviewer", "agent": "Codex", "provider": "codex",
         "stem": "feat-x_impl_r2_reviewer_x", "log_path": None, "events_path": None,
         "started_at": datetime.now(timezone.utc).isoformat(), "pid": None, "child_ident": None,
         "watcher_pid": me, "watcher_ident": procs.identity(me)}
    f.update(over)
    return f


class TestInflightLiveness:
    """Markers are written by the real slot writers (`claim_turn_slot`,
    `update_turn_slot`) with the field sets the gate and the engine use, then
    read back through `status_facts()`."""

    def _liveness(self, root):
        inf = capi.status_facts(root)["inflight"]
        return inf["liveness"], inf["pid_alive"]

    def test_a_running_gate_has_no_child_by_design(self, project):
        # gatekeeper.py: pid None for the whole run, watcher_pid = the process running the gate
        claim = h.claim_turn_slot(project, kind=h.SLOT_KIND_GATE, role="gatekeeper",
                                  fields=_marker_fields(role="gatekeeper", agent="gatekeeper", provider=None))
        try:
            assert self._liveness(project) == ("no-child", False)      # pid_alive is False here — and means nothing
            hl = capi.headline(capi.status_facts(project))
            assert (hl["state"], hl["text"]) == ("working", "Pre-check running · round 2")
        finally:
            h.release_turn_slot(claim)

    @pytest.mark.parametrize("kind", [h.SLOT_KIND_CYCLE, h.SLOT_KIND_PANEL])
    def test_before_the_child_is_spawned_then_running_then_finishing(self, project, kind):
        claim = h.claim_turn_slot(project, kind=kind, role="reviewer", fields=_marker_fields())
        try:
            assert self._liveness(project) == ("starting", False)
            assert capi.headline(capi.status_facts(project))["state"] == "working"
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            try:
                assert h.update_turn_slot(claim, pid=child.pid, child_ident=procs.identity(child.pid))
                assert self._liveness(project) == ("running", True)
            finally:
                child.kill(); child.wait()
            assert self._liveness(project) == ("finishing", False)        # child gone, live owner recording the result
            hl = capi.headline(capi.status_facts(project))
            assert hl["state"] == "working" and hl["tone"] == "working"
            if kind == h.SLOT_KIND_CYCLE:
                assert hl["text"] == "Codex's turn has ended — recording the result"
        finally:
            h.release_turn_slot(claim)

    def test_a_dead_owner_is_lost(self, project):
        claim = h.claim_turn_slot(project, kind=h.SLOT_KIND_CYCLE, role="lead",
                                  fields=_marker_fields(role="lead", agent="Claude", watcher_pid=_dead_pid(),
                                                        watcher_ident="gone", pid=os.getpid()))
        try:
            assert self._liveness(project)[0] == "lost"              # a live child does not save a dead runner
            hl = capi.headline(capi.status_facts(project))
            assert (hl["state"], hl["tone"]) == ("turn-lost", "danger")
            assert hl["text"] == "Claude's turn was abandoned — the process running it is gone"
        finally:
            h.release_turn_slot(claim)

    def test_pure_rules_for_legacy_markers_and_missing_evidence(self):
        L = capi.inflight_liveness
        assert L(None, child_alive=None, owner_gone=None) is None
        assert L({"pid": 5}, child_alive=False, owner_gone=False) == "lost"           # legacy: no runner pid, child dead
        assert L({"pid": 5}, child_alive=True, owner_gone=False) == "running"
        assert L({"pid": 5, "watcher_pid": 9}, child_alive=False, owner_gone=False) == "finishing"
        # no evidence is never promoted to a confirmed-dead sentence
        assert L({"pid": 5, "watcher_pid": 9}, child_alive=None, owner_gone=None) == "unknown"
        assert L({"pid": 5, "watcher_pid": 9}, child_alive=None, owner_gone=False) == "unknown"
        assert L({"pid": None, "watcher_pid": 9}, child_alive=None, owner_gone=None) == "starting"
        # an owner whose identity cannot be read is not gone (slot_owner_gone fails closed) → not lost
        m = _marker_fields(pid=os.getpid())
        with patch_identity(None):
            gone = h.slot_owner_gone(m)[0]
        assert gone is False and L(m, child_alive=True, owner_gone=gone) == "running"

    def test_unknown_liveness_reads_as_working_not_as_finishing(self):
        facts = _facts(inflight={"kind": "cycle", "role": "reviewer", "agent": "Codex", "round": 1, "type": "impl",
                                 "liveness": "unknown", "age_s": 12.0})
        assert capi.headline(facts)["text"] == "Codex is reviewing · round 1"


from contextlib import contextmanager


@contextmanager
def patch_identity(value):
    real = procs.identity
    procs.identity = lambda _pid: value
    try:
        yield
    finally:
        procs.identity = real


_NOWISH = datetime.now(timezone.utc).isoformat()


def _facts(*, status="ready", turn="reviewer", seq=7, inflight=None, paused=None, running=True, mode="iterm2",
           beat="fresh", dispatch=None, owed_age=30.0, **state):
    st = {"phase": "feat-x", "type": "impl", "round": 2, "status": status, "turn": turn, "seq": seq, **state}
    agents = {"lead": "Claude", "reviewer": "Codex"}
    owed = None
    if status in ("ready", "working") and turn in ("lead", "reviewer"):
        owed = {"role": turn, "agent": agents[turn], "since": None, "age_s": owed_age}
    return {"state": st, "config": {}, "agents": agents, "owed": owed, "inflight": inflight,
            "turn_kind": (inflight or {}).get("kind"), "paused": paused,
            "watcher": {"running": running, "mode": mode if running else None, "pid": 1 if running else None,
                        "beat": {"state": beat, "age_s": 400.0 if beat == "stale" else 3.0},
                        "last_dispatch": dispatch}}


def _sent(mode="iterm2", seq=7, kind="sent"):
    return {"kind": kind, "mode": mode, "seq": seq, "msg": "Sent to Codex"}


class TestHeadline:
    @pytest.mark.parametrize("facts, state, tone, text, age_of", [
        (_facts(status="escalated", turn=None, updated_at=_NOWISH), "needs-you", "attention", "Waiting on you — escalated", "owed"),
        (_facts(status="needs-human", turn=None, updated_at=_NOWISH, updated_by="Codex"), "needs-you", "attention",
         "Waiting on you — a question from Codex", "owed"),
        (_facts(status="escalated", turn=None, updated_at=_NOWISH, roadmap={"pause_reason": "blocked: p2 needs p1"}), "needs-you",
         "attention", "Waiting on you — roadmap paused: blocked: p2 needs p1", "owed"),
        (_facts(inflight={"kind": "cycle", "role": "lead", "agent": "Claude", "round": 1, "type": "impl",
                          "liveness": "running", "age_s": 62.0}), "working", "working",
         "Claude is implementing · round 1", "turn"),
        (_facts(inflight={"kind": "cycle", "role": "lead", "agent": "Claude", "round": 1, "type": "plan",
                          "liveness": "starting", "age_s": 1.0}), "working", "working",
         "Claude is working on the plan · round 1", "turn"),
        (_facts(inflight={"kind": "panel", "role": "reviewer", "round": 2, "liveness": "running", "age_s": 5.0}),
         "working", "working", "Review panel running · round 2", "turn"),
        (_facts(inflight={"kind": "briefer", "liveness": "running", "age_s": 5.0}), "working", "working",
         "Writing the decision brief", "turn"),
        (_facts(inflight={"kind": "conversation", "role": "lead", "agent": "Claude", "liveness": "running",
                          "age_s": 8.0}), "working", "working", "Claude is answering you", "turn"),
        (_facts(inflight={"kind": "gate", "role": "gatekeeper", "round": 3, "liveness": "lost", "age_s": 900.0}),
         "turn-lost", "danger", "The running pre-check was abandoned — the process running it is gone", "turn"),
        (_facts(paused={"by": "jack", "age_s": 40.0}), "paused", "attention", "Paused by jack — Codex's turn is held",
         "pause"),
        # a pause applied AFTER the turn was typed into the terminal holds future dispatch, not that turn
        (_facts(paused={"by": "jack", "age_s": 40.0}, dispatch=_sent()), "paused", "attention",
         "Paused by jack — Codex has its turn; further hand-offs are held", "pause"),
        (_facts(running=False), "watcher-off", "attention",
         "Waiting on Codex — the watcher is off, nothing will start its turn", "owed"),
        (_facts(running=False, dispatch=_sent()), "watcher-off", "attention",
         "Waiting on Codex — its turn was sent; the watcher has since stopped, so the next hand-off will not happen",
         "owed"),
        (_facts(beat="stale"), "stalled", "danger", "Stalled: Codex is owed a turn and the watcher has stopped looking",
         "beat"),
        (_facts(beat="stale", dispatch=_sent()), "watcher-stale", "attention",
         "Waiting on Codex — it has its turn; the watcher has stopped looking", "beat"),
        (_facts(mode="headless", beat="fresh"), "starting", "working", "Starting Codex's turn…", "owed"),
        (_facts(mode="headless", beat="in-turn"), "starting", "working", "Starting Codex's turn…", "owed"),
        (_facts(mode="headless", beat="none"), "waiting", "waiting",
         "Waiting on Codex — a headless watcher is running but has not reported in", "owed"),
        (_facts(mode="headless", beat="previous"), "waiting", "waiting",
         "Waiting on Codex — a headless watcher is running but has not reported in", "owed"),
        (_facts(mode="iterm2", dispatch=_sent()), "waiting", "waiting",
         "Waiting on Codex — its turn was sent to its terminal", "owed"),
        (_facts(mode="tmux"), "waiting", "waiting",
         "Waiting on Codex — the watcher will send its turn to its terminal", "owed"),
        (_facts(mode="notify", dispatch=_sent(mode="notify")), "waiting", "waiting",
         "Waiting on Codex — the watcher only notifies you; run the turn in Codex's session", "owed"),
        (_facts(mode=None), "waiting", "waiting", "Waiting on Codex — a watcher is running", "owed"),
        (_facts(mode="iterm2", owed_age=901.0), "waiting", "attention",
         "Waiting on Codex — the watcher will send its turn to its terminal", "owed"),
        (_facts(status="done", turn=None, result="approved"), "approved", "ok", "Approved — feat-x, implementation", None),
        (_facts(status="done", turn=None, result="roadmap-complete"), "approved", "ok", "Roadmap complete", None),
        (_facts(status="aborted", turn=None), "aborted", "idle", "Aborted — feat-x, implementation", None),
        ({"state": {}, "agents": {}, "owed": None, "inflight": None, "paused": None, "watcher": {}}, "idle", "idle",
         "Nothing in progress", None),
    ])
    def test_rows(self, facts, state, tone, text, age_of):
        hl = capi.headline(facts)
        assert (hl["state"], hl["tone"], hl["text"], hl["age_of"]) == (state, tone, text, age_of)
        assert set(hl) == {"state", "tone", "text", "age_s", "age_of", "role", "agent"}

    def test_priorities(self):
        busy = {"kind": "cycle", "role": "reviewer", "agent": "Codex", "round": 2, "type": "impl",
                "liveness": "running", "age_s": 9.0}
        # a pause holds future dispatch; it does not stop a running turn
        assert capi.headline(_facts(inflight=busy, paused={"by": "jack", "age_s": 1.0}))["state"] == "working"
        # a stale beat while this watcher runs the turn is `in-turn`, and in any case in-flight wins
        assert capi.headline(_facts(inflight=busy, beat="stale"))["state"] == "working"
        # an old beat file says nothing about a watcher that is not running
        assert capi.headline(_facts(running=False, beat="stale"))["state"] == "watcher-off"
        # needs-you outranks everything
        assert capi.headline(_facts(status="escalated", turn=None, inflight=busy))["state"] == "needs-you"
        # the cockpit-only launch row: below in-flight, above the owed rows
        launch = {"status": "pending", "phase": "feat-x", "type": "impl", "age_s": 2.0}
        assert capi.headline(_facts(inflight=busy), launch)["state"] == "working"
        hl = capi.headline(_facts(status="done", turn=None, result="approved"), launch)
        assert (hl["state"], hl["text"]) == ("launching", "Starting feat-x, implementation — Claude is on it")
        assert capi.headline(_facts(status="done", turn=None, result="approved"),
                             {"status": "failed"})["state"] == "approved"

    def test_delivered_means_typed_into_a_terminal_for_this_seq(self):
        assert capi.turn_delivered(_facts(dispatch=_sent()))
        for ev in (_sent(mode="notify"), _sent(mode="headless"), _sent(seq=6), _sent(kind="turn"),
                   _sent(kind="send-failed"), {"kind": "sent", "mode": "iterm2"}, None):
            assert not capi.turn_delivered(_facts(dispatch=ev)), ev
        assert not capi.turn_delivered(_facts(seq=None, dispatch={"kind": "sent", "mode": "iterm2", "seq": None}))

    def test_an_owed_turn_never_reads_as_idle_and_always_names_the_agent(self):
        """The owed-turn rows are exhaustive: every combination of pause ×
        watcher × beat × mode × delivered lands on a sentence about the agent."""
        seen = set()
        for paused, running, beat, mode, delivered, turn in itertools.product(
                (None, {"by": "jack", "age_s": 5.0}), (True, False),
                ("none", "previous", "fresh", "in-turn", "stale"),
                ("headless", "iterm2", "terminal", "tmux", "notify", None), (False, True), ("lead", "reviewer")):
            facts = _facts(turn=turn, paused=paused, running=running, mode=mode, beat=beat,
                           dispatch=_sent(mode=mode or "iterm2") if delivered else None)
            hl = capi.headline(facts)
            seen.add(hl["state"])
            who = "Claude" if turn == "lead" else "Codex"
            assert hl["state"] not in ("idle", "approved", "aborted", "working", "launching"), (facts, hl)
            assert who in hl["text"] and hl["agent"] == who and hl["role"] == turn, (facts, hl)
            assert hl["age_s"] is not None and hl["age_of"] in ("owed", "pause", "beat")
        assert seen == {"paused", "watcher-off", "stalled", "watcher-stale", "starting", "waiting"}

    def test_the_text_never_carries_an_age(self):
        import re
        ages = re.compile(r"\b\d+\s*(s|m|h|min|sec)\b|\bago\b")
        for facts in (_facts(beat="stale"), _facts(running=False), _facts(paused={"by": "j", "age_s": 400.0}),
                      _facts(inflight={"kind": "gate", "round": 3, "liveness": "no-child", "age_s": 431.0})):
            assert not ages.search(capi.headline(facts)["text"]), capi.headline(facts)["text"]


class TestStatusFacts:
    def test_no_database_is_opened_and_now_payload_uses_the_same_facts(self, project, monkeypatch):
        _init_cycle(project)
        now = capi.now_payload(project)                         # the cockpit's payload, database and all
        real = db.connect
        monkeypatch.setattr(db, "connect", lambda *a, **k: pytest.fail("status_facts opened the database"))
        facts = capi.status_facts(project)
        monkeypatch.setattr(db, "connect", real)
        assert facts["owed"]["role"] == "reviewer" and facts["owed"]["agent"] == "Codex"
        assert facts["watcher"]["beat"]["text"] == "never (no heartbeat recorded)"
        hl = capi.headline(facts)
        assert now["headline"]["text"] == hl["text"] == "Waiting on Codex — the watcher is off, nothing will start its turn"
        # every key the cockpit already relied on is still there
        assert {"ts", "state", "cycle", "owed", "inflight", "turn_kind", "launch", "last_turn", "paused", "watcher",
                "briefer_enabled", "gatekeeper", "agents", "pending_notes", "project_dir"} <= set(now)
        assert {"running", "pid", "mode", "source", "stale_pidfile", "beat", "last_event", "last_dispatch"} \
            <= set(now["watcher"])

    def test_real_dispatch_record_makes_the_turn_delivered(self, project):
        from tagteam import watchlog
        _init_cycle(project)
        (project / ".tagteam").mkdir(exist_ok=True)
        seq = state_mod.read_state(str(project))["seq"]
        sink = watchlog.Sink(project, "iterm2", every_s=10)
        sink.event(">> Codex's turn", "turn", seq=seq); sink.event("   Sent to Codex: /handoff", "sent", seq=seq)
        facts = capi.status_facts(project)
        assert facts["watcher"]["last_dispatch"]["kind"] == "sent" and capi.turn_delivered(facts)
        assert capi.headline(facts)["text"].startswith("Waiting on Codex — its turn was sent; the watcher has since stopped")
