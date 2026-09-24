"""Phase 37: launch intent matrix, start payload, composite launch (idempotent,
crash windows, retry), the cockpit's start/launch/watch/session/lead
endpoints (send → SSE replay/reconnect, busy 409, boundaries), the port
lease, hub rows carrying the intent, and the `tagteam lead` CLI."""
from __future__ import annotations

import io
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from tagteam import db, headless as h, launch as L, lead_chat as lc, procs
from tagteam import server as srv, portlease
from tagteam.config import read_config
from tests.test_lead_chat import _install_fake, _project, REPO
from tests.test_server_cockpit import Client, Served, SSEReader

ROADMAP = """# Roadmap

### Phase 1: Alpha Work
- **Status:** ✅ Complete — shipped
### Phase 2: Beta Work
- **Status:** Absorbed — see Phase 1
### Phase 3: Gamma Work
- **Status:** Not started
### Phase 4: Delta Work
- **Status:** In progress
### Phase 5: Epsilon Work
- **Status:** Deferred (2026-05-03)
"""


@pytest.fixture
def fake_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "fakebin"
    _install_fake(bin_dir)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_AGENT_MODE", "chat")
    monkeypatch.setenv("FAKE_AGENT_SLEEP", "0.05")
    monkeypatch.setenv("FAKE_AGENT_MEMDIR", str(tmp_path))
    monkeypatch.setenv("PYTHONPATH", str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("TAGTEAM_PORT_LEASE_DIR", str(tmp_path / "leases"))
    return bin_dir


def _proj(tmp_path, roadmap=ROADMAP):
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = _project(tmp_path)
    (p / "docs" / "roadmap.md").write_text(roadmap, encoding="utf-8")
    return p


def _cycle(p: Path, phase: str, ctype: str, state: str, round_=1):
    from tagteam import cycle
    if state == "in-progress":
        cycle.init_cycle(phase, ctype, "Claude", "Codex", "submitted", project_dir=str(p), updated_by="Claude")
        return
    cycle.init_cycle(phase, ctype, "Claude", "Codex", "submitted", project_dir=str(p), updated_by="Claude")
    if state == "approved":
        cycle.add_round(phase, ctype, "reviewer", "APPROVE", 1, "ok", project_dir=str(p), updated_by="Codex")
    elif state == "escalated":
        cycle.add_round(phase, ctype, "reviewer", "ESCALATE", 1, "?", project_dir=str(p), updated_by="Codex")


# ---------------------------------------------------------- intent ----

class TestLaunchIntent:
    def test_matrix(self, tmp_path):
        p = _proj(tmp_path)
        it = L.launch_intent(p)
        assert it["command"] == "/handoff start gamma-work" and it["type"] == "plan"      # no state → first actionable
        _cycle(p, "gamma-work", "plan", "in-progress")
        it = L.launch_intent(p)
        assert it["command"] is None and "in progress" in it["reason"]
        _cycle_add_approve = None
        from tagteam import cycle
        cycle.add_round("gamma-work", "plan", "reviewer", "APPROVE", 1, "ok", project_dir=str(p), updated_by="Codex")
        it = L.launch_intent(p)
        assert it == {**it, "phase": "gamma-work", "type": "impl", "command": "/handoff start gamma-work impl"}
        _cycle(p, "gamma-work", "impl", "approved")
        it = L.launch_intent(p)
        # Phase 69: Delta's roadmap status says "In progress" — a DECLARED phase is never offered
        # Start, and nothing else is ready (Epsilon is deferred)
        assert it["command"] is None and "no phase is ready" in it["reason"]
        # escalated → none
        _cycle(p, "delta-work", "plan", "escalated")
        it = L.launch_intent(p)
        assert it["command"] is None and "in progress" in it["reason"]
        # observed echoes the state
        assert it["observed"]["phase"] == "delta-work" and it["observed"]["state"] == "escalated"

    def test_exhausted_setup_missing_and_paused(self, tmp_path):
        p = _proj(tmp_path, roadmap="# Roadmap\n\n### Phase 1: Only\n- **Status:** Complete\n")
        it = L.launch_intent(p)
        assert it["command"] is None and "no phase is ready" in it["reason"]
        (p / "docs" / "roadmap.md").unlink()
        assert "quickstart" in L.launch_intent(p)["reason"]
        (p / "tagteam.yaml").unlink()
        assert "tagteam.yaml" in L.launch_intent(p)["reason"]
        p2 = _proj(tmp_path / "two")
        h.write_pause(p2, {"reason": "failed turn", "ts": "2026-01-01T00:00:00+00:00"})
        it = L.launch_intent(p2)
        assert it["command"] is None and "paused" in it["reason"]

    def test_impl_approved_offers_the_first_ready_phase_not_the_next_in_document_order(self, tmp_path):
        """Phase 69: `_next_after` (document order, the just-approved phase
        skipped by name) is gone — the next phase is the first READY one."""
        p = _proj(tmp_path)
        _cycle(p, "delta-work", "impl", "approved")   # roadmap says Delta "In progress": in progress (approved)
        it = L.launch_intent(p)
        assert it["command"] == "/handoff start gamma-work"     # before Delta in the document, and ready
        p2 = _proj(tmp_path / "b", roadmap=ROADMAP.replace("### Phase 5: Epsilon Work\n- **Status:** Deferred (2026-05-03)",
                                                             "### Phase 5: Epsilon Work\n- **Status:** Not started"))
        _cycle(p2, "delta-work", "impl", "approved")
        assert L.launch_intent(p2)["command"] == "/handoff start gamma-work"
        assert L.launch_intent(p2, phase="epsilon-work")["command"] == "/handoff start epsilon-work"   # a chosen ready phase

    def test_start_payload_headless_gate(self, tmp_path, fake_path):
        p = _proj(tmp_path)
        cfg = read_config(p / "tagteam.yaml")
        sp = L.start_payload(p, cfg)
        assert sp["headless"]["ok"] and sp["recommended"] == "headless"
        assert sp["commands"]["headless"][0].startswith("tagteam watch --mode headless")
        (p / "tagteam.yaml").write_text("agents:\n  lead:\n    name: Claude\n    headless:\n      provider: claude\n"
                                        "  reviewer:\n    name: Rev\n    headless:\n      provider: nope\n", encoding="utf-8")
        sp = L.start_payload(p, read_config(p / "tagteam.yaml"))
        assert not sp["headless"]["ok"] and sp["recommended"] == "interactive"
        assert any("reviewer" in e for e in sp["headless"]["errors"])


# ------------------------------------------------------- composite ----

class TestComposite:
    def _send_ok(self, calls):
        def _s():
            calls.append("send")
            return {"n": 1, "status": "ok", "reply": "echo"}
        return _s

    def test_launch_idempotent_and_no_duplicate(self, tmp_path, fake_path, monkeypatch):
        p = _proj(tmp_path)
        cfg = read_config(p / "tagteam.yaml")
        it = L.launch_intent(p)
        calls = []
        # fake watcher start: pretend it started (no real process)
        monkeypatch.setattr(L, "start_watcher", lambda root, mode="headless", wait_s=5.0: {"ok": True, "pid": os.getpid(), "mode": mode, "message": "fake"})
        st, res = L.launch(p, intent=it, config=cfg, by="t", send=self._send_ok(calls))
        assert st == 200 and res["launched"] and calls == ["send"]
        # repeat → existing, no second message
        st2, res2 = L.launch(p, intent=it, config=cfg, by="t", send=self._send_ok(calls))
        assert st2 == 200 and res2["launched"] is False and res2["existing"]["conversation_id"] == res["conversation_id"]
        assert calls == ["send"]
        # observed drift → 409
        bad = dict(it, observed=dict(it["observed"], seq=999))
        st3, _ = L.launch(p, intent=bad, config=cfg, by="t", send=self._send_ok(calls))
        assert st3 == 409 and calls == ["send"]

    def test_concurrent_identical_posts_one_watcher_one_message(self, tmp_path, fake_path, monkeypatch):
        p = _proj(tmp_path)
        cfg = read_config(p / "tagteam.yaml")
        it = L.launch_intent(p)
        starts, sends = [], []
        def fake_start(root, mode="headless", wait_s=5.0):
            starts.append(1); time.sleep(0.2)
            return {"ok": True, "pid": os.getpid(), "mode": mode, "message": "fake"}
        monkeypatch.setattr(L, "start_watcher", fake_start)
        barrier = threading.Barrier(3)
        results = []
        def go():
            barrier.wait()
            def _s():
                sends.append(1); return {"n": 1, "status": "ok"}
            results.append(L.launch(p, intent=it, config=cfg, by="t", send=_s))
        ts = [threading.Thread(target=go) for _ in range(3)]
        [t.start() for t in ts]; [t.join() for t in ts]
        assert len(starts) == 1 and len(sends) == 1
        codes = sorted(r[0] for r in results)
        assert codes[0] == 200 and all(c in (200, 202) for c in codes)

    def test_watcher_early_exit_and_partial_state(self, tmp_path, fake_path, monkeypatch):
        p = _proj(tmp_path)
        cfg = read_config(p / "tagteam.yaml")
        it = L.launch_intent(p)
        monkeypatch.setattr(L, "start_watcher", lambda root, mode="headless", wait_s=5.0: {"ok": False, "exited": 2, "message": "the watcher exited immediately (code 2)"})
        st, res = L.launch(p, intent=it, config=cfg, by="t", send=lambda: (_ for _ in ()).throw(AssertionError("must not send")))
        assert st == 409 and "watcher" in res["error"] and res["status"] == "failed"
        # slot busy partial state after the watcher started
        monkeypatch.setattr(L, "start_watcher", lambda root, mode="headless", wait_s=5.0: {"ok": True, "pid": os.getpid(), "mode": mode, "message": "fake"})
        st, res = L.launch(p, intent=it, config=cfg, by="t", retry=True,
                           send=lambda: (_ for _ in ()).throw(lc.LeadBusy({"stem": "cycle-r3"}, "owner alive")))
        assert st == 409 and "slot busy" in res["error"] and res["partial"]["watcher"]["pid"] == os.getpid()
        # a plain repeat reports the stored failure; retry:true reruns only the missing step (turn), reusing the watcher
        st, res = L.launch(p, intent=it, config=cfg, by="t")
        assert st == 409 and res["status"] == "failed"
        sends = []
        st, res = L.launch(p, intent=it, config=cfg, by="t", retry=True, send=lambda: (sends.append(1), {"n": 1, "status": "ok"})[1])
        assert st == 200 and res["launched"] and res["watcher"]["reused"] and sends == [1]

    def test_crash_windows_reconciled_and_retryable(self, tmp_path, fake_path, monkeypatch):
        p = _proj(tmp_path)
        cfg = read_config(p / "tagteam.yaml")
        it = L.launch_intent(p)
        key = L.launch_key(it)
        dead_pid, dead_ident = 999999, "dead:1"
        conn = db.connect(project_dir=str(p))
        # (1) crashed after claim, before watcher
        db.claim_launch(conn, key=key, ts="t", intent_json=json.dumps(it), owner_pid=dead_pid, owner_ident=dead_ident)
        conn.close()
        changed = L.reconcile_launches(p)
        assert changed and changed[0]["status"] == "failed" and json.loads(changed[0]["partial_json"])["watcher"] is None
        # retry runs everything (watcher + turn), no duplicate
        monkeypatch.setattr(L, "start_watcher", lambda root, mode="headless", wait_s=5.0: {"ok": True, "pid": os.getpid(), "mode": mode, "message": "fake"})
        sends = []
        st, res = L.launch(p, intent=it, config=cfg, by="t", retry=True, send=lambda: (sends.append(1), {"n": 1, "status": "ok"})[1])
        assert st == 200 and sends == [1]
        conn = db.connect(project_dir=str(p)); row = db.get_launch(conn, key); conn.close()
        assert row["status"] == "succeeded" and row["attempt"] == 2
        # (2) crashed after watcher, before turn (fresh key: change observed via a new project)
        p2 = _proj(tmp_path / "p2"); it2 = L.launch_intent(p2); key2 = L.launch_key(it2)
        conn = db.connect(project_dir=str(p2))
        db.claim_launch(conn, key=key2, ts="t", intent_json=json.dumps(it2), owner_pid=dead_pid, owner_ident=dead_ident)
        db.update_launch(conn, key2, ts="t", watcher_pid=os.getpid(), watcher_ident=procs.identity(os.getpid()))
        conn.close()
        # reconcile-on-request: a plain launch reconciles the orphan and reports the truthful partial state
        st, res = L.launch(p2, intent=it2, config=cfg, by="t", send=lambda: {"n": 1, "status": "ok"})
        assert st == 409 and res["status"] == "failed" and res["partial"]["watcher"] == {"pid": os.getpid(), "alive": True}
        sends = []
        st, res = L.launch(p2, intent=it2, config=cfg, by="t", retry=True, send=lambda: (sends.append(1), {"n": 1, "status": "ok"})[1])
        assert st == 200 and res["watcher"]["reused"] and sends == [1]
        # (3) crashed after turn creation, before finalization
        p3 = _proj(tmp_path / "p3"); it3 = L.launch_intent(p3); key3 = L.launch_key(it3)
        cid = lc.new_conversation(p3, provider="claude")["id"]
        conn = db.connect(project_dir=str(p3))
        db.add_conversation_turn(conn, conversation_id=cid, ts="t", user_text=it3["command"], owner_pid=dead_pid, owner_ident=dead_ident)
        db.finish_conversation_turn(conn, cid, 1, status="ok", ts="t", reply="done")
        db.claim_launch(conn, key=key3, ts="t", intent_json=json.dumps(it3), owner_pid=dead_pid, owner_ident=dead_ident)
        db.update_launch(conn, key3, ts="t", watcher_pid=os.getpid(), watcher_ident=procs.identity(os.getpid()), conversation_id=cid, turn_n=1)
        conn.close()
        st, res = L.launch(p3, intent=it3, config=cfg, by="t", retry=True, send=lambda: (_ for _ in ()).throw(AssertionError("no second message")))
        assert st == 200 and res["launched"] is False and res["existing"] == {"conversation_id": cid, "turn_n": 1}


# --------------------------------------------------------- server ----

def _cockpit(p):
    return Served(p, "cockpit")


class TestServerEndpoints:
    def test_start_payload_and_info(self, tmp_path, fake_path):
        p = _proj(tmp_path)
        with _cockpit(p) as s:
            r = s.client.get("/api/start")
            assert r["status"] == 200 and r["json"]["intent"]["command"] == "/handoff start gamma-work"
            info = s.client.get("/api/info")["json"]
            assert info["app"] == "tagteam" and info["kind"] == "cockpit" and info["project"] == "proj"
        with Served(p, "legacy") as s:
            assert s.client.get("/api/info")["json"]["kind"] == "saloon"

    def test_lead_send_stream_replay_and_reconnect(self, tmp_path, fake_path, monkeypatch):
        monkeypatch.setenv("FAKE_AGENT_SLEEP", "0.4")
        p = _proj(tmp_path)
        with _cockpit(p) as s:
            c = s.client
            assert c.post("/api/lead/new", {}, headers={}).get("status") == 403          # token required
            r = c.post("/api/lead/new", {}, headers=s.auth()); assert r["status"] == 200
            cid = r["json"]["conversation"]["id"]
            r = c.post(f"/api/lead/{cid}/send", {"text": "hello"}, headers=s.auth())
            assert r["status"] == 202 and r["json"]["turn_n"] == 1
            rd = SSEReader(s.port, path=f"/api/lead/{cid}/events")
            frames = rd.wait_frames(2, timeout=8)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not any(f.get("event") == "end" for f in rd.frames):
                rd.pump(0.3)
            rd.close()
            ids = [f["id"] for f in rd.frames]
            assert ids and ids == sorted(ids, key=lambda x: (int(x.split(":")[0]), 10**9 if x.endswith("end") else int(x.split(":")[1])))
            end = [f for f in rd.frames if f.get("event") == "end"][0]
            assert end["data"]["status"] == "ok" and end["data"]["reply"] == "echo: hello"
            # fast-finish: subscribe only after the turn is over → still get everything after the cursor
            late = SSEReader(s.port, path=f"/api/lead/{cid}/events")
            late.wait_frames(len(ids), timeout=6); late.close()
            assert [f["id"] for f in late.frames][:len(ids)] == ids
            # reconnect with Last-Event-ID = the 2nd id → exactly the rest, no dupes
            rec = SSEReader(s.port, headers={"Last-Event-ID": ids[1]}, path=f"/api/lead/{cid}/events")
            rec.wait_frames(len(ids) - 2, timeout=6); rec.close()
            assert [f["id"] for f in rec.frames][:len(ids) - 2] == ids[2:]
            # after 'end' → nothing (heartbeat only)
            done = SSEReader(s.port, headers={"Last-Event-ID": ids[-1]}, path=f"/api/lead/{cid}/events")
            done.pump(1.5); done.close()
            assert done.frames == []
            # transcript + GET
            r = c.get(f"/api/lead/{cid}")
            assert r["json"]["turns"][0]["reply"] == "echo: hello" and r["json"]["slot"]["held"] is False
            assert (p / ".tagteam" / "conversations" / cid / "transcript.md").exists()

    def test_send_refused_when_cycle_turn_holds_slot_and_boundaries(self, tmp_path, fake_path):
        p = _proj(tmp_path)
        with _cockpit(p) as s:
            c = s.client
            cid = c.post("/api/lead/new", {}, headers=s.auth())["json"]["conversation"]["id"]
            claim = h.claim_turn_slot(p, kind="cycle", role="lead", fields={"stem": "cyc-r2", "round": 2,
                                      "watcher_pid": os.getpid(), "watcher_ident": procs.identity(os.getpid()), "pid": None})
            try:
                r = c.post(f"/api/lead/{cid}/send", {"text": "hi"}, headers=s.auth())
                assert r["status"] == 409 and r["json"]["busy"] and "busy" in r["json"]["message"]
                assert c.get(f"/api/lead/{cid}")["json"]["turns"] == []
                assert c.get("/api/lead")["json"]["slot"]["kind"] == "cycle"
            finally:
                h.release_turn_slot(claim)
            # boundaries: bad ids never touch the filesystem
            for bad in ("../../etc", "c-zzzzzzzzzzzz", "c-000000000000", "%2e%2e%2f"):
                assert c.get(f"/api/lead/{bad}")["status"] == 404
                assert c.post(f"/api/lead/{bad}/send", {"text": "x"}, headers=s.auth())["status"] == 404
                assert c.get(f"/api/lead/{bad}/events")["status"] == 404
            big = "x" * (lc.MAX_MESSAGE_BYTES + 10)
            assert c.post(f"/api/lead/{cid}/send", {"text": big}, headers=s.auth())["status"] == 413
            assert c.post(f"/api/lead/{cid}/send", {"text": ""}, headers=s.auth())["status"] == 400
            # dry-run preview
            r = c.post(f"/api/lead/{cid}/send", {"text": "hi", "dry_run": True}, headers=s.auth())
            assert r["status"] == 200 and r["json"]["cli"].startswith("tagteam lead --conversation")

    def test_hostile_reply_is_rendered_as_text_in_transcript_payload(self, tmp_path, fake_path, monkeypatch):
        """Server-side: the reply is stored verbatim; the page renders it via
        textContent (JS-source guard in test_docs_story). Here: nothing in the
        payload path strips or executes it, and it round-trips unchanged."""
        monkeypatch.setenv("FAKE_AGENT_CHAT_HTML", "1")
        p = _proj(tmp_path)
        cfg = read_config(p / "tagteam.yaml")
        cid = lc.new_conversation(p, provider="claude")["id"]
        t = lc.send(p, cid, "hi", config=cfg)
        assert "<script>" in t["reply"] and "onerror" in t["reply"]
        js = (REPO / "tagteam" / "data" / "web" / "cockpit.js").read_text(encoding="utf-8")
        lead_block = js[js.index("Phase 37: Lead panel"):js.index("Live connection: SSE with polling fallback")]
        assert "innerHTML" not in lead_block.replace("innerHTML = ''", "").replace("innerHTML=''", ""), \
            "the Lead panel must build DOM with createElement/textContent only"

    def test_watch_session_and_launch_endpoints(self, tmp_path, fake_path, monkeypatch):
        p = _proj(tmp_path)
        started = []
        # Phase 58: the server passes its WatcherOwner so shutdown can stop what it started
        monkeypatch.setattr(L, "start_watcher", lambda root, mode="headless", wait_s=5.0, owner=None, source="watch-start": (started.append((mode, isinstance(owner, L.WatcherOwner), source)), {"ok": True, "pid": 4242, "mode": mode, "message": "fake"})[1])
        monkeypatch.setattr(L, "start_session", lambda root, backend=None: {"ok": True, "backend": "manual", "result": "manual", "message": "cmds"})
        with _cockpit(p) as s:
            c = s.client
            r = c.post("/api/watch/start", {"mode": "headless", "dry_run": True}, headers=s.auth())
            assert r["json"]["cli"] == "tagteam watch --mode headless --pidfile"
            r = c.post("/api/watch/start", {"mode": "headless"}, headers=s.auth())
            assert r["status"] == 200 and started == [("headless", True, "watch-start")]
            r = c.post("/api/session/start", {}, headers=s.auth())
            assert r["status"] == 200 and r["json"]["result"] == "manual"
            it = c.get("/api/start")["json"]["intent"]
            r = c.post("/api/start/launch", {"intent": it, "dry_run": True}, headers=s.auth())
            assert "tagteam lead" in r["json"]["cli"] and "watch --mode headless" in r["json"]["cli"]
            # the composite ACCEPTS the turn and returns while it runs (blocking-ish runner: 2 s of events)
            monkeypatch.setenv("FAKE_AGENT_SLEEP", "1.0")
            t0 = time.monotonic()
            r = c.post("/api/start/launch", {"intent": it}, headers=s.auth())
            assert r["status"] == 202 and r["json"]["launched"] and r["json"]["status"] == "pending"
            assert time.monotonic() - t0 < 1.5, "the POST must not wait for the agent turn"
            cid, n = r["json"]["conversation_id"], r["json"]["turn_n"]
            conv = c.get("/api/lead/" + cid)["json"]
            assert conv["turns"][0]["status"] == "running" and conv["turns"][0]["user_text"] == it["command"]
            assert conv["slot"]["held"] and conv["slot"]["kind"] == "conversation"
            # SSE emits while the turn is still running (before the slot is released)
            rd = SSEReader(s.port, path=f"/api/lead/{cid}/events")
            rd.wait_frames(1, timeout=6)
            assert rd.frames and rd.frames[0]["event"] == "line"
            assert c.get("/api/lead/" + cid)["json"]["turns"][0]["status"] == "running"
            # a concurrent repeat while pending → the same reference, no second watcher/message
            r2 = c.post("/api/start/launch", {"intent": it}, headers=s.auth())
            assert r2["status"] == 202 and r2["json"]["launched"] is False and r2["json"]["conversation_id"] == cid
            assert started == [("headless", True, "watch-start"), ("headless", True, "launch")]   # one from /api/watch/start above, one from the launch — both owned
            # completion finalizes the turn and the launch
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not any(f.get("event") == "end" for f in rd.frames):
                rd.pump(0.3)
            rd.close()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and c.get("/api/lead/" + cid)["json"]["turns"][0]["status"] == "running":
                time.sleep(0.1)
            conv = c.get("/api/lead/" + cid)["json"]
            assert conv["turns"][0]["status"] == "ok" and conv["turns"][0]["reply"].startswith("echo:")
            assert conv["slot"]["held"] is False
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                r3 = c.post("/api/start/launch", {"intent": it}, headers=s.auth())
                if r3["status"] == 200:
                    break
                time.sleep(0.1)
            assert r3["status"] == 200 and r3["json"]["launched"] is False and r3["json"]["existing"]["conversation_id"] == cid
            assert len(c.get("/api/lead/" + cid)["json"]["turns"]) == 1 and started == [("headless", True, "watch-start"), ("headless", True, "launch")]
            # the legacy Saloon /api/launch is untouched (different route)
            assert c.post("/api/launch", {"lead": "A", "reviewer": "B", "first_prompt": ""}, headers=s.auth())["status"] == 400


# ----------------------------------------------------------- port ----

class TestPortLease:
    def test_lease_exclusive_both_orders_and_stale_recovery(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAGTEAM_PORT_LEASE_DIR", str(tmp_path / "leases"))
        a = portlease.acquire(48123, host="127.0.0.1", project="one", kind="cockpit")
        with pytest.raises(portlease.PortHeld) as ei:
            portlease.acquire(48123, host="0.0.0.0", project="two", kind="hub")
        assert "held by tagteam cockpit for one" in ei.value.reason and ei.value.tagteam
        assert a.release() is True and portlease.read_lease(48123) is None
        b = portlease.acquire(48123, host="", project="two", kind="hub")
        with pytest.raises(portlease.PortHeld):
            portlease.acquire(48123, host="127.0.0.1", project="one", kind="cockpit")
        b.release()
        # stale (dead pid) → replaced; live-but-unverifiable → held
        portlease.lease_path(48124).parent.mkdir(parents=True, exist_ok=True)
        portlease.lease_path(48124).write_text(json.dumps({"pid": 999999, "ident": "x", "project": "gone", "kind": "cockpit"}))
        c = portlease.acquire(48124, host="127.0.0.1", project="new", kind="cockpit")
        assert portlease.read_lease(48124)["project"] == "new"; c.release()
        portlease.lease_path(48124).write_text(json.dumps({"pid": os.getpid(), "project": "legacy", "kind": "cockpit"}))
        with pytest.raises(portlease.PortHeld):
            portlease.acquire(48124, host="127.0.0.1", project="new", kind="cockpit")
        # a foreign token cannot release
        rec = portlease.read_lease(48124)
        assert portlease.Lease(48124, portlease.lease_path(48124), {"token": "nope"}).release() is False
        portlease.lease_path(48124).unlink()

    def test_serve_refuses_occupied_port_and_restarts_immediately(self, tmp_path, fake_path, capsys):
        p = _proj(tmp_path)
        # unrelated listener → generic message, exit 2 (probe + bind authoritative)
        srv_sock = socket.socket(); srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv_sock.bind(("127.0.0.1", 0)); srv_sock.listen(1); port = srv_sock.getsockname()[1]
        rc = srv.serve_command(["--dir", str(p), "--port", str(port)])
        out = capsys.readouterr().out
        assert rc == 2 and f"port {port} is in use on 127.0.0.1" in out and "--port" in out
        srv_sock.close()
        # a Tagteam holder → named (lease); then immediate restart after release works
        lease = portlease.acquire(port, host="127.0.0.1", project="other-proj", kind="cockpit")
        rc = srv.serve_command(["--dir", str(p), "--port", str(port)])
        out = capsys.readouterr().out
        assert rc == 2 and "held by tagteam cockpit for other-proj" in out
        lease.release()
        # a normal run: start in a thread, stop via KeyboardInterrupt-equivalent (server.stop) after banner
        import threading as _t
        holder = {}
        orig = srv.TagteamHTTPServer.serve_forever
        def fake_forever(self, *a, **k):
            holder["srv"] = self
            raise KeyboardInterrupt
        srv.TagteamHTTPServer.serve_forever = fake_forever
        try:
            rc = srv.serve_command(["--dir", str(p), "--port", str(port)])
            out = capsys.readouterr().out
            assert rc == 0 and "Tagteam cockpit — proj —" in out and "no active cycle" in out
            assert portlease.read_lease(port) is None            # lease released on shutdown
            rc = srv.serve_command(["--dir", str(p), "--port", str(port)])   # immediate restart on the same port
            assert rc == 0
        finally:
            srv.TagteamHTTPServer.serve_forever = orig


# ------------------------------------------------------------- hub ----

class TestHubIntent:
    def test_rows_carry_intent_and_only_actionable_get_a_command(self, tmp_path, fake_path):
        from tagteam import hub_api
        p_plan = _proj(tmp_path / "plan"); _cycle(p_plan, "gamma-work", "plan", "approved")
        # Phase 69: a status of "In progress" is never offered — Delta must be plain "Not started" here
        p_impl = _proj(tmp_path / "impl", roadmap=ROADMAP.replace("- **Status:** In progress", "- **Status:** Not started"))
        _cycle(p_impl, "gamma-work", "impl", "approved")
        p_active = _proj(tmp_path / "act"); _cycle(p_active, "gamma-work", "plan", "in-progress")
        p_done = _proj(tmp_path / "done", roadmap="# R\n\n### Phase 1: Solo\n- **Status:** Complete\n")
        rows = {r["path"]: r for r in (hub_api.project_summary(x) for x in (p_plan, p_impl, p_active, p_done))}
        assert rows[str(p_plan)]["intent"]["command"] == "/handoff start gamma-work impl"
        assert rows[str(p_impl)]["intent"]["command"] == "/handoff start delta-work"
        assert rows[str(p_active)]["intent"]["command"] is None
        assert rows[str(p_done)]["intent"]["command"] is None
        js = (REPO / "tagteam" / "data" / "web" / "hub.js").read_text(encoding="utf-8")
        assert "r.intent && r.intent.command" in js and "#start" in js


# ------------------------------------------------------------- CLI ----

class TestLeadCLI:
    def test_lead_cli_send_list_and_busy(self, tmp_path, fake_path):
        p = _proj(tmp_path)
        out = io.StringIO()
        assert lc.lead_command(["hello there"], project_root=p, out=out) == 0
        assert "echo: hello there" in out.getvalue()
        out = io.StringIO()
        assert lc.lead_command(["--list"], project_root=p, out=out) == 0 and "1 turn" in out.getvalue()
        out = io.StringIO()
        assert lc.lead_command(["again", "--json"], project_root=p, out=out) == 0
        j = json.loads(out.getvalue()); assert j["ok"] and j["turn"]["continuity"] == "resumed session"
        claim = h.claim_turn_slot(p, kind="cycle", role="lead", fields={"stem": "c", "watcher_pid": os.getpid(),
                                  "watcher_ident": procs.identity(os.getpid()), "pid": None})
        try:
            out = io.StringIO()
            assert lc.lead_command(["blocked"], project_root=p, out=out) == 3 and "busy" in out.getvalue()
        finally:
            h.release_turn_slot(claim)
        assert lc.lead_command([], project_root=p, out=io.StringIO()) == 2
        assert lc.lead_command(["--conversation", "c-000000000000", "x"], project_root=p, out=io.StringIO()) == 2


class TestPortLeaseAtomicity:
    def test_publish_window_has_exactly_one_winner(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAGTEAM_PORT_LEASE_DIR", str(tmp_path / "leases"))
        orig = os.link
        barrier = threading.Barrier(2)
        def slow_link(src, dst):
            barrier.wait(timeout=5)       # both contenders have written their temp record; race the publish
            return orig(src, dst)
        monkeypatch.setattr(os, "link", slow_link)
        results = []
        def go():
            try:
                results.append(("ok", portlease.acquire(48777, host="127.0.0.1", project="x", kind="cockpit")))
            except portlease.PortHeld as e:
                results.append(("held", e))
        ts = [threading.Thread(target=go) for _ in range(2)]
        [t.start() for t in ts]; [t.join() for t in ts]
        assert sorted(r[0] for r in results) == ["held", "ok"]
        winner = [r for r in results if r[0] == "ok"][0][1]
        assert portlease.read_lease(48777)["token"] == winner.record["token"]
        held = [r for r in results if r[0] == "held"][0][1]
        assert "held by tagteam cockpit for x" in held.reason
        winner.release()

    def test_malformed_lease_fails_closed_and_is_kept(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAGTEAM_PORT_LEASE_DIR", str(tmp_path / "leases"))
        portlease.lease_path(48778).parent.mkdir(parents=True, exist_ok=True)
        portlease.lease_path(48778).write_text("{not json", encoding="utf-8")
        with pytest.raises(portlease.PortHeld) as ei:
            portlease.acquire(48778, host="127.0.0.1", project="y", kind="cockpit")
        assert "unreadable lease" in ei.value.reason and "--port 48779" in ei.value.reason
        assert portlease.lease_path(48778).read_text(encoding="utf-8") == "{not json"


class TestBodyCap:
    def test_json_body_cap_and_bad_lengths(self, tmp_path, fake_path):
        p = _proj(tmp_path)
        with _cockpit(p) as s:
            c = s.client
            cid = c.post("/api/lead/new", {}, headers=s.auth())["json"]["conversation"]["id"]
            # >64 KiB object whose `text` is small → 413 (ignored fields cannot bypass the cap)
            big = {"text": "hi", "padding": "x" * (65 * 1024)}
            r = c.post(f"/api/lead/{cid}/send", big, headers=s.auth())
            assert r["status"] == 413 and "exceeds" in r["json"]["message"]
            assert c.get(f"/api/lead/{cid}")["json"]["turns"] == []
            # legacy route under cockpit mode is capped too
            r = c.post("/api/state", {"turn": "lead", "pad": "x" * (65 * 1024)}, headers=s.auth())
            assert r["status"] == 413
            # malformed / negative Content-Length → 400, never read
            import http.client as hc
            for bad in ("abc", "-5"):
                conn = hc.HTTPConnection("127.0.0.1", s.port, timeout=5)
                conn.putrequest("POST", "/api/pause")
                for k, v in s.auth().items():
                    conn.putheader(k, v)
                conn.putheader("Content-Type", "application/json")
                conn.putheader("Content-Length", bad)
                conn.endheaders()
                resp = conn.getresponse(); body = resp.read(); conn.close()
                assert resp.status == 400, (bad, resp.status, body[:80])
            # a normal small body still works
            assert c.post(f"/api/lead/{cid}/send", {"text": "hi", "dry_run": True}, headers=s.auth())["status"] == 200


class TestAsyncBoundaries:
    """Impl round 3: dispatch failure aborts the accepted turn; launch status
    follows the persisted turn status."""

    def test_thread_start_failure_aborts_turn_composite_and_send(self, tmp_path, fake_path, monkeypatch):
        p = _proj(tmp_path)
        cfg = read_config(p / "tagteam.yaml")
        monkeypatch.setattr(L, "start_watcher", lambda root, mode="headless", wait_s=5.0: {"ok": True, "pid": os.getpid(), "mode": mode, "message": "fake"})
        it = L.launch_intent(p)
        def boom(*a, **k):
            raise RuntimeError("cannot start thread")
        monkeypatch.setattr(lc, "start_worker", boom)
        st, res = L.launch(p, intent=it, config=cfg, by="t", background=True)
        assert st == 409 and res["status"] == "failed" and "worker thread" in res["error"]
        assert h.read_inflight(p) is None                                          # slot free
        conv = lc.get_conversation(p, res.get("conversation_id") or lc.list_conversations(p)[0]["id"])
        assert conv["turns"][-1]["status"] == "failed" and "aborted before running" in conv["turns"][-1]["error"]
        conn = db.connect(project_dir=str(p)); row = db.get_launch(conn, L.launch_key(it)); conn.close()
        assert row["status"] == "failed" and "worker thread" in row["error"]
        # a subsequent retry works (worker start restored): reuses the conversation, no duplicate message
        monkeypatch.setattr(lc, "start_worker", lambda target, name: (_ for _ in ()).throw(AssertionError("sync path")))
        sends = []
        st, res = L.launch(p, intent=it, config=cfg, by="t", retry=True, send=lambda: (sends.append(1), {"n": 2, "status": "ok"})[1])
        assert st == 200 and res["status"] == "succeeded" and sends == [1]
        # ordinary Send over HTTP with Thread.start failing
        with _cockpit(p) as s:
            cid = s.client.post("/api/lead/new", {}, headers=s.auth())["json"]["conversation"]["id"]
            monkeypatch.setattr(lc, "start_worker", boom)
            r = s.client.post(f"/api/lead/{cid}/send", {"text": "hi"}, headers=s.auth())
            assert r["status"] == 503 and "aborted" in r["json"]["message"]
            import threading as _t
            monkeypatch.setattr(lc, "start_worker", lambda target, name: _t.Thread(target=target, name=name, daemon=True).start())
            assert h.read_inflight(p) is None
            conv = s.client.get(f"/api/lead/{cid}")["json"]
            assert conv["turns"][-1]["status"] == "failed" and "aborted" in conv["turns"][-1]["error"]
            r = s.client.post(f"/api/lead/{cid}/send", {"text": "again"}, headers=s.auth())   # works again
            assert r["status"] == 202
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and s.client.get(f"/api/lead/{cid}")["json"]["turns"][-1]["status"] == "running":
                time.sleep(0.1)
            assert s.client.get(f"/api/lead/{cid}")["json"]["turns"][-1]["status"] == "ok"

    @pytest.mark.parametrize("mode,expect", [("nonzero", "failed"), ("cancelled", "cancelled")])
    def test_launch_status_follows_turn_status_sync_and_background(self, tmp_path, fake_path, monkeypatch, mode, expect):
        p = _proj(tmp_path)
        cfg = read_config(p / "tagteam.yaml")
        monkeypatch.setattr(L, "start_watcher", lambda root, mode="headless", wait_s=5.0: {"ok": True, "pid": os.getpid(), "mode": mode, "message": "fake"})
        it = L.launch_intent(p)
        if mode == "nonzero":
            monkeypatch.setenv("FAKE_AGENT_MODE", "nonzero")
            runner = None
        else:
            # a runner that "gets cancelled": writes the cancel marker for the stem then exits nonzero
            def runner(argv, prompt, cwd, **kw):
                m = h.read_inflight(cwd)
                h.write_cancel(cwd, {"stem": m["stem"], "pid": 1, "by": "test"})
                kw["events_path"].write_text("")
                return h.RunOutput(exit_code=137, timed_out=False, duration_ms=1)
        # synchronous path: the injected sender runs a real (fake-agent) turn in the launch's conversation
        def sync_send(cid):
            return lc.send(p, cid, it["command"], config=cfg, run=runner)
        st, res = L.launch(p, intent=it, config=cfg, by="t", send=sync_send)
        assert st == 409 and res["status"] == "failed" and f"lead turn {expect}" in res["error"]
        assert res["partial"]["turn"]["status"] == expect
        conn = db.connect(project_dir=str(p)); row = db.get_launch(conn, L.launch_key(it)); conn.close()
        assert row["status"] == "failed"
        # retry with the existing failed turn: not re-sent, not relabelled a success
        st, res = L.launch(p, intent=it, config=cfg, by="t", retry=True, send=lambda: (_ for _ in ()).throw(AssertionError("must not re-send")))
        assert st == 409 and res["status"] == "failed" and "will not re-send" in res["error"]
        assert res["partial"]["turn"]["status"] == expect
        # background path on a fresh project
        p2 = _proj(tmp_path / "bg"); it2 = L.launch_intent(p2)
        if runner is not None:
            monkeypatch.setattr(h, "run_process", runner)    # the background path uses the engine runner
        st, res = L.launch(p2, intent=it2, config=cfg, by="t", background=True)
        assert st == 202 and res["status"] == "pending"
        cid2 = res["conversation_id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            conn = db.connect(project_dir=str(p2)); row = db.get_launch(conn, L.launch_key(it2)); conn.close()
            if row["status"] != "pending":
                break
            time.sleep(0.1)
        assert row["status"] == "failed" and f"lead turn {expect}" in row["error"]
        turn = lc.get_conversation(p2, cid2)["turns"][-1]
        assert turn["status"] == expect
        assert json.loads(row["partial_json"])["turn"]["status"] == expect
        assert h.read_inflight(p2) is None

    def test_launch_status_ok_and_running_paths(self, tmp_path, fake_path, monkeypatch):
        p = _proj(tmp_path)
        cfg = read_config(p / "tagteam.yaml")
        monkeypatch.setattr(L, "start_watcher", lambda root, mode="headless", wait_s=5.0: {"ok": True, "pid": os.getpid(), "mode": mode, "message": "fake"})
        it = L.launch_intent(p)
        # a persisted running turn (server alive) → repeat is pending, not succeeded
        cid = lc.new_conversation(p, provider="claude")["id"]
        conn = db.connect(project_dir=str(p))
        db.add_conversation_turn(conn, conversation_id=cid, ts="t", user_text=it["command"], owner_pid=os.getpid(), owner_ident=procs.identity(os.getpid()))
        db.claim_launch(conn, key=L.launch_key(it), ts="t", intent_json=json.dumps(it), owner_pid=os.getpid(), owner_ident=procs.identity(os.getpid()))
        db.update_launch(conn, L.launch_key(it), ts="t", conversation_id=cid, turn_n=1)
        conn.close()
        st, res = L.launch(p, intent=it, config=cfg, by="t")
        assert st == 202 and res["status"] == "pending"
        # once that turn is ok, the launch finalizes succeeded on the next call, without re-sending
        conn = db.connect(project_dir=str(p)); db.finish_conversation_turn(conn, cid, 1, status="ok", ts="t", reply="r")
        db.update_launch(conn, L.launch_key(it), ts="t", status="failed"); conn.close()   # simulate the worker's claim having failed elsewhere
        st, res = L.launch(p, intent=it, config=cfg, by="t", retry=True, send=lambda: (_ for _ in ()).throw(AssertionError("no re-send")))
        assert st == 200 and res["status"] == "succeeded" and res["existing"] == {"conversation_id": cid, "turn_n": 1}


class TestLaunchExceptionBoundary:
    """Impl round 4: an unexpected exception anywhere after the claim
    finalizes the launch as failed (never a pending row under a live PID)."""

    def _prep(self, tmp_path, monkeypatch):
        p = _proj(tmp_path)
        cfg = read_config(p / "tagteam.yaml")
        monkeypatch.setattr(L, "start_watcher", lambda root, mode="headless", wait_s=5.0: {"ok": True, "pid": os.getpid(), "mode": mode, "message": "fake"})
        return p, cfg, L.launch_intent(p)

    def _row(self, p, it):
        conn = db.connect(project_dir=str(p)); row = db.get_launch(conn, L.launch_key(it)); conn.close(); return row

    def test_start_turn_exception(self, tmp_path, fake_path, monkeypatch):
        p, cfg, it = self._prep(tmp_path, monkeypatch)
        monkeypatch.setattr(lc, "start_turn", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("setup exploded")))
        st, res = L.launch(p, intent=it, config=cfg, by="t", background=True)
        assert st == 409 and res["status"] == "failed" and "setup exploded" in res["error"]
        row = self._row(p, it)
        assert row["status"] == "failed" and "setup exploded" in row["error"]
        assert h.read_inflight(p) is None
        assert all(t["status"] != "running" for c in lc.list_conversations(p) for t in lc.get_conversation(p, c["id"])["turns"])
        # a repeat is not stuck at 202; retry proceeds without duplication
        st, res = L.launch(p, intent=it, config=cfg, by="t")
        assert st == 409 and res["status"] == "failed"
        monkeypatch.undo()
        monkeypatch.setattr(L, "start_watcher", lambda root, mode="headless", wait_s=5.0: {"ok": True, "pid": os.getpid(), "mode": mode, "message": "fake"})
        sends = []
        st, res = L.launch(p, intent=it, config=cfg, by="t", retry=True, send=lambda: (sends.append(1), {"n": 1, "status": "ok"})[1])
        assert st == 200 and res["status"] == "succeeded" and sends == [1]

    def test_start_watcher_exception(self, tmp_path, fake_path, monkeypatch):
        p, cfg, it = self._prep(tmp_path, monkeypatch)
        monkeypatch.setattr(L, "start_watcher", lambda *a, **k: (_ for _ in ()).throw(OSError("fork failed")))
        st, res = L.launch(p, intent=it, config=cfg, by="t", background=True)
        assert st == 409 and res["status"] == "failed" and "fork failed" in res["error"]
        assert self._row(p, it)["status"] == "failed"
        assert h.read_inflight(p) is None and lc.list_conversations(p) == []
        monkeypatch.setattr(L, "start_watcher", lambda root, mode="headless", wait_s=5.0: {"ok": True, "pid": os.getpid(), "mode": mode, "message": "fake"})
        sends = []
        st, res = L.launch(p, intent=it, config=cfg, by="t", retry=True, send=lambda: (sends.append(1), {"n": 1, "status": "ok"})[1])
        assert st == 200 and sends == [1]

    def test_dispatch_preparation_exception_aborts_started_turn(self, tmp_path, fake_path, monkeypatch):
        """start_turn succeeded, then persisting turn_n raised: the started
        turn is aborted (owner-safe) and the launch fails."""
        p, cfg, it = self._prep(tmp_path, monkeypatch)
        real_update = db.update_launch
        calls = {"n": 0}
        def flaky_update(conn, key, *, ts, **fields):
            if "turn_n" in fields and "conversation_id" not in fields:
                raise RuntimeError("db hiccup")
            return real_update(conn, key, ts=ts, **fields)
        monkeypatch.setattr(db, "update_launch", flaky_update)
        st, res = L.launch(p, intent=it, config=cfg, by="t", background=True)
        assert st == 409 and res["status"] == "failed" and "db hiccup" in res["error"]
        assert h.read_inflight(p) is None
        conv = lc.get_conversation(p, lc.list_conversations(p)[0]["id"])
        assert conv["turns"][-1]["status"] == "failed" and "aborted before running" in conv["turns"][-1]["error"]
        assert self._row(p, it)["status"] == "failed"
