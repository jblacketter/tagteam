"""Phase 72: jobs — recorded background tasks, and the `ci-watch` kind.

A fake `gh` on PATH replays canned (JSON body, exit code) pairs per
subcommand, matching what the real command gives (e.g. `gh pr view` exits 0
with an EMPTY rollup when no checks are registered, and 1 with a GraphQL
message for a missing PR). PyPI is a local HTTP stub.
"""

import http.server
import io
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tagteam import jobs

REPO = Path(__file__).resolve().parent.parent

FAKE_GH = textwrap.dedent(r'''
    #!{python}
    import json, os, sys
    spec_path = os.environ["FAKE_GH_SPEC"]
    args = sys.argv[1:]
    with open(os.environ["FAKE_GH_CALLS"], "a") as f:
        f.write(json.dumps(args) + "\n")
    if "--log-failed" in args:
        key = "run log"
    else:
        key = " ".join(args[:2])
    spec = json.load(open(spec_path))
    seq = spec.get(key)
    if seq is None:
        sys.stderr.write("fake gh: no response for %r\n" % key)
        sys.exit(2)
    counts_path = spec_path + ".counts"
    counts = json.load(open(counts_path)) if os.path.exists(counts_path) else {}
    i = counts.get(key, 0)
    counts[key] = i + 1
    json.dump(counts, open(counts_path, "w"))
    r = seq[min(i, len(seq) - 1)]
    if r.get("sleep"):
        import time
        time.sleep(r["sleep"])
    out = r.get("out", "")
    sys.stdout.write(out if isinstance(out, str) else json.dumps(out))
    sys.stderr.write(r.get("err", ""))
    sys.exit(r.get("rc", 0))
''').lstrip()


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".tagteam").mkdir(parents=True)
    (root / "docs" / "handoffs").mkdir(parents=True)
    (root / "tagteam.yaml").write_text("agents:\n  lead:\n    name: claude\n  reviewer:\n    name: codex\n")
    monkeypatch.chdir(root)
    monkeypatch.delenv("TAGTEAM_READ_ONLY", raising=False)
    monkeypatch.setenv("TAGTEAM_NO_NOTIFY", "1")
    return root


