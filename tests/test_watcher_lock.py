"""Phase 58 — one watcher per project, graceful SIGTERM, `serve` stops the
watchers it started (WatcherOwner), start_watcher refusal wording, and
activity items carrying the target cycle."""
from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from tagteam import cockpit_api as capi
from tagteam import db
from tagteam import headless as h
from tagteam import launch as L
from tagteam import procs
from tagteam import watcher as W
from tests.test_headless import project, fake_path, _init_cycle, REPO  # noqa: F401

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals and process groups")


def _env(**extra):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    for k in ("TAGTEAM_READ_ONLY", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(k, None)
    env.update(extra)
    return env


def _wait(pred, timeout=20.0, step=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return pred()


def _start_watch(root: Path, *args, **env) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "tagteam", "watch", "--interval", "1", *args],
                            cwd=str(root), env=_env(**env), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)


def _child_output(proc: subprocess.Popen, limit: int = 4000) -> str:
    """What a spawned watcher said, for a failure message. Bounded in time:
    a child still running is terminated, then killed, and a pipe that will
    not close is abandoned — a diagnostic must never become a hung gate."""
    if proc.poll() is not None:
        how = f"had already exited with code {proc.returncode}"
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            proc.wait(5)
            how = f"was still running; terminated for the dump (exit {proc.returncode})"
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                pass
            how = "was still running and ignored SIGTERM; killed for the dump"
    try:
        out, _ = proc.communicate(timeout=5)
        text = (out or b"").decode("utf-8", errors="replace")
    except (subprocess.TimeoutExpired, ValueError, OSError) as e:
        text = f"<output not readable: {e.__class__.__name__}>"
    return f"child pid {proc.pid} {how}; output:\n{text[-limit:] or '<none>'}"


def _wait_child(proc: subprocess.Popen, pred, timeout=20.0, step=0.1):
    """`_wait`, for a condition a spawned watcher is expected to bring about.
    Same timeout, same result on success; on failure the assertion carries the
    child's exit status and output (issue 10: a flake that could not explain
    itself because nothing read the pipe)."""
    v = _wait(pred, timeout=timeout, step=step)
    if not v:
        raise AssertionError(f"condition not met within {timeout}s — {_child_output(proc)}")
    return v


def _reap(*ps):
    for p in ps:
        if p is not None and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.wait(5)


def _lock_free(root: Path) -> bool:
    lk = W.acquire_watcher_lock(root, "probe")
    if lk is None:
        return False
    lk.release()
    return True


def _lock_held_by(root: Path, pid: int) -> bool:
    """Has `pid` taken the project's watcher lock? Read from the lock's own
    record, WITHOUT acquiring it. `_lock_free` takes the exclusive lock for a
    moment (and `procs.identity` runs a `ps` inside that moment); polled while
    a spawned watcher makes its single non-blocking attempt, the probe can win
    and the child is refused — `(pid <this test>, probe)`, exit 1. That was
    issue 10's flake (seen in two gate runs on 2026-09-20; the second one said
    so, thanks to `_child_output`). Use this while a child is starting;
    `_lock_free` is fine once nothing is racing for the lock."""
    rec = W.read_watcher_lock(root) or {}
    return rec.get("pid") == pid and rec.get("mode") != "probe"


class _NoBuild:
    def __init__(self):
        self.called = False

    def __call__(self, **kw):
        self.called = True
        return None


# ---------------------------------------------------------------------------
# §1 lock + refusal

