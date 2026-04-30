"""Regression tests for the lead-side AMEND action.

Covers Issue #5 from docs/handoff-cycle-issues-2026-04-24.md.

AMEND semantics:
- lead-only, mid-review-only (state=in-progress, ready_for=reviewer)
- no round bump, no state transition, no top-level state derive
- excluded from stale-round detection (progress, not staleness)
- --round must match the active round
"""

import json
from pathlib import Path

import pytest

import tagteam.state as state_mod
from tagteam.cycle import (
    STALE_ROUND_LIMIT,
    _count_stale_rounds,
    add_round,
    cycle_command,
    init_cycle,
    read_status,
)
from tagteam.parser import parse_jsonl_rounds, read_cycle_rounds


@pytest.fixture(autouse=True)
def _reset_root_cache():
    state_mod._cached_project_root = None
    state_mod._warned_outer = False
    yield
    state_mod._cached_project_root = None
    state_mod._warned_outer = False


@pytest.fixture
def project(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "tagteam.yaml").write_text("agents: {}\n")
    (proj / "docs" / "handoffs").mkdir(parents=True)
    monkeypatch.chdir(proj)
    return str(proj)


def _seed_cycle(project_dir: str, *, phase: str = "p", cycle_type: str = "plan"):
    init_cycle(phase, cycle_type, "L", "R", "round-1 submission",
               project_dir=project_dir, updated_by="L")


def _read_jsonl(project_dir: str, phase: str, cycle_type: str) -> list[dict]:
    p = Path(project_dir) / "docs/handoffs" / f"{phase}_{cycle_type}_rounds.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


# --- 1: happy path ---

def test_amend_mid_review_appends_without_bumping_round(project):
    _seed_cycle(project)
    add_round("p", "plan", "lead", "AMEND", 1, "answer to open question",
              project_dir=project, updated_by="L")

    status = read_status("p", "plan", project)
    assert status["state"] == "in-progress"
    assert status["ready_for"] == "reviewer"
    assert status["round"] == 1

    entries = _read_jsonl(project, "p", "plan")
    assert len(entries) == 2  # original SUBMIT_FOR_REVIEW + AMEND
    assert entries[1]["action"] == "AMEND"
    assert entries[1]["role"] == "lead"
    assert entries[1]["round"] == 1
    assert entries[1]["content"] == "answer to open question"


# --- 2: error when ready_for == lead ---

def test_amend_after_request_changes_errors(project):
    _seed_cycle(project)
    # Reviewer asks for changes, turn returns to lead.
    add_round("p", "plan", "reviewer", "REQUEST_CHANGES", 1, "fix x",
              project_dir=project, updated_by="R")
    status = read_status("p", "plan", project)
    assert status["ready_for"] == "lead"

    before = _read_jsonl(project, "p", "plan")
    with pytest.raises(ValueError, match="AMEND only valid mid-review"):
        add_round("p", "plan", "lead", "AMEND", 1, "too late",
                  project_dir=project, updated_by="L")
    after = _read_jsonl(project, "p", "plan")
    assert before == after  # JSONL unchanged


# --- 3: reviewer cannot AMEND ---

def test_amend_with_reviewer_role_errors(project):
    _seed_cycle(project)
    before = _read_jsonl(project, "p", "plan")
    with pytest.raises(ValueError, match="AMEND requires role=lead"):
        add_round("p", "plan", "reviewer", "AMEND", 1, "no",
                  project_dir=project, updated_by="R")
    after = _read_jsonl(project, "p", "plan")
    assert before == after


# --- 4: AMEND after APPROVE errors (cycle done) ---

def test_amend_after_approve_errors(project):
    _seed_cycle(project)
    add_round("p", "plan", "reviewer", "APPROVE", 1, "ok",
              project_dir=project, updated_by="R")
    before = _read_jsonl(project, "p", "plan")
    with pytest.raises(ValueError, match="AMEND only valid mid-review"):
        add_round("p", "plan", "lead", "AMEND", 1, "post-approve",
                  project_dir=project, updated_by="L")
    after = _read_jsonl(project, "p", "plan")
    assert before == after