@pytest.fixture
def gh(tmp_path, monkeypatch):
    """Install the fake gh; returns set(spec) and calls()."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "gh"
    exe.write_text(FAKE_GH.replace("{python}", sys.executable))
    exe.chmod(0o755)
    spec = tmp_path / "gh-spec.json"
    calls = tmp_path / "gh-calls.jsonl"
    spec.write_text("{}")
    calls.write_text("")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_GH_SPEC", str(spec))
    monkeypatch.setenv("FAKE_GH_CALLS", str(calls))

    class G:
        def set(self, d):
            spec.write_text(json.dumps(d))
            c = Path(str(spec) + ".counts")
            if c.exists():
                c.unlink()

        def calls(self):
            return [json.loads(l) for l in calls.read_text().splitlines() if l.strip()]
    return G()


def _virtual_clock(monkeypatch):
    off = [0.0]
    real = time.monotonic
    monkeypatch.setattr(jobs, "_clock", lambda: real() + off[0])
    monkeypatch.setattr(jobs, "_sleep", lambda s: off.__setitem__(0, off[0] + s))
    return off


@pytest.fixture
def fast(monkeypatch):
    """In-process runs: a virtual clock that `_sleep` advances (no real
    sleeping, and the deadline arithmetic still holds); notifications captured."""
    _virtual_clock(monkeypatch)
    sent = []
    monkeypatch.setattr(jobs, "_notify", lambda t, m: sent.append((t, m)) or True)
    return sent


def _check(name, status="COMPLETED", conclusion="SUCCESS", url=None):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion,
            "detailsUrl": url or f"https://github.com/o/r/actions/runs/111/job/{abs(hash(name)) % 1000}"}


def _pr(checks, head="a" * 40):
    return {"out": {"number": 59, "state": "OPEN", "headRefOid": head, "statusCheckRollup": checks}}


def _mk(proj, target, **kw):
    jid = jobs.create(proj, "ci-watch", target, interval_s=kw.get("interval_s", 5),
                      timeout_s=kw.get("timeout_s", 3600), to_lead=kw.get("to_lead", False),
                      quiet=kw.get("quiet", False), by="test")
    assert jid
    return jid


def _run_inproc(proj, jid):
    buf = io.StringIO()
    rc = jobs.run(proj, jid, out=buf)
    return rc, jobs.read_record(proj, jid), buf.getvalue()


# ---------------------------------------------------------------------------
# 1. Each target reaches the right answer from canned gh sequences
# ---------------------------------------------------------------------------

class TestPrTarget:
    def test_no_checks_then_pending_then_green(self, proj, gh, fast):
        gh.set({"pr view": [_pr([]), _pr([_check("pytest", status="IN_PROGRESS", conclusion="")]),
                            _pr([_check("pytest"), _check("win", conclusion="SKIPPED")])]})
        jid = _mk(proj, {"pr": 59})
        rc, rec, _ = _run_inproc(proj, jid)
        assert rec["status"] == "succeeded" and rc == 0
        assert "1/2 checks passed, 1 skipped" in rec["result"]["summary"]
        log = "\n".join(jobs.read_log(proj, jid, 50))
        assert "no checks registered yet" in log and "0/1 checks complete" in log
        assert len(fast) == 1 and fast[0][0] == "CI green"

    def test_empty_rollup_is_never_green(self, proj, gh, fast):
        gh.set({"pr view": [_pr([])]})
        jid = _mk(proj, {"pr": 59}, timeout_s=0)
        _rc, rec, _ = _run_inproc(proj, jid)
        assert rec["status"] == "timed-out"
        jid2 = _mk(proj, {"pr": 59})
        polls = []
        orig = jobs.poll

        def limited(root, target, mem):
            polls.append(1)
            if len(polls) > 5:
                (proj / jobs._job_rel(jid2, "cancel")).write_text("{}")
            return orig(root, target, mem)
        import pytest as _p
        mp = _p.MonkeyPatch()
        mp.setattr(jobs, "poll", limited)
        try:
            _rc, rec2, _ = _run_inproc(proj, jid2)
        finally:
            mp.undo()
        assert rec2["status"] == "cancelled" and len(polls) >= 5

    def test_pending_then_red_with_names_urls_and_log_tail(self, proj, gh, fast):
        bad = _check("pytest (ubuntu)", conclusion="FAILURE",
                     url="https://github.com/o/r/actions/runs/424242/job/9")
        gh.set({"pr view": [_pr([_check("pytest (ubuntu)", status="QUEUED", conclusion="")]),
                            _pr([bad, _check("lint")])],
                "run log": [{"out": "\n".join(f"line {i}" for i in range(100))}]})
        jid = _mk(proj, {"pr": 59})
        rc, rec, _ = _run_inproc(proj, jid)
        assert rec["status"] == "failed" and rc == 4
        r = rec["result"]
        assert r["failing"] == [{"name": "pytest (ubuntu)", "url": bad["detailsUrl"]}]
        assert len(r["log_tail"]) == jobs.LOG_TAIL_LINES and r["log_tail"][-1] == "line 99"
        assert ["run", "view", "424242", "--log-failed"] in gh.calls()
        assert fast[0][0] == "CI failed" and bad["detailsUrl"] in fast[0][1]

    def test_status_context_items_count(self, proj, gh, fast):
        gh.set({"pr view": [_pr([{"__typename": "StatusContext", "context": "ci/legacy", "state": "PENDING",
                                  "targetUrl": "u"}]),
                            _pr([{"__typename": "StatusContext", "context": "ci/legacy", "state": "ERROR",
                                  "targetUrl": "u"}])]})
        _rc, rec, _ = _run_inproc(proj, _mk(proj, {"pr": 59}))
        assert rec["status"] == "failed" and rec["result"]["failing"][0]["name"] == "ci/legacy"

    def test_expect_checks_does_not_judge_one_completed_check(self, proj, gh, fast):
        gh.set({"pr view": [_pr([_check("a")]), _pr([_check("a"), _check("b")])]})
        jid = _mk(proj, {"pr": 59, "expect_checks": 2})
        _rc, rec, _ = _run_inproc(proj, jid)
        assert rec["status"] == "succeeded" and "2/2" in rec["result"]["summary"]
        assert "1/2 checks registered" in "\n".join(jobs.read_log(proj, jid, 50))

    def test_head_change_mid_watch_is_followed(self, proj, gh, fast):
        gh.set({"pr view": [_pr([_check("a", status="IN_PROGRESS", conclusion="")], head="1" * 40),
                            _pr([_check("a")], head="2" * 40)]})
        jid = _mk(proj, {"pr": 59})
        _rc, rec, _ = _run_inproc(proj, jid)
        assert rec["result"]["sha"] == "2" * 40 and "(2222222)" in rec["result"]["summary"]
        assert "head moved 1111111 → 2222222" in "\n".join(jobs.read_log(proj, jid, 50))


class TestRunAndPypiTargets:
    def test_run_succeeds(self, proj, gh, fast):
        gh.set({"run view": [{"out": {"status": "in_progress", "conclusion": "", "name": "Tests", "url": "u",
                                      "jobs": [{"name": "a", "status": "completed"}, {"name": "b", "status": "queued"}]}},
                             {"out": {"status": "completed", "conclusion": "success", "name": "Tests", "url": "u",
                                      "jobs": []}}]})
        jid = _mk(proj, {"run": 77})
        _rc, rec, _ = _run_inproc(proj, jid)
        assert rec["status"] == "succeeded" and "Tests #77: success" == rec["result"]["summary"]
        assert "(1/2 jobs done)" in "\n".join(jobs.read_log(proj, jid, 50))

    def test_run_fails_with_failing_jobs(self, proj, gh, fast):
        gh.set({"run view": [{"out": {"status": "completed", "conclusion": "failure", "name": "Tests", "url": "u",
                                      "jobs": [{"name": "ok", "conclusion": "success", "url": "j1"},
                                               {"name": "pytest", "conclusion": "failure", "url": "j2"}]}}],
                "run log": [{"out": "boom"}]})
        _rc, rec, _ = _run_inproc(proj, _mk(proj, {"run": 77}))
        assert rec["status"] == "failed"
        assert rec["result"]["failing"] == [{"name": "pytest", "url": "j2"}] and rec["result"]["log_tail"] == ["boom"]

    def test_pypi_absent_then_listed(self, proj, fast, monkeypatch):
        hits = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append((self.path, self.headers.get("Accept")))
                if len(hits) == 1:
                    self.send_response(404); self.end_headers(); return
                body = {"name": "tagteam", "versions": ["3.14.8"] + (["3.14.9"] if len(hits) > 2 else []),
                        "files": []}
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.pypi.simple.v1+json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            monkeypatch.setenv(jobs.PYPI_SIMPLE_ENV, f"http://127.0.0.1:{srv.server_port}/simple/")
            _rc, rec, _ = _run_inproc(proj, _mk(proj, {"pypi": "TagTeam", "version": "3.14.9"}))
        finally:
            srv.shutdown()
        assert rec["status"] == "succeeded" and len(hits) == 3
        assert hits[0][0] == "/simple/tagteam/" and "pypi.simple.v1+json" in hits[0][1]


# ---------------------------------------------------------------------------
# 2. The previous-run trap
# ---------------------------------------------------------------------------

def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(proj):
    _git(proj, "init", "-q")
    _git(proj, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "one")
    old = _git(proj, "rev-parse", "HEAD")
    _git(proj, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "two")
    _git(proj, "tag", "v1.2.3")
    new = _git(proj, "rev-parse", "HEAD")
    return old, new


class TestPreviousRunTrap:
    def test_a_60s_old_green_run_of_another_commit_is_ignored(self, proj, gh, fast, repo):
        old, new = repo
        recent = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        prev = {"databaseId": 900, "headSha": old, "event": "push", "createdAt": recent,
                "status": "completed", "conclusion": "success", "url": "u900"}
        mine = {"databaseId": 901, "headSha": new, "event": "push", "createdAt": recent,
                "status": "in_progress", "conclusion": "", "url": "u901"}
        # `--commit` filters server-side; a sloppy index may still hand back another commit's run
        gh.set({"run list": [{"out": [prev]}, {"out": [prev, mine]}],
                "run view": [{"out": {"status": "completed", "conclusion": "success", "name": "Publish", "url": "u901",
                                      "jobs": [], "headSha": new}}]})
        buf = io.StringIO()
        assert jobs.job_command(["start", "ci-watch", "--workflow", "Publish", "--ref", "v1.2.3",
                                 "--interval", "5", "--quiet"],
                                project_root=proj, out=buf) in (0, 1)
        jid = buf.getvalue().split()[1].rstrip(":")
        # the spawned runner is real; wait for it
        rec = _wait_terminal(proj, jid)
        assert rec["target"]["sha"] == new and rec["target"]["ref"] == "v1.2.3"
        assert rec["status"] == "succeeded" and "#901" in rec["result"]["summary"]
        log = "\n".join(jobs.read_log(proj, jid, 50))
        assert f"waiting for a run of {new[:7]}" in log
        assert ["run", "view", "900", "--json", "status,conclusion,name,url,jobs,headSha"] not in gh.calls()
        assert any(c[:2] == ["run", "list"] and c[c.index("--commit") + 1] == new for c in gh.calls())

    def test_two_runs_for_the_sha_pin_the_higher_id_for_good(self, proj, gh, fast, repo):
        _old, new = repo
        runs = [{"databaseId": d, "headSha": new, "status": "in_progress"} for d in (901, 905)]
        gh.set({"run list": [{"out": runs}],
                "run view": [{"out": {"status": "in_progress", "name": "P", "jobs": []}},
                             {"out": {"status": "completed", "conclusion": "success", "name": "P", "jobs": []}}]})
        jid = _mk(proj, {"workflow": "P", "sha": new})
        _rc, rec, _ = _run_inproc(proj, jid)
        assert rec["pinned_run"] == 905 and rec["status"] == "succeeded"
        views = [c for c in gh.calls() if c[:2] == ["run", "view"]]
        assert views and all(c[2] == "905" for c in views)
        assert sum(1 for c in gh.calls() if c[:2] == ["run", "list"]) == 1     # pinned: never listed again
        assert "pinned 905, ignored 901" in "\n".join(jobs.read_log(proj, jid, 50))

    def test_an_unresolvable_ref_fails_start_and_writes_nothing(self, proj, gh, repo):
        buf = io.StringIO()
        rc = jobs.job_command(["start", "ci-watch", "--workflow", "P", "--ref", "v9.9.9"], project_root=proj, out=buf)
        assert rc == 1 and "does not resolve" in buf.getvalue()
        assert not (proj / jobs.JOBS_REL).exists()


# ---------------------------------------------------------------------------
# 3. error is not failed
# ---------------------------------------------------------------------------

class TestErrorIsNotFailed:
    def test_missing_gh(self, proj, fast, monkeypatch, tmp_path):
        empty = tmp_path / "nobin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        _rc, rec, _ = _run_inproc(proj, _mk(proj, {"pr": 1}))
        assert rec["status"] == "error" and "gh not found" in rec["error"] and rec["attempts"] == 1

    @pytest.mark.parametrize("err", [
        "To get started with GitHub CLI, please run:  gh auth login\n",
        "GraphQL: Could not resolve to a PullRequest with the number of 99999. (repository.pullRequest)\n"])
    def test_unauthenticated_or_not_found_ends_at_once(self, proj, gh, fast, err):
        gh.set({"pr view": [{"rc": 1, "err": err}]})
        _rc, rec, _ = _run_inproc(proj, _mk(proj, {"pr": 99999}))
        assert rec["status"] == "error" and rec["error"].endswith(err.strip().splitlines()[0])
        assert len(gh.calls()) == 1
        assert fast[0][0] == "CI watch error"


# ---------------------------------------------------------------------------
# 4. The lifecycle — ownership is two OS-released locks
# ---------------------------------------------------------------------------

def _wait_terminal(proj, jid, timeout=30):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        rec = jobs.read_record(proj, jid)
        if rec and rec.get("status") in jobs.TERMINAL:
            return rec
        time.sleep(0.1)
    raise AssertionError(f"job {jid} not terminal: {jobs.read_record(proj, jid)}\n"
                         + (proj / jobs._job_rel(jid, 'runner.out')).read_text())


def _child(code: str, *, wait_for: Path | None = None) -> subprocess.Popen:
    p = subprocess.Popen([sys.executable, "-c", code], cwd=str(REPO), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    if wait_for is not None:
        t0 = time.monotonic()
        while not wait_for.exists():
            assert time.monotonic() - t0 < 20 and p.poll() is None, p.communicate()
            time.sleep(0.02)
    return p


HOLD = """
import os, sys, time
from pathlib import Path
from tagteam import jobs
root, jid, what, flag = Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])
""".strip()


def _holder(proj, jid, script_body, flag):
    code = HOLD.replace("sys.argv[1]", repr(str(proj))).replace("sys.argv[2]", repr(jid)) \
               .replace("sys.argv[3]", "'x'").replace("sys.argv[4]", repr(str(flag))) + "\n" + script_body
    return _child(code, wait_for=flag)


class TestLifecycle:
    def test_timeout(self, proj, gh, fast):
        gh.set({"pr view": [_pr([_check("a", status="IN_PROGRESS", conclusion="")])]})
        _rc, rec, _ = _run_inproc(proj, _mk(proj, {"pr": 1}, timeout_s=0))
        assert rec["status"] == "timed-out" and fast[0][0] == "CI watch timed out"

    def test_real_runner_cancel_within_one_interval(self, proj, gh):
        gh.set({"pr view": [_pr([_check("a", status="IN_PROGRESS", conclusion="")])]})
        jid, rec = jobs.start(proj, "ci-watch", {"pr": 1}, interval_s=5, quiet=True, by="t")
        assert rec["status"] == "running"
        assert jobs.liveness(proj, jobs.read_record(proj, jid)) == "running"
        t0 = time.monotonic()
        outcome, _ = jobs.cancel(proj, jid)
        assert outcome == "left-to-runner"
        rec = _wait_terminal(proj, jid, timeout=15)
        assert rec["status"] == "cancelled" and time.monotonic() - t0 < 6.5
        assert rec["delivery"]["notify"] == "skipped"

    def test_fast_completion_keeps_the_runners_result(self, proj, gh):
        gh.set({"run view": [{"out": {"status": "completed", "conclusion": "success", "name": "T", "jobs": []}}]})
        jid, rec = jobs.start(proj, "ci-watch", {"run": 5}, quiet=True, by="t")
        final = _wait_terminal(proj, jid)
        assert final["status"] == "succeeded"
        # start's deadline write, re-applied now, finds the record terminal and writes nothing
        before = (proj / jobs._job_rel(jid, "job.json")).read_bytes()
        _r, wrote = jobs.update_record(proj, jid, lambda r: dict(r, status="error") if r["status"] == "starting" else None)
        assert not wrote and (proj / jobs._job_rel(jid, "job.json")).read_bytes() == before

    def test_duplicate_runner_exits_writing_and_delivering_nothing(self, proj, gh, fast, tmp_path):
        jid = _mk(proj, {"pr": 1})
        flag = tmp_path / "held"
        p = _holder(proj, jid, """