class TestOneWatcherPerProject:
    def test_second_watcher_refused_other_project_starts(self, project, tmp_path, monkeypatch, capsys):
        first = _start_watch(project, "--mode", "notify", "--pidfile")
        try:
            rec = _wait_child(first, lambda: W.read_pidfile(project))
            assert rec and rec["pid"] == first.pid
            nobuild = _NoBuild()
            monkeypatch.setattr(W, "_build_processor", nobuild)
            assert W.watch(mode="notify", project_dir=str(project), interval=1) is False
            err = capsys.readouterr().err
            assert f"refused: another watcher is already running for this project (pid {first.pid}" in err
            assert f"kill {first.pid}" in err and not nobuild.called
            # the CLI exits 1
            second = subprocess.run([sys.executable, "-m", "tagteam", "watch", "--mode", "notify", "--interval", "1"],
                                    cwd=str(project), env=_env(), capture_output=True, text=True, timeout=60)
            assert second.returncode == 1 and "refused" in second.stderr
            # a different project is not affected: it passes the guard and builds
            other = tmp_path / "other"
            other.mkdir()
            (other / "tagteam.yaml").write_text("agents:\n  lead:\n    name: A\n  reviewer:\n    name: B\n")
            assert W.watch(mode="notify", project_dir=str(other), interval=1) is False
            assert nobuild.called
        finally:
            _reap(first)

    def test_lock_holder_without_pidfile_is_refused(self, project, monkeypatch, capsys):
        holder = subprocess.Popen(
            [sys.executable, "-c", "import sys, time; from tagteam.watcher import acquire_watcher_lock; "
             "l = acquire_watcher_lock(sys.argv[1], 'headless'); print('locked' if l else 'no', flush=True); "
             "time.sleep(60)", str(project)],
            env=_env(), stdout=subprocess.PIPE, text=True, start_new_session=True)
        try:
            assert holder.stdout.readline().strip() == "locked"
            nobuild = _NoBuild()
            monkeypatch.setattr(W, "_build_processor", nobuild)
            assert W.watch(mode="notify", project_dir=str(project), interval=1) is False
            err = capsys.readouterr().err
            assert f"(pid {holder.pid}, headless" in err and not nobuild.called
        finally:
            _reap(holder)

    def test_process_scan_only_watcher_is_refused(self, project, tmp_path, monkeypatch, capsys):
        """A watcher from a release without the lock (no lock, no pidfile) is
        still found by the process scan."""
        shim = tmp_path / "bin" / "tagteam"
        shim.parent.mkdir()
        shim.write_text("import time\ntime.sleep(60)\n")
        old = subprocess.Popen([sys.executable, str(shim), "watch", "--mode", "notify"], cwd=str(project),
                               start_new_session=True)
        try:
            assert _wait(lambda: capi.watcher_status(project).get("running"))
            assert _lock_free(project)
            nobuild = _NoBuild()
            monkeypatch.setattr(W, "_build_processor", nobuild)
            assert W.watch(mode="notify", project_dir=str(project), interval=1) is False
            assert f"(pid {old.pid}" in capsys.readouterr().err and not nobuild.called
        finally:
            _reap(old)

    def test_kill_9_releases_the_lock(self, project):
        first = _start_watch(project, "--mode", "notify", "--pidfile")
        try:
            assert _wait_child(first, lambda: W.read_pidfile(project))
            assert not _lock_free(project)
            os.kill(first.pid, signal.SIGKILL)
            first.wait(10)
            assert _wait(lambda: _lock_free(project), timeout=5)
            second = _start_watch(project, "--mode", "notify", "--pidfile")
            try:
                rec = _wait(lambda: (W.read_pidfile(project) or {}).get("pid") == second.pid)
                assert rec and second.poll() is None
            finally:
                _reap(second)
        finally:
            _reap(first)


# ---------------------------------------------------------------------------
# §2 SIGTERM

class TestSigterm:
    def test_sigterm_removes_pidfile_and_frees_lock(self, project):
        w = _start_watch(project, "--mode", "notify", "--pidfile")
        try:
            assert _wait_child(w, lambda: W.read_pidfile(project))
            os.kill(w.pid, signal.SIGTERM)
            assert w.wait(15) == 0
            # The contract is the clean exit. Since Phase 67 both loops also record one `stop`
            # (the watchdog event loop, installed in CI via `.[event]`, used to return silently).
            assert W.read_pidfile(project) is None and _lock_free(project)
            from tagteam import watchlog
            events = watchlog.read(project, 500)
            assert [e["kind"] for e in events].count("stop") == 1 and events[-1]["kind"] == "stop", events[-3:]
            assert watchlog.read_beat(project) is None
        finally:
            _reap(w)

    def test_sigterm_kills_in_flight_headless_turn(self, project, fake_path, tmp_path, monkeypatch):
        gc_pidfile = tmp_path / "gc.pid"
        _init_cycle(project)
        w = _start_watch(project, "--mode", "headless", "--pidfile", FAKE_AGENT_MODE="grandchild_hang",
                         FAKE_AGENT_PIDFILE=str(gc_pidfile), FAKE_AGENT_SLEEP="0.05")
        try:
            assert _wait(lambda: gc_pidfile.exists() and gc_pidfile.read_text().strip(), timeout=60)
            marker = h.read_inflight(project)
            assert marker and marker.get("pid")
            agent_pid, gpid = int(marker["pid"]), int(gc_pidfile.read_text())
            os.kill(w.pid, signal.SIGTERM)
            w.wait(30)
            assert _wait(lambda: not procs.pid_alive(agent_pid) and not procs.pid_alive(gpid), timeout=15)
            # recorded the way an interrupted turn already is: marker cleared, no watcher left behind
            assert h.read_inflight(project) is None
            assert W.read_pidfile(project) is None and _lock_free(project)
        finally:
            _reap(w)


