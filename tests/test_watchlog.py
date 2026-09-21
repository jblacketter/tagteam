"""Phase 67 — the watcher's event log and heartbeat (`tagteam/watchlog.py`),
the sink `watcher._log` feeds, `tagteam watch status` / `watch log`, and the
two read surfaces (`/api/watcher/events`, `now.watcher.beat`)."""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from tagteam import cli, procs, watcher, watchlog
from tagteam.watchlog import Sink

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="FIFOs / symlinks: POSIX")


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".tagteam").mkdir()
    return tmp_path


@pytest.fixture
def alarm():
    """A blocking open would hang the suite: fail loudly after 10 s instead."""
    if sys.platform == "win32":
        yield
        return

    def _boom(*_a):
        raise AssertionError("a watchlog write blocked")
    old = signal.signal(signal.SIGALRM, _boom)
    signal.alarm(10)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _events(root):
    return watchlog.read(root, 10_000)


# ---------------------------------------------------------------------------
# event log


class TestEventLog:
    def test_record_shape_context_and_cap(self, root):
        s = Sink(root, "notify", every_s=10)
        assert s.event(">> Codex's turn", "turn", phase="p1", type="impl", round=2, turn="reviewer", seq=7)
        assert s.event("x" * 2000)
        assert s.event("odd", "not-a-kind", phase=None)
        a, b, c = _events(root)
        assert a["kind"] == "turn" and a["msg"] == ">> Codex's turn" and a["pid"] == os.getpid()
        assert (a["phase"], a["type"], a["round"], a["turn"], a["seq"]) == ("p1", "impl", 2, "reviewer", 7)
        assert a["mode"] == "notify" and datetime.fromisoformat(a["ts"]).tzinfo is not None
        assert len(b["msg"]) == watchlog.MSG_LIMIT and b["kind"] == "info" and "phase" not in b
        assert c["kind"] == "info"          # unknown kinds never reach the log

    def test_never_creates_the_runtime_dir(self, tmp_path):
        s = Sink(tmp_path, "notify", every_s=10)
        assert s.event("hello") is False and s.beat({"seq": 1}) is False
        assert list(tmp_path.iterdir()) == []

    def test_rotation_keeps_one_generation_and_read_spans_both(self, root, monkeypatch):
        monkeypatch.setattr(watchlog, "MAX_BYTES", 2_000)
        s = Sink(root, "notify", every_s=10)
        for i in range(200):
            assert s.event(f"event {i:04d}")
        names = sorted(p.name for p in (root / ".tagteam").iterdir())
        assert names == ["watcher-events.jsonl", "watcher-events.jsonl.1"]
        live = (root / watchlog.EVENTS_REL).read_text().splitlines()
        assert 0 < len(live) < 200
        got = watchlog.read(root, len(live) + 5)
        assert [e["msg"] for e in got] == [f"event {i:04d}" for i in range(200 - len(got), 200)]
        assert [e["msg"] for e in watchlog.read(root, 3)] == ["event 0197", "event 0198", "event 0199"]

    def test_malformed_lines_are_skipped(self, root):
        Sink(root, "notify", every_s=10).event("one")
        with open(root / watchlog.EVENTS_REL, "ab") as f:
            f.write(b"not json\n[1, 2]\n\xff\xfe\n{\"kind\": \"info\", \"msg\": \"torn")
        Sink(root, "notify", every_s=10)    # a second writer instance changes nothing
        assert [e["msg"] for e in _events(root)] == ["one"]

    def test_last_event_skips_chatter(self, root):
        s = Sink(root, "notify", every_s=10)
        assert watchlog.last_event(root) is None
        s.event("   Sent to Codex", "sent"); s.event("gate: pass", "gate"); s.event("noise")
        assert watchlog.last_event(root)["kind"] == "gate"
        assert watchlog.last_event(root, watchlog.DISPATCH_KINDS)["kind"] == "sent"

    def test_last_event_is_not_hidden_by_chatter_or_rotation(self, root, monkeypatch):
        """impl r3: a 400-record window lost a dispatch that was still on disk."""
        s = Sink(root, "notify", every_s=10)
        s.event("   Sent to Codex", "sent", seq=9)
        for i in range(1_000):
            s.event(f"chatter {i}")
        assert not (root / watchlog.ROTATED_REL).exists()
        assert watchlog.last_event(root)["seq"] == 9
        assert watchlog.last_event(root, watchlog.DISPATCH_KINDS)["seq"] == 9
        # …and once it has been rotated out of the live file
        monkeypatch.setattr(watchlog, "MAX_BYTES", (root / watchlog.EVENTS_REL).stat().st_size - 1)
        s.event("after rotation")
        live = (root / watchlog.EVENTS_REL).read_text()
        assert "Sent to Codex" not in live and "Sent to Codex" in (root / watchlog.ROTATED_REL).read_text()
        assert watchlog.last_event(root)["seq"] == 9
        s.event("gate: pass", "gate")                                   # the live file wins when it has a match
        assert watchlog.last_event(root)["kind"] == "gate"
        assert watchlog.last_event(root, watchlog.DISPATCH_KINDS)["seq"] == 9

    def test_story_mode_filters_chatter_before_counting_and_folds_runs(self, root):
        """Phase 68, seen live: a terminal watcher's idle-probe chatter pushed
        every dispatch out of a newest-200 window, and one 8-minute pre-check
        produced 48 identical `turn` lines."""
        s = Sink(root, "iterm2", every_s=10)
        s.event(">> Codex's turn", "turn", seq=4); s.event("   Sent to Codex", "sent", seq=4)
        for i in range(300):
            s.event(f"   (not idle yet, probe {i})")
        for _ in range(48):
            s.event(">> Codex's turn", "turn", seq=5); s.event("   gate: turn slot busy")
        s.event("   Sent to Codex", "sent", seq=5)
        assert [e["kind"] for e in watchlog.read(root, 200)][:3] == ["info", "info", "info"]        # the old window
        story = watchlog.read(root, 200, include_info=False)
        assert [(e["kind"], e.get("seq"), e.get("repeat")) for e in story] == [
            ("turn", 4, None), ("sent", 4, None), ("turn", 5, 48), ("sent", 5, None)]
        assert story[2]["last_ts"] >= story[2]["ts"]
        assert len(watchlog.read(root, 2, include_info=False)) == 2
        # folding is a view: nothing on disk changes, and the full log is not folded
        assert sum(1 for e in watchlog.read(root, 10_000) if e["kind"] == "turn") == 49

    def test_story_history_is_sized_after_folding_and_spans_the_rotation(self, root, monkeypatch):
        """impl r1: whether to read `.1` was decided on the PRE-fold count — 201
        identical live lines folded to one row and the older `sent` in `.1`
        was never read."""
        s = Sink(root, "iterm2", every_s=10)
        s.event("   Sent to Codex", "sent", seq=1)
        s.event(">> Codex's turn", "turn", seq=2); s.event(">> Codex's turn", "turn", seq=2)      # a run that will straddle
        monkeypatch.setattr(watchlog, "MAX_BYTES", (root / watchlog.EVENTS_REL).stat().st_size - 1)
        s.event(">> Codex's turn", "turn", seq=2)                                                 # rotates first
        monkeypatch.setattr(watchlog, "MAX_BYTES", 512 * 1024)
        for _ in range(200):
            s.event(">> Codex's turn", "turn", seq=2)
        assert (root / watchlog.ROTATED_REL).exists()
        assert "Sent to Codex" not in (root / watchlog.EVENTS_REL).read_text()                    # only in `.1`
        assert len(watchlog._records(root, watchlog.EVENTS_REL)) == 201                           # > n, all identical
        story = watchlog.read(root, 200, include_info=False)
        assert [(e["kind"], e.get("seq"), e.get("repeat")) for e in story] == [("sent", 1, None), ("turn", 2, 203)]
        assert story[1]["ts"] < story[1]["last_ts"]                       # the run began in `.1` and ended in the live file
        assert [e["kind"] for e in watchlog.read(root, 1, include_info=False)] == ["turn"]          # n is applied LAST
        # the unfolded listing is unchanged: newest n raw lines, `.1` only when the live file is short
        assert len(watchlog.read(root, 200)) == 200 and len(watchlog.read(root, 500)) == 204

    def test_a_different_seq_or_message_is_not_folded(self, root):
        s = Sink(root, "iterm2", every_s=10)
        s.event("x", "turn", seq=1); s.event("x", "turn", seq=2); s.event("y", "turn", seq=2); s.event("y", "sent", seq=2)
        assert [e.get("repeat") for e in watchlog.read(root, 10, include_info=False)] == [None] * 4

    def test_concurrent_callers_across_rotation(self, root, monkeypatch):
        """watch_with_events calls the processor from two threads."""
        monkeypatch.setattr(watchlog, "MAX_BYTES", 4_000)
        s = Sink(root, "iterm2", every_s=30)
        per, threads = 150, 8
        ts = [threading.Thread(target=lambda t=t: [s.event(f"t{t}-{i:03d}") for i in range(per)])
              for t in range(threads)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        names = sorted(p.name for p in (root / ".tagteam").iterdir())
        assert names == ["watcher-events.jsonl", "watcher-events.jsonl.1"]
        for rel in (watchlog.EVENTS_REL, watchlog.ROTATED_REL):
            for line in (root / rel).read_bytes().splitlines():
                assert json.loads(line)["msg"].startswith("t")      # only whole lines
        kept = [e["msg"] for e in _events(root)]
        assert len(kept) == len(set(kept))
        # rotation drops only the globally oldest records, so what survives of
        # each thread is an unbroken tail ending at its last event
        for t in range(threads):
            mine = [int(m.split("-")[1]) for m in kept if m.startswith(f"t{t}-")]
            assert mine == list(range(per - len(mine), per)), (t, mine[:5])
        assert sum(1 for m in kept if m.endswith(f"-{per - 1:03d}")) >= 1


# ---------------------------------------------------------------------------
# writer safety


@posix_only
class TestWriterSafety:
    def _target(self, tmp_path):
        t = tmp_path / "outside.txt"
        t.write_bytes(b"precious\n")
        return t

    @pytest.mark.parametrize("rel", [watchlog.EVENTS_REL, watchlog.BEAT_REL])
    def test_fifo_at_the_path_is_refused_without_blocking(self, root, alarm, rel):
        os.mkfifo(root / rel)
        s = Sink(root, "notify", every_s=10)
        if rel == watchlog.EVENTS_REL:
            assert s.event("hello") is False
        else:
            # os.replace substitutes the directory entry: the FIFO is never opened
            assert s.beat({"seq": 1}) is True and (root / rel).is_file()

    @pytest.mark.parametrize("rel", [watchlog.EVENTS_REL, watchlog.ROTATED_REL, watchlog.BEAT_REL])
    def test_symlink_target_is_never_written(self, root, tmp_path_factory, alarm, monkeypatch, rel):
        target = self._target(tmp_path_factory.mktemp("o"))
        monkeypatch.setattr(watchlog, "MAX_BYTES", 300)
        os.symlink(target, root / rel)
        s = Sink(root, "notify", every_s=10)
        for i in range(30):
            s.event(f"event {i}")
        s.beat({"seq": 1}, force=True)
        assert target.read_bytes() == b"precious\n"

    @pytest.mark.parametrize("kind", ["fifo", "symlink", "file"])
    def test_leftover_at_the_beat_temp_name(self, root, tmp_path_factory, alarm, kind):
        target = self._target(tmp_path_factory.mktemp("o"))
        s = Sink(root, "notify", every_s=10)
        tmp = root / f"{watchlog.BEAT_REL}.{s.pid}.tmp"
        {"fifo": lambda: os.mkfifo(tmp), "symlink": lambda: os.symlink(target, tmp),
         "file": lambda: tmp.write_text("old")}[kind]()
        assert s.beat({"seq": 3}) is True
        assert watchlog.read_beat(root)["seq"] == 3 and not tmp.exists() and not tmp.is_symlink()
        assert target.read_bytes() == b"precious\n"

    def test_symlinked_runtime_dir_is_refused(self, tmp_path, alarm):
        real = tmp_path / "elsewhere"; real.mkdir()
        proj = tmp_path / "proj"; proj.mkdir()
        os.symlink(real, proj / ".tagteam")
        s = Sink(proj, "notify", every_s=10)
        assert s.event("hello") is False and s.beat({"seq": 1}) is False
        assert list(real.iterdir()) == []
        assert watchlog.read(proj) == [] and watchlog.read_beat(proj) is None

    def test_swap_to_fifo_between_check_and_open(self, root, alarm):
        """The lstat passes (no file yet), then a FIFO appears: the
        non-blocking open fails (ENXIO) instead of waiting for a reader."""
        real_lstat = os.lstat

        def lstat_then_swap(path, *a, **kw):
            try:
                return real_lstat(path, *a, **kw)
            finally:
                if str(path).endswith(watchlog.EVENTS_NAME) and not os.path.lexists(path):
                    os.mkfifo(path)
        with patch.object(watchlog.os, "lstat", lstat_then_swap):
            assert Sink(root, "notify", every_s=10).event("hello") is False

    def test_readers_refuse_fifo_and_symlink(self, root, tmp_path_factory, alarm):
        target = self._target(tmp_path_factory.mktemp("o"))
        os.mkfifo(root / watchlog.EVENTS_REL)
        os.symlink(target, root / watchlog.BEAT_REL)
        assert watchlog.read(root) == [] and watchlog.read_beat(root) is None
        assert watchlog.beat_view(root)["state"] == "none"

    @pytest.mark.parametrize("failing", ["open", "write", "replace"])
    def test_oserror_is_swallowed(self, root, failing):
        s = Sink(root, "notify", every_s=10)

        def boom(*_a, **_kw):
            raise OSError(28, "No space left on device")
        with patch.object(watchlog.os, failing, boom):
            assert s.event("hello") is False or failing == "replace"
            assert s.beat({"seq": 1}, force=True) is False
        assert not list((root / ".tagteam").glob("*.tmp"))


# ---------------------------------------------------------------------------
# the sink behind watcher._log


def _processor(mode="notify", **kw):
    d = dict(mode=mode, lead_name="Claude", reviewer_name="Codex", lead_pane="tagteam:0.0",
             reviewer_pane="tagteam:0.2", lead_session_id="L" if mode == "iterm2" else None,
             reviewer_session_id="R" if mode == "iterm2" else None, confirm=False, timeout_minutes=30,
             project_dir=".", max_retries=3, retry_delay=2.0, pre_send_delay=1.0)
    d.update(kw)
    return watcher._StateProcessor(**d)


def _state(seq, status="ready", turn="lead", **extra):
    s = {"seq": seq, "status": status, "turn": turn, "command": "/handoff", "phase": "p1", "type": "impl",
         "round": 2, "updated_at": f"2026-09-20T00:00:{seq:02d}+00:00"}
    s.update(extra)
    return s


@pytest.fixture
def sink(root, monkeypatch):
    monkeypatch.chdir(root)
    s = Sink(root, "iterm2", every_s=10)
    monkeypatch.setattr(watcher, "_SINK", s)
    return s


class TestWatcherSink:
    def test_without_a_sink_log_only_prints(self, root, monkeypatch, capsys):
        monkeypatch.chdir(root)
        assert watcher._SINK is None
        watcher._log("hello", kind="turn", phase="p1")
        assert capsys.readouterr().out.endswith("] hello\n")
        assert list((root / ".tagteam").iterdir()) == []

    def test_printed_line_is_unchanged_by_the_sink(self, sink, capsys):
        watcher._log("   Sent to Codex: /handoff", kind="sent", round=2)
        out = capsys.readouterr().out
        assert out[0] == "[" and out[9:] == "]    Sent to Codex: /handoff\n"

    def test_scripted_run_records_kinds_with_context(self, sink, root):
        p = _processor("iterm2")
        with patch("tagteam.watcher.send_iterm_command", return_value=True), \
             patch("tagteam.watcher.notify_macos"):
            p.tick(_state(1, turn="lead"))
        with patch("tagteam.watcher.send_iterm_command", return_value=False), \
             patch("tagteam.watcher.notify_macos"):
            p.tick(_state(2, turn="lead"))
        with patch("tagteam.watcher.notify_macos"), \
             patch.object(p, "_pause_info", return_value={"reason": "held by you", "by": "arbiter"}):
            p.tick(_state(3, turn="lead"))
        with patch("tagteam.watcher.send_iterm_command", return_value=True), \
             patch("tagteam.watcher.notify_macos"):
            p.tick(_state(3, turn="lead"))                      # marker gone → resumed
            p.tick(_state(4, status="aborted", turn=None, reason="gave up"))
        kinds = [(e["kind"], e.get("seq")) for e in _events(root) if e["kind"] != "info"]
        assert kinds == [("turn", 1), ("sent", 1), ("turn", 2), ("send-failed", 2), ("turn", 3), ("paused", None),
                         ("resumed", 3), ("turn", 3), ("sent", 3), ("aborted", 4)], kinds
        turn = next(e for e in _events(root) if e["kind"] == "turn")
        assert (turn["phase"], turn["type"], turn["round"], turn["turn"]) == ("p1", "impl", 2, "lead")
        assert all(e["mode"] == "iterm2" and e["pid"] == os.getpid() for e in _events(root))

    @posix_only
    def test_dispatch_proceeds_when_the_log_cannot_be_written(self, sink, root, alarm):
        os.mkfifo(root / watchlog.EVENTS_REL)
        p = _processor("iterm2")
        with patch("tagteam.watcher.send_iterm_command", return_value=True) as send, \
             patch("tagteam.watcher.notify_macos"):
            p.tick(_state(1, turn="reviewer"))
        assert send.call_count == 1

    def test_every_kind_used_in_the_watcher_is_in_the_vocabulary(self):
        import re
        src = Path(watcher.__file__).read_text(encoding="utf-8")
        used = set(re.findall(r'kind="([\w-]+)"\s*(?:,\s*\*\*_state_ctx\(state\))?\)', src))
        used -= {"auto"}            # run_gate / run_panel's own `kind=` argument
        assert used and used <= set(watchlog.KINDS), used - set(watchlog.KINDS)
        assert {"turn", "sent", "send-failed", "paused", "resumed", "gate", "panel", "done", "stop"} <= used


class TestHeartbeatLoops:
    def _poll_once(self, monkeypatch, root, interval=10):
        p = _processor("notify", project_dir=str(root))
        monkeypatch.setattr(watcher, "read_state", lambda _d: _state(5, status="working", turn="lead"))

        def stop(_s):
            raise KeyboardInterrupt
        monkeypatch.setattr(watcher.time, "sleep", stop)
        watcher._run_poll_loop(p, str(root), interval)

    def test_poll_loop_beats_and_records_the_stop(self, sink, root, monkeypatch):
        self._poll_once(monkeypatch, root)
        b = watchlog.read_beat(root)
        assert (b["pid"], b["seq"], b["status"], b["turn"], b["every_s"]) == (os.getpid(), 5, "working", "lead", 10.0)
        assert b["ident"] == procs.identity(os.getpid()) and b["mode"] == "iterm2"
        assert _events(root)[-1]["kind"] == "stop"

    @pytest.mark.parametrize("loop", ["poll", "event"])
    def test_a_long_tick_leaves_a_beat_from_after_it_with_the_state_it_produced(self, sink, root, monkeypatch, loop):
        """Phase 68a: the beat was written only BEFORE tick(), so after a 33 s
        headless turn `watch status` read STALE until the next iteration."""
        from tagteam import watcher_events
        clock = {"t": 1000.0}
        monkeypatch.setattr(watchlog.time, "monotonic", lambda: clock["t"])
        states = iter([_state(5, status="ready", turn="reviewer"), _state(6, status="done", turn=None)])
        monkeypatch.setattr(watcher, "read_state", lambda _d: next(states))
        p = _processor("notify", project_dir=str(root))
        seen = {}

        def long_tick(state):
            seen["before"] = watchlog.read_beat(root)
            clock["t"] += 33.0                          # the turn ran, and moved the state

        monkeypatch.setattr(p, "tick", long_tick)
        if loop == "poll":
            def stop(_s):
                raise KeyboardInterrupt
            monkeypatch.setattr(watcher.time, "sleep", stop)
            watcher._run_poll_loop(p, str(root), 10)
        else:
            monkeypatch.setattr(watcher_events, "watch_with_events", lambda _p, on_change: on_change())
            watcher._run_event_loop(p, str(root))
        after = watchlog.read_beat(root)
        assert (seen["before"]["seq"], seen["before"]["turn"]) == (5, "reviewer")
        assert (after["seq"], after["status"], after["turn"]) == (6, "done", None)
        assert after["ts"] > seen["before"]["ts"]

    def test_a_short_tick_writes_no_second_beat_and_rereads_nothing(self, sink, root, monkeypatch):
        reads = []

        def read_state(_d):
            reads.append(1)
            return _state(5, status="working")
        monkeypatch.setattr(watcher, "read_state", read_state)
        p = _processor("notify", project_dir=str(root))
        monkeypatch.setattr(p, "tick", lambda _s: None)

        def stop(_s):
            raise KeyboardInterrupt
        monkeypatch.setattr(watcher.time, "sleep", stop)
        writes = []
        real = sink._write_beat
        monkeypatch.setattr(sink, "_write_beat", lambda data: writes.append(1) or real(data))
        watcher._run_poll_loop(p, str(root), 10)
        assert len(writes) == 1 and len(reads) == 1

    def test_event_loop_beats_on_change(self, sink, root, monkeypatch):
        from tagteam import watcher_events
        monkeypatch.setattr(watcher, "read_state", lambda _d: _state(9))
        monkeypatch.setattr(watcher_events, "watch_with_events", lambda _p, on_change: on_change())
        p = _processor("notify", project_dir=str(root))
        with patch("tagteam.watcher.notify_macos"):
            assert watcher._run_event_loop(p, str(root)) is True
        assert watchlog.read_beat(root)["seq"] == 9

    @pytest.mark.parametrize("ending", ["inner-handler", "propagated"])
    def test_event_loop_records_exactly_one_stop(self, sink, root, monkeypatch, ending):
        """impl r3: `watch_with_events` catches KeyboardInterrupt itself and
        returns normally — the path a real Ctrl-C / SIGTERM takes."""
        from tagteam import watcher_events

        def fake(_path, on_change):
            on_change()
            try:
                raise KeyboardInterrupt
            except KeyboardInterrupt:
                if ending == "propagated":
                    raise
        monkeypatch.setattr(watcher, "read_state", lambda _d: _state(9, status="working"))
        monkeypatch.setattr(watcher_events, "watch_with_events", fake)
        assert watcher._run_event_loop(_processor("notify", project_dir=str(root)), str(root)) is True
        assert [e["kind"] for e in _events(root)].count("stop") == 1
        assert _events(root)[-1]["msg"] == "Watcher stopped."

    def test_event_loop_failure_is_an_error_not_a_stop(self, sink, root, monkeypatch):
        from tagteam import watcher_events

        def boom(_path, _on_change):
            raise SystemError("Cannot start fsevents stream")
        monkeypatch.setattr(watcher_events, "watch_with_events", boom)
        assert watcher._run_event_loop(_processor("notify", project_dir=str(root)), str(root)) is False
        kinds = [e["kind"] for e in _events(root)]
        assert kinds.count("error") == 1 and "stop" not in kinds      # the caller falls back to polling

    def test_throttle_and_concurrent_beats(self, root):
        s = Sink(root, "notify", every_s=1)
        assert s.beat({"seq": 1}) is True and s.beat({"seq": 2}) is False
        assert watchlog.read_beat(root)["seq"] == 1
        s2 = Sink(root, "notify", every_s=1)
        results = []
        ts = [threading.Thread(target=lambda: results.append(s2.beat({"seq": 3}))) for _ in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        assert results.count(True) == 1
        assert not list((root / ".tagteam").glob("*.tmp"))

    @pytest.mark.parametrize("pidfile", [False, True])
    def test_watch_installs_and_removes_the_sink_whatever_the_pidfile(self, root, monkeypatch, pidfile):
        monkeypatch.chdir(root)
        monkeypatch.setenv("TAGTEAM_WATCHER_LOCK_DIR", str(root / "locks"))
        (root / "tagteam.yaml").write_text("agents:\n  lead: {name: Claude}\n  reviewer: {name: Codex}\n")
        seen = {}

        def fake_loop(processor, project_dir, interval):
            seen["sink"] = watcher._SINK
            watcher._beat(_state(1, status="working"))
            seen["beat"] = watchlog.read_beat(root)
        monkeypatch.setattr(watcher, "_run_poll_loop", fake_loop)
        assert watcher.watch(mode="notify", project_dir=str(root), force_poll=True, interval=7,
                             pidfile=pidfile) is True
        assert isinstance(seen["sink"], Sink) and seen["beat"]["every_s"] == 7.0
        assert watcher._SINK is None
        assert watchlog.read_beat(root) is None                       # removed on exit…
        assert (root / ".tagteam" / "watcher.json").exists() is False
        kinds = [e["kind"] for e in _events(root)]
        assert "start" in kinds                                        # …the history stays

    def test_close_leaves_another_watchers_beat(self, root):
        other = Sink(root, "headless", every_s=10, pid=os.getpid() + 10_000)
        assert other.beat({"seq": 1})
        Sink(root, "notify", every_s=10).close()
        assert watchlog.read_beat(root)["pid"] == os.getpid() + 10_000


# ---------------------------------------------------------------------------
# beat_view — the shared reader contract

NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
ME = os.getpid()


def _write_beat(root, age_s, every_s=10, pid=None, ident="__mine__", **extra):
    pid = ME if pid is None else pid
    rec = {"pid": pid, "ident": procs.identity(ME) if ident == "__mine__" else ident, "mode": "iterm2",
           "started_at": NOW.isoformat(), "ts": (NOW - timedelta(seconds=age_s)).isoformat(),
           "every_s": every_s, "seq": 1, "status": "ready", "turn": "lead"}
    rec.update(extra)
    (root / watchlog.BEAT_REL).write_text(json.dumps(rec))


def _dead_pid():
    import subprocess
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


class TestBeatView:
    def _v(self, root, **kw):
        return watchlog.beat_view(root, now=NOW, **kw)

    def test_none_missing_and_malformed(self, root):
        assert self._v(root) == {"state": "none", "age_s": None, "every_s": None, "stale_after_s": None}
        for junk in ("not json", "[1]", '"str"', ""):
            (root / watchlog.BEAT_REL).write_text(junk)
            assert self._v(root)["state"] == "none", junk
        _write_beat(root, 1, ts="yesterday-ish")
        assert self._v(root)["state"] == "none"

    def test_fresh_and_stale_follow_the_beats_own_cadence(self, root):
        _write_beat(root, 25, every_s=10)
        v = self._v(root)
        assert (v["state"], v["age_s"], v["every_s"], v["stale_after_s"]) == ("fresh", 25.0, 10.0, 30.0)
        _write_beat(root, 40, every_s=10)
        assert self._v(root)["state"] == "stale"
        _write_beat(root, 150, every_s=60)                  # --interval 60
        assert self._v(root)["state"] == "fresh"
        _write_beat(root, 85, every_s=30)                   # event mode
        assert self._v(root)["state"] == "fresh"

    def test_short_intervals_are_judged_against_the_write_throttle(self, root):
        """--interval 1 still beats only every 5 s: 3 × 1 s would cry wolf."""
        _write_beat(root, 9, every_s=1)
        v = self._v(root)
        assert (v["state"], v["every_s"], v["stale_after_s"]) == ("fresh", 5.0, 15.0)
        _write_beat(root, 16, every_s=1)
        assert self._v(root)["state"] == "stale"
        for bad in (0, -3, None, "soon"):
            _write_beat(root, 80, every_s=bad)
            assert self._v(root)["every_s"] == 30.0 and self._v(root)["state"] == "fresh"

    def test_previous_watcher(self, root):
        _write_beat(root, 3, pid=_dead_pid(), ident="x")
        v = self._v(root)
        assert v["state"] == "previous" and v["age_s"] == 3.0
        _write_beat(root, 3, ident="someone-else-with-my-pid")
        assert self._v(root)["state"] == "previous"
        _write_beat(root, 3)                                 # alive, right identity…
        assert self._v(root, watcher={"running": True, "pid": ME + 1})["state"] == "previous"
        assert self._v(root, watcher={"running": True, "pid": ME})["state"] == "fresh"
        assert self._v(root, watcher={"running": False, "pid": None})["state"] == "fresh"

    def test_unreadable_identity_does_not_demote(self, root):
        _write_beat(root, 3)
        with patch.object(procs, "identity", return_value=None):
            assert self._v(root)["state"] == "fresh"

    def test_in_turn_only_when_this_watcher_runs_the_inflight(self, root):
        _write_beat(root, 600, every_s=10)
        mine = {"kind": "cycle", "watcher_pid": ME, "watcher_ident": procs.identity(ME), "pid": ME}
        assert self._v(root, inflight=mine)["state"] == "in-turn"
        assert self._v(root, inflight={**mine, "kind": "gate", "pid": None})["state"] == "in-turn"
        assert self._v(root, inflight={"watcher_pid": ME})["state"] == "in-turn"           # legacy marker
        for other in (None, {}, {**mine, "watcher_pid": ME + 1},                          # another runner
                      {**mine, "watcher_ident": "not-me"},                                # a reused pid
                      {"kind": "conversation", "watcher_pid": ME + 2, "pid": ME}):         # the server's turn
            assert self._v(root, inflight=other)["state"] == "stale", other

    def test_a_dead_runners_leftover_is_previous_not_in_turn(self, root):
        dead = _dead_pid()
        _write_beat(root, 600, pid=dead, ident="gone")
        assert self._v(root, inflight={"kind": "cycle", "watcher_pid": dead, "watcher_ident": "gone"})["state"] \
            == "previous"


# ---------------------------------------------------------------------------
# CLI


def _cli(monkeypatch, capsys, cwd, *argv):
    from tagteam import state as state_mod
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(state_mod, "_cached_project_root", None)    # the resolver memoises per process
    monkeypatch.setattr(sys, "argv", ["tagteam", *argv])
    rc = cli.main()
    out = capsys.readouterr()
    return rc, out.out, out.err


@pytest.fixture
def proj(root):
    (root / "tagteam.yaml").write_text("agents:\n  lead: {name: Claude}\n  reviewer: {name: Codex}\n")
    return root


class TestCli:
    def test_status_and_log_on_an_empty_project(self, proj, monkeypatch, capsys):
        rc, out, _ = _cli(monkeypatch, capsys, proj, "watch", "status")
        assert rc == 0 and out.splitlines() == [
            "now: Nothing in progress",                       # Phase 68: the cockpit's headline, first
            "watcher: not running", "last look: never (no heartbeat recorded)",
            "last dispatch: none recorded", "dispatch: not paused"]
        rc, out, _ = _cli(monkeypatch, capsys, proj, "watch", "log")
        assert rc == 0 and "No watcher events recorded yet" in out

    def test_log_renders_and_json(self, proj, monkeypatch, capsys):
        s = Sink(proj, "iterm2", every_s=10)
        for i in range(40):
            s.event(f"   line {i}", "sent" if i % 2 else "info")
        rc, out, _ = _cli(monkeypatch, capsys, proj, "watch", "log")
        lines = out.splitlines()
        assert rc == 0 and len(lines) == 30 and lines[-1].endswith("sent        line 39")
        assert lines[-1][2] == ":" and lines[-1][8:10] == "  "
        rc, out, _ = _cli(monkeypatch, capsys, proj, "watch", "log", "-n", "3", "--json")
        assert [json.loads(l)["msg"] for l in out.splitlines()] == ["   line 37", "   line 38", "   line 39"]
        assert _cli(monkeypatch, capsys, proj, "watch", "log", "-n", "many")[0] == 2
        assert _cli(monkeypatch, capsys, proj, "watch", "log", "--follow")[0] == 2

    def test_status_reports_the_beat_and_the_last_dispatch(self, proj, monkeypatch, capsys):
        s = Sink(proj, "iterm2", every_s=10)
        s.event(">> Codex's turn (phase: p1, round: 2)", "turn"); s.event("   Sent to Codex: /handoff", "sent")
        s.event("gate: pass", "gate"); s.beat({"seq": 1})
        rc, out, _ = _cli(monkeypatch, capsys, proj, "watch", "status")
        lines = out.splitlines()
        assert lines[0].startswith("now: ")
        assert lines[2] in ("last look: 0s ago", "last look: 1s ago")
        assert lines[3].startswith("last dispatch: ") and lines[3].endswith("Sent to Codex: /handoff")
        s.event("   REFUSED: " + "x" * 400, "refused")               # status stays one screen line; the log keeps it all
        line = _cli(monkeypatch, capsys, proj, "watch", "status")[1].splitlines()[3]
        assert line.endswith("…") and len(line) < 140
        assert len(_cli(monkeypatch, capsys, proj, "watch", "log", "-n", "1")[1]) > 400

    @pytest.mark.parametrize("read_only", [False, True])
    def test_status_creates_nothing_on_a_project_without_a_database(self, proj, monkeypatch, capsys, read_only):
        """Phase 68: the headline's facts never open the database — `now_payload`
        would db.connect(), which creates and migrates .tagteam/tagteam.db in
        ordinary mode (only TAGTEAM_READ_ONLY stops that)."""
        from tagteam import db
        (proj / "handoff-state.json").write_text(json.dumps({
            "phase": "p1", "type": "impl", "round": 2, "status": "ready", "turn": "reviewer", "seq": 4,
            "command": "/handoff", "updated_at": datetime.now(timezone.utc).isoformat()}))
        monkeypatch.setattr(db, "connect", lambda *a, **k: pytest.fail("watch status opened the database"))
        if read_only:
            monkeypatch.setenv("TAGTEAM_READ_ONLY", "1")
        else:
            monkeypatch.delenv("TAGTEAM_READ_ONLY", raising=False)

        def tree():
            return {str(q.relative_to(proj)): (q.read_bytes() if q.is_file() else None) for q in proj.rglob("*")}
        before = tree()
        rc, out, _ = _cli(monkeypatch, capsys, proj, "watch", "status")
        assert rc == 0 and out.splitlines()[0].startswith(
            "now: Waiting on Codex — the watcher is off, nothing will start its turn · ")
        assert tree() == before and not (proj / ".tagteam" / "tagteam.db").exists()

    def test_describe_beat_wording(self):
        d = watchlog.describe_beat
        assert d({"state": "previous", "age_s": 3 * 3600 + 60}) == "previous watcher, 3h 01m ago"
        assert d({"state": "in-turn", "age_s": 125}, {"agent": "claude"}) == "2m ago — busy running claude"
        assert d({"state": "in-turn", "age_s": 125}, {"kind": "gate"}) == "2m ago — busy running gate"
        assert d({"state": "stale", "age_s": 400, "every_s": 10.0}).startswith("STALE: 6m ago, expected every 10s")

    @pytest.mark.parametrize("argv", [["watch", "status"], ["watch", "log"], ["watch", "log", "-n", "5", "--json"]])
    def test_reads_are_allowed_read_only_and_write_nothing(self, proj, monkeypatch, capsys, argv):
        assert cli.read_only_refusal(argv) is None
        monkeypatch.setenv("TAGTEAM_READ_ONLY", "1")
        before = sorted(str(p.relative_to(proj)) for p in proj.rglob("*"))
        assert _cli(monkeypatch, capsys, proj, *argv)[0] == 0
        assert sorted(str(p.relative_to(proj)) for p in proj.rglob("*")) == before

    @pytest.mark.parametrize("args", [(), ("--help",), ("-h",), ("--mode", "notify"), ("--pidfile",),
                                      ("help",), ("start",), ("--mode", "headless", "status")])
    def test_starting_a_watcher_stays_refused_read_only(self, proj, monkeypatch, capsys, args):
        """`watch` left READ_ONLY_REFUSED for its two reads; everything else
        must still fail closed, through the real CLI, before dispatch."""
        monkeypatch.setenv("TAGTEAM_READ_ONLY", "1")
        monkeypatch.setattr(watcher, "watch", lambda **_kw: pytest.fail("a watcher was started"))
        rc, out, err = _cli(monkeypatch, capsys, proj, "watch", *args)
        assert rc == 2 and "tagteam: refused" in err and "is not a read command" in err, (args, out, err)
        assert not list((proj / ".tagteam").iterdir())

    def test_existing_watch_options_still_parse(self, proj, monkeypatch):
        monkeypatch.chdir(proj)
        got = {}
        monkeypatch.setattr(watcher, "watch", lambda **kw: got.update(kw) or True)
        assert watcher.watch_command(["--mode", "notify", "--interval", "7", "--pidfile", "--poll"]) == 0
        assert (got["mode"], got["interval"], got["pidfile"], got["force_poll"]) == ("notify", 7, True, True)


# ---------------------------------------------------------------------------
# API


class TestApi:
    def test_now_watcher_block_and_events_payload(self, proj):
        from tagteam import cockpit_api as capi
        w = capi.now_payload(str(proj))["watcher"]
        assert {"running", "pid", "mode", "source", "stale_pidfile"} <= set(w)          # existing keys stay
        assert w["beat"] == {"state": "none", "age_s": None, "every_s": None, "stale_after_s": None,
                             "text": "never (no heartbeat recorded)"}                  # Phase 68: + text
        assert w["last_event"] is None and w["last_dispatch"] is None
        assert capi.watcher_events_payload(str(proj)) == {"events": []}
        s = Sink(proj, "iterm2", every_s=10)
        s.event(">> Claude's turn", "turn", phase="p1"); s.event("chatter"); s.beat({"seq": 2})
        w = capi.now_payload(str(proj))["watcher"]
        assert w["beat"]["state"] == "fresh" and w["beat"]["stale_after_s"] == 30.0
        assert w["last_event"]["kind"] == "turn" and w["last_event"]["phase"] == "p1"
        assert [e["kind"] for e in capi.watcher_events_payload(str(proj), "1")["events"]] == ["info"]
        assert len(capi.watcher_events_payload(str(proj), "junk")["events"]) == 2
        assert len(capi.watcher_events_payload(str(proj), 10**9)["events"]) == 2

    def test_events_endpoint_is_a_tokenless_get(self, proj):
        from tests.test_server_cockpit import Served
        Sink(proj, "iterm2", every_s=10).event("   Sent to Codex", "sent", seq=4)
        with Served(proj, "cockpit") as s:
            r = s.client.get("/api/watcher/events?n=5")
            assert r["status"] == 200 and r["json"]["events"][0]["kind"] == "sent"
            assert s.client.get("/api/watcher/events?n=abc")["status"] == 200
            assert s.client.get("/api/now")["json"]["watcher"]["last_event"]["seq"] == 4
            # 3.14.5 took the first CHARACTER of n (n=200 → 2, n=37 → 3); single digits hid it
            sink = Sink(proj, "iterm2", every_s=10)
            for i in range(40):
                sink.event(f"e{i}", "sent" if i % 2 else "info", seq=i)
            assert len(s.client.get("/api/watcher/events?n=37")["json"]["events"]) == 37
            assert len(s.client.get("/api/watcher/events?n=200")["json"]["events"]) == 41
            story = s.client.get("/api/watcher/events?n=200&chatter=0")["json"]["events"]
            assert len(story) == 21 and all(e["kind"] != "info" for e in story)