fd = jobs._hold_runner_lock(root, jid)
assert fd is not None
flag.write_text('1')
time.sleep(30)
""", flag)
        try:
            before = (proj / jobs._job_rel(jid, "job.json")).read_bytes()
            buf = io.StringIO()
            assert jobs.run(proj, jid, out=buf) == 3 and "another runner holds it" in buf.getvalue()
            assert (proj / jobs._job_rel(jid, "job.json")).read_bytes() == before
            assert fast == []
        finally:
            p.kill(); p.wait()

    def test_a_probe_does_not_block_a_real_start(self, proj, monkeypatch):
        jid = _mk(proj, {"pr": 1})
        fd = jobs._open_guarded(proj, jobs._job_rel(jid, "runner.lock"), os.O_RDONLY)
        assert jobs._try_lock(fd)              # a reader's probe, caught mid-probe

        def release():
            time.sleep(0.3)
            jobs._unlock(fd)
            os.close(fd)
        threading.Thread(target=release).start()
        got = jobs._hold_runner_lock(proj, jid)
        assert got is not None
        jobs._unlock(got); os.close(got)

    def test_killed_runner_reads_lost_and_the_reader_writes_nothing(self, proj, gh, tmp_path):
        jid = _mk(proj, {"pr": 1})
        flag = tmp_path / "held"
        p = _holder(proj, jid, """