# ---------------------------------------------------------------------------
# §3 WatcherOwner

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]
IGNORES_TERM = [sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "print('ready', flush=True); time.sleep(60)"]


class TestWatcherOwner:
    def test_spawn_after_close_creates_nothing(self, tmp_path):
        owner = L.WatcherOwner()
        assert owner.close() == []
        assert owner.spawn(SLEEPER, source="t") is None and owner.children() == []

    def test_stop_exited_and_term_ignoring_children(self, tmp_path):
        owner = L.WatcherOwner()
        live = owner.spawn(SLEEPER, source="t", start_new_session=True)
        done = owner.spawn([sys.executable, "-c", "raise SystemExit(3)"], source="t")
        stubborn = owner.spawn(IGNORES_TERM, source="t", stdout=subprocess.PIPE, text=True, start_new_session=True)
        try:
            done.wait(10)
            assert stubborn.stdout.readline().strip() == "ready"
            lines = L.stop_owned_watchers(tmp_path, owner, wait_s=1.0)
            assert f"Stopped watcher pid {live.pid} (started by this cockpit)." in lines
            assert f"Watcher pid {done.pid} (started by this cockpit) had already exited (code 3)." in lines
            assert f"Watcher pid {stubborn.pid} did not exit within 1 s — stop it with: kill {stubborn.pid}" in lines
            assert live.poll() is not None and stubborn.poll() is None
            assert owner.spawn(SLEEPER, source="t") is None
        finally:
            _reap(live, stubborn)

    def test_readiness_timeout_is_owned_and_stopped(self, project):
        owner = L.WatcherOwner()
        res = L.start_watcher(project, mode="notify", wait_s=0.01, owner=owner)
        kids = owner.children()
        try:
            assert res.get("started_unverified") and not res["ok"]
            assert [k["pid"] for k in kids] == [res["pid"]] and kids[0]["source"] == "watch-start"
            lines = L.stop_owned_watchers(project, owner)
            assert f"Stopped watcher pid {res['pid']} (started by this cockpit)." in lines
            assert kids[0]["proc"].poll() is not None and W.read_pidfile(project) is None
        finally:
            _reap(*(k["proc"] for k in kids))

    def test_external_and_already_running_watchers_untouched(self, project):
        external = _start_watch(project, "--mode", "notify", "--pidfile")
        try:
            assert _wait_child(external, lambda: W.read_pidfile(project))
            owner = L.WatcherOwner()
            res = L.start_watcher(project, mode="notify", owner=owner)
            assert res.get("already") and owner.children() == []
            lines = L.stop_owned_watchers(project, owner)
            assert lines == [f"Watcher pid {external.pid} was not started by this cockpit — left running."]
            assert external.poll() is None
        finally:
            _reap(external)

    def test_reused_launch_watcher_is_not_recorded(self, tmp_path, monkeypatch):
        from tests.test_launchpad import _proj
        from tagteam.config import read_config
        monkeypatch.setenv("TAGTEAM_PORT_LEASE_DIR", str(tmp_path / "leases"))
        p = _proj(tmp_path)
        cfg = read_config(p / "tagteam.yaml")
        it = L.launch_intent(p)
        key = L.launch_key(it)
        dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
        conn = db.connect(project_dir=str(p))
        try:
            db.claim_launch(conn, key=key, ts="t", intent_json=json.dumps(it), owner_pid=dead.pid, owner_ident=None)
            db.update_launch(conn, key, ts="t", watcher_pid=os.getpid(), watcher_ident=procs.identity(os.getpid()))
        finally:
            conn.close()
        owner = L.WatcherOwner()
        L.launch(p, intent=it, config=cfg, by="t", send=lambda: {"n": 1, "status": "ok"}, watcher_owner=owner)
        st, res = L.launch(p, intent=it, config=cfg, by="t", retry=True, send=lambda: {"n": 1, "status": "ok"},
                           watcher_owner=owner)
        assert st == 200 and res["watcher"]["reused"] and owner.children() == []


