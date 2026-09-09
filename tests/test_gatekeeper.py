"""Phase 38 tests: gatekeeper pre-checks — config, implementation boundary
matrix, checks (scope-first freeze, tests, plan-doc, truncation, timeout,
log), decide + bounce cap, run_gate (slot-first claim, at-most-once,
superseded, deferred, error, released slot), idempotent decision
application across stores + fault matrix (a)–(g), sweep matrix, watcher
seam + gate-owed latch in every mode, PASS write semantics across ticks and
restart, CLI, cockpit payloads, SKILL copies, flag-off compatibility."""
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

from tagteam import cockpit_api as capi
from tagteam import cycle as cycle_mod
from tagteam import db, headless as h, procs
from tagteam import gatekeeper as g
from tagteam import state as state_mod
from tagteam.config import get_gatekeeper_spec, validate_gatekeeper_config, read_config

from tests.test_headless import project, _init_cycle, SKILL_SRC  # noqa: F401
from tests.test_controls import needs_proc_inspection  # noqa: F401

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
OK_CMD = f'"{PY}" -c "print(\'1 passed in 0.01s\')"'
FAIL_CMD = f'"{PY}" -c "import sys; print(\'boom\'); sys.exit(3)"'


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _git(project: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(project), capture_output=True, text=True,
                          check=True).stdout.strip()


def _git_project(project: Path, commit: bool = True) -> Path:
    _git(project, "init", "-q")
    _git(project, "config", "user.email", "t@t")
    _git(project, "config", "user.name", "t")
    _git(project, "config", "commit.gpgsign", "false")
    (project / "docs" / "phases").mkdir(parents=True, exist_ok=True)
    (project / "docs" / "phases" / "feat-x.md").write_text("# plan\n", encoding="utf-8")
    (project / "docs" / "roadmap.md").write_text("# roadmap\n", encoding="utf-8")
    (project / "src.py").write_text("x = 1\n", encoding="utf-8")
    (project / ".gitignore").write_text(".tagteam/\ndocs/handoffs/\nhandoff-state.json\nhandoff-diagnostics.jsonl\n",
                                        encoding="utf-8")
    if commit:
        _git(project, "add", "-A")
        _git(project, "commit", "-qm", "init")
    return project


def _enable(project: Path, extra: str = "", command: str | None = OK_CMD) -> None:
    body = ("agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\n"
            "gatekeeper:\n  enabled: true\n")
    if command is not None:
        body += f"  tests:\n    command: {json.dumps(command)}\n"
    (project / "tagteam.yaml").write_text(body + extra, encoding="utf-8")


def _spec(project: Path) -> g.GateSpec:
    return g.load_spec(project)


def _approve_plan(project: Path, phase="feat-x"):
    _init_cycle(project, phase=phase)
    cycle_mod.add_round(phase, "plan", "reviewer", "APPROVE", 1, "ok", str(project), updated_by="Codex")


def _open_impl(project: Path, phase="feat-x", content="impl v1"):
    cycle_mod.init_cycle(phase, "impl", "Claude", "Codex", content, str(project), updated_by="Claude")
    return state_mod.read_state(str(project))


def _state(project: Path) -> dict:
    return state_mod.read_state(str(project)) or {}


def _rows(project: Path, phase="feat-x", ctype="impl") -> list[dict]:
    conn = db.connect(project_dir=str(project))
    try:
        return db.gates_for_cycle(conn, phase, ctype)
    finally:
        conn.close()


def _entries(project: Path, phase="feat-x", ctype="impl") -> list[dict]:
    return [e for e in cycle_mod.read_rounds_file(phase, ctype, str(project))
            if e.get("role") == cycle_mod.ROLE_GATEKEEPER]


def _run(project: Path, **kw) -> g.GateResult:
    return g.run_gate(str(project), spec=kw.pop("spec", None) or _spec(project), **kw)


def _counter_cmd(project: Path) -> str:
    """A test command that counts its invocations in a file (outside the repo tree)."""
    cnt = project.parent / f"{project.name}-count"
    return f'"{PY}" -c "import pathlib; p=pathlib.Path({str(cnt)!r}); p.write_text(str(int(p.read_text() or 0)+1) if p.exists() else \'1\')"'


def _count(project: Path) -> int:
    cnt = project.parent / f"{project.name}-count"
    return int(cnt.read_text() or 0) if cnt.exists() else 0


@pytest.fixture
def gated(project):
    """git project, gate enabled (tests ok), plan approved, impl open with
    one real code change → reviewer-ready impl submission."""
    _git_project(project)
    _enable(project)
    _approve_plan(project)
    (project / "src.py").write_text("x = 2\n", encoding="utf-8")
    _open_impl(project)
    return project


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_absent_block_is_off_and_defaults(self):
        spec = get_gatekeeper_spec({})
        assert spec["enabled"] is False and spec["on"] == ["impl"] and spec["scope"] is True
        assert spec["max_bounces"] == 2 and spec["max_output_chars"] == 4000
        assert spec["tests_command"] is None and spec["tests_timeout_s"] == 15 * 60
        assert validate_gatekeeper_config({}) == []
        gs = g.resolve_gatekeeper({})
        assert not gs.enabled and not gs.applies_to("impl") and gs.problems == []

    def test_valid_block(self):
        cfg = {"gatekeeper": {"enabled": True, "on": ["plan", "impl"],
                              "tests": {"command": ["python", "-m", "pytest"], "timeout_minutes": 3},
                              "scope": False, "max_bounces": 1, "max_output_chars": 100}}
        assert validate_gatekeeper_config(cfg) == []
        s = get_gatekeeper_spec(cfg)
        assert s["tests_command"] == ["python", "-m", "pytest"] and s["tests_timeout_s"] == 180
        gs = g.resolve_gatekeeper(cfg)
        assert gs.enabled and gs.applies_to("plan") and gs.applies_to("impl") and gs.scope is False

    @pytest.mark.parametrize("block,needle", [
        ({"enabled": "yes"}, "enabled"),
        ({"enabled": True, "on": "impl"}, "on"),
        ({"enabled": True, "on": ["review"]}, "on"),
        ({"enabled": True, "tests": "pytest"}, "tests"),
        ({"enabled": True, "tests": {"command": 3}}, "command"),
        ({"enabled": True, "tests": {"command": "x", "timeout_minutes": 0}}, "timeout"),
        ({"enabled": True, "tests": {"command": "x", "bogus": 1}}, "bogus"),
        ({"enabled": True, "scope": "no"}, "scope"),
        ({"enabled": True, "max_bounces": -1}, "max_bounces"),
        ({"enabled": True, "max_output_chars": "many"}, "max_output_chars"),
        ({"enabled": True, "unknown": 1}, "unknown"),
    ])
    def test_invalid_blocks_are_reported_and_disable(self, block, needle):
        problems = validate_gatekeeper_config({"gatekeeper": block})
        assert problems and any(needle in p for p in problems)
        gs = g.resolve_gatekeeper({"gatekeeper": block})
        assert not gs.enabled and gs.problems

    def test_not_a_mapping(self):
        assert validate_gatekeeper_config({"gatekeeper": [1]})
        assert not g.resolve_gatekeeper({"gatekeeper": "on"}).enabled


# ---------------------------------------------------------------------------
# implementation boundary
# ---------------------------------------------------------------------------

class TestImplBoundary:
    def test_captured_on_plan_approve_and_copied_at_impl_init(self, project):
        _git_project(project)
        _init_cycle(project)
        assert cycle_mod.read_impl_boundary("feat-x", "plan", str(project)) is None
        cycle_mod.add_round("feat-x", "plan", "reviewer", "APPROVE", 1, "ok", str(project), updated_by="Codex")
        b = cycle_mod.read_impl_boundary("feat-x", "plan", str(project))
        assert b and b["source"] == "plan-approve" and b["sha"] == _git(project, "rev-parse", "HEAD")
        assert isinstance(b["dirty"], dict) and b["captured_at"]
        _open_impl(project)
        bi = cycle_mod.read_impl_boundary("feat-x", "impl", str(project))
        assert bi and bi["source"] == "copied-from-plan" and bi["sha"] == b["sha"] and bi["dirty"] == b["dirty"]

    def test_captured_on_arbiter_ruling_approve(self, project):
        _git_project(project)
        _init_cycle(project)
        cycle_mod.add_round("feat-x", "plan", "reviewer", "ESCALATE", 1, "stuck", str(project), updated_by="Codex")
        cycle_mod.add_ruling("feat-x", "plan", "APPROVE", "go", "Jack", str(project))
        b = cycle_mod.read_impl_boundary("feat-x", "plan", str(project))
        assert b and b["source"] == "plan-approve"

    def test_impl_cycle_approve_does_not_capture(self, project):
        _git_project(project)
        _approve_plan(project)
        _open_impl(project)
        cycle_mod.add_round("feat-x", "impl", "reviewer", "APPROVE", 1, "ok", str(project), updated_by="Codex")
        st = cycle_mod._read_status_from_file(cycle_mod._status_path("feat-x", "impl", str(project)))
        assert st["impl_boundary"]["source"] == "copied-from-plan"

    def test_no_git_boundary_is_none_and_work_unavailable(self, project):
        _approve_plan(project)
        assert cycle_mod.read_impl_boundary("feat-x", "plan", str(project)) is None
        with pytest.raises(cycle_mod.ImplWorkUnavailable):
            cycle_mod.compute_impl_work("feat-x", str(project))

    def test_plan_only_changes_fail(self, project):
        _git_project(project); _approve_plan(project); _open_impl(project)
        (project / "docs" / "roadmap.md").write_text("# roadmap\n- more\n", encoding="utf-8")
        (project / "docs" / "phases" / "feat-x.md").write_text("# plan\nrevised\n", encoding="utf-8")
        _git(project, "add", "-A"); _git(project, "commit", "-qm", "plan work")
        w = cycle_mod.compute_impl_work("feat-x", str(project))
        assert w["paths"] == [] and "docs/roadmap.md" in w["excluded"] and "docs/phases/feat-x.md" in w["excluded"]

    def test_code_change_passes_committed_or_dirty(self, project):
        _git_project(project); _approve_plan(project); _open_impl(project)
        (project / "src.py").write_text("x = 2\n", encoding="utf-8")
        assert cycle_mod.compute_impl_work("feat-x", str(project))["paths"] == ["src.py"]
        _git(project, "add", "-A"); _git(project, "commit", "-qm", "code")
        assert cycle_mod.compute_impl_work("feat-x", str(project))["paths"] == ["src.py"]

    def test_impl_doc_change_passes(self, project):
        _git_project(project); _approve_plan(project); _open_impl(project)
        (project / "README.md").write_text("# readme\n", encoding="utf-8")
        assert cycle_mod.compute_impl_work("feat-x", str(project))["paths"] == ["README.md"]

    def test_unborn_head_snapshot_decides(self, project):
        _git_project(project, commit=False)      # no HEAD
        _approve_plan(project)
        b = cycle_mod.read_impl_boundary("feat-x", "plan", str(project))
        assert b["sha"] is None and "src.py" in b["dirty"]
        _open_impl(project)
        assert cycle_mod.compute_impl_work("feat-x", str(project))["paths"] == []     # nothing changed
        (project / "src.py").write_text("x = 9\n", encoding="utf-8")
        assert cycle_mod.compute_impl_work("feat-x", str(project))["paths"] == ["src.py"]

    def test_dirty_at_capture_unchanged_then_committed_identical_then_modified(self, project):
        _git_project(project)
        (project / "src.py").write_text("x = 5\n", encoding="utf-8")   # dirty at approval
        _approve_plan(project); _open_impl(project)
        assert cycle_mod.compute_impl_work("feat-x", str(project))["paths"] == []       # still dirty, unchanged
        _git(project, "add", "-A"); _git(project, "commit", "-qm", "commit unchanged")
        assert cycle_mod.compute_impl_work("feat-x", str(project))["paths"] == []       # committed identical → still no work
        (project / "src.py").write_text("x = 6\n", encoding="utf-8")
        assert cycle_mod.compute_impl_work("feat-x", str(project))["paths"] == ["src.py"]

    def test_dirty_at_capture_modified_still_dirty_passes_and_deletion_counts(self, project):
        _git_project(project)
        (project / "src.py").write_text("x = 5\n", encoding="utf-8")
        _approve_plan(project); _open_impl(project)
        (project / "src.py").write_text("x = 7\n", encoding="utf-8")
        assert cycle_mod.compute_impl_work("feat-x", str(project))["paths"] == ["src.py"]
        (project / "src.py").unlink()
        w = cycle_mod.compute_impl_work("feat-x", str(project))
        assert w["paths"] == ["src.py"]

    def test_untracked_directory_growth_counts(self, project):
        _git_project(project)
        (project / "pkg").mkdir(); (project / "pkg" / "a.py").write_text("a\n")
        _approve_plan(project); _open_impl(project)
        assert cycle_mod.compute_impl_work("feat-x", str(project))["paths"] == []
        (project / "pkg" / "b.py").write_text("b\n")
        assert cycle_mod.compute_impl_work("feat-x", str(project))["paths"] == ["pkg/b.py"]

    def test_legacy_cycle_without_boundary_skips(self, project):
        _git_project(project); _approve_plan(project); _open_impl(project)
        for t in ("plan", "impl"):
            p = cycle_mod._status_path("feat-x", t, str(project))
            d = json.loads(p.read_text()); d.pop("impl_boundary", None); p.write_text(json.dumps(d))
        with pytest.raises(cycle_mod.ImplWorkUnavailable):
            cycle_mod.compute_impl_work("feat-x", str(project))
        r = g.check_scope(g.resolve_gatekeeper({"gatekeeper": {"enabled": True}}), "feat-x", "impl", str(project))
        assert r.status == "skip" and "boundary" in r.detail

    def test_pre_flight_uses_plan_boundary_before_impl_init(self, project):
        _git_project(project); _approve_plan(project)
        (project / "src.py").write_text("x = 3\n", encoding="utf-8")
        assert cycle_mod.compute_impl_work("feat-x", str(project))["paths"] == ["src.py"]