fd = jobs._hold_runner_lock(root, jid)
jobs.update_record(root, jid, lambda r: dict(r, status='running', pid=os.getpid()))
flag.write_text('1')
time.sleep(30)
""", flag)
        assert jobs.liveness(proj, jobs.read_record(proj, jid)) == "running"
        p.kill(); p.wait()
        d = proj / jobs._job_rel(jid)
        snap = {f.name: f.stat().st_mtime_ns for f in d.iterdir()}
        rec = jobs.read_record(proj, jid)
        assert jobs.liveness(proj, rec) == "lost" and jobs.view(proj, rec)["shown"] == "lost"
        jobs.jobs_payload(proj)
        assert {f.name: f.stat().st_mtime_ns for f in d.iterdir()} == snap

    def test_interrupted_finaliser_then_cancel_finishes_it_durably(self, proj, fast, tmp_path):
        """A real child takes runner.lock AND job.lock, then dies before the terminal write."""
        jid = _mk(proj, {"pr": 1})
        flag = tmp_path / "held"
        p = _holder(proj, jid, """
fd = jobs._hold_runner_lock(root, jid)
jobs.update_record(root, jid, lambda r: dict(r, status='running', pid=os.getpid()))
lk = jobs._JobLock(root, jid).__enter__()      # 'about to commit succeeded'
flag.write_text('1')
os._exit(0)
""", flag)
        p.wait()
        rec = jobs.read_record(proj, jid)
        assert rec["status"] == "running" and jobs.liveness(proj, rec) == "lost"
        outcome, rec = jobs.cancel(proj, jid)
        assert outcome == "finalised" and rec["status"] == "cancelled" and rec["error"] == "runner lost"
        again = jobs.read_record(proj, jid)
        assert again == rec
        assert jobs.cancel(proj, jid)[0] == "terminal" and jobs.read_record(proj, jid) == rec
        assert fast == []                                   # a lost-job cleanup delivers nothing

    def test_a_canceller_that_dies_holding_job_lock_leaves_it_recoverable(self, proj, tmp_path):
        jid = _mk(proj, {"pr": 1})
        jobs.update_record(proj, jid, lambda r: dict(r, status="running"))
        flag = tmp_path / "held"
        p = _holder(proj, jid, """
