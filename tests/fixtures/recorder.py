"""
Deterministic CI parity-fixtures recorder for Phase 28 Step A.

Drives `tagteam.cycle.init_cycle` / `add_round` through a defined
matrix of scenarios under a frozen clock, captures the resulting
file-side state into `tests/fixtures/recorded/<scenario>/`, and
strips the auto-generated `.tagteam/` shadow DB so the fixtures
represent only the canonical files.

The fixtures are committed and consumed by `tests/test_parity_corpus.py`,
which re-imports them into a fresh DB and asserts the file/DB
renderers produce byte-identical output.

Usage:
    python tests/fixtures/recorder.py tests/fixtures/recorded/

Re-running the recorder against an existing fixtures directory must
produce a byte-identical diff if the recorder and tagteam version
are unchanged. A non-empty diff is the proof that something
legitimately changed (renderer output, schema, etc.) and the
fixtures need to be re-recorded.

Determinism notes:
  - `cycle.datetime` and `state.datetime` are monkeypatched to a
    fake clock that returns sequential timestamps (1-second
    increments from a fixed base).
  - Fixture projects are not git repos, so `cycle._capture_baseline`
    returns None — no env-dependent SHA enters the fixture.
  - Agent names (Lead/Reviewer) are fixed strings.

Coverage matrix at the bottom of this module.
"""

from __future__ import annotations

import json
import shutil
import sys
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterator


_BASE_TS = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


class _FakeDatetime:
    """Minimal stand-in for datetime that returns sequential timestamps.

    `now(tz)` returns the next timestamp in the sequence, advanced by
    one second per call. Other classmethods/static methods delegate
    to the real datetime so anything else (e.g. `fromisoformat`)
    keeps working.
    """

    _counter = [0]

    def __init__(self, real):
        self._real = real

    @classmethod
    def now(cls, tz=None):
        offset = cls._counter[0]
        cls._counter[0] += 1
        ts = _BASE_TS + timedelta(seconds=offset)
        if tz is None:
            return ts.replace(tzinfo=None)
        return ts.astimezone(tz)

    def __getattr__(self, name):
        return getattr(self._real, name)

    @classmethod
    def reset(cls):
        cls._counter[0] = 0


@contextmanager
def _frozen_clock() -> Iterator[None]:
    """Patch `cycle.datetime` and `state.datetime` to the fake clock
    for the duration of the with-block. Resets the counter on entry."""
    from tagteam import cycle as cycle_mod
    from tagteam import state as state_mod

    _FakeDatetime.reset()

    real_cycle_dt = cycle_mod.datetime
    real_state_dt = state_mod.datetime
    cycle_mod.datetime = _FakeDatetime
    state_mod.datetime = _FakeDatetime
    try:
        yield
    finally:
        cycle_mod.datetime = real_cycle_dt
        state_mod.datetime = real_state_dt


# ---------- Scenario library ----------

def _scenario_plan_approve_direct(project: Path) -> None:
    """Simplest: SUBMIT → APPROVE."""
    from tagteam import cycle
    cycle.init_cycle(
        "phase-a", "plan", "Lead", "Reviewer",
        "First draft of the plan.",
        str(project),
    )
    cycle.add_round(
        "phase-a", "plan", "reviewer", "APPROVE", 1,
        "Approved.", str(project),
    )


def _scenario_plan_revision(project: Path) -> None:
    """SUBMIT → REQUEST_CHANGES → SUBMIT → APPROVE."""
    from tagteam import cycle
    cycle.init_cycle(
        "phase-b", "plan", "Lead", "Reviewer",
        "v1 draft.",
        str(project),
    )
    cycle.add_round(
        "phase-b", "plan", "reviewer", "REQUEST_CHANGES", 1,
        "Please address: scope creep, missing rollback story.",
        str(project),
    )
    cycle.add_round(
        "phase-b", "plan", "lead", "SUBMIT_FOR_REVIEW", 2,
        "v2 — scope tightened, rollback added.",
        str(project),
    )
    cycle.add_round(
        "phase-b", "plan", "reviewer", "APPROVE", 2,
        "Approved.", str(project),
    )