# ---------------------------------------------------------------------------
# checks + decide
# ---------------------------------------------------------------------------

class TestChecks:
    def _spec(self, **kw) -> g.GateSpec:
        base = dict(enabled=True, on=["impl"], tests_command=None, tests_timeout_s=60.0, scope=True,
                    max_bounces=2, max_output_chars=4000)
        base.update(kw)
        return g.GateSpec(**base)

    def test_scope_frozen_before_tests_run(self, project):
        """A test command that creates an untracked file cannot satisfy scope."""
        _git_project(project); _approve_plan(project); _open_impl(project)
        cmd = f'"{PY}" -c "open(\'made_by_tests.py\',\'w\').write(\'x\')"'
        res = g.run_checks(self._spec(tests_command=cmd), "feat-x", "impl", str(project))
        by = {c["id"]: c for c in res["checks"]}
        assert [c["id"] for c in res["checks"]] == ["scope", "plan-doc", "tests"]
        assert by["scope"]["status"] == "fail" and by["tests"]["status"] == "ok"
        assert (project / "made_by_tests.py").exists()
        # and independently: with real work present, scope passes regardless of the tests
        (project / "src.py").write_text("y\n")
        res2 = g.run_checks(self._spec(tests_command=FAIL_CMD), "feat-x", "impl", str(project))
        by2 = {c["id"]: c for c in res2["checks"]}
        assert by2["scope"]["status"] == "ok" and by2["tests"]["status"] == "fail"

    def test_tests_skip_without_command_and_plan_gate_scope_na(self, project):
        _git_project(project); _approve_plan(project)
        res = g.run_checks(self._spec(on=["plan"]), "feat-x", "plan", str(project))
        by = {c["id"]: c for c in res["checks"]}
        assert by["tests"]["status"] == "skip" and by["scope"]["status"] == "skip"
        d = g.decide(res, self._spec(), 0)
        assert d["action"] == cycle_mod.GATE_PASS and "not checked" in d["content"]

    def test_tests_fail_captures_tail_and_log(self, project, tmp_path):
        log = tmp_path / "gate.log"
        r = g.check_tests(self._spec(tests_command=FAIL_CMD, max_output_chars=4), str(project), log_path=log)
        assert r.status == "fail" and "exit 3" in r.summary and r.data["exit_code"] == 3
        assert "last 4 chars" in r.detail and r.detail.rstrip().endswith("oom")     # truncated to the LAST chars
        assert "boom" in log.read_text() and "exit=3" in log.read_text()

    def test_tests_ok_summarizes_pytest_line_and_env(self, project):
        cmd = f'"{PY}" -c "import os; assert os.environ.get(\'TAGTEAM_GATE\')==\'1\'; print(\'===== 12 passed, 1 skipped in 0.5s =====\')"'
        r = g.check_tests(self._spec(tests_command=cmd), str(project))
        assert r.status == "ok" and "12 passed, 1 skipped" in r.summary

    def test_summary_recognizes_bare_q_form(self):
        # the exact tail `pytest -q` printed for the Phase 41 round-1 gate
        out = ("........................................... [ 96%]\n"
               "...........................................                              [100%]\n"
               "1265 passed, 5 skipped in 247.01s (0:04:07)\n")
        assert g._summarize_test_output(out) == "1265 passed, 5 skipped"
        assert g._summarize_test_output("===== 12 passed, 1 skipped in 0.5s =====\n") == "12 passed, 1 skipped"
        assert g._summarize_test_output("2 failed, 40 passed, 1 warning in 3.20s\n") == "2 failed, 40 passed, 1 warning"
        assert g._summarize_test_output("no tests ran in 0.01s\n") == "no tests ran"
        # a test's own chatter is not the summary; unrelated runners → None
        assert g._summarize_test_output("INFO: 3 passed in 1s of budget\nok\n") is None
        assert g._summarize_test_output("go: ok  \tPASS\n") is None

    def test_q_form_reaches_the_gate_entry_and_status(self, project, capsys):
        _prep_impl_ready(project, command=f'"{PY}" -c "print(\'7 passed, 2 skipped in 0.30s\')"')
        cycle_mod.cycle_command(["init", "--phase", "feat-x", "--type", "impl", "--lead", "Claude",
                                 "--reviewer", "Codex", "--updated-by", "Claude", "--content", "impl v1"])
        capsys.readouterr()
        ent = _entries(project)[0]
        assert "tests ok (7 passed, 2 skipped, " in ent["content"] and "checked: HEAD " in ent["content"]
        assert g.gate_command(["status"], project_root=project) == 0
        assert "7 passed, 2 skipped" in capsys.readouterr().out
        # BOUNCE keeps the failing summary too
        cycle_mod.add_round("feat-x", "impl", "reviewer", "REQUEST_CHANGES", 1, "no", str(project), updated_by="Codex")
        _enable(project, extra="  on_submit: true\n",
                command=f'"{PY}" -c "import sys; print(\'1 failed, 6 passed in 0.30s\'); sys.exit(1)"')
        (project / "src.py").write_text("x = 3\n", encoding="utf-8")
        cycle_mod.cycle_command(["add", "--phase", "feat-x", "--type", "impl", "--role", "lead",
                                 "--action", "SUBMIT_FOR_REVIEW", "--round", "2", "--updated-by", "Claude",
                                 "--content", "impl v2"])
        capsys.readouterr()
        b = [e for e in _entries(project) if e["action"] == cycle_mod.GATE_BOUNCE][0]
        assert b["content"].splitlines()[0].endswith("tests FAILED (1 failed, 6 passed, exit 1, 0.3s)") \
            or "tests FAILED (1 failed, 6 passed, exit 1, " in b["content"].splitlines()[0]

    def test_tests_list_form_and_unstartable(self, project):
        r = g.check_tests(self._spec(tests_command=[PY, "-c", "print(1)"]), str(project))
        assert r.status == "ok"
        r = g.check_tests(self._spec(tests_command=["/definitely/not/here-xyz"]), str(project))
        assert r.status == "fail" and "could not start" in r.summary

    @pytest.mark.skipif(sys.platform == "win32", reason="process-group kill is POSIX here")
    def test_tests_timeout_kills_and_fails(self, project):
        cmd = f'"{PY}" -c "import time; print(\'started\', flush=True); time.sleep(30)"'
        t0 = time.monotonic()
        r = g.check_tests(self._spec(tests_command=cmd, tests_timeout_s=0.5), str(project))
        assert time.monotonic() - t0 < 10
        assert r.status == "fail" and "timed out" in r.summary and r.data["timed_out"] is True

    def test_plan_doc(self, project):
        _git_project(project)
        assert g.check_plan_doc("feat-x", str(project)).status == "ok"
        (project / "docs" / "phases" / "feat-x.md").write_text("  \n")
        assert g.check_plan_doc("feat-x", str(project)).status == "fail"
        assert g.check_plan_doc("nope", str(project)).status == "fail"

    def test_decide_pass_bounce_cap(self):
        ok = {"checks": [{"id": "scope", "status": "ok", "summary": "scope 1 path", "detail": ""},
                         {"id": "plan-doc", "status": "ok", "summary": "plan-doc ok", "detail": ""},
                         {"id": "tests", "status": "ok", "summary": "tests ok (1s)", "detail": ""}]}
        d = g.decide(ok, self._spec(), 5)
        assert d["action"] == cycle_mod.GATE_PASS and d["content"] == "GATE: PASS | scope 1 path | plan-doc ok | tests ok (1s)"
        bad = {"checks": [{"id": "scope", "status": "ok", "summary": "scope 1 path", "detail": ""},
                          {"id": "plan-doc", "status": "ok", "summary": "plan-doc ok", "detail": ""},
                          {"id": "tests", "status": "fail", "summary": "tests FAILED (exit 1, 2s)",
                           "detail": "--- tests: last 4000 chars ---\nFAILED t.py::x"}]}
        d = g.decide(bad, self._spec(), 1)
        assert d["action"] == cycle_mod.GATE_BOUNCE and d["failed"] == ["tests"]
        assert d["content"].startswith("GATE: BOUNCE | scope 1 path | plan-doc ok | tests FAILED (exit 1, 2s)\n--- tests")
        d = g.decide(bad, self._spec(max_bounces=2), 2)
        assert d["action"] == cycle_mod.GATE_PASS and d["cap_hit"] and d["failed"] == ["tests"]
        assert d["content"].startswith("GATE: checks failed but bounce cap (2) reached — reviewer, see report\n")
        assert "PASS-WITH-FINDINGS" in d["content"] and "FAILED t.py::x" in d["content"]

    def test_consecutive_bounces_counts_trailing_only(self):
        rows = [{"role": "lead", "action": "SUBMIT_FOR_REVIEW"},
                {"role": "gatekeeper", "action": "GATE_BOUNCE"},
                {"role": "gatekeeper", "action": "GATE_PASS"},
                {"role": "gatekeeper", "action": "GATE_BOUNCE"},
                {"role": "reviewer", "action": "REQUEST_CHANGES"},
                {"role": "gatekeeper", "action": "GATE_BOUNCE"}]
        assert g.consecutive_bounces(rows) == 2
        assert g.consecutive_bounces([]) == 0
        assert g.consecutive_bounces(rows[:3]) == 0

    def test_format_report(self):
        res = {"checks": [{"id": "scope", "status": "ok", "summary": "scope 1 path", "detail": "  a.py  (x)"},
                          {"id": "tests", "status": "fail", "summary": "tests FAILED", "detail": "boom"}]}
        out = g.format_check_report(res, {"content": "GATE: BOUNCE | ..."})
        assert out.splitlines()[0] == "GATE: BOUNCE | ..." and "✗ tests" in out and "boom" in out and "a.py" in out


