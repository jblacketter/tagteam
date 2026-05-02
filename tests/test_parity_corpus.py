"""CI parity test — render parity across the recorded fixture corpus.

For each scenario in `tests/fixtures/recorded/`:
  1. Import the file-side state into a fresh SQLite DB.
  2. Render every cycle via both `tagteam.cycle.render_cycle`
     (file-backed) and `tagteam.db.render_cycle` (DB-backed).
  3. Assert byte-identity.

This is the load-bearing parity contract for Phase 28: auto-export
to markdown is non-regressive iff the two renderers agree on every
cycle shape. CI catching a drift here catches a real bug before it
reaches users.

Also runs a smoke check that re-recording the corpus produces a
byte-identical result, catching nondeterminism in the recorder
itself.
"""

import filecmp
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from tagteam import cycle, db


CORPUS_DIR = Path(__file__).parent / "fixtures" / "recorded"
RECORDER = Path(__file__).parent / "fixtures" / "recorder.py"


def _scenario_dirs():
    if not CORPUS_DIR.is_dir():
        return []
    return sorted(d for d in CORPUS_DIR.iterdir() if d.is_dir())


def _cycles_in_dir(project_dir: Path) -> list[tuple[str, str]]:
    """Walk docs/handoffs/ and return [(phase, type), ...] for every
    cycle (status file present)."""
    handoffs = project_dir / "docs" / "handoffs"
    if not handoffs.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for f in sorted(handoffs.iterdir()):
        m = re.match(r"^(.+)_(plan|impl)_status\.json$", f.name)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


@pytest.mark.parametrize(
    "scenario_dir",
    _scenario_dirs(),
    ids=lambda d: d.name if hasattr(d, "name") else str(d),
)
class TestRenderParityCorpus:
    """Per-scenario parity check. Each scenario is its own pytest
    parametrization so a failure names exactly which scenario
    regressed."""

    def test_db_render_matches_file_render(self, scenario_dir, tmp_path):
        # Copy the scenario into a tmp project so we don't mutate the
        # committed fixtures (cycle.render_cycle has no side effects,
        # but the importer creates a DB sidecar).
        project = tmp_path / "project"
        shutil.copytree(scenario_dir, project)

        # Import into fresh DB at non-default path so we're sure we
        # don't read a leftover.
        db_path = tmp_path / "shadow.db"
        conn = db.connect(db_path=db_path)
        try:
            db.import_from_files(project, conn)

            cycles = _cycles_in_dir(project)
            assert cycles, (
                f"scenario {scenario_dir.name} has no cycles "
                "— corpus or recorder is broken"
            )

            for phase, cycle_type in cycles:
                file_md = cycle.render_cycle(
                    phase, cycle_type, str(project)
                )
                db_md = db.render_cycle(conn, phase, cycle_type)
                assert file_md == db_md, (
                    f"render parity failed for "
                    f"{scenario_dir.name}::{phase}_{cycle_type}\n"
                    f"--- file ---\n{file_md}\n--- db ---\n{db_md}"
                )
        finally:
            conn.close()


class TestRecorderDeterminism:
    """Re-run the recorder and assert the output matches the
    committed corpus byte-for-byte. Catches nondeterminism in the
    recorder OR in the writers it drives."""

    def test_recorder_produces_identical_corpus(self, tmp_path):
        if not _scenario_dirs():
            pytest.skip("no committed corpus to compare against")

        # Run the recorder fresh into tmp_path/recorded.
        target = tmp_path / "recorded"
        # Invoke as a subprocess so we don't pollute the test
        # process's `cycle.datetime` / `state.datetime` patches.
        import subprocess
        result = subprocess.run(
            [sys.executable, str(RECORDER), str(target)],
            capture_output=True, text=True, cwd=Path(__file__).parent.parent,
        )
        assert result.returncode == 0, (
            f"recorder failed: stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )

        # Compare each scenario's contents to the committed version.
        committed_scenarios = {d.name for d in _scenario_dirs()}
        new_scenarios = {d.name for d in target.iterdir() if d.is_dir()}
        assert committed_scenarios == new_scenarios, (
            f"scenario set drifted: "
            f"committed={sorted(committed_scenarios)} "
            f"recorder={sorted(new_scenarios)}"
        )

        for scenario in committed_scenarios:
            committed = CORPUS_DIR / scenario
            fresh = target / scenario
            _assert_dirs_byte_identical(committed, fresh, scenario)


def _assert_dirs_byte_identical(a: Path, b: Path, label: str) -> None:
    """Compare two directories recursively; assert byte-identical
    contents. Fails fast on the first mismatch with a useful message."""
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        msg = [f"directory diff for scenario {label}:"]
        if cmp.left_only:
            msg.append(f"  only in committed: {cmp.left_only}")
        if cmp.right_only:
            msg.append(f"  only in recorder:  {cmp.right_only}")
        if cmp.diff_files:
            msg.append(f"  differing files:   {cmp.diff_files}")
            for fname in cmp.diff_files:
                old = (a / fname).read_text()
                new = (b / fname).read_text()
                msg.append(f"  --- committed/{fname} ---\n{old}")
                msg.append(f"  --- recorded/{fname} ---\n{new}")
        if cmp.funny_files:
            msg.append(f"  funny files: {cmp.funny_files}")
        pytest.fail("\n".join(msg))
    for sub in cmp.common_dirs:
        _assert_dirs_byte_identical(a / sub, b / sub, f"{label}/{sub}")