def _scenario_plan_amend(project: Path) -> None:
    """SUBMIT → AMEND → APPROVE. Exercises the AMEND mid-round path."""
    from tagteam import cycle
    cycle.init_cycle(
        "phase-c", "plan", "Lead", "Reviewer",
        "Initial submission with one open question.",
        str(project),
    )
    cycle.add_round(
        "phase-c", "plan", "lead", "AMEND", 1,
        "Addendum: the human arbiter answered question 3 — proceeding.",
        str(project),
    )
    cycle.add_round(
        "phase-c", "plan", "reviewer", "APPROVE", 1,
        "Approved.", str(project),
    )


def _scenario_plan_escalated(project: Path) -> None:
    """SUBMIT → REQUEST_CHANGES → SUBMIT → ESCALATE.
    Reviewer escalates instead of approving."""
    from tagteam import cycle
    cycle.init_cycle(
        "phase-d", "plan", "Lead", "Reviewer",
        "First proposal.",
        str(project),
    )
    cycle.add_round(
        "phase-d", "plan", "reviewer", "REQUEST_CHANGES", 1,
        "Blockers in scope.",
        str(project),
    )
    cycle.add_round(
        "phase-d", "plan", "lead", "SUBMIT_FOR_REVIEW", 2,
        "Revised but the same disagreement on scope persists.",
        str(project),
    )
    cycle.add_round(
        "phase-d", "plan", "reviewer", "ESCALATE", 2,
        "Cannot reach agreement on scope; needs human arbiter.",
        str(project),
    )


def _scenario_non_ascii_content(project: Path) -> None:
    """Non-ASCII content in plan text. The renderers must preserve
    UTF-8 round-trip; Codex's review flagged this as worth covering."""
    from tagteam import cycle
    cycle.init_cycle(
        "phase-e", "plan", "Lead", "Reviewer",
        "プラン: お母さん API — 非ASCII content shouldn't break parity.",
        str(project),
    )
    cycle.add_round(
        "phase-e", "plan", "reviewer", "APPROVE", 1,
        "了解 ✓",
        str(project),
    )


def _scenario_plan_needs_human(project: Path) -> None:
    """SUBMIT → NEED_HUMAN. NEED_HUMAN renders STATE: needs-human
    while ESCALATE renders STATE: escalated — they are NOT the same
    parity state, contrary to a previous (now-fixed) recorder
    comment that claimed they were."""
    from tagteam import cycle
    cycle.init_cycle(
        "phase-f", "plan", "Lead", "Reviewer",
        "Plan submission with one open question requiring human input.",
        str(project),
    )
    cycle.add_round(
        "phase-f", "plan", "reviewer", "NEED_HUMAN", 1,
        "Need human decision on whether to use library X or Y.",
        str(project),
    )


def _scenario_multi_cycle(project: Path) -> None:
    """Plan + impl cycles for the same phase. Exercises
    db.import_from_files walking multiple cycle file pairs in
    docs/handoffs/ — a code path the single-cycle scenarios don't
    reach."""
    from tagteam import cycle
    cycle.init_cycle(
        "phase-g", "plan", "Lead", "Reviewer",
        "Plan for phase g.", str(project),
    )
    cycle.add_round(
        "phase-g", "plan", "reviewer", "APPROVE", 1,
        "Plan approved.", str(project),
    )
    cycle.init_cycle(
        "phase-g", "impl", "Lead", "Reviewer",
        "Implementation matching the approved plan.",
        str(project),
    )
    cycle.add_round(
        "phase-g", "impl", "reviewer", "APPROVE", 1,
        "Implementation approved.", str(project),
    )