# ---------------------------------------------------------------------------
# §3 real `tagteam serve` process exit with a start paused inside the critical section

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _http(port, method, path, body=None, token=None, timeout=60):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=(json.dumps(body).encode() if body is not None else None))
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Tagteam-Token", token)
        req.add_header("Origin", f"http://127.0.0.1:{port}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # the server went away mid-request (expected here)
        return None, str(e).encode()


class TestServeProcessExit:
    def _serve(self, root: Path, tmp_path: Path):
        port = _free_port()
        env = _env(TAGTEAM_TEST_WATCHER_SPAWN_PAUSE_S=str(self.PAUSE_S), TAGTEAM_PORT_LEASE_DIR=str(tmp_path / "leases"))
        srv = subprocess.Popen([sys.executable, "-m", "tagteam", "serve", "--dir", str(root), "--port", str(port),
                                "--theme", "cockpit"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True)
        page = _wait(lambda: (lambda r: r[1] if r[0] == 200 else None)(_http(port, "GET", "/", timeout=2)), timeout=30)
        assert page, "serve did not come up"
        token = re.search(rb'name="tagteam-token" content="([0-9a-f]+)"', page).group(1).decode()
        return srv, port, token

    PAUSE_S = 6.0

    def _paused_start_then_sigterm(self, root, srv, request):
        t = threading.Thread(target=request, daemon=True)
        started = time.monotonic()
        t.start()
        # the watcher child exists (it is running) while its registration is still paused
        ws = _wait(lambda: (lambda s: s if s.get("running") else None)(capi.watcher_status(root)), timeout=30)
        assert ws, "the watcher child never started"
        killed = time.monotonic()
        # Popen happened after `started`, and the pause follows Popen: a kill this early lands inside it
        assert killed - started < self.PAUSE_S - 0.5, "too slow to hit the paused window"
        os.kill(srv.pid, signal.SIGTERM)
        out = srv.communicate(timeout=60)[0].decode()
        # shutdown waited for the paused registration instead of exiting past it
        assert time.monotonic() - started >= self.PAUSE_S - 0.5
        return ws["pid"], out

    def test_watch_start_entry_point(self, tmp_path):
        from tests.test_launchpad import _proj
        root = _proj(tmp_path)
        srv, port, token = self._serve(root, tmp_path)
        try:
            pid, out = self._paused_start_then_sigterm(
                root, srv, lambda: _http(port, "POST", "/api/watch/start", {"mode": "notify"}, token))
            assert srv.returncode == 0
            assert f"Stopped watcher pid {pid} (started by this cockpit)." in out, out
            assert _wait(lambda: not procs.pid_alive(pid), timeout=10)
            assert W.read_pidfile(root) is None and not capi.watcher_status(root).get("running")
        finally:
            _reap(srv)

    def test_start_launch_entry_point(self, tmp_path):
        from tests.test_launchpad import _proj
        root = _proj(tmp_path)
        srv, port, token = self._serve(root, tmp_path)
        try:
            code, raw = _http(port, "GET", "/api/start", token=token, timeout=10)
            intent = json.loads(raw)["intent"]
            assert intent.get("command")
            pid, out = self._paused_start_then_sigterm(
                root, srv, lambda: _http(port, "POST", "/api/start/launch",
                                         {"intent": intent, "ensure_watcher": True, "mode": "notify"}, token))
            assert srv.returncode == 0
            assert f"Stopped watcher pid {pid} (started by this cockpit)." in out, out
            assert _wait(lambda: not procs.pid_alive(pid), timeout=10)
            assert W.read_pidfile(root) is None and not capi.watcher_status(root).get("running")
        finally:
            _reap(srv)


# ---------------------------------------------------------------------------
# §1 start_watcher wording, §4 activity target identity

def test_start_watcher_reports_refusal_as_already_running(project):
    external = _start_watch(project, "--mode", "notify")    # no pidfile: only the scan and the lock see it
    try:
        assert _wait_child(external, lambda: _lock_held_by(project, external.pid))
        real = capi.watcher_status
        try:
            capi.watcher_status = lambda *a, **k: {"running": False}   # the pre-spawn check misses it
            res = L.start_watcher(project, mode="notify", wait_s=20)
        finally:
            capi.watcher_status = real
        assert res["ok"] is False and res.get("already") and res["pid"] == external.pid
        assert res["message"] == f"a watcher is already running (pid {external.pid})"
    finally:
        _reap(external)


def test_activity_items_use_target_cycle(tmp_path):
    conn = db.connect(project_dir=str(tmp_path))
    try:
        db.add_usage(conn, ts="2026-09-15T00:00:00+00:00", status="ok", phase="old-phase", type="impl", round=3,
                     role="lead", kind="cycle", target_phase="new-phase", target_type="plan", target_round=1)
        db.add_usage(conn, ts="2026-09-15T00:01:00+00:00", status="ok", phase="old-phase", type="impl", round=3,
                     role="reviewer", kind="cycle")
        items = capi._activity_from_db(conn, 10)
    finally:
        conn.close()
    by_role = {i["role"]: i for i in items}
    assert (by_role["lead"]["phase"], by_role["lead"]["type"], by_role["lead"]["round"]) == ("new-phase", "plan", 1)
    assert (by_role["reviewer"]["phase"], by_role["reviewer"]["type"], by_role["reviewer"]["round"]) == ("old-phase", "impl", 3)


def test_activity_items_on_schema_without_target_columns(tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    raw = sqlite3.connect(p)
    raw.execute("CREATE TABLE usage (id INTEGER PRIMARY KEY, ts TEXT, phase TEXT, type TEXT, round INTEGER, role TEXT,"
                " agent TEXT, status TEXT, duration_ms INTEGER, log_path TEXT, kind TEXT)")
    raw.execute("INSERT INTO usage (ts, phase, type, round, role, status, kind) VALUES "
                "('2026-09-15T00:00:00+00:00', 'p', 'plan', 2, 'reviewer', 'ok', 'cycle')")
    raw.commit()
    items = capi._activity_from_db(raw, 10)
    raw.close()
    assert [(i["phase"], i["type"], i["round"]) for i in items] == [("p", "plan", 2)]


# ---------------------------------------------------------------------------
# Phase 65 — a wait on a spawned watcher explains itself when it fails

def test_wait_child_reports_a_child_that_already_exited(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "import sys; print('boom: no project here'); sys.exit(3)"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    child.wait(10)
    with pytest.raises(AssertionError) as e:
        _wait_child(child, lambda: False, timeout=0.3)
    msg = str(e.value)
    assert "condition not met within 0.3s" in msg
    assert "had already exited with code 3" in msg and "boom: no project here" in msg


def test_wait_child_terminates_and_reports_a_child_still_running(tmp_path):
    code = "import sys, time; print('started, waiting', flush=True); time.sleep(120)"
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             start_new_session=True)
    try:
        t0 = time.monotonic()
        with pytest.raises(AssertionError) as e:
            _wait_child(child, lambda: False, timeout=0.5)
        assert time.monotonic() - t0 < 15                       # bounded: never a hung gate
        msg = str(e.value)
        assert "was still running; terminated for the dump" in msg and "started, waiting" in msg
        assert child.poll() is not None
    finally:
        _reap(child)


def test_wait_child_kills_a_child_that_ignores_sigterm(tmp_path):
    code = ("import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print('ignoring TERM', flush=True); time.sleep(120)")
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             start_new_session=True)
    try:
        assert child.stdout.readline().strip() == b"ignoring TERM"     # handler installed before we signal
        with pytest.raises(AssertionError) as e:
            _wait_child(child, lambda: False, timeout=0.3)
        assert "ignored SIGTERM; killed for the dump" in str(e.value) and child.poll() is not None
    finally:
        _reap(child)


def test_wait_child_is_wait_on_success():
    child = subprocess.Popen([sys.executable, "-c", "pass"], stdout=subprocess.PIPE, start_new_session=True)
    try:
        assert _wait_child(child, lambda: "value", timeout=1) == "value"
    finally:
        _reap(child)
