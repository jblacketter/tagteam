"""Phase 37: lead conversation engine + turn slot + roadmap intent helpers."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tagteam import db, headless as h, lead_chat as lc, procs
from tagteam.config import read_config

REPO = Path(__file__).resolve().parents[1]
FAKE = REPO / "tests" / "fixtures" / "fake_agent.py"


def _install_fake(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    for name in ("claude", "codex"):
        if sys.platform == "win32":
            (bin_dir / f"{name}.cmd").write_text(
                "@echo off\r\n" f"set FAKE_AGENT_FLAVOR={name}\r\n" f"\"{py}\" \"{FAKE}\" %*\r\n",
                encoding="utf-8")
        else:
            p = bin_dir / name
            p.write_text("#!/bin/sh\n" f"FAKE_AGENT_FLAVOR={name} exec \"{py}\" \"{FAKE}\" \"$@\"\n",
                         encoding="utf-8")
            p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def fake_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "fakebin"
    _install_fake(bin_dir)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_AGENT_MODE", "chat")
    monkeypatch.setenv("FAKE_AGENT_SLEEP", "0.05")
    monkeypatch.setenv("FAKE_AGENT_MEMDIR", str(tmp_path))
    monkeypatch.delenv("FAKE_AGENT_CAPTURE", raising=False)
    monkeypatch.delenv("FAKE_AGENT_CHAT_HTML", raising=False)
    monkeypatch.setenv("PYTHONPATH", str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    return bin_dir


def _project(tmp_path: Path, *, lead: str = "claude", reviewer: str = "codex") -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    (p / "tagteam.yaml").write_text(
        f"agents:\n  lead:\n    name: Claude\n    command: {lead}\n    headless:\n      provider: {lead}\n"
        f"  reviewer:\n    name: Codex\n    command: {reviewer}\n    headless:\n      provider: {reviewer}\n",
        encoding="utf-8")
    (p / ".claude" / "skills" / "handoff").mkdir(parents=True)
    (p / ".claude" / "skills" / "handoff" / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    (p / "docs").mkdir()
    return p


# ------------------------------------------------------------- slot ----

class TestTurnSlot:
    def test_claim_release_and_owner_only_unlink(self, tmp_path):
        root = tmp_path
        me = os.getpid()
        f = {"stem": "a", "watcher_pid": me, "watcher_ident": procs.identity(me), "pid": None}
        c1 = h.claim_turn_slot(root, kind="cycle", role="lead", fields=f)
        assert h.read_inflight(root)["owner_token"] == c1.token
        with pytest.raises(h.SlotBusy) as ei:
            h.claim_turn_slot(root, kind="conversation", role="lead", fields=dict(f, stem="b"))
        assert "alive" in ei.value.reason
        # a foreign claim object cannot release our marker
        fake = h.SlotClaim(token="nope", marker={}, path=c1.path, root=Path(root))
        assert h.release_turn_slot(fake) is False
        assert h.read_inflight(root)["owner_token"] == c1.token
        assert h.release_turn_slot(c1) is True
        assert h.read_inflight(root) is None

    def test_stale_owner_recovered_definitively_only(self, tmp_path):
        root = tmp_path
        # dead pid → recovered
        dead = {"stem": "old", "watcher_pid": 999999, "watcher_ident": "x:1", "pid": None}
        h.inflight_path(root).parent.mkdir(parents=True, exist_ok=True)
        h.inflight_path(root).write_text(json.dumps(dead), encoding="utf-8")
        c = h.claim_turn_slot(root, kind="cycle", role="lead",
                              fields={"stem": "new", "watcher_pid": os.getpid(),
                                      "watcher_ident": procs.identity(os.getpid()), "pid": None})
        assert c.recovered_from["stem"] == "old"
        h.release_turn_slot(c)
        # live pid, recorded identity definitively different → recovered
        h.inflight_path(root).write_text(json.dumps(
            {"stem": "reuse", "watcher_pid": os.getpid(), "watcher_ident": "definitely-not-me", "pid": None}),
            encoding="utf-8")
        c = h.claim_turn_slot(root, kind="cycle", role="lead",
                              fields={"stem": "new2", "watcher_pid": os.getpid(),
                                      "watcher_ident": procs.identity(os.getpid()), "pid": None})
        assert c.recovered_from["stem"] == "reuse"
        h.release_turn_slot(c)

    def test_fail_closed_on_unverifiable_owner(self, tmp_path, monkeypatch):
        root = tmp_path
        h.inflight_path(root).parent.mkdir(parents=True, exist_ok=True)
        # legacy marker: live pid, no identity recorded → BUSY, untouched
        legacy = {"stem": "legacy", "watcher_pid": os.getpid(), "pid": None}
        h.inflight_path(root).write_text(json.dumps(legacy), encoding="utf-8")
        with pytest.raises(h.SlotBusy) as ei:
            h.claim_turn_slot(root, kind="cycle", role="lead", fields={"stem": "x", "watcher_pid": 1})
        assert "legacy" in ei.value.reason and "fail closed" in ei.value.reason
        assert json.loads(h.inflight_path(root).read_text()) == legacy
        # identity lookup unavailable for a live pid → BUSY, untouched
        rec = {"stem": "live", "watcher_pid": os.getpid(), "watcher_ident": "recorded", "pid": None}
        h.inflight_path(root).write_text(json.dumps(rec), encoding="utf-8")
        monkeypatch.setattr(procs, "identity", lambda pid: None)
        with pytest.raises(h.SlotBusy) as ei:
            h.claim_turn_slot(root, kind="cycle", role="lead", fields={"stem": "x", "watcher_pid": 1})
        assert "unavailable" in ei.value.reason
        assert json.loads(h.inflight_path(root).read_text()) == rec
        assert h.slot_status(root)["held"] is True

    def test_barrier_race_exactly_one_winner(self, tmp_path):
        root = tmp_path
        results: list = []
        barrier = threading.Barrier(2)

        def go(name):
            barrier.wait()
            try:
                c = h.claim_turn_slot(root, kind="cycle" if name == "w" else "conversation", role="lead",
                                      fields={"stem": name, "watcher_pid": os.getpid(),
                                              "watcher_ident": procs.identity(os.getpid()), "pid": None})
                results.append(("won", name, c))
            except h.SlotBusy as b:
                results.append(("busy", name, b))
        ts = [threading.Thread(target=go, args=(n,)) for n in ("w", "s")]
        [t.start() for t in ts]; [t.join() for t in ts]
        kinds = sorted(r[0] for r in results)
        assert kinds == ["busy", "won"]
        winner = [r for r in results if r[0] == "won"][0][2]
        loser = [r for r in results if r[0] == "busy"][0][2]
        # the loser cannot erase the winner's marker
        assert h.release_turn_slot(h.SlotClaim(token="x", marker={}, path=winner.path, root=Path(root))) is False
        assert h.read_inflight(root)["stem"] == winner.marker["stem"]
        assert loser.marker["stem"] == winner.marker["stem"]
        h.release_turn_slot(winner)


# ------------------------------------------------------- resume argv ----

class TestResumeArgv:
    def test_claude_and_codex_shapes(self):
        a = h.build_conversation_argv(h.CLAUDE, "claude", [], "/p", session_id="S", resume=False)
        assert a[-2:] == ["--session-id", "S"] and a[:2] == ["claude", "-p"]
        a = h.build_conversation_argv(h.CLAUDE, "claude", [], "/p", session_id="S", resume=True)
        assert a[-2:] == ["--resume", "S"]
        first = h.build_conversation_argv(h.CODEX, "codex", [], "/p", session_id="", resume=False)
        res = h.build_conversation_argv(h.CODEX, "codex", [], "/p", session_id="T", resume=True)
        assert first == h.build_argv(h.CODEX, "codex", [], "/p")
        # parent options before the subcommand; same policy tokens; stdin marker last
        assert res[:5] == ["codex", "exec", "--json", "-C", "/p"]
        assert res[-3:] == ["resume", "T", "-"]
        assert res[5:-3] == first[5:-1]
        assert "--sandbox" in res and res.index("--sandbox") < res.index("resume")

    @pytest.mark.skipif(not __import__("shutil").which("codex"), reason="codex CLI not installed")
    def test_codex_parser_accepts_resume_shape(self):
        argv = h.build_conversation_argv(h.CODEX, "codex", [], "/tmp", session_id="T", resume=True)
        r = subprocess.run(argv[:-1] + ["--help"], capture_output=True, text=True, timeout=20)
        assert r.returncode == 0 and "resume" in (r.stdout + r.stderr).lower()

    def test_probe_is_cached_and_injectable(self):
        h._RESUME_PROBE.clear()
        calls = []
        class R:  # noqa
            returncode = 0; stdout = "Usage: codex exec resume"; stderr = ""
        assert h.codex_resume_supported("fake-codex-x", run=lambda argv: (calls.append(argv), R())[1]) is True
        assert h.codex_resume_supported("fake-codex-x", run=lambda argv: (_ for _ in ()).throw(RuntimeError())) is True
        assert len(calls) == 1
        h._RESUME_PROBE.clear()


# ---------------------------------------------------------- lead chat ----

class TestLeadChat:
    def test_new_send_resume_transcript_usage(self, tmp_path, fake_path, monkeypatch):
        p = _project(tmp_path)
        cfg = read_config(p / "tagteam.yaml")
        conv = lc.new_conversation(p, provider="claude")
        cid = conv["id"]
        assert lc.CONVERSATION_ID_RE.match(cid)
        cap = tmp_path / "cap1.json"
        monkeypatch.setenv("FAKE_AGENT_CAPTURE", str(cap))
        t1 = lc.send(p, cid, "hello lead", config=cfg, by="test")
        assert t1["status"] == "ok" and t1["n"] == 1
        assert t1["reply"] == "echo: hello lead" and t1["continuity"] == "new session"
        argv1 = json.loads(cap.read_text())["argv"]
        assert "--session-id" in argv1 and "--resume" not in argv1
        assert "You are the Lead agent" in json.loads(cap.read_text())["prompt"]
        # second message resumes and the fake proves continuity
        cap2 = tmp_path / "cap2.json"
        monkeypatch.setenv("FAKE_AGENT_CAPTURE", str(cap2))
        t2 = lc.send(p, cid, "second", config=cfg)
        assert t2["status"] == "ok" and t2["continuity"] == "resumed session"
        argv2 = json.loads(cap2.read_text())["argv"]
        assert "--resume" in argv2 and argv2[argv2.index("--resume") + 1] == argv1[argv1.index("--session-id") + 1]
        assert "earlier you said: hello lead" in t2["reply"]
        assert "You are the Lead agent" not in json.loads(cap2.read_text())["prompt"]
        # transcript + files under .tagteam/conversations/<cid>/
        tp = lc.transcript_path(p, cid)
        text = tp.read_text()
        assert "hello lead" in text and "echo: second" in text
        assert (lc.conversation_dir(p, cid) / "1.events.jsonl").exists()
        # usage rows kind=conversation
        conn = db.connect(project_dir=str(p))
        rows = db.get_usage(conn)
        assert len(rows) == 2 and all(r["kind"] == "conversation" for r in rows)
        assert rows[0]["role"] == "lead" and rows[0]["input_tokens"] == 5
        conn.close()
        # slot released
        assert h.read_inflight(p) is None
        assert lc.get_conversation(p, cid)["session_id"] == argv1[argv1.index("--session-id") + 1]

    def test_codex_replay_when_resume_unsupported_and_resume_when_supported(self, tmp_path, fake_path, monkeypatch):
        p = _project(tmp_path, lead="codex", reviewer="claude")
        cfg = read_config(p / "tagteam.yaml")
        conv = lc.new_conversation(p, provider="codex")
        cid = conv["id"]
        t1 = lc.send(p, cid, "first", config=cfg, resume_probe=lambda exe: False)
        assert t1["status"] == "ok" and t1["reply"] == "echo: first"
        cap = tmp_path / "cap.json"
        monkeypatch.setenv("FAKE_AGENT_CAPTURE", str(cap))
        t2 = lc.send(p, cid, "second", config=cfg, resume_probe=lambda exe: False)
        assert t2["continuity"] == "transcript replay"
        prompt = json.loads(cap.read_text())["prompt"]
        assert "transcript replay" in prompt and "[you] first" in prompt and "[lead] echo: first" in prompt
        assert "resume" not in json.loads(cap.read_text())["argv"]
        # now supported: resumed session with the thread id, parent options first
        cap3 = tmp_path / "cap3.json"
        monkeypatch.setenv("FAKE_AGENT_CAPTURE", str(cap3))
        t3 = lc.send(p, cid, "third", config=cfg, resume_probe=lambda exe: True)
        assert t3["continuity"] == "resumed session"
        argv = json.loads(cap3.read_text())["argv"]
        assert "resume" in argv and argv[-1] == "-" and argv.index("--sandbox") < argv.index("resume")

    def test_send_refused_while_cycle_turn_holds_the_slot(self, tmp_path, fake_path):
        p = _project(tmp_path)
        cfg = read_config(p / "tagteam.yaml")
        cid = lc.new_conversation(p, provider="claude")["id"]
        c = h.claim_turn_slot(p, kind="cycle", role="lead", fields={
            "stem": "cycle-r3", "round": 3, "watcher_pid": os.getpid(),
            "watcher_ident": procs.identity(os.getpid()), "pid": None})
        try:
            with pytest.raises(lc.LeadBusy) as ei:
                lc.send(p, cid, "hi", config=cfg)
            assert ei.value.marker["stem"] == "cycle-r3"
            assert lc.get_conversation(p, cid)["turns"] == []          # no turn row created
        finally:
            h.release_turn_slot(c)
        # and the reverse: a running conversation turn makes the engine's claim Busy
        # (unit-level: the marker is kind=conversation while running)
        seen = {}
        def slow_run(argv, prompt, cwd, **kw):
            seen["marker"] = h.read_inflight(p)
            with pytest.raises(h.SlotBusy):
                h.claim_turn_slot(p, kind="cycle", role="lead", fields={"stem": "cyc", "watcher_pid": os.getpid(),
                                                                       "watcher_ident": procs.identity(os.getpid()), "pid": None})
            kw["events_path"].write_text('{"type":"result","result":"ok","session_id":"s"}\n')
            return h.RunOutput(exit_code=0, timed_out=False, duration_ms=1)
        t = lc.send(p, cid, "hi", config=cfg, run=slow_run)
        assert seen["marker"]["kind"] == "conversation" and seen["marker"]["conversation_id"] == cid
        assert seen["marker"]["watcher_pid"] == os.getpid() and t["status"] == "ok"
        assert h.read_inflight(p) is None

    def test_failed_turn_surfaced_and_no_pause_marker(self, tmp_path, fake_path, monkeypatch):
        p = _project(tmp_path)
        cfg = read_config(p / "tagteam.yaml")
        cid = lc.new_conversation(p, provider="claude")["id"]
        monkeypatch.setenv("FAKE_AGENT_MODE", "nonzero")
        t = lc.send(p, cid, "hi", config=cfg)
        assert t["status"] == "failed" and "exited" in t["error"]
        assert h.read_pause(p) is None
        assert h.read_inflight(p) is None
        assert "no reply" in lc.transcript_path(p, cid).read_text()

    def test_reconcile_orphaned_running_turn(self, tmp_path, fake_path):
        p = _project(tmp_path)
        cid = lc.new_conversation(p, provider="claude")["id"]
        conn = db.connect(project_dir=str(p))
        db.add_conversation_turn(conn, conversation_id=cid, ts="2026-01-01T00:00:00+00:00", user_text="x",
                                 owner_pid=999999, owner_ident="gone:1")
        # a live owner (this process) with a recorded identity → left alone
        db.add_conversation_turn(conn, conversation_id=cid, ts="2026-01-01T00:00:01+00:00", user_text="y",
                                 owner_pid=os.getpid(), owner_ident=procs.identity(os.getpid()))
        conn.close()
        changed = lc.reconcile(p)
        assert [t["n"] for t in changed] == [1]
        turns = lc.get_conversation(p, cid)["turns"]
        assert turns[0]["status"] == "failed" and "orphaned" in turns[0]["error"]
        assert turns[1]["status"] == "running"

    def test_events_replay_from_cursor(self, tmp_path, fake_path):
        p = _project(tmp_path)
        cfg = read_config(p / "tagteam.yaml")
        cid = lc.new_conversation(p, provider="claude")["id"]
        lc.send(p, cid, "one", config=cfg)
        evs = lc.turn_events(p, cid)
        ids = [e["id"] for e in evs]
        assert ids[-1] == "1:end" and all(i.startswith("1:") for i in ids)
        assert evs[-1]["status"] == "ok" and evs[-1]["reply"] == "echo: one"
        # cursor: after the 2nd event → the rest exactly once; after end → nothing
        rest = lc.turn_events(p, cid, after=ids[1])
        assert [e["id"] for e in rest] == ids[2:]
        assert lc.turn_events(p, cid, after="1:end") == []
        lc.send(p, cid, "two", config=cfg)
        again = lc.turn_events(p, cid, after="1:end")
        assert again and all(e["id"].startswith("2:") for e in again) and again[-1]["id"] == "2:end"

    def test_id_and_size_boundaries(self, tmp_path, fake_path):
        p = _project(tmp_path)
        cfg = read_config(p / "tagteam.yaml")
        with pytest.raises(lc.LeadChatError):
            lc.conversation_dir(p, "../etc")
        with pytest.raises(lc.LeadChatError):
            lc.conversation_dir(p, "/abs")
        assert lc.get_conversation(p, "c-000000000000") is None
        cid = lc.new_conversation(p, provider="claude")["id"]
        with pytest.raises(lc.LeadChatError):
            lc.send(p, cid, "x" * (lc.MAX_MESSAGE_BYTES + 1), config=cfg)
        with pytest.raises(lc.LeadChatError):
            lc.send(p, "c-ffffffffffff", "hi", config=cfg)
        assert lc.conversation_dir(p, cid).resolve().is_relative_to(lc.conversations_dir(p).resolve())

    def test_lead_not_configured(self, tmp_path, fake_path):
        p = _project(tmp_path)
        (p / "tagteam.yaml").write_text("agents:\n  lead:\n    name: Lead\n    command: mystery-cli\n  reviewer:\n    name: Codex\n", encoding="utf-8")
        cfg = read_config(p / "tagteam.yaml")
        cid = lc.new_conversation(p, provider=None)["id"]
        with pytest.raises(lc.LeadChatError, match="not configured"):
            lc.send(p, cid, "hi", config=cfg)


# ------------------------------------------------------- roadmap status ----

class TestRoadmapTerminal:
    def test_terminal_normalization(self):
        from tagteam.roadmap import is_terminal_status
        for s in ("Complete", "✅ Complete — impl approved 2026-08-15 (round 3)", "Complete (2026-05-03).",
                  "Absorbed — see Phase 28", "Deferred (2026-05-03) — superseded", "Superseded by X", "  ✅  complete"):
            assert is_terminal_status(s), s
        for s in ("Not started", "In progress — plan cycle opened", "Planning", "Unknown", "", None,
                  "Not started — deliberately kept out"):
            assert not is_terminal_status(s), s

    def test_real_roadmap_queue_starts_at_first_open_phase(self, tmp_path):
        from tagteam import roadmap
        src = (REPO / "docs" / "roadmap.md").read_text(encoding="utf-8")
        rp = tmp_path / "roadmap.md"; rp.write_text(src, encoding="utf-8")
        allp = roadmap.parse_roadmap(rp)
        assert len(allp) > 30                                   # the real roadmap: many phases, many status forms
        # Phase 20-era "Complete" / "Absorbed" / "Deferred" / "✅ Complete …" are all terminal now
        terminal = [p for p in allp if roadmap.is_terminal_status(p.status)]
        assert len(terminal) >= 30
        assert any(p.status.startswith("✅") for p in terminal) and any(p.status.startswith("Absorbed") for p in terminal)
        open_ = [p for p in allp if not roadmap.is_terminal_status(p.status)]
        if open_:
            phases = roadmap.get_incomplete_phases(rp)
            assert [p.slug for p in phases] == [p.slug for p in open_]
            assert phases[0].slug == open_[0].slug
        else:   # every phase shipped: the queue is honestly empty (was: started at Phase 20)
            with pytest.raises(ValueError, match="complete"):
                roadmap.get_incomplete_phases(rp)


# ------------------------------------------------ watcher: slot re-dispatch ----

class TestWatcherSlotRedispatch:
    def test_headless_watcher_redispatches_once_the_slot_frees(self, tmp_path):
        """A conversation turn held the slot when the owed turn arrived; the
        engine declined (slot_busy). Same seq, later tick, slot free →
        dispatch exactly once; while still held → nothing."""
        from tagteam.watcher import _StateProcessor
        calls = []

        class Eng:
            slot_busy = None
            def paused(self): return None
            def run_owed_turn(self, state):
                calls.append(state["seq"])
                st = h.slot_status(tmp_path)
                if st["held"]:
                    self.slot_busy = {"reason": st["reason"]}
                    return None
                self.slot_busy = None
                return "ran"
        eng = Eng()
        w = _StateProcessor(mode="headless", lead_name="Claude", reviewer_name="Codex", lead_pane="a",
                            reviewer_pane="b", lead_session_id=None, reviewer_session_id=None, confirm=False,
                            timeout_minutes=30, project_dir=str(tmp_path), max_retries=0, retry_delay=0,
                            pre_send_delay=0, engine=eng)
        state = {"seq": 5, "status": "ready", "turn": "lead", "command": "/handoff", "phase": "p", "round": 1,
                 "updated_at": "2026-05-03T00:00:05+00:00"}
        claim = h.claim_turn_slot(tmp_path, kind="conversation", role="lead", fields={
            "stem": "conv", "watcher_pid": os.getpid(), "watcher_ident": procs.identity(os.getpid()), "pid": None})
        w.tick(state)                      # first sight of seq 5 → dispatch → busy
        assert calls == [5] and eng.slot_busy is not None
        w.tick(state)                      # same seq, still held → no re-dispatch
        assert calls == [5]
        h.release_turn_slot(claim)
        w.tick(state)                      # slot freed → dispatched again, once
        assert calls == [5, 5] and eng.slot_busy is None
        w.tick(state)
        assert calls == [5, 5]


# ------------------------------------------ lifecycle ownership (impl r2) ----

class TestSendLifecycle:
    def _proj(self, tmp_path):
        p = _project(tmp_path)
        return p, read_config(p / "tagteam.yaml")

    def test_failure_before_the_row_releases_the_slot(self, tmp_path, fake_path, monkeypatch):
        p, cfg = self._proj(tmp_path)
        cid = lc.new_conversation(p, provider="claude")["id"]
        monkeypatch.setattr(db, "add_conversation_turn", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
        with pytest.raises(RuntimeError):
            lc.send(p, cid, "hi", config=cfg)
        assert h.read_inflight(p) is None
        assert lc.get_conversation(p, cid)["turns"] == []

    def test_failure_after_the_row_ends_it_and_releases(self, tmp_path, fake_path, monkeypatch):
        p, cfg = self._proj(tmp_path)
        cid = lc.new_conversation(p, provider="claude")["id"]
        monkeypatch.setattr(lc, "_append_transcript", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(OSError):
            lc.send(p, cid, "hi", config=cfg)
        assert h.read_inflight(p) is None
        turns = lc.get_conversation(p, cid)["turns"]
        assert len(turns) == 1 and turns[0]["status"] == "failed" and "setup failed" in turns[0]["error"]

    def test_unexpected_runner_exception_ends_the_turn(self, tmp_path, fake_path):
        p, cfg = self._proj(tmp_path)
        cid = lc.new_conversation(p, provider="claude")["id"]
        def boom(*a, **k):
            raise ValueError("runner exploded")
        with pytest.raises(ValueError):
            lc.send(p, cid, "hi", config=cfg, run=boom)
        assert h.read_inflight(p) is None
        turns = lc.get_conversation(p, cid)["turns"]
        assert turns[-1]["status"] == "failed" and "runner error" in turns[-1]["error"]
        assert "no reply" in lc.transcript_path(p, cid).read_text()
        # the conversation is usable again
        t = lc.send(p, cid, "again", config=cfg)
        assert t["status"] == "ok" and lc.reconcile(p) == []

    def test_finalize_failure_ends_the_turn(self, tmp_path, fake_path, monkeypatch):
        p, cfg = self._proj(tmp_path)
        cid = lc.new_conversation(p, provider="claude")["id"]
        monkeypatch.setattr(lc, "extract_reply", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("parse bug")))
        with pytest.raises(RuntimeError):
            lc.send(p, cid, "hi", config=cfg)
        assert h.read_inflight(p) is None
        turns = lc.get_conversation(p, cid)["turns"]
        assert turns[-1]["status"] == "failed" and "finalize failed" in turns[-1]["error"]

    def test_start_turn_then_run_turn_split(self, tmp_path, fake_path):
        p, cfg = self._proj(tmp_path)
        cid = lc.new_conversation(p, provider="claude")["id"]
        handle = lc.start_turn(p, cid, "split", config=cfg)
        assert h.read_inflight(p)["conversation_id"] == cid and h.read_inflight(p)["turn_n"] == handle.n
        assert lc.get_conversation(p, cid)["turns"][0]["status"] == "running"
        with pytest.raises(lc.LeadChatError, match="still running"):
            lc.start_turn(p, cid, "second", config=cfg)
        t = lc.run_turn(handle)
        assert t["status"] == "ok" and h.read_inflight(p) is None


# Phase 76: the first-turn state line reads as prose, never `turn None`.
class TestStateLine:
    def _line(self, tmp_path, monkeypatch, **state):
        monkeypatch.setattr("tagteam.state.read_state", lambda root: state)
        return lc._state_line(tmp_path)

    def test_done_cycle_names_the_result_and_no_none(self, tmp_path, monkeypatch):
        line = self._line(tmp_path, monkeypatch, phase="loom-experiments", type="impl",
                          round=1, status="done", turn=None, result="approved")
        assert line == ("phase loom-experiments · impl · round 1 · "
                        "status done (approved) · turn —")
        assert "None" not in line

    def test_live_cycle_is_unchanged(self, tmp_path, monkeypatch):
        assert self._line(tmp_path, monkeypatch, phase="a", type="plan", round=2,
                          status="ready", turn="reviewer") == (
            "phase a · plan · round 2 · status ready · turn reviewer")

    def test_no_phase(self, tmp_path, monkeypatch):
        assert self._line(tmp_path, monkeypatch) == "no active cycle"