# --- 5: many AMENDs do not auto-escalate ---

def test_repeated_amends_do_not_auto_escalate(project):
    _seed_cycle(project)
    for i in range(STALE_ROUND_LIMIT + 1):
        add_round("p", "plan", "lead", "AMEND", 1, f"amend {i}",
                  project_dir=project, updated_by="L")
    status = read_status("p", "plan", project)
    assert status["state"] == "in-progress"
    assert status["ready_for"] == "reviewer"
    assert _count_stale_rounds("p", "plan", project) == 0


# --- 6: parser exposes lead_amendments ---

def test_parser_returns_lead_amendments(project):
    _seed_cycle(project)
    add_round("p", "plan", "lead", "AMEND", 1, "first amend",
              project_dir=project, updated_by="L")
    add_round("p", "plan", "lead", "AMEND", 1, "second amend",
              project_dir=project, updated_by="L")

    jsonl_path = Path(project) / "docs/handoffs/p_plan_rounds.jsonl"
    rounds = parse_jsonl_rounds(jsonl_path)
    assert rounds is not None
    assert len(rounds) == 1
    r1 = rounds[0]
    assert r1["round"] == 1
    assert r1["lead_action"] == "SUBMIT_FOR_REVIEW"
    assert isinstance(r1["lead_amendments"], list)
    assert len(r1["lead_amendments"]) == 2
    assert r1["lead_amendments"][0]["content"] == "first amend"
    assert "ts" in r1["lead_amendments"][0]


# --- 7: cycle rounds CLI surfaces lead_amendments ---

def test_cli_rounds_includes_lead_amendments(project, capsys):
    _seed_cycle(project)
    add_round("p", "plan", "lead", "AMEND", 1, "amend body",
              project_dir=project, updated_by="L")

    capsys.readouterr()
    rc = cycle_command(["rounds", "--phase", "p", "--type", "plan"])
    assert rc == 0
    out = capsys.readouterr().out
    rounds = [json.loads(line) for line in out.strip().splitlines() if line.strip()]
    assert all("lead_amendments" in r for r in rounds)
    r1 = next(r for r in rounds if r.get("round") == 1)
    assert len(r1["lead_amendments"]) == 1
    assert r1["lead_amendments"][0]["content"] == "amend body"


# --- 8: round-pinning ---

def test_amend_with_wrong_round_errors(project):
    _seed_cycle(project)
    before = _read_jsonl(project, "p", "plan")
    with pytest.raises(ValueError, match=r"does not match the active round \(1\)"):
        add_round("p", "plan", "lead", "AMEND", 999, "wrong round",
                  project_dir=project, updated_by="L")
    after = _read_jsonl(project, "p", "plan")
    assert before == after


# --- 9: legacy markdown schema stability ---

def test_legacy_markdown_round_carries_empty_lead_amendments(project, monkeypatch):
    handoffs = Path(project) / "docs/handoffs"
    md = handoffs / "legacy_plan_cycle.md"
    md.write_text(
        "# Legacy cycle\n\n"
        "## Round 1\n"
        "**Lead** [SUBMIT_FOR_REVIEW]:\n\n"
        "Plan body.\n\n"
        "**Reviewer** [APPROVE]:\n\n"
        "Approved.\n",
        encoding="utf-8",
    )

    rounds = read_cycle_rounds("legacy", "plan")
    assert rounds is not None
    for r in rounds:
        assert r.get("lead_amendments") == []


# --- 10: CLI clean-error contract ---

def test_cli_amend_wrong_role_returns_1_no_traceback(project, capsys):
    _seed_cycle(project)
    before = _read_jsonl(project, "p", "plan")

    rc = cycle_command([
        "add", "--phase", "p", "--type", "plan",
        "--role", "reviewer", "--action", "AMEND",
        "--round", "1", "--updated-by", "R",
        "--content", "no",
    ])
    captured = capsys.readouterr()

    assert rc == 1
    assert "AMEND requires role=lead" in captured.err
    assert "Traceback" not in captured.err
    after = _read_jsonl(project, "p", "plan")
    assert before == after
