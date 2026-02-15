"""Tests for the shared cycle document parser."""

import tempfile
from pathlib import Path

import pytest

from ai_handoff.parser import extract_all_rounds, format_rounds_html, _extract_summary


SAMPLE_CYCLE = """\
# Plan Review Cycle: test-phase

**Phase:** test-phase
**Type:** Plan Review

---

## Round 1

### Lead

**Action: SUBMIT_FOR_REVIEW**

This is the lead's submission summary.

### Reviewer

**Action: REQUEST_CHANGES**

**Blocking 1:** Something needs fixing.

The plan needs more detail on error handling.

---

## Round 2

### Lead

**Action: SUBMIT_REVISED_PLAN**

Addressed all feedback from Round 1.

### Reviewer

**Action: APPROVE**

Looks good. Ship it.

---

<!-- CYCLE_STATUS -->
READY_FOR: lead
ROUND: 2
STATE: approved
"""

EMPTY_CYCLE = """\
# Plan Review Cycle: empty-phase

No rounds here.
"""

PARTIAL_CYCLE = """\
## Round 1

### Lead

**Action: SUBMIT_FOR_REVIEW**

Initial submission.

### Reviewer

_awaiting response_
"""

LEGACY_CYCLE = """\
## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Legacy lead submission.

### Reviewer

**Action:** APPROVE

Legacy approval.
"""


class TestExtractAllRounds:
    def test_extracts_two_rounds(self, tmp_path):
        cycle_file = tmp_path / "test_cycle.md"
        cycle_file.write_text(SAMPLE_CYCLE)

        rounds = extract_all_rounds(cycle_file)
        assert rounds is not None
        assert len(rounds) == 2

    def test_round_numbers(self, tmp_path):
        cycle_file = tmp_path / "test_cycle.md"
        cycle_file.write_text(SAMPLE_CYCLE)

        rounds = extract_all_rounds(cycle_file)
        assert rounds[0]["round"] == 1
        assert rounds[1]["round"] == 2

    def test_lead_action_extracted(self, tmp_path):
        cycle_file = tmp_path / "test_cycle.md"
        cycle_file.write_text(SAMPLE_CYCLE)

        rounds = extract_all_rounds(cycle_file)
        assert rounds[0]["lead_action"] == "SUBMIT_FOR_REVIEW"
        assert rounds[1]["lead_action"] == "SUBMIT_REVISED_PLAN"

    def test_reviewer_action_extracted(self, tmp_path):
        cycle_file = tmp_path / "test_cycle.md"
        cycle_file.write_text(SAMPLE_CYCLE)

        rounds = extract_all_rounds(cycle_file)
        assert rounds[0]["action"] == "REQUEST_CHANGES"
        assert rounds[1]["action"] == "APPROVE"

    def test_lead_summary_extracted(self, tmp_path):
        cycle_file = tmp_path / "test_cycle.md"
        cycle_file.write_text(SAMPLE_CYCLE)

        rounds = extract_all_rounds(cycle_file)
        assert rounds[0]["lead_summary"] == "This is the lead's submission summary."

    def test_reviewer_summary_extracted(self, tmp_path):
        cycle_file = tmp_path / "test_cycle.md"
        cycle_file.write_text(SAMPLE_CYCLE)

        rounds = extract_all_rounds(cycle_file)
        # First substantive line after Action that isn't a **Blocking header
        assert "plan needs more detail" in rounds[0]["reviewer_summary"]

    def test_returns_none_for_empty_cycle(self, tmp_path):
        cycle_file = tmp_path / "empty_cycle.md"
        cycle_file.write_text(EMPTY_CYCLE)

        result = extract_all_rounds(cycle_file)
        assert result is None

    def test_returns_none_for_missing_file(self, tmp_path):
        missing = tmp_path / "nonexistent.md"
        result = extract_all_rounds(missing)
        assert result is None

    def test_partial_cycle_awaiting_response(self, tmp_path):
        cycle_file = tmp_path / "partial_cycle.md"
        cycle_file.write_text(PARTIAL_CYCLE)

        rounds = extract_all_rounds(cycle_file)
        assert rounds is not None
        assert len(rounds) == 1
        assert rounds[0]["lead_action"] == "SUBMIT_FOR_REVIEW"
        # Reviewer text is "_awaiting response_" which gets skipped by summary
        assert rounds[0]["reviewer_summary"] is None

    def test_legacy_action_format(self, tmp_path):
        """Regression: legacy cycle docs use **Action:** VALUE (bold ends before value)."""
        cycle_file = tmp_path / "legacy_cycle.md"
        cycle_file.write_text(LEGACY_CYCLE)

        rounds = extract_all_rounds(cycle_file)
        assert rounds is not None
        assert len(rounds) == 1
        assert rounds[0]["lead_action"] == "SUBMIT_FOR_REVIEW"
        assert rounds[0]["action"] == "APPROVE"
        assert rounds[0]["lead_summary"] == "Legacy lead submission."
        assert rounds[0]["reviewer_summary"] == "Legacy approval."

    def test_malformed_content(self, tmp_path):
        cycle_file = tmp_path / "malformed.md"
        cycle_file.write_text("just some random text\nno round headers")

        result = extract_all_rounds(cycle_file)
        assert result is None


class TestExtractSummary:
    def test_skips_action_line(self):
        text = "**Action: APPROVE**\nLooks good."
        assert _extract_summary(text) == "Looks good."

    def test_skips_blocking_headers(self):
        text = "**Action: REQUEST_CHANGES**\n**Blocking 1:** Fix it.\nThe actual feedback."
        assert _extract_summary(text) == "The actual feedback."

    def test_skips_legacy_action_line(self):
        """Regression: legacy format **Action:** VALUE (bold ends before value)."""
        text = "**Action:** APPROVE\nLooks good."
        assert _extract_summary(text) == "Looks good."

    def test_skips_awaiting_response(self):
        text = "_awaiting response_"
        assert _extract_summary(text) is None

    def test_returns_none_for_empty(self):
        assert _extract_summary("") is None
        assert _extract_summary("   \n  \n  ") is None


class TestFormatRoundsHtml:
    def test_empty_rounds(self):
        html = format_rounds_html([])
        assert "no-data" in html.lower() or "No rounds" in html

    def test_single_round_html(self):
        rounds = [{
            "round": 1,
            "lead_text": "some text",
            "reviewer_text": "review text",
            "lead_summary": "Lead did things",
            "reviewer_summary": "Looks good",
            "lead_action": "SUBMIT_FOR_REVIEW",
            "action": "APPROVE",
        }]
        html = format_rounds_html(rounds)
        assert "Round 1" in html
        assert "Lead did things" in html
        assert "Looks good" in html
        assert "SUBMIT_FOR_REVIEW" in html
        assert "APPROVE" in html

    def test_escapes_html_in_summary(self):
        rounds = [{
            "round": 1,
            "lead_text": "",
            "reviewer_text": "",
            "lead_summary": "<script>alert('xss')</script>",
            "reviewer_summary": None,
            "lead_action": None,
            "action": None,
        }]
        html = format_rounds_html(rounds)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
