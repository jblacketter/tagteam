"""Phase 33 tests: escalation briefer — config/spec, event identity, prompt
budgets, claim semantics (auto/manual, dedupe, restart, concurrency,
abandoned), runner + inflight lifecycle, `brief` command, `rule`
(add_ruling without the stale gate, capture-before-append, answer/rearm),
grouped-rounds `entries`/`rulings`, repair preservation, watcher trigger
and bootstrap, flag-off compatibility."""
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

from tagteam import briefer as b
from tagteam import controls, db, headless as h, procs, repair
from tagteam import cycle as cycle_mod
from tagteam import state as state_mod
from tagteam.config import read_config, get_briefer_spec, validate_briefer_config
from tagteam.cycle import STALE_ROUND_LIMIT

from tests.test_headless import (  # noqa: F401
    project, fake_path, _init_cycle, _usage_rows, _diag_kinds, _pid_alive, STD_CMD,
)
from tests.test_controls import needs_proc_inspection  # noqa: F401


def _enable(project: Path, extra: str = "") -> None:
    (project / "tagteam.yaml").write_text(
        "agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\n"
        "briefer:\n  enabled: true\n" + extra, encoding="utf-8")


def _spec(project: Path) -> b.BriefSpec:
    return b.resolve_briefer(read_config(project / "tagteam.yaml") or {}, project)


def _escalate(project: Path, phase="feat-x", how="ESCALATE"):
    """init cycle, reviewer escalates (or NEED_HUMAN)."""
    _init_cycle(project, phase=phase)
    cycle_mod.add_round(phase, "plan", "reviewer", how, 1, f"{how} because reasons",
                        str(project), updated_by="Codex")
    return state_mod.read_state(str(project))


def _auto_escalate(project: Path, phase="feat-x"):
    """Reach `escalated` via STALE_ROUND_LIMIT identical lead submissions."""
    _init_cycle(project, phase=phase)
    r = 1
    for _ in range(STALE_ROUND_LIMIT + 1):
        cycle_mod.add_round(phase, "plan", "reviewer", "REQUEST_CHANGES", r, "same objection",
                            str(project), updated_by="Codex")
        st = cycle_mod.read_status(phase, "plan", str(project))
        if st.get("state") == "escalated":
            return state_mod.read_state(str(project))
        r += 1
        cycle_mod.add_round(phase, "plan", "lead", "SUBMIT_FOR_REVIEW", r, "initial",
                            str(project), updated_by="Claude")
    raise AssertionError("did not auto-escalate")


def _briefs(project: Path):
    conn = db.connect(project_dir=str(project))
    try:
        return db.brief_history(conn)
    finally:
        conn.close()


def _run(project: Path, kind="auto", logs=None):
    return b.run_briefer(project, kind=kind, spec=_spec(project),
                         log=(logs.append if logs is not None else None))


# ---------------------------------------------------------------------------
# config / spec
# ---------------------------------------------------------------------------

