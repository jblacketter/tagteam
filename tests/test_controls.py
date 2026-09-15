"""Phase 32 tests: pause/resume (all watcher modes + resume re-dispatch),
cancel-turn (PID binding, live kill, cancelled outcome), interject
(provenance, delivery, targeting, scoping, list/retire, rounds attach,
render), usage aggregation/CLI, fingerprint retry gate (e2e via fake
agent), per-role timeout, rollback, procs helpers."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tagteam import controls, db, headless as h, procs, usage as usage_mod
from tagteam import cycle as cycle_mod
from tagteam import state as state_mod
from tagteam import fingerprint as fpm
from tagteam.config import read_config

# Reuse the Phase 31 fixtures/helpers.
from tests.test_headless import (  # noqa: F401
    project, fake_path, _engine, _init_cycle, _usage_rows, _diag_kinds, _pid_alive, STD_CMD,
)


def _injected_block(prompt: str) -> str:
    """The composed prompt's interjection block only — excludes the copy of
    the handoff contract (SKILL.md), which itself mentions the header."""
    return prompt.split("=== COMMAND ===", 1)[0]


# Real process inspection (`ps`, /proc, Win32_Process) may be denied in
# sandboxed review environments; the identity-binding unit tests are
# hermetic (helpers patched) and the real-process checks are gated.
_PROC_INSPECTION = procs.identity(os.getpid()) is not None and procs.parent_pid(os.getpid()) is not None
needs_proc_inspection = pytest.mark.skipif(
    not _PROC_INSPECTION, reason="process inspection (ps/proc/CIM) unavailable in this sandbox")


def _sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=str(cwd), check=True, capture_output=True)


def _git_init(project: Path) -> None:
    _git(project, "init", "-q")
    (project / ".gitignore").write_text(".tagteam/\nhandoff-state.json\n")
    (project / "tracked.txt").write_text("hello\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-qm", "init")


# ---------------------------------------------------------------------------
# pause / resume
# ---------------------------------------------------------------------------

class TestPauseResume:
    def test_pause_writes_marker_and_resume_clears(self, project, capsys):
        assert controls.pause_command(["--reason", "hand review", "--by", "jack"],
                                      project_root=project) == 0
        info = h.read_pause(project)
        assert info["reason"] == "hand review" and info["by"] == "jack" and info["source"] == "cli"
        # idempotent update
        assert controls.pause_command(["--reason", "still"], project_root=project) == 0
        assert h.read_pause(project)["reason"] == "still"
        assert controls.resume_command([], project_root=project) == 0
        out = capsys.readouterr().out
        assert "Resumed" in out and "still" in out
        assert h.read_pause(project) is None
        assert controls.resume_command([], project_root=project) == 1
        assert controls.resume_command(["--quiet"], project_root=project) == 0

    def test_resume_shows_failed_turn_context(self, project, capsys):
        h.write_pause(project, {"reason": "headless turn no_round: x", "outcome": "no_round",
                                "phase": "p", "type": "plan", "round": 2, "role": "lead",
                                "log_path": "/tmp/t.log"})
        # a CLI pause on top keeps the failure visible
        controls.pause_command(["--reason", "hold"], project_root=project)
        assert controls.resume_command([], project_root=project) == 0
        out = capsys.readouterr().out
        assert "no_round" in out and "/tmp/t.log" in out

    def test_pause_rejects_positional(self, project, capsys):
        assert controls.pause_command(["oops"], project_root=project) == 1


class TestWatcherPauseAllModes:
    def _proc(self, mode, project):
        from tagteam.watcher import _StateProcessor
        return _StateProcessor(
            mode=mode, lead_name="Claude", reviewer_name="Codex", lead_pane="l",
            reviewer_pane="r", lead_session_id="ls" if mode == "iterm2" else None,
            reviewer_session_id="rs" if mode == "iterm2" else None, confirm=False,
            timeout_minutes=30, project_dir=str(project), max_retries=1,
            retry_delay=0, pre_send_delay=0)

    def _state(self, seq, **extra):
        s = {"seq": seq, "status": "ready", "turn": "lead", "command": "/handoff",
             "phase": "p", "type": "plan", "round": 1, "updated_at": f"t{seq}"}
        s.update(extra); return s

    @pytest.mark.parametrize("mode,target", [
        ("notify", "tagteam.watcher.notify_macos"),
        ("tmux", "tagteam.watcher.send_tmux_keys"),
        ("iterm2", "tagteam.watcher.send_iterm_command"),
    ])
    def test_paused_skips_dispatch_then_resume_redispatches_once(self, project, mode, target):
        controls.pause_command(["--reason", "hold"], project_root=project)
        p = self._proc(mode, project)
        from contextlib import nullcontext
        quiet = (patch("tagteam.watcher.notify_macos") if mode != "notify" else nullcontext())
        with patch(target) as send, quiet:
            p.tick(self._state(1))
            send.assert_not_called()
            assert p.last_ready_send_time is None   # watchdog not armed while paused
            p.tick(self._state(1))                  # still paused, same seq
            send.assert_not_called()
            controls.resume_command([], project_root=project)
            p.tick(self._state(1))                  # marker gone → one re-dispatch
            assert send.call_count == 1
            p.tick(self._state(1))                  # not again
            assert send.call_count == 1

    def test_headless_paused_then_resume_redispatches_once(self, project):
        controls.pause_command(["--reason", "hold"], project_root=project)
        p = self._proc("headless", project)
        eng = MagicMock(); eng.paused.return_value = None
        p.engine = eng
        p.tick(self._state(1))
        eng.run_owed_turn.assert_not_called()
        controls.resume_command([], project_root=project)
        p.tick(self._state(1))
        assert eng.run_owed_turn.call_count == 1
        p.tick(self._state(1))
        assert eng.run_owed_turn.call_count == 1

    def test_pause_log_rate_limited(self, project, capsys):
        controls.pause_command(["--reason", "hold"], project_root=project)
        p = self._proc("notify", project)
        with patch("tagteam.watcher.notify_macos"):
            p.tick(self._state(1))
            p.tick(self._state(1)); p.tick(self._state(1))
        out = capsys.readouterr().out
        assert out.count("PAUSED") == 1


class TestPauseVisibility:
    """A stale pause marker must be visible where it matters: at the lead's
    hand-off write, in the watcher log (aged), and in `cycle status` /
    `state` (docs/tagteam-issue-stale-pause-marker-2026-08-22.md)."""

    def _stale_marker(self, project, days=4, hours=15, **extra):
        from datetime import datetime, timedelta, timezone
        ts = (datetime.now(timezone.utc) - timedelta(days=days, hours=hours)).isoformat()
        payload = {"reason": "roadmap run complete; no active cycle", "by": "claude",
                   "source": "cli", "ts": ts}
        payload.update(extra)
        h.write_pause(project, payload)

    def test_pause_age_and_describe(self, project):
        assert h.pause_age(None) == "?"
        assert h.pause_age({"ts": "garbage"}) == "?"
        assert h.pause_age({"ts": h._now_iso()}) == "just now"
        self._stale_marker(project, state={"phase": "old", "type": "impl", "round": 3,
                                           "status": "done", "turn": None, "seq": 9})
        line = h.describe_pause(h.read_pause(project))
        assert line.startswith("PAUSED (4d 15h ago, by claude): roadmap run complete")
        assert "[set on old/impl r3, status done]" in line
        assert "just now ago" not in h.describe_pause({"ts": h._now_iso(), "reason": "r"})

    def test_pause_command_records_state_context(self, project):
        _init_cycle(project)
        controls.pause_command(["--reason", "hold"], project_root=project)
        on = h.read_pause(project)["state"]
        assert on["phase"] == "feat-x" and on["type"] == "plan" and on["turn"] == "reviewer"

    def test_cycle_init_warns_when_paused(self, project, capsys):
        self._stale_marker(project)
        assert cycle_mod.cycle_command(["init", "--phase", "p1", "--type", "plan",
                                        "--updated-by", "Claude", "--content", "c", "--no-gate"]) == 0
        out = capsys.readouterr().out
        assert "Cycle created" in out
        assert "note: watcher dispatch is PAUSED (4d 15h ago, by claude)" in out
        assert "Codex will NOT be dispatched until `tagteam resume`" in out

    def test_cycle_init_silent_when_not_paused(self, project, capsys):
        assert cycle_mod.cycle_command(["init", "--phase", "p1", "--type", "plan",
                                        "--updated-by", "Claude", "--content", "c", "--no-gate"]) == 0
        assert "PAUSED" not in capsys.readouterr().out

    def test_cycle_add_warns_only_when_a_turn_is_handed_over(self, project, capsys):
        _init_cycle(project)
        self._stale_marker(project)
        # reviewer → lead: a turn is owed → note (named for the lead)
        assert cycle_mod.cycle_command(["add", "--phase", "feat-x", "--type", "plan", "--role", "reviewer",
                                        "--action", "REQUEST_CHANGES", "--round", "1",
                                        "--updated-by", "Codex", "--content", "fix"]) == 0
        out = capsys.readouterr().out
        assert "PAUSED" in out and "Claude will NOT be dispatched" in out
        # lead → reviewer
        assert cycle_mod.cycle_command(["add", "--phase", "feat-x", "--type", "plan", "--role", "lead",
                                        "--action", "SUBMIT_FOR_REVIEW", "--round", "2",
                                        "--updated-by", "Claude", "--content", "done", "--no-gate"]) == 0
        assert "Codex will NOT be dispatched" in capsys.readouterr().out
        # APPROVE closes the cycle: nobody is owed, no note
        assert cycle_mod.cycle_command(["add", "--phase", "feat-x", "--type", "plan", "--role", "reviewer",
                                        "--action", "APPROVE", "--round", "2",
                                        "--updated-by", "Codex", "--content", "ok"]) == 0
        assert "PAUSED" not in capsys.readouterr().out

    def test_state_set_warns_when_handing_a_turn(self, project, capsys):
        _init_cycle(project)
        self._stale_marker(project)
        assert state_mod.state_command(["set", "--turn", "reviewer", "--status", "ready"]) == 0
        assert "Codex will NOT be dispatched" in capsys.readouterr().out
        assert state_mod.state_command(["set", "--status", "done"]) == 0
        assert "PAUSED" not in capsys.readouterr().out

    def test_cycle_status_and_state_show_dispatch(self, project, capsys):
        _init_cycle(project)
        assert cycle_mod.cycle_command(["status", "--phase", "feat-x", "--type", "plan"]) == 0
        assert "dispatch: not paused" in capsys.readouterr().out
        self._stale_marker(project)
        assert cycle_mod.cycle_command(["status", "--phase", "feat-x", "--type", "plan"]) == 0
        out = capsys.readouterr().out
        assert "dispatch: PAUSED (4d 15h ago, by claude)" in out and "tagteam resume" in out
        assert state_mod.state_command([]) == 0
        assert "Dispatch:   PAUSED (4d 15h ago" in capsys.readouterr().out

    def test_watcher_log_ages_the_reason(self, project, capsys):
        self._stale_marker(project)
        p = TestWatcherPauseAllModes()._proc("notify", project)
        with patch("tagteam.watcher.notify_macos"):
            p.tick(TestWatcherPauseAllModes()._state(1))
        out = capsys.readouterr().out
        assert "!! PAUSED (4d 15h ago, by claude): roadmap run complete" in out


# ---------------------------------------------------------------------------
# cancel-turn
# ---------------------------------------------------------------------------

class TestCancelTurn:
    def test_nothing_in_flight(self, project, capsys):
        assert controls.cancel_turn_command([], project_root=project) == 1
        assert "Nothing in flight" in capsys.readouterr().out

    @needs_proc_inspection
    def test_unrelated_sleeper_not_killed(self, project, capsys):
        sleeper = _sleeper()
        fake_watcher = _sleeper()
        try:
            time.sleep(0.3)
            h.turns_dir(project).mkdir(parents=True, exist_ok=True)
            h.inflight_path(project).write_text(json.dumps({
                "stem": "old", "pid": sleeper.pid, "watcher_pid": fake_watcher.pid,
                "watcher_ident": procs.identity(fake_watcher.pid),
                "child_ident": procs.identity(sleeper.pid),
                "started_at": "2026-01-01T00:00:00+00:00"}))
            rc = controls.cancel_turn_command([], project_root=project)
            out = capsys.readouterr().out
            assert rc == 1 and "Refusing" in out and "parent" in out
            assert h.read_inflight(project) is None            # stale metadata cleaned
            assert h.read_cancel(project) is None
            assert sleeper.poll() is None                        # still alive
        finally:
            sleeper.kill(); fake_watcher.kill()

    @pytest.mark.parametrize("mutate,needle", [
        (lambda d, s, w: d.update(pid=None), "no child pid"),
        (lambda d, s, w: d.update(pid=w, watcher_pid=s), "this process"),
        (lambda d, s, w: d.update(pid=s, watcher_pid=s), "equals the watcher"),
        (lambda d, s, w: d.update(watcher_pid=None), "no watcher pid"),
        (lambda d, s, w: d.update(child_ident=None), "identities missing"),
        (lambda d, s, w: d.update(child_ident="999:bogus"), "identity mismatch"),
        (lambda d, s, w: d.update(watcher_ident="999:bogus"), "identity mismatch"),
    ])
    def test_bind_rejections(self, mutate, needle, monkeypatch):
        """Hermetic: process helpers are stubbed so this runs in sandboxes
        that deny ps/proc/CIM. child pid 200 is alive with parent 100
        (the watcher); identities are stable strings."""
        idents = {200: "200:c-start", 100: "100:w-start"}
        monkeypatch.setattr(procs, "pid_alive", lambda pid: pid in idents)
        monkeypatch.setattr(procs, "identity", lambda pid: idents.get(pid))
        monkeypatch.setattr(procs, "parent_pid", lambda pid: 100 if pid == 200 else None)
        me = 100
        d = {"stem": "s", "pid": 200, "watcher_pid": me,
             "watcher_ident": "100:w-start", "child_ident": "200:c-start"}
        mutate(d, 200, me)
        ok, why = controls.bind_inflight(d, self_pid=me)
        assert not ok and needle in why, why

    def test_bind_accepts_and_rejects_hermetic(self, monkeypatch):
        idents = {200: "200:c-start", 100: "100:w-start"}
        monkeypatch.setattr(procs, "pid_alive", lambda pid: pid in idents)
        monkeypatch.setattr(procs, "identity", lambda pid: idents.get(pid))
        monkeypatch.setattr(procs, "parent_pid", lambda pid: 100 if pid == 200 else None)
        d = {"stem": "s", "pid": 200, "watcher_pid": 100,
             "watcher_ident": "100:w-start", "child_ident": "200:c-start"}
        assert controls.bind_inflight(d, self_pid=-1) == (True, "bound")
        # numeric pids match but a recorded creation identity differs → no kill
        for key in ("child_ident", "watcher_ident"):
            bad = dict(d, **{key: "reused:other-start"})
            ok, why = controls.bind_inflight(bad, self_pid=-1)
            assert not ok and "identity mismatch" in why
        # unverifiable identity / parent → reject
        monkeypatch.setattr(procs, "identity", lambda pid: None)
        ok, why = controls.bind_inflight(d, self_pid=-1)
        assert not ok and "unverifiable" in why
        monkeypatch.setattr(procs, "identity", lambda pid: idents.get(pid))
        monkeypatch.setattr(procs, "parent_pid", lambda pid: None)
        ok, why = controls.bind_inflight(d, self_pid=-1)
        assert not ok and "unverifiable" in why
        # wrong parent → reject
        monkeypatch.setattr(procs, "parent_pid", lambda pid: 999)
        ok, why = controls.bind_inflight(d, self_pid=-1)
        assert not ok and "not the recorded watcher" in why
        # dead pid → reject
        monkeypatch.setattr(procs, "pid_alive", lambda pid: False)
        ok, why = controls.bind_inflight(d, self_pid=-1)
        assert not ok and "not alive" in why

    @needs_proc_inspection
    def test_bind_accepts_own_child_real(self):
        sleeper = _sleeper()
        try:
            time.sleep(0.2)
            me = os.getpid()
            d = {"stem": "s", "pid": sleeper.pid, "watcher_pid": me,
                 "watcher_ident": procs.identity(me), "child_ident": procs.identity(sleeper.pid)}
            ok, why = controls.bind_inflight(d, self_pid=-1)
            assert ok, why
        finally:
            sleeper.kill()

    @needs_proc_inspection
    def test_live_turn_cancelled_end_to_end(self, project, fake_path, monkeypatch, tmp_path):
        monkeypatch.setenv("FAKE_AGENT_MODE", "grandchild_hang")
        pidfile = tmp_path / "gc.pid"
        monkeypatch.setenv("FAKE_AGENT_PIDFILE", str(pidfile))
        st = _init_cycle(project)
        eng = _engine(project, timeout_minutes=5)
        results = {}
        th = threading.Thread(target=lambda: results.update(r=eng.run_owed_turn(st)))
        th.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            inf = h.read_inflight(project)
            if inf and inf.get("pid") and inf.get("child_ident") and pidfile.exists():
                break
            time.sleep(0.1)
        inf = h.read_inflight(project)
        assert inf and inf["watcher_pid"] == os.getpid() and inf["watcher_ident"]
        rc = controls.cancel_turn_command(["--by", "jack"], project_root=project)
        assert rc == 0
        th.join(60)
        res = results["r"]
        assert res.outcome == "cancelled" and "cancelled by jack" in res.reason
        assert eng.paused()["outcome"] == "cancelled" and "cancelled by jack" in eng.paused()["reason"]
        assert _usage_rows(project)[-1]["status"] == "cancelled"
        assert "headless_turn_cancelled" in _diag_kinds(project)
        assert h.read_cancel(project) is None
        gpid = int(pidfile.read_text())
        deadline = time.monotonic() + 10
        while _pid_alive(gpid) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not _pid_alive(gpid)

    def test_stale_cancel_marker_for_other_stem_ignored(self, project, fake_path):
        st = _init_cycle(project)
        h.write_cancel(project, {"stem": "someone-else", "pid": 1, "by": "x"})
        eng = _engine(project)
        res = eng.run_owed_turn(st)
        assert res.outcome == "ok"
        assert h.read_cancel(project) is None


# ---------------------------------------------------------------------------
# interject
# ---------------------------------------------------------------------------

class TestInterject:
    def test_record_with_owed_identity_and_prompt_delivery(self, project, fake_path,
                                                          monkeypatch, tmp_path, capsys):
        st = _init_cycle(project)  # reviewer owed r1
        assert controls.interject_command(["prefer the smaller diff", "--by", "jack"],
                                          project_root=project) == 0
        out = capsys.readouterr().out
        assert "#1 recorded for feat-x/plan" in out and "reviewer owed" in out
        conn = db.connect(project_dir=str(project))
        row = db.get_interjections(conn)[0]; conn.close()
        assert (row["phase"], row["type"], row["round"], row["turn"]) == ("feat-x", "plan", 1, "reviewer")
        assert row["by"] == "jack" and row["target_role"] is None
        assert json.loads(row["observed_state"])["status"] == "ready"
        cap = tmp_path / "cap.json"; monkeypatch.setenv("FAKE_AGENT_CAPTURE", str(cap))
        eng = _engine(project)
        res = eng.run_owed_turn(st)
        assert res.outcome == "ok"
        prompt = json.loads(cap.read_text())["prompt"]
        block = _injected_block(prompt)
        assert h.INTERJECTIONS_HEADER in block and "prefer the smaller diff" in block
        assert "jack (→ next turn)" in block
        conn = db.connect(project_dir=str(project))
        row = db.get_interjections(conn)[0]; conn.close()
        assert row["delivered_role"] == "reviewer" and row["delivered_round"] == 1
        assert row["delivered_stem"] == res.stem and row["delivered_ts"]
        # not repeated on the next turn
        st2 = state_mod.read_state(str(project))
        eng.run_owed_turn(st2)
        prompt2 = json.loads(cap.read_text())["prompt"]
        assert h.INTERJECTIONS_HEADER not in _injected_block(prompt2)

    def test_targeted_note_waits_for_its_role(self, project, fake_path, monkeypatch, tmp_path):
        st = _init_cycle(project)  # reviewer owed
        cycle_mod.add_round("feat-x", "plan", "reviewer", "REQUEST_CHANGES", 1, "r",
                            str(project), updated_by="Codex")
        st = state_mod.read_state(str(project))  # lead owed r2
        controls.interject_command(["for the reviewer only", "--to", "reviewer"], project_root=project)
        cap = tmp_path / "cap.json"; monkeypatch.setenv("FAKE_AGENT_CAPTURE", str(cap))
        eng = _engine(project)
        assert eng.run_owed_turn(st).outcome == "ok"     # lead turn
        assert "for the reviewer only" not in json.loads(cap.read_text())["prompt"]
        conn = db.connect(project_dir=str(project))
        assert db.get_interjections(conn)[0]["delivered_ts"] is None; conn.close()
        st = state_mod.read_state(str(project))          # reviewer owed r2
        res = eng.run_owed_turn(st)
        assert res.outcome == "ok"
        assert "for the reviewer only" in json.loads(cap.read_text())["prompt"]
        conn = db.connect(project_dir=str(project))
        row = db.get_interjections(conn)[0]; conn.close()
        assert row["delivered_role"] == "reviewer" and row["delivered_stem"] == res.stem

    def test_failed_turn_leaves_note_pending(self, project, fake_path, monkeypatch):
        st = _init_cycle(project)
        controls.interject_command(["note"], project_root=project)
        monkeypatch.setenv("FAKE_AGENT_MODE", "no_round")
        assert _engine(project).run_owed_turn(st).outcome == "no_round"
        conn = db.connect(project_dir=str(project))
        assert db.get_interjections(conn)[0]["delivered_ts"] is None; conn.close()

    def test_no_owed_turn_all_null_and_next_cycle_delivery(self, project, fake_path,
                                                          monkeypatch, tmp_path, capsys):
        # cycle A reaches a terminal state
        _init_cycle(project, phase="phase-a")
        cycle_mod.add_round("phase-a", "plan", "reviewer", "APPROVE", 1, "ok", str(project),
                            updated_by="Codex")
        assert controls.interject_command(["general note"], project_root=project) == 0
        assert controls.interject_command(["for reviewer", "--to", "reviewer"], project_root=project) == 0
        out = capsys.readouterr().out
        assert "WARNING: no turn is currently owed" in out
        conn = db.connect(project_dir=str(project))
        rows = db.get_interjections(conn); conn.close()
        for r in rows:
            assert (r["phase"], r["type"], r["round"], r["turn"]) == (None, None, None, None)
            assert json.loads(r["observed_state"])["status"] == "done"
        # a note surfaced during an interactive cycle A must not leak into B
        cycle_mod.init_cycle("phase-a", "impl", "Claude", "Codex", "impl", str(project), updated_by="Claude")
        controls.interject_command(["A-impl only"], project_root=project)  # scoped to phase-a/impl
        cycle_mod.add_round("phase-a", "impl", "reviewer", "APPROVE", 1, "ok", str(project), updated_by="Codex")
        # start cycle B: lead turn via start command
        state_mod.update_state({"turn": "lead", "status": "ready", "command": "/handoff start phase-b"},
                               str(project))
        st = state_mod.read_state(str(project))
        cap = tmp_path / "cap.json"; monkeypatch.setenv("FAKE_AGENT_CAPTURE", str(cap))
        eng = _engine(project)
        res = eng.run_owed_turn(st)
        assert res.outcome == "ok", res.reason
        prompt = json.loads(cap.read_text())["prompt"]
        assert "general note" in prompt and "for reviewer" not in prompt and "A-impl only" not in prompt
        conn = db.connect(project_dir=str(project))
        rows = {r["note"]: r for r in db.get_interjections(conn)}; conn.close()
        assert rows["general note"]["delivered_stem"] == res.stem and rows["general note"]["delivered_role"] == "lead"
        assert rows["for reviewer"]["delivered_ts"] is None and rows["A-impl only"]["delivered_ts"] is None
        # B's reviewer turn gets the targeted note
        st = state_mod.read_state(str(project))
        assert st["phase"] == "phase-b" and st["turn"] == "reviewer"
        res2 = eng.run_owed_turn(st)
        assert res2.outcome == "ok"
        prompt = json.loads(cap.read_text())["prompt"]
        assert "for reviewer" in prompt and "A-impl only" not in prompt
        conn = db.connect(project_dir=str(project))
        rows = {r["note"]: r for r in db.get_interjections(conn)}; conn.close()
        assert rows["for reviewer"]["delivered_stem"] == res2.stem
        assert rows["A-impl only"]["delivered_ts"] is None   # never leaked

    def test_same_cycle_mode_switch_injects_once(self, project, fake_path, monkeypatch, tmp_path):
        st = _init_cycle(project)
        controls.interject_command(["seen interactively"], project_root=project)
        # surfaced interactively
        rows = cycle_mod.tail_rounds("feat-x", "plan", None, str(project))
        assert rows[-1]["interjections"][0]["note"] == "seen interactively"
        cap = tmp_path / "cap.json"; monkeypatch.setenv("FAKE_AGENT_CAPTURE", str(cap))
        eng = _engine(project)
        assert eng.run_owed_turn(st).outcome == "ok"
        block = _injected_block(json.loads(cap.read_text())["prompt"])
        assert "seen interactively" in block and "may already have been addressed" in block
        st2 = state_mod.read_state(str(project))
        eng.run_owed_turn(st2)
        prompt2 = json.loads(cap.read_text())["prompt"]
        assert h.INTERJECTIONS_HEADER not in _injected_block(prompt2)   # not re-injected …
        assert '"delivered_role": "reviewer"' in prompt2                # … but visible as history

    def test_list_retire_and_validation(self, project, capsys):
        _init_cycle(project)
        controls.interject_command(["a"], project_root=project)
        controls.interject_command(["b", "--to", "lead"], project_root=project)
        assert controls.interject_command(["c", "--to", "human"], project_root=project) == 1
        assert controls.interject_command([], project_root=project) == 1
        capsys.readouterr()
        assert controls.interject_command(["--list"], project_root=project) == 0
        out = capsys.readouterr().out
        assert "#1" in out and "#2" in out and "→ lead" in out and "pending" in out
        assert controls.interject_command(["--retire", "1", "--by", "jack"], project_root=project) == 0
        assert controls.interject_command(["--retire", "1"], project_root=project) == 1
        assert controls.interject_command(["--retire", "x"], project_root=project) == 1
        conn = db.connect(project_dir=str(project))
        r1 = db.get_interjections(conn)[0]
        assert r1["retired_ts"] and r1["retired_by"] == "jack"
        assert [r["id"] for r in db.pending_interjections_for(conn, "lead", "feat-x", "plan")] == [2]
        assert [r["id"] for r in db.pending_interjections_for(conn, "reviewer", "feat-x", "plan")] == []
        conn.close()
        capsys.readouterr()
        assert controls.interject_command(["--list", "--json"], project_root=project) == 0
        assert json.loads(capsys.readouterr().out)[0]["id"] == 1

    def test_rounds_attach_and_render(self, project, capsys):
        _init_cycle(project)
        rows = cycle_mod.tail_rounds("feat-x", "plan", None, str(project))
        assert rows[0]["interjections"] == []
        controls.interject_command(["watch the schema"], project_root=project)
        rows = cycle_mod.tail_rounds("feat-x", "plan", 1, str(project))
        assert rows[0]["interjections"][0]["note"] == "watch the schema"
        # rounds JSONL untouched
        jsonl = (project / "docs" / "handoffs" / "feat-x_plan_rounds.jsonl").read_text()
        assert "watch the schema" not in jsonl
        assert cycle_mod.cycle_command(["render", "--phase", "feat-x", "--type", "plan"]) == 0
        out = capsys.readouterr().out
        assert "**Arbiter interjection #1**" in out and "watch the schema" in out

    def test_db_accessor_validation(self, tmp_path):
        c = db.connect(project_dir=str(tmp_path))
        with pytest.raises(ValueError):
            db.add_interjection(c, ts="t", note="x", target_role="human")
        with pytest.raises(ValueError):
            db.add_interjection(c, ts="t", note="  ")
        with pytest.raises(ValueError):
            db.pending_interjections_for(c, "human", "p", "plan")
        c.close()


# ---------------------------------------------------------------------------
# usage
# ---------------------------------------------------------------------------

class TestUsage:
    def test_aggregate_pure(self):
        rows = [
            {"role": "lead", "phase": "p", "type": "plan", "round": 1, "status": "ok",
             "input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 100,
             "cache_write_tokens": None, "cost_usd": 0.5, "duration_ms": 1000},
            {"role": "reviewer", "phase": "p", "type": "plan", "round": 1, "status": "no_round",
             "input_tokens": None, "output_tokens": None, "cache_read_tokens": None,
             "cache_write_tokens": None, "cost_usd": None, "duration_ms": 3000},
            {"role": "lead", "phase": "q", "type": "impl", "round": 2, "status": "ok",
             "input_tokens": 1, "output_tokens": 1, "cache_read_tokens": 1,
             "cache_write_tokens": 1, "cost_usd": 0.25, "duration_ms": None},
        ]
        agg = usage_mod.aggregate(rows)
        t = agg["totals"]
        assert t["turns"] == 3 and t["ok"] == 2 and t["failed"] == 1
        assert t["input_tokens"] == 11 and t["cost_usd"] == 0.75 and t["cost_known_turns"] == 2
        assert t["mean_duration_ms"] == 2000                     # over the 2 known durations only
        assert t["duration_known_turns"] == 2
        assert agg["by_cycle"]["q/impl"]["mean_duration_ms"] is None
        assert agg["by_role"]["lead"]["turns"] == 2 and agg["by_role"]["reviewer"]["failed"] == 1
        assert set(agg["by_cycle"]) == {"p/plan", "q/impl"}
        text = usage_mod.render_text(agg)
        assert "By role:" in text and "Totals:" in text
        # Phase 55: no dollar figures in the text view (JSON keeps cost_usd, asserted above)
        assert "priced" not in text and "cost" not in text and "$" not in text
        assert "No usage rows" in usage_mod.render_text(usage_mod.aggregate([]))

    def test_cli(self, project, capsys):
        conn = db.connect(project_dir=str(project))
        db.add_usage(conn, ts="2026-01-01T00:00:00+00:00", phase="p", type="plan", round=1,
                     role="lead", provider="claude", status="ok", input_tokens=5, output_tokens=2)
        db.add_usage(conn, ts="2026-01-01T00:00:01+00:00", phase="p", type="plan", round=1,
                     role="reviewer", provider="codex", status="timeout")
        conn.close()
        assert usage_mod.usage_command([], project_root=project) == 0
        out = capsys.readouterr().out
        assert "p/plan r1" in out and "timeout" in out and "Totals: turns=2" in out
        assert usage_mod.usage_command(["--role", "lead", "--json"], project_root=project) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["totals"]["turns"] == 1 and list(data["by_role"]) == ["lead"]
        assert usage_mod.usage_command(["--phase", "zzz"], project_root=project) == 0
        assert "No usage rows" in capsys.readouterr().out
        assert usage_mod.usage_command(["--limit", "x"], project_root=project) == 1
        assert usage_mod.usage_command(["--wat"], project_root=project) == 1


# ---------------------------------------------------------------------------
# fingerprint + retries
# ---------------------------------------------------------------------------

class TestFingerprint:
    def test_non_git_and_content_sensitivity(self, tmp_path):
        assert fpm.repo_fingerprint(tmp_path) is None
        repo = tmp_path / "r"; repo.mkdir(); _git_init(repo)
        a = fpm.repo_fingerprint(repo)
        (repo / "tracked.txt").write_text("changed\n")
        b = fpm.repo_fingerprint(repo); assert b != a
        (repo / "tracked.txt").write_text("changed again\n")   # already-modified edited again
        c = fpm.repo_fingerprint(repo); assert c != b
        (repo / "u.txt").write_text("u")
        d = fpm.repo_fingerprint(repo); assert d != c
        (repo / "u.txt").write_text("uu")                       # untracked content change
        e = fpm.repo_fingerprint(repo); assert e != d
        _git(repo, "add", "u.txt")                              # stage-only
        f = fpm.repo_fingerprint(repo); assert f != e
        _git(repo, "commit", "-qm", "c")                        # commit
        g = fpm.repo_fingerprint(repo); assert g != f
        (repo / "ignored.txt").write_text("x")
        (repo / ".gitignore").write_text(".tagteam/\nhandoff-state.json\nignored.txt\n")
        h1 = fpm.repo_fingerprint(repo)
        (repo / "ignored.txt").write_text("y")                  # ignored: blind spot by design
        assert fpm.repo_fingerprint(repo) == h1

    def test_embedded_and_declared_only_and_nested(self, tmp_path):
        repo = tmp_path / "r"; repo.mkdir(); _git_init(repo)
        emb = repo / "vendor" / "localrepo"; emb.mkdir(parents=True); _git_init(emb)
        base = fpm.repo_fingerprint(repo)
        assert base not in (None, fpm.UNSUPPORTED)
        (emb / "tracked.txt").write_text("dirty\n")
        d1 = fpm.repo_fingerprint(repo); assert d1 != base
        (emb / "tracked.txt").write_text("dirty again\n")       # pre-dirty edited again
        assert fpm.repo_fingerprint(repo) != d1
        # declared-only .gitmodules path (no gitlink in HEAD/index)
        (repo / ".gitmodules").write_text('[submodule "x"]\n\tpath = declared/x\n\turl = ./x\n')
        dec = repo / "declared" / "x"; dec.mkdir(parents=True); _git_init(dec)
        e1 = fpm.repo_fingerprint(repo)
        (dec / "tracked.txt").write_text("changed\n")
        assert fpm.repo_fingerprint(repo) != e1
        # nested: embedded inside embedded
        nested = emb / "deep" / "n"; nested.mkdir(parents=True); _git_init(nested)
        n1 = fpm.repo_fingerprint(repo)
        (nested / "tracked.txt").write_text("nested change\n")
        assert fpm.repo_fingerprint(repo) != n1

    @pytest.mark.skipif(sys.platform == "win32", reason="newline in path not allowed on Windows")
    def test_newline_path_and_suffix_lookalike(self, tmp_path):
        repo = tmp_path / "r"; repo.mkdir(); _git_init(repo)
        weird = repo / "sub\nvendor"; weird.mkdir(); _git_init(weird)
        look = repo / "vendor"; look.mkdir(); _git_init(look)
        base = fpm.repo_fingerprint(repo)
        assert base not in (None, fpm.UNSUPPORTED)
        (look / "tracked.txt").write_text("edited\n")
        assert fpm.repo_fingerprint(repo) != base

    def test_unsupported_states(self, tmp_path, monkeypatch):
        repo = tmp_path / "r"; repo.mkdir(); _git_init(repo)
        # unmerged index: conflicting edits on two branches, then a merge that
        # must fail *because of the conflict* (identity is configured via _git,
        # so it cannot fail for lack of user.name/email as on a bare CI runner)
        base = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo,
                              capture_output=True, text=True).stdout.strip()
        _git(repo, "checkout", "-qb", "other")
        (repo / "tracked.txt").write_text("other\n"); _git(repo, "commit", "-qam", "o")
        _git(repo, "checkout", "-q", base)
        (repo / "tracked.txt").write_text("mine\n"); _git(repo, "commit", "-qam", "m")
        r = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "merge", "other"],
                           cwd=repo, capture_output=True, text=True)
        assert r.returncode != 0
        unmerged = subprocess.run(["git", "ls-files", "-u"], cwd=repo, capture_output=True,
                                  text=True).stdout
        assert unmerged.strip(), f"expected an unmerged index; merge output: {r.stdout} {r.stderr}"
        assert fpm.repo_fingerprint(repo) == fpm.UNSUPPORTED
        # mocked git failure
        repo2 = tmp_path / "r2"; repo2.mkdir(); _git_init(repo2)
        real = fpm._git

        def boom(cwd, *args, **kw):
            if args and args[0] == "write-tree":
                raise fpm.FingerprintError("boom")
            return real(cwd, *args, **kw)
        monkeypatch.setattr(fpm, "_git", boom)
        assert fpm.repo_fingerprint(repo2) == fpm.UNSUPPORTED

    def test_probe_failure_is_unsupported_not_non_git(self, tmp_path):
        repo = tmp_path / "r"; repo.mkdir(); _git_init(repo)
        with patch("tagteam.fingerprint.subprocess.run", side_effect=OSError("git missing")):
            assert fpm.repo_fingerprint(repo) == fpm.UNSUPPORTED
            assert fpm.repo_fingerprint(tmp_path) == fpm.UNSUPPORTED   # even for a non-repo dir
        with patch("tagteam.fingerprint.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("git", 1)):
            assert fpm.repo_fingerprint(repo) == fpm.UNSUPPORTED
        # git present but refusing (e.g. dubious ownership) → unknown → UNSUPPORTED
        m = MagicMock(); m.returncode = 128; m.stdout = b""; m.stderr = b"fatal: detected dubious ownership"
        with patch("tagteam.fingerprint.subprocess.run", return_value=m):
            assert fpm.repo_fingerprint(repo) == fpm.UNSUPPORTED
        assert fpm.probe_repo(tmp_path) == "not-repo"

    def test_probe_failure_suppresses_retries_engine_level(self, project, fake_path, monkeypatch, tmp_path):
        st = _init_cycle(project)   # non-git project on disk …
        monkeypatch.setenv("FAKE_AGENT_MODE", "nonzero")
        eng = _engine(project, retries=2)
        real_run = fpm.subprocess.run

        def broken_git(argv, **kw):   # … but git itself is broken → UNSUPPORTED, not "non-git"
            if argv and argv[0] == "git":
                raise OSError("git missing")
            return real_run(argv, **kw)
        monkeypatch.setattr(fpm.subprocess, "run", broken_git)
        # even a spawn failure (which a real non-git project may retry) is not retried
        real_popen = h.subprocess.Popen

        def fail_spawn(*a, **k):
            if a and str(a[0][0]).startswith(str(fake_path)):
                raise PermissionError(13, "denied", a[0][0])
            return real_popen(*a, **k)
        monkeypatch.setattr(h.subprocess, "Popen", fail_spawn)
        res = eng.run_owed_turn(st)
        assert res.outcome == "spawn_failed"
        assert len(_usage_rows(project)) == 1
        assert any("UNSUPPORTED" in l for l in eng._test_logs)

    def test_handoff_fingerprint_changes_on_transition(self, project):
        st = _init_cycle(project)
        a = fpm.handoff_fingerprint(project, "feat-x", "plan")
        state_mod.update_state({"command": "poke"}, str(project))
        b = fpm.handoff_fingerprint(project, "feat-x", "plan")
        assert a != b
        cycle_mod.add_round("feat-x", "plan", "reviewer", "APPROVE", 1, "ok", str(project), updated_by="Codex")
        assert fpm.handoff_fingerprint(project, "feat-x", "plan") != b


def _mk_source_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    (path / "tracked.txt").write_text("src\n")
    _git(path, "add", "-A"); _git(path, "commit", "-qm", "init")
    return path


NL_SUB = "sub\nvendor"


def _build_gitlink_fixture(project: Path, src_root: Path) -> dict:
    """The approved-plan fixture for retry criteria (a)–(l):

      libs/regsub          registered submodule (pre-dirty)            (h)
      libs/regsub/inner    registered nested sub-submodule            (l)
      sub\nvendor          registered submodule with a newline path   (k, POSIX)
      vendor/              plain embedded repo = newline suffix lookalike (k)
      vendor2/localrepo    untracked embedded repo, pre-dirty          (i)
      declared/x           committed .gitmodules entry, no gitlink, nested repo (j)
      newdir/pre.txt       already-present untracked file             (c, f)
      tracked.txt          clean tracked file                          (d)
      tracked2.txt         tracked file already modified               (e, g)
    """
    _git_init(project)
    s2 = _mk_source_repo(src_root / "S2")
    s1 = _mk_source_repo(src_root / "S1")
    subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                    str(s2), "inner"], cwd=s1, check=True, capture_output=True)
    _git(s1, "commit", "-qm", "inner")
    s3 = _mk_source_repo(src_root / "S3")
    (project / "tracked2.txt").write_text("two\n")
    _git(project, "add", "-A"); _git(project, "commit", "-qm", "t2")
    subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                    str(s1), "libs/regsub"], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "update",
                    "--init", "--recursive", "-q"], cwd=project, check=True, capture_output=True)
    has_nl = sys.platform != "win32"
    if has_nl:
        subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "add",
                        "--name", "nlsub", "-q", str(s3), NL_SUB],
                       cwd=project, check=True, capture_output=True)
    _git(project, "commit", "-qm", "submodules")
    # declared-only .gitmodules entry (committed), no gitlink in HEAD/index
    with open(project / ".gitmodules", "a") as f:
        f.write('[submodule "declared"]\n\tpath = declared/x\n\turl = ./x\n')
    _git(project, "commit", "-qam", "declare-only")
    _mk_source_repo(project / "declared" / "x")          # nested plain repo at that path
    _mk_source_repo(project / "vendor2" / "localrepo")   # untracked embedded repo
    (project / "vendor2" / "localrepo" / "tracked.txt").write_text("pre-dirty\n")
    _mk_source_repo(project / "vendor")                  # suffix lookalike of "sub\nvendor"
    (project / "newdir").mkdir(); (project / "newdir" / "pre.txt").write_text("pre")
    (project / "tracked2.txt").write_text("pre-modified\n")
    (project / "libs" / "regsub" / "tracked.txt").write_text("sub pre-dirty\n")
    return {"has_nl": has_nl}


RETRY_CASES = {
    "a-cycle-add":         ([{"cycle_add": True}], "handoff state changed"),
    "b-commit":            ([{"git": ["-c", "user.email=t@t", "-c", "user.name=t", "commit",
                                      "--allow-empty", "-qm", "x"]}], "worktree changed"),
    "c-untracked-dir":     ([{"write": ["newdir/inner.txt", "x"]}], "worktree changed"),
    "d-tracked-clean":     ([{"write": ["tracked.txt", "edit"]}], "worktree changed"),
    "e-tracked-premod":    ([{"write": ["tracked2.txt", "again"]}], "worktree changed"),
    "f-untracked-content": ([{"write": ["newdir/pre.txt", "changed"]}], "worktree changed"),
    "g-stage-only":        ([{"git": ["add", "-A"]}], "worktree changed"),
    "h-registered-sub":    ([{"write": ["libs/regsub/tracked.txt", "again"]}], "worktree changed"),
    "i-embedded":          ([{"write": ["vendor2/localrepo/tracked.txt", "again"]}], "worktree changed"),
    "j-declared-only":     ([{"write": ["declared/x/tracked.txt", "x"]}], "worktree changed"),
    "k-newline-lookalike": ([{"write": ["vendor/tracked.txt", "x"]}], "worktree changed"),
    "l-nested-sub":        ([{"write": ["libs/regsub/inner/tracked.txt", "x"]}], "worktree changed"),
}


class TestRetries:
    """Mirrors the approved plan's retry-gate criteria (a)–(l) one-to-one."""

    def _setup(self, project, monkeypatch, tmp_path_factory, gitlinks: bool):
        if gitlinks:
            _build_gitlink_fixture(project, tmp_path_factory.mktemp("srcrepos"))
        else:
            _git_init(project)
        (project / ".tagteam").mkdir(exist_ok=True)
        monkeypatch.setenv("FAKE_AGENT_COUNTER", str(project / ".tagteam" / "fake-counter"))
        return _init_cycle(project)

    def test_clean_failure_retried_then_ok(self, project, fake_path, monkeypatch, tmp_path_factory):
        st = self._setup(project, monkeypatch, tmp_path_factory, gitlinks=False)
        monkeypatch.setenv("FAKE_AGENT_MODE", "flaky")
        monkeypatch.setenv("FAKE_AGENT_FAIL_TIMES", "1")
        eng = _engine(project, retries=2)
        res = eng.run_owed_turn(st)
        assert res.outcome == "ok", (res.reason, eng._test_logs)
        assert [r["status"] for r in _usage_rows(project)] == ["nonzero_exit", "ok"]
        assert eng.paused() is None
        logs = list(h.turns_dir(project).glob("*.log"))
        assert any("retry 1/2" in p.read_text() for p in logs)
        assert any(p.name.endswith("_a2.log") for p in logs)

    def test_clean_failure_retried_with_gitlinks_present(self, project, fake_path, monkeypatch,
                                                         tmp_path_factory):
        """A repo full of gitlinks still retries a genuinely clean failure."""
        st = self._setup(project, monkeypatch, tmp_path_factory, gitlinks=True)
        monkeypatch.setenv("FAKE_AGENT_MODE", "flaky")
        monkeypatch.setenv("FAKE_AGENT_FAIL_TIMES", "1")
        eng = _engine(project, retries=1)
        res = eng.run_owed_turn(st)
        assert res.outcome == "ok", (res.reason, eng._test_logs)
        assert [r["status"] for r in _usage_rows(project)] == ["nonzero_exit", "ok"]

    def test_retries_exhausted_pauses(self, project, fake_path, monkeypatch, tmp_path_factory):
        st = self._setup(project, monkeypatch, tmp_path_factory, gitlinks=False)
        monkeypatch.setenv("FAKE_AGENT_MODE", "nonzero")
        eng = _engine(project, retries=1)
        assert eng.run_owed_turn(st).outcome == "nonzero_exit"
        assert len(_usage_rows(project)) == 2 and eng.paused()

    @pytest.mark.parametrize("case", list(RETRY_CASES))
    def test_criteria_a_to_l_block_retry(self, project, fake_path, monkeypatch,
                                         tmp_path_factory, case):
        info = self._build_and_check(project, monkeypatch, tmp_path_factory, case)
        st = info["state"]
        effect, label = RETRY_CASES[case]
        monkeypatch.setenv("FAKE_AGENT_MODE", "nonzero")
        monkeypatch.setenv("FAKE_AGENT_SIDE_EFFECT", json.dumps(effect))
        eng = _engine(project, retries=3)
        res = eng.run_owed_turn(st)
        assert res.outcome == "nonzero_exit"
        assert len(_usage_rows(project)) == 1, "must not retry"
        assert eng.paused()
        assert any(label in l for l in eng._test_logs), eng._test_logs

    def _build_and_check(self, project, monkeypatch, tmp_path_factory, case):
        st = self._setup(project, monkeypatch, tmp_path_factory, gitlinks=True)
        if case == "k-newline-lookalike" and sys.platform == "win32":
            pytest.skip("newline in path not allowed on Windows")
        # (i)–(l): the same edit changes the fingerprint directly.
        if case[0] in "ijkl":
            effect, _ = RETRY_CASES[case]
            path, content = effect[0]["write"]
            before = fpm.repo_fingerprint(project)
            assert before not in (None, fpm.UNSUPPORTED)
            with open(project / path, "a") as f:
                f.write(content)
            after = fpm.repo_fingerprint(project)
            assert after != before, f"{case}: fingerprint did not change"
            with open(project / path, "a") as f:
                f.write("")  # (no-op) keep the tree in this edited state for the engine run
        return {"state": st}

    def test_fingerprint_fixture_shape(self, project, tmp_path_factory):
        info = _build_gitlink_fixture(project, tmp_path_factory.mktemp("srcrepos"))
        listing = subprocess.run(["git", "ls-files", "--stage", "-z"], cwd=project,
                                 capture_output=True).stdout.split(b"\0")
        gitlinks = [e.split(b"\t", 1)[1] for e in listing if e.startswith(b"160000")]
        assert b"libs/regsub" in gitlinks
        if info["has_nl"]:
            assert NL_SUB.encode() in gitlinks
        inner = subprocess.run(["git", "ls-files", "--stage", "-z"], cwd=project / "libs" / "regsub",
                               capture_output=True).stdout
        assert b"160000" in inner and b"inner" in inner       # registered nested sub-submodule
        gm = (project / ".gitmodules").read_text()
        assert "declared/x" in gm and b"declared" not in b"".join(gitlinks)   # declared-only
        assert (project / "vendor" / ".git").exists() and (project / "vendor2" / "localrepo" / ".git").exists()

    def test_no_round_never_retried(self, project, fake_path, monkeypatch, tmp_path_factory):
        st = self._setup(project, monkeypatch, tmp_path_factory, gitlinks=False)
        monkeypatch.setenv("FAKE_AGENT_MODE", "no_round")
        eng = _engine(project, retries=3)
        assert eng.run_owed_turn(st).outcome == "no_round"
        assert len(_usage_rows(project)) == 1
        assert any("never retried" in l for l in eng._test_logs)

    @needs_proc_inspection
    def test_cancelled_never_retried(self, project, fake_path, monkeypatch, tmp_path_factory, tmp_path):
        st = self._setup(project, monkeypatch, tmp_path_factory, gitlinks=False)
        monkeypatch.setenv("FAKE_AGENT_MODE", "grandchild_hang")
        pidfile = project / ".tagteam" / "gc.pid"
        monkeypatch.setenv("FAKE_AGENT_PIDFILE", str(pidfile))
        eng = _engine(project, timeout_minutes=5, retries=3)
        results = {}
        th = threading.Thread(target=lambda: results.update(r=eng.run_owed_turn(st))); th.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            inf = h.read_inflight(project)
            if inf and inf.get("pid") and inf.get("child_ident") and pidfile.exists():
                break
            time.sleep(0.1)
        assert controls.cancel_turn_command(["--by", "jack"], project_root=project) == 0
        th.join(60)
        assert results["r"].outcome == "cancelled"
        assert len(_usage_rows(project)) == 1
        assert any("never retried" in l for l in eng._test_logs)

    @pytest.mark.parametrize("failure", ["git", "parse", "recursion", "probe"])
    def test_unsupported_variants_never_retried(self, project, fake_path, monkeypatch,
                                                tmp_path_factory, failure):
        st = self._setup(project, monkeypatch, tmp_path_factory, gitlinks=(failure == "recursion"))
        real_git = fpm._git
        if failure == "git":
            def bad(cwd, *args, **kw):
                if args and args[0] == "write-tree":
                    raise fpm.FingerprintError("boom")
                return real_git(cwd, *args, **kw)
            monkeypatch.setattr(fpm, "_git", bad)
        elif failure == "parse":
            def bad(cwd, *args, **kw):
                r = real_git(cwd, *args, **kw)
                if args and args[0] == "ls-files":
                    r.stdout = b"garbage-without-tab\0"
                return r
            monkeypatch.setattr(fpm, "_git", bad)
        elif failure == "recursion":
            monkeypatch.setattr(fpm, "_MAX_DEPTH", 0)     # first gitlink recursion is "too deep"
        else:
            monkeypatch.setattr(fpm, "probe_repo", lambda root: "unknown")
        assert fpm.repo_fingerprint(project) == fpm.UNSUPPORTED
        monkeypatch.setenv("FAKE_AGENT_MODE", "nonzero")
        eng = _engine(project, retries=3)
        assert eng.run_owed_turn(st).outcome == "nonzero_exit"
        assert len(_usage_rows(project)) == 1
        assert any("UNSUPPORTED" in l for l in eng._test_logs)

    def test_non_git_only_spawn_failed_retried(self, project, fake_path, monkeypatch, tmp_path):
        st = _init_cycle(project)   # not a git repo
        monkeypatch.setenv("FAKE_AGENT_MODE", "nonzero")
        eng = _engine(project, retries=2)
        assert eng.run_owed_turn(st).outcome == "nonzero_exit"
        assert len(_usage_rows(project)) == 1
        h.clear_pause(project)
        # spawn_failed once, then ok
        real_popen = h.subprocess.Popen
        calls = {"n": 0}

        def flaky_popen(*a, **k):
            argv0 = str(a[0][0]) if a and a[0] else ""
            if argv0.startswith(str(fake_path)):   # only the agent spawn, not `ps` etc.
                calls["n"] += 1
                if calls["n"] == 1:
                    raise PermissionError(13, "denied", argv0)
            return real_popen(*a, **k)
        monkeypatch.setattr(h.subprocess, "Popen", flaky_popen)
        monkeypatch.setenv("FAKE_AGENT_MODE", "ok")
        res = eng.run_owed_turn(st)
        assert res.outcome == "ok", res.reason
        assert [r["status"] for r in _usage_rows(project)][-2:] == ["spawn_failed", "ok"]