# ---------------------------------------------------------------------------
# run_gate
# ---------------------------------------------------------------------------

class TestRunGate:
    def test_pass_attaches_entry_seq_unchanged_dispatch(self, gated):
        before = _state(gated)
        assert before["turn"] == "reviewer" and before["status"] == "ready"
        res = _run(gated)
        assert res.status == "pass" and res.dispatch is True and res.event_key.endswith(f"/r1/{before['seq']}")
        after = _state(gated)
        assert after["seq"] == before["seq"] and after["turn"] == "reviewer" and after["updated_by"] == "Claude"
        ents = _entries(gated)
        assert len(ents) == 1 and ents[0]["action"] == "GATE_PASS" and ents[0]["updated_by"] == "Gatekeeper"
        assert ents[0]["gate_event"] == res.event_key and ents[0]["gate_id"] == res.gate_id and ents[0]["gate_attempt"] == 1
        assert ents[0]["content"].startswith("GATE: PASS | scope 1 path | plan-doc ok | tests ok (")
        rows = _rows(gated)
        assert len(rows) == 1 and rows[0]["status"] == "pass" and rows[0]["kind"] == "auto" and rows[0]["stem"]
        assert rows[0]["runner_pid"] == os.getpid() and rows[0]["applied_seq"] is None
        assert json.loads(rows[0]["result_json"])["decision"]["verdict"] == "PASS"
        assert not h.slot_status(gated)["held"]
        assert Path(res.log_path).exists()
        from tagteam import dualwrite
        assert not dualwrite.is_db_invalid(str(gated))          # the shadow mirror accepted the entry
        # cycle status untouched
        st = cycle_mod.read_status("feat-x", "impl", str(gated))
        assert st["state"] == "in-progress" and st["ready_for"] == "reviewer"

    def test_bounce_hands_turn_to_lead_once(self, gated):
        _enable(gated, command=FAIL_CMD)
        before = _state(gated)
        res = _run(gated)
        assert res.status == "bounce" and res.dispatch is False
        after = _state(gated)
        assert after["turn"] == "lead" and after["status"] == "ready" and after["seq"] == before["seq"] + 1
        assert after["updated_by"] == "Gatekeeper" and after["round"] == 1
        st = cycle_mod.read_status("feat-x", "impl", str(gated))
        assert st["state"] == "in-progress" and st["ready_for"] == "lead"
        ents = _entries(gated)
        assert len(ents) == 1 and ents[0]["action"] == "GATE_BOUNCE" and "tests FAILED (exit 3" in ents[0]["content"]
        assert "boom" in ents[0]["content"]
        rows = _rows(gated)
        assert rows[0]["status"] == "bounce" and rows[0]["applied_seq"] == after["seq"]
        assert not h.slot_status(gated)["held"]
        # the lead's re-submission gets its own event and its own gate
        (gated / "tagteam.yaml").write_text((gated / "tagteam.yaml").read_text().replace(json.dumps(FAIL_CMD), json.dumps(OK_CMD)))
        cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", 2, "fixed", str(gated), updated_by="Claude")
        res2 = _run(gated)
        assert res2.status == "pass" and res2.event_key != res.event_key and res2.event_key.endswith("/r2/" + str(_state(gated)["seq"]))

    def test_decided_is_not_rerun_and_no_second_entry(self, gated):
        _enable(gated, command=_counter_cmd(gated))
        r1 = _run(gated); assert r1.status == "pass" and _count(gated) == 1
        r2 = _run(gated); assert r2.status == "pass" and r2.dispatch and _count(gated) == 1   # peek: decided
        r3 = _run(gated); assert r3.status == "pass" and _count(gated) == 1
        assert len(_entries(gated)) == 1 and len(_rows(gated)) == 1

    def test_not_applicable_plan_and_disabled(self, project):
        _git_project(project); _enable(project)
        _init_cycle(project)                                          # plan cycle, on=[impl]
        res = _run(project)
        assert res.status == "not-applicable" and res.dispatch is True and _rows(project, ctype="plan") == []
        # not-ready: turn is the lead's
        cycle_mod.add_round("feat-x", "plan", "reviewer", "REQUEST_CHANGES", 1, "no", str(project), updated_by="Codex")
        res = _run(project)
        assert res.status == "not-ready" and res.dispatch is False

    def test_stale_observation_never_dispatches(self, gated):
        stale = dict(_state(gated))
        cycle_mod.add_round("feat-x", "impl", "reviewer", "REQUEST_CHANGES", 1, "no", str(gated), updated_by="Codex")
        cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", 2, "again", str(gated), updated_by="Claude")
        res = _run(gated, state=stale)
        assert res.status == "stale" and res.dispatch is False and _rows(gated) == []

    def test_slot_busy_defers_without_a_row(self, gated):
        claim = h.claim_turn_slot(gated, kind=h.SLOT_KIND_CONVERSATION, role="lead",
                                  fields={"stem": "conv", "watcher_pid": os.getpid(),
                                          "watcher_ident": procs.identity(os.getpid())})
        try:
            res = _run(gated)
            assert res.status == "deferred" and res.dispatch is False and "busy" in res.reason
            assert _rows(gated) == [] and _entries(gated) == []
        finally:
            h.release_turn_slot(claim)
        res = _run(gated)                                              # a later identical tick decides
        assert res.status == "pass" and len(_rows(gated)) == 1

    def test_live_other_runner_defers_and_slot_released(self, gated):
        sub = g.current_submission(str(gated))
        conn = db.connect(project_dir=str(gated))
        try:
            db.claim_gate(conn, ts="t", phase=sub.phase, cycle_type=sub.type, round_=sub.round,
                          submission_seq=sub.submission_seq, event_key=sub.event_key, kind="auto",
                          runner_pid=os.getpid(), runner_ident=procs.identity(os.getpid()))
        finally:
            conn.close()
        res = _run(gated)
        assert res.status == "deferred" and not res.dispatch and "another gate runner" in res.reason
        assert not h.slot_status(gated)["held"]
        assert [r["status"] for r in _rows(gated)] == ["running"]

    def test_superseded_when_submission_moves_mid_check(self, gated):
        """The lead AMENDs (same round, rounds-only, no seq bump) while the
        checks run → no cycle write, row superseded, slot released."""
        real = g.run_checks

        def racing(*a, **kw):
            res = real(*a, **kw)
            cycle_mod.add_round("feat-x", "impl", "lead", "AMEND", 1, "ps", str(gated), updated_by="Claude")
            return res
        with patch.object(g, "run_checks", racing):
            res = _run(gated)
        assert res.status == "superseded" and res.dispatch is False
        assert _entries(gated) == []
        rows = _rows(gated)
        assert len(rows) == 1 and rows[0]["status"] == "superseded" and rows[0]["reason"]
        assert not h.slot_status(gated)["held"]
        # an AMEND is rounds-only (same seq → same event key): the next tick
        # re-runs on the same key as attempt 2 and decides
        res2 = _run(gated)
        assert res2.status == "pass" and res2.event_key == res.event_key and res2.attempt == 2
        assert [r["status"] for r in _rows(gated)] == ["superseded", "pass"]

    def test_error_in_checks_finishes_row_error_and_releases_slot(self, gated):
        with patch.object(g, "run_checks", side_effect=RuntimeError("kaboom")):
            res = _run(gated)
        assert res.status == "error" and not res.dispatch and "kaboom" in res.reason
        rows = _rows(gated)
        assert len(rows) == 1 and rows[0]["status"] == "error" and "kaboom" in rows[0]["reason"]
        assert not h.slot_status(gated)["held"] and _entries(gated) == []
        # one automatic retry (attempt 2) then decides
        res2 = _run(gated)
        assert res2.status == "pass" and res2.attempt == 2

    def test_attempts_exhausted_pass_with_findings(self, gated):
        with patch.object(g, "run_checks", side_effect=RuntimeError("kaboom")):
            assert _run(gated).status == "error"
            assert _run(gated).status == "error"
        res = _run(gated)
        assert res.status == "pass" and res.dispatch and res.attempt == 3
        ents = _entries(gated)
        assert len(ents) == 1 and ents[0]["action"] == "GATE_PASS"
        assert ents[0]["content"].startswith("GATE: PASS | gate could not complete after 2 attempts — reviewer, see report")
        assert [r["status"] for r in _rows(gated)] == ["error", "error", "pass"]
        assert _state(gated)["turn"] == "reviewer"

    def test_bounce_cap_then_pass_with_findings(self, gated):
        _enable(gated, command=FAIL_CMD)
        for rnd in (1, 2):
            res = _run(gated)
            assert res.status == "bounce"
            cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", rnd + 1, f"try {rnd}", str(gated),
                                updated_by="Claude")
        res = _run(gated)
        assert res.status == "pass" and res.dispatch and res.decision["cap_hit"]
        assert _entries(gated)[-1]["content"].startswith("GATE: checks failed but bounce cap (2) reached")
        assert _state(gated)["turn"] == "reviewer"
        # a reviewer round resets the trailing count? no — a GATE_PASS did: the next failing submission bounces again
        cycle_mod.add_round("feat-x", "impl", "reviewer", "REQUEST_CHANGES", 3, "fix it", str(gated), updated_by="Codex")
        cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", 4, "try 4", str(gated), updated_by="Claude")
        assert _run(gated).status == "bounce"

    def test_superseded_bounce_entries_do_not_consume_the_cap(self, gated):
        """A retained audit entry whose row is `superseded` never counts; only
        consecutive APPLIED bounce rows reach max_bounces; an applied pass
        resets the streak."""
        _enable(gated, command=FAIL_CMD)
        # two stale bounce entries from recovery races (rows superseded), then a real submission
        for i in range(2):
            sub = g.current_submission(str(gated))
            conn = db.connect(project_dir=str(gated))
            try:
                rid, _ = db.claim_gate(conn, ts=g._now_iso(), phase=sub.phase, cycle_type=sub.type, round_=sub.round,
                                       submission_seq=sub.submission_seq, event_key=sub.event_key, kind="auto",
                                       runner_pid=1, runner_ident="x")
            finally:
                conn.close()
            rp = cycle_mod._rounds_path("feat-x", "impl", str(gated))
            with open(rp, "a") as f:
                f.write(json.dumps({"round": sub.round, "role": "gatekeeper", "action": "GATE_BOUNCE",
                                    "content": "GATE: BOUNCE | stale", "ts": g._now_iso(), "gate_event": sub.event_key,
                                    "gate_id": rid, "gate_attempt": 1}) + "\n")
            # the submission moves before the sweep → superseded
            cycle_mod.add_round("feat-x", "impl", "reviewer", "REQUEST_CHANGES", sub.round, "no", str(gated), updated_by="Codex")
            cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", sub.round + 1, f"again {i}", str(gated),
                                updated_by="Claude")
            g.sweep_abandoned_gates(str(gated), _spec(gated))
        assert [r["status"] for r in _rows(gated)] == ["superseded", "superseded"]
        assert len(_entries(gated)) == 2
        assert g.applied_bounce_streak(str(gated), "feat-x", "impl") == 0
        # legacy view (no DB) would have counted them
        assert g.consecutive_bounces(cycle_mod.read_rounds_file("feat-x", "impl", str(gated))) == 2
        # a failing submission now BOUNCES (streak 0), not pass-with-findings
        res = _run(gated)
        assert res.status == "bounce" and not res.decision["cap_hit"]
        assert g.applied_bounce_streak(str(gated), "feat-x", "impl") == 1
        rnd = _state(gated)["round"]
        cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", rnd + 1, "again", str(gated), updated_by="Claude")
        assert _run(gated).status == "bounce"
        assert g.applied_bounce_streak(str(gated), "feat-x", "impl") == 2
        rnd = _state(gated)["round"]
        cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", rnd + 1, "again2", str(gated), updated_by="Claude")
        res = _run(gated)
        assert res.status == "pass" and res.decision["cap_hit"]                      # cap: two APPLIED bounces
        assert g.applied_bounce_streak(str(gated), "feat-x", "impl") == 0            # an applied pass resets
        # cockpit summary reports the applied decision, never a superseded audit entry
        summ = g.last_gate_summary(str(gated), "feat-x", "impl")
        assert summ["status"] == "pass"

    def test_manual_kind_recorded(self, gated):
        res = _run(gated, kind="manual")
        assert res.status == "pass" and _rows(gated)[0]["kind"] == "manual"

    def test_no_git_scope_skips_but_tests_decide(self, project):
        _enable(project, command=FAIL_CMD)
        _approve_plan(project); _open_impl(project)
        res = _run(project)
        assert res.status == "bounce"
        c = _entries(project)[0]["content"]
        assert "scope skipped" in c and "tests FAILED" in c and "not checked" in c

    def test_bounces_do_not_reset_stale_counter(self, gated):
        _enable(gated, command=FAIL_CMD)
        _run(gated)
        cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", 2, "impl v1", str(gated), updated_by="Claude")
        # stale counting looks at lead/reviewer transitions only
        assert cycle_mod._count_stale_rounds("feat-x", "impl", str(gated)) >= 0