lk = jobs._JobLock(root, jid).__enter__()
flag.write_text('1')
os._exit(0)
""", flag)
        p.wait()
        outcome, rec = jobs.cancel(proj, jid)
        assert outcome == "finalised" and rec["status"] == "cancelled"

    def test_concurrent_cancellers_exactly_one_commits(self, proj):
        jid = _mk(proj, {"pr": 1})
        jobs.update_record(proj, jid, lambda r: dict(r, status="running"))
        code = ("import sys\nfrom pathlib import Path\nfrom tagteam import jobs\n"
                f"print(jobs.cancel(Path({str(proj)!r}), {jid!r})[0])")
        ps = [_child(code) for _ in range(4)]
        outs = sorted(p.communicate()[0].strip() for p in ps)
        assert outs.count("finalised") == 1 and outs.count("terminal") == 3, outs

    def test_recovery_never_overwrites_a_committed_result(self, proj):
        jid = _mk(proj, {"pr": 1})
        jobs.update_record(proj, jid, lambda r: dict(r, status="running"))
        rec, wrote = jobs._commit(proj, jid, "succeeded", {"summary": "green"})
        assert wrote
        assert jobs.cancel(proj, jid) == ("terminal", rec)
        assert jobs.read_record(proj, jid) == rec
        # only the committer's one `delivery` may be added — and only once
        assert jobs.update_record(proj, jid, lambda r: dict(r, delivery={"notify": "sent"}))[1]
        assert not jobs.update_record(proj, jid, lambda r: dict(r, delivery={"notify": "again"}))[1]
        assert not jobs.update_record(proj, jid, lambda r: dict(r, status="failed"))[1]

    def test_missing_lock_file_reads_unknown_and_cancel_does_not_finalise(self, proj):
        jid = _mk(proj, {"pr": 1})
        jobs.update_record(proj, jid, lambda r: dict(r, status="running"))
        (proj / jobs._job_rel(jid, "runner.lock")).unlink()
        rec = jobs.read_record(proj, jid)
        assert jobs.liveness(proj, rec) == "unknown" and not jobs.view(proj, rec)["cancellable"]
        outcome, rec2 = jobs.cancel(proj, jid)
        assert outcome == "unknown" and rec2["status"] == "running"
        assert (proj / jobs._job_rel(jid, "cancel")).exists()

    def test_runner_never_starts_then_a_late_runner_exits(self, proj, monkeypatch, fast):
        monkeypatch.setattr(jobs, "_spawn", lambda root, jid: None)
        jid, rec = jobs.start(proj, "ci-watch", {"pr": 1}, by="t", deadline_s=0.2)
        assert rec["status"] == "error" and "did not start" in rec["error"]
        assert fast and fast[0][0] == "CI watch error"
        buf = io.StringIO()
        assert jobs.run(proj, jid, out=buf) == 3 and "not starting" in buf.getvalue()
        after = jobs.read_record(proj, jid)
        assert {k: v for k, v in after.items() if k != "delivery"} == rec and after["delivery"]["notify"] == "sent"

    def test_retention_prunes_old_finished_and_cancelled_lost_jobs(self, proj):
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        a = _mk(proj, {"pr": 1})
        jobs.update_record(proj, a, lambda r: dict(r, status="cancelled", created_at=old, error="runner lost"))
        b = _mk(proj, {"pr": 2})
        jobs.update_record(proj, b, lambda r: dict(r, status="running", created_at=old))   # lost, never cancelled
        c = _mk(proj, {"pr": 3})
        jobs.update_record(proj, c, lambda r: dict(r, status="succeeded"))                  # recent
        assert sorted(jobs.prune(proj)) == sorted([a, b])
        assert jobs.list_ids(proj) == [c]

    def test_reads_create_nothing_and_work_read_only(self, proj, monkeypatch, tmp_path):
        bare = tmp_path / "bare"
        bare.mkdir()
        (bare / "tagteam.yaml").write_text("agents: {}\n")
        env = dict(os.environ, TAGTEAM_READ_ONLY="1")
        for args in (["job", "list"], ["job", "list", "--json"], ["job", "status", "20260101-000000-abcd"],
                     ["job", "log", "20260101-000000-abcd"]):
            r = subprocess.run([sys.executable, "-m", "tagteam", *args], cwd=bare, env=env,
                               capture_output=True, text=True)
            assert "not a read command" not in r.stderr, (args, r.stderr)
        assert sorted(p.name for p in bare.iterdir()) == ["tagteam.yaml"]
        for args in (["job", "start", "ci-watch", "--pr", "1"], ["job", "cancel", "20260101-000000-abcd"]):
            r = subprocess.run([sys.executable, "-m", "tagteam", *args], cwd=bare, env=env,
                               capture_output=True, text=True)
            assert r.returncode == 2 and "not a read command" in r.stderr
        assert sorted(p.name for p in bare.iterdir()) == ["tagteam.yaml"]


# ---------------------------------------------------------------------------
# 5. Delivery: at most once, best-effort (local calls only — no phone)
# ---------------------------------------------------------------------------

class TestDelivery:
    def test_normal_path_one_notification_and_a_delivery_field(self, proj, gh, fast):
        gh.set({"run view": [{"out": {"status": "completed", "conclusion": "success", "name": "T", "jobs": []}}]})
        jid = _mk(proj, {"run": 1})
        _rc, rec, _ = _run_inproc(proj, jid)
        assert len(fast) == 1 and rec["delivery"]["notify"] == "sent"
        assert rec["delivery"]["interjection"] == "skipped"

    def test_a_raising_notifier_does_not_change_the_result(self, proj, gh, monkeypatch):
        _virtual_clock(monkeypatch)
        monkeypatch.setattr(jobs, "_notify", lambda t, m: 1 / 0)
        gh.set({"run view": [{"out": {"status": "completed", "conclusion": "success", "name": "T", "jobs": []}}]})
        _rc, rec, _ = _run_inproc(proj, _mk(proj, {"run": 1}))
        assert rec["status"] == "succeeded" and rec["delivery"]["notify"].startswith("failed")

    def test_notifications_disabled_is_skipped_not_failed(self, proj, gh, monkeypatch):
        _virtual_clock(monkeypatch)
        gh.set({"run view": [{"out": {"status": "completed", "conclusion": "success", "name": "T", "jobs": []}}]})
        _rc, rec, _ = _run_inproc(proj, _mk(proj, {"run": 1}))          # TAGTEAM_NO_NOTIFY=1 from the fixture
        assert rec["delivery"]["notify"] == "skipped: TAGTEAM_NO_NOTIFY is set"

    def test_quiet_sends_none(self, proj, gh, fast):
        gh.set({"run view": [{"out": {"status": "completed", "conclusion": "success", "name": "T", "jobs": []}}]})
        _rc, rec, _ = _run_inproc(proj, _mk(proj, {"run": 1}, quiet=True))
        assert fast == [] and rec["delivery"]["notify"] == "skipped"

    def test_a_racing_cancel_adds_no_notification(self, proj, gh, fast):
        gh.set({"run view": [{"out": {"status": "completed", "conclusion": "success", "name": "T", "jobs": []}}]})
        jid = _mk(proj, {"run": 1})
        _run_inproc(proj, jid)
        assert jobs.cancel(proj, jid)[0] == "terminal"
        assert len(fast) == 1

    def test_committer_killed_before_delivery_leaves_no_field_and_no_replay(self, proj, gh, fast, monkeypatch):
        class Killed(BaseException):
            pass

        def die(*a, **k):
            raise Killed()
        monkeypatch.setattr(jobs, "deliver", die)
        gh.set({"run view": [{"out": {"status": "completed", "conclusion": "success", "name": "T", "jobs": []}}]})
        jid = _mk(proj, {"run": 1})
        with pytest.raises(Killed):
            jobs.run(proj, jid, out=io.StringIO())
        rec = jobs.read_record(proj, jid)
        assert rec["status"] == "succeeded" and "delivery" not in rec
        monkeypatch.undo()
        monkeypatch.setattr(jobs, "_notify", lambda t, m: fast.append((t, m)) or True)
        assert jobs.cancel(proj, jid)[0] == "terminal" and jobs.run(proj, jid, out=io.StringIO()) == 3
        assert fast == [] and "delivery" not in jobs.read_record(proj, jid)
        buf = io.StringIO()
        jobs.job_command(["status", jid], project_root=proj, out=buf)
        assert "delivery: not recorded" in buf.getvalue()

    def test_to_lead_records_one_interjection_by_the_job(self, proj, gh, fast):
        from tagteam import db
        from tagteam.state import write_state
        write_state({"phase": "p", "type": "impl", "round": 1, "turn": "lead", "status": "ready"}, str(proj))
        gh.set({"run view": [{"out": {"status": "completed", "conclusion": "success", "name": "T", "jobs": []}}]})
        jid = _mk(proj, {"run": 1}, to_lead=True)
        _rc, rec, _ = _run_inproc(proj, jid)
        assert rec["delivery"]["interjection"] == "recorded", rec["delivery"]
        conn = db.connect(project_dir=str(proj))
        rows = db.get_interjections(conn, phase="p", cycle_type="impl")
        conn.close()
        assert len(rows) == 1 and rows[0]["by"] == f"job:{jid}" and rows[0]["target_role"] == "lead"
        assert "CI green" in rows[0]["note"]

    def test_to_lead_is_skipped_with_no_active_cycle(self, proj, gh, fast):
        gh.set({"run view": [{"out": {"status": "completed", "conclusion": "success", "name": "T", "jobs": []}}]})
        _rc, rec, _ = _run_inproc(proj, _mk(proj, {"run": 1}, to_lead=True))
        assert rec["delivery"]["interjection"] == "skipped: no active cycle"


# ---------------------------------------------------------------------------
# 6. A job never touches the turn slot
# ---------------------------------------------------------------------------

class TestNotATurn:
    def test_a_running_job_beside_a_turn_changes_no_turn_fact(self, proj, gh, tmp_path):
        from tagteam import cockpit_api as capi
        from tagteam.state import write_state
        write_state({"phase": "p", "type": "impl", "round": 1, "turn": "reviewer", "status": "ready"}, str(proj))
        facts0 = capi.status_facts(proj)
        noage = lambda h: {k: v for k, v in h.items() if k not in ("age_s", "age_of")}
        head0 = noage(capi.headline(facts0))
        avail0 = capi.launch_availability(proj)
        jid = _mk(proj, {"pr": 1})
        flag = tmp_path / "held"
        p = _holder(proj, jid, """
