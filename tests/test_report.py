"""Phase 55: `tagteam report --phase P` — phase measurement from stored data.

Cycles are written as JSONL/status files (the canonical source); usage and
gate rows go straight into the DB. Every coverage fixture is checked against
the partition invariant. The read-path tests snapshot the tree before and
after to prove the report creates, migrates and changes nothing.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tagteam import db, dualwrite, report
from tagteam import state as state_mod

T0 = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
P = "measure-x"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _ts(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def E(rnd, role, action, minute, **kw):
    e = {"round": rnd, "role": role, "action": action, "content": "x"}
    if minute is not None:
        e["ts"] = _ts(minute)
    e.update(kw)
    return e


def write_cycle(root: Path, phase: str, ctype: str, entries: list[dict], state: str = "approved"):
    d = root / "docs" / "handoffs"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{phase}_{ctype}_rounds.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    rnd = max((e["round"] for e in entries), default=0)
    (d / f"{phase}_{ctype}_status.json").write_text(json.dumps(
        {"state": state, "round": rnd, "phase": phase, "type": ctype,
         "lead": "claude", "reviewer": "codex", "ready_for": None}), encoding="utf-8")


PLAN = [E(1, "lead", "SUBMIT_FOR_REVIEW", 0), E(1, "reviewer", "REQUEST_CHANGES", 10),
        E(2, "lead", "SUBMIT_FOR_REVIEW", 20), E(2, "reviewer", "APPROVE", 25)]
IMPL = [E(1, "lead", "SUBMIT_FOR_REVIEW", 85), E(1, "gatekeeper", "GATE_BOUNCE", 90),
        E(2, "lead", "SUBMIT_FOR_REVIEW", 100), E(2, "gatekeeper", "GATE_PASS", 105),
        E(2, "reviewer", "REQUEST_CHANGES", 108),
        E(3, "lead", "SUBMIT_FOR_REVIEW", 118), E(3, "gatekeeper", "GATE_PASS", 123),
        E(3, "reviewer", "APPROVE", 125)]
TOK = dict(input_tokens=10, output_tokens=2, cache_read_tokens=100, cache_write_tokens=5)


def _conn(root: Path):
    return db.connect(project_dir=str(root))


def usage_rows(root: Path, *rows: dict) -> None:
    c = _conn(root)
    try:
        for r in rows:
            db.add_usage(c, **{"ts": _ts(1), "status": "ok", "agent": "a", "provider": "claude", **r})
    finally:
        c.close()


def gate_rows(root: Path, phase: str, ctype: str, *rows: tuple[int, str, float | None]) -> None:
    c = _conn(root)
    try:
        for i, (rnd, status, dur) in enumerate(rows):
            c.execute("INSERT INTO gates (event_key, phase, type, round, submission_seq, kind, status, attempt, "
                      "started_at, duration_s) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (f"{phase}/{ctype}/r{rnd}/{i}", phase, ctype, rnd, i, "auto", status, 1, _ts(0), dur))
        c.commit()
    finally:
        c.close()


def check_invariant(cov: dict) -> None:
    parts = sum(cov[s] for s in report.TURN_STATUSES)
    assert parts == cov["turns"] == len(cov["per_turn"])
    assert cov["matched"] <= cov["turns"]
    assert cov["turns_with_non_ok_rows"] <= cov["matched"] + cov["no_token_data"]


def turn(rep: dict, ctype: str, rnd: int, role: str) -> dict:
    matches = [t for t in rep["coverage"]["per_turn"]
               if (t["type"], t["round"], t["role"]) == (ctype, rnd, role)]
    assert len(matches) == 1, matches
    return matches[0]


def run(root: Path, phase: str = P, *extra: str) -> tuple[int, str]:
    out = io.StringIO()
    rc = report.report_command(["--phase", phase, *extra], project_root=root, out=out)
    return rc, out.getvalue()


def snapshot(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "tagteam.yaml").write_text("agents:\n  lead:\n    name: claude\n  reviewer:\n    name: codex\n")
    monkeypatch.setattr(state_mod, "_cached_project_root", None, raising=False)
    monkeypatch.delenv(dualwrite.READ_ONLY_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def full(root):
    write_cycle(root, P, "plan", PLAN)
    write_cycle(root, P, "impl", IMPL)
    gate_rows(root, P, "impl", (1, "bounce", 290.0), (2, "pass", 300.0), (3, "pass", 310.0),
              (3, "abandoned", None))
    usage_rows(root,
               dict(phase=P, type="plan", round=1, role="reviewer", provider="codex", **TOK),
               dict(phase=P, type="plan", round=1, role="lead", model="claude-fable-5-1", **TOK),
               dict(phase=P, type="plan", round=2, role="reviewer", provider="codex", **TOK),
               # the impl start: stored under the plan cycle (owed state), no target → unattributed
               dict(phase=P, type="plan", round=2, role="lead", model="claude-fable-5-1", **TOK),
               dict(phase=P, type="impl", round=1, role="lead", model="claude-fable-5-1",
                    model_usage_json=json.dumps({"claude-fable-5-1": {"input_tokens": 4},
                                                 "claude-haiku-4-5": {"input_tokens": 6}}), **TOK),
               # retries for one reviewer turn: two failed, one ok
               dict(phase=P, type="impl", round=2, role="reviewer", status="nonzero_exit", **TOK),
               dict(phase=P, type="impl", round=2, role="reviewer", status="timeout", **TOK),
               dict(phase=P, type="impl", round=2, role="reviewer", **TOK),
               # parsed nothing: a row without token data
               dict(phase=P, type="impl", round=2, role="lead"),
               # a briefer row: consumption, never a workflow turn
               dict(phase=P, type="impl", round=2, role="briefer", **TOK))
    return root


# ---------------------------------------------------------------------------
# full data
# ---------------------------------------------------------------------------

class TestFullReport:
    def test_counts_and_times(self, full):
        rep = report.phase_report(full, P)
        plan, impl = rep["cycles"]["plan"], rep["cycles"]["impl"]
        assert (plan["state"], plan["rounds"], plan["change_requests"], plan["gate_bounces"]) == ("approved", 2, 1, 0)
        assert (impl["rounds"], impl["change_requests"], impl["gate_bounces"]) == (3, 1, 1)
        assert impl["gate_runs"] == 3 and impl["gate_seconds"] == 900.0
        assert plan["time"]["lead"] == {"seconds": 600.0, "spans": 1, "unknown": 1}
        assert plan["time"]["reviewer"] == {"seconds": 900.0, "spans": 2, "unknown": 0}
        assert impl["time"]["lead"] == {"seconds": 1200.0, "spans": 2, "unknown": 1}
        assert impl["time"]["gate"] == {"seconds": 900.0, "spans": 3, "unknown": 0}
        assert impl["time"]["reviewer"] == {"seconds": 300.0, "spans": 2, "unknown": 0}
        wc = rep["wall_clock"]
        assert wc["approved"] and wc["seconds"] == 125 * 60
        assert wc["implementation_before_first_submit_seconds"] == 60 * 60

    def test_consumption(self, full):
        u = report.phase_report(full, P)["usage"]
        assert (u["rows"], u["with_tokens"], u["without_token_data"], u["non_ok"]) == (10, 9, 1, 2)
        assert u["totals"]["input_tokens"] == 90
        assert u["by_role"]["briefer"]["turns"] == 1
        assert u["by_model"]["claude-haiku-4-5"]["input_tokens"] == 6
        assert "codex (model not reported)" in u["by_model"]

    def test_coverage_owed_state(self, full):
        rep = report.phase_report(full, P)
        cov = rep["coverage"]
        check_invariant(cov)
        assert cov["turns"] == 9
        assert turn(rep, "plan", 1, "reviewer")["status"] == "matched"
        assert turn(rep, "plan", 2, "lead")["status"] == "matched"      # lead row r1 → submission r2
        assert turn(rep, "plan", 1, "lead")["status"] == "unknown"      # start turn, no target row
        assert turn(rep, "impl", 1, "lead")["status"] == "unknown"      # its row sits under plan r2
        assert turn(rep, "impl", 2, "lead")["status"] == "matched"
        retried = turn(rep, "impl", 2, "reviewer")
        assert retried["status"] == "matched" and len(retried["row_ids"]) == 3 and retried["non_ok_rows"] == 2
        assert turn(rep, "impl", 3, "lead")["status"] == "no_token_data"
        assert turn(rep, "impl", 3, "reviewer")["status"] == "unmatched"
        assert (cov["matched"], cov["no_token_data"], cov["unmatched"], cov["unknown"]) == (5, 1, 1, 2)
        assert cov["turns_with_non_ok_rows"] == 1
        assert cov["unattributed_rows"] == 1                            # plan r2 lead row

    def test_text_block(self, full):
        rc, out = run(full)
        assert rc == 0
        assert "impl   3 rounds · 1 change request · 1 bounce · gate 3 runs, 15m 00s" in out
        assert "start→approve 2h 05m" in out and "implementation before first submit 1h 00m" in out
        assert "(elapsed; includes relay wait)" in out
        assert "turns  matched 5 of 9 · no token data 1 · unmatched 1 · unknown 2" in out
        assert "turns with non-ok rows 1" in out and "unattributed rows 1" in out
        assert "$" not in out and "cost" not in out.lower() and "interactive" not in out

    def test_json_has_no_cost(self, full):
        rc, out = run(full, P, "--json")
        assert rc == 0
        data = json.loads(out)
        assert data["coverage"]["matched"] == 5
        assert "$" not in out and "cost" not in out.lower()


# ---------------------------------------------------------------------------
# coverage rules
# ---------------------------------------------------------------------------

class TestCoverageRules:
    def test_target_rows_v10(self, root):
        write_cycle(root, P, "plan", PLAN)
        write_cycle(root, P, "impl", IMPL[:1], state="in-progress")
        usage_rows(root, dict(phase=P, type="plan", round=2, role="lead", target_phase=P, target_type="impl",
                              target_round=1, **TOK))
        rep = report.phase_report(root, P)
        check_invariant(rep["coverage"])
        assert turn(rep, "impl", 1, "lead")["status"] == "matched"
        assert turn(rep, "plan", 2, "lead")["status"] == "unmatched"   # no owed-state fallback for a target row
        assert rep["coverage"]["unattributed_rows"] == 0

    def test_cross_phase_start(self, root):
        write_cycle(root, "phase-a", "impl", [E(3, "reviewer", "APPROVE", 0),
                                              E(4, "lead", "SUBMIT_FOR_REVIEW", 5)], state="in-progress")
        write_cycle(root, "phase-b", "plan", [E(1, "lead", "SUBMIT_FOR_REVIEW", 10)], state="in-progress")
        usage_rows(root, dict(phase="phase-a", type="impl", round=3, role="lead", target_phase="phase-b",
                              target_type="plan", target_round=1, **TOK))
        b = report.phase_report(root, "phase-b")
        check_invariant(b["coverage"])
        assert turn(b, "plan", 1, "lead")["status"] == "matched"
        assert b["usage"]["rows"] == 0                               # consumption stays with the stored phase
        a = report.phase_report(root, "phase-a")
        check_invariant(a["coverage"])
        assert a["usage"]["rows"] == 1
        assert turn(a, "impl", 4, "lead")["status"] == "unmatched"

    def test_row_selected_by_both_queries_counted_once(self, root):
        write_cycle(root, P, "impl", IMPL[:1], state="in-progress")
        usage_rows(root, dict(phase=P, type="impl", round=0, role="lead", target_phase=P, target_type="impl",
                              target_round=1, **TOK))
        rep = report.phase_report(root, P)
        assert turn(rep, "impl", 1, "lead")["row_ids"] == [1]

    def test_panels(self, root):
        write_cycle(root, P, "impl", [
            E(1, "lead", "SUBMIT_FOR_REVIEW", 0), E(1, "reviewer", "REQUEST_CHANGES", 5, updated_by="codex panel"),
            E(2, "lead", "SUBMIT_FOR_REVIEW", 10), E(2, "reviewer", "REQUEST_CHANGES", 15, updated_by="codex panel"),
            E(3, "lead", "SUBMIT_FOR_REVIEW", 20), E(3, "reviewer", "APPROVE", 25, updated_by="codex")])
        usage_rows(root,
                   *[dict(phase=P, type="impl", round=1, role="reviewer", kind=f"panel:{lens}", **TOK)
                     for lens in ("correctness", "scope", "verification")],
                   dict(phase=P, type="impl", round=2, role="reviewer", **TOK),                 # plain row, panel verdict
                   dict(phase=P, type="impl", round=3, role="reviewer", kind="panel:scope", **TOK))  # lens row, plain verdict
        rep = report.phase_report(root, P)
        check_invariant(rep["coverage"])
        r1 = turn(rep, "impl", 1, "reviewer")
        assert r1["status"] == "matched" and len(r1["row_ids"]) == 3
        assert turn(rep, "impl", 2, "reviewer")["status"] == "unmatched"
        assert turn(rep, "impl", 3, "reviewer")["status"] == "unmatched"

    def test_failed_rows(self, root):
        write_cycle(root, P, "impl", [
            E(1, "lead", "SUBMIT_FOR_REVIEW", 0), E(1, "reviewer", "REQUEST_CHANGES", 5),
            E(2, "lead", "SUBMIT_FOR_REVIEW", 10), E(2, "reviewer", "REQUEST_CHANGES", 15),
            E(3, "lead", "SUBMIT_FOR_REVIEW", 20), E(3, "reviewer", "APPROVE", 25)])
        usage_rows(root,
                   dict(phase=P, type="impl", round=1, role="reviewer", status="nonzero_exit", **TOK),
                   dict(phase=P, type="impl", round=2, role="reviewer", status="cancelled", **TOK),
                   dict(phase=P, type="impl", round=2, role="reviewer"),
                   dict(phase=P, type="impl", round=3, role="reviewer", status="cancelled"))
        rep = report.phase_report(root, P)
        check_invariant(rep["coverage"])
        assert (turn(rep, "impl", 1, "reviewer")["status"], turn(rep, "impl", 1, "reviewer")["non_ok_rows"]) == ("matched", 1)
        assert (turn(rep, "impl", 2, "reviewer")["status"], turn(rep, "impl", 2, "reviewer")["non_ok_rows"]) == ("matched", 1)
        assert (turn(rep, "impl", 3, "reviewer")["status"], turn(rep, "impl", 3, "reviewer")["non_ok_rows"]) == ("no_token_data", 1)
        assert rep["coverage"]["turns_with_non_ok_rows"] == 3


# ---------------------------------------------------------------------------
# degraded data
# ---------------------------------------------------------------------------

class TestDegraded:
    def test_no_usage_rows(self, root):
        write_cycle(root, P, "plan", PLAN)
        usage_rows(root, dict(phase="other", role="lead", **TOK))
        rep = report.phase_report(root, P)
        assert rep["usage"]["rows"] == 0 and rep["coverage"]["unmatched"] == 3 and rep["coverage"]["unknown"] == 1
        rc, out = run(root)
        assert "no usage rows stored under this phase" in out and "interactive" not in out
        assert "plan   2 rounds" in out

    def test_missing_timestamps(self, root):
        write_cycle(root, P, "plan", [E(1, "lead", "SUBMIT_FOR_REVIEW", None),
                                      E(1, "reviewer", "APPROVE", None)])
        rep = report.phase_report(root, P)
        assert rep["cycles"]["plan"]["rounds"] == 1
        assert rep["cycles"]["plan"]["time"]["reviewer"] == {"seconds": 0.0, "spans": 0, "unknown": 1}
        assert rep["wall_clock"]["seconds"] is None
        rc, out = run(root)
        assert rc == 0 and "start→approve unknown" not in out and "unknown" in out

    def test_plan_only(self, root):
        write_cycle(root, P, "plan", PLAN)
        rep = report.phase_report(root, P)
        assert set(rep["cycles"]) == {"plan"} and rep["wall_clock"]["approved"] is False

    def test_unknown_phase(self, root):
        rc, out = run(root, "nope")
        assert rc == 1 and out.strip() == "report: no cycles or usage rows found for phase 'nope'"

    def test_usage_argument_errors(self, root):
        assert run(root, P, "--bogus")[0] == 1
        out = io.StringIO()
        assert report.report_command([], project_root=root, out=out) == 1


# ---------------------------------------------------------------------------
# read path: creates nothing, in either mode; older schemas readable
# ---------------------------------------------------------------------------

def _v9_project(root: Path) -> Path:
    from tests.test_db import _raw_db_at
    write_cycle(root, P, "plan", PLAN)
    p, raw = _raw_db_at(root, 9)
    raw.execute("INSERT INTO usage (ts, status, phase, type, round, role, provider, input_tokens) "
                "VALUES (?, 'ok', ?, 'plan', 1, 'reviewer', 'codex', 42)", (_ts(1), P))
    raw.commit(); raw.close()
    return p


@pytest.mark.parametrize("read_only", [False, True])
class TestReadPath:
    def test_files_only_project(self, root, monkeypatch, read_only):
        write_cycle(root, P, "plan", PLAN)
        if read_only:
            monkeypatch.setenv(dualwrite.READ_ONLY_ENV, "1")
        before = snapshot(root)
        rep = report.phase_report(root, P)
        assert rep["cycles"]["plan"]["rounds"] == 2 and rep["usage"] is None
        assert rep["database"] == "no database"
        rc, out = run(root)
        assert rc == 0 and "figures from cycle files only" in out
        assert not (root / ".tagteam").exists() and snapshot(root) == before

    def test_v9_project_report_and_usage(self, root, monkeypatch, read_only):
        from tagteam import usage
        dbp = _v9_project(root)
        if read_only:
            monkeypatch.setenv(dualwrite.READ_ONLY_ENV, "1")
        before = snapshot(root)
        listing = sorted(p.name for p in dbp.parent.iterdir())
        rep = report.phase_report(root, P)
        assert turn(rep, "plan", 1, "reviewer")["status"] == "matched"
        assert rep["usage"]["totals"]["input_tokens"] == 42
        out = io.StringIO()
        assert usage.usage_command(["--by", "model", "--json"], project_root=root, out=out) == 0
        assert json.loads(out.getvalue())["by_model"]["codex (model not reported)"]["input_tokens"] == 42
        assert snapshot(root) == before
        assert sorted(p.name for p in dbp.parent.iterdir()) == listing
        c = sqlite3.connect(dbp)
        assert c.execute("PRAGMA user_version").fetchone()[0] == 9
        assert "target_phase" not in {r[1] for r in c.execute("PRAGMA table_info(usage)")}
        c.close()


class TestCli:
    def test_report_is_a_read_command(self, root, monkeypatch, capsys):
        from tests.test_readonly import _cli
        write_cycle(root, P, "plan", PLAN)
        monkeypatch.setenv(dualwrite.READ_ONLY_ENV, "1")
        rc, out, err = _cli(monkeypatch, capsys, root, "report", "--phase", P)
        assert rc == 0 and "plan   2 rounds" in out
        assert not (root / ".tagteam").exists()
        from tagteam import cli
        assert ("report", None) in cli._read_only_summary()


class TestNoDollarsInViews:
    def test_cockpit_and_hub_assets(self):
        web = Path(report.__file__).parent / "data" / "web"
        for name in ("cockpit.js", "hub.js"):
            js = (web / name).read_text(encoding="utf-8")
            assert "fmtCost" not in js and "cost_usd" not in js and "<th>cost</th>" not in js, name