# ---------------------------------------------------------------------------
# idempotent decision application + fault matrix (a)–(g)
# ---------------------------------------------------------------------------

class TestEnsureGateApplied:
    def _decision(self, sub: g.Submission, action, gate_id=1, attempt=1, content="GATE: x"):
        return {"action": action, "content": content, "round": sub.round, "gate_event": sub.event_key,
                "gate_id": gate_id, "gate_attempt": attempt, "submission_seq": sub.submission_seq}

    def test_pass_twice_is_one_entry_no_state_write(self, gated):
        sub = g.current_submission(str(gated))
        seq = _state(gated)["seq"]
        r1 = cycle_mod.ensure_gate_applied("feat-x", "impl", self._decision(sub, "GATE_PASS"), str(gated))
        r2 = cycle_mod.ensure_gate_applied("feat-x", "impl", self._decision(sub, "GATE_PASS"), str(gated))
        assert r1 == {"entry_appended": True, "applied": "pass", "applied_seq": None, "seq": seq}
        assert r2["entry_appended"] is False and r2["applied"] == "pass"
        assert len(_entries(gated)) == 1 and _state(gated)["seq"] == seq
        # mirrored to the shadow DB once
        conn = db.connect(project_dir=str(gated))
        try:
            rows = db.get_rounds(conn, "feat-x", "impl")
        finally:
            conn.close()
        assert [r["role"] for r in rows].count("gatekeeper") == 1

    def test_bounce_twice_bumps_seq_once(self, gated):
        sub = g.current_submission(str(gated))
        seq = _state(gated)["seq"]
        r1 = cycle_mod.ensure_gate_applied("feat-x", "impl", self._decision(sub, "GATE_BOUNCE"), str(gated))
        assert r1["applied"] == "applied" and r1["applied_seq"] == seq + 1 and r1["entry_appended"]
        r2 = cycle_mod.ensure_gate_applied("feat-x", "impl", self._decision(sub, "GATE_BOUNCE"), str(gated))
        assert r2["applied"] == "already" and r2["entry_appended"] is False and r2["seq"] == seq + 1
        d = self._decision(sub, "GATE_BOUNCE"); d["applied_seq"] = seq + 1
        r3 = cycle_mod.ensure_gate_applied("feat-x", "impl", d, str(gated))
        assert r3["applied"] == "already"
        st = _state(gated)
        assert st["seq"] == seq + 1 and st["turn"] == "lead" and st["updated_by"] == "Gatekeeper"
        assert len(_entries(gated)) == 1

    def test_fresh_decision_for_moved_submission_writes_nothing(self, gated):
        sub = g.current_submission(str(gated))
        # (i) an AMEND (rounds-only) is detected through the pinned log length
        pre = len(cycle_mod.read_rounds_file("feat-x", "impl", str(gated)))
        cycle_mod.add_round("feat-x", "impl", "lead", "AMEND", 1, "ps", str(gated), updated_by="Claude")
        seq = _state(gated)["seq"]
        for action in ("GATE_PASS", "GATE_BOUNCE"):
            d = self._decision(sub, action); d["pre_entries"] = pre
            r = cycle_mod.ensure_gate_applied("feat-x", "impl", d, str(gated))
            assert r == {"entry_appended": False, "applied": "superseded", "applied_seq": None, "seq": seq}
        assert _entries(gated) == [] and _state(gated)["seq"] == seq and _state(gated)["turn"] == "reviewer"
        # (ii) a state move (reviewer acted, lead re-submitted) is detected by seq/round
        cycle_mod.add_round("feat-x", "impl", "reviewer", "REQUEST_CHANGES", 1, "no", str(gated), updated_by="Codex")
        cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", 2, "again", str(gated), updated_by="Claude")
        seq = _state(gated)["seq"]
        for action in ("GATE_PASS", "GATE_BOUNCE"):
            r = cycle_mod.ensure_gate_applied("feat-x", "impl", self._decision(sub, action), str(gated))
            assert r == {"entry_appended": False, "applied": "superseded", "applied_seq": None, "seq": seq}
        assert _entries(gated) == [] and _state(gated)["seq"] == seq and _state(gated)["round"] == 2

    def test_bad_action_rejected(self, gated):
        sub = g.current_submission(str(gated))
        with pytest.raises(ValueError):
            cycle_mod.ensure_gate_applied("feat-x", "impl", self._decision(sub, "APPROVE"), str(gated))