fd = jobs._hold_runner_lock(root, jid)
jobs.update_record(root, jid, lambda r: dict(r, status='running', pid=os.getpid()))
flag.write_text('1')
time.sleep(30)
""", flag)
        try:
            facts1 = capi.status_facts(proj)
            assert noage(capi.headline(facts1)) == head0 and capi.launch_availability(proj) == avail0
            assert facts1.get("inflight") == facts0.get("inflight")
        finally:
            p.kill(); p.wait()


# ---------------------------------------------------------------------------
# CLI + API surface
# ---------------------------------------------------------------------------

class TestSurface:
    def test_start_validation(self, proj):
        for args, msg in ((["start", "other"], "only job kind"),
                          (["start", "ci-watch"], "exactly one target"),
                          (["start", "ci-watch", "--pr", "1", "--run", "2"], "exactly one target"),
                          (["start", "ci-watch", "--pr", "x"], "integer"),
                          (["start", "ci-watch", "--pr", "1", "--interval", "2"], "at least 5"),
                          (["start", "ci-watch", "--pypi", "tagteam"], "PKG==VERSION"),
                          (["start", "ci-watch", "--workflow", "P"], "exactly one of --ref"),
                          (["start", "ci-watch", "--workflow", "P", "--sha", "zz"], "hex"),
                          (["start", "ci-watch", "--run", "1", "--ref", "v1"], "go with --workflow")):
            buf = io.StringIO()
            assert jobs.job_command(args, project_root=proj, out=buf) == 1, args
            assert msg in buf.getvalue(), (args, buf.getvalue())
        assert not (proj / jobs.JOBS_REL).exists()

    def test_list_status_log_and_json(self, proj, gh, fast):
        gh.set({"run view": [{"out": {"status": "completed", "conclusion": "success", "name": "T", "jobs": []}}]})
        jid = _mk(proj, {"run": 1})
        _run_inproc(proj, jid)
        buf = io.StringIO()
        jobs.job_command(["list"], project_root=proj, out=buf)
        assert jid in buf.getvalue() and "✓ green" in buf.getvalue()
        buf = io.StringIO()
        jobs.job_command(["log", jid, "-n", "1"], project_root=proj, out=buf)
        assert len(buf.getvalue().splitlines()) == 1
        buf = io.StringIO()
        jobs.job_command(["status", jid, "--json"], project_root=proj, out=buf)
        assert json.loads(buf.getvalue())["status"] == "succeeded"

    def test_the_cockpit_cancel_action_runs_the_cli_line(self, proj):
        from tagteam import cockpit_api as capi
        jid = _mk(proj, {"pr": 1})
        jobs.update_record(proj, jid, lambda r: dict(r, status="running"))
        assert capi.cli_preview("jobs/cancel", {"id": jid}) == f"tagteam job cancel {jid}"
        res = capi.run_action("jobs/cancel", {"id": jid}, proj)
        assert res["ok"] and "runner lost" in res["message"]
        assert capi.run_action("jobs/cancel", {"id": "../x"}, proj)["rc"] == 400

    def test_api_jobs_and_the_signature(self, proj):
        from tagteam import cockpit_api as capi
        from tests.test_server_cockpit import Served
        assert capi.events_signature(proj).get("jobs") is None
        jid = _mk(proj, {"pr": 1})
        sig1 = capi.events_signature(proj)["jobs"]
        assert sig1 and sig1[0] == 1
        time.sleep(0.01)
        jobs.update_record(proj, jid, lambda r: dict(r, note="changed"))
        assert capi.events_signature(proj)["jobs"] != sig1
        with Served(proj, "cockpit") as s:
            body = s.client.get("/api/jobs")["json"]
        assert [j["id"] for j in body["jobs"]] == [jid] and body["jobs"][0]["cancel_cli"] == f"tagteam job cancel {jid}"


# ---------------------------------------------------------------------------
# 7. The cockpit's Jobs strip — the shipped markup/CSS/slice in REAL Chromium
# ---------------------------------------------------------------------------

WEB = REPO / "tagteam" / "data" / "web"

_STRIP_SHIMS = r"""
function $(id) { return document.getElementById(id); }
function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function fmtAge(s) { return s + 's'; }
var ACTS = [];
function act(btn, url, data, opts) { ACTS.push({ url: url, data: data, title: opts && opts.confirm && opts.confirm.title }); }
var API = { jobs: [] };
function getJSON(p) { return Promise.resolve({ ok: true, body: { jobs: API.jobs } }); }
"""


def _run_strip_in_chromium(scenario_js: str, width: int = 1200) -> dict:
    import html as _html
    import re
    import tempfile
    from tests.test_cockpit_activity import _find_chromium
    chrome = _find_chromium()
    if not chrome:
        pytest.skip("no Chromium/Chrome found (set TAGTEAM_TEST_CHROME) — the Jobs strip test needs one")
    page_html = (WEB / "cockpit.html").read_text(encoding="utf-8")
    css = (WEB / "cockpit.css").read_text(encoding="utf-8")
    js = (WEB / "cockpit.js").read_text(encoding="utf-8")
    header = page_html[page_html.index('<header class="now" id="now">'):page_html.index("</header>") + len("</header>")]
    block = js[js.index("// ---------- Phase 72: Jobs strip"):js.index("// ---------- end Phase 72")]
    page = ("<!DOCTYPE html><html><head><meta charset='utf-8'><style>" + css + "</style></head><body>" + header
            + "<pre id='RESULT'></pre><script>var RESULT = null;\n(async function () { try {\n" + _STRIP_SHIMS + block
            + "\n" + scenario_js
            + "\n} catch (e) { RESULT = { scriptError: String(e && e.stack || e) }; }"
            + "\ndocument.getElementById('RESULT').textContent = JSON.stringify(RESULT); })();</script></body></html>")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "strip.html"
        f.write_text(page, encoding="utf-8")
        r = subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                            f"--window-size={width},900", f"--user-data-dir={d}/profile",
                            "--virtual-time-budget=2000", "--dump-dom", f.as_uri()],
                           capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr[-2000:]
    m = re.search(r"<pre id=\"RESULT\">(.*?)</pre>", r.stdout, re.S)
    assert m and m.group(1).strip(), "the page produced no RESULT — a script error? " + r.stderr[-1500:]
    out = json.loads(_html.unescape(m.group(1)))
    assert not (isinstance(out, dict) and out.get("scriptError")), out
    return out


def _api_job(proj, jid):
    return jobs.view(proj, jobs.read_record(proj, jid))


class TestJobsStripInARealBrowser:
    def test_absent_running_finished_and_lost(self, proj, tmp_path):
        # real payload rows, from real records
        run_id = _mk(proj, {"pr": 59})
        flag = tmp_path / "held"
        p = _holder(proj, run_id, """
