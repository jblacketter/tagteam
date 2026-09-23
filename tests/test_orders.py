"""Phase 70: standing orders (`tagteam/orders.py` and its hooks in cycle,
state, headless and the watcher)."""

import json
import os
from pathlib import Path

import pytest

from tagteam import orders
from tagteam.cycle import (
    _derive_top_level_state, _is_tagteam_artifact, _cli_rounds, add_round, add_ruling,
    init_cycle,
)
from tagteam.headless import compose_prompt, _standing_orders_block
from tagteam.state import read_state, state_command, write_state
from tagteam.watcher import _try_roadmap_advance


ROADMAP = """\
# Roadmap

### Phase 1: A
- **Status:** In review

### Phase 2: B
- **Status:** Not started
- **Depends on:** C

### Phase 3: C
- **Status:** Not started
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "docs" / "handoffs").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TAGTEAM_READ_ONLY", raising=False)
    monkeypatch.setenv("TAGTEAM_ARBITER", "jack")
    return tmp_path


def _roadmap(p: Path, text: str = ROADMAP) -> None:
    (p / "docs" / "roadmap.md").write_text(text, encoding="utf-8")


def _project_orders(p: Path, **body) -> None:
    (p / orders.ORDERS_FILE).write_text(json.dumps({"version": 1, **body}), encoding="utf-8")


def _status(p: Path, phase: str, ctype: str = "impl") -> dict:
    return json.loads((p / "docs" / "handoffs" / f"{phase}_{ctype}_status.json").read_text())


def _approve_impl(p: Path, phase: str) -> dict:
    """Open an impl cycle for `phase` and approve it (reviewer APPROVE)."""
    init_cycle(phase, "impl", "claude", "codex", "impl", str(p))
    add_round(phase, "impl", "reviewer", "APPROVE", 1, "ok", str(p))
    return read_state(str(p))


def _full_roadmap_state(p: Path, queue, idx, completed, **extra) -> None:
    write_state({"phase": queue[idx], "type": "plan", "status": "done", "result": "approved",
                 "run_mode": "full-roadmap",
                 "roadmap": {"queue": list(queue), "current_index": idx,
                             "completed": list(completed), "pause_reason": None},
                 **extra}, str(p))


def _run(p: Path, *args) -> int:
    return orders.orders_command(list(args), project_root=p)


def _strip_volatile(state: dict) -> dict:
    return {k: v for k, v in state.items() if k not in ("updated_at", "history", "seq")}


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

class TestEffective:
    @pytest.mark.parametrize("run_stop, run_mode, project_stop, want", [
        ("phase", "full-roadmap", "roadmap", ("phase", "run")),
        ("roadmap", "single-phase", "phase", ("roadmap", "run")),
        (None, "full-roadmap", "phase", ("roadmap", "run-mode")),
        (None, "single-phase", "roadmap", ("roadmap", "project")),
        (None, "single-phase", None, ("phase", "default")),
    ])
    def test_precedence(self, project, run_stop, run_mode, project_stop, want):
        if project_stop:
            _project_orders(project, stop=project_stop, advisory=[])
        state = {"run_mode": run_mode}
        if run_stop:
            state["orders"] = {"stop": run_stop, "advisory": []}
        eff = orders.effective(state, project)
        assert (eff["stop"], eff["stop_source"]) == want

    def test_advisory_project_then_run(self, project):
        _project_orders(project, stop=None, advisory=[{"id": 1, "text": "hold the PR"}])
        state = {"orders": {"advisory": [{"id": 1, "text": "open the PR"}]}}
        eff = orders.effective(state, project)
        assert [(n["source"], n["text"]) for n in eff["advisory"]] == [
            ("project", "hold the PR"), ("run", "open the PR")]

    @pytest.mark.parametrize("body", [
        {}, {"stop": None}, {"stop": None, "advisory": []}])
    def test_empty_file_is_not_explicit(self, project, body):
        _project_orders(project, **body)
        assert orders.effective({}, project)["explicit"] is False

    @pytest.mark.parametrize("raw", [
        "not json", "[1, 2]", json.dumps({"stop": "sometimes"}),
        json.dumps({"advisory": [{"id": "x", "text": "t"}]}),
        json.dumps({"advisory": "hold"})])
    def test_malformed_file_is_no_orders_with_a_warning(self, project, raw):
        (project / orders.ORDERS_FILE).write_text(raw)
        eff = orders.effective({}, project)
        assert eff["explicit"] is False and eff["stop"] == "phase" and eff["warn"]

    def test_symlinked_file_is_not_followed(self, project, tmp_path_factory):
        target = tmp_path_factory.mktemp("elsewhere") / "o.json"
        target.write_text(json.dumps({"stop": "roadmap"}))
        os.symlink(target, project / orders.ORDERS_FILE)
        eff = orders.effective({}, project)
        assert eff["explicit"] is False and "symlink" in eff["warn"]

    def test_malformed_file_never_breaks_the_approval_write(self, project):
        (project / orders.ORDERS_FILE).write_text("{broken")
        state = _approve_impl(project, "a")
        assert state["status"] == "done" and state["result"] == "approved"
        assert "run_decision" not in _status(project, "a")


# ---------------------------------------------------------------------------
# The no-orders promise
# ---------------------------------------------------------------------------

class TestNoOrdersPromise:
    def test_single_phase_approval_writes_nothing_new(self, project, capsys):
        _roadmap(project)
        state = _approve_impl(project, "a")
        status = _status(project, "a")
        assert "run_decision" not in status and "orders" not in state
        assert state["run_mode"] == "single-phase" and "roadmap" not in state
        assert "standing orders" not in capsys.readouterr().err
        assert not (project / orders.ORDERS_FILE).exists()

    def test_full_roadmap_approval_is_unchanged(self, project, capsys):
        _roadmap(project)
        _full_roadmap_state(project, ["a", "c", "b"], 0, [])
        state = _approve_impl(project, "a")
        assert "run_decision" not in _status(project, "a")
        assert state["run_mode"] == "full-roadmap"
        assert state["roadmap"] == {"queue": ["a", "c", "b"], "current_index": 0,
                                    "completed": [], "pause_reason": None}
        assert "orders" not in state
        assert "standing orders" not in capsys.readouterr().err

    def test_no_block_no_line_no_stderr(self, project, capsys):
        init_cycle("a", "plan", "claude", "codex", "plan", str(project))
        capsys.readouterr()
        assert _standing_orders_block(read_state(str(project)), project) == ""
        _cli_rounds(["--phase", "a", "--type", "plan"])
        out = capsys.readouterr()
        assert out.err == "" and out.out.startswith("{")
        state_command([])
        assert "Orders:" not in capsys.readouterr().out

    def test_read_creates_nothing(self, project, capsys):
        before = sorted(p.name for p in project.iterdir())
        assert _run(project) == 0
        assert _run(project, "--json") == 0
        assert sorted(p.name for p in project.iterdir()) == before


# ---------------------------------------------------------------------------
# Enforcement at the approval write
# ---------------------------------------------------------------------------

class TestConvert:
    def test_stop_roadmap_converts_and_the_unchanged_watcher_advances(self, project, capsys):
        _roadmap(project)
        _run(project, "stop", "roadmap")
        state = _approve_impl(project, "a")
        dec = _status(project, "a")["run_decision"]
        assert dec["outcome"] == "convert" and dec["source"] == "project"
        assert state["run_mode"] == "full-roadmap"
        assert state["roadmap"] == {"queue": ["a", "c", "b"], "current_index": 0, "completed": []}
        assert "converted to a roadmap run" in capsys.readouterr().err
        new = _try_roadmap_advance(state, str(project))
        # B depends on C: the watcher starts C, not B.
        assert new["phase"] == "c" and new["type"] == "plan" and new["turn"] == "lead"
        assert new["roadmap"]["completed"] == ["a"]

    def test_approved_phase_already_complete_is_queue_head(self, project):
        _roadmap(project, ROADMAP.replace("- **Status:** In review", "- **Status:** ✅ Complete"))
        _run(project, "stop", "roadmap")
        state = _approve_impl(project, "a")
        assert state["roadmap"]["queue"][0] == "a"
        new = _try_roadmap_advance(state, str(project))
        assert new["phase"] == "c"

    def test_phase_not_in_roadmap_still_heads_the_queue(self, project):
        _roadmap(project)
        _run(project, "stop", "roadmap")
        state = _approve_impl(project, "adhoc-fix")
        assert state["roadmap"]["queue"] == ["adhoc-fix", "a", "c", "b"]
        assert _try_roadmap_advance(state, str(project))["phase"] == "a"

    def test_exhausted_roadmap_completes_and_drops_the_override(self, project):
        _roadmap(project, "# R\n\n### Phase 1: A\n- **Status:** In review\n\n"
                          "### Phase 2: B\n- **Status:** ✅ Complete\n")
        init_cycle("a", "impl", "claude", "codex", "impl", str(project))
        _run(project, "stop", "roadmap", "--run")
        add_round("a", "impl", "reviewer", "APPROVE", 1, "ok", str(project))
        state = read_state(str(project))
        dec = _status(project, "a")["run_decision"]
        assert (dec["outcome"], dec["reason"]) == ("complete", "roadmap-exhausted")
        assert state["run_mode"] == "single-phase" and "roadmap" not in state
        assert "orders" not in state

    @pytest.mark.parametrize("roadmap_text", [
        None,                                                        # missing
        "no headings here\n",                                        # unparseable
        "### Phase 1: A\n- **Status:** In review\n- **Depends on:** nowhere\n",  # graph problem
    ])
    def test_invalid_roadmap_stops_and_drops_the_override(self, project, capsys, roadmap_text):
        if roadmap_text is not None:
            _roadmap(project, roadmap_text)
        init_cycle("a", "impl", "claude", "codex", "impl", str(project))
        _run(project, "stop", "roadmap", "--run")
        add_round("a", "impl", "reviewer", "APPROVE", 1, "ok", str(project))
        state = read_state(str(project))
        dec = _status(project, "a")["run_decision"]
        assert dec["outcome"] == "stop" and dec["reason"].startswith("roadmap-invalid")
        assert state["status"] == "done" and state["run_mode"] == "single-phase"
        assert "orders" not in state
        assert "roadmap invalid" in capsys.readouterr().err


class TestStopAndContinue:
    def test_run_stop_phase_halts_a_roadmap_run(self, project):
        _roadmap(project)
        _full_roadmap_state(project, ["a", "c", "b"], 0, [])
        init_cycle("a", "impl", "claude", "codex", "impl", str(project))
        _run(project, "stop", "phase", "--run")
        add_round("a", "impl", "reviewer", "APPROVE", 1, "ok", str(project))
        state = read_state(str(project))
        assert _status(project, "a")["run_decision"]["outcome"] == "stop"
        assert state["run_mode"] == "single-phase" and "roadmap" not in state
        assert "orders" not in state
        assert _try_roadmap_advance(state, str(project)) is None

    def test_continue_keeps_the_runs_completed_list(self, project):
        """Criterion 13: A completed only in the run state; B approved with an
        advisory note; C depends on A — the watcher starts C."""
        _roadmap(project, "# R\n\n### Phase 1: A\n- **Status:** In review\n\n"
                          "### Phase 2: B\n- **Status:** In review\n\n"
                          "### Phase 3: C\n- **Status:** Not started\n- **Depends on:** A\n")
        _project_orders(project, stop=None, advisory=[{"id": 1, "text": "hold the PR"}])
        _full_roadmap_state(project, ["a", "b", "c"], 1, ["a"])
        before = dict(read_state(str(project))["roadmap"])
        state = _approve_impl(project, "b")
        assert _status(project, "b")["run_decision"]["outcome"] == "continue"
        assert state["roadmap"] == before
        new = _try_roadmap_advance(state, str(project))
        assert new["phase"] == "c"
        assert new["roadmap"]["completed"] == ["a", "b"]

    def test_arbiter_ruling_approval_decides_too(self, project):
        _roadmap(project)
        _run(project, "stop", "roadmap")
        init_cycle("a", "impl", "claude", "codex", "impl", str(project))
        add_round("a", "impl", "reviewer", "ESCALATE", 1, "help", str(project))
        add_ruling("a", "impl", "APPROVE", "fine", "jack", str(project))
        assert _status(project, "a")["run_decision"]["outcome"] == "convert"
        assert read_state(str(project))["run_mode"] == "full-roadmap"

    def test_repeated_approve_does_not_redecide_a_stopped_run(self, project):
        """r2 review: a duplicate APPROVE of an approved cycle keeps its
        recorded stop instead of re-resolving against the project order."""
        _roadmap(project)
        _project_orders(project, stop="roadmap", advisory=[])
        init_cycle("a", "impl", "claude", "codex", "impl", str(project))
        _run(project, "stop", "phase", "--run")
        add_round("a", "impl", "reviewer", "APPROVE", 1, "ok", str(project))
        first = _status(project, "a")["run_decision"]
        assert first["outcome"] == "stop"
        add_round("a", "impl", "reviewer", "APPROVE", 1, "ok again", str(project))
        assert _status(project, "a")["run_decision"] == first
        s = read_state(str(project))
        assert s["run_mode"] == "single-phase" and "roadmap" not in s

    def test_repeated_approve_keeps_an_override_queued_for_the_next_run(self, project):
        _roadmap(project)
        _run(project, "stop", "phase")
        _approve_impl(project, "a")                        # stop, recorded
        _run(project, "add", "next run: open the PR", "--run")
        add_round("a", "impl", "reviewer", "APPROVE", 1, "again", str(project))
        s = read_state(str(project))
        assert s["orders"]["advisory"][0]["text"] == "next run: open the PR"
        assert s["run_mode"] == "single-phase"

    def test_plan_approval_records_nothing(self, project):
        _run(project, "stop", "roadmap")
        init_cycle("a", "plan", "claude", "codex", "plan", str(project))
        add_round("a", "plan", "reviewer", "APPROVE", 1, "ok", str(project))
        assert "run_decision" not in _status(project, "a", "plan")
        assert read_state(str(project))["run_mode"] == "single-phase"


# ---------------------------------------------------------------------------
# Run override lifetime and reconciliation
# ---------------------------------------------------------------------------

class TestLifetime:
    def test_override_survives_intermediate_writes_and_resumable_pauses(self, project):
        init_cycle("a", "plan", "claude", "codex", "plan", str(project))
        _run(project, "add", "open the PR when done", "--run")
        p = str(project)
        add_round("a", "plan", "reviewer", "REQUEST_CHANGES", 1, "fix", p)
        add_round("a", "plan", "lead", "SUBMIT_FOR_REVIEW", 2, "fixed", p)
        add_round("a", "plan", "reviewer", "NEED_HUMAN", 2, "?", p)
        assert "orders" in read_state(p)
        add_ruling("a", "plan", "APPROVE", "yes", "jack", p)
        init_cycle("a", "impl", "claude", "codex", "impl", p)
        add_round("a", "impl", "reviewer", "ESCALATE", 1, "stuck", p)
        assert read_state(p)["orders"]["advisory"][0]["text"] == "open the PR when done"
        add_ruling("a", "impl", "REQUEST_CHANGES", "try again", "jack", p)
        assert "orders" in read_state(p)
        add_round("a", "impl", "lead", "SUBMIT_FOR_REVIEW", 2, "again", p)
        add_round("a", "impl", "reviewer", "APPROVE", 2, "ok", p)   # stop: phase → run ends
        assert "orders" not in read_state(p)

    def test_roadmap_complete_drops_the_override(self, project):
        _roadmap(project, "# R\n\n### Phase 1: A\n- **Status:** ✅ Complete\n")
        _full_roadmap_state(project, ["a"], 0, [],
                            orders={"stop": "roadmap", "advisory": []})
        state = read_state(str(project))
        state.update({"type": "impl"})
        write_state(state, str(project))
        new = _try_roadmap_advance(read_state(str(project)), str(project))
        assert new["result"] == "roadmap-complete" and "orders" not in new

    def test_blocked_pause_keeps_the_override(self, project):
        _roadmap(project, "# R\n\n### Phase 1: A\n- **Status:** In review\n\n"
                          "### Phase 2: B\n- **Status:** Not started\n- **Depends on:** Z\n\n"
                          "### Phase 3: Z\n- **Status:** In review\n")
        _full_roadmap_state(project, ["a", "b"], 0, [],
                            orders={"advisory": [{"id": 1, "text": "x"}]})
        state = read_state(str(project))
        state["type"] = "impl"
        write_state(state, str(project))
        new = _try_roadmap_advance(read_state(str(project)), str(project))
        assert new["status"] == "escalated" and new["roadmap"]["pause_reason"].startswith("blocked:")
        assert new["orders"]["advisory"][0]["text"] == "x"

    def test_clear_run(self, project):
        init_cycle("a", "plan", "claude", "codex", "plan", str(project))
        _run(project, "stop", "roadmap", "--run")
        assert _run(project, "clear", "--run") == 0
        assert "orders" not in read_state(str(project))

    def test_run_write_does_not_bump_seq(self, project):
        """A seq bump reads as a new turn to a watcher (it would re-dispatch)."""
        init_cycle("a", "plan", "claude", "codex", "plan", str(project))
        seq = read_state(str(project))["seq"]
        _run(project, "add", "note", "--run")
        _run(project, "stop", "roadmap", "--run")
        _run(project, "remove", "1", "--run")
        assert read_state(str(project))["seq"] == seq


class TestReconciliation:
    def test_a_sync_reapplies_a_recorded_stop(self, project, capsys):
        """11(a): project roadmap + run phase → approve → sync stays single-phase."""
        _roadmap(project)
        _project_orders(project, stop="roadmap", advisory=[])
        init_cycle("a", "impl", "claude", "codex", "impl", str(project))
        _run(project, "stop", "phase", "--run")
        add_round("a", "impl", "reviewer", "APPROVE", 1, "ok", str(project))
        assert "orders" not in read_state(str(project))
        for _ in range(2):
            state_command(["sync", "--phase", "a", "--type", "impl"])
            s = read_state(str(project))
            assert s["run_mode"] == "single-phase" and "roadmap" not in s

    def test_b_queued_override_survives_a_sync_of_the_old_cycle(self, project):
        _roadmap(project)
        _approve_impl(project, "a")                       # no orders: nothing recorded
        _run(project, "stop", "roadmap", "--run")         # queued for the next run
        state_command(["sync", "--phase", "a", "--type", "impl"])
        s = read_state(str(project))
        assert s["orders"]["stop"] == "roadmap"
        assert s["run_mode"] == "single-phase" and "roadmap" not in s
        assert "run_decision" not in _status(project, "a")

    def test_c_sync_after_the_watcher_moved_on_does_not_recreate_the_queue(self, project):
        _roadmap(project)
        _run(project, "stop", "roadmap")
        state = _approve_impl(project, "a")
        new = _try_roadmap_advance(state, str(project))
        assert new["phase"] == "c"
        before = _strip_volatile(read_state(str(project)))
        # A deliberate sync of the OLD converted cycle.
        _derive_top_level_state("a", "impl", str(project), updated_by="state-sync")
        s = read_state(str(project))
        assert s["run_mode"] == "single-phase" and "roadmap" not in s
        assert before["run_mode"] == "full-roadmap"


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

class TestDelivery:
    def test_block_contents(self, project):
        _project_orders(project, stop="roadmap",
                        advisory=[{"id": 1, "text": "commit at phase end, hold the PR"}])
        block = _standing_orders_block({}, project)
        assert block.startswith(orders.BLOCK_HEADER)
        assert "stop: roadmap" in block and "Escalations and questions" in block
        assert "does not enforce" in block and "#1 (project): commit at phase end" in block

    def test_prompt_carries_the_block(self, project):
        _project_orders(project, stop=None, advisory=[{"id": 1, "text": "hold the PR"}])
        prompt = compose_prompt(role="lead", agent_name="claude", project_root=project,
                                state={}, skill_text="contract", tail_entries=[], tail_n=1,
                                orders_block=_standing_orders_block({}, project))
        assert orders.BLOCK_HEADER in prompt
        assert prompt.index(orders.BLOCK_HEADER) < prompt.index("=== COMMAND ===")

    def test_block_is_capped(self, project):
        notes = [{"id": i, "text": "x" * 200} for i in range(1, 60)]
        _project_orders(project, stop=None, advisory=notes)
        block = _standing_orders_block({}, project)
        assert len(block) < orders.BLOCK_BUDGET + 200 and "tagteam orders" in block

    def test_cycle_rounds_block_on_stderr_stdout_json(self, project, capsys):
        init_cycle("a", "plan", "claude", "codex", "plan", str(project))
        _run(project, "add", "hold the PR")
        capsys.readouterr()
        _cli_rounds(["--phase", "a", "--type", "plan"])
        out = capsys.readouterr()
        assert orders.BLOCK_HEADER in out.err
        for line in out.out.strip().splitlines():
            json.loads(line)

    def test_state_line(self, project, capsys):
        _run(project, "stop", "roadmap")
        _run(project, "add", "hold the PR")
        _roadmap(project)
        _approve_impl(project, "a")
        capsys.readouterr()
        state_command([])
        line = [l for l in capsys.readouterr().out.splitlines() if l.startswith("Orders:")]
        assert line and "stop: roadmap (run-mode)" in line[0] and "1 advisory" in line[0]
        assert "converted to a roadmap run" in line[0]


# ---------------------------------------------------------------------------
# CLI, read-only, scope
# ---------------------------------------------------------------------------

class TestCli:
    def test_project_writes(self, project, capsys):
        assert _run(project, "stop", "roadmap") == 0
        assert _run(project, "add", "hold", "the", "PR") == 0
        body = json.loads((project / orders.ORDERS_FILE).read_text())
        assert body["stop"] == "roadmap" and body["advisory"][0]["text"] == "hold the PR"
        assert body["advisory"][0]["by"] == "jack"
        assert _run(project, "remove", "1") == 0
        assert _run(project, "stop", "--unset") == 0
        assert json.loads((project / orders.ORDERS_FILE).read_text()) == {
            "version": 1, "stop": None, "advisory": []}
        out = capsys.readouterr().out
        assert "Enforced (the engine does this)" in out and "Advisory (delivered, not enforced)" in out

    def test_write_over_the_read_limit_is_refused_and_leaves_the_file(self, project):
        """r2 review: never write what the reader would refuse. Cumulative
        notes and escaped non-ASCII text count by their encoded size."""
        _run(project, "stop", "roadmap")
        before = (project / orders.ORDERS_FILE).read_bytes()
        assert _run(project, "add", "x" * orders.MAX_BYTES) == 1
        assert (project / orders.ORDERS_FILE).read_bytes() == before
        # Many notes: fill until refused; the last accepted file is still read.
        note = "é" * 1500                       # json.dumps escapes → ~9 KB per note
        rc = 0
        while rc == 0:
            kept = (project / orders.ORDERS_FILE).read_bytes()
            rc = _run(project, "add", note)
        assert rc == 1 and (project / orders.ORDERS_FILE).read_bytes() == kept
        assert len(kept) <= orders.MAX_BYTES
        eff = orders.effective({}, project)
        assert eff["warn"] is None and eff["stop"] == "roadmap" and eff["advisory"]
        assert not list(project.glob(".tagteam-orders.json.*.tmp"))

    def test_bad_usage(self, project):
        assert _run(project, "stop", "sometimes") == 2
        assert _run(project, "remove", "x") == 2
        assert _run(project, "clear") == 2
        assert _run(project, "remove", "7") == 1

    def test_refuses_to_overwrite_a_malformed_file(self, project):
        (project / orders.ORDERS_FILE).write_text("{oops")
        assert _run(project, "add", "x") == 1
        assert (project / orders.ORDERS_FILE).read_text() == "{oops"

    def test_refuses_a_symlink(self, project, tmp_path_factory):
        target = tmp_path_factory.mktemp("t") / "o.json"
        target.write_text("{}")
        os.symlink(target, project / orders.ORDERS_FILE)
        assert _run(project, "stop", "roadmap") == 1
        assert target.read_text() == "{}"

    def test_run_needs_state(self, project):
        assert _run(project, "stop", "roadmap", "--run") == 1

    def test_read_only_refuses_writes_before_disk(self, project, monkeypatch):
        from tagteam.cli import read_only_refusal
        from tagteam.dualwrite import ReadOnlyError
        init_cycle("a", "plan", "claude", "codex", "plan", str(project))
        state_before = (project / "handoff-state.json").read_text()
        monkeypatch.setenv("TAGTEAM_READ_ONLY", "1")
        assert read_only_refusal(["orders"]) is None
        assert read_only_refusal(["orders", "--json"]) is None
        for argv in (["orders", "stop", "roadmap"], ["orders", "add", "x"],
                     ["orders", "clear", "--run"], ["orders", "remove", "1"]):
            assert read_only_refusal(argv) is not None
        # And the deeper guards, should a caller get past the table:
        with pytest.raises(ReadOnlyError):
            _run(project, "stop", "roadmap")
        with pytest.raises(ReadOnlyError):
            _run(project, "stop", "roadmap", "--run")
        assert not (project / orders.ORDERS_FILE).exists()
        assert (project / "handoff-state.json").read_text() == state_before

    def test_orders_file_is_not_implementation_work(self):
        assert _is_tagteam_artifact(orders.ORDERS_FILE)