def _scenario_empty_content(project: Path) -> None:
    """SUBMIT with empty content → APPROVE. Validation allows empty
    strings; the renderers must handle them without producing torn
    or differing output."""
    from tagteam import cycle
    cycle.init_cycle(
        "phase-h", "plan", "Lead", "Reviewer",
        "",
        str(project),
    )
    cycle.add_round(
        "phase-h", "plan", "reviewer", "APPROVE", 1,
        "",
        str(project),
    )


SCENARIOS = {
    "plan-approve-direct":  _scenario_plan_approve_direct,
    "plan-revision":        _scenario_plan_revision,
    "plan-amend":           _scenario_plan_amend,
    "plan-escalated":       _scenario_plan_escalated,
    "plan-needs-human":     _scenario_plan_needs_human,
    "multi-cycle":          _scenario_multi_cycle,
    "empty-content":        _scenario_empty_content,
    "non-ascii-content":    _scenario_non_ascii_content,
}


# Coverage matrix (✓ = present, ✓✓ = appears multiple times):
#                       SUBMIT  REQ_CH  APPROVE  AMEND  ESCALATE  NEED_HUMAN  empty  non-ASCII  multi
# plan-approve-direct     ✓               ✓
# plan-revision           ✓✓     ✓        ✓
# plan-amend              ✓               ✓        ✓
# plan-escalated          ✓✓     ✓                            ✓
# plan-needs-human        ✓                                                ✓
# multi-cycle             ✓✓              ✓✓                                                              ✓
# empty-content           ✓               ✓                                          ✓
# non-ascii-content       ✓               ✓                                                    ✓


# ---------- Driver ----------

def record_one(scenario: str, target_dir: Path) -> None:
    """Record one scenario into target_dir. Wipes target_dir first
    so the output is deterministic regardless of prior runs.

    Uses an OS-level tmpdir as the scenario project so that
    `cycle._capture_baseline` does NOT walk up into whatever git
    repo the recorder happens to be invoked from. tmpdirs in /tmp
    are not inside the tagteam source tree, so baseline is always
    None and fixtures never carry a stray host git SHA.
    """
    import tempfile

    fn = SCENARIOS[scenario]

    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)

    with tempfile.TemporaryDirectory(prefix="tagteam-recorder-") as td:
        project = Path(td)
        (project / "docs" / "handoffs").mkdir(parents=True)
        (project / "tagteam.yaml").write_text(
            "agents:\n  lead: {name: Lead}\n  reviewer: {name: Reviewer}\n",
            encoding="utf-8",
        )
        # Reset the cached project root so each scenario gets a fresh
        # resolution targeting our tmpdir.
        from tagteam import state as state_mod
        state_mod._cached_project_root = None

        with _frozen_clock():
            fn(project)

        # Strip the auto-created shadow DB. The fixture represents
        # only the canonical file-side state.
        tagteam_dir = project / ".tagteam"
        if tagteam_dir.exists():
            shutil.rmtree(tagteam_dir)

        # Copy file-side state to the target. We replicate the
        # subset that import_from_files needs: docs/handoffs/* and
        # handoff-state.json. tagteam.yaml is also copied so the
        # fixture is a self-contained tagteam project.
        for relpath in [
            "docs/handoffs",
            "handoff-state.json",
            "tagteam.yaml",
        ]:
            src = project / relpath
            if src.is_dir():
                shutil.copytree(src, target_dir / relpath)
            elif src.exists():
                (target_dir / relpath).parent.mkdir(
                    parents=True, exist_ok=True
                )
                shutil.copy2(src, target_dir / relpath)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("Usage: python tests/fixtures/recorder.py <output-dir>")
        return 1
    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    for scenario in SCENARIOS:
        target = out / scenario
        print(f"recording {scenario} -> {target}")
        record_one(scenario, target)
    print(f"\nDone — {len(SCENARIOS)} scenarios written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
