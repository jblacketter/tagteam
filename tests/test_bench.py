"""Phase 56 — review bench (tagteam/bench.py)."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tagteam import bench as b
from tagteam import cycle as cycle_mod
from tagteam import db
from tagteam import report as report_mod
from tagteam import state as state_mod

FAKE_CLAUDE = r'''
import json, os, re, sys
prompt = sys.stdin.read()
cap = os.environ.get("FAKE_BENCH_CAPTURE")
if cap:
    with open(cap, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(),
                            "read_only": os.environ.get("TAGTEAM_READ_ONLY"),
                            "bench": os.environ.get("TAGTEAM_BENCH"), "prompt": prompt}) + "\n")
print(json.dumps({"type": "system", "subtype": "init", "model": "fake-sonnet", "session_id": "s1"}))
m = re.search(r"exactly this path and stop:\s*\n\s*(\S+)", prompt)
verdict = os.environ.get("FAKE_BENCH_VERDICT", "APPROVE")
seq = os.environ.get("FAKE_BENCH_VERDICT_SEQ")
if seq and cap:
    calls = len(open(cap, encoding="utf-8").read().splitlines())
    verdict = seq.split(",")[min(calls, len(seq.split(","))) - 1]
if m and verdict != "none":
    body = {"verdict": verdict, "summary": "fake"}
    if verdict == "REQUEST_CHANGES":
        body["findings"] = [{"title": "bug", "severity": "blocker"}, {"title": "gap", "severity": "major"}]
    with open(m.group(1), "w", encoding="utf-8") as f:
        json.dump(body, f)
print(json.dumps({"type": "result", "subtype": "success", "num_turns": 2, "session_id": "s1",
                  "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 900,
                            "cache_creation_input_tokens": 0}}))
'''


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                           *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    script = bin_dir / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    if sys.platform == "win32":
        (bin_dir / "claude.cmd").write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n')
    else:
        exe = bin_dir / "claude"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    cap = tmp_path / "capture.jsonl"
    monkeypatch.setenv("FAKE_BENCH_CAPTURE", str(cap))
    monkeypatch.setenv("FAKE_BENCH_VERDICT", "APPROVE")
    for k in ("TAGTEAM_READ_ONLY", "CLAUDECODE"):
        monkeypatch.delenv(k, raising=False)
    return cap


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "tagteam.yaml").write_text("agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\n",
                                       encoding="utf-8")
    (root / "docs" / "handoffs").mkdir(parents=True)
    (root / "docs" / "phases").mkdir(parents=True)
    (root / "docs" / "phases" / "feat-x.md").write_text("# Feat X plan\n", encoding="utf-8")
    (root / ".gitignore").write_text(".tagteam/*\n!/.tagteam/legacy/\nhandoff-state.json\n", encoding="utf-8")
    (root / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    monkeypatch.setattr(state_mod, "_cached_project_root", None, raising=False)
    monkeypatch.chdir(root)
    return root


def _cli_add(role, action, rnd, content, phase="feat-x", ctype="impl"):
    assert cycle_mod._cli_add(["--phase", phase, "--type", ctype, "--role", role, "--action", action,
                               "--round", str(rnd), "--content", content]) == 0


def _amended_cycle(root):
    """impl round 1: submit → change + new file → AMEND → REQUEST_CHANGES;
    round 2: submit → APPROVE. Snapshots captured by the CLI."""
    assert cycle_mod._cli_init(["--phase", "feat-x", "--type", "impl", "--content", "r1"]) == 0
    (root / "app.py").write_text("print('amended')\n", encoding="utf-8")
    (root / "added.py").write_text("NEW = 1\n", encoding="utf-8")
    _cli_add("lead", "AMEND", 1, "amended app.py")
    _cli_add("reviewer", "REQUEST_CHANGES", 1, "HELD-OUT-VERDICT-R1")
    (root / "app.py").write_text("print('v2')\n", encoding="utf-8")
    _cli_add("lead", "SUBMIT_FOR_REVIEW", 2, "r2")
    _cli_add("reviewer", "APPROVE", 2, "HELD-OUT-VERDICT-R2")


def _out(fn, args, root):
    buf = io.StringIO()
    rc = fn(args, str(root), buf)
    return rc, buf.getvalue()


# ---------------------------------------------------------------------------

class TestCells:
    def test_valid_cell(self):
        c = b.parse_cell("claude:sonnet:high")
        assert c.label == "claude:sonnet:high" and c.user_args == ["--model", "sonnet", "--effort", "high"]

    @pytest.mark.parametrize("spec,msg", [("codex:gpt:high", "codex cells are not supported yet"),
                                          ("claude:sonnet", "provider:model:effort"),
                                          ("gemini:x:y", "unknown provider"),
                                          ("claude:--resume:high", "cell")])
    def test_refused(self, spec, msg):
        with pytest.raises(b.BenchError, match=msg):
            b.parse_cell(spec)


class TestRoundSelection:
    def test_amend_is_reviewed_version(self, project):
        _amended_cycle(project)
        rounds = b.benchable_rounds(str(project))
        r1 = b.find_round(rounds, "feat-x", "impl", 1)
        entries = cycle_mod.read_rounds("feat-x", "impl", str(project))
        amend = next(e for e in entries if e["action"] == "AMEND")
        assert r1.recorded_verdict == "REQUEST_CHANGES" and r1.amended == 1
        assert r1.version_ts == amend["ts"]
        assert r1.pre_verdict[-1]["action"] == "AMEND"
        conn = db.connect(project_dir=str(project))
        try:
            rows = b.select_rows(str(project), conn)
        finally:
            conn.close()
        assert [(x["round"], x["recorded"], x["provenance"]) for x in rows] == [
            ("feat-x:impl:1", "REQUEST_CHANGES", "snapshot"), ("feat-x:impl:2", "APPROVE", "snapshot")]

    def test_missing_amend_snapshot_is_none(self, project):
        _amended_cycle(project)
        conn = db.connect(project_dir=str(project))
        try:
            conn.execute("DELETE FROM submission_snapshots WHERE action='AMEND'"); conn.commit()
            rows = {x["round"]: x for x in b.select_rows(str(project), conn)}
        finally:
            conn.close()
        assert rows["feat-x:impl:1"]["provenance"] == "none"
        rc, out = _out(b.run_command, ["--round", "feat-x:impl:1", "--cell", "claude:sonnet:high"], project)
        assert rc == 1 and "no snapshot for the reviewed version" in out

    def test_unreviewed_rounds_excluded(self):
        entries = [
            {"round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW", "ts": "t1"},
            {"round": 1, "role": "gatekeeper", "action": "GATE_BOUNCE", "ts": "t2"},
            {"round": 2, "role": "lead", "action": "SUBMIT_FOR_REVIEW", "ts": "t3"},
            {"round": 2, "role": "reviewer", "action": "APPROVE", "ts": "t4"},
        ]
        rounds = b.rounds_in_cycle("p", "impl", entries)
        assert [(r.round, r.version_ts) for r in rounds] == [(2, "t3")]

    def test_select_command_text(self, project):
        _amended_cycle(project)
        rc, out = _out(b.select_command, ["--verdict", "REQUEST_CHANGES"], project)
        assert rc == 0 and "feat-x:impl:1" in out and "(amended 1)" in out and "feat-x:impl:2" not in out


class TestRunPlanning:
    def test_dry_run_spawns_and_writes_nothing(self, project, fake_claude):
        _amended_cycle(project)
        dbfile = project / ".tagteam" / "tagteam.db"
        before = dbfile.stat().st_mtime_ns, dbfile.stat().st_size
        rc, out = _out(b.run_command, ["--round", "feat-x:impl:1", "--round", "feat-x:impl:2",
                                       "--cell", "claude:sonnet:high", "--cell", "claude:opus:medium"], project)
        assert rc == 0
        assert "4 pair(s), 0 already done, 4 to run" in out and "dry run: nothing spawned" in out
        assert "estimate unknown" in out and "$" not in out
        assert not fake_claude.exists()
        assert (dbfile.stat().st_mtime_ns, dbfile.stat().st_size) == before

    def test_estimate_is_proxy_labelled(self, project, fake_claude):
        _amended_cycle(project)
        conn = db.connect(project_dir=str(project))
        try:
            db.add_usage(conn, ts="2026-09-14T00:00:00+00:00", status="ok", role="reviewer",
                         input_tokens=1000, cache_read_tokens=9000)
        finally:
            conn.close()
        rc, out = _out(b.run_command, ["--round", "feat-x:impl:1", "--cell", "claude:sonnet:high"], project)
        assert "≈ 1 × 10k input tokens" in out and "proxy: past reviewer turns" in out

    def test_max_turns_cap(self, project, fake_claude):
        _amended_cycle(project)
        rc, out = _out(b.run_command, ["--round", "feat-x:impl:1", "--round", "feat-x:impl:2",
                                       "--cell", "claude:sonnet:high", "--max-turns", "1", "--yes"], project)
        assert rc == 1 and "exceeds --max-turns 1" in out
        assert not fake_claude.exists()

    def test_asserted_impl_needs_base(self, project, fake_claude):
        _amended_cycle(project)
        rc, out = _out(b.run_command, ["--round", "feat-x:impl:1@HEAD", "--cell", "claude:sonnet:high"], project)
        assert rc == 1 and "needs @BASE..REV" in out

    def test_codex_cell_refused_before_spawn(self, project, fake_claude):
        _amended_cycle(project)
        rc, out = _out(b.run_command, ["--round", "feat-x:impl:1", "--cell", "codex:gpt-5:high", "--yes"], project)
        assert rc == 1 and "codex cells are not supported yet" in out
        assert not fake_claude.exists()


def _live_fingerprint(root):
    return ((root / "docs" / "handoffs" / "feat-x_impl_rounds.jsonl").read_bytes(),
            (root / "handoff-state.json").read_bytes())


class TestReplay:
    def test_run_pair_isolation_scrub_and_results(self, project, fake_claude, monkeypatch):
        _amended_cycle(project)
        # Every supported copy of the target cycle's outcome, in the submitted tree.
        legacy = project / ".tagteam" / "legacy"
        legacy.mkdir(parents=True)
        full = (project / "docs" / "handoffs" / "feat-x_impl_rounds.jsonl").read_text()
        (legacy / "feat-x_impl_rounds.jsonl").write_text(full)
        (legacy / "feat-x_impl_status.json").write_text('{"state": "approved", "x": "HELD-OUT-VERDICT-R2"}')
        (project / "docs" / "handoffs" / "feat-x_impl.md").write_text("HELD-OUT-VERDICT-R1\n")
        (project / "docs" / "handoffs" / "feat-x_impl_cycle.md").write_text("## Round 1\nHELD-OUT-VERDICT-R1\n")
        _git(project, "add", "-A")
        _git(project, "add", "-f", ".tagteam/legacy")
        _git(project, "commit", "-q", "-m", "closeout with outcome")
        closeout = _git(project, "rev-parse", "HEAD")
        base = _git(project, "rev-list", "--max-parents=0", "HEAD")
        (project / "LATER.txt").write_text("after the verdict\n")
        _git(project, "add", "-A"); _git(project, "commit", "-q", "-m", "later work")
        later = _git(project, "rev-parse", "HEAD")

        kept = []
        real_rmtree = b.shutil.rmtree

        def spy_rmtree(p, *a, **k):
            if Path(p).name.startswith(b.TEMP_PREFIX):
                repo = Path(p) / "repo"
                g = lambda *args: subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True).stdout
                blobs = g("log", "-p", "--all")
                files_text = "".join(f.read_text(errors="replace") for f in repo.rglob("*")
                                     if f.is_file() and ".git" not in f.parts)
                kept.append({"log": g("log", "--format=%s", "--all").split(), "status": g("status", "--porcelain"),
                             "names": g("diff", "--name-status", "HEAD~1", "HEAD"), "blobs": blobs,
                             "files": files_text, "has_later": (repo / "LATER.txt").exists(),
                             "cat_later": subprocess.run(["git", "cat-file", "-e", later], cwd=repo).returncode,
                             "refs": g("for-each-ref"),
                             "jsonl": (repo / "docs/handoffs/feat-x_impl_rounds.jsonl").read_text(),
                             "gone": [x for x in b.outcome_paths("feat-x", "impl")[1:] if (repo / x).exists()],
                             "app": (repo / "app.py").read_text()})
            return real_rmtree(p, *a, **k)
        monkeypatch.setattr(b.shutil, "rmtree", spy_rmtree)

        live_before = _live_fingerprint(project)
        seq_before = state_mod.read_state(str(project))["seq"]
        monkeypatch.setenv("FAKE_BENCH_VERDICT", "REQUEST_CHANGES")
        rc, out = _out(b.run_command, ["--round", f"feat-x:impl:1@{base}..{closeout}",
                                       "--round", "feat-x:impl:1", "--cell", "claude:sonnet:high", "--yes"], project)
        assert rc == 0, out
        assert _live_fingerprint(project) == live_before
        assert state_mod.read_state(str(project))["seq"] == seq_before

        calls = [json.loads(l) for l in fake_claude.read_text().splitlines()]
        assert len(calls) == 2 and len(kept) == 2
        for call, k in zip(calls, kept):
            assert call["read_only"] == "1" and call["bench"] == "1"
            assert not Path(call["cwd"]).resolve().is_relative_to(project.resolve())
            assert "--model" in call["argv"] and "sonnet" in call["argv"] and "high" in call["argv"]
            assert k["log"] == ["submitted", "base"] and k["status"] == ""
            assert "added.py" in k["names"] and "app.py" in k["names"]
            assert "git diff HEAD~1" in call["prompt"] and "added.py" in call["prompt"]
            assert "HELD-OUT-VERDICT" not in k["blobs"] and "HELD-OUT-VERDICT" not in k["files"]
            assert "HELD-OUT-VERDICT" not in call["prompt"]
            assert k["gone"] == [] and not k["has_later"] and k["cat_later"] != 0
            assert "refs/tagteam" not in k["refs"]
            pre = [json.loads(l) for l in k["jsonl"].splitlines()]
            assert pre[-1]["action"] == "AMEND" and all(e["role"] != "reviewer" for e in pre)
        # asserted used the closeout tree; snapshot used the AMEND tree
        assert kept[1]["app"] == "print('amended')\n"

        conn = db.connect(project_dir=str(project))
        try:
            res = db.bench_results(conn)
            usage = {u["id"]: u for u in db.get_usage(conn)}
        finally:
            conn.close()
        assert [(r["provenance"], r["outcome"], r["verdict"], r["n_blocker"], r["n_major"]) for r in res] == [
            ("asserted", "ok", "REQUEST_CHANGES", 1, 1), ("snapshot", "ok", "REQUEST_CHANGES", 1, 1)]
        u = usage[res[0]["usage_row_id"]]
        assert u["kind"] == "bench" and u["phase"] is None and u["target_phase"] is None
        assert u["input_tokens"] == 100 and u["model"] == "fake-sonnet"
        assert not list(Path(b.tempfile.gettempdir()).glob(b.TEMP_PREFIX + "*")) or all(
            not b._owner_matches(d, str(project.resolve())) for d in Path(b.tempfile.gettempdir()).glob(b.TEMP_PREFIX + "*"))

        # resume: both identities done; same commit under two provenances stays two results
        rc, out = _out(b.run_command, ["--round", f"feat-x:impl:1@{base}..{closeout}", "--round", "feat-x:impl:1",
                                       "--cell", "claude:sonnet:high", "--yes"], project)
        assert rc == 0 and "2 already done, 0 to run" in out
        assert len(fake_claude.read_text().splitlines()) == 2

        rc, out = _out(b.table_command, [], project)
        assert rc == 0
        assert out.index("provenance snapshot") < out.index("provenance asserted")
        assert out.count(b.NOT_GROUND_TRUTH) == 2
        assert "$" not in out

        rep = report_mod.phase_report(str(project), "feat-x")
        assert "bench" not in json.dumps(rep)

    def test_same_commit_two_provenances_do_not_suppress(self, project, fake_claude):
        _amended_cycle(project)
        conn = db.connect(project_dir=str(project))
        try:
            snap = next(s for s in db.submission_snapshots(conn) if s["round"] == 2)
        finally:
            conn.close()
        rc, out = _out(b.run_command, ["--round", "feat-x:impl:2", "--cell", "claude:sonnet:high", "--yes"], project)
        assert rc == 0
        rc, out = _out(b.run_command, ["--round", f"feat-x:impl:2@{snap['base_sha']}..{snap['commit_sha']}",
                                       "--cell", "claude:sonnet:high"], project)
        assert "0 already done, 1 to run" in out and "asserted" in out

    def test_failed_pair_recorded_and_retried(self, project, fake_claude, monkeypatch):
        _amended_cycle(project)
        monkeypatch.setenv("FAKE_BENCH_VERDICT", "none")
        rc, out = _out(b.run_command, ["--round", "feat-x:impl:2", "--cell", "claude:sonnet:high", "--yes"], project)
        assert rc == 1 and "failed" in out
        conn = db.connect(project_dir=str(project))
        try:
            res = db.bench_results(conn)
        finally:
            conn.close()
        assert res[-1]["outcome"] == "failed" and "no verdict file" in res[-1]["reason"]
        monkeypatch.setenv("FAKE_BENCH_VERDICT", "APPROVE")
        rc, out = _out(b.run_command, ["--round", "feat-x:impl:2", "--cell", "claude:sonnet:high", "--yes"], project)
        assert rc == 0 and "0 already done, 1 to run" in out
        rc, out = _out(b.table_command, ["--json"], project)
        cell = json.loads(out)["blocks"][0]["cells"][0]
        assert cell["rounds"] == 1 and cell["agree"] == 1 and cell["failed"] == 0


class TestTable:
    def _row(self, **kw):
        base = dict(run_id="r", phase="p", type="impl", round=1, version_ts="t", amended=0,
                    provenance="snapshot", cell="claude:sonnet:high", commit_sha="c", base_sha="b",
                    recorded_verdict="REQUEST_CHANGES", verdict="APPROVE", n_blocker=0, n_major=0, n_minor=0,
                    findings_json="[]", outcome="ok", reason="ok", usage_row_id=None, duration_ms=2000,
                    ts="2026-09-14T00:00:00+00:00")
        base.update(kw)
        return base

    def test_counts(self, tmp_path):
        conn = db.connect(project_dir=str(tmp_path))
        try:
            db.add_bench_result(conn, **self._row(round=1))                                   # missed RC
            db.add_bench_result(conn, **self._row(round=2, verdict="REQUEST_CHANGES", n_blocker=2, n_major=1))
            db.add_bench_result(conn, **self._row(round=3, recorded_verdict="APPROVE",
                                                  verdict="REQUEST_CHANGES", n_blocker=0, n_major=1))  # extra RC
            db.add_bench_result(conn, **self._row(round=4, outcome="failed", verdict=None, reason="timeout"))
            data = b.table_data(conn)
        finally:
            conn.close()
        c = data["blocks"][0]["cells"][0]
        assert (c["rounds"], c["agree"], c["missed_rc"], c["recorded_rc"], c["extra_rc"],
                c["recorded_approve"], c["failed"]) == (3, 1, 1, 2, 1, 1, 1)
        assert c["blockers_per_rc"] == 1.0 and c["majors_per_rc"] == 1.0
        text = b.render_table(data)
        assert "failed: claude:sonnet:high 1 (timeout)" in text and b.NOT_GROUND_TRUTH in text

    def test_no_db(self, tmp_path):
        (tmp_path / "tagteam.yaml").write_text("agents: {}\n")
        rc, out = _out(b.table_command, [], tmp_path)
        assert rc == 0 and out.strip() == "no bench results"


def test_cli_dispatch_help(project):
    r = subprocess.run([sys.executable, "-m", "tagteam", "bench", "--help"], capture_output=True, text=True,
                       cwd=project, env={**os.environ, "PYTHONPATH": str(Path(b.__file__).parents[1])})
    assert r.returncode == 0 and "tagteam bench run" in r.stdout


def test_read_only_mode_refuses_bench(project):
    r = subprocess.run([sys.executable, "-m", "tagteam", "bench", "table"], capture_output=True, text=True,
                       cwd=project, env={**os.environ, "TAGTEAM_READ_ONLY": "1",
                                         "PYTHONPATH": str(Path(b.__file__).parents[1])})
    assert r.returncode == 2


class TestReviewRound1Fixes:
    def test_two_revisions_same_round_cell_do_not_share_artifacts(self, project, fake_claude, monkeypatch):
        _amended_cycle(project)
        base = _git(project, "rev-list", "--max-parents=0", "HEAD")
        (project / "other.py").write_text("x = 2\n")
        _git(project, "add", "-A"); _git(project, "commit", "-q", "-m", "rev a")
        rev_a = _git(project, "rev-parse", "HEAD")
        (project / "other.py").write_text("x = 3\n")
        _git(project, "add", "-A"); _git(project, "commit", "-q", "-m", "rev b")
        rev_b = _git(project, "rev-parse", "HEAD")
        monkeypatch.setenv("FAKE_BENCH_VERDICT_SEQ", "APPROVE,none")
        spec_a = f"feat-x:impl:2@{base}..{rev_a}"
        rc, out = _out(b.run_command, ["--round", spec_a, "--round", spec_a,          # duplicate request
                                       "--round", f"feat-x:impl:2@{base}..{rev_b}",
                                       "--cell", "claude:sonnet:high", "--yes"], project)
        assert "2 pair(s), 0 already done, 2 to run" in out
        assert rc == 1
        conn = db.connect(project_dir=str(project))
        try:
            res = db.bench_results(conn)
        finally:
            conn.close()
        assert [(r["commit_sha"], r["outcome"], r["verdict"]) for r in res] == [
            (rev_a, "ok", "APPROVE"), (rev_b, "failed", None)]
        run_dirs = list((project / ".tagteam" / "bench").iterdir())
        assert len(run_dirs) == 1 and len(list(run_dirs[0].iterdir())) == 2
        # a second invocation gets a distinct run id
        monkeypatch.delenv("FAKE_BENCH_VERDICT_SEQ")
        _out(b.run_command, ["--round", f"feat-x:impl:2@{base}..{rev_b}", "--cell", "claude:sonnet:high",
                             "--yes"], project)
        conn = db.connect(project_dir=str(project))
        try:
            res = db.bench_results(conn)
        finally:
            conn.close()
        assert res[-1]["outcome"] == "ok" and res[-1]["run_id"] != res[0]["run_id"]

    def test_prune_only_abandoned(self, tmp_path):
        import tempfile as _tf
        root = str(tmp_path / "proj")
        dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
        made = {}
        for name, owner in {
            "active": {"project": root, "keep": False, "pid": os.getpid(), "ident": None},
            "kept": {"project": root, "keep": True, "pid": dead.pid, "ident": None},
            "abandoned": {"project": root, "keep": False, "pid": dead.pid, "ident": None},
            "other": {"project": root + "-elsewhere", "keep": False, "pid": dead.pid, "ident": None},
        }.items():
            d = Path(_tf.mkdtemp(prefix=b.TEMP_PREFIX))
            (d / b.OWNER_FILE).write_text(json.dumps(owner))
            made[name] = d
        try:
            assert {k: b.replay_dir_state(d, root) for k, d in made.items()} == {
                "active": "active", "kept": "kept", "abandoned": "abandoned", "other": "other"}
            assert b.prune_stale_replays(root) == 1
            assert {k: d.exists() for k, d in made.items()} == {
                "active": True, "kept": True, "abandoned": False, "other": True}
        finally:
            for d in made.values():
                b.shutil.rmtree(d, ignore_errors=True)

    def test_keep_survives_next_run(self, project, fake_claude):
        _amended_cycle(project)
        root = str(project.resolve())
        rc, out = _out(b.run_command, ["--round", "feat-x:impl:1", "--cell", "claude:sonnet:high",
                                       "--keep", "--yes"], project)
        assert rc == 0
        kept = [d for d in Path(b.tempfile.gettempdir()).glob(b.TEMP_PREFIX + "*")
                if b.replay_dir_state(d, root) == "kept"]
        try:
            assert len(kept) == 1 and (kept[0] / "repo" / "app.py").exists()
            _out(b.run_command, ["--round", "feat-x:impl:2", "--cell", "claude:sonnet:high", "--yes"], project)
            assert kept[0].exists()
        finally:
            for d in kept:
                b.shutil.rmtree(d, ignore_errors=True)

    def test_replay_is_faithful_to_tree_objects(self, project, fake_claude):
        (project / ".gitattributes").write_text(
            "app.py export-ignore\nversion.txt export-subst\ncrlf.txt text eol=crlf\n")
        (project / "version.txt").write_text("$Format:%H$\n")
        (project / "crlf.txt").write_bytes(b"line1\nline2\n")
        (project / "run.sh").write_text("#!/bin/sh\necho hi\n"); (project / "run.sh").chmod(0o755)
        if sys.platform != "win32":
            os.symlink("app.py", project / "link.py")
        _git(project, "add", "-A"); _git(project, "commit", "-q", "-m", "attributes")
        _amended_cycle(project)
        conn = db.connect(project_dir=str(project))
        try:
            pair = b.plan_pairs(str(project), conn, ["feat-x:impl:2"], [b.parse_cell("claude:sonnet:high")])[0]
        finally:
            conn.close()
        repo = project.parent / "replay"
        b.build_replay_repo(str(project), repo, pair)

        def tree(cwd, rev):
            out = _git(cwd, "ls-tree", "-r", "--full-tree", rev)
            return {line.split("\t", 1)[1]: line.split("\t", 1)[0] for line in out.splitlines()}
        skip = set(b.outcome_paths("feat-x", "impl"))
        src = {k: v for k, v in tree(project, pair.commit_sha).items() if k not in skip}
        dst = tree(repo, "HEAD")
        canonical = "docs/handoffs/feat-x_impl_rounds.jsonl"
        assert canonical in dst
        dst.pop(canonical)
        assert dst == src
        base_src = {k: v for k, v in tree(project, pair.base_sha).items() if k not in skip}
        assert tree(repo, "HEAD~1") == base_src
        assert (repo / "app.py").read_text() == "print('v2')\n"
        assert (repo / "version.txt").read_text() == "$Format:%H$\n"
        assert (repo / "crlf.txt").read_bytes() == b"line1\nline2\n"
        assert os.access(repo / "run.sh", os.X_OK)
        if sys.platform != "win32":
            assert os.readlink(repo / "link.py") == "app.py"
        assert _git(repo, "status", "--porcelain") == ""


def test_table_counts_one_row_per_round_version(tmp_path):
    row = TestTable()._row
    conn = db.connect(project_dir=str(tmp_path))
    try:
        db.add_bench_result(conn, **row(provenance="asserted", commit_sha="c1", verdict="APPROVE",
                                        recorded_verdict="APPROVE", duration_ms=1000))
        db.add_bench_result(conn, **row(provenance="asserted", commit_sha="c2", verdict="REQUEST_CHANGES",
                                        recorded_verdict="APPROVE", n_blocker=1, duration_ms=5000))
        data = b.table_data(conn)
    finally:
        conn.close()
    c = data["blocks"][0]["cells"][0]
    assert c["rounds"] == 1 and c["agree"] == 0 and c["extra_rc"] == 1 and c["seconds"] == 5.0
