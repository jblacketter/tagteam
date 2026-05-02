"""Tests for the Phase 28 Step A repair state machine
(`tagteam.repair`).

Covers:
  - `should_attempt_repair` gating on sentinel + backoff window
  - `attempt_repair` happy path (sentinel cleared, success returned)
  - `attempt_repair` failure path (sentinel preserved, backoff
    advanced, exponential schedule)
  - `attempt_repair` reentrancy under existing writer lock
  - `needs_louder_signal` 24-hour threshold
  - Sentinel `since` preserved across failures (louder-signal anchor)
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from tagteam import cycle, db, dualwrite, repair


@pytest.fixture
def project(tmp_path):
    (tmp_path / "docs" / "handoffs").mkdir(parents=True)
    return tmp_path


def _seed_clean_cycle(project):
    """Set up a project with one valid cycle so import_from_files
    has something to import."""
    cycle.init_cycle("p", "plan", "L", "R", "v1", str(project))


# ---------- should_attempt_repair ----------

class TestShouldAttemptRepair:
    def test_false_when_sentinel_clear(self, project):
        assert repair.should_attempt_repair(project) is False

    def test_true_when_sentinel_set_and_no_prior_attempt(self, project):
        dualwrite.mark_db_invalid(project, reason="test")
        assert repair.should_attempt_repair(project) is True

    def test_false_when_within_backoff_window(self, project):
        dualwrite.mark_db_invalid(project, reason="test")
        # Manually set next_attempt_at to the future.
        flag = project / ".tagteam" / "DB_INVALID"
        info = json.loads(flag.read_text())
        future = datetime.now(timezone.utc) + timedelta(minutes=10)
        info["next_attempt_at"] = future.isoformat()
        flag.write_text(json.dumps(info))
        assert repair.should_attempt_repair(project) is False

    def test_true_when_backoff_window_elapsed(self, project):
        dualwrite.mark_db_invalid(project, reason="test")
        flag = project / ".tagteam" / "DB_INVALID"
        info = json.loads(flag.read_text())
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        info["next_attempt_at"] = past.isoformat()
        flag.write_text(json.dumps(info))
        assert repair.should_attempt_repair(project) is True

    def test_true_on_unparseable_backoff(self, project):
        """Permissive on corruption — let the next attempt either
        succeed or rewrite the field."""
        dualwrite.mark_db_invalid(project, reason="test")
        flag = project / ".tagteam" / "DB_INVALID"
        info = json.loads(flag.read_text())
        info["next_attempt_at"] = "not a timestamp"
        flag.write_text(json.dumps(info))
        assert repair.should_attempt_repair(project) is True


# ---------- attempt_repair: happy path ----------

class TestAttemptRepairHappyPath:
    def test_clears_sentinel_after_successful_rebuild(self, project):
        _seed_clean_cycle(project)
        dualwrite.mark_db_invalid(project, reason="simulated failure")
        assert dualwrite.is_db_invalid(project)

        result = repair.attempt_repair(project)
        assert result["success"] is True
        assert dualwrite.is_db_invalid(project) is False

    def test_db_rebuilt_from_files(self, project):
        _seed_clean_cycle(project)
        # Tamper with the DB — mutate a round so it diverges from files.
        conn = db.connect(project_dir=str(project))
        try:
            conn.execute("UPDATE rounds SET content='tampered' WHERE 1=1")
            conn.commit()
        finally:
            conn.close()
        dualwrite.mark_db_invalid(project, reason="simulated")

        result = repair.attempt_repair(project)
        assert result["success"] is True

        conn = db.connect(project_dir=str(project))
        try:
            rows = conn.execute("SELECT content FROM rounds").fetchall()
        finally:
            conn.close()
        # The tampered content was overwritten by the rebuild from files.
        assert all("tampered" not in r[0] for r in rows)

    def test_idempotent_when_sentinel_already_clear(self, project):
        _seed_clean_cycle(project)
        # No sentinel set — should be a no-op success.
        result = repair.attempt_repair(project)
        assert result["success"] is True
        assert "sentinel not set" in result.get("reason", "")


# ---------- attempt_repair: failure path ----------

class TestAttemptRepairFailurePath:
    def test_failure_keeps_sentinel_set(self, project):
        _seed_clean_cycle(project)
        dualwrite.mark_db_invalid(project, reason="simulated")

        with patch(
            "tagteam.db.import_from_files",
            side_effect=RuntimeError("import failed"),
        ):
            result = repair.attempt_repair(project)

        assert result["success"] is False
        assert dualwrite.is_db_invalid(project) is True
        assert "import failed" in result["reason"]

    def test_failure_records_backoff(self, project):
        _seed_clean_cycle(project)
        dualwrite.mark_db_invalid(project, reason="simulated")
        now = datetime.now(timezone.utc)

        with patch(
            "tagteam.db.import_from_files",
            side_effect=RuntimeError("import failed"),
        ):
            result = repair.attempt_repair(project, now=now)

        next_at = datetime.fromisoformat(result["next_attempt_at"])
        # First failure: 1 minute backoff
        assert (next_at - now).total_seconds() == 60

    def test_exponential_backoff_schedule(self, project):
        """Each failure doubles the backoff up to 1-hour cap."""
        _seed_clean_cycle(project)
        dualwrite.mark_db_invalid(project, reason="simulated")
        now = datetime.now(timezone.utc)

        expected_delays = [60, 120, 240, 480, 960, 1920, 3600, 3600, 3600]

        with patch(
            "tagteam.db.import_from_files",
            side_effect=RuntimeError("nope"),
        ):
            for i, expected_delay in enumerate(expected_delays):
                result = repair.attempt_repair(project, now=now)
                next_at = datetime.fromisoformat(result["next_attempt_at"])
                actual_delay = (next_at - now).total_seconds()
                assert actual_delay == expected_delay, (
                    f"failure #{i+1}: expected {expected_delay}s, "
                    f"got {actual_delay}s"
                )

    def test_since_preserved_across_failures(self, project):
        """The `since` timestamp must NOT advance on each failure
        — it anchors the 24-hour louder-signal threshold."""
        _seed_clean_cycle(project)
        dualwrite.mark_db_invalid(project, reason="simulated")
        original_since = dualwrite.get_db_invalid_info(project)["since"]

        with patch(
            "tagteam.db.import_from_files",
            side_effect=RuntimeError("nope"),
        ):
            for _ in range(3):
                repair.attempt_repair(project)

        info = dualwrite.get_db_invalid_info(project)
        assert info["since"] == original_since
        assert info["consecutive_failures"] == 3

    def test_inconsistent_file_blocks_repair(self, project):
        """If the file side has malformed input, repair refuses to
        rebuild — the operator needs to fix the files first. Sentinel
        stays set, with a `file_inconsistent` reason."""
        _seed_clean_cycle(project)
        # Corrupt the file side: rounds.jsonl with a non-object line
        # trips file_side_sanity's `rounds_jsonl_object_shape` check.
        rp = project / "docs" / "handoffs" / "p_plan_rounds.jsonl"
        rp.write_text("1\n")
        dualwrite.mark_db_invalid(project, reason="simulated")

        result = repair.attempt_repair(project)
        assert result["success"] is False
        assert dualwrite.is_db_invalid(project) is True
        assert "file_inconsistent" in result["reason"]
        assert "object_shape" in result["reason"]


# ---------- needs_louder_signal ----------

class TestNeedsLouderSignal:
    def test_false_when_sentinel_clear(self, project):
        assert repair.needs_louder_signal(project) is False

    def test_false_when_recent(self, project):
        dualwrite.mark_db_invalid(project, reason="test")
        # Default `since` is now — recent, no louder signal.
        assert repair.needs_louder_signal(project) is False

    def test_true_when_24h_old(self, project):
        dualwrite.mark_db_invalid(project, reason="test")
        # Force `since` to 25 hours ago.
        flag = project / ".tagteam" / "DB_INVALID"
        info = json.loads(flag.read_text())
        old = datetime.now(timezone.utc) - timedelta(hours=25)
        info["since"] = old.isoformat()
        flag.write_text(json.dumps(info))
        assert repair.needs_louder_signal(project) is True

    def test_threshold_boundary(self, project):
        """Just under 24h: False. At/over 24h: True."""
        dualwrite.mark_db_invalid(project, reason="test")
        flag = project / ".tagteam" / "DB_INVALID"
        info = json.loads(flag.read_text())

        # 23h59m: False
        info["since"] = (
            datetime.now(timezone.utc) - timedelta(hours=23, minutes=59)
        ).isoformat()
        flag.write_text(json.dumps(info))
        assert repair.needs_louder_signal(project) is False

        # 24h01m: True
        info["since"] = (
            datetime.now(timezone.utc) - timedelta(hours=24, minutes=1)
        ).isoformat()
        flag.write_text(json.dumps(info))
        assert repair.needs_louder_signal(project) is True


# ---------- Reentrancy with existing writer lock ----------

class TestRepairReentrancy:
    def test_repair_works_when_caller_holds_lock(self, project):
        """Retry-on-next-write callers run inside the dual-write
        writer lock. attempt_repair must re-acquire harmlessly."""
        _seed_clean_cycle(project)
        dualwrite.mark_db_invalid(project, reason="simulated")

        with dualwrite.writer_lock(project):
            result = repair.attempt_repair(project)

        assert result["success"] is True
        assert dualwrite.is_db_invalid(project) is False