fd = jobs._hold_runner_lock(root, jid)
jobs.update_record(root, jid, lambda r: dict(r, status='running', pid=os.getpid(), note='1/2 checks complete'))
flag.write_text('1')
time.sleep(30)
""", flag)
        try:
            running = _api_job(proj, run_id)
        finally:
            p.kill(); p.wait()
        done_id = _mk(proj, {"run": 7})
        jobs.update_record(proj, done_id, lambda r: dict(r, status="running"))
        jobs._commit(proj, done_id, "failed", {"summary": "Tests #7: failure — pytest",
                                               "failing": [{"name": "pytest", "url": "https://github.com/o/r/x"}],
                                               "log_tail": ["E assert 1 == 2"]})
        finished = _api_job(proj, done_id)
        lost = _api_job(proj, run_id)                       # the holder is dead now
        assert running["shown"] == "running" and lost["shown"] == "lost" and finished["shown"] == "failed"
        r = _run_strip_in_chromium("""
var out = {};
function snap() {
  var s = $('jobs-strip');
  return { hidden: s.classList.contains('hidden'), height: s.getBoundingClientRect().height,
           chips: Array.prototype.map.call($('jobs-chips').children, function (c) { return c.textContent; }),
           detail: $('jobs-detail').classList.contains('hidden') ? null : $('jobs-detail').textContent,
           cancel: $('job-cancel') ? $('job-cancel').textContent : null,
           overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth };
}
await loadJobs(); out.none = snap();
API.jobs = [RUNNING]; await loadJobs(); out.running = snap();
$('jobs-chips').children[0].click(); out.runningOpen = snap();
$('job-cancel').click(); out.acts = ACTS.slice();
API.jobs = [FINISHED, LOST]; await loadJobs(); out.afterSwap = snap();
$('jobs-chips').children[0].click(); out.finishedOpen = snap();
$('jobs-chips').children[1].click(); out.lostOpen = snap();
RESULT = out;
""".replace("RUNNING", json.dumps(running)).replace("FINISHED", json.dumps(finished)).replace("LOST", json.dumps(lost)))
        assert r["none"]["hidden"] and r["none"]["height"] == 0 and r["none"]["chips"] == []
        assert not r["running"]["hidden"] and r["running"]["height"] > 0
        assert r["running"]["chips"][0].startswith("ci-watch · PR #59 · running ")
        assert r["running"]["detail"] is None
        assert "1/2 checks complete" in r["runningOpen"]["detail"] and r["runningOpen"]["cancel"] == "Cancel job"
        assert r["acts"] == [{"url": "/api/jobs/cancel", "data": {"id": run_id}, "title": "Cancel ci-watch · PR #59?"}]
        # the job the reader opened stays open across a refresh and now says it is lost
        assert "The runner is gone" in r["afterSwap"]["detail"] and r["afterSwap"]["cancel"] == "Clear (cancel)"
        assert r["afterSwap"]["chips"] == ["ci-watch · run 7 · ✗ failed", "ci-watch · PR #59 · runner lost"]
        f = r["finishedOpen"]
        assert "Tests #7: failure — pytest" in f["detail"] and "✗ pytest" in f["detail"] and "E assert 1 == 2" in f["detail"]
        assert f["cancel"] is None and "delivery: not recorded" in f["detail"]
        lo = r["lostOpen"]
        assert "The runner is gone" in lo["detail"] and lo["cancel"] == "Clear (cancel)"
        assert not any(s["overflow"] for s in r.values() if isinstance(s, dict) and "overflow" in s)

    def test_a_long_label_does_not_scroll_the_page_at_phone_width(self, proj):
        jid = _mk(proj, {"workflow": "A very long workflow name that goes on and on for a while", "sha": "a" * 40,
                         "ref": "refs/heads/some-extremely-long-branch-name-for-testing-overflow"})
        row = _api_job(proj, jid)
        r = _run_strip_in_chromium("""
API.jobs = [ROW]; await loadJobs(); $('jobs-chips').children[0].click();
RESULT = { overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
           chipW: $('jobs-chips').children[0].getBoundingClientRect().width, vw: document.documentElement.clientWidth };