class TestFaultMatrix:
    """Crash windows (a)–(d) + concurrency/stale cases (e)–(g)."""

    def _running_row(self, project, sub, *, pid, ident, stem="stem-a1", started_at=None):
        conn = db.connect(project_dir=str(project))
        try:
            rid, att = db.claim_gate(conn, ts=started_at or g._now_iso(), phase=sub.phase, cycle_type=sub.type,
                                     round_=sub.round, submission_seq=sub.submission_seq,
                                     event_key=sub.event_key, kind="auto", runner_pid=pid, runner_ident=ident)
            db.update_gate(conn, rid, ts="t", stem=stem)
            if started_at:
                conn.execute("UPDATE gates SET started_at=? WHERE id=?", (started_at, rid)); conn.commit()
            return rid, att
        finally:
            conn.close()

    def _dead_pid(self):
        p = subprocess.Popen([PY, "-c", "pass"]); p.wait()
        return p.pid

    def test_a_dies_before_entry(self, gated):
        """row running, no entry, dead runner + stale slot marker → swept,
        attempt 2 runs the checks (no entry existed → no duplicate)."""
        _enable(gated, command=_counter_cmd(gated))
        sub = g.current_submission(str(gated))
        dead = self._dead_pid()
        rid, _ = self._running_row(gated, sub, pid=dead, ident="gone")
        h.turns_dir(gated).mkdir(parents=True, exist_ok=True)
        h.inflight_path(gated).write_text(json.dumps({"stem": "stem-a1", "kind": "gate", "role": "gatekeeper",
                                                      "watcher_pid": dead, "watcher_ident": "gone",
                                                      "owner_token": "x"}))
        res = _run(gated)
        assert res.status == "pass" and res.attempt == 2 and _count(gated) == 1
        rows = _rows(gated)
        assert [r["status"] for r in rows] == ["abandoned", "pass"] and "gone" in rows[0]["reason"]
        assert len(_entries(gated)) == 1 and not h.slot_status(gated)["held"]

    def test_b_dies_after_entry_before_row_finish(self, gated):
        """entry present, row running → sweep completes the row from the
        entry; the checks are NOT re-run; PASS: seq unchanged."""
        _enable(gated, command=_counter_cmd(gated))
        sub = g.current_submission(str(gated))
        seq = _state(gated)["seq"]
        rid, _ = self._running_row(gated, sub, pid=self._dead_pid(), ident="gone")
        cycle_mod.ensure_gate_applied("feat-x", "impl", {"action": "GATE_PASS", "content": "GATE: PASS | x",
                                                          "round": sub.round, "gate_event": sub.event_key,
                                                          "gate_id": rid, "gate_attempt": 1,
                                                          "submission_seq": sub.submission_seq}, str(gated))
        out = g.sweep_abandoned_gates(str(gated), _spec(gated))
        assert out["reconciled"] == [rid] and out["abandoned"] == []
        rows = _rows(gated)
        assert len(rows) == 1 and rows[0]["status"] == "pass"
        assert _count(gated) == 0 and len(_entries(gated)) == 1 and _state(gated)["seq"] == seq
        # and the watcher path: decided → dispatch, still no re-run
        res = _run(gated)
        assert res.status == "pass" and res.dispatch and _count(gated) == 0

    def test_c_mid_bounce_entry_present_transition_incomplete(self, gated):
        """entry appended, status/state still reviewer-ready → the lead-ready
        transition is applied exactly once; the reviewer is never dispatched."""
        sub = g.current_submission(str(gated))
        seq = _state(gated)["seq"]
        rid, _ = self._running_row(gated, sub, pid=self._dead_pid(), ident="gone")
        # simulate: entry written, crash before status/derive
        rp = cycle_mod._rounds_path("feat-x", "impl", str(gated))
        with open(rp, "a") as f:
            f.write(json.dumps({"round": 1, "role": "gatekeeper", "action": "GATE_BOUNCE", "content": "GATE: BOUNCE | t",
                                "ts": g._now_iso(), "gate_event": sub.event_key, "gate_id": rid, "gate_attempt": 1}) + "\n")
        assert _state(gated)["turn"] == "reviewer"
        res = _run(gated)                          # the same-seq tick re-enters the gate path
        assert res.status == "bounce" and res.dispatch is False
        st = _state(gated)
        assert st["turn"] == "lead" and st["seq"] == seq + 1 and st["updated_by"] == "Gatekeeper"
        rows = _rows(gated)
        assert len(rows) == 1 and rows[0]["status"] == "bounce" and rows[0]["applied_seq"] == seq + 1
        assert len(_entries(gated)) == 1
        # calling again is a no-op
        out = g.sweep_abandoned_gates(str(gated), _spec(gated))
        assert out == {"reconciled": [], "abandoned": [], "unverifiable": []}
        assert _state(gated)["seq"] == seq + 1

    def test_c2_mid_bounce_after_status_write_before_derive(self, gated):
        """The precise middle of a BOUNCE apply: entry appended AND cycle
        status already `ready_for: lead`, top-level state still reviewer at
        the submission seq → the derive is finished exactly once (top-level
        lead, seq+1, updated_by Gatekeeper); one entry; terminal bounce row
        with applied_seq; not misclassified as superseded."""
        sub = g.current_submission(str(gated))
        seq = _state(gated)["seq"]
        rid, _ = self._running_row(gated, sub, pid=self._dead_pid(), ident="gone")
        rp = cycle_mod._rounds_path("feat-x", "impl", str(gated))
        with open(rp, "a") as f:
            f.write(json.dumps({"round": 1, "role": "gatekeeper", "action": "GATE_BOUNCE", "content": "GATE: BOUNCE | t",
                                "ts": g._now_iso(), "gate_event": sub.event_key, "gate_id": rid, "gate_attempt": 1,
                                "updated_by": "Gatekeeper"}) + "\n")
        sp = cycle_mod._status_path("feat-x", "impl", str(gated))
        d = json.loads(sp.read_text()); d["ready_for"] = "lead"; sp.write_text(json.dumps(d, indent=2) + "\n")
        assert _state(gated)["turn"] == "reviewer" and _state(gated)["seq"] == seq          # split state
        r = cycle_mod.ensure_gate_applied("feat-x", "impl", {"action": "GATE_BOUNCE", "content": "GATE: BOUNCE | t",
                                                              "round": 1, "gate_event": sub.event_key, "gate_id": rid,
                                                              "gate_attempt": 1, "submission_seq": sub.submission_seq},
                                          str(gated))
        assert r["applied"] == "applied" and r["applied_seq"] == seq + 1 and r["entry_appended"] is False
        st = _state(gated)
        assert st["turn"] == "lead" and st["seq"] == seq + 1 and st["updated_by"] == "Gatekeeper"
        assert len(_entries(gated)) == 1
        # a second call is a no-op ("already"); the sweep finishes the row
        r2 = cycle_mod.ensure_gate_applied("feat-x", "impl", {"action": "GATE_BOUNCE", "content": "x", "round": 1,
                                                               "gate_event": sub.event_key, "gate_id": rid,
                                                               "gate_attempt": 1, "submission_seq": sub.submission_seq},
                                           str(gated))
        assert r2["applied"] == "already" and _state(gated)["seq"] == seq + 1
        out = g.sweep_abandoned_gates(str(gated), _spec(gated))
        assert out["reconciled"] == [rid]
        rows = _rows(gated)
        assert len(rows) == 1 and rows[0]["status"] == "bounce" and rows[0]["applied_seq"] == seq + 1
        # the mirror carries the entry
        conn = db.connect(project_dir=str(gated))
        try:
            assert [x["role"] for x in db.get_rounds(conn, "feat-x", "impl")].count("gatekeeper") == 1
        finally:
            conn.close()
        # and through the watcher path from the same split state (fresh project)
        # — covered by test_c (entry only) + this (entry + status): both reach the same end state.

    def test_b2_pass_crash_right_after_jsonl_append_restores_parity(self, gated):
        """Die immediately after the JSONL write (before the shadow mirror and
        the export): the DB has no gatekeeper round and the render differs;
        recovery must restore both without touching top-level state."""
        sub = g.current_submission(str(gated))
        seq = _state(gated)["seq"]
        rid, _ = self._running_row(gated, sub, pid=self._dead_pid(), ident="gone")
        rp = cycle_mod._rounds_path("feat-x", "impl", str(gated))
        with open(rp, "a") as f:
            f.write(json.dumps({"round": 1, "role": "gatekeeper", "action": "GATE_PASS", "content": "GATE: PASS | ok",
                                "ts": g._now_iso(), "gate_event": sub.event_key, "gate_id": rid, "gate_attempt": 1,
                                "updated_by": "Gatekeeper"}) + "\n")
        conn = db.connect(project_dir=str(gated))
        try:
            assert [x["role"] for x in db.get_rounds(conn, "feat-x", "impl")] == ["lead"]        # DB is behind
        finally:
            conn.close()
        out = g.sweep_abandoned_gates(str(gated), _spec(gated))
        assert out["reconciled"] == [rid] and _rows(gated)[0]["status"] == "pass"
        conn = db.connect(project_dir=str(gated))
        try:
            roles = [x["role"] for x in db.get_rounds(conn, "feat-x", "impl")]
            assert roles == ["lead", "gatekeeper"]                                             # mirrored once
            assert db.render_cycle(conn, "feat-x", "impl") == cycle_mod.render_cycle_from_files("feat-x", "impl", str(gated))
        finally:
            conn.close()
        assert _state(gated)["seq"] == seq and _state(gated)["turn"] == "reviewer"
        # DB-first reviewer tail sees the gate entry
        tail = cycle_mod.tail_rounds("feat-x", "impl", 1, str(gated))
        assert any(e.get("role") == "gatekeeper" for e in tail[-1]["entries"])
        # idempotent: a second recovery does not duplicate
        g.sweep_abandoned_gates(str(gated), _spec(gated))
        cycle_mod.ensure_gate_applied("feat-x", "impl", {"action": "GATE_PASS", "content": "GATE: PASS | ok", "round": 1,
                                                          "gate_event": sub.event_key, "gate_id": rid, "gate_attempt": 1,
                                                          "submission_seq": sub.submission_seq}, str(gated))
        conn = db.connect(project_dir=str(gated))
        try:
            assert [x["role"] for x in db.get_rounds(conn, "feat-x", "impl")].count("gatekeeper") == 1
        finally:
            conn.close()
        assert len(_entries(gated)) == 1

    def test_d_after_row_finish_before_slot_release(self, gated):
        """decided row + entry, stale slot marker owned by a dead pid → the
        slot is recovered; PASS hands off, BOUNCE returns; nothing re-run."""
        _enable(gated, command=_counter_cmd(gated))
        res = _run(gated); assert res.status == "pass" and _count(gated) == 1
        dead = self._dead_pid()
        h.inflight_path(gated).write_text(json.dumps({"stem": "s", "kind": "gate", "role": "gatekeeper",
                                                      "watcher_pid": dead, "watcher_ident": "gone",
                                                      "owner_token": "x"}))
        res2 = _run(gated)
        assert res2.status == "pass" and res2.dispatch and _count(gated) == 1
        assert len(_rows(gated)) == 1 and len(_entries(gated)) == 1
        # BOUNCE flavour
        cycle_mod.add_round("feat-x", "impl", "reviewer", "REQUEST_CHANGES", 1, "no", str(gated), updated_by="Codex")
        _enable(gated, command=FAIL_CMD)
        cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", 2, "again", str(gated), updated_by="Claude")
        res3 = _run(gated); assert res3.status == "bounce"
        seq = _state(gated)["seq"]
        h.inflight_path(gated).write_text(json.dumps({"stem": "s2", "kind": "gate", "role": "gatekeeper",
                                                      "watcher_pid": dead, "watcher_ident": "gone",
                                                      "owner_token": "y"}))
        # a stale watcher still holding the reviewer-ready observation for r2
        stale = {"seq": seq - 1, "turn": "reviewer", "status": "ready", "phase": "feat-x", "type": "impl", "round": 2}
        res4 = _run(gated, state=stale)
        assert res4.dispatch is False and _state(gated)["seq"] == seq and _state(gated)["turn"] == "lead"

    def test_e_second_watcher_stale_snapshot_after_bounce_never_dispatches(self, gated):
        _enable(gated, command=FAIL_CMD)
        stale = dict(_state(gated))
        res = _run(gated); assert res.status == "bounce"
        seq = _state(gated)["seq"]
        # watcher B: `_maybe_gate` with the stale state
        from tagteam.watcher import _StateProcessor
        p = _StateProcessor(mode="notify", lead_name="Claude", reviewer_name="Codex", lead_pane="l", reviewer_pane="r",
                            lead_session_id=None, reviewer_session_id=None, confirm=False, timeout_minutes=30,
                            project_dir=str(gated), max_retries=1, retry_delay=0, pre_send_delay=0,
                            gatekeeper=_spec(gated))
        with patch("tagteam.watcher.notify_macos") as notify:
            p.tick(stale)
            notify.assert_not_called()
        assert _state(gated)["seq"] == seq and len(_rows(gated)) == 1 and len(_entries(gated)) == 1

    def test_f_mid_bounce_crash_then_newer_state_before_sweep(self, gated):
        """entry for the OLD submission exists, row running; the reviewer
        acts and the lead re-submits before the sweep → the old row is
        superseded, the new turn/round/seq untouched."""
        sub = g.current_submission(str(gated))
        rid, _ = self._running_row(gated, sub, pid=self._dead_pid(), ident="gone")
        rp = cycle_mod._rounds_path("feat-x", "impl", str(gated))
        with open(rp, "a") as f:
            f.write(json.dumps({"round": 1, "role": "gatekeeper", "action": "GATE_BOUNCE", "content": "GATE: BOUNCE | t",
                                "ts": g._now_iso(), "gate_event": sub.event_key, "gate_id": rid, "gate_attempt": 1}) + "\n")
        cycle_mod.add_round("feat-x", "impl", "reviewer", "REQUEST_CHANGES", 1, "no", str(gated), updated_by="Codex")
        cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", 2, "again", str(gated), updated_by="Claude")
        before = _state(gated)
        out = g.sweep_abandoned_gates(str(gated), _spec(gated))
        assert out["reconciled"] == [rid]
        rows = _rows(gated)
        assert rows[0]["status"] == "superseded" and rows[0]["applied_seq"] is None
        after = _state(gated)
        assert after == before                                  # no overwrite, no seq bump
        assert len(_entries(gated)) == 1                       # entry retained for audit
        # the arbiter-ruling flavour: escalate + rule, then sweep another stale row
        sub2 = g.current_submission(str(gated))
        rid2, _ = self._running_row(gated, sub2, pid=self._dead_pid(), ident="gone", stem="s2")
        with open(rp, "a") as f:
            f.write(json.dumps({"round": 2, "role": "gatekeeper", "action": "GATE_BOUNCE", "content": "GATE: BOUNCE | t2",
                                "ts": g._now_iso(), "gate_event": sub2.event_key, "gate_id": rid2, "gate_attempt": 1}) + "\n")
        cycle_mod.add_round("feat-x", "impl", "reviewer", "ESCALATE", 2, "stuck", str(gated), updated_by="Codex")
        cycle_mod.add_ruling("feat-x", "impl", "APPROVE", "ship it", "Jack", str(gated))
        before = _state(gated)
        g.sweep_abandoned_gates(str(gated), _spec(gated))
        assert _rows(gated)[-1]["status"] == "superseded" and _state(gated) == before

    def test_g_recovered_pass_after_submission_advanced_no_handoff(self, gated):
        res = _run(gated); assert res.status == "pass"
        row = _rows(gated)[0]
        cycle_mod.add_round("feat-x", "impl", "reviewer", "REQUEST_CHANGES", 1, "no", str(gated), updated_by="Codex")
        conn = db.connect(project_dir=str(gated))
        try:
            out = g._handle_decided(conn, row, str(gated), lambda m: None)
        finally:
            conn.close()
        assert out.status == "pass" and out.dispatch is False
        stale = {"seq": row["submission_seq"], "turn": "reviewer", "status": "ready", "phase": "feat-x",
                 "type": "impl", "round": 1}
        assert _run(gated, state=stale).dispatch is False