class TestPerRoleTimeoutAndRollback:
    def test_per_role_timeout_override(self, project, fake_path):
        (project / "tagteam.yaml").write_text(
            "agents:\n  lead:\n    name: Claude\n    headless:\n      timeout_minutes: 7\n"
            "  reviewer:\n    name: Codex\n", encoding="utf-8")
        eng = _engine(project, timeout_minutes=60)
        assert eng.roles["lead"].timeout_s == 7 * 60 and eng.roles["reviewer"].timeout_s is None
        (project / "tagteam.yaml").write_text(
            "agents:\n  lead:\n    name: Claude\n    headless:\n      timeout_minutes: 0\n"
            "  reviewer:\n    name: Codex\n", encoding="utf-8")
        eng = h.HeadlessEngine(project, read_config(project / "tagteam.yaml"),
                               lead_name="Claude", reviewer_name="Codex", log=lambda m: None)
        assert any("timeout_minutes" in e for e in eng.validate())

    def test_rollback(self, capsys):
        assert controls.rollback_command(["nope"]) == 1
        assert controls.rollback_command([]) == 1
        capsys.readouterr()
        assert controls.rollback_command(["0.8.0"]) == 0
        out = capsys.readouterr().out
        assert "tagteam==0.8.0" in out and "tagteam upgrade" in out and "Nothing executed" in out
        runs = []

        def runner(cmd):
            runs.append(cmd); m = MagicMock(); m.returncode = 0; return m
        assert controls.rollback_command(["0.8.0", "--yes"], runner=runner) == 0
        assert len(runs) == 2 and runs[1] == ["tagteam", "upgrade"]

        def bad(cmd):
            m = MagicMock(); m.returncode = 2; return m
        assert controls.rollback_command(["0.8.0", "--yes"], runner=bad) == 1

    @needs_proc_inspection
    def test_procs_helpers(self):
        me = os.getpid()
        assert procs.pid_alive(me)
        assert not procs.pid_alive(0) and not procs.pid_alive(-5)
        ident = procs.identity(me)
        assert ident and ident.startswith(f"{me}:")
        assert procs.identity(me) == ident
        s = _sleeper()
        try:
            time.sleep(0.2)
            assert procs.parent_pid(s.pid) == me
        finally:
            s.kill()
