"""Phase 39 tests: reviewer panels — config, spec resolution, lens prompt,
verdict validation, deterministic merge, run_panel with the fake agent
(merged / fallback / superseded / rogue / deferred / error / attempts
exhausted), terminal claim policy, interjection snapshot + crash-safe
delivery, sweep, watcher seam + latch in every mode (after the gate),
CLI, docs, flag-off."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tagteam import cycle as cycle_mod
from tagteam import db, headless as h, procs
from tagteam import panel as pnl
from tagteam import state as state_mod
from tagteam.config import validate_panel_config, get_panel_spec

from tests.test_headless import project, fake_path, _init_cycle, SKILL_SRC  # noqa: F401

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _enable(project: Path, extra: str = "", lenses: list | None = None, on: str = "[impl]") -> None:
    body = ("agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\n"
            f"panel:\n  enabled: true\n  on: {on}\n")
    if lenses is not None:
        body += "  lenses:\n" + "".join(f"    - {l}\n" for l in lenses)
    (project / "tagteam.yaml").write_text(body + extra, encoding="utf-8")


def _spec(project: Path) -> pnl.PanelSpec:
    return pnl.load_spec(project)


def _open_impl(project: Path, phase="feat-x"):
    _init_cycle(project, phase=phase)
    cycle_mod.add_round(phase, "plan", "reviewer", "APPROVE", 1, "ok", str(project), updated_by="Codex")
    (project / "src.py").write_text("x = 1\n", encoding="utf-8")
    cycle_mod.init_cycle(phase, "impl", "Claude", "Codex", "impl v1", str(project), updated_by="Claude")
    (project / "docs" / "phases").mkdir(parents=True, exist_ok=True)
    (project / "docs" / "phases" / f"{phase}.md").write_text("# plan\n- do x\n", encoding="utf-8")
    return state_mod.read_state(str(project))


def _state(project: Path) -> dict:
    return state_mod.read_state(str(project)) or {}


def _rows(project: Path, phase="feat-x", ctype="impl") -> list[dict]:
    conn = db.connect(project_dir=str(project))
    try:
        return db.panels_for_cycle(conn, phase, ctype)
    finally:
        conn.close()


def _entries(project: Path, phase="feat-x", ctype="impl") -> list[dict]:
    return [e for e in cycle_mod.read_rounds_file(phase, ctype, str(project)) if e.get("panel_event")]


def _usage(project: Path) -> list[dict]:
    conn = db.connect(project_dir=str(project))
    try:
        return db.get_usage(conn)
    finally:
        conn.close()


def _run(project: Path, **kw) -> pnl.PanelResult:
    return pnl.run_panel(str(project), spec=kw.pop("spec", None) or _spec(project), **kw)


def _verdicts(monkeypatch, table: dict | None = None, default: str | None = None):
    monkeypatch.setenv("FAKE_AGENT_MODE", "panel")
    monkeypatch.setenv("FAKE_AGENT_SLEEP", "0.02")
    if table is not None:
        monkeypatch.setenv("FAKE_PANEL_VERDICTS", json.dumps(table))
    else:
        monkeypatch.delenv("FAKE_PANEL_VERDICTS", raising=False)
    if default is not None:
        monkeypatch.setenv("FAKE_PANEL_VERDICT", default)
    else:
        monkeypatch.delenv("FAKE_PANEL_VERDICT", raising=False)


@pytest.fixture
def paneled(project, fake_path, monkeypatch):
    """panel enabled (fake codex reviewer), impl submission reviewer-ready,
    every lens approves by default."""
    _enable(project)
    _open_impl(project)
    _verdicts(monkeypatch, default="approve")
    return project


def _proc(mode, project, spec, engine=None, gate=None):
    from tagteam.watcher import _StateProcessor
    return _StateProcessor(mode=mode, lead_name="Claude", reviewer_name="Codex", lead_pane="l", reviewer_pane="r",
                           lead_session_id="ls" if mode == "iterm2" else None,
                           reviewer_session_id="rs" if mode == "iterm2" else None, confirm=False,
                           timeout_minutes=30, project_dir=str(project), max_retries=1, retry_delay=0,
                           pre_send_delay=0, engine=engine, gatekeeper=gate, panel=spec)


MODES = [("notify", "tagteam.watcher.notify_macos"), ("tmux", "tagteam.watcher.send_tmux_keys"),
         ("iterm2", "tagteam.watcher.send_iterm_command")]


# ---------------------------------------------------------------------------
# config + spec
# ---------------------------------------------------------------------------

class TestConfig:
    def test_defaults_and_absent(self):
        assert validate_panel_config({}) == []
        s = get_panel_spec({})
        assert s["enabled"] is False and s["on"] == ["impl"] and [l["name"] for l in s["lenses"]] == \
            ["correctness", "scope", "verification"]

    @pytest.mark.parametrize("block,needle", [
        ({"enabled": "yes"}, "enabled"), ({"enabled": True, "on": "impl"}, "on"),
        ({"enabled": True, "on": ["review"]}, "on"), ({"enabled": True, "phases": "x"}, "phases"),
        ({"enabled": True, "lenses": ["a"]}, "2–3"), ({"enabled": True, "lenses": ["a", "b", "c", "d"]}, "2–3"),
        ({"enabled": True, "lenses": ["a", "a"]}, "unique"), ({"enabled": True, "lenses": ["Bad Name", "b"]}, "lens names"),
        ({"enabled": True, "lenses": [{"name": "a", "brief": ""}, "b"]}, "brief"),
        ({"enabled": True, "lenses": [{"name": "a", "bogus": 1}, "b"]}, "unknown"),
        ({"enabled": True, "nope": 1}, "unknown"),
    ])
    def test_invalid(self, block, needle):
        problems = validate_panel_config({"panel": block})
        assert problems and any(needle in p for p in problems), problems

    def test_resolve_builtin_override_config_and_missing(self, project, fake_path):
        _enable(project)
        spec = _spec(project)
        assert spec.enabled and spec.provider == "codex" and spec.reviewer_name == "Codex"
        assert [l.source for l in spec.lenses] == ["builtin"] * 3 and spec.timeout_s == 60 * 60.0
        # override
        ov = project / ".tagteam" / "panels" / "lenses"; ov.mkdir(parents=True)
        (ov / "scope.md").write_text("# my scope brief\n")
        assert [l.source for l in _spec(project).lenses] == ["builtin", "override", "builtin"]
        # config brief + missing built-in
        (project / "my-lens.md").write_text("# custom\n")
        _enable(project, lenses=["correctness", "{name: custom, brief: my-lens.md}"])
        spec = _spec(project)
        assert spec.enabled and [(l.name, l.source) for l in spec.lenses] == [("correctness", "builtin"), ("custom", "config")]
        _enable(project, lenses=["correctness", "nothing"])
        spec = _spec(project)
        assert not spec.enabled and any("brief not found" in p for p in spec.problems)

    def test_reviewer_must_validate_for_headless(self, project, monkeypatch):
        monkeypatch.setenv("PATH", str(project / "empty-bin"))
        (project / "empty-bin").mkdir()
        _enable(project)
        spec = _spec(project)
        assert not spec.enabled and spec.problems
        (project / "tagteam.yaml").write_text("agents:\n  lead:\n    name: A\n  reviewer:\n    name: B\npanel:\n  enabled: true\n")
        spec = _spec(project)
        assert not spec.enabled and any("headless" in p for p in spec.problems)

    def test_applies_to_phases_allowlist(self, project, fake_path):
        _enable(project, extra="  phases: [feat-x]\n")
        spec = _spec(project)
        assert spec.applies_to("feat-x", "impl") and not spec.applies_to("other", "impl") \
            and not spec.applies_to("feat-x", "plan")


# ---------------------------------------------------------------------------
# verdicts + merge
# ---------------------------------------------------------------------------

class TestVerdictAndMerge:
    def _w(self, tmp_path, obj):
        p = tmp_path / "v.json"
        p.write_text(json.dumps(obj) if not isinstance(obj, str) else obj)
        return p

    def test_verify_verdict(self, tmp_path):
        assert pnl.verify_verdict(tmp_path / "missing.json")[0] is None
        assert "JSON" in pnl.verify_verdict(self._w(tmp_path, "{nope"))[1]
        assert pnl.verify_verdict(self._w(tmp_path, {"verdict": "MAYBE"}))[0] is None
        assert "blocker/major" in pnl.verify_verdict(self._w(tmp_path, {"verdict": "REQUEST_CHANGES", "findings": []}))[1]
        assert "cannot carry" in pnl.verify_verdict(self._w(tmp_path, {"verdict": "APPROVE", "findings": [{"title": "t", "severity": "blocker"}]}))[1]
        assert "question" in pnl.verify_verdict(self._w(tmp_path, {"verdict": "NEED_HUMAN", "summary": "s"}))[1]
        assert "reason" in pnl.verify_verdict(self._w(tmp_path, {"verdict": "ESCALATE", "summary": " "}))[1]
        assert "severity" in pnl.verify_verdict(self._w(tmp_path, {"verdict": "REQUEST_CHANGES", "findings": [{"title": "t", "severity": "huge"}]}))[1]
        v, r = pnl.verify_verdict(self._w(tmp_path, {"verdict": "REQUEST_CHANGES", "summary": "s",
                                                     "findings": [{"title": "t", "detail": "d", "where": "f:1", "severity": "major"}]}))
        assert r == "ok" and v["findings"][0]["where"] == "f:1"
        v, _ = pnl.verify_verdict(self._w(tmp_path, {"verdict": "NEED_HUMAN", "question": "which?"}))
        assert v["question"] == "which?"

    def _lr(self, lens, verdict=None, findings=None, summary="", question=None, failed=None):
        if failed:
            return pnl.LensResult(lens, "failed", None, failed)
        return pnl.LensResult(lens, "ok", {"verdict": verdict, "summary": summary or f"{lens} summary",
                                          "findings": findings or [], "question": question})

    def test_merge_matrix(self):
        order = ["correctness", "scope", "verification"]
        A = lambda l: self._lr(l, "APPROVE")
        R = lambda l, sev="major": self._lr(l, "REQUEST_CHANGES", [{"title": f"{l} bad", "detail": "d", "where": None, "severity": sev}])
        # all approve
        m = pnl.merge([A("correctness"), A("scope"), A("verification")], order)
        assert m["decision"] == "APPROVE" and m["content"].startswith("PANEL: APPROVE — correctness: APPROVE | scope: APPROVE | verification: APPROVE")
        # one requests changes → grouped, blockers first, approved lenses noted
        m = pnl.merge([A("correctness"), R("scope", "blocker"), A("verification")], order)
        assert m["decision"] == "REQUEST_CHANGES"
        assert m["content"].splitlines()[0] == "PANEL: REQUEST_CHANGES — correctness: APPROVE | scope: REQUEST_CHANGES (1 blocker) | verification: APPROVE"
        assert "## scope\n1. [blocker] scope bad" in m["content"] and "## correctness — approved" in m["content"]
        # all approve but one failed → fallback (never partial approval)
        m = pnl.merge([A("correctness"), A("scope"), self._lr("verification", failed="timeout")], order)
        assert m["decision"] is None and m["fallback"] and "never approve on a partial panel" in m["reason"]
        # objection + failed → REQUEST_CHANGES with the failed lens named
        m = pnl.merge([R("correctness"), self._lr("scope", failed="no verdict file written"), A("verification")], order)
        assert m["decision"] == "REQUEST_CHANGES" and "scope: lens failed (no verdict file written)" in m["content"] \
            and "## scope — lens failed" in m["content"]
        # all failed → fallback
        m = pnl.merge([self._lr("correctness", failed="x"), self._lr("scope", failed="y")], order)
        assert m["decision"] is None and "every lens failed" in m["reason"]
        # precedence + tie order (configured order leads)
        m = pnl.merge([R("correctness"), self._lr("scope", "ESCALATE", summary="scope escalates"),
                       self._lr("verification", "NEED_HUMAN", question="verification asks?")], order)
        assert m["decision"] == "NEED_HUMAN" and m["content"].splitlines()[1:3] == ["## verification — NEED_HUMAN", "verification asks?"]
        m = pnl.merge([self._lr("verification", "ESCALATE", summary="v reason"), self._lr("scope", "ESCALATE", summary="s reason")], order)
        assert m["content"].splitlines()[1] == "## scope — ESCALATE" and "## verification — ESCALATE" in m["content"]   # configured order
        # empty
        assert pnl.merge([], order)["fallback"]


# ---------------------------------------------------------------------------
# run_panel
# ---------------------------------------------------------------------------

class TestRunPanel:
    def test_all_approve_merges_one_entry(self, paneled):
        before = _state(paneled)
        res = _run(paneled)
        assert res.status == "merged" and res.dispatch is False and res.decision == "APPROVE"
        ents = _entries(paneled)
        assert len(ents) == 1
        e = ents[0]
        assert e["role"] == "reviewer" and e["action"] == "APPROVE" and e["updated_by"] == "Codex panel"
        assert e["panel_event"] == res.event_key and e["panel_id"] == res.panel_id
        assert [l["lens"] for l in e["panel_lenses"]] == ["correctness", "scope", "verification"]
        assert e["content"].startswith("PANEL: APPROVE — correctness: APPROVE | scope: APPROVE | verification: APPROVE")
        st = _state(paneled)
        assert st["status"] == "done" and st["seq"] == before["seq"] + 1 and st["updated_by"] == "Codex panel"
        assert cycle_mod.read_status("feat-x", "impl", str(paneled))["state"] == "approved"
        rows = _rows(paneled)
        assert len(rows) == 1 and rows[0]["status"] == "merged" and rows[0]["decision"] == "APPROVE" \
            and rows[0]["applied_seq"] == st["seq"] and rows[0]["kind"] == "auto"
        lenses = json.loads(rows[0]["lenses_json"])
        assert [l["outcome"] for l in lenses] == ["ok"] * 3
        # usage per lens
        kinds = sorted(u["kind"] for u in _usage(paneled) if u.get("kind"))
        assert kinds == ["panel:correctness", "panel:scope", "panel:verification"]
        assert all(u["role"] == "reviewer" and u["agent"] == "Codex" for u in _usage(paneled) if u.get("kind"))
        # files
        d = Path(paneled) / ".tagteam" / "panels" / rows[0]["stem"]
        assert (d / "scope.prompt").exists() and (d / "scope.verdict.json").exists() and (d / "scope.log").exists()
        assert not h.slot_status(paneled)["held"]
        # DB mirror + render parity
        conn = db.connect(project_dir=str(paneled))
        try:
            assert [r["role"] for r in db.get_rounds(conn, "feat-x", "impl")] == ["lead", "reviewer"]
            assert db.get_rounds(conn, "feat-x", "impl")[-1]["updated_by"] == "Codex panel"
            assert db.render_cycle(conn, "feat-x", "impl") == cycle_mod.render_cycle_from_files("feat-x", "impl", str(paneled))
        finally:
            conn.close()

    def test_request_changes_grouped_turn_to_lead(self, paneled, monkeypatch):
        _verdicts(monkeypatch, {"correctness": "approve", "scope": "request-changes", "verification": "request-changes"})
        before = _state(paneled)
        res = _run(paneled)
        assert res.status == "merged" and res.decision == "REQUEST_CHANGES"
        st = _state(paneled)
        assert st["turn"] == "lead" and st["status"] == "ready" and st["seq"] == before["seq"] + 1
        c = _entries(paneled)[0]["content"]
        assert c.splitlines()[0] == ("PANEL: REQUEST_CHANGES — correctness: APPROVE | scope: REQUEST_CHANGES (1 major) | "
                                     "verification: REQUEST_CHANGES (1 major)")
        assert "## scope\n1. [major] scope finding" in c and "## verification\n1. [major] verification finding" in c
        assert "## correctness — approved" in c
        # decided → the second call does not re-run and does not dispatch
        n = len(_usage(paneled))
        res2 = _run(paneled)
        assert res2.status == "not-ready" and res2.dispatch is False and len(_usage(paneled)) == n

    def test_escalate_and_need_human_transitions(self, paneled, monkeypatch):
        _verdicts(monkeypatch, {"correctness": "approve", "scope": "escalate", "verification": "approve"})
        res = _run(paneled)
        assert res.decision == "ESCALATE" and cycle_mod.read_status("feat-x", "impl", str(paneled))["state"] == "escalated"
        assert _entries(paneled)[0]["content"].splitlines()[1:3] == ["## scope — ESCALATE", "scope cannot decide: needs the arbiter"]
        # ruling re-arms; a new submission; NEED_HUMAN
        cycle_mod.add_ruling("feat-x", "impl", "REQUEST_CHANGES", "fix", "Jack", str(paneled))
        cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", 2, "again", str(paneled), updated_by="Claude")
        _verdicts(monkeypatch, {"correctness": "need-human", "scope": "approve", "verification": "approve"})
        res = _run(paneled)
        assert res.decision == "NEED_HUMAN" and cycle_mod.read_status("feat-x", "impl", str(paneled))["state"] == "needs-human"
        assert "correctness: which behaviour did you intend?" in _entries(paneled)[-1]["content"]

    def test_fallback_when_a_lens_fails_and_none_object(self, paneled, monkeypatch):
        _verdicts(monkeypatch, {"correctness": "approve", "scope": "no-file", "verification": "approve"})
        before = _state(paneled)
        res = _run(paneled)
        assert res.status == "fallback" and res.dispatch is True and "never approve on a partial panel" in res.reason
        assert _entries(paneled) == [] and _state(paneled) == before
        rows = _rows(paneled)
        assert rows[0]["status"] == "fallback" and rows[0]["decision"] is None
        assert [l["outcome"] for l in json.loads(rows[0]["lenses_json"])] == ["ok", "failed", "ok"]
        assert not h.slot_status(paneled)["held"]
        # decided fallback: later calls dispatch the ordinary reviewer without re-running
        n = len(_usage(paneled))
        res2 = _run(paneled)
        assert res2.status == "fallback" and res2.dispatch is True and len(_usage(paneled)) == n

    @pytest.mark.parametrize("beh,reason", [("bad-json", "JSON"), ("bad-shape", "verdict must be"),
                                            ("need-human-noq", "question"), ("approve-with-blocker", "cannot carry")])
    def test_nonconforming_lens_is_failed(self, paneled, monkeypatch, beh, reason):
        _verdicts(monkeypatch, {"correctness": "request-changes", "scope": beh, "verification": "approve"})
        res = _run(paneled)
        assert res.status == "merged" and res.decision == "REQUEST_CHANGES"
        lens = [l for l in res.lenses if l["lens"] == "scope"][0]
        assert lens["outcome"] == "failed" and reason.lower() in lens["reason"].lower()
        assert "scope: lens failed" in _entries(paneled)[0]["content"]

    def test_nonzero_exit_with_conforming_verdict_counts(self, paneled, monkeypatch):
        """A lens that exits non-zero but wrote a conforming verdict is `ok`
        (the plan: nonzero WITHOUT a conforming file is the failure)."""
        _verdicts(monkeypatch, {"correctness": "approve", "scope": "nonzero", "verification": "approve"})
        res = _run(paneled)
        assert res.status == "merged" and res.decision == "APPROVE"
        lens = [l for l in res.lenses if l["lens"] == "scope"][0]
        assert lens["outcome"] == "ok" and lens["exit_code"] == 3

    def test_all_failed_fallback(self, paneled, monkeypatch):
        _verdicts(monkeypatch, default="no-file")
        res = _run(paneled)
        assert res.status == "fallback" and res.dispatch and "every lens failed" in res.reason

    def test_rogue_lens_write_is_refused(self, paneled, monkeypatch, tmp_path):
        """Phase 50: the lens child runs read-only, so a rogue `cycle add` is
        refused by the CLI (exit 2) before it touches the cycle. The lens then
        has no verdict, so the panel falls back to the ordinary reviewer turn."""
        _verdicts(monkeypatch, {"correctness": "rogue", "scope": "approve", "verification": "approve"})
        rogue_out = tmp_path / "rogue.json"
        monkeypatch.setenv("FAKE_ROGUE_OUT", str(rogue_out))
        before = cycle_mod.read_rounds_file("feat-x", "impl", str(paneled))
        res = _run(paneled)
        attempt = json.loads(rogue_out.read_text(encoding="utf-8"))
        assert attempt["rc"] == 2 and "tagteam: refused" in attempt["stderr"]
        assert res.status == "fallback"
        assert cycle_mod.read_rounds_file("feat-x", "impl", str(paneled)) == before
        assert _state(paneled)["turn"] == "reviewer"

    def test_rogue_lens_superseded(self, paneled, monkeypatch):
        """The post-hoc net: a write through a path the switch does not cover
        (simulated by a lens that clears it) is still detected and supersedes."""
        _verdicts(monkeypatch, {"correctness": "rogue-unset", "scope": "approve", "verification": "approve"})
        res = _run(paneled)
        assert res.status == "superseded" and res.dispatch is False
        rows = _rows(paneled)
        assert rows[0]["status"] == "superseded" and "wrote to the cycle" in rows[0]["reason"]
        lenses = json.loads(rows[0]["lenses_json"])
        assert len(lenses) == 1 and lenses[0]["outcome"] == "failed" and "wrote to the cycle" in lenses[0]["reason"]
        # the rogue write stands as the reviewer's entry; no panel entry
        rounds = cycle_mod.read_rounds_file("feat-x", "impl", str(paneled))
        assert rounds[-1]["content"] == "rogue lens wrote this" and _entries(paneled) == []
        assert _state(paneled)["turn"] == "lead"

    def test_superseded_on_mid_panel_amend(self, paneled, monkeypatch):
        real = pnl.run_lens

        def racing(spec, lens, index, sub, **kw):
            r = real(spec, lens, index, sub, **kw)
            if lens.name == "scope":
                cycle_mod.add_round("feat-x", "impl", "lead", "AMEND", 1, "ps", str(paneled), updated_by="Claude")
            return r
        with patch.object(pnl, "run_lens", racing):
            res = _run(paneled)
        assert res.status == "superseded" and _entries(paneled) == []
        assert _rows(paneled)[0]["status"] == "superseded"
        # the same key re-runs as attempt 2 — supersession did not consume the budget
        res2 = _run(paneled)
        assert res2.status == "merged" and res2.attempt == 2 and res2.event_key == res.event_key

    def test_slot_busy_defers_without_row(self, paneled):
        claim = h.claim_turn_slot(paneled, kind=h.SLOT_KIND_CONVERSATION, role="lead",
                                  fields={"stem": "conv", "watcher_pid": os.getpid(),
                                          "watcher_ident": procs.identity(os.getpid())})
        try:
            res = _run(paneled)
            assert res.status == "deferred" and not res.dispatch and _rows(paneled) == []
        finally:
            h.release_turn_slot(claim)
        assert _run(paneled).status == "merged"

    def test_not_applicable_and_stale(self, project, fake_path, monkeypatch):
        _enable(project, on="[plan]")
        _init_cycle(project)
        _verdicts(monkeypatch, default="approve")
        # plan cycles gated: applies; impl not
        _enable(project)
        assert _run(project).status == "not-applicable"
        assert _run(project).dispatch is True
        _enable(project, on="[plan]")
        stale = dict(_state(project))
        cycle_mod.add_round("feat-x", "plan", "lead", "AMEND", 1, "x", str(project), updated_by="Claude")
        # AMEND keeps seq → not stale; a reviewer write moves it
        cycle_mod.add_round("feat-x", "plan", "reviewer", "REQUEST_CHANGES", 1, "no", str(project), updated_by="Codex")
        cycle_mod.add_round("feat-x", "plan", "lead", "SUBMIT_FOR_REVIEW", 2, "again", str(project), updated_by="Claude")
        res = _run(project, state=stale)
        assert res.status == "stale" and res.dispatch is False and _rows(project, ctype="plan") == []

    def test_error_retry_then_fallback_terminal(self, paneled):
        with patch.object(pnl, "run_lens", side_effect=RuntimeError("kaboom")):
            r1 = _run(paneled); r2 = _run(paneled)
        assert r1.status == "error" and r2.status == "error"
        assert [r["status"] for r in _rows(paneled)] == ["error", "error"]
        assert not h.slot_status(paneled)["held"] and _entries(paneled) == []
        n = len(_usage(paneled))
        r3 = _run(paneled)
        assert r3.status == "fallback" and r3.dispatch is True and r3.attempt == 3
        assert [r["status"] for r in _rows(paneled)] == ["error", "error", "fallback"]
        assert "could not complete after 2 attempts" in _rows(paneled)[-1]["reason"]
        assert len(_usage(paneled)) == n                                   # no lens ran on the third pass
        # restart / peek from the terminal fallback: dispatch, no new row
        r4 = _run(paneled)
        assert r4.status == "fallback" and r4.dispatch and len(_rows(paneled)) == 3

    def test_abandoned_retry_then_fallback(self, paneled):
        sub = pnl.current_submission(str(paneled))
        dead = subprocess.Popen([PY, "-c", "pass"]); dead.wait()
        conn = db.connect(project_dir=str(paneled))
        try:
            for _ in range(2):
                rid, _a = db.claim_panel(conn, ts=pnl._now_iso(), phase=sub.phase, cycle_type=sub.type, round_=sub.round,
                                         submission_seq=sub.submission_seq, event_key=sub.event_key, kind="auto",
                                         runner_pid=dead.pid, runner_ident="gone")
                pnl.sweep_abandoned_panels(str(paneled), _spec(paneled), conn=conn)
        finally:
            conn.close()
        assert [r["status"] for r in _rows(paneled)] == ["abandoned", "abandoned"]
        res = _run(paneled)
        assert res.status == "fallback" and res.dispatch is True
        assert [r["status"] for r in _rows(paneled)] == ["abandoned", "abandoned", "fallback"]

    def test_repeated_supersession_never_exhausts(self, paneled):
        real = pnl.run_lens
        calls = {"n": 0}

        def racing(spec, lens, index, sub, **kw):
            r = real(spec, lens, index, sub, **kw)
            if lens.name == "verification" and calls["n"] < 3:
                calls["n"] += 1
                cycle_mod.add_round("feat-x", "impl", "lead", "AMEND", 1, f"ps{calls['n']}", str(paneled), updated_by="Claude")
            return r
        with patch.object(pnl, "run_lens", racing):
            statuses = [_run(paneled).status for _ in range(4)]
        assert statuses == ["superseded", "superseded", "superseded", "merged"]
        assert [r["status"] for r in _rows(paneled)] == ["superseded"] * 3 + ["merged"]

    def test_manual_kind(self, paneled):
        assert _run(paneled, kind="manual").status == "merged" and _rows(paneled)[0]["kind"] == "manual"


# ---------------------------------------------------------------------------
# interjections
# ---------------------------------------------------------------------------

def _note(project: Path, text: str, target: str | None = "reviewer") -> int:
    from tagteam import controls
    args = [text] + (["--to", target] if target else [])
    controls.interject_command(args, project_root=project)
    conn = db.connect(project_dir=str(project))
    try:
        return conn.execute("SELECT MAX(id) FROM interjections").fetchone()[0]
    finally:
        conn.close()


def _delivered(project: Path) -> dict:
    conn = db.connect(project_dir=str(project))
    try:
        return {r[0]: r[1] for r in conn.execute("SELECT id, delivered_stem FROM interjections ORDER BY id")}
    finally:
        conn.close()


class TestInterjections:
    def test_snapshot_rendered_to_every_lens_and_stamped_on_merge(self, paneled, monkeypatch):
        n1 = _note(paneled, "prefer the smaller diff")
        n2 = _note(paneled, "note for the lead only", target="lead")
        res = _run(paneled)
        assert res.status == "merged"
        stem_dir = Path(paneled) / ".tagteam" / "panels" / res.stem
        prompts = [(stem_dir / f"{l}.prompt").read_text() for l in ("correctness", "scope", "verification")]
        assert all("prefer the smaller diff" in p for p in prompts) and all("lead only" not in p for p in prompts)
        assert _entries(paneled)[0]["panel_interjections"] == [n1]
        assert json.loads(_rows(paneled)[0]["interjection_ids"]) == [n1]
        d = _delivered(paneled)
        assert d[n1] == res.stem and d[n2] is None

    def test_fallback_and_superseded_stamp_nothing(self, paneled, monkeypatch):
        n1 = _note(paneled, "keep it small")
        _verdicts(monkeypatch, {"correctness": "approve", "scope": "no-file", "verification": "approve"})
        assert _run(paneled).status == "fallback"
        assert _delivered(paneled)[n1] is None
        # superseded (rogue) → nothing stamped either
        cycle_mod.add_round("feat-x", "impl", "reviewer", "REQUEST_CHANGES", 1, "no", str(paneled), updated_by="Codex")
        cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", 2, "again", str(paneled), updated_by="Claude")
        _verdicts(monkeypatch, {"correctness": "rogue-unset", "scope": "approve", "verification": "approve"})
        assert _run(paneled).status == "superseded"
        assert _delivered(paneled)[n1] is None

    def test_crash_after_add_round_reconciles_exactly_the_snapshot(self, paneled):
        """Entry written with panel_interjections=[n1]; row still running; a
        NEWER note n2 arrives before the sweep → reconciliation stamps n1
        only, finishes the row merged, seq untouched."""
        n1 = _note(paneled, "first")
        sub = pnl.current_submission(str(paneled))
        dead = subprocess.Popen([PY, "-c", "pass"]); dead.wait()
        conn = db.connect(project_dir=str(paneled))
        try:
            rid, _ = db.claim_panel(conn, ts=pnl._now_iso(), phase=sub.phase, cycle_type=sub.type, round_=sub.round,
                                    submission_seq=sub.submission_seq, event_key=sub.event_key, kind="auto",
                                    runner_pid=dead.pid, runner_ident="gone", interjection_ids=[n1])
            db.update_panel(conn, rid, ts="t", stem="crashed-stem")
        finally:
            conn.close()
        cycle_mod.add_round("feat-x", "impl", "reviewer", "REQUEST_CHANGES", 1, "PANEL: REQUEST_CHANGES — …", str(paneled),
                            updated_by="Codex panel",
                            meta={"panel_event": sub.event_key, "panel_id": rid,
                                  "panel_lenses": [{"lens": "scope", "outcome": "ok", "verdict": "REQUEST_CHANGES"}],
                                  "panel_interjections": [n1], "panel_applied_seq": sub.submission_seq + 1})
        seq = _state(paneled)["seq"]
        assert seq == sub.submission_seq + 1
        n2 = _note(paneled, "second, later")
        # the state ADVANCES before the sweep (lead re-submits) — reconciliation
        # must still record the ORIGINAL applied seq and deliver only n1
        cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", 2, "again", str(paneled), updated_by="Claude")
        assert _state(paneled)["seq"] == seq + 1
        out = pnl.sweep_abandoned_panels(str(paneled), _spec(paneled))
        assert out["reconciled"] == [rid]
        row = _rows(paneled)[0]
        assert row["status"] == "merged" and row["decision"] == "REQUEST_CHANGES" and row["applied_seq"] == seq
        d = _delivered(paneled)
        assert d[n1] == "crashed-stem" and d[n2] is None
        assert _state(paneled)["seq"] == seq + 1                                # untouched by the sweep
        # idempotent
        assert pnl.sweep_abandoned_panels(str(paneled), _spec(paneled))["reconciled"] == []
        # an entry without the seq key (older writer) records null, never a fabricated seq
        sub2 = pnl.current_submission(str(paneled))
        conn = db.connect(project_dir=str(paneled))
        try:
            rid2, _ = db.claim_panel(conn, ts=pnl._now_iso(), phase=sub2.phase, cycle_type=sub2.type, round_=sub2.round,
                                     submission_seq=sub2.submission_seq, event_key=sub2.event_key, kind="auto",
                                     runner_pid=dead.pid, runner_ident="gone")
        finally:
            conn.close()
        cycle_mod.add_round("feat-x", "impl", "reviewer", "APPROVE", 2, "PANEL: APPROVE — …", str(paneled),
                            updated_by="Codex panel", meta={"panel_event": sub2.event_key, "panel_id": rid2,
                                                             "panel_lenses": [], "panel_interjections": []})
        pnl.sweep_abandoned_panels(str(paneled), _spec(paneled))
        assert _rows(paneled)[-1]["status"] == "merged" and _rows(paneled)[-1]["applied_seq"] is None

    def test_stamp_before_finish_crash_boundary(self, paneled, monkeypatch):
        """Fault-inject the delivery/terminalisation boundary: a crash right
        after `add_round` + stamping but before `finish_panel` leaves a
        still-running row that the next sweep completes idempotently (no
        double delivery, exact applied seq); and if `finish_panel` were
        reached first the ordering would have stranded the note — assert the
        implementation stamps first."""
        n1 = _note(paneled, "note")
        real_finish = db.finish_panel
        calls = []

        def boom(conn, id_, **kw):
            calls.append(kw.get("status"))
            if kw.get("status") == "merged" and len(calls) == 1:
                raise RuntimeError("crash before terminalisation")
            return real_finish(conn, id_, **kw)
        with patch.object(db, "finish_panel", boom):
            res = _run(paneled)
        assert res.status == "error"                              # the crash surfaced as an error return …
        rows = _rows(paneled)
        assert len(rows) == 1 and rows[0]["status"] in ("running", "error")
        # … but the entry + delivery already happened (stamp came first)
        assert len(_entries(paneled)) == 1 and _delivered(paneled)[n1] is not None
        seq = _state(paneled)["seq"]
        conn = db.connect(project_dir=str(paneled))
        try:
            # simulate the crash precisely: the row is still running (the error path finished it as
            # `error` after the exception — put it back to running to model a hard crash)
            conn.execute("UPDATE panels SET status='running', finished_at=NULL, reason=NULL WHERE id=?", (rows[0]["id"],))
            conn.commit()
        finally:
            conn.close()
        out = pnl.sweep_abandoned_panels(str(paneled), _spec(paneled))
        assert out["reconciled"] == [rows[0]["id"]]
        row = _rows(paneled)[0]
        assert row["status"] == "merged" and row["applied_seq"] == seq
        assert _delivered(paneled)[n1] is not None and len(_entries(paneled)) == 1
        assert _state(paneled)["seq"] == seq


# ---------------------------------------------------------------------------
# sweep
# ---------------------------------------------------------------------------

class TestSweep:
    def _row(self, project, sub, pid, ident, stem="s", started_at=None):
        conn = db.connect(project_dir=str(project))
        try:
            rid, _ = db.claim_panel(conn, ts=started_at or pnl._now_iso(), phase=sub.phase, cycle_type=sub.type,
                                    round_=sub.round, submission_seq=sub.submission_seq, event_key=sub.event_key,
                                    kind="auto", runner_pid=pid, runner_ident=ident)
            db.update_panel(conn, rid, ts="t", stem=stem)
            if started_at:
                conn.execute("UPDATE panels SET started_at=? WHERE id=?", (started_at, rid)); conn.commit()
            return rid
        finally:
            conn.close()

    def test_live_dead_timeout_unverifiable(self, paneled):
        from datetime import datetime, timedelta, timezone
        sub = pnl.current_submission(str(paneled))
        me = os.getpid()
        rid = self._row(paneled, sub, me, procs.identity(me))
        out = pnl.sweep_abandoned_panels(str(paneled), _spec(paneled))
        assert out == {"reconciled": [], "abandoned": [], "unverifiable": []}
        conn = db.connect(project_dir=str(paneled)); conn.execute("DELETE FROM panels"); conn.commit(); conn.close()
        dead = subprocess.Popen([PY, "-c", "pass"]); dead.wait()
        rid = self._row(paneled, sub, dead.pid, "x")
        assert pnl.sweep_abandoned_panels(str(paneled), _spec(paneled))["abandoned"] == [rid]
        old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        rid = self._row(paneled, sub, me, procs.identity(me), stem="live-stem", started_at=old)
        h.turns_dir(paneled).mkdir(parents=True, exist_ok=True)
        h.inflight_path(paneled).write_text(json.dumps({"stem": "live-stem", "kind": "panel", "watcher_pid": me,
                                                        "watcher_ident": procs.identity(me), "owner_token": "t"}))
        out = pnl.sweep_abandoned_panels(str(paneled), _spec(paneled))
        assert out["abandoned"] == [] and [u["id"] for u in out["unverifiable"]] == [rid]
        h.inflight_path(paneled).unlink()
        assert pnl.sweep_abandoned_panels(str(paneled), _spec(paneled))["abandoned"] == [rid]
        conn = db.connect(project_dir=str(paneled)); conn.execute("DELETE FROM panels"); conn.commit(); conn.close()
        rid = self._row(paneled, sub, me, None)
        out = pnl.sweep_abandoned_panels(str(paneled), _spec(paneled))
        assert [u["id"] for u in out["unverifiable"]] == [rid]
        assert [u["id"] for u in pnl.panel_status(str(paneled))["unverifiable"]] == [rid]
        assert _run(paneled).status == "deferred"


# ---------------------------------------------------------------------------
# watcher
# ---------------------------------------------------------------------------

class TestWatcherPanel:
    @pytest.mark.parametrize("mode,target", MODES)
    def test_merged_never_dispatches_fallback_does(self, paneled, monkeypatch, mode, target):
        p = _proc(mode, paneled, _spec(paneled))
        st = _state(paneled)
        from contextlib import nullcontext
        quiet = patch("tagteam.watcher.notify_macos") if mode != "notify" else nullcontext()
        _verdicts(monkeypatch, {"correctness": "request-changes", "scope": "approve", "verification": "approve"})
        with patch(target) as send, quiet:
            send.return_value = True
            p.tick(st)
            send.assert_not_called()                                # merged: the entry is the turn
            new = _state(paneled)
            assert new["turn"] == "lead" and new["seq"] == st["seq"] + 1
            p.tick(new)                                             # the lead is dispatched as usual
            assert send.call_count == 1
            # lead re-submits; a lens fails → fallback → the ordinary reviewer IS dispatched
            cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", 2, "again", str(paneled), updated_by="Claude")
            _verdicts(monkeypatch, {"correctness": "approve", "scope": "no-file", "verification": "approve"})
            st2 = _state(paneled)
            p.tick(st2)
            assert send.call_count == 2 and _entries(paneled)[-1]["round"] == 1
            assert _rows(paneled)[-1]["status"] == "fallback"

    def test_headless_merged_skips_engine_fallback_runs_it(self, paneled, monkeypatch):
        eng = MagicMock(); eng.paused.return_value = None; eng.slot_busy = None
        p = _proc("headless", paneled, _spec(paneled), engine=eng)
        st = _state(paneled)
        p.tick(st)
        eng.run_owed_turn.assert_not_called()
        assert _state(paneled)["status"] == "done"                  # all approved → cycle approved
        # new cycle scenario: fallback runs the engine
        _open_impl(paneled, phase="feat-y")
        (paneled / "docs" / "phases" / "feat-y.md").write_text("# y\n")
        _verdicts(monkeypatch, {"correctness": "approve", "scope": "no-file", "verification": "approve"})
        st2 = _state(paneled)
        p.tick(st2)
        assert eng.run_owed_turn.call_count == 1 and eng.run_owed_turn.call_args[0][0]["seq"] == st2["seq"]

    @pytest.mark.parametrize("mode,target", MODES + [("headless", None)])
    def test_latch_busy_then_free(self, paneled, mode, target):
        eng = None
        if mode == "headless":
            eng = MagicMock(); eng.paused.return_value = None; eng.slot_busy = None
        p = _proc(mode, paneled, _spec(paneled), engine=eng)
        st = _state(paneled)
        claim = h.claim_turn_slot(paneled, kind=h.SLOT_KIND_CONVERSATION, role="lead",
                                  fields={"stem": "conv", "watcher_pid": os.getpid(),
                                          "watcher_ident": procs.identity(os.getpid())})
        from contextlib import nullcontext
        quiet = patch("tagteam.watcher.notify_macos") if mode != "notify" else nullcontext()
        with (patch(target) if target else nullcontext()) as send, quiet:
            p.tick(st)
            assert p._panel_owed_seq == st["seq"] and _rows(paneled) == []
            if send: send.assert_not_called()
            if eng: eng.run_owed_turn.assert_not_called()
            p.tick(st)
            h.release_turn_slot(claim)
            p.tick(st)                                              # identical tick decides (all approve → merged)
            assert p._panel_owed_seq is None and _rows(paneled)[0]["status"] == "merged"
            if send: send.assert_not_called()
            if eng: eng.run_owed_turn.assert_not_called()

    def test_gate_bounce_means_no_panel_gate_pass_then_panel(self, paneled, monkeypatch):
        from tagteam import gatekeeper as g
        # git repo so the gate can capture a boundary; tests command fails → BOUNCE → no panel
        subprocess.run(["git", "init", "-q"], cwd=str(paneled), check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=str(paneled), check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], cwd=str(paneled), check=True)
        fail = f'"{PY}" -c "import sys; sys.exit(1)"'
        (paneled / "tagteam.yaml").write_text((paneled / "tagteam.yaml").read_text() +
                                              f"gatekeeper:\n  enabled: true\n  scope: false\n  tests:\n    command: {json.dumps(fail)}\n")
        gate = g.load_spec(paneled)
        p = _proc("notify", paneled, _spec(paneled), gate=gate)
        st = _state(paneled)
        with patch("tagteam.watcher.notify_macos") as notify:
            p.tick(st)
            notify.assert_not_called()
        assert _state(paneled)["turn"] == "lead" and _rows(paneled) == []       # bounced, no panel
        # tests pass → gate PASS → panel merges in the same tick
        ok = f'"{PY}" -c "print(1)"'
        (paneled / "tagteam.yaml").write_text((paneled / "tagteam.yaml").read_text().replace(json.dumps(fail), json.dumps(ok)))
        p.gatekeeper = g.load_spec(paneled)
        cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", 2, "fixed", str(paneled), updated_by="Claude")
        st2 = _state(paneled)
        with patch("tagteam.watcher.notify_macos") as notify:
            p.tick(st2)
            notify.assert_not_called()
        rounds = cycle_mod.read_rounds_file("feat-x", "impl", str(paneled))
        actions = [(e["role"], e["action"]) for e in rounds if e["round"] == 2]
        assert actions == [("lead", "SUBMIT_FOR_REVIEW"), ("gatekeeper", "GATE_PASS"), ("reviewer", "APPROVE")]
        # the lenses saw the gate report
        stem_dir = Path(paneled) / ".tagteam" / "panels" / _rows(paneled)[0]["stem"]
        assert "GATE: PASS" in (stem_dir / "verification.prompt").read_text()

    def test_disabled_is_byte_identical(self, paneled):
        (paneled / "tagteam.yaml").write_text("agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\n")
        p = _proc("notify", paneled, None)
        with patch("tagteam.watcher.notify_macos") as notify, patch.object(pnl, "run_panel") as rp:
            p.tick(_state(paneled))
            assert notify.call_count == 1 and rp.call_count == 0
        assert _rows(paneled) == [] and _entries(paneled) == []
        assert p._maybe_panel({"turn": "reviewer", "seq": 1}) is True

    def test_build_processor_resolves_and_warns(self, paneled, capsys):
        from tagteam.watcher import _build_processor
        p = _build_processor(mode="notify", lead_pane="l", reviewer_pane="r", confirm=False, timeout_minutes=30,
                             project_dir=str(paneled), max_retries=1, retry_delay=0, pre_send_delay=0)
        assert p.panel is not None and p.panel.enabled
        _enable(paneled, extra="  bogus: 1\n")
        p = _build_processor(mode="notify", lead_pane="l", reviewer_pane="r", confirm=False, timeout_minutes=30,
                             project_dir=str(paneled), max_retries=1, retry_delay=0, pre_send_delay=0)
        assert p.panel is None and "reviewer panel disabled" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# CLI + docs + flag-off
# ---------------------------------------------------------------------------

class TestCliDocs:
    def test_lenses_preview_status_list_run(self, paneled, capsys, monkeypatch):
        assert pnl.panel_command(["lenses"], project_root=paneled) == 0
        out = capsys.readouterr().out
        assert "Panel: on (impl cycles; lenses: correctness, scope, verification)" not in out  # lenses view differs
        assert "correctness" in out and "builtin" in out and "reviewer: Codex via codex" in out
        assert pnl.panel_command(["preview", "--lens", "scope"], project_root=paneled) == 0
        out = capsys.readouterr().out
        assert "=== PANEL CONTRACT ===" in out and "Lens: scope" in out and "=== PLAN" in out and _rows(paneled) == []
        assert pnl.panel_command(["preview"], project_root=paneled) == 1
        assert pnl.panel_command(["preview", "--lens", "nope"], project_root=paneled) == 1
        capsys.readouterr()
        assert pnl.panel_command(["status"], project_root=paneled) == 0
        assert "No panel has run" in capsys.readouterr().out
        _verdicts(monkeypatch, {"correctness": "approve", "scope": "request-changes", "verification": "approve"})
        assert pnl.panel_command(["run"], project_root=paneled) == 0
        out = capsys.readouterr().out
        assert "PANEL: REQUEST_CHANGES" in out and "panel: merged" in out and "next: the lead" in out
        assert _rows(paneled)[0]["kind"] == "manual"
        assert pnl.panel_command(["status"], project_root=paneled) == 0
        out = capsys.readouterr().out
        assert "Last panel for feat-x_impl" in out and "✓ scope" in out and "REQUEST_CHANGES" in out
        assert pnl.panel_command(["list", "--json"], project_root=paneled) == 0
        assert json.loads(capsys.readouterr().out)[0]["status"] == "merged"
        assert pnl.panel_command(["status", "--json"], project_root=paneled) == 0
        assert json.loads(capsys.readouterr().out)["last"]["decision"] == "REQUEST_CHANGES"
        assert pnl.panel_command([], project_root=paneled) == 1
        assert pnl.panel_command(["bogus"], project_root=paneled) == 1
        assert pnl.panel_command(["run", "--nope"], project_root=paneled) == 1

    def test_run_disabled(self, project, capsys):
        assert pnl.panel_command(["run"], project_root=project) == 1
        assert "not enabled" in capsys.readouterr().out

    def test_cli_dispatch_and_help(self, paneled):
        r = subprocess.run([PY, "-m", "tagteam", "panel", "lenses"], cwd=str(paneled), capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": str(REPO)})
        assert r.returncode == 0 and "correctness" in r.stdout
        r = subprocess.run([PY, "-m", "tagteam", "--help"], cwd=str(paneled), capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": str(REPO)})
        assert "panel " in r.stdout

    def test_docs_and_package_data(self):
        a = (REPO / "plugin" / "skills" / "handoff" / "SKILL.md").read_text(encoding="utf-8")
        assert a == SKILL_SRC.read_text(encoding="utf-8")
        assert "PANEL:" in a and "panel" in a.lower()
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        htw = (REPO / "docs" / "how-tagteam-works.md").read_text(encoding="utf-8")
        assert "panel:" in readme and "tagteam panel" in readme
        assert '<a id="panels"></a>' in htw and "tagteam panel preview" in htw
        assert '"data/panels/*.md"' in (REPO / "pyproject.toml").read_text()
        for n in ("correctness", "scope", "verification"):
            assert (REPO / "tagteam" / "data" / "panels" / f"{n}.md").exists()

    def test_flag_off_full_cycle(self, project, fake_path):
        _open_impl(project)
        cycle_mod.add_round("feat-x", "impl", "reviewer", "APPROVE", 1, "ok", str(project), updated_by="Codex")
        conn = db.connect(project_dir=str(project))
        try:
            assert conn.execute("SELECT COUNT(*) FROM panels").fetchone()[0] == 0
            assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 9
        finally:
            conn.close()
        assert _entries(project) == []
        assert not [u for u in _usage(project) if (u.get("kind") or "").startswith("panel")]


# ---------------------------------------------------------------------------
# impl round 2: watcher supersession latch, cancel-turn, prompt context parity
# ---------------------------------------------------------------------------

class TestRound2:
    @pytest.mark.parametrize("mode", ["headless", "notify"])
    def test_watcher_amend_during_lens_then_identical_tick_merges(self, paneled, monkeypatch, mode):
        """A rounds-only AMEND during a lens inside `tick` supersedes the
        attempt WITHOUT changing seq; the identical next tick must run a fresh
        attempt that merges — never dispatch an ordinary reviewer, never
        strand the turn."""
        real = pnl.run_lens
        calls = {"n": 0}

        def racing(spec, lens, index, sub, **kw):
            r = real(spec, lens, index, sub, **kw)
            if lens.name == "scope" and calls["n"] == 0:
                calls["n"] += 1
                cycle_mod.add_round(sub.phase, "impl", "lead", "AMEND", 1, "ps", str(paneled), updated_by="Claude")
            return r
        eng = MagicMock(); eng.paused.return_value = None; eng.slot_busy = None
        p = _proc(mode, paneled, _spec(paneled), engine=eng if mode == "headless" else None)
        st = _state(paneled)
        with patch.object(pnl, "run_lens", racing), patch("tagteam.watcher.notify_macos") as notify:
            p.tick(st)
            assert p._panel_owed_seq == st["seq"] and _rows(paneled)[-1]["status"] == "superseded"
            assert _state(paneled)["seq"] == st["seq"]                       # AMEND: seq unchanged
            p.tick(st)                                                        # identical tick → fresh attempt
            assert p._panel_owed_seq is None
            assert [r["status"] for r in _rows(paneled)] == ["superseded", "merged"]
            notify.assert_not_called()
            eng.run_owed_turn.assert_not_called()
        assert _state(paneled)["status"] == "done"                            # merged APPROVE

    def test_gate_superseded_on_same_seq_re_arms_latch(self, paneled, monkeypatch):
        """The same stranding existed for the gate; superseded → latch."""
        from tagteam import gatekeeper as g
        subprocess.run(["git", "init", "-q"], cwd=str(paneled), check=True)
        (paneled / "tagteam.yaml").write_text("agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\n"
                                              f"gatekeeper:\n  enabled: true\n  scope: false\n  tests:\n    command: {json.dumps(f'\"{PY}\" -c \"print(1)\"')}\n")
        gate = g.load_spec(paneled)
        p = _proc("notify", paneled, None, gate=gate)
        st = _state(paneled)
        real = g.run_checks
        done = {"n": 0}

        def racing(*a, **kw):
            res = real(*a, **kw)
            if done["n"] == 0:
                done["n"] += 1
                cycle_mod.add_round("feat-x", "impl", "lead", "AMEND", 1, "ps", str(paneled), updated_by="Claude")
            return res
        with patch.object(g, "run_checks", racing), patch("tagteam.watcher.notify_macos") as notify:
            p.tick(st)
            assert p._gate_owed_seq == st["seq"] and notify.call_count == 0
            p.tick(st)                                                            # identical tick → decides + hands off
            assert p._gate_owed_seq is None and notify.call_count == 1

    def test_cancel_turn_on_a_hanging_lens(self, paneled, monkeypatch):
        """`tagteam cancel-turn` during a lens: later lenses do not run, no
        reviewer transition, no notes delivered, slot released, row `error`
        (cancelled), dispatch PAUSED with a diagnostic; `tagteam resume`
        retries the same owed reviewer turn once (attempt 2 merges)."""
        import threading, time
        from tagteam import controls
        n1 = _note(paneled, "note")
        _verdicts(monkeypatch, {"correctness": "hang", "scope": "approve", "verification": "approve"})
        spec = _spec(paneled)
        spec.timeout_s = 120.0
        p = _proc("notify", paneled, spec)
        st = _state(paneled)
        outcome = {}

        def cancel_when_running():
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                m = h.read_inflight(paneled)
                if m and m.get("kind") == "panel" and m.get("pid"):
                    outcome["rc"] = controls.cancel_turn_command(["--by", "jack"], project_root=paneled)
                    return
                time.sleep(0.1)
            outcome["rc"] = "never saw a lens pid"
        t = threading.Thread(target=cancel_when_running); t.start()
        with patch("tagteam.watcher.notify_macos") as notify:
            p.tick(st)
            t.join(60)
            notify.assert_not_called()
        assert outcome.get("rc") == 0
        rows = _rows(paneled)
        assert len(rows) == 1 and rows[0]["status"] == "error" and "cancelled by jack" in rows[0]["reason"]
        lenses = json.loads(rows[0]["lenses_json"])
        assert [l["lens"] for l in lenses] == ["correctness"] and "cancelled" in lenses[0]["reason"]   # no later lenses
        assert _entries(paneled) == [] and _state(paneled)["seq"] == st["seq"] and _state(paneled)["turn"] == "reviewer"
        assert _delivered(paneled)[n1] is None
        assert not h.slot_status(paneled)["held"]
        pause = h.read_pause(paneled)
        assert pause and pause["outcome"] == "cancelled" and "panel lens cancelled by jack" in pause["reason"]
        conn = db.connect(project_dir=str(paneled))
        try:
            kinds = [r[0] for r in conn.execute("SELECT kind FROM diagnostics ORDER BY id")]
        finally:
            conn.close()
        assert "panel_cancelled" in kinds
        assert p._panel_owed_seq == st["seq"]
        # paused: identical ticks do nothing
        _verdicts(monkeypatch, default="approve")
        with patch("tagteam.watcher.notify_macos") as notify:
            p.tick(st)
            assert len(_rows(paneled)) == 1 and notify.call_count == 0
            # resume → the same owed reviewer turn is retried once → attempt 2 merges
            controls.resume_command([], project_root=paneled)
            p.tick(st)
            assert [r["status"] for r in _rows(paneled)] == ["error", "merged"] and _rows(paneled)[-1]["attempt"] == 2
            notify.assert_not_called()
        assert _state(paneled)["status"] == "done" and _delivered(paneled)[n1] is not None

    def test_context_bounded_tail_note_scoping_gate_report_and_preview_parity(self, paneled, monkeypatch, capsys):
        # build history: 5 lead/reviewer rounds so the tail bound bites
        for r in range(1, 5):
            cycle_mod.add_round("feat-x", "impl", "reviewer", "REQUEST_CHANGES", r, f"no {r}", str(paneled), updated_by="Codex")
            cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", r + 1, f"again {r + 1}", str(paneled), updated_by="Claude")
        sub = pnl.current_submission(str(paneled))
        assert sub.round == 5
        # a gate entry for this round + a lead-only note + a reviewer note
        rp = cycle_mod._rounds_path("feat-x", "impl", str(paneled))
        with open(rp, "a") as f:
            f.write(json.dumps({"round": 5, "role": "gatekeeper", "action": "GATE_PASS", "content": "GATE: PASS | tests ok (1s)",
                                "ts": pnl._now_iso(), "gate_event": sub.event_key}) + "\n")
        n_lead = _note(paneled, "lead-only note", target="lead")
        n_rev = _note(paneled, "reviewer note")
        # spec with tail_n=2 (the watcher's --tail-rounds)
        spec = pnl.load_spec(paneled, tail_n=2)
        assert spec.tail_n == 2
        ctx = pnl.build_lens_context(str(paneled), sub, spec)
        assert [e["round"] for e in ctx["tail"]] == [4, 5]                          # bounded
        assert ctx["gate_entry"]["content"].startswith("GATE: PASS")
        assert [n["id"] for n in ctx["interjections"]] == [n_rev]                   # reviewer-scoped
        # the real run's prompt == preview's prompt (modulo the verdict-path placeholder)
        _verdicts(monkeypatch, default="approve")
        res = pnl.run_panel(str(paneled), spec=spec)
        assert res.status == "merged"
        real_prompt = (Path(paneled) / ".tagteam" / "panels" / res.stem / "scope.prompt").read_text()
        assert "again 5" in real_prompt and "again 4" in real_prompt and "again 2" not in real_prompt
        assert "GATE: PASS | tests ok (1s)" in real_prompt and "reviewer note" in real_prompt and "lead-only" not in real_prompt
        # preview against the same submission (reopen an equivalent state: preview needs a reviewer-ready one)
        # → compare on the previous submission by regenerating from the recorded context: use a fresh cycle
        _open_impl(paneled, phase="feat-p")
        (paneled / "docs" / "phases" / "feat-p.md").write_text("# p\n")
        sub2 = pnl.current_submission(str(paneled))
        n3 = _note(paneled, "note for p")
        _note(paneled, "lead-only for p", target="lead")
        capsys.readouterr()                                                          # drop the interject chatter
        assert pnl.panel_command(["preview", "--lens", "scope", "--tail", "2"], project_root=paneled) == 0
        preview = capsys.readouterr().out
        ctx2 = pnl.build_lens_context(str(paneled), sub2, pnl.load_spec(paneled, tail_n=2))
        expected = pnl.compose_lens_prompt(pnl.load_spec(paneled, tail_n=2), spec.lenses[1], 2, sub2, state=ctx2["state"],
                                           plan_text=ctx2["plan_text"], tail=ctx2["tail"], interjections=ctx2["interjections"],
                                           gate_entry=ctx2["gate_entry"],
                                           verdict_path=Path(paneled) / ".tagteam" / "panels" / "<stem>" / "scope.verdict.json")
        assert preview.strip() == expected.strip()
        assert "note for p" in preview and "lead-only for p" not in preview
        # and the real run for that submission differs from the preview ONLY in the verdict path
        res2 = pnl.run_panel(str(paneled), spec=pnl.load_spec(paneled, tail_n=2))
        real2 = (Path(paneled) / ".tagteam" / "panels" / res2.stem / "scope.prompt").read_text()
        norm = lambda t: t.replace(res2.stem, "<stem>").strip()
        assert norm(real2) == preview.strip()

    def test_build_processor_passes_tail_rounds(self, paneled):
        from tagteam.watcher import _build_processor
        p = _build_processor(mode="notify", lead_pane="l", reviewer_pane="r", confirm=False, timeout_minutes=30,
                             project_dir=str(paneled), max_retries=1, retry_delay=0, pre_send_delay=0, tail_rounds=7)
        assert p.panel.tail_n == 7
        p = _build_processor(mode="notify", lead_pane="l", reviewer_pane="r", confirm=False, timeout_minutes=30,
                             project_dir=str(paneled), max_retries=1, retry_delay=0, pre_send_delay=0)
        assert p.panel.tail_n == h.DEFAULT_TAIL_ROUNDS