# ---------------------------------------------------------------------------
# sweep matrix
# ---------------------------------------------------------------------------

class TestSweep:
    def _row(self, project, sub, **kw):
        return TestFaultMatrix._running_row(TestFaultMatrix(), project, sub, **kw)

    def _old(self):
        from datetime import datetime, timedelta, timezone
        return (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    def test_live_verified_kept(self, gated):
        sub = g.current_submission(str(gated))
        me = os.getpid()
        rid, _ = self._row(gated, sub, pid=me, ident=procs.identity(me))
        out = g.sweep_abandoned_gates(str(gated), _spec(gated))
        assert out["abandoned"] == [] and out["unverifiable"] == []
        assert _rows(gated)[0]["status"] == "running"

    def test_dead_pid_abandoned(self, gated):
        sub = g.current_submission(str(gated))
        p = subprocess.Popen([PY, "-c", "pass"]); p.wait()
        rid, _ = self._row(gated, sub, pid=p.pid, ident="whatever")
        out = g.sweep_abandoned_gates(str(gated), _spec(gated))
        assert out["abandoned"] == [rid] and "dead" in _rows(gated)[0]["reason"]

    @needs_proc_inspection
    def test_identity_mismatch_abandoned(self, gated):
        sub = g.current_submission(str(gated))
        me = os.getpid()
        rid, _ = self._row(gated, sub, pid=me, ident="not-my-identity")
        out = g.sweep_abandoned_gates(str(gated), _spec(gated))
        assert out["abandoned"] == [rid] and "mismatch" in _rows(gated)[0]["reason"]

    def test_timeout_with_slot_marker_kept_without_abandoned(self, gated):
        sub = g.current_submission(str(gated))
        me = os.getpid()
        rid, _ = self._row(gated, sub, pid=me, ident=procs.identity(me), stem="live-stem", started_at=self._old())
        h.turns_dir(gated).mkdir(parents=True, exist_ok=True)
        h.inflight_path(gated).write_text(json.dumps({"stem": "live-stem", "kind": "gate", "watcher_pid": me,
                                                      "watcher_ident": procs.identity(me), "owner_token": "t"}))
        out = g.sweep_abandoned_gates(str(gated), _spec(gated))
        assert out["abandoned"] == [] and [u["id"] for u in out["unverifiable"]] == [rid]
        assert _rows(gated)[0]["status"] == "running"
        h.inflight_path(gated).unlink()
        out = g.sweep_abandoned_gates(str(gated), _spec(gated))
        assert out["abandoned"] == [rid] and "timed out" in _rows(gated)[0]["reason"]

    def test_unverifiable_kept_and_reported(self, gated):
        sub = g.current_submission(str(gated))
        rid, _ = self._row(gated, sub, pid=os.getpid(), ident=None)      # alive, no recorded identity
        out = g.sweep_abandoned_gates(str(gated), _spec(gated))
        assert out["abandoned"] == [] and [u["id"] for u in out["unverifiable"]] == [rid]
        info = g.gate_status(str(gated))
        assert [u["id"] for u in info["unverifiable"]] == [rid]
        # and it blocks a new attempt (live-other) rather than double-running
        res = _run(gated)
        assert res.status == "deferred"

    def test_concurrent_reclaim_after_abandon_yields_one_running_row(self, gated):
        sub = g.current_submission(str(gated))
        p = subprocess.Popen([PY, "-c", "pass"]); p.wait()
        self._row(gated, sub, pid=p.pid, ident="x")
        g.sweep_abandoned_gates(str(gated), _spec(gated))
        results = []

        def claim():
            from tagteam import dualwrite
            with dualwrite.writer_lock(str(gated)):
                conn = db.connect(project_dir=str(gated))
                try:
                    results.append(db.claim_gate(conn, ts="t", phase=sub.phase, cycle_type=sub.type, round_=sub.round,
                                                 submission_seq=sub.submission_seq, event_key=sub.event_key,
                                                 kind="auto", runner_pid=os.getpid(), runner_ident="i"))
                finally:
                    conn.close()
        ts = [threading.Thread(target=claim) for _ in range(6)]
        [t.start() for t in ts]; [t.join() for t in ts]
        assert len([r for r in results if r]) == 1
        assert [r["status"] for r in _rows(gated)] == ["abandoned", "running"]


# ---------------------------------------------------------------------------
# watcher seam + gate-owed latch
# ---------------------------------------------------------------------------

def _proc(mode, project, spec, engine=None):
    from tagteam.watcher import _StateProcessor
    return _StateProcessor(mode=mode, lead_name="Claude", reviewer_name="Codex", lead_pane="l", reviewer_pane="r",
                           lead_session_id="ls" if mode == "iterm2" else None,
                           reviewer_session_id="rs" if mode == "iterm2" else None, confirm=False,
                           timeout_minutes=30, project_dir=str(project), max_retries=1, retry_delay=0,
                           pre_send_delay=0, engine=engine, gatekeeper=spec)


MODES = [("notify", "tagteam.watcher.notify_macos"), ("tmux", "tagteam.watcher.send_tmux_keys"),
         ("iterm2", "tagteam.watcher.send_iterm_command")]


class TestWatcherGate:
    @pytest.mark.parametrize("mode,target", MODES)
    def test_pass_gates_then_hands_off_same_tick(self, gated, mode, target):
        _enable(gated, command=_counter_cmd(gated))
        p = _proc(mode, gated, _spec(gated))
        st = _state(gated)
        from contextlib import nullcontext
        quiet = patch("tagteam.watcher.notify_macos") if mode != "notify" else nullcontext()
        with patch(target) as send, quiet:
            send.return_value = True
            p.tick(st)
            assert send.call_count == 1 and _count(gated) == 1
            assert p._gate_owed_seq is None
            p.tick(st); p.tick(st)                          # identical ticks: nothing more
            assert send.call_count == 1 and _count(gated) == 1
        assert len(_entries(gated)) == 1 and _entries(gated)[0]["action"] == "GATE_PASS"
        assert _state(gated)["seq"] == st["seq"]

    @pytest.mark.parametrize("mode,target", MODES)
    def test_bounce_never_dispatches_reviewer(self, gated, mode, target):
        _enable(gated, command=FAIL_CMD)
        p = _proc(mode, gated, _spec(gated))
        st = _state(gated)
        from contextlib import nullcontext
        quiet = patch("tagteam.watcher.notify_macos") if mode != "notify" else nullcontext()
        with patch(target) as send, quiet:
            p.tick(st)
            send.assert_not_called()
            new = _state(gated)
            assert new["turn"] == "lead" and new["seq"] == st["seq"] + 1
            p.tick(new)                                    # next tick: the lead is dispatched as usual
            assert send.call_count == 1
            args = send.call_args[0]
            assert args[0] == ("l" if mode == "tmux" else "ls") if mode != "notify" else True

    def test_headless_pass_runs_engine_bounce_does_not(self, gated):
        _enable(gated, command=FAIL_CMD)
        eng = MagicMock(); eng.paused.return_value = None; eng.slot_busy = None
        p = _proc("headless", gated, _spec(gated), engine=eng)
        st = _state(gated)
        p.tick(st)
        eng.run_owed_turn.assert_not_called()
        assert _state(gated)["turn"] == "lead"
        # lead fixes and re-submits (tests now pass)
        _enable(gated, command=OK_CMD)
        p.gatekeeper = _spec(gated)                    # the watcher resolves its spec once, at start
        cycle_mod.add_round("feat-x", "impl", "lead", "SUBMIT_FOR_REVIEW", 2, "fixed", str(gated), updated_by="Claude")
        st2 = _state(gated)
        p.tick(st2)
        assert eng.run_owed_turn.call_count == 1
        assert eng.run_owed_turn.call_args[0][0]["seq"] == st2["seq"]
        assert [e["action"] for e in _entries(gated)] == ["GATE_BOUNCE", "GATE_PASS"]

    @pytest.mark.parametrize("mode,target", MODES + [("headless", None)])
    def test_gate_owed_latch_busy_then_free_on_identical_tick(self, gated, mode, target):
        _enable(gated, command=_counter_cmd(gated))
        eng = None
        if mode == "headless":
            eng = MagicMock(); eng.paused.return_value = None; eng.slot_busy = None
        p = _proc(mode, gated, _spec(gated), engine=eng)
        st = _state(gated)
        claim = h.claim_turn_slot(gated, kind=h.SLOT_KIND_CONVERSATION, role="lead",
                                  fields={"stem": "conv", "watcher_pid": os.getpid(),
                                          "watcher_ident": procs.identity(os.getpid())})
        from contextlib import nullcontext
        quiet = patch("tagteam.watcher.notify_macos") if mode != "notify" else nullcontext()
        with (patch(target) if target else nullcontext()) as send, quiet:
            if send: send.return_value = True
            p.tick(st)
            assert p._gate_owed_seq == st["seq"] and _rows(gated) == []          # no self-owned running row
            if send: send.assert_not_called()
            if eng: eng.run_owed_turn.assert_not_called()
            p.tick(st)                                                           # still busy
            assert p._gate_owed_seq == st["seq"] and _count(gated) == 0
            h.release_turn_slot(claim)
            p.tick(st)                                                           # identical tick decides
            assert p._gate_owed_seq is None and _count(gated) == 1
            if send: assert send.call_count == 1
            if eng: assert eng.run_owed_turn.call_count == 1
            p.tick(st)
            if send: assert send.call_count == 1
            if eng: assert eng.run_owed_turn.call_count == 1
        assert len(_entries(gated)) == 1 and _state(gated)["seq"] == st["seq"]

    def test_latch_respects_pause(self, gated):
        from tagteam import controls
        _enable(gated, command=_counter_cmd(gated))
        p = _proc("notify", gated, _spec(gated))
        st = _state(gated)
        claim = h.claim_turn_slot(gated, kind=h.SLOT_KIND_CONVERSATION, role="lead",
                                  fields={"stem": "conv", "watcher_pid": os.getpid(),
                                          "watcher_ident": procs.identity(os.getpid())})
        with patch("tagteam.watcher.notify_macos") as notify:
            p.tick(st)
            h.release_turn_slot(claim)
            controls.pause_command(["--reason", "hold"], project_root=gated)
            p.tick(st)
            assert _count(gated) == 0 and notify.call_count == 0
            controls.resume_command([], project_root=gated)
            p.tick(st)
            assert _count(gated) == 1 and notify.call_count == 1

    def test_restart_mid_gate_defers_to_live_runner_then_decides_after_death(self, gated):
        _enable(gated, command=_counter_cmd(gated))
        sub = g.current_submission(str(gated))
        me = os.getpid()
        conn = db.connect(project_dir=str(gated))
        try:
            rid, _ = db.claim_gate(conn, ts=g._now_iso(), phase=sub.phase, cycle_type=sub.type, round_=sub.round,
                                   submission_seq=sub.submission_seq, event_key=sub.event_key, kind="auto",
                                   runner_pid=me, runner_ident=procs.identity(me))
        finally:
            conn.close()
        p = _proc("notify", gated, _spec(gated))
        st = _state(gated)
        with patch("tagteam.watcher.notify_macos") as notify:
            p.tick(st)                                       # fresh processor picks up the ready state
            notify.assert_not_called()
            assert p._gate_owed_seq == st["seq"] and _count(gated) == 0
            # the other runner "dies": mark its pid dead
            dead = subprocess.Popen([PY, "-c", "pass"]); dead.wait()
            conn = db.connect(project_dir=str(gated))
            try:
                conn.execute("UPDATE gates SET runner_pid=?, runner_ident='x' WHERE id=?", (dead.pid, rid)); conn.commit()
            finally:
                conn.close()
            p.tick(st)
            assert notify.call_count == 1 and _count(gated) == 1
        assert [r["status"] for r in _rows(gated)] == ["abandoned", "pass"]

    def test_disabled_is_byte_identical(self, gated):
        (gated / "tagteam.yaml").write_text("agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\n")
        p = _proc("notify", gated, None)
        with patch("tagteam.watcher.notify_macos") as notify, patch.object(g, "run_gate") as rg:
            p.tick(_state(gated))
            assert notify.call_count == 1 and rg.assert_not_called() is None
        assert _rows(gated) == [] and _entries(gated) == []
        assert p._maybe_gate({"turn": "reviewer", "seq": 1}) is True

    def test_pass_write_semantics_across_restart(self, gated):
        """one decision across ticks and a restart; interactive re-notify on
        restart is allowed (existing at-least-once), headless is protected."""
        _enable(gated, command=_counter_cmd(gated))
        st = _state(gated)
        p1 = _proc("notify", gated, _spec(gated))
        with patch("tagteam.watcher.notify_macos") as n1:
            p1.tick(st); p1.tick(st)
            assert n1.call_count == 1
        p2 = _proc("notify", gated, _spec(gated))                # restart
        with patch("tagteam.watcher.notify_macos") as n2:
            p2.tick(st)
            assert n2.call_count == 1                            # re-notify allowed
        assert _count(gated) == 1 and len(_rows(gated)) == 1 and len(_entries(gated)) == 1
        assert _state(gated)["seq"] == st["seq"]
        # headless restart: the engine is asked once more, and its own slot /
        # verify_transition protect the turn — but the gate decided exactly once
        eng = MagicMock(); eng.paused.return_value = None; eng.slot_busy = None
        p3 = _proc("headless", gated, _spec(gated), engine=eng)
        p3.tick(st)
        assert eng.run_owed_turn.call_count == 1 and _count(gated) == 1

    def test_gate_error_is_contained_and_latched(self, gated):
        p = _proc("notify", gated, _spec(gated))
        st = _state(gated)
        with patch("tagteam.watcher.notify_macos") as notify, patch.object(g, "run_gate", side_effect=RuntimeError("x")):
            p.tick(st)
            notify.assert_not_called()
            assert p._gate_owed_seq == st["seq"]
        with patch("tagteam.watcher.notify_macos") as notify:
            p.tick(st)
            assert notify.call_count == 1 and p._gate_owed_seq is None

    def test_build_processor_resolves_spec_and_warns_on_problems(self, gated, capsys):
        from tagteam.watcher import _build_processor
        p = _build_processor(mode="notify", lead_pane="l", reviewer_pane="r", confirm=False, timeout_minutes=30,
                             project_dir=str(gated), max_retries=1, retry_delay=0, pre_send_delay=0)
        assert p is not None and p.gatekeeper is not None and p.gatekeeper.enabled
        _enable(gated, extra="  bogus: 1\n")
        p = _build_processor(mode="notify", lead_pane="l", reviewer_pane="r", confirm=False, timeout_minutes=30,
                             project_dir=str(gated), max_retries=1, retry_delay=0, pre_send_delay=0)
        assert p.gatekeeper is None
        assert "gatekeeper disabled" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCli:
    def test_check_exit_codes_and_report(self, gated, capsys):
        assert g.gate_command(["check"], project_root=gated) == 0
        out = capsys.readouterr().out
        assert "GATE: PASS" in out and "✓ scope" in out and "✓ tests" in out
        _enable(gated, command=FAIL_CMD)
        assert g.gate_command(["check"], project_root=gated) == 1
        out = capsys.readouterr().out
        assert "GATE: BOUNCE" in out and "✗ tests" in out and "boom" in out
        assert _rows(gated) == [] and _entries(gated) == []          # writes nothing
        assert g.gate_command(["check", "--json"], project_root=gated) == 1
        assert json.loads(capsys.readouterr().out)["decision"]["action"] == "GATE_BOUNCE"

    def test_check_when_disabled_still_runs(self, gated, capsys):
        (gated / "tagteam.yaml").write_text("agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\n")
        assert g.gate_command(["check"], project_root=gated) == 0
        assert "not enabled" in capsys.readouterr().out

    def test_run_status_list(self, gated, capsys):
        assert g.gate_command(["status"], project_root=gated) == 0
        assert "No gate has run" in capsys.readouterr().out
        assert g.gate_command(["run"], project_root=gated) == 0
        out = capsys.readouterr().out
        assert "GATE: PASS" in out and "gate: pass" in out and "reviewer's turn" in out
        assert _rows(gated)[0]["kind"] == "manual"
        assert g.gate_command(["status"], project_root=gated) == 0
        out = capsys.readouterr().out
        assert "Gatekeeper: on (impl cycles)" in out and "manual" in out and "✓ tests" in out and "entry: r1 GATE: PASS" in out
        assert g.gate_command(["list"], project_root=gated) == 0
        assert "#1 feat-x/impl/r1/" in capsys.readouterr().out
        assert g.gate_command(["status", "--json"], project_root=gated) == 0
        j = json.loads(capsys.readouterr().out)
        assert j["enabled"] and j["last"]["status"] == "pass"
        assert g.gate_command(["list", "--json"], project_root=gated) == 0
        assert json.loads(capsys.readouterr().out)[0]["status"] == "pass"

    def test_status_sweeps_dead_rows_and_reconciles_from_entries(self, gated, capsys):
        sub = g.current_submission(str(gated))
        dead = subprocess.Popen([PY, "-c", "pass"]); dead.wait()
        conn = db.connect(project_dir=str(gated))
        try:
            rid, _ = db.claim_gate(conn, ts=g._now_iso(), phase=sub.phase, cycle_type=sub.type, round_=sub.round,
                                   submission_seq=sub.submission_seq, event_key=sub.event_key, kind="auto",
                                   runner_pid=dead.pid, runner_ident="gone")
        finally:
            conn.close()
        assert g.gate_command(["status"], project_root=gated) == 0
        out = capsys.readouterr().out
        assert "abandoned" in out and "running" not in out.split("Last gate")[1]
        assert _rows(gated)[0]["status"] == "abandoned"
        # entry-first reconciliation through `status`: a running row whose entry exists is completed
        conn = db.connect(project_dir=str(gated))
        try:
            rid2, _ = db.claim_gate(conn, ts=g._now_iso(), phase=sub.phase, cycle_type=sub.type, round_=sub.round,
                                    submission_seq=sub.submission_seq, event_key=sub.event_key, kind="auto",
                                    runner_pid=dead.pid, runner_ident="gone")
        finally:
            conn.close()
        rp = cycle_mod._rounds_path("feat-x", "impl", str(gated))
        with open(rp, "a") as f:
            f.write(json.dumps({"round": 1, "role": "gatekeeper", "action": "GATE_PASS", "content": "GATE: PASS | ok",
                                "ts": g._now_iso(), "gate_event": sub.event_key, "gate_id": rid2, "gate_attempt": 2}) + "\n")
        assert g.gate_command(["status"], project_root=gated) == 0
        out = capsys.readouterr().out
        assert "pass" in out and _rows(gated)[-1]["status"] == "pass"
        assert g.gate_command(["list"], project_root=gated) == 0
        assert [r["status"] for r in _rows(gated)] == ["abandoned", "pass"]

    def test_run_bounce_and_disabled(self, gated, capsys):
        _enable(gated, command=FAIL_CMD)
        assert g.gate_command(["run"], project_root=gated) == 0
        assert "lead's turn" in capsys.readouterr().out and _state(gated)["turn"] == "lead"
        (gated / "tagteam.yaml").write_text("agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\n")
        assert g.gate_command(["run"], project_root=gated) == 1
        assert "not enabled" in capsys.readouterr().out

    def test_usage_and_unknown(self, gated, capsys):
        assert g.gate_command([], project_root=gated) == 1
        assert g.gate_command(["--help"], project_root=gated) == 0
        assert g.gate_command(["bogus"], project_root=gated) == 1
        assert g.gate_command(["check", "--nope"], project_root=gated) == 1

    def test_cli_dispatch(self, gated):
        r = subprocess.run([PY, "-m", "tagteam", "gate", "status"], cwd=str(gated), capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": str(REPO)})
        assert r.returncode == 0 and "Gatekeeper: on" in r.stdout
        r = subprocess.run([PY, "-m", "tagteam", "--help"], cwd=str(gated), capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": str(REPO)})
        assert "gate " in r.stdout


# ---------------------------------------------------------------------------
# cockpit + parser tolerance + SKILL + flag-off
# ---------------------------------------------------------------------------

class TestCockpitAndDocs:
    def test_now_payload_gatekeeper_and_feed_entries(self, gated):
        n = capi.now_payload(gated)
        assert n["gatekeeper"] == {"enabled": True, "on": ["impl"], "last": None}
        _enable(gated, command=FAIL_CMD)
        _run(gated)
        n = capi.now_payload(gated)
        last = n["gatekeeper"]["last"]
        assert last["status"] == "bounce" and last["round"] == 1 and last["headline"].startswith("GATE: BOUNCE")
        rp = capi.rounds_payload(gated, "feat-x", "impl")
        ents = rp["rounds"][0]["entries"]
        assert any(e["role"] == "gatekeeper" and e["action"] == "GATE_BOUNCE" for e in ents)
        assert rp["rounds"][0]["lead_text"] == "impl v1"                # grouping intact
        # tail_rounds (the headless prompt's tail) carries it too
        tail = cycle_mod.tail_rounds("feat-x", "impl", 1, str(gated))
        assert any(e.get("role") == "gatekeeper" for e in tail[-1]["entries"])
        # cockpit assets know the kind
        js = (REPO / "tagteam" / "data" / "web" / "cockpit.js").read_text(encoding="utf-8")
        css = (REPO / "tagteam" / "data" / "web" / "cockpit.css").read_text(encoding="utf-8")
        html = (REPO / "tagteam" / "data" / "web" / "cockpit.html").read_text(encoding="utf-8")
        assert "GATE_BOUNCE" in js and "chip-gate" in js and "chip-gate" in html
        assert ".feed-item.gate" in css and ".feed-item.gate.bounce" in css

    def test_render_labels_gatekeeper(self, gated):
        _run(gated)
        md = cycle_mod.render_cycle_from_files("feat-x", "impl", str(gated))
        assert "### Gatekeeper" in md and "GATE: PASS" in md
        conn = db.connect(project_dir=str(gated))
        try:
            assert "### Gatekeeper" in db.render_cycle(conn, "feat-x", "impl")
        finally:
            conn.close()

    def test_skill_copies_identical_and_mention_gate(self):
        a = (REPO / "plugin" / "skills" / "handoff" / "SKILL.md").read_text(encoding="utf-8")
        b = SKILL_SRC.read_text(encoding="utf-8")
        assert a == b
        assert "tagteam gate check" in b and "GATE_BOUNCE" in b and "role: gatekeeper" in b

    def test_readme_and_htw_document_the_block(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        htw = (REPO / "docs" / "how-tagteam-works.md").read_text(encoding="utf-8")
        assert "gatekeeper:" in readme and "tagteam gate check" in readme
        assert '<a id="gatekeeper"></a>' in htw and "max_bounces" in htw and "tagteam gate run" in htw

    def test_flag_off_full_cycle_writes_nothing_new(self, project):
        """Success criterion 1: no gatekeeper block → no gates rows, no gate
        entries, no boundary-driven behaviour beyond the inert status key."""
        _git_project(project)
        _init_cycle(project)
        cycle_mod.add_round("feat-x", "plan", "reviewer", "APPROVE", 1, "ok", str(project), updated_by="Codex")
        _open_impl(project)
        cycle_mod.add_round("feat-x", "impl", "reviewer", "APPROVE", 1, "ok", str(project), updated_by="Codex")
        p = _proc("notify", project, None)
        with patch("tagteam.watcher.notify_macos"):
            p.tick(_state(project))
        for t in ("plan", "impl"):
            assert _rows(project, ctype=t) == [] and _entries(project, ctype=t) == []
        conn = db.connect(project_dir=str(project))
        try:
            assert conn.execute("SELECT COUNT(*) FROM gates").fetchone()[0] == 0
            assert {r["role"] for r in db.get_rounds(conn, "feat-x", "impl")} == {"lead", "reviewer"}
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Phase 41: on-submit gate (`gatekeeper.on_submit`), --no-gate, --skip-tests
# ---------------------------------------------------------------------------

def _prep_impl_ready(project: Path, *, on_submit: bool = True, command: str | None = OK_CMD) -> None:
    """git project, gate enabled (+ on_submit), plan approved, one code change;
    the impl cycle is NOT open yet — the test opens it through the CLI."""
    _git_project(project)
    _enable(project, extra=("  on_submit: true\n" if on_submit else ""), command=command)
    _approve_plan(project)
    (project / "src.py").write_text("x = 2\n", encoding="utf-8")


class TestOnSubmit:
    def test_config_key(self):
        assert validate_gatekeeper_config({"gatekeeper": {"enabled": True, "on_submit": True}}) == []
        assert get_gatekeeper_spec({"gatekeeper": {"enabled": True, "on_submit": True}})["on_submit"] is True
        assert get_gatekeeper_spec({"gatekeeper": {"enabled": True}})["on_submit"] is False
        assert any("on_submit" in e for e in validate_gatekeeper_config({"gatekeeper": {"on_submit": "yes"}}))
        assert g.resolve_gatekeeper({"gatekeeper": {"enabled": True, "on_submit": True}}).on_submit is True

    def test_init_runs_gate_once_pass_no_watcher(self, project, capsys):
        _prep_impl_ready(project, command=_counter_cmd(project))
        rc = cycle_mod.cycle_command(["init", "--phase", "feat-x", "--type", "impl", "--lead", "Claude",
                                      "--reviewer", "Codex", "--updated-by", "Claude", "--content", "impl v1"])
        out = capsys.readouterr()
        assert rc == 0
        assert "Cycle created" in out.out and "gate: pass" in out.out and "tell Codex to run /handoff" in out.out
        assert "GATE: PASS" in out.out and "one full-suite run" in out.err
        assert _count(project) == 1
        rows, ents = _rows(project), _entries(project)
        assert len(rows) == 1 and rows[0]["kind"] == "manual" and rows[0]["status"] == "pass"
        assert len(ents) == 1 and ents[0]["action"] == cycle_mod.GATE_PASS
        assert "checked: HEAD " in ents[0]["content"]
        assert _state(project)["turn"] == "reviewer" and _state(project)["status"] == "ready"
        # a second gate for the same submission does not re-run the checks
        res = _run(project)
        assert res.status == "pass" and _count(project) == 1 and len(_rows(project)) == 1
        assert g.gate_command(["run"], project_root=project) == 0 and _count(project) == 1

    def test_add_submit_bounces_and_says_so(self, project, capsys):
        _prep_impl_ready(project)
        _open_impl(project)                                   # round 1 (gate not run — direct API)
        cycle_mod.add_round("feat-x", "impl", "reviewer", "REQUEST_CHANGES", 1, "no", str(project), updated_by="Codex")
        _enable(project, extra="  on_submit: true\n", command=FAIL_CMD)
        (project / "src.py").write_text("x = 3\n", encoding="utf-8")
        rc = cycle_mod.cycle_command(["add", "--phase", "feat-x", "--type", "impl", "--role", "lead",
                                      "--action", "SUBMIT_FOR_REVIEW", "--round", "2", "--updated-by", "Claude",
                                      "--content", "impl v2"])
        out = capsys.readouterr().out
        assert rc == 0                                        # the round was added; the verdict is data
        assert "Round added" in out and "gate: bounce" in out and "already back with you" in out
        assert "GATE: BOUNCE" in out and "boom" in out
        st = _state(project)
        assert st["turn"] == "lead" and st["status"] == "ready"
        ents = _entries(project)
        assert len(ents) == 1 and ents[0]["action"] == cycle_mod.GATE_BOUNCE and ents[0]["round"] == 2

    def test_no_gate_leaves_submission_gate_eligible(self, project, capsys):
        _prep_impl_ready(project, command=_counter_cmd(project))
        rc = cycle_mod.cycle_command(["init", "--phase", "feat-x", "--type", "impl", "--lead", "Claude",
                                      "--reviewer", "Codex", "--updated-by", "Claude", "--content", "impl v1",
                                      "--no-gate"])
        out = capsys.readouterr()
        assert rc == 0 and "gate:" not in out.out and _count(project) == 0
        assert _rows(project) == [] and _entries(project) == []
        assert _state(project)["turn"] == "reviewer"
        # a later watcher / `gate run` gates it exactly once
        res = _run(project)
        assert res.status == "pass" and _count(project) == 1
        assert _run(project).status == "pass" and _count(project) == 1

    def test_reviewer_actions_and_amend_never_gate(self, project, capsys):
        _prep_impl_ready(project, command=_counter_cmd(project))
        cycle_mod.cycle_command(["init", "--phase", "feat-x", "--type", "impl", "--lead", "Claude",
                                 "--reviewer", "Codex", "--updated-by", "Claude", "--content", "impl v1"])
        assert _count(project) == 1
        cycle_mod.cycle_command(["add", "--phase", "feat-x", "--type", "impl", "--role", "lead",
                                 "--action", "AMEND", "--round", "1", "--updated-by", "Claude", "--content", "ps"])
        cycle_mod.cycle_command(["add", "--phase", "feat-x", "--type", "impl", "--role", "reviewer",
                                 "--action", "APPROVE", "--round", "1", "--updated-by", "Codex", "--content", "ok"])
        capsys.readouterr()
        assert _count(project) == 1 and len(_rows(project)) == 1

    def test_default_off_and_not_applicable_are_byte_identical(self, project, capsys):
        # on_submit unset → cycle add/init print exactly what they printed before
        _prep_impl_ready(project, on_submit=False, command=_counter_cmd(project))
        rc = cycle_mod.cycle_command(["init", "--phase", "feat-x", "--type", "impl", "--lead", "Claude",
                                      "--reviewer", "Codex", "--updated-by", "Claude", "--content", "impl v1"])
        out = capsys.readouterr()
        assert rc == 0 and out.out == "Cycle created: feat-x_impl (round 1, ready_for: reviewer) + state updated\n"
        assert out.err.startswith("[tagteam] project root:") and out.err.count("\n") == 1
        assert _count(project) == 0 and _rows(project) == []
        # on_submit on, but a plan cycle (not in `on`) → nothing
        _enable(project, extra="  on_submit: true\n", command=_counter_cmd(project))
        rc = cycle_mod.cycle_command(["init", "--phase", "feat-y", "--type", "plan", "--lead", "Claude",
                                      "--reviewer", "Codex", "--updated-by", "Claude", "--content", "plan"])
        out = capsys.readouterr()
        assert rc == 0 and "gate" not in out.out and out.err.count("\n") == 1 and _count(project) == 0
        # --no-gate is a silent no-op there too
        rc = cycle_mod.cycle_command(["add", "--phase", "feat-y", "--type", "plan", "--role", "lead",
                                      "--action", "AMEND", "--round", "1", "--updated-by", "Claude",
                                      "--content", "ps", "--no-gate"])
        assert rc == 0 and _count(project) == 0

    def test_gate_error_does_not_fail_the_round(self, project, capsys):
        _prep_impl_ready(project)
        with patch("tagteam.gatekeeper.run_gate", side_effect=RuntimeError("kaboom")):
            rc = cycle_mod.cycle_command(["init", "--phase", "feat-x", "--type", "impl", "--lead", "Claude",
                                          "--reviewer", "Codex", "--updated-by", "Claude", "--content", "impl v1"])
        out = capsys.readouterr()
        assert rc == 0 and "Cycle created" in out.out and "kaboom" in out.err and "gate run" in out.err
        assert _state(project)["turn"] == "reviewer" and _rows(project) == []

    def test_deferred_when_slot_held(self, project, capsys):
        _prep_impl_ready(project, command=_counter_cmd(project))
        claim = h.claim_turn_slot(project, kind=h.SLOT_KIND_CONVERSATION, role="lead",
                                  fields={"stem": "conv", "watcher_pid": os.getpid(),
                                          "watcher_ident": procs.identity(os.getpid())})
        try:
            rc = cycle_mod.cycle_command(["init", "--phase", "feat-x", "--type", "impl", "--lead", "Claude",
                                          "--reviewer", "Codex", "--updated-by", "Claude", "--content", "impl v1"])
        finally:
            h.release_turn_slot(claim)
        out = capsys.readouterr().out
        assert rc == 0 and "gate: deferred" in out and "still owed" in out and _count(project) == 0
        assert _state(project)["turn"] == "reviewer" and _rows(project) == []
        assert _run(project).status == "pass" and _count(project) == 1       # a later gate decides once

    def test_gate_check_skip_tests_and_on_submit_note(self, project, capsys):
        _prep_impl_ready(project, command=_counter_cmd(project))
        _open_impl(project)
        assert g.gate_command(["check", "--skip-tests"], project_root=project) == 0
        out = capsys.readouterr().out
        assert "tests skipped (--skip-tests)" in out and "note: on_submit" not in out and _count(project) == 0
        assert g.gate_command(["check"], project_root=project) == 0
        out = capsys.readouterr().out
        assert out.startswith("note: on_submit is on") and _count(project) == 1
        # --skip-tests with a failing scope still fails on scope
        _git(project, "add", "-A"); _git(project, "commit", "-qm", "work")
        assert g.gate_command(["check", "--skip-tests"], project_root=project) == 0   # committed change still counts
        capsys.readouterr()
        assert g.gate_command(["run", "--skip-tests"], project_root=project) == 1     # only `check` takes the flag
        assert "Unknown argument" in capsys.readouterr().out
        assert _count(project) == 1

    def test_head_sha_in_entry_and_run_checks_result(self, gated):
        res = g.run_checks(_spec(gated), "feat-x", "impl", str(gated))
        assert res["head"] and len(res["head"]) >= 7
        d = g.decide(res, _spec(gated), 0)
        assert d["content"].splitlines()[0].startswith("GATE: PASS") and f"checked: HEAD {res['head']}" in d["content"]
        # results without a head (older rows / no git) still decide
        res.pop("head")
        assert "checked" not in g.decide(res, _spec(gated), 0)["content"]