""".replace("ROW", json.dumps(row)), width=390)
        assert not r["overflow"] and r["chipW"] <= r["vw"]



# ---------------------------------------------------------------------------
# impl review r1: cancel and the deadline are honoured DURING polling and waiting;
# read-only at the shared mutation boundary; the final poll's metadata persists
# ---------------------------------------------------------------------------

def _cancel_after(proj, jid, delay):
    def go():
        time.sleep(delay)
        (proj / jobs._job_rel(jid, "cancel")).write_text("{}")
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


class TestCancelAndDeadlineDuringWork:
    def test_a_cancel_accepted_mid_poll_wins_over_the_answer(self, proj, fast, monkeypatch):
        jid = _mk(proj, {"pr": 1})
        jobs.update_record(proj, jid, lambda r: dict(r, status="starting"))
        seen = {}

        def poll(root, target, mem):
            seen["outcome"] = jobs.cancel(proj, jid)[0]          # the arbiter cancels while gh runs…
            return jobs._done("succeeded", "PR #1: green")        # …and then CI answers green
        monkeypatch.setattr(jobs, "poll", poll)
        _rc, rec, _ = _run_inproc(proj, jid)
        assert seen["outcome"] == "left-to-runner"
        assert rec["status"] == "cancelled" and fast[0][0] == "CI watch cancelled"

    def test_cancel_during_a_slow_gh_call_kills_it_promptly(self, proj, gh):
        gh.set({"pr view": [{"sleep": 30, "out": _pr([_check("a")])["out"]}]})
        jid = _mk(proj, {"pr": 1}, quiet=True)
        _cancel_after(proj, jid, 0.5)
        t0 = time.monotonic()
        _rc, rec, _ = _run_inproc(proj, jid)
        assert rec["status"] == "cancelled" and time.monotonic() - t0 < 3
        assert rec["attempts"] == 1

    def test_cancel_during_a_slow_log_fetch(self, proj, gh):
        gh.set({"run view": [{"out": {"status": "completed", "conclusion": "failure", "name": "T",
                                      "jobs": [{"name": "x", "conclusion": "failure", "url": "u"}]}}],
                "run log": [{"sleep": 30, "out": "never"}]})
        jid = _mk(proj, {"run": 3}, quiet=True)
        _cancel_after(proj, jid, 0.5)
        t0 = time.monotonic()
        _rc, rec, _ = _run_inproc(proj, jid)
        assert rec["status"] == "cancelled" and time.monotonic() - t0 < 3

    def test_cancel_during_a_slow_pypi_fetch(self, proj, monkeypatch):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                time.sleep(20)

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            monkeypatch.setenv(jobs.PYPI_SIMPLE_ENV, f"http://127.0.0.1:{srv.server_port}/simple/")
            jid = _mk(proj, {"pypi": "tagteam", "version": "9"}, quiet=True)
            _cancel_after(proj, jid, 0.5)
            t0 = time.monotonic()
            _rc, rec, _ = _run_inproc(proj, jid)
        finally:
            srv.shutdown()
        assert rec["status"] == "cancelled" and time.monotonic() - t0 < 3

    def test_the_deadline_expires_during_external_work(self, proj, gh):
        gh.set({"pr view": [{"sleep": 30, "out": _pr([_check("a")])["out"]}]})
        jid = _mk(proj, {"pr": 1}, timeout_s=1, quiet=True)
        t0 = time.monotonic()
        _rc, rec, _ = _run_inproc(proj, jid)
        assert rec["status"] == "timed-out" and time.monotonic() - t0 < 3
        assert "no answer in 1 s" in rec["result"]["summary"]

    def test_an_interval_longer_than_the_timeout_ends_at_the_deadline(self, proj, gh, monkeypatch):
        off = _virtual_clock(monkeypatch)
        monkeypatch.setattr(jobs, "_notify", lambda t, m: True)
        gh.set({"pr view": [_pr([_check("a", status="IN_PROGRESS", conclusion="")])]})
        jid = _mk(proj, {"pr": 1}, timeout_s=60, interval_s=120)
        _rc, rec, _ = _run_inproc(proj, jid)
        assert rec["status"] == "timed-out" and 59 <= off[0] <= 61, off[0]
        assert rec["attempts"] == 1

    def test_an_answer_that_arrives_after_the_deadline_is_not_accepted(self, proj, monkeypatch):
        off = _virtual_clock(monkeypatch)
        monkeypatch.setattr(jobs, "_notify", lambda t, m: True)

        def poll(root, target, mem):
            off[0] += 61                                   # the external work took 61 of 60 seconds
            return jobs._done("succeeded", "green")
        monkeypatch.setattr(jobs, "poll", poll)
        _rc, rec, _ = _run_inproc(proj, _mk(proj, {"pr": 1}, timeout_s=60))
        assert rec["status"] == "timed-out"

    def test_a_committed_result_survives_a_late_cancel_and_deadline(self, proj, gh, fast):
        gh.set({"run view": [{"out": {"status": "completed", "conclusion": "success", "name": "T", "jobs": []}}]})
        jid = _mk(proj, {"run": 1})
        _rc, rec, _ = _run_inproc(proj, jid)
        (proj / jobs._job_rel(jid, "cancel")).write_text("{}")
        assert jobs._commit(proj, jid, "timed-out", {"summary": "x"})[1] is False
        assert jobs.read_record(proj, jid)["status"] == "succeeded"


class TestReadOnlyAtTheBoundary:
    def test_the_cockpit_action_and_direct_calls_are_refused(self, proj, monkeypatch):
        from tagteam import cockpit_api as capi
        from tagteam.dualwrite import ReadOnlyError
        jid = _mk(proj, {"pr": 1})
        jobs.update_record(proj, jid, lambda r: dict(r, status="running"))
        d = proj / jobs._job_rel(jid)
        snap = {f.name: f.read_bytes() for f in d.iterdir()}
        monkeypatch.setenv("TAGTEAM_READ_ONLY", "1")
        res = capi.run_action("jobs/cancel", {"id": jid}, proj)
        assert res["ok"] is False and "refused" in res["message"]
        for call in (lambda: jobs.cancel(proj, jid), lambda: jobs.start(proj, "ci-watch", {"pr": 2}),
                     lambda: jobs.create(proj, "ci-watch", {"pr": 2}, interval_s=5, timeout_s=60, to_lead=False,
                                         quiet=False, by="t"),
                     lambda: jobs.update_record(proj, jid, lambda r: dict(r, note="x")),
                     lambda: jobs.prune(proj), lambda: jobs.run(proj, jid, out=io.StringIO())):
            with pytest.raises(ReadOnlyError):
                call()
        jobs._append_log(proj, jid, "must not be written")
        assert {f.name: f.read_bytes() for f in d.iterdir()} == snap
        assert jobs.list_ids(proj) == [jid]
        # reads still work, and create nothing
        assert jobs.jobs_payload(proj)["jobs"][0]["id"] == jid
        assert {f.name: f.read_bytes() for f in d.iterdir()} == snap


class TestFinalPollMetadata:
    def test_a_run_already_complete_on_the_first_view_keeps_its_pin(self, proj, gh, fast, repo):
        _old, new = repo
        gh.set({"run list": [{"out": [{"databaseId": 905, "headSha": new, "status": "completed"}]}],
                "run view": [{"out": {"status": "completed", "conclusion": "success", "name": "P", "jobs": []}}]})
        _rc, rec, _ = _run_inproc(proj, _mk(proj, {"workflow": "P", "sha": new}))
        assert rec["status"] == "succeeded" and rec["pinned_run"] == 905
        assert rec["attempts"] == 1 and rec["last_poll_at"]

    def test_the_final_poll_is_counted(self, proj, gh, fast):
        gh.set({"pr view": [_pr([]), _pr([_check("a", status="IN_PROGRESS", conclusion="")]), _pr([_check("a")])]})
        _rc, rec, _ = _run_inproc(proj, _mk(proj, {"pr": 1}))
        assert rec["status"] == "succeeded" and rec["attempts"] == 3
