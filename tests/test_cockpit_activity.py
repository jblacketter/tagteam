"""Phase 43 tests: the cockpit's activity read model (`activity_payload`,
`normalize_outcome`, `last_turn`, `launch_view`), the `now_payload` keys
(`turn_kind` / `launch` / `last_turn`), the SSE signature's new sources
(conversation turns, launches, in-flight log growth), `tail_payload(stem=)`,
the `/api/activity` + turn-log SSE endpoints, and the source guards on the
shipped cockpit (no innerHTML in the Cycle/Activity block, the ids the page
needs, the tail drawer gone, exactly seven outcomes)."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tagteam import cockpit_api as capi
from tagteam import db, headless as h, launch as L, lead_chat as lc, procs
from tagteam import state as state_mod

from tests.test_headless import project, fake_path, _init_cycle  # noqa: F401
from tests.test_lead_chat import fake_path as chat_fake_path  # noqa: F401  (chat-mode fake agents)
from tests.test_server_cockpit import Served, SSEReader

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "tagteam" / "data" / "web"


def _iso(delta_s: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


def _conn(project: Path):
    return db.connect(project_dir=str(project))


_SEQ = {"n": 0}


def _seed_usage(project: Path, **over) -> int:
    _SEQ["n"] += 1
    fields = dict(ts=_iso(-60), phase="feat-x", type="plan", round=1, role="lead", agent="Claude",
                  provider="claude", status="ok", duration_ms=1500,
                  log_path=str(h.turns_dir(project) / f"feat-x_plan_r1_lead_x{_SEQ['n']}.log"))
    fields.update(over)
    conn = _conn(project)
    try:
        return db.add_usage(conn, **fields)
    finally:
        conn.close()


def _seed_conversation_turn(project: Path, *, status="ok", error=None, text="hello",
                            ts=None, finished=None) -> tuple[str, int]:
    cid = lc.new_conversation(project, provider="claude")["id"]
    conn = _conn(project)
    try:
        t = db.add_conversation_turn(conn, conversation_id=cid, ts=ts or _iso(-30), user_text=text,
                                     owner_pid=os.getpid(), owner_ident=procs.identity(os.getpid()),
                                     log_path=str(project / ".tagteam" / "conversations" / cid / "1.log"))
        if status != "running":
            db.finish_conversation_turn(conn, cid, t["n"], status=status, ts=finished or _iso(-20), error=error)
    finally:
        conn.close()
    return cid, t["n"]


def _seed_gate(project: Path, *, status="pass", stem="feat-x_plan_r1_gate_x") -> int:
    conn = _conn(project)
    try:
        claimed = db.claim_gate(conn, ts=_iso(-50), phase="feat-x", cycle_type="impl", round_=1,
                                submission_seq=1, event_key="k1", kind="auto",
                                runner_pid=os.getpid(), runner_ident=procs.identity(os.getpid()))
        gid = claimed[0]
        if status != "running":
            db.finish_gate(conn, gid, status=status, ts=_iso(-40), duration_s=9.5, stem=stem, reason="tests ok")
        else:
            db.update_gate(conn, gid, ts=_iso(-49), stem=stem)
        return gid
    finally:
        conn.close()


def _seed_panel(project: Path, *, status="merged", stem="feat-x_plan_r1_panel_x") -> int:
    conn = _conn(project)
    try:
        claimed = db.claim_panel(conn, ts=_iso(-45), phase="feat-x", cycle_type="plan", round_=1,
                                 submission_seq=1, event_key="p1", kind="auto",
                                 runner_pid=os.getpid(), runner_ident=procs.identity(os.getpid()))
        pid = claimed[0]
        if status != "running":
            db.finish_panel(conn, pid, status=status, ts=_iso(-35), duration_s=20.0, stem=stem,
                            decision="APPROVE")
        return pid
    finally:
        conn.close()


def _seed_launch(project: Path, *, status="pending", intent=None, cid=None, turn_n=None,
                 created=None, finished=None, error=None, key=None) -> str:
    intent = intent or {"phase": "gamma-work", "type": "plan", "command": "/handoff start gamma-work",
                        "observed": {"seq": None, "phase": None, "type": None, "round": None, "state": None}}
    key = key or L.launch_key(intent)
    conn = _conn(project)
    try:
        db.claim_launch(conn, key=key, ts=created or _iso(-10), intent_json=json.dumps(intent),
                        owner_pid=os.getpid(), owner_ident=procs.identity(os.getpid()))
        fields = {"status": status}
        if cid is not None:
            fields.update(conversation_id=cid, turn_n=turn_n)
        if finished:
            fields["finished_at"] = finished
        if error:
            fields["error"] = error
        db.update_launch(conn, key, ts=_iso(-9), **fields)
    finally:
        conn.close()
    return key


def _marker(project: Path, **over) -> dict:
    m = {"kind": "cycle", "role": "reviewer", "agent": "Codex", "provider": "codex",
         "phase": "feat-x", "type": "plan", "round": 2, "stem": "feat-x_plan_r2_reviewer_now",
         "log_path": str(h.turns_dir(project) / "feat-x_plan_r2_reviewer_now.log"),
         "events_path": str(h.turns_dir(project) / "feat-x_plan_r2_reviewer_now.events.jsonl"),
         "started_at": _iso(-5), "pid": os.getpid(), "watcher_pid": os.getpid(), "owner_token": "t"}
    m.update(over)
    h.turns_dir(project).mkdir(parents=True, exist_ok=True)
    h.inflight_path(project).write_text(json.dumps(m))
    return m


# ---------------------------------------------------------------------------
# outcome vocabulary
# ---------------------------------------------------------------------------

class TestOutcomes:
    def test_vocabulary_is_exactly_seven(self):
        assert capi.OUTCOMES == ("running", "finished", "cancelled", "failed", "timed_out",
                                 "process_gone", "orphaned")

    @pytest.mark.parametrize("raw,expect", [
        ("ok", "finished"), ("running", "running"), ("cancelled", "cancelled"),
        ("timeout", "timed_out"), ("nonzero_exit", "failed"), ("no_round", "failed"),
        ("spawn_failed", "failed"), ("failed", "failed"), ("error", "failed"),
        ("pass", "finished"), ("bounce", "finished"), ("merged", "finished"), ("fallback", "finished"),
        ("superseded", "finished"), ("abandoned", "orphaned"), ("pending", "running"),
        ("succeeded", "finished"), ("partial", "finished"), ("something-new", "failed"), (None, "failed"),
    ])
    def test_normalize_table(self, raw, expect):
        assert capi.normalize_outcome(raw) == expect

    def test_normalize_liveness_and_orphan_error(self):
        assert capi.normalize_outcome("running", pid_alive=False) == "process_gone"
        assert capi.normalize_outcome("running", pid_alive=True) == "running"
        assert capi.normalize_outcome("failed", error="orphaned at t (owner process gone)") == "orphaned"
        assert capi.normalize_outcome("failed", error="aborted before running: x") == "failed"


# ---------------------------------------------------------------------------
# activity_payload
# ---------------------------------------------------------------------------

class TestActivity:
    def test_empty_project(self, project):
        a = capi.activity_payload(project)
        assert a == {"items": [], "truncated": False, "limit": capi.ACTIVITY_DEFAULT_LIMIT}

    def test_every_source_is_represented_and_normalised(self, project):
        _seed_usage(project, log_path=str(h.turns_dir(project) / "feat-x_plan_r1_lead_x.log"))   # cycle turn, finished
        _seed_usage(project, ts=_iso(-55), role="reviewer", agent="Codex", status="timeout",
                    log_path=str(h.turns_dir(project) / "feat-x_plan_r1_reviewer_y.log"))
        _seed_usage(project, ts=_iso(-54), role="reviewer", agent="Codex", status="ok", kind="panel:scope",
                    log_path=str(h.turns_dir(project) / "feat-x_plan_r1_reviewer_lens.log"))
        _seed_usage(project, ts=_iso(-53), role="briefer", agent="briefer", status="ok", kind="briefer")
        _seed_usage(project, ts=_iso(-52), role="lead", status="ok", kind="conversation")   # skipped (conversation_turns is the source)
        cid, n = _seed_conversation_turn(project, status="ok", text="brainstorm the plan\nmore")
        _seed_conversation_turn(project, status="failed", error="orphaned at t (owner process gone)", ts=_iso(-25))
        _seed_gate(project, status="bounce")
        _seed_panel(project, status="merged")
        _seed_launch(project, status="failed", error="lead turn failed: boom", finished=_iso(-8))
        a = capi.activity_payload(project)
        items = a["items"]
        by_kind = {}
        for it in items:
            by_kind.setdefault(it["kind"], []).append(it)
        assert set(by_kind) == {"cycle", "panel_lens", "briefer", "conversation", "gate", "panel", "launch"}
        assert not any(it["source"] == "usage" and it["raw_status"] == "ok" and it["kind"] == "conversation" for it in items)
        cyc = sorted(by_kind["cycle"], key=lambda i: i["role"])
        assert [c["status"] for c in cyc] == ["finished", "timed_out"]
        assert cyc[0]["ref"] == {"log": "feat-x_plan_r1_lead_x"} and cyc[0]["stem"] == "feat-x_plan_r1_lead_x"
        assert cyc[0]["id"] == "turn:feat-x_plan_r1_lead_x" and by_kind["gate"][0]["id"] == "turn:feat-x_plan_r1_gate_x"
        assert cyc[0]["duration_ms"] == 1500 and cyc[0]["started_at"] < cyc[0]["ended_at"]
        assert by_kind["panel_lens"][0]["detail"] == "scope"
        assert by_kind["briefer"][0]["role"] == "briefer"
        convs = sorted(by_kind["conversation"], key=lambda i: i["status"])
        assert [c["status"] for c in convs] == ["finished", "orphaned"]
        ok = [c for c in convs if c["status"] == "finished"][0]
        assert ok["ref"] == {"conversation": cid, "turn": n} and ok["detail"] == "brainstorm the plan"
        assert by_kind["gate"][0]["status"] == "finished" and by_kind["gate"][0]["raw_status"] == "bounce"
        assert by_kind["gate"][0]["detail"].startswith("bounce") and by_kind["gate"][0]["duration_ms"] == 9500
        assert by_kind["panel"][0]["status"] == "finished" and "APPROVE" in by_kind["panel"][0]["detail"]
        assert by_kind["launch"][0]["status"] == "failed" and "/handoff start gamma-work" in by_kind["launch"][0]["detail"]
        # every item has the documented shape and a vocabulary status
        for it in items:
            assert set(it) >= {"id", "source", "kind", "role", "agent", "phase", "type", "round", "status",
                               "raw_status", "started_at", "ended_at", "duration_ms", "log_path", "stem",
                               "detail", "ref", "pid_alive", "age_s"}
            assert it["status"] in capi.OUTCOMES
        # newest first
        starts = [it["started_at"] or "" for it in items]
        assert starts == sorted(starts, reverse=True)
        assert len({it["id"] for it in items}) == len(items)

    def test_succeeded_launches_are_not_activity(self, project):
        _seed_launch(project, status="succeeded", finished=_iso(-1))
        assert capi.activity_payload(project)["items"] == []

    def test_a_launch_that_reached_its_turn_is_that_conversation_row(self, project):
        cid, n = _seed_conversation_turn(project, status="running", text="/handoff start gamma-work")
        _seed_launch(project, status="pending", cid=cid, turn_n=n)
        items = capi.activity_payload(project)["items"]
        assert [i["kind"] for i in items] == ["conversation"]
        # a launch that never got a turn stays visible (it is the only record of the attempt)
        _seed_launch(project, status="failed", error="orphaned: the launching process died", finished=_iso(-1),
                     key="other-key")
        kinds = sorted(i["kind"] for i in capi.activity_payload(project)["items"])
        assert kinds == ["conversation", "launch"]

    def test_inflight_marker_becomes_running_row_or_marks_the_matching_row(self, project):
        _seed_usage(project)
        _marker(project)                                        # a reviewer cycle turn, live pid
        a = capi.activity_payload(project)
        top = a["items"][0]
        assert top["source"] == "inflight" and top["status"] == "running" and top["kind"] == "cycle"
        assert top["role"] == "reviewer" and top["agent"] == "Codex" and top["round"] == 2
        assert top["ref"] == {"log": "feat-x_plan_r2_reviewer_now"} and top["pid_alive"] is True
        assert top["id"] == "turn:feat-x_plan_r2_reviewer_now"
        # dead pid → process_gone (never "running" by inference)
        _marker(project, pid=999999)
        assert capi.activity_payload(project)["items"][0]["status"] == "process_gone"
        # claimed-not-spawned (pid None) is running, not gone
        _marker(project, pid=None)
        assert capi.activity_payload(project)["items"][0]["status"] == "running"
        # a running gate row with the same stem is marked, not duplicated
        h.inflight_path(project).unlink()
        _seed_gate(project, status="running", stem="gate-stem-1")
        _marker(project, kind="gate", role="gatekeeper", agent="gate", stem="gate-stem-1", round=1)
        items = capi.activity_payload(project)["items"]
        gates = [i for i in items if i["kind"] == "gate"]
        assert len(gates) == 1 and gates[0]["status"] == "running" and gates[0]["source"] == "gate"
        assert not any(i["source"] == "inflight" for i in items)
        # a running conversation turn is marked through its conversation ref
        h.inflight_path(project).unlink()
        cid, n = _seed_conversation_turn(project, status="running")
        _marker(project, kind="conversation", role="lead", agent="Claude", stem="conv-stem",
                conversation_id=cid, turn_n=n, round=None)
        items = capi.activity_payload(project)["items"]
        convs = [i for i in items if i["kind"] == "conversation"]
        assert len(convs) == 1 and convs[0]["status"] == "running" and convs[0]["source"] == "conversation"
        assert convs[0]["pid_alive"] is True and items[0] is convs[0]     # running rows sort first
        # once the engine records a stem-bearing turn, the SAME id carries the outcome (one row, patched)
        h.inflight_path(project).unlink()
        _marker(project)
        assert capi.activity_payload(project)["items"][0]["id"] == "turn:feat-x_plan_r2_reviewer_now"
        _seed_usage(project, ts=_iso(), role="reviewer", agent="Codex", round=2, status="ok",
                    log_path=str(h.turns_dir(project) / "feat-x_plan_r2_reviewer_now.log"))
        same = [i for i in capi.activity_payload(project)["items"] if i["id"] == "turn:feat-x_plan_r2_reviewer_now"]
        assert len(same) == 1 and same[0]["status"] == "finished" and same[0]["source"] == "usage"   # a lingering marker: the record wins
        h.inflight_path(project).unlink()
        same = [i for i in capi.activity_payload(project)["items"] if i["id"] == "turn:feat-x_plan_r2_reviewer_now"]
        assert len(same) == 1 and same[0]["status"] == "finished"

    def test_cap_and_truncated(self, project):
        for i in range(12):
            _seed_usage(project, ts=_iso(-100 + i))
        a = capi.activity_payload(project, limit=5)
        assert len(a["items"]) == 5 and a["truncated"] is True and a["limit"] == 5
        assert capi.activity_payload(project, limit=10 ** 9)["limit"] == capi.ACTIVITY_MAX_LIMIT
        assert capi.activity_payload(project, limit="x")["limit"] == capi.ACTIVITY_DEFAULT_LIMIT

    def test_last_turn_skips_running_and_launches(self, project):
        assert capi.last_turn([]) is None
        _seed_launch(project, status="failed", error="x", finished=_iso(-1))
        _seed_usage(project, ts=_iso(-3), status="cancelled")
        _seed_usage(project, ts=_iso(-30), status="ok")
        _marker(project)
        items = capi.activity_payload(project)["items"]
        lt = capi.last_turn(items)
        assert lt["kind"] == "cycle" and lt["status"] == "cancelled"


# ---------------------------------------------------------------------------
# now_payload keys + launch_view
# ---------------------------------------------------------------------------

class TestNowKeys:
    def test_now_carries_turn_kind_launch_last_turn(self, project):
        n = capi.now_payload(project)
        assert n["turn_kind"] is None and n["launch"] is None and n["last_turn"] is None
        _seed_usage(project, status="ok")
        _marker(project, kind="conversation", role="lead", agent="Claude")
        n = capi.now_payload(project)
        assert n["turn_kind"] == "conversation"
        assert n["last_turn"]["kind"] == "cycle" and n["last_turn"]["status"] == "finished"
        # a legacy marker without `kind` is a cycle turn
        _marker(project, kind=None)
        m = h.read_inflight(project); m.pop("kind"); h.inflight_path(project).write_text(json.dumps(m))
        assert capi.now_payload(project)["turn_kind"] == "cycle"

    def test_launch_view_pending_follows_the_turn(self, project):
        (project / "docs").mkdir(exist_ok=True)
        (project / "docs" / "roadmap.md").write_text("# R\n### Phase 1: Gamma Work\n- **Status:** Not started\n")
        it = L.launch_intent(project)
        assert it["command"] == "/handoff start gamma-work"
        cid, n = _seed_conversation_turn(project, status="running", text=it["command"])
        _seed_launch(project, status="pending", intent=it, cid=cid, turn_n=n)
        lv = capi.launch_view(project)
        assert lv["status"] == "pending" and lv["command"] == it["command"] and lv["conversation_id"] == cid
        assert lv["phase"] == "gamma-work" and lv["age_s"] is not None
        # the turn fails → the launch is failed (with the turn's error), for the current intent
        conn = _conn(project)
        try:
            db.finish_conversation_turn(conn, cid, n, status="failed", ts=_iso(), error="boom")
        finally:
            conn.close()
        lv = capi.launch_view(project)
        assert lv["status"] == "failed" and "lead turn failed: boom" in lv["error"]
        # the turn succeeds → nothing to acknowledge
        conn = _conn(project)
        try:
            conn.execute("UPDATE conversation_turns SET status='ok', error=NULL"); conn.commit()
        finally:
            conn.close()
        assert capi.launch_view(project) is None

    def test_launch_view_failed_only_recent_and_for_current_intent(self, project):
        (project / "docs").mkdir(exist_ok=True)
        (project / "docs" / "roadmap.md").write_text("# R\n### Phase 1: Gamma Work\n- **Status:** Not started\n")
        it = L.launch_intent(project)
        _seed_launch(project, status="failed", intent=it, error="lead turn cancelled", finished=_iso(-100))
        assert capi.launch_view(project)["status"] == "failed"
        # stale (>24h) → hidden
        conn = _conn(project)
        try:
            conn.execute("UPDATE launches SET finished_at=?, updated_at=?, created_at=?",
                         (_iso(-90000), _iso(-90000), _iso(-90000))); conn.commit()
        finally:
            conn.close()
        assert capi.launch_view(project) is None
        # a failed launch for a DIFFERENT intent (state moved on) → hidden
        other = dict(it, command="/handoff start other", observed=dict(it["observed"], seq=99))
        _seed_launch(project, status="failed", intent=other, error="x", finished=_iso(-5))
        assert capi.launch_view(project) is None

    def test_launch_view_orphaned_pending_row(self, project):
        (project / "docs").mkdir(exist_ok=True)
        (project / "docs" / "roadmap.md").write_text("# R\n### Phase 1: Gamma Work\n- **Status:** Not started\n")
        it = L.launch_intent(project)
        key = _seed_launch(project, status="pending", intent=it)
        conn = _conn(project)
        try:
            db.update_launch(conn, key, ts=_iso(), owner_pid=999999, owner_ident="dead:ident")
        finally:
            conn.close()
        lv = capi.launch_view(project)
        assert lv["status"] == "failed" and "orphaned" in lv["error"]


# ---------------------------------------------------------------------------
# events_signature: new sources
# ---------------------------------------------------------------------------

class TestSignature:
    def test_conversation_turn_and_launch_and_log_growth_change_the_signature(self, project):
        _init_cycle(project)
        i0 = capi.signature_id(capi.events_signature(project))
        cid, n = _seed_conversation_turn(project, status="running")
        s1 = capi.events_signature(project); i1 = capi.signature_id(s1)
        assert i1 != i0 and s1["conversation_turns"][1] == 1
        conn = _conn(project)
        try:
            db.finish_conversation_turn(conn, cid, n, status="ok", ts=_iso())
        finally:
            conn.close()
        i2 = capi.signature_id(capi.events_signature(project)); assert i2 != i1
        key = _seed_launch(project, status="pending")
        s3 = capi.events_signature(project); i3 = capi.signature_id(s3)
        assert i3 != i2 and s3["launches"][1] == 1
        conn = _conn(project)
        try:
            db.update_launch(conn, key, ts=_iso(), status="failed", finished_at=_iso(), error="x")
        finally:
            conn.close()
        i4 = capi.signature_id(capi.events_signature(project)); assert i4 != i3
        # unchanged → same id
        assert capi.signature_id(capi.events_signature(project)) == i4
        # in-flight log growth: coarse steps (LOG_SIGNAL_STEP), not per line
        m = _marker(project)
        log = Path(m["log_path"]); log.write_text("a\n")
        s5 = capi.events_signature(project); i5 = capi.signature_id(s5)
        assert i5 != i4 and s5["inflight"]["kind"] == "cycle" and s5["inflight"]["log_step"] == 0
        log.write_text("a\n" * 100)                            # < one step
        assert capi.signature_id(capi.events_signature(project)) == i5
        log.write_text("x" * (capi.LOG_SIGNAL_STEP + 10) + "\n")
        s6 = capi.events_signature(project); i6 = capi.signature_id(s6)
        assert i6 != i5 and s6["inflight"]["log_step"] == 1

    def test_signature_cost_stays_bounded(self, project):
        _init_cycle(project)
        for i in range(200):
            _seed_usage(project, ts=_iso(-1000 + i))
        for _ in range(20):
            _seed_conversation_turn(project, status="ok")
        t0 = time.perf_counter()
        for _ in range(20):
            capi.events_signature(project)
        per = (time.perf_counter() - t0) / 20
        assert per < 0.25, f"events_signature took {per:.3f}s"


# ---------------------------------------------------------------------------
# tail_payload(stem=) + turn_log_path
# ---------------------------------------------------------------------------

class TestTailStem:
    def test_stem_resolves_only_under_turns_dir(self, project):
        d = h.turns_dir(project); d.mkdir(parents=True)
        (d / "s1.log").write_text("\n".join(f"l{i}" for i in range(10)))
        (project / "secret.log").write_text("nope")
        assert capi.turn_log_path(project, "s1") == (d / "s1.log").resolve()
        for bad in ("../secret", "..", "a/b", "", None, "s1.log/../../secret"):
            assert capi.turn_log_path(project, bad) is None, bad
        p = capi.tail_payload(project, 3, stem="s1")
        assert p["lines"] == ["l7", "l8", "l9"] and p["stem"] == "s1" and p["inflight"] is False
        assert p["path"] == str((d / "s1.log").resolve())
        p = capi.tail_payload(project, 3, stem="missing")
        assert p["path"] is None and p["lines"] == [] and "no turn log" in p["message"]
        p = capi.tail_payload(project, 3, stem="../secret")
        assert p["path"] is None and p["lines"] == []
        # inflight flag reflects THIS stem
        _marker(project, stem="s1", log_path=str(d / "s1.log"))
        assert capi.tail_payload(project, 3, stem="s1")["inflight"] is True
        assert capi.tail_payload(project, 3, stem="other")["inflight"] is False


# ---------------------------------------------------------------------------
# server: /api/activity, the turn-log SSE, /api/tail?stem=
# ---------------------------------------------------------------------------

class TestServer:
    def test_activity_endpoint_shape_and_legacy_404(self, project):
        _seed_usage(project)
        with Served(project, mode="cockpit") as s:
            r = s.client.get("/api/activity")
            assert r["status"] == 200 and r["json"]["items"][0]["kind"] == "cycle"
            assert r["json"]["truncated"] is False
            r = s.client.get("/api/activity?limit=1")
            assert r["json"]["limit"] == 1
            n = s.client.get("/api/now")["json"]
            assert "turn_kind" in n and "launch" in n and "last_turn" in n
        with Served(project, mode="legacy") as s:
            assert s.client.get("/api/activity").get("status") == 404
            assert s.client.get("/api/activity/log/x/events").get("status") == 404

    def test_tail_stem_query(self, project):
        d = h.turns_dir(project); d.mkdir(parents=True)
        (d / "s1.log").write_text("a\nb\nc\n")
        with Served(project, mode="cockpit") as s:
            r = s.client.get("/api/tail?stem=s1&lines=2")
            assert r["json"]["lines"] == ["b", "c"] and r["json"]["stem"] == "s1"
            r = s.client.get("/api/tail?stem=..%2Fs1&lines=2")
            assert r["json"]["path"] is None

    def test_log_sse_streams_replays_and_ends(self, project):
        d = h.turns_dir(project); d.mkdir(parents=True)
        log = d / "run1.log"
        log.write_text("first\nsecond\n")
        m = _marker(project, stem="run1", log_path=str(log))
        with Served(project, mode="cockpit") as s:
            rd = SSEReader(s.port, path="/api/activity/log/run1/events")
            rd.wait_frames(2, timeout=5)
            assert rd.status == 200
            assert [f["data"]["text"] for f in rd.frames[:2]] == ["first", "second"]
            off2 = int(rd.frames[1]["id"])
            assert off2 == len("first\nsecond\n")
            # live follow: an appended line arrives with the next offset as its id
            with open(log, "a") as f:
                f.write("third\n")
            rd.wait_frames(3, timeout=5)
            assert rd.frames[2]["data"]["text"] == "third" and int(rd.frames[2]["id"]) == off2 + len("third\n")
            # a partial line is held back while the writer lives …
            with open(log, "a") as f:
                f.write("part")
            rd.pump(1.0)
            assert len(rd.frames) == 3
            # … and flushed as the final line once the marker is gone, then `end`
            h.inflight_path(project).unlink()
            rd.wait_frames(5, timeout=5)
            assert rd.frames[3]["data"]["text"] == "part" and rd.frames[3]["event"] == "line"
            assert rd.frames[4]["event"] == "end" and rd.frames[4]["data"]["stem"] == "run1"
            assert rd.frames[4]["id"].endswith(":end")
            rd.close()
            # replay from a byte offset (Last-Event-ID) — only what follows it, then end
            rd2 = SSEReader(s.port, headers={"Last-Event-ID": str(off2)}, path="/api/activity/log/run1/events")
            rd2.wait_frames(3, timeout=5)
            texts = [f["data"].get("text") for f in rd2.frames if f["event"] == "line"]
            assert texts == ["third", "part"] and rd2.frames[-1]["event"] == "end"
            rd2.close()
            # ?after= with the `:end` id form replays nothing new
            rd3 = SSEReader(s.port, path=f"/api/activity/log/run1/events?after={rd.frames[4]['id']}")
            rd3.wait_frames(1, timeout=5)
            assert rd3.frames[0]["event"] == "end"
            rd3.close()
            # a finished stem streams its whole log then ends (no marker at all)
            (d / "done.log").write_text("x\ny\n")
            rd4 = SSEReader(s.port, path="/api/activity/log/done/events")
            rd4.wait_frames(3, timeout=5)
            assert [f["event"] for f in rd4.frames] == ["line", "line", "end"]
            rd4.close()

    def test_log_sse_validation_and_cap(self, project):
        d = h.turns_dir(project); d.mkdir(parents=True)
        (d / "ok.log").write_text("x\n")
        with Served(project, mode="cockpit", max_sse=1) as s:
            assert s.client.get("/api/activity/log/..%2Fok/events")["status"] == 400
            assert s.client.get("/api/activity/log/a%2Fb/events")["status"] == 400
            assert s.client.get("/api/activity/log/nope/events")["status"] == 404
            rd = SSEReader(s.port, path="/api/activity/log/ok/events")
            rd.wait_frames(1, timeout=5)
            assert rd.status == 200
            # (the stream ended: no marker → drained → end; the slot is released)
            rd.wait_frames(2, timeout=5); rd.close()
            time.sleep(0.3)
            _marker(project, stem="ok", log_path=str(d / "ok.log"))
            rd2 = SSEReader(s.port, path="/api/activity/log/ok/events")
            rd2.wait_frames(1, timeout=5)
            assert rd2.status == 200
            r = s.client.get("/api/events")
            assert r["status"] == 503                                   # the shared cap counts this stream
            rd2.close()

    def test_lead_sse_still_replays_and_ends_after_the_shared_poller(self, tmp_path, chat_fake_path, monkeypatch):
        # the Phase 37 conversation stream rides the same poller: replay + end unchanged
        from tagteam.config import read_config
        from tests.test_lead_chat import _project as _chat_project
        monkeypatch.setattr(state_mod, "_cached_project_root", None, raising=False)
        project = _chat_project(tmp_path)
        cfg = read_config(project / "tagteam.yaml")
        cid = lc.new_conversation(project, provider="claude")["id"]
        t = lc.send(project, cid, "hi", config=cfg)
        assert t["status"] == "ok"
        with Served(project, mode="cockpit") as s:
            rd = SSEReader(s.port, path=f"/api/lead/{cid}/events")
            rd.wait_frames(1, timeout=5)
            rd.pump(1.0)
            assert rd.frames[-1]["event"] == "end" and rd.frames[-1]["data"]["status"] == "ok"
            last = rd.frames[-1]["id"]
            rd.close()
            rd2 = SSEReader(s.port, headers={"Last-Event-ID": last}, path=f"/api/lead/{cid}/events")
            rd2.pump(1.5)
            assert rd2.frames == [] and rd2.status == 200
            rd2.close()


# ---------------------------------------------------------------------------
# source guards on the shipped cockpit
# ---------------------------------------------------------------------------

class TestSourceGuards:
    def test_html_has_the_cycle_region_and_no_tail_drawer(self):
        html = (WEB / "cockpit.html").read_text(encoding="utf-8")
        for id_ in ("lanes", "lane-lead", "lane-reviewer", "lane-token", "lane-lead-name", "lane-reviewer-name",
                    "lead-timeline", "reviewer-timeline", "all-activity", "activity",
                    "activity-empty", "activity-more", "activity-meta",
                    "turn-bar", "turn-text", "turn-age", "watcher-drawer", "wd-facts", "wd-rows", "wd-all", "wd-new",
                    "btn-watcher", "chip-cycle", "now-version"):
            assert f'id="{id_}"' in html, id_
        # Phase 68: four pills became ONE sentence (the turn bar) + the watcher drawer. They must not
        # creep back — seven equal pills was the problem. (Changed on purpose; was a required-id list.)
        js68 = (WEB / "cockpit.js").read_text(encoding="utf-8")
        for gone in ("chip-owed", "chip-inflight", "chip-paused", "chip-watcher"):
            assert gone not in html and gone not in js68, gone
        bar = html[html.index('id="turn-bar"') - 40:html.index("</button>", html.index('id="turn-bar"'))]
        assert "<button" in bar and 'aria-expanded="false"' in bar and 'aria-controls="watcher-drawer"' in bar
        # the bar has a full-width row of its own (seen live: beside the phase chip the sentence was cut off)
        assert html.index('id="now-chips"') < html.index('class="now-row turn-row"') < html.index('id="turn-bar"') \
            < html.index('id="watcher-drawer"') < html.index('id="needs-you"')
        # Phase 45: the flat list and its support elements live inside the Rounds-tab disclosure
        disc = html[html.index('id="all-activity"'):html.index("</details>", html.index('id="all-activity"'))]
        for id_ in ("activity", "activity-empty", "activity-more", "activity-meta"):
            assert f'id="{id_}"' in disc, id_
        # ids that went with the Lead tab / the Cycle region — gone from the HTML and never written from the JS
        js0 = (WEB / "cockpit.js").read_text(encoding="utf-8")
        for gone in ("tab-lead", "tab-lead-name", "lead-dot", "panel-lead", "lead-title", "lead-continuity",
                     "lead-transcript", "cycle-line", "activity-head"):
            assert f'id="{gone}"' not in html, gone
            assert f"$('{gone}')" not in js0, gone
        assert 'id="cycle"' not in html
        assert 'data-tab="lead"' not in html
        assert "btn-tail" not in html and "tail-drawer" not in html and "btn-cancel-turn" not in html
        js = (WEB / "cockpit.js").read_text(encoding="utf-8")
        assert "btn-tail" not in js and "tail-drawer" not in js
        # UX pass 2026-08-17: one Start (the cockpit's own engine); no terminals from the page;
        # the tab for the lead is named after the lead; Feed reads "Rounds"
        assert "Launch terminals" not in js and "/api/session/start" not in js and "Start headless" not in js
        assert ">Rounds<" in html
        # the lead lane is always visible: the chat refreshes on every pass, no tab condition
        assert "loadActivity(), loadLead(false)]" in js and "activeTab() === 'lead'" not in js
        assert 'name="tagteam-version"' not in html          # injected by the server, not shipped

    def test_cycle_activity_block_builds_dom_with_text_nodes_only(self):
        js = (WEB / "cockpit.js").read_text(encoding="utf-8")
        start = js.index("Phase 43: Cycle region + Activity log")
        end = js.index("Phase 37: Lead panel")
        block = js[start:end]
        assert "innerHTML" not in block, "the lanes/Activity block must build DOM with createElement/textContent only"
        # rows are keyed and patched — no container is ever wiped
        assert "store.rows[it.id]" in block
        for cont in ("activity", "lead-timeline", "reviewer-timeline"):
            assert f"$('{cont}').innerHTML" not in js, cont
        assert "function upsertActivity" in block and "function patchActRow" in block and "function upsertRow" in block
        # the verdict map lists exactly the six round actions
        m = re.search(r"var VERDICT_WORD = \{([^}]*)\};", block)
        assert m and re.findall(r"(\w+):", m.group(1)) == ["APPROVE", "REQUEST_CHANGES", "ESCALATE", "NEED_HUMAN", "GATE_PASS", "GATE_BOUNCE"]
        # role split
        assert "function inReviewerLane" in block and "function inLeadLane" in block

    def test_outcome_label_lists_exactly_the_seven_outcomes(self):
        js = (WEB / "cockpit.js").read_text(encoding="utf-8")
        m = re.search(r"var OUTCOME_LABEL = \{([^}]*)\};", js)
        assert m, "OUTCOME_LABEL missing"
        keys = re.findall(r"(\w+):\s*'", m.group(1))
        assert keys == list(capi.OUTCOMES), keys

    def test_lead_panel_keeps_lines_and_names_the_cycle_turn(self):
        js = (WEB / "cockpit.js").read_text(encoding="utf-8")
        lead_block = js[js.index("Phase 37: Lead panel"):js.index("Live connection: SSE with polling fallback")]
        assert "details" in lead_block and "activity (" in lead_block          # retained lines disclosure
        assert "streaming " in lead_block and "watch it" in lead_block
        assert "innerHTML" not in lead_block.replace("innerHTML = ''", "").replace("innerHTML=''", "")
        # Phase 45: messages are keyed rows in the lead lane, never a wiped transcript
        assert "LEAD.msgRows[key]" in lead_block and "function upsertLeadMessage" in lead_block

    def test_css_has_the_lane_and_outcome_styles(self):
        css = (WEB / "cockpit.css").read_text(encoding="utf-8")
        for sel in (".lane.running", ".token.lead", ".token.reviewer", ".act-row.s-running",
                    ".act-row.s-cancelled", ".act-row.s-process_gone", ".act-lines"):
            assert sel in css, sel
        assert ".tail-drawer" not in css


# ---------------------------------------------------------------------------
# behavioural: the real Activity-log code under a minimal DOM stub (node)
# ---------------------------------------------------------------------------

_DOM_STUB = r"""
// Minimal DOM: enough for the Cycle/Activity block (createElement/textContent,
// classList, dataset, appendChild/insertBefore/children, addEventListener).
function Node(tag) {
  this.tagName = tag; this.children = []; this.parentNode = null; this.textContent = '';
  this.dataset = {}; this._cls = []; this.style = {}; this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0;
  var self = this;
  this.classList = {
    add: function (c) { if (self._cls.indexOf(c) < 0) self._cls.push(c); },
    remove: function (c) { self._cls = self._cls.filter(function (x) { return x !== c; }); },
    toggle: function (c, force) { var has = self._cls.indexOf(c) >= 0; var want = (force === undefined) ? !has : !!force; if (want && !has) self._cls.push(c); if (!want && has) self.classList.remove(c); return want; },
    contains: function (c) { return self._cls.indexOf(c) >= 0; }
  };
}
Object.defineProperty(Node.prototype, 'className', { get: function () { return this._cls.join(' '); }, set: function (v) { this._cls = String(v || '').split(/\s+/).filter(Boolean); } });
Object.defineProperty(Node.prototype, 'firstChild', { get: function () { return this.children[0] || null; } });
Node.prototype.appendChild = function (n) { if (n.parentNode) n.parentNode.removeChild(n); n.parentNode = this; this.children.push(n); return n; };
Node.prototype.insertBefore = function (n, ref) { if (n.parentNode) n.parentNode.removeChild(n); n.parentNode = this; var i = ref ? this.children.indexOf(ref) : -1; if (i < 0) this.children.push(n); else this.children.splice(i, 0, n); return n; };
Node.prototype.removeChild = function (n) { var i = this.children.indexOf(n); if (i >= 0) this.children.splice(i, 1); n.parentNode = null; return n; };
Node.prototype.addEventListener = function () {};
Node.prototype.querySelector = function () { return null; };
Node.prototype.scrollIntoView = function () {};
var BY_ID = {};
function el(tag, cls, text) { var e = new Node(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function $(id) { if (!BY_ID[id]) { BY_ID[id] = new Node('div'); BY_ID[id].id = id; } return BY_ID[id]; }
var document = { getElementById: $, createElement: function (t) { return new Node(t); }, querySelector: function () { return null; }, querySelectorAll: function () { return []; } };
var window = { EventSource: undefined };
var localStorage = { getItem: function () { return null; }, setItem: function () {} };
var console = { error: function () {}, log: function () {} };
function esc(s) { return String(s == null ? '' : s); }
function fmtAge(s) { return String(s) + 's'; }
function fmtTs(ts) { return String(ts || ''); }
function fmtTime(ts) { return String(ts || '').slice(11, 19); }
function url(p) { return p; }
function getJSON() { return Promise.resolve({ ok: false }); }
function postJSON() { return Promise.resolve({ ok: false }); }
function toast() {}
function act() {}
function refreshAll() {}
function showTab() {}
function loadLead() { return Promise.resolve(); }
function leadStreamPath(cid) { return '/api/lead/' + cid + '/events'; }
var LEAD = { lines: {}, cursor: {} };
var NOW = null;
var CYCLE_ID = null;
"""


def _run_activity_harness(js_body: str) -> dict:
    """Evaluate the real Phase 43 block from cockpit.js under the DOM stub and
    run `js_body` after it (which must set `RESULT`). Returns RESULT as JSON."""
    import shutil
    import subprocess
    import tempfile
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed — the behavioural activity-log test needs it")
    js = (WEB / "cockpit.js").read_text(encoding="utf-8")
    # the lanes/activity block AND the lead-lane chat block (Phase 45: both feed the timelines)
    block = js[js.index("// ---------- Phase 43: Cycle region + Activity log"):js.index("// ---------- Live connection: SSE with polling fallback")]
    prog = _DOM_STUB + "\n" + block + "\n" + js_body + "\nprocess.stdout.write(JSON.stringify(RESULT));\n"
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "harness.js"
        f.write_text(prog, encoding="utf-8")
        r = subprocess.run([node, str(f)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


class TestActivityLogBehaviour:
    def test_running_to_terminal_reorders_without_rebuilding(self):
        """A long-running turn that ends must move below newer terminal rows
        (running first, then newest first) — moved, not rebuilt: same node,
        its lines intact, the container never wiped."""
        res = _run_activity_harness(r"""
var list = $('activity');
function item(id, status, started) { return { id: id, kind: 'cycle', role: 'lead', agent: 'A', status: status, started_at: started, ref: { log: id }, stem: id, age_s: 1, duration_ms: 1000 }; }
// t=10 a running turn; t=20 a newer finished turn → running sits on top
upsertActivity(item('turn:old', 'running', '2026-01-01T00:00:10+00:00'), list);
upsertActivity(item('turn:new', 'finished', '2026-01-01T00:00:20+00:00'), list);
var oldRow = ACT.rows['turn:old'].row;
appendActLine(ACT.rows['turn:old'], 'line one'); appendActLine(ACT.rows['turn:old'], 'line two');
var before = list.children.map(function (r) { return r.dataset.id; });
// the old turn ends → its record says finished (same id, same started_at)
upsertActivity(item('turn:old', 'finished', '2026-01-01T00:00:10+00:00'), list);
var after = list.children.map(function (r) { return r.dataset.id; });
// and a newer running turn appears → on top
upsertActivity(item('turn:run2', 'running', '2026-01-01T00:00:05+00:00'), list);
var after2 = list.children.map(function (r) { return r.dataset.id; });
// a terminal → running flip (a lingering marker re-claims a stem) moves it back up
upsertActivity(item('turn:new', 'running', '2026-01-01T00:00:20+00:00'), list);
var after3 = list.children.map(function (r) { return r.dataset.id; });
var RESULT = { before: before, after: after, after2: after2, after3: after3,
               sameNode: ACT.rows['turn:old'].row === oldRow, lines: ACT.rows['turn:old'].lines,
               boxKids: ACT.rows['turn:old'].box.children.length, oldStatus: ACT.rows['turn:old'].statusEl.textContent,
               oldKey: oldRow.dataset.key, rowsInDom: list.children.length, count: Object.keys(ACT.rows).length };
""")
        assert res["before"] == ["turn:old", "turn:new"]
        assert res["after"] == ["turn:new", "turn:old"], res            # running → terminal: moved below the newer terminal row
        assert res["after2"] == ["turn:run2", "turn:new", "turn:old"]   # a running row always sits on top
        assert res["after3"] == ["turn:new", "turn:run2", "turn:old"]   # terminal → running (newer started) moves up
        assert res["sameNode"] is True and res["lines"] == ["line one", "line two"] and res["boxKids"] == 2
        assert res["oldStatus"].startswith("done") and res["oldKey"].startswith("0|")
        assert res["rowsInDom"] == 3 and res["count"] == 3

    def test_source_guard_reinserts_on_key_change(self):
        js = (WEB / "cockpit.js").read_text(encoding="utf-8")
        start = js.index("function upsertRow("); end = js.index("function insertKeyed(")
        body = js[start:end]
        assert "storeSortKey(store, it)" in body and "insertRow(store, rec)" in body, \
            "an existing row must be re-inserted when its sort key changes"


class TestUxPassWords:
    """UX pass 2026-08-17: the page speaks the arbiter's words; the CLI's names
    live in tooltips and the confirm modal."""

    def test_outcome_labels_are_plain_words(self):
        js = (WEB / "cockpit.js").read_text(encoding="utf-8")
        m = re.search(r"var OUTCOME_LABEL = \{([^}]*)\};", js)
        vals = dict(re.findall(r"(\w+):\s*'([^']*)'", m.group(1)))
        assert vals == {"running": "working", "finished": "done", "cancelled": "cancelled", "failed": "failed",
                        "timed_out": "timed out", "process_gone": "process disappeared",
                        "orphaned": "no result recorded"}

    def test_strip_and_cards_avoid_engine_jargon(self):
        """User-facing strings in the shipped JS must not use the engine's
        words for the primary path. (Tooltips / confirm bodies may name the CLI.)"""
        js = (WEB / "cockpit.js").read_text(encoding="utf-8")
        html = (WEB / "cockpit.html").read_text(encoding="utf-8")
        # visible labels that must be gone
        for bad in ("in flight:", "no turn owed", "· owed ", "owed to ", "Start headless", "no watcher", "Interject</button>",
                    "Dispatch is on hold", "nothing is dispatching", "In-flight pointer", "Talk to the lead"):
            assert bad not in js and bad not in html, bad
        # and the words that replace them
        for good in ("Start the watcher", "Turns are paused", "Chat with", "Leave note", "Rounds"):
            assert good in js or good in html, good
        # Phase 68: who-has-the-ball is worded ONCE, server-side (cockpit_api.headline) — in the same plain
        # words, with none of the engine's. (Was: "is working" / "waiting on" / "watcher: on|off" in the JS.)
        import itertools
        from tagteam import cockpit_api as capi
        texts = set()
        for status, inflight, paused, running, mode, beat in itertools.product(
                ("ready", "escalated", "needs-human", "done"),
                (None, {"kind": "cycle", "role": "reviewer", "agent": "codex", "round": 1, "type": "impl",
                        "liveness": "running"}, {"kind": "gate", "round": 1, "liveness": "lost"}),
                (None, {"by": "you"}), (True, False), ("headless", "iterm2", "notify", None), ("fresh", "stale", "none")):
            facts = {"state": {"status": status, "turn": "reviewer" if status == "ready" else None, "seq": 1,
                               "phase": "p", "type": "impl", "result": "approved"},
                     "agents": {"lead": "claude", "reviewer": "codex"}, "inflight": inflight,
                     "turn_kind": (inflight or {}).get("kind"), "paused": paused,
                     "owed": {"role": "reviewer", "agent": "codex", "age_s": 1.0} if status == "ready" else None,
                     "watcher": {"running": running, "mode": mode, "beat": {"state": beat, "age_s": 1.0}}}
            texts.add(capi.headline(facts)["text"])
        joined = "\n".join(sorted(texts))
        for good in ("Waiting on codex", "codex is reviewing", "the watcher is off", "Paused by you", "Waiting on you"):
            assert good in joined, good
        for bad in ("in flight", "in-flight", "owed to", "dispatch", "slot", "marker", "seq", "pid"):
            assert bad not in joined.lower(), (bad, joined)

    def test_version_meta_is_injected_in_cockpit_mode(self, project):
        from tagteam import __version__
        with Served(project, mode="cockpit") as s:
            html = s.client.get("/")["raw"].decode()
            assert f'<meta name="tagteam-version" content="{__version__}">' in html
        with Served(project, mode="legacy") as s:
            assert "tagteam-version" not in s.client.get("/")["raw"].decode()


class TestLanesBehaviour:
    """Phase 45: the two lanes, driven through the real code under the DOM stub."""

    def test_reviewer_lane_ascending_streams_and_gets_its_verdict(self):
        res = _run_activity_harness(r"""
NOW = { agents: { lead: 'Claude', reviewer: 'Codex' } };
function item(id, role, kind, status, started, round, extra) { var o = { id: id, kind: kind, role: role, agent: role === 'reviewer' ? 'Codex' : (role === 'lead' ? 'Claude' : null), status: status, started_at: started, ref: { log: id }, stem: id, age_s: 1, duration_ms: 1000, round: round, phase: 'feat', type: 'impl' }; Object.keys(extra || {}).forEach(function (k) { o[k] = extra[k]; }); return o; }
CYCLE_ID = 'feat_impl'; setVerdictCycle(CYCLE_ID); setLaneCycle(CYCLE_ID);
var rl = $('reviewer-timeline'), ll = $('lead-timeline');
// two rounds arrive newest-first from the API; the lane must be ascending
[item('turn:rev-r2', 'reviewer', 'cycle', 'running', '2026-01-01T00:20:00+00:00', 2),
 item('turn:gate-r2', 'gatekeeper', 'gate', 'finished', '2026-01-01T00:15:00+00:00', 2, { raw_status: 'pass' }),
 item('turn:lead-r2', 'lead', 'cycle', 'finished', '2026-01-01T00:10:00+00:00', 2),
 item('turn:rev-r1', 'reviewer', 'cycle', 'finished', '2026-01-01T00:05:00+00:00', 1),
 item('turn:lead-r1', 'lead', 'cycle', 'finished', '2026-01-01T00:00:00+00:00', 1)].forEach(function (it) {
  upsertRow(ACT, it); if (inReviewerLane(it)) upsertRow(RLANE, it); if (inLeadLane(it)) upsertRow(LLANE, it);
});
var revOrder = rl.children.map(function (r) { return r.dataset.id; });
var leadOrder = ll.children.map(function (r) { return r.dataset.id; });
var running = RLANE.rows['turn:rev-r2']; var runningNode = running.row;
appendActLine(running, '[codex] reading the diff');
// the rounds arrive: round 1 was REQUEST_CHANGES, round 2 approves once it ends
applyRounds('feat_impl', [{ round: 1, entries: [{ role: 'reviewer', action: 'REQUEST_CHANGES', content: 'Please fix the postal code case.' }] },
                          { round: 2, entries: [{ role: 'reviewer', action: 'APPROVE', content: 'Approved.' }] }]);
var v1 = RLANE.rows['turn:rev-r1'].verdictEl.textContent;
var v2running = RLANE.rows['turn:rev-r2'].verdictEl.textContent;   // still running: no verdict yet
var gateV = RLANE.rows['turn:gate-r2'].verdictEl.textContent;
// the review ends → same node, verdict word, lines intact, still at the foot
upsertRow(RLANE, item('turn:rev-r2', 'reviewer', 'cycle', 'finished', '2026-01-01T00:20:00+00:00', 2));
var v2 = RLANE.rows['turn:rev-r2'].verdictEl.textContent;
toggleActText(RLANE.rows['turn:rev-r1']);
var RESULT = { revOrder: revOrder, leadOrder: leadOrder, v1: v1, v2running: v2running, gateV: gateV, v2: v2,
               sameNode: RLANE.rows['turn:rev-r2'].row === runningNode, lines: RLANE.rows['turn:rev-r2'].lines,
               foot: rl.children[rl.children.length - 1].dataset.id, text: RLANE.rows['turn:rev-r1'].textEl.textContent,
               actCount: Object.keys(ACT.rows).length, revCount: Object.keys(RLANE.rows).length, leadCount: Object.keys(LLANE.rows).length };
""")
        assert res["revOrder"] == ["turn:rev-r1", "turn:gate-r2", "turn:rev-r2"]      # ascending; pre-check before its review
        assert res["leadOrder"] == ["turn:lead-r1", "turn:lead-r2"]                    # only the lead's turns, ascending
        assert res["v1"] == "changes requested" and res["v2running"] == "" and res["gateV"] == "passed"
        assert res["v2"] == "approved" and res["sameNode"] is True and res["lines"] == ["[codex] reading the diff"]
        assert res["foot"] == "turn:rev-r2" and res["text"] == "Please fix the postal code case."
        assert (res["actCount"], res["revCount"], res["leadCount"]) == (5, 3, 2)      # role split: nothing crosses lanes

    def test_lead_lane_merges_chat_and_turns_in_time_without_wiping(self):
        res = _run_activity_harness(r"""
NOW = { agents: { lead: 'Claude', reviewer: 'Codex' } };
LEAD.cfg = { ok: true, agent: 'Claude' };
var ll = $('lead-timeline');
LEAD.conv = { id: 'c-1', turns: [
  { n: 1, ts: '2026-01-01T00:00:00+00:00', user_text: 'plan it', status: 'ok', reply: 'Here is the plan', finished_at: '2026-01-01T00:01:00+00:00' },
  { n: 2, ts: '2026-01-01T00:30:00+00:00', user_text: '/handoff start x', status: 'running' } ] };
renderLeadTimeline();
var card = { id: 'turn:lead-r1', kind: 'cycle', role: 'lead', agent: 'Claude', status: 'finished', started_at: '2026-01-01T00:10:00+00:00', ref: { log: 'turn:lead-r1' }, stem: 'turn:lead-r1', age_s: 1, duration_ms: 240000, round: 1, type: 'plan' };
upsertRow(LLANE, card);
var order = ll.children.map(function (r) { return r.dataset.id; });
var row2 = LEAD.msgRows['msg:c-1:2'].row;
// lines stream into the running message; a re-render keeps them
leadLines('c-1', 2).push('[claude] reading the roadmap');
LEAD.conv.turns[1] = { n: 2, ts: '2026-01-01T00:30:00+00:00', user_text: '/handoff start x', status: 'ok', reply: 'Cycle opened.', finished_at: '2026-01-01T00:31:00+00:00' };
renderLeadTimeline();
var sameRow = LEAD.msgRows['msg:c-1:2'].row === row2;
var hasDetails = !!row2.children[1] && row2.children[1].children.some(function (c) { return c.tagName === 'details'; });
var order2 = ll.children.map(function (r) { return r.dataset.id; });
// switching conversation removes the other's messages, keeps the lead's cards
LEAD.conv = { id: 'c-2', turns: [ { n: 1, ts: '2026-01-01T00:40:00+00:00', user_text: 'hi', status: 'ok', reply: 'hello', finished_at: '2026-01-01T00:41:00+00:00' } ] };
renderLeadTimeline();
var order3 = ll.children.map(function (r) { return r.dataset.id; });
var RESULT = { order: order, sameRow: sameRow, hasDetails: hasDetails, order2: order2, order3: order3, kids: ll.children.length };
""")
        assert res["order"] == ["msg:c-1:1", "turn:lead-r1", "msg:c-1:2"]     # merged in time: message · card · message
        assert res["sameRow"] is True and res["hasDetails"] is True         # patched, not rebuilt; kept lines under a disclosure
        assert res["order2"] == ["msg:c-1:1", "turn:lead-r1", "msg:c-1:2"]
        assert res["order3"] == ["turn:lead-r1", "msg:c-2:1"] and res["kids"] == 2

    def test_lead_lane_prompt_stays_with_cards_but_no_messages(self):
        """No conversation yet + the lead's cycle cards → the cards show AND the
        "No messages yet …" prompt stays (it speaks to the chat, not the lane)."""
        res = _run_activity_harness(r"""
NOW = { agents: { lead: 'Claude', reviewer: 'Codex' } };
LEAD.cfg = { ok: true, agent: 'Claude' };
LEAD.conv = null;
renderLeadTimeline();
var promptNoConv = !$('lead-empty').classList.contains('hidden');
upsertRow(LLANE, { id: 'turn:lead-r1', kind: 'cycle', role: 'lead', agent: 'Claude', status: 'finished', started_at: '2026-01-01T00:10:00+00:00', ref: { log: 'turn:lead-r1' }, stem: 'turn:lead-r1', age_s: 1, duration_ms: 1000, round: 1, type: 'plan' });
renderLeadEmpty();
var promptWithCard = !$('lead-empty').classList.contains('hidden');
var cards = $('lead-timeline').children.length;
// an empty conversation (created, nothing said yet) → still the prompt
LEAD.conv = { id: 'c-1', turns: [] }; renderLeadTimeline();
var promptEmptyConv = !$('lead-empty').classList.contains('hidden');
// the first message → the prompt goes
LEAD.conv = { id: 'c-1', turns: [ { n: 1, ts: '2026-01-01T00:20:00+00:00', user_text: 'hi', status: 'ok', reply: 'hello', finished_at: '2026-01-01T00:21:00+00:00' } ] }; renderLeadTimeline();
var promptAfterMsg = !$('lead-empty').classList.contains('hidden');
var RESULT = { promptNoConv: promptNoConv, promptWithCard: promptWithCard, cards: cards, promptEmptyConv: promptEmptyConv, promptAfterMsg: promptAfterMsg, kids: $('lead-timeline').children.length };
""")
        assert res["promptNoConv"] is True
        assert res["promptWithCard"] is True and res["cards"] == 1
        assert res["promptEmptyConv"] is True
        assert res["promptAfterMsg"] is False and res["kids"] == 2


class TestViewportFit:
    def test_lanes_fit_the_viewport_and_the_start_card_is_compact(self):
        js = (WEB / "cockpit.js").read_text(encoding="utf-8")
        css = (WEB / "cockpit.css").read_text(encoding="utf-8")
        assert "function fitLanes(" in js and "window.addEventListener('resize', fitLanes)" in js
        # Phase 68: renderOwedChip is gone; renderNow now ends at the watcher-button handler
        assert "fitLanes();" in js[js.index("function renderNow("):js.index("$('btn-watcher').addEventListener(")]
        assert "scard.classList.add('compact')" in js
        assert ".card.start.compact" in css and ".lanes .lane { min-height: 0; overflow: hidden; }" in css


class TestReviewerLanePerCycle:
    """Phase 58: the reviewer lane shows the current cycle; earlier cycles sit
    behind "Show last session"; verdict chips belong to the cycle they came from."""

    _ITEM = r"""
function rev(id, phase, type, round, started) { return { id: id, kind: 'cycle', role: 'reviewer', agent: 'Codex', status: 'finished', started_at: started, ref: { log: id }, stem: id, age_s: 1, duration_ms: 1000, round: round, phase: phase, type: type }; }
function vis(store) { return Object.keys(store.rows).filter(function (id) { return !store.rows[id].row.classList.contains('hidden'); }).sort(); }
function useCycle(c) { CYCLE_ID = c; setVerdictCycle(c); setLaneCycle(c); }
"""

    def test_scope_empty_state_and_toggle(self):
        res = _run_activity_harness(self._ITEM + r"""
useCycle('alpha_impl');
upsertRow(RLANE, rev('turn:a1', 'alpha', 'impl', 1, '2026-01-01T00:00:00+00:00'));
upsertRow(RLANE, rev('turn:a2', 'alpha', 'impl', 2, '2026-01-01T00:10:00+00:00'));
renderReviewerLaneScope();
var onA = { vis: vis(RLANE), btnHidden: $('btn-reviewer-last').classList.contains('hidden'), emptyHidden: $('reviewer-empty').classList.contains('hidden') };
useCycle('beta_plan');                                      // a new cycle: blank by default
var onB = { vis: vis(RLANE), btnHidden: $('btn-reviewer-last').classList.contains('hidden'), btn: $('btn-reviewer-last').textContent,
            empty: $('reviewer-empty').textContent, emptyHidden: $('reviewer-empty').classList.contains('hidden') };
toggleLastSession();
var shown = { vis: vis(RLANE), btn: $('btn-reviewer-last').textContent, label: RLANE.rows['turn:a1'].cycleEl.textContent,
              dim: RLANE.rows['turn:a1'].row.classList.contains('other-cycle'), emptyHidden: $('reviewer-empty').classList.contains('hidden') };
upsertRow(RLANE, rev('turn:b1', 'beta', 'plan', 1, '2026-01-01T01:00:00+00:00'));   // a patch keeps the scope classes
upsertRow(RLANE, rev('turn:a1', 'alpha', 'impl', 1, '2026-01-01T00:00:00+00:00'));
var afterPatch = vis(RLANE);
useCycle('gamma_impl');                                     // switching again hides earlier rows again
var onC = { vis: vis(RLANE), btn: $('btn-reviewer-last').textContent, empty: $('reviewer-empty').textContent };
var RESULT = { onA: onA, onB: onB, shown: shown, afterPatch: afterPatch, onC: onC };
""")
        assert res["onA"] == {"vis": ["turn:a1", "turn:a2"], "btnHidden": True, "emptyHidden": True}
        assert res["onB"]["vis"] == [] and res["onB"]["btnHidden"] is False and res["onB"]["btn"] == "Show last session"
        assert res["onB"]["empty"] == "No reviews yet for beta · plan." and res["onB"]["emptyHidden"] is False
        assert res["shown"]["vis"] == ["turn:a1", "turn:a2"] and res["shown"]["btn"] == "Hide last session"
        assert res["shown"]["label"] == "alpha · impl" and res["shown"]["dim"] is True and res["shown"]["emptyHidden"] is True
        assert res["afterPatch"] == ["turn:a1", "turn:a2", "turn:b1"]
        assert res["onC"] == {"vis": [], "btn": "Show last session", "empty": "No reviews yet for gamma · impl."}

    def test_verdict_cache_is_bound_to_its_cycle(self):
        res = _run_activity_harness(self._ITEM + r"""
var A = [{ round: 1, entries: [{ role: 'reviewer', action: 'REQUEST_CHANGES', content: 'A says fix' }] }];
var B = [{ round: 1, entries: [{ role: 'reviewer', action: 'APPROVE', content: 'B approves' }] }];
function chip(id) { return RLANE.rows[id].verdictEl.textContent; }
useCycle('alpha_impl');
upsertRow(RLANE, rev('turn:a1', 'alpha', 'impl', 1, '2026-01-01T00:00:00+00:00'));
var appliedA = applyRounds('alpha_impl', A);
var aChip = chip('turn:a1');
useCycle('beta_impl');
upsertRow(RLANE, rev('turn:b1', 'beta', 'impl', 1, '2026-01-01T01:00:00+00:00'));
var beforeB = chip('turn:b1');                               // B has not loaded: no chip (never A's round 1)
var aAfterSwitch = chip('turn:a1');                          // A's row lost its chip with its cache
var lateA = applyRounds('alpha_impl', A);                    // a slow A response lands after the switch
var afterLateA = chip('turn:b1');
var appliedB = applyRounds('beta_impl', B);
var bChip = chip('turn:b1'), aChipOnB = chip('turn:a1');
var staleAgain = applyRounds('alpha_impl', A);               // out of order: B, then a stale A
var RESULT = { appliedA: appliedA, aChip: aChip, beforeB: beforeB, aAfterSwitch: aAfterSwitch, lateA: lateA,
               afterLateA: afterLateA, appliedB: appliedB, bChip: bChip, aChipOnB: aChipOnB, staleAgain: staleAgain,
               bStill: chip('turn:b1'), cache: ROUNDS.cycle };
""")
        assert res["appliedA"] is True and res["aChip"] == "changes requested"
        assert res["beforeB"] == "" and res["aAfterSwitch"] == ""
        assert res["lateA"] is False and res["afterLateA"] == ""
        assert res["appliedB"] is True and res["bChip"] == "approved" and res["aChipOnB"] == ""
        assert res["staleAgain"] is False and res["bStill"] == "approved" and res["cache"] == "beta_impl"

    def test_button_and_markup_present(self):
        html = (WEB / "cockpit.html").read_text(encoding="utf-8")
        js = (WEB / "cockpit.js").read_text(encoding="utf-8")
        assert 'id="btn-reviewer-last"' in html and "Show last session" in html
        assert "ROUNDS_BY_N" not in js
        assert "$('btn-reviewer-last').addEventListener('click'" in js


# ---------------------------------------------------------------------------
# Phase 68 — the turn bar and the watcher drawer, run for real under node
# ---------------------------------------------------------------------------

_P68_PRELUDE = r"""
Node.prototype.setAttribute = function (k, v) { this.attrs = this.attrs || {}; this.attrs[k] = String(v); };
document.addEventListener = function () {};
var START = null;
var FETCHED = [];
var EVENTS_BODY = { events: [] };
getJSON = function (path) { FETCHED.push(path); return Promise.resolve({ ok: true, body: path.indexOf('/api/watcher/events') === 0 ? EVENTS_BODY : {} }); };
function setInterval() { return 1; }
function clearInterval() {}
function fitLanes() {}
function lastTurnText(lt) { return 'last: ' + lt.kind; }
fmtAge = function (s) { return s + 's'; };
function texts(node) { return node.children.map(function (c) { return c.textContent; }); }
function rows() { return $('wd-rows').children.map(function (r) { return r.className + ' | ' + (r.children.length ? texts(r).join(' | ') : r.textContent); }); }
var DONE = null;   // a test that needs to wait sets DONE to a promise
// A toy layout: every row is 100px tall, stacked in order, in a scroll box that sits 57px down the
// page with a 1px border — so a row's page position is NOT its position in the box. (The real
// layout — offsetParent, wrapped rows — is exercised in Chromium: TestDrawerScrollInARealBrowser.)
Node.prototype.getBoundingClientRect = function () {
  var p = this.parentNode;
  if (p && p.id === 'wd-rows') { var i = p.children.indexOf(this); return { top: 57 + 1 + i * 100 - p.scrollTop, height: 100 }; }
  return { top: 57, height: this.clientHeight || 0 };
};
Object.defineProperty(Node.prototype, 'offsetTop', { get: function () { throw new Error('offsetTop is relative to the offsetParent, not the scroll box — use rowTopIn()'); } });
"""


def _run_p68(js_body: str) -> dict:
    import shutil
    import subprocess
    import tempfile
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed — the behavioural turn-bar test needs it")
    js = (WEB / "cockpit.js").read_text(encoding="utf-8")
    block = js[js.index("// ---------- Phase 68: Turn bar + watcher drawer"):js.index("// ---------- end Phase 68")]
    prog = (_DOM_STUB + _P68_PRELUDE + "\n" + block + "\n" + js_body
            + "\nPromise.resolve(DONE).then(function () { process.stdout.write(JSON.stringify(RESULT)); });\n")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "p68.js"
        f.write_text(prog, encoding="utf-8")
        r = subprocess.run([node, str(f)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


class TestTurnBarAndDrawer:
    def test_the_slice_is_self_contained_and_never_uses_innerhtml(self):
        js = (WEB / "cockpit.js").read_text(encoding="utf-8")
        block = js[js.index("// ---------- Phase 68: Turn bar + watcher drawer"):js.index("// ---------- end Phase 68")]
        assert "innerHTML" not in block and "insertAdjacentHTML" not in block
        # the bar presents the server's sentence; it must not work out who has the ball itself
        for derived in ("n.owed.agent", "inflight.agent", "is working", "waiting on", "Waiting on"):
            assert derived not in block, derived
        assert "n.headline" in block and "/api/watcher/events" in block

    def test_bar_shows_the_servers_sentence_its_tone_and_one_ticking_age(self):
        r = _run_p68(r"""
          renderTurnBar({ headline: { state: 'working', tone: 'working', text: 'codex is reviewing · round 2', age_s: 61.4, role: 'reviewer' } });
          var a = { cls: $('turn-bar').className, state: $('turn-bar').dataset.state, text: $('turn-text').textContent, age: $('turn-age').textContent };
          tickTurnBar(); tickTurnBar();
          var ticked = $('turn-age').textContent;
          renderTurnBar({ headline: { state: 'approved', tone: 'ok', text: 'Approved — p1, plan', age_s: null, role: null } });
          tickTurnBar();
          var b = { cls: $('turn-bar').className, age: $('turn-age').textContent, text: $('turn-text').textContent };
          renderTurnBar({});
          var RESULT = { a: a, ticked: ticked, b: b, none: $('turn-bar').className };
        """)
        assert r["a"] == {"cls": "turn-bar tone-working role-reviewer", "state": "working",
                          "text": "codex is reviewing · round 2", "age": "· 61s"}
        assert r["ticked"] == "· 63s"                                  # the age lives in one place and ticks locally
        assert r["b"] == {"cls": "turn-bar tone-ok", "age": "", "text": "Approved — p1, plan"}
        assert r["none"] == "turn-bar tone-idle"

    def test_drawer_hides_chatter_until_asked_and_fetches_nothing_while_closed(self):
        r = _run_p68(r"""
          EVENTS_BODY = { events: [
            { ts: '2026-09-21T06:33:46+00:00', kind: 'start', msg: 'Watching handoff-state.json' },
            { ts: '2026-09-21T06:33:46+00:00', kind: 'info', msg: 'Lead: claude | Reviewer: codex' },
            { ts: '2026-09-21T06:33:47+00:00', kind: 'turn', msg: ">> codex's turn" },
            { ts: '2026-09-21T06:33:48+00:00', kind: 'send-failed', msg: '   FAILED: Could not send <b>x</b>' },
            { ts: '2026-09-21T06:34:00+00:00', kind: 'gate', msg: '   gate: bounced' } ] };
          var RESULT = {};
          DONE = loadWatcherEvents().then(function () {
            RESULT.closedFetches = FETCHED.slice();
            setDrawer(true);
            return Promise.resolve().then(function () { return Promise.resolve(); });
          }).then(function () {
            RESULT.open = { hidden: $('watcher-drawer').classList.contains('hidden'), expanded: $('turn-bar').attrs['aria-expanded'],
                            fetches: FETCHED.slice(), rows: rows() };
            $('wd-all').checked = true; DRAWER.all = true; renderDrawerRows(DRAWER.events);
            RESULT.all = rows().length;
            return loadWatcherEvents().then(function () { RESULT.allFetch = FETCHED[FETCHED.length - 1]; });
          }).then(function () {
            renderDrawerRows([{ ts: '2026-09-21T06:50:19+00:00', kind: 'turn', msg: ">> codex's turn", repeat: 46, last_ts: '2026-09-21T06:57:50+00:00' }]);
            RESULT.folded = rows()[0];
            setDrawer(false);
            RESULT.closed = { hidden: $('watcher-drawer').classList.contains('hidden'), expanded: $('turn-bar').attrs['aria-expanded'] };
          });
        """)
        assert r["closedFetches"] == []
        assert r["open"]["hidden"] is False and r["open"]["expanded"] == "true"
        assert r["open"]["fetches"] == ["/api/watcher/events?n=200&chatter=0"]      # the server drops chatter BEFORE counting
        assert r["open"]["rows"] == [
            "wd-row f-dispatch | 06:33:46 | start | Watching handoff-state.json",
            "wd-row f-dispatch | 06:33:47 | turn | >> codex's turn",
            "wd-row f-problem | 06:33:48 | send-failed | FAILED: Could not send <b>x</b>",     # text, never markup
            "wd-row f-check | 06:34:00 | gate | gate: bounced"]
        assert r["all"] == 5 and r["allFetch"] == "/api/watcher/events?n=200&chatter=1"
        assert r["folded"] == "wd-row f-dispatch | 06:50:19 | turn | >> codex's turn   (×46, until 06:57:50)"
        assert r["closed"] == {"hidden": True, "expanded": "false"}

    def test_drawer_empty_states(self):
        r = _run_p68(r"""
          DRAWER.open = true;
          renderDrawerRows([]);
          var none = rows();
          renderDrawerRows([{ ts: '', kind: 'info', msg: 'chatter' }]);
          var RESULT = { none: none, onlyInfo: rows() };
        """)
        assert "No watcher history yet" in r["none"][0] and "3.14.5" in r["none"][0]
        assert "show everything" in r["onlyInfo"][0]

    _SCROLL_SETUP = r"""
          var box = $('wd-rows'); box.clientHeight = 300; box.clientTop = 1;
          Object.defineProperty(box, 'scrollHeight', { get: function () { return box.children.length * 100; } });
          function ev(i, extra) { var e = { ts: 't' + i, kind: 'sent', seq: i, msg: 'e' + i }; for (var k in (extra || {})) e[k] = extra[k]; return e; }
          function range(a, b) { var o = []; for (var i = a; i < b; i++) o.push(ev(i)); return o; }
          function cue() { return !$('wd-new').classList.contains('hidden'); }
          function topRow() { var a = topAnchor(box); return a ? a.key.split('|')[3] + '+' + a.delta : null; }
    """

    def test_drawer_follows_new_rows_only_while_at_the_bottom(self):
        r = _run_p68(self._SCROLL_SETUP + r"""
          renderDrawerRows(range(0, 5));
          renderDrawerRows(range(0, 6));                                   // was at the bottom → follows
          var followed = { top: box.scrollTop, cue: cue() };
          box.scrollTop = 140;                                             // the arbiter scrolls back: row e1, 40px in
          renderDrawerRows(range(0, 7));
          var RESULT = { followed: followed, held: { top: box.scrollTop, row: topRow(), cue: cue() } };
        """)
        assert r["followed"] == {"top": 600, "cue": False}
        assert r["held"] == {"top": 140, "row": "e1+40", "cue": True}

    def test_a_full_window_still_shows_news_and_keeps_the_reader_on_the_same_event(self):
        """impl r1: the API returns a rolling 200-row window. At capacity the
        length never changes, and the same pixel offset becomes another event."""
        r = _run_p68(self._SCROLL_SETUP + r"""
          renderDrawerRows(range(0, 200));
          box.scrollTop = 5040;                                            // reading e50, 40px in
          renderDrawerRows(range(1, 201));                                 // e0 dropped out, e200 arrived: same length
          var rolled = { len: box.children.length, top: box.scrollTop, row: topRow(), cue: cue() };
          renderDrawerRows(range(60, 260));                                // the reader's event is gone from the window
          var gone = { top: box.scrollTop, cue: cue() };
          var RESULT = { rolled: rolled, gone: gone };
        """)
        assert r["rolled"] == {"len": 200, "top": 4940, "row": "e50+40", "cue": True}   # one row up, same event
        assert r["gone"] == {"top": 4940, "cue": True}                                  # stays put rather than jumping

    def test_the_unread_cue_is_sticky_until_the_reader_catches_up(self):
        r = _run_p68(self._SCROLL_SETUP + r"""
          renderDrawerRows(range(0, 10)); box.scrollTop = 100;
          var quiet = (renderDrawerRows(range(0, 10)), cue());             // an identical refresh is not news
          renderDrawerRows(range(0, 11));
          var news = cue();
          renderDrawerRows(range(0, 11)); renderDrawerRows(range(0, 11));  // …and further unchanged refreshes keep the cue
          var kept = cue();
          // a folded row growing in place is news too, with no change in length
          drawerCaughtUp();
          var folded = range(0, 11); folded[10] = ev(10, { repeat: 2, last_ts: 't10b' });
          renderDrawerRows(folded);
          var grew = { cue: cue(), len: box.children.length, row: topRow() };
          box.scrollTop = box.scrollHeight;                                // the reader scrolls to the bottom
          renderDrawerRows(folded);
          var RESULT = { quiet: quiet, news: news, kept: kept, grew: grew, caughtUp: cue(), unread: DRAWER.unread };
        """)
        assert r == {"quiet": False, "news": True, "kept": True, "grew": {"cue": True, "len": 11, "row": "e1+0"},
                     "caughtUp": False, "unread": False}

    def test_facts_and_the_reason_the_page_cannot_run_turns(self):
        r = _run_p68(r"""
          function facts() { var d = $('wd-facts').children, o = {}; for (var i = 0; i < d.length; i += 2) o[d[i].textContent] = d[i + 1].textContent + (d[i + 1].className ? ' [' + d[i + 1].className + ']' : ''); return o; }
          START = { headless: { ok: false, errors: ['not set up: no docs/roadmap.md'] } };
          renderDrawerFacts({ owed: { role: 'reviewer' }, watcher: { running: false, beat: { state: 'none', text: 'never (no heartbeat recorded)' } } });
          var off = { facts: facts(), btn: $('btn-watcher').textContent, title: $('btn-watcher').title, note: $('wd-note').textContent,
                      body: watcherStartBody(watcherStartMode()) };
          START = { headless: { ok: true, errors: [] } };
          renderDrawerFacts({ paused: { by: 'jack', reason: 'reading the plan' }, last_turn: { kind: 'gate' },
            watcher: { running: true, mode: 'iterm2', pid: 35417, source: 'scan', started_at: '2026-09-21T06:15:24+00:00',
                       beat: { state: 'stale', text: 'STALE: 6m ago, expected every 10s' } } });
          var on = { facts: facts(), btnHidden: $('btn-watcher').classList.contains('hidden'), note: $('wd-note').textContent,
                     body: watcherStartBody(watcherStartMode()) };
          renderDrawerFacts({ watcher: { running: true, mode: 'headless', pid: 9, source: 'pidfile', beat: { state: 'fresh', text: '2s ago' } } });
          var RESULT = { off: off, on: on, headless: { note: $('wd-note').textContent, noteHidden: $('wd-note').classList.contains('hidden'),
                                                         btn: $('btn-watcher').textContent, btnHidden: $('btn-watcher').classList.contains('hidden') } };
        """)
        off, on = r["off"], r["on"]
        assert off["facts"] == {"watcher": "not running [warn]", "last look": "never (no heartbeat recorded)", "turns": "not paused"}
        assert off["btn"] == "Start the watcher" and off["title"].endswith("--mode notify --pidfile")
        assert off["note"] == "This page cannot run the agents itself yet: not set up: no docs/roadmap.md."
        assert "only NOTIFIES" in off["body"] and "not set up: no docs/roadmap.md" in off["body"]
        assert on["facts"] == {"watcher": "running — iterm2, pid 35417, since 06:15:24",
                               "last look": "STALE: 6m ago, expected every 10s [danger]",
                               "turns": "paused by jack — reading the plan [warn]", "last turn": "gate"}
        assert on["btnHidden"] is True                       # not ours to stop: found by the process scan
        assert on["note"] == "Agents are running in their terminals (iterm2). The lanes show only turns the cockpit runs itself."
        assert on["body"].startswith("From then on each waiting turn runs by itself")
        assert r["headless"] == {"note": "", "noteHidden": True, "btn": "Stop the watcher", "btnHidden": False}


# ---------------------------------------------------------------------------
# Phase 68 (impl r2) — the drawer's scroll anchor in a REAL layout engine.
# The node stub cannot know that a row's offsetParent is `.wd-history`, not the
# scroll box; headless Chromium with the shipped CSS and markup can.
# ---------------------------------------------------------------------------

def _find_chromium() -> str | None:
    import glob
    import shutil
    env = os.environ.get("TAGTEAM_TEST_CHROME")
    if env:
        return env if Path(env).exists() else None
    home = Path.home()
    cands = sorted(glob.glob(str(home / "Library/Caches/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell")),
                   reverse=True)
    cands += sorted(glob.glob(str(home / ".cache/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell")),
                    reverse=True)
    cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    for c in cands:
        if Path(c).exists():
            return c
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome-headless-shell"):
        found = shutil.which(name)
        if found:
            return found
    return None


_BROWSER_SHIMS = r"""
function $(id) { return document.getElementById(id); }
function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function fmtAge(s) { return s + 's'; }
function fmtTime(ts) { return String(ts || ''); }
function getJSON() { return Promise.resolve({ ok: false }); }
function lastTurnText(lt) { return 'last: x'; }
function fitLanes() {}
function act() {}
var START = null, NOW = {};
"""


def _run_in_chromium(scenario_js: str) -> dict:
    import re
    import subprocess
    import tempfile
    chrome = _find_chromium()
    if not chrome:
        pytest.skip("no Chromium/Chrome found (set TAGTEAM_TEST_CHROME) — the real-layout drawer test needs one")
    html = (WEB / "cockpit.html").read_text(encoding="utf-8")
    css = (WEB / "cockpit.css").read_text(encoding="utf-8")
    js = (WEB / "cockpit.js").read_text(encoding="utf-8")
    header = html[html.index('<header class="now" id="now">'):html.index("</header>") + len("</header>")]
    header = header.replace('class="watcher-drawer hidden"', 'class="watcher-drawer"')       # the shipped markup, drawer open
    block = js[js.index("// ---------- Phase 68: Turn bar + watcher drawer"):js.index("// ---------- end Phase 68")]
    page = ("<!DOCTYPE html><html><head><meta charset='utf-8'><style>" + css + "</style></head><body>" + header
            + "<pre id='RESULT'></pre><script>" + _BROWSER_SHIMS + block + "\n" + scenario_js
            + "\ndocument.getElementById('RESULT').textContent = JSON.stringify(RESULT);</script></body></html>")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "drawer.html"
        f.write_text(page, encoding="utf-8")
        r = subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                            "--window-size=1200,900", f"--user-data-dir={d}/profile", "--dump-dom", f.as_uri()],
                           capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr[-2000:]
    m = re.search(r"<pre id=\"RESULT\">(.*?)</pre>", r.stdout, re.S)
    assert m and m.group(1).strip(), "the page produced no RESULT — a script error? " + r.stderr[-1500:]
    import html as _html
    return json.loads(_html.unescape(m.group(1)))


class TestDrawerScrollInARealBrowser:
    def test_the_reader_stays_on_the_same_event_when_the_oldest_row_drops_out(self):
        r = _run_in_chromium(r"""
          var box = $('wd-rows');
          // variable heights: every third message wraps over several lines in the real column width
          function ev(i) { var long = (i % 3 === 1) ? ' ' + new Array(40).join('a very long watcher message that wraps ') : '';
                           return { ts: 't' + i, kind: 'sent', seq: i, msg: 'event ' + i + long }; }
          function range(a, b) { var o = []; for (var i = a; i < b; i++) o.push(ev(i)); return o; }
          // measured independently of the code under test
          function firstVisible() {
            var edge = box.getBoundingClientRect().top + box.clientTop;
            for (var i = 0; i < box.children.length; i++) {
              var rc = box.children[i].getBoundingClientRect();
              if (rc.bottom > edge + 0.5) return { seq: +box.children[i].dataset.key.split('|')[2], into: Math.round(edge - rc.top) };
            }
          }
          renderDrawerRows(range(0, 200));
          var row0 = box.children[0], row1 = box.children[1];
          var h0 = row0.getBoundingClientRect().height, h1 = row1.getBoundingClientRect().height;
          box.scrollTop = Math.round(h0 + 30);                    // 30px into event 1 (a wrapped, tall row)
          var before = firstVisible();
          var trap = { offsetParentIsTheBox: row0.offsetParent === box, offsetParentClass: row0.offsetParent && row0.offsetParent.className,
                       offsetTopOfRow0: row0.offsetTop };
          var anchor = topAnchor(box);
          renderDrawerRows(range(1, 201));                        // event 0 drops out, event 200 arrives
          var after = firstVisible();
          renderDrawerRows(range(1, 201));                        // an unchanged refresh must not move the reader either
          var RESULT = { h0: Math.round(h0), h1: Math.round(h1), before: before, after: after, again: firstVisible(),
                         anchor: { seq: +anchor.key.split('|')[2], delta: Math.round(anchor.delta) }, padTop: parseFloat(getComputedStyle(box).paddingTop),
                         trap: trap, rows: box.children.length, scrollTop: Math.round(box.scrollTop),
                         cue: !$('wd-new').classList.contains('hidden') };
        """)
        # the trap is real under the shipped CSS: rows are positioned against `.wd-history`, not the scroll box
        assert r["trap"]["offsetParentIsTheBox"] is False and "wd-history" in r["trap"]["offsetParentClass"]
        assert r["trap"]["offsetTopOfRow0"] > 0
        assert r["h1"] > r["h0"] * 2                                     # the rows really do wrap to different heights
        into = 30 - round(r["padTop"])                                    # the box's own padding sits above row 0
        assert r["before"] == {"seq": 1, "into": into} and into > 0
        assert r["anchor"] == {"seq": 1, "delta": into}                   # the code under test sees what the reader sees
        assert r["after"] == {"seq": 1, "into": into}                     # same event, same offset into it
        assert r["again"] == r["after"] and r["rows"] == 200
        assert r["scrollTop"] == 30                                       # event 0's height is gone from above the reader
        assert r["cue"] is True