class TestConfig:
    def test_absent_block_is_disabled(self, project):
        cfg = read_config(project / "tagteam.yaml")
        assert validate_briefer_config(cfg) == []
        assert get_briefer_spec(cfg)["enabled"] is False
        spec = b.resolve_briefer(cfg, project)
        assert not spec.enabled and spec.problems == []

    def test_enabled_defaults_to_lead_provider(self, project, fake_path):
        _enable(project)
        spec = _spec(project)
        assert spec.enabled and spec.provider == "claude" and spec.argv[0].startswith(str(fake_path))
        assert spec.timeout_s == 15 * 60

    @pytest.mark.parametrize("extra,needle", [
        ("  provider: gemini\n", "briefer.provider"),
        ("  args: '--model x'\n", "list of strings"),
        ("  bogus: 1\n", "unknown keys"),
        ("  enabled: yes-please\n", "briefer.enabled"),
        ("  timeout_minutes: 0\n", "timeout_minutes"),
    ])
    def test_invalid_block_disables_with_problem(self, project, fake_path, extra, needle):
        _enable(project, extra)
        spec = _spec(project)
        assert not spec.enabled and any(needle in p for p in spec.problems), spec.problems

    def test_missing_executable_disables(self, project, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        _enable(project)
        spec = _spec(project)
        assert not spec.enabled and any("not found" in p for p in spec.problems)

    def test_invalid_timeout_never_raises_and_brief_still_reports(self, project, fake_path, capsys):
        # resolve_briefer contract: never raises (disabled + garbage timeout)
        cfg = {"agents": {"lead": {"name": "Claude"}, "reviewer": {"name": "Codex"}},
               "briefer": {"enabled": False, "timeout_minutes": "nope"}}
        spec = b.resolve_briefer(cfg, project)
        assert not spec.enabled and spec.timeout_s == 15 * 60
        # `tagteam brief` on an escalated cycle with invalid config reports state, no crash
        _enable(project, "  timeout_minutes: nope\n")
        _escalate(project)
        assert b.brief_command([], project_root=project) == 1
        assert "No brief yet" in capsys.readouterr().out
        # every disabled/error return of resolve_briefer carries the fallback timeout
        for extra in ("  timeout_minutes: nope\n", "  provider: gemini\n", "  bogus: 1\n"):
            _enable(project, extra)
            spec = _spec(project)
            assert not spec.enabled and spec.timeout_s == 15 * 60, (extra, spec)
        # a live matching attempt aged between grace (5) and default timeout+grace (20)
        # must stay running when `tagteam brief` sweeps under invalid config
        _enable(project, "  timeout_minutes: nope\n")
        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(procs, "pid_alive", lambda pid: pid == 100)
            monkeypatch.setattr(procs, "identity", lambda pid: "100:start" if pid == 100 else None)
            ev, _ = b.current_event(project)
            started = (b.datetime.now(b.timezone.utc) - b.timedelta(minutes=10)).isoformat()
            conn = db.connect(project_dir=str(project))
            try:
                db.claim_brief(conn, ts=started, phase="feat-x", cycle_type="plan", round_=1,
                               cycle_state="escalated", event_key=ev.event_key, kind="manual",
                               runner_pid=100, runner_ident="100:start")
            finally:
                conn.close()
            assert b.brief_command([], project_root=project) == 1
            assert _briefs(project)[0]["status"] == "running"
        finally:
            monkeypatch.undo()

    def test_enabled_false(self, project, fake_path):
        _enable(project)
        (project / "tagteam.yaml").write_text(
            "agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\nbriefer:\n  enabled: false\n")
        assert not _spec(project).enabled

    def test_agents_errors_still_fatal_and_briefer_never_blocks(self, project, fake_path):
        from tagteam import watcher
        _enable(project, "  provider: gemini\n")   # invalid briefer → warn only
        logs = []
        orig = watcher._log; watcher._log = lambda m, **_kw: logs.append(m)   # Phase 67: _log takes kind=/context
        try:
            proc = watcher._build_processor(mode="notify", lead_pane="a", reviewer_pane="b",
                                            confirm=False, timeout_minutes=30,
                                            project_dir=str(project), max_retries=1,
                                            retry_delay=0, pre_send_delay=0)
        finally:
            watcher._log = orig
        assert proc is not None and proc.briefer is None
        assert any("briefer disabled" in l for l in logs)


# ---------------------------------------------------------------------------
# event identity + prompt
# ---------------------------------------------------------------------------

class TestEventAndPrompt:
    def test_event_from_canonical_status(self, project):
        _escalate(project, how="NEED_HUMAN")
        ev, why = b.current_event(project)
        assert ev and ev.cycle_state == "needs-human" and ev.action == "NEED_HUMAN"
        assert ev.event_key.startswith("feat-x|plan|1|reviewer|NEED_HUMAN|")
        assert ev.event_row_id is not None and ev.stamp
        # not escalated → None
        _init_cycle(project, phase="other")
        state_mod.update_state({"phase": "other", "type": "plan"}, str(project))
        ev2, why2 = b.current_event(project)
        assert ev2 is None and "not escalated" in why2

    def _mk_event(self, content="dispute text", n_rounds=2):
        return b.Event(phase="p", type="plan", round=n_rounds, cycle_state="escalated",
                       role="reviewer", action="ESCALATE", ts="2026-01-01T00:00:00+00:00",
                       content=content, event_key="p|plan|2|reviewer|ESCALATE|t", stamp="s")

    def _rounds(self, n, size=200):
        return [{"round": i, "entries": [
            {"role": "lead", "action": "SUBMIT_FOR_REVIEW", "ts": f"t{i}", "updated_by": "Claude",
             "content": "L" * size},
            {"role": "reviewer", "action": "REQUEST_CHANGES", "ts": f"t{i}", "updated_by": "Codex",
             "content": "R" * size}]} for i in range(1, n + 1)]

    def test_prompt_contents_and_forbidden(self):
        ev = self._mk_event()
        prompt, notices = b.compose_brief_prompt(event=ev, rounds=self._rounds(2), plan_text="PLAN",
                                                 interjections=[{"ts": "t", "by": "jack",
                                                                 "target_role": None, "note": "NOTE"}],
                                                 state={"phase": "p"}, output_path="/x/out.md")
        for hd in b.BRIEF_HEADINGS:
            assert hd in prompt
        assert "/x/out.md" in prompt and "dispute text" in prompt and "PLAN" in prompt and "NOTE" in prompt
        assert "must NOT run `tagteam cycle add`" in prompt
        assert "ROUND HISTORY (4 entries" in prompt and notices == []
        assert prompt.index("=== OUTPUT PATH ===") < prompt.index("=== ROUND HISTORY")

    def test_budget_forty_rounds_and_oversized_components(self):
        ev = self._mk_event(content="E" * 50_000)
        prompt, notices = b.compose_brief_prompt(
            event=ev, rounds=self._rounds(40, size=3000), plan_text="P" * 100_000,
            interjections=[{"ts": "t", "by": "j", "target_role": None, "note": "I" * 30_000}],
            state={"k": "V" * 20_000}, output_path="/x/out.md")
        assert len(prompt) <= b.TOTAL_BUDGET
        assert "chars elided" in prompt                      # escalation head+tail
        assert prompt.count("E") >= b.ESCALATION_MIN_HEAD + b.ESCALATION_MIN_TAIL
        assert "=== OUTPUT PATH ===" in prompt and all(hd in prompt for hd in b.BRIEF_HEADINGS)
        assert any("older entries abbreviated" in n for n in notices)

    def test_budget_boundary_framing(self):
        # component maxima that individually fit but sum above the total
        ev = self._mk_event(content="E" * 7_500)
        rounds = self._rounds(12, size=3_900)
        prompt, notices = b.compose_brief_prompt(
            event=ev, rounds=rounds, plan_text="P" * 19_500,
            interjections=[{"ts": "t", "by": "j", "target_role": None, "note": "I" * 3_500}],
            state={"k": "V" * 3_500}, output_path="/x/out.md")
        assert len(prompt) <= b.TOTAL_BUDGET
        assert "=== OUTPUT PATH ===" in prompt and "## Rulings" in prompt
        assert notices, "reductions must be announced"

    def test_escalation_entry_never_below_minimum(self):
        ev = self._mk_event(content="A" * 30_000)
        rounds = self._rounds(60, size=5_000)
        prompt, _ = b.compose_brief_prompt(event=ev, rounds=rounds, plan_text="P" * 50_000,
                                           interjections=[], state={}, output_path="/x/o.md")
        assert len(prompt) <= b.TOTAL_BUDGET
        assert prompt.count("A") >= b.ESCALATION_MIN_HEAD + b.ESCALATION_MIN_TAIL


# ---------------------------------------------------------------------------
# runner: outcomes, claim semantics, inflight, alias
# ---------------------------------------------------------------------------

class TestRunner:
    def test_ok_brief_end_to_end(self, project, fake_path, monkeypatch):
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief")
        _enable(project)
        _escalate(project)
        logs = []
        res = _run(project, logs=logs)
        assert res.status == "ok", (res.reason, logs)
        assert Path(res.path).exists() and res.path.endswith("-a1.md")
        rows = _briefs(project)
        assert len(rows) == 1 and rows[0]["status"] == "ok" and rows[0]["kind"] == "auto"
        assert rows[0]["attempt"] == 1 and rows[0]["event_key"] == res.event_key
        assert rows[0]["content"].startswith("<!-- fake brief")
        assert rows[0]["runner_pid"] == os.getpid() and rows[0]["stem"].endswith("_a1")
        u = _usage_rows(project)[-1]
        assert u["role"] == "briefer" and u["status"] == "ok" and u["input_tokens"] == 100
        alias = b.alias_path(project, "feat-x", "plan")
        assert alias.exists() and "fake brief" in alias.read_text()
        assert h.read_inflight(project) is None
        # dedupe: second automatic run is skipped (event already briefed)
        res2 = _run(project)
        assert res2.status == "skipped" and "already briefed" in res2.reason
        assert len(_briefs(project)) == 1

    def test_partial_and_failed_variants(self, project, fake_path, monkeypatch):
        _enable(project)
        _escalate(project)
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief_partial")
        res = _run(project)
        assert res.status == "partial" and "missing headings" in res.reason
        assert _briefs(project)[0]["status"] == "partial"
        # a partial success satisfies the event
        assert _run(project).status == "skipped"
        # fresh event: nofile → failed; no pause marker; diagnostic; alias stub
        _init_cycle(project, phase="p2")
        cycle_mod.add_round("p2", "plan", "reviewer", "ESCALATE", 1, "x", str(project), updated_by="Codex")
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief_nofile")
        res = _run(project)
        assert res.status == "failed" and "not written" in res.reason
        assert h.read_pause(project) is None
        assert "briefer_failed" in _diag_kinds(project)
        assert _usage_rows(project)[-1]["status"] == "no_round"
        stub = b.alias_path(project, "p2", "plan").read_text()
        assert "No brief yet" in stub and res.event_key in stub
        # failed automatic attempt is not retried automatically
        assert _run(project).status == "refused"
        # nonzero
        _init_cycle(project, phase="p3")
        cycle_mod.add_round("p3", "plan", "reviewer", "ESCALATE", 1, "x", str(project), updated_by="Codex")
        monkeypatch.setenv("FAKE_AGENT_MODE", "nonzero")
        assert _run(project).status == "failed"
        assert _usage_rows(project)[-1]["status"] == "nonzero_exit"

    def test_timeout(self, project, fake_path, monkeypatch):
        _enable(project, "  timeout_minutes: 0.15\n")
        _escalate(project)
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief_hang")
        res = _run(project)
        assert res.status == "failed" and "timeout" in res.reason
        assert _usage_rows(project)[-1]["status"] == "timeout"

    def test_manual_generate_after_failed_auto_and_attempt_numbering(self, project, fake_path,
                                                                    monkeypatch, capsys):
        _enable(project)
        _escalate(project)
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief_nofile")
        assert _run(project).status == "failed"
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief")
        assert b.brief_command(["--generate"], project_root=project) == 0
        rows = _briefs(project)
        assert [(r["kind"], r["attempt"], r["status"]) for r in reversed(rows)] == \
            [("auto", 1, "failed"), ("manual", 2, "ok")]
        assert rows[0]["path"].endswith("-a2.md")
        capsys.readouterr()
        # brief shows the manual success for the current event
        assert b.brief_command([], project_root=project) == 0
        assert "manual a2" in capsys.readouterr().out
        # manual first-then-auto: new event
        _init_cycle(project, phase="q")
        cycle_mod.add_round("q", "plan", "reviewer", "ESCALATE", 1, "x", str(project), updated_by="Codex")
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief_nofile")
        assert b.brief_command(["--generate"], project_root=project) == 1     # manual a1 failed
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief")
        res = _run(project)                                                   # auto may still run once → a2
        assert res.status == "ok" and res.attempt == 2 and res.path.endswith("-a2.md")
        # manual success satisfies the event: auto does not run
        _init_cycle(project, phase="r")
        cycle_mod.add_round("r", "plan", "reviewer", "ESCALATE", 1, "x", str(project), updated_by="Codex")
        assert b.brief_command(["--generate"], project_root=project) == 0
        assert _run(project).status == "skipped"

    def test_same_round_reescalation_is_new_event_and_new_file(self, project, fake_path, monkeypatch, capsys):
        _enable(project)
        _escalate(project, how="NEED_HUMAN")
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief")
        r1 = _run(project); assert r1.status == "ok"
        assert controls.rule_command(["answer", "--content", "go on", "--by", "jack"],
                                     project_root=project) == 0
        cycle_mod.add_round("feat-x", "plan", "reviewer", "NEED_HUMAN", 1, "again?", str(project),
                            updated_by="Codex")
        r2 = _run(project)
        assert r2.status == "ok" and r2.event_key != r1.event_key
        assert r2.path != r1.path and Path(r1.path).exists() and Path(r2.path).exists()
        # brief shows B (current), --list shows both
        capsys.readouterr()
        assert b.brief_command([], project_root=project) == 0
        out = capsys.readouterr().out
        assert r2.event_key in out and r1.event_key not in out.split("\n")[1]
        assert b.brief_command(["--list"], project_root=project) == 0
        assert capsys.readouterr().out.count("event=") == 2

    def test_brief_shows_current_event_state_never_older(self, project, fake_path, monkeypatch, capsys):
        _enable(project)
        _escalate(project, how="NEED_HUMAN")
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief")
        r1 = _run(project); assert r1.status == "ok"
        controls.rule_command(["answer", "--content", "ok", "--by", "jack"], project_root=project)
        cycle_mod.add_round("feat-x", "plan", "reviewer", "NEED_HUMAN", 1, "again", str(project),
                            updated_by="Codex")
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief_nofile")
        assert _run(project).status == "failed"
        capsys.readouterr()
        assert b.brief_command([], project_root=project) == 1
        out = capsys.readouterr().out
        assert "No brief yet for the current event" in out and "a1 failed" in out
        assert r1.path not in out
        conn = db.connect(project_dir=str(project))
        try:
            ev, _ = b.current_event(project)
            assert db.successful_brief_for_event(conn, ev.event_key) is None
            assert db.successful_brief_for_event(conn, r1.event_key)["path"] == r1.path
        finally:
            conn.close()
        stub = b.alias_path(project, "feat-x", "plan").read_text()
        assert "No brief yet" in stub and r1.path in stub
        assert b.brief_command(["--event", r1.event_key], project_root=project) == 0

    def test_concurrent_claims_one_wins(self, project, fake_path, monkeypatch):
        _enable(project)
        _escalate(project)
        conn_a = db.connect(project_dir=str(project)); conn_b = db.connect(project_dir=str(project))
        ev, _ = b.current_event(project)
        try:
            a = db.claim_brief(conn_a, ts="t1", phase="feat-x", cycle_type="plan", round_=1,
                               cycle_state="escalated", event_key=ev.event_key, kind="auto",
                               runner_pid=1, runner_ident="x")
            bb = db.claim_brief(conn_b, ts="t2", phase="feat-x", cycle_type="plan", round_=1,
                                cycle_state="escalated", event_key=ev.event_key, kind="auto",
                                runner_pid=2, runner_ident="y")
            m = db.claim_brief(conn_b, ts="t3", phase="feat-x", cycle_type="plan", round_=1,
                               cycle_state="escalated", event_key=ev.event_key, kind="manual",
                               runner_pid=2, runner_ident="y")
        finally:
            conn_a.close(); conn_b.close()
        assert a is not None and bb is None and m is None   # one auto; running blocks manual

    def test_crash_after_claim_is_abandoned_not_respawned(self, project, fake_path, monkeypatch):
        _enable(project)
        _escalate(project)
        ev, _ = b.current_event(project)
        conn = db.connect(project_dir=str(project))
        try:
            claim = db.claim_brief(conn, ts="2000-01-01T00:00:00+00:00", phase="feat-x",
                                   cycle_type="plan", round_=1, cycle_state="escalated",
                                   event_key=ev.event_key, kind="auto", runner_pid=999999,
                                   runner_ident="999999:dead")
            assert claim is not None
        finally:
            conn.close()
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief")
        res = _run(project)
        assert res.status == "refused"                       # never auto-respawn
        assert _briefs(project)[0]["status"] == "abandoned"
        # manual retry works and numbers a2
        assert b.brief_command(["--generate"], project_root=project) == 0
        assert _briefs(project)[0]["attempt"] == 2

    @needs_proc_inspection
    def test_live_manual_runner_not_misclassified(self, project, fake_path):
        _enable(project)
        _escalate(project)
        ev, _ = b.current_event(project)
        me = os.getpid()
        conn = db.connect(project_dir=str(project))
        try:
            # recent claim by a live runner (this process)
            db.claim_brief(conn, ts=b._now_iso(), phase="feat-x", cycle_type="plan",
                           round_=1, cycle_state="escalated", event_key=ev.event_key,
                           kind="manual", runner_pid=me, runner_ident=procs.identity(me))
            db.set_brief_stem(conn, 1, "live-stem")
        finally:
            conn.close()
        # (1) alive + within timeout → not abandoned, with or without a pointer
        assert b.sweep_abandoned(project, 60) == []
        h.turns_dir(project).mkdir(parents=True, exist_ok=True)
        h.inflight_path(project).write_text(json.dumps({"stem": "live-stem", "watcher_pid": me,
                                                        "watcher_ident": procs.identity(me)}))
        assert b.sweep_abandoned(project, 60) == []
        # (2) alive + past timeout+grace but pointer still binds to the stem → not abandoned
        conn = db.connect(project_dir=str(project))
        try:
            conn.execute("UPDATE briefs SET started_at='2000-01-01T00:00:00+00:00' WHERE id=1"); conn.commit()
        finally:
            conn.close()
        assert b.sweep_abandoned(project, 60) == []
        # (3) alive + past timeout+grace + no pointer for the stem → hung → abandoned (rule b)
        h.inflight_path(project).unlink()
        assert b.sweep_abandoned(project, 60) == [1]
        assert _briefs(project)[0]["status"] == "abandoned"
        # (4) dead runner → abandoned immediately (rule a)
        conn = db.connect(project_dir=str(project))
        try:
            db.claim_brief(conn, ts=b._now_iso(), phase="feat-x", cycle_type="plan",
                           round_=1, cycle_state="escalated", event_key=ev.event_key,
                           kind="manual", runner_pid=999999, runner_ident="999999:dead")
        finally:
            conn.close()
        assert b.sweep_abandoned(project, 60) == [2]

    @needs_proc_inspection
    def test_inflight_lifecycle_and_tail(self, project, fake_path, monkeypatch, capsys):
        _enable(project)
        _escalate(project)
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief")
        monkeypatch.setenv("FAKE_AGENT_SLEEP", "0.6")
        seen = {}
        stop = threading.Event()

        def sampler():
            while not stop.is_set():
                inf = h.read_inflight(project)
                if inf and inf.get("role") == "briefer" and inf.get("pid"):
                    seen.setdefault("inflight", inf)
                    if "tail" not in seen:
                        import io
                        buf = io.StringIO()
                        h.tail_command(["--no-follow", "--lines", "3"], project_root=project, out=buf)
                        seen["tail"] = buf.getvalue()
                time.sleep(0.05)
        th = threading.Thread(target=sampler, daemon=True); th.start()
        res = _run(project)
        stop.set(); th.join(2)
        assert res.status == "ok"
        assert "inflight" in seen and seen["inflight"]["watcher_pid"] == os.getpid()
        assert seen["inflight"]["child_ident"] and seen["inflight"]["event_key"] == res.event_key
        assert "briefer" in seen.get("tail", "") or res.stem in seen.get("tail", "")
        assert h.read_inflight(project) is None

    @needs_proc_inspection
    def test_busy_inflight_refuses_claim(self, project, fake_path, monkeypatch):
        _enable(project)
        _escalate(project)
        h.turns_dir(project).mkdir(parents=True, exist_ok=True)
        # live pointer: this test process as runner
        h.inflight_path(project).write_text(json.dumps({"stem": "turn-x", "watcher_pid": os.getpid(),
                                                        "watcher_ident": procs.identity(os.getpid()),
                                                        "pid": None}))
        res = _run(project)
        assert res.status == "refused" and "in flight" in res.reason
        assert _briefs(project) == []
        # stale pointer (dead runner) is removed and the claim proceeds
        h.inflight_path(project).write_text(json.dumps({"stem": "old", "watcher_pid": 999999,
                                                        "watcher_ident": "999999:x", "pid": None}))
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief")
        assert _run(project).status == "ok"

    def test_event_resolved_inside_lock_race(self, project, fake_path, monkeypatch):
        """A concurrent rule that closes the cycle right before the claim
        critical section must not be briefed (event resolved under the lock)."""
        _enable(project)
        _escalate(project)
        from tagteam import dualwrite
        real_lock = dualwrite.writer_lock
        state = {"done": False}
        from contextlib import contextmanager

        @contextmanager
        def racing_lock(root):
            with real_lock(root):
                if not state["done"]:
                    state["done"] = True
                    # arbiter approves while we hold the lock (simulates a
                    # rule that won the lock first)
                    cycle_mod.add_ruling("feat-x", "plan", "APPROVE", "closing", "jack", str(project))
                yield
        monkeypatch.setattr(dualwrite, "writer_lock", racing_lock)
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief")
        res = _run(project)
        assert res.status == "skipped" and "not escalated" in res.reason
        assert _briefs(project) == []

    @pytest.mark.parametrize("stage", ["setup", "compose", "finalize"])
    def test_fault_injection_never_strands_claim(self, project, fake_path, monkeypatch, stage):
        _enable(project)
        _escalate(project)
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief")
        with monkeypatch.context() as m:      # scoped fault: PATH/fake agent stay in place
            if stage == "setup":
                m.setattr(db, "set_brief_stem", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom-setup")))
            elif stage == "compose":
                m.setattr(b, "compose_brief_prompt", lambda **k: (_ for _ in ()).throw(RuntimeError("boom-compose")))
            else:
                m.setattr(b, "verify_brief_file", lambda p: (_ for _ in ()).throw(RuntimeError("boom-final")))
            res = _run(project)
        assert res.status == "failed" and "boom" in res.reason
        rows = _briefs(project)
        assert len(rows) == 1 and rows[0]["status"] == "failed" and "boom" in rows[0]["reason"]
        assert h.read_inflight(project) is None
        assert "briefer_failed" in _diag_kinds(project)
        # not stranded: a manual retry (still the fake agent) can proceed
        assert b.brief_command(["--generate"], project_root=project) == 0

    def test_alias_failure_does_not_change_status(self, project, fake_path, monkeypatch):
        _enable(project)
        _escalate(project)
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief")
        monkeypatch.setattr(b, "_write_alias", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
        res = _run(project)
        assert res.status == "ok" and _briefs(project)[0]["status"] == "ok"

    def test_brief_sweep_uses_configured_timeout(self, project, fake_path, monkeypatch, capsys):
        # hermetic process identity: the runner "100" is alive with a stable identity
        monkeypatch.setattr(procs, "pid_alive", lambda pid: pid == 100)
        monkeypatch.setattr(procs, "identity", lambda pid: "100:start" if pid == 100 else None)
        _enable(project, "  timeout_minutes: 120\n")
        _escalate(project)
        ev, _ = b.current_event(project)
        started = (b.datetime.now(b.timezone.utc) - b.timedelta(minutes=30)).isoformat()
        conn = db.connect(project_dir=str(project))
        try:
            db.claim_brief(conn, ts=started, phase="feat-x", cycle_type="plan", round_=1,
                           cycle_state="escalated", event_key=ev.event_key, kind="manual",
                           runner_pid=100, runner_ident="100:start")
        finally:
            conn.close()
        # 30 min old, configured timeout 120 → still running (a 15-min default would abandon it)
        assert b.brief_command([], project_root=project) == 1
        assert _briefs(project)[0]["status"] == "running"
        assert "a1 running" in capsys.readouterr().out
        # shorter configured timeout → abandoned
        _enable(project, "  timeout_minutes: 1\n")
        assert b.brief_command([], project_root=project) == 1
        assert _briefs(project)[0]["status"] == "abandoned"

    def test_db_invalid_skips(self, project, fake_path, monkeypatch):
        from tagteam import dualwrite
        _enable(project)
        _escalate(project)
        monkeypatch.setattr(dualwrite, "is_db_invalid", lambda p: True)
        res = _run(project)
        assert res.status == "skipped" and res.reason == "db_invalid"

    def test_disabled_writes_nothing(self, project, fake_path):
        _escalate(project)   # no briefer block
        res = _run(project)
        assert res.status == "skipped"
        assert not b.escalations_dir(project).exists()
        assert _briefs(project) == []


# ---------------------------------------------------------------------------
# rule
# ---------------------------------------------------------------------------

class TestRule:
    def test_request_changes_after_auto_escalation(self, project, capsys):
        st = _auto_escalate(project)
        assert cycle_mod.read_status("feat-x", "plan", str(project))["state"] == "escalated"
        assert controls.rule_command(["request-changes", "--content", "do X", "--by", "jack"],
                                     project_root=project) == 0
        status = cycle_mod.read_status("feat-x", "plan", str(project))
        assert (status["state"], status["ready_for"]) == ("in-progress", "lead")
        top = state_mod.read_state(str(project))
        assert top["status"] == "ready" and top["turn"] == "lead" and top["updated_by"] == "jack"
        rows = cycle_mod.tail_rounds("feat-x", "plan", 1, str(project))
        assert rows[-1]["rulings"] and rows[-1]["rulings"][0]["content"].startswith("[ARBITER RULING by jack]")
        assert any(e["action"] == "REQUEST_CHANGES" for e in rows[-1]["entries"])

    def test_approve_after_explicit_escalate_and_capture_before_append(self, project, fake_path,
                                                                       monkeypatch, capsys):
        _enable(project)
        _escalate(project)
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief")
        r = _run(project); assert r.status == "ok"
        assert controls.rule_command(["approve", "--by", "jack"], project_root=project) == 0
        out = capsys.readouterr().out
        assert f"Acted on brief #{r.brief_id}" in out
        assert cycle_mod.read_status("feat-x", "plan", str(project))["state"] == "approved"
        conn = db.connect(project_dir=str(project))
        try:
            d = json.loads(conn.execute(
                "SELECT payload_json FROM diagnostics WHERE kind='arbiter_ruling' ORDER BY id DESC LIMIT 1"
            ).fetchone()[0])
        finally:
            conn.close()
        assert d["event_key"] == r.event_key and d["brief_id"] == r.brief_id and d["ruling"] == "approve"
        # both the ESCALATE and the ruling are visible in grouped rounds
        rows = cycle_mod.tail_rounds("feat-x", "plan", None, str(project))
        actions = [e["action"] for e in rows[-1]["entries"]]
        assert "ESCALATE" in actions and "APPROVE" in actions
        assert rows[-1]["reviewer_action"] if "reviewer_action" in rows[-1] else True
        # invalid state now
        assert controls.rule_command(["approve"], project_root=project) == 1

    def test_answer_rearms_and_records_interjection(self, project, capsys):
        _escalate(project, how="NEED_HUMAN")
        assert controls.rule_command(["answer", "--content", "yes, do it", "--by", "jack"],
                                     project_root=project) == 0
        status = cycle_mod.read_status("feat-x", "plan", str(project))
        assert (status["state"], status["ready_for"]) == ("in-progress", "reviewer")
        top = state_mod.read_state(str(project))
        assert top["turn"] == "reviewer" and top["status"] == "ready"
        conn = db.connect(project_dir=str(project))
        try:
            notes = db.pending_interjections_for(conn, "reviewer", "feat-x", "plan")
            assert notes and "yes, do it" in notes[0]["note"] and notes[0]["target_role"] == "reviewer"
            assert not db.pending_interjections_for(conn, "lead", "feat-x", "plan")
        finally:
            conn.close()
        # second NEED_HUMAN at the same round: both visible in entries
        cycle_mod.add_round("feat-x", "plan", "reviewer", "NEED_HUMAN", 1, "and this?", str(project),
                            updated_by="Codex")
        rows = cycle_mod.tail_rounds("feat-x", "plan", None, str(project))
        assert [e["action"] for e in rows[-1]["entries"]].count("NEED_HUMAN") == 2
        assert controls.rule_command(["answer", "--to", "lead", "--content", "lead continues"],
                                     project_root=project) == 0
        assert state_mod.read_state(str(project))["turn"] == "lead"

    def test_rule_rejections(self, project, capsys):
        _init_cycle(project)
        assert controls.rule_command(["approve"], project_root=project) == 1
        assert "Nothing to rule on" in capsys.readouterr().out
        assert controls.rule_command(["request-changes"], project_root=project) == 1   # content required
        assert controls.rule_command(["bogus"], project_root=project) == 1
        assert controls.rule_command(["approve", "--to", "lead"], project_root=project) == 1
        assert controls.rule_command([], project_root=project) == 1
        with pytest.raises(ValueError):
            cycle_mod.add_ruling("feat-x", "plan", "APPROVE", "x", "jack", str(project))
        with pytest.raises(ValueError):
            cycle_mod.rearm("feat-x", "plan", "lead", "jack", str(project))


# ---------------------------------------------------------------------------
# repair preservation + schema
# ---------------------------------------------------------------------------

class TestRepairAndSchema:
    def test_schema_v5(self, tmp_path):
        c = db.connect(project_dir=str(tmp_path))
        # v5 objects are present (Phase 34 bumped user_version to 6, additively).
        assert c.execute("PRAGMA user_version").fetchone()[0] >= 5
        assert c.execute("SELECT name FROM sqlite_master WHERE name='uq_briefs_auto'").fetchone()
        assert c.execute("SELECT name FROM sqlite_master WHERE name='uq_briefs_running'").fetchone()
        with pytest.raises(ValueError):
            db.claim_brief(c, ts="t", phase="p", cycle_type="plan", round_=1, cycle_state="approved",
                           event_key="k", kind="auto", runner_pid=1, runner_ident="x")
        c.close()

    def test_repair_aborts_if_snapshot_fails(self, project, fake_path, monkeypatch):
        _enable(project)
        _escalate(project)
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief")
        assert _run(project).status == "ok"
        before = _briefs(project)
        monkeypatch.setattr(db, "snapshot_non_file_backed",
                            lambda conn: (_ for _ in ()).throw(RuntimeError("cannot read")))
        res = repair.rebuild_db_from_files_and_verify(project)
        assert not res["success"] and "snapshot" in res["reason"]
        assert (project / ".tagteam" / "tagteam.db").exists()
        assert _briefs(project) == before             # original DB + audit rows intact

    def test_repair_preserves_non_file_backed_tables(self, project, fake_path, monkeypatch):
        _enable(project)
        _escalate(project)
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief")
        r = _run(project); assert r.status == "ok"
        controls.interject_command(["keep me"], project_root=project)
        before = _briefs(project)
        usage_before = _usage_rows(project)
        res = repair.rebuild_db_from_files_and_verify(project)
        assert res["success"], res
        res["conn"].close()
        after = _briefs(project)
        assert [(x["id"], x["status"], x["event_key"]) for x in after] == \
            [(x["id"], x["status"], x["event_key"]) for x in before]
        assert len(_usage_rows(project)) == len(usage_before)
        conn = db.connect(project_dir=str(project))
        try:
            assert db.get_interjections(conn)[0]["note"] == "keep me"
            ev, _ = b.current_event(project)
            assert db.successful_brief_for_event(conn, ev.event_key)["id"] == r.brief_id
        finally:
            conn.close()
        # dedupe still holds after repair
        assert _run(project).status == "skipped"


# ---------------------------------------------------------------------------
# watcher trigger / bootstrap / flag-off
# ---------------------------------------------------------------------------

class TestWatcherTrigger:
    def _proc(self, project, briefer):
        from tagteam.watcher import _StateProcessor
        return _StateProcessor(mode="notify", lead_name="Claude", reviewer_name="Codex",
                               lead_pane="l", reviewer_pane="r", lead_session_id=None,
                               reviewer_session_id=None, confirm=False, timeout_minutes=30,
                               project_dir=str(project), max_retries=1, retry_delay=0,
                               pre_send_delay=0, briefer=briefer)

    def test_escalated_state_briefs_once_and_bootstrap(self, project, fake_path, monkeypatch):
        _enable(project)
        st = _escalate(project)
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief")
        p = self._proc(project, _spec(project))
        with patch("tagteam.watcher.notify_macos"):
            p.tick(dict(st, seq=st["seq"]))          # first-poll bootstrap on escalated → one brief
            assert len(_briefs(project)) == 1
            p.tick(dict(st, seq=st["seq"]))          # same seq → nothing
            p.tick(dict(st, seq=st["seq"] + 1))      # re-tick new seq, same event → dedupe
        assert len(_briefs(project)) == 1
        # restart with completed record → none
        p2 = self._proc(project, _spec(project))
        with patch("tagteam.watcher.notify_macos"):
            p2.tick(dict(st, seq=st["seq"] + 2))
        assert len(_briefs(project)) == 1

    def test_roadmap_pause_and_disabled_do_nothing(self, project, fake_path, monkeypatch):
        _enable(project)
        st = _escalate(project)
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief")
        p = self._proc(project, _spec(project))
        with patch("tagteam.watcher.notify_macos"):
            p.tick(dict(st, roadmap={"pause_reason": "advance failed"}))
        assert _briefs(project) == []
        p3 = self._proc(project, None)               # disabled (0.9.0 behavior)
        with patch("tagteam.watcher.notify_macos") as n:
            p3.tick(dict(st, seq=st["seq"] + 4, status="working"))   # bootstrap on a non-escalated state
            p3.tick(dict(st, seq=st["seq"] + 5))                     # then the escalation arrives
        n.assert_called()                            # existing escalation notification still fires
        assert _briefs(project) == [] and not b.escalations_dir(project).exists()

    def test_pre_010_config_flag_off_compat(self, project, fake_path):
        # 0.9.0-era config: no briefer block → processor.briefer is None
        from tagteam import watcher
        proc = watcher._build_processor(mode="notify", lead_pane="a", reviewer_pane="b",
                                        confirm=False, timeout_minutes=30,
                                        project_dir=str(project), max_retries=1,
                                        retry_delay=0, pre_send_delay=0)
        assert proc is not None and proc.briefer is None

    def test_repair_preserves_rate_limits(self, project, fake_path, monkeypatch):
        """Phase 34: `rate_limits` is non-file-backed and survives a rebuild."""
        _enable(project)
        _escalate(project)
        monkeypatch.setenv("FAKE_AGENT_MODE", "brief")
        r = _run(project); assert r.status == "ok"        # claude fake emits the five_hour frame
        conn = db.connect(project_dir=str(project))
        try:
            before = db.latest_rate_limits(conn)
            db.upsert_rate_limit(conn, provider="claude", kind="seven_day", status="allowed",
                                 resets_at=None, payload={"k": 1}, ts="t")
            before = db.latest_rate_limits(conn)
        finally:
            conn.close()
        assert [x["kind"] for x in before] == ["five_hour", "seven_day"]
        res = repair.rebuild_db_from_files_and_verify(project)
        assert res["success"], res
        res["conn"].close()
        conn = db.connect(project_dir=str(project))
        try:
            after = db.latest_rate_limits(conn)
        finally:
            conn.close()
        assert [(x["id"], x["kind"], x["status"], x["resets_at"], x["payload"]) for x in after] == \
            [(x["id"], x["kind"], x["status"], x["resets_at"], x["payload"]) for x in before]
