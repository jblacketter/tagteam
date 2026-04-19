"""Tests for the migrate module."""

import pytest
from pathlib import Path

from tagteam.migrate import detect_agent_names


class TestDetectAgentNames:
    """Tests for detect_agent_names function."""

    def test_returns_defaults_when_no_docs(self, tmp_path):
        lead, reviewer = detect_agent_names(tmp_path)
        assert lead == "Claude"
        assert reviewer == "Codex"

    def test_returns_defaults_when_empty_handoffs(self, tmp_path):
        (tmp_path / "docs" / "handoffs").mkdir(parents=True)
        lead, reviewer = detect_agent_names(tmp_path)
        assert lead == "Claude"
        assert reviewer == "Codex"

    def test_detects_lead_name(self, tmp_path):
        handoffs = tmp_path / "docs" / "handoffs"
        handoffs.mkdir(parents=True)
        (handoffs / "test.md").write_text("**From:** Alice (Lead)\n**To:** Bob (Reviewer)")

        lead, reviewer = detect_agent_names(tmp_path)
        assert lead == "Alice"
        assert reviewer == "Bob"

    def test_detects_names_with_spaces(self, tmp_path):
        handoffs = tmp_path / "docs" / "handoffs"
        handoffs.mkdir(parents=True)
        (handoffs / "test.md").write_text("**From:** Claude 3 Opus (Lead)\n**To:** GPT-4 Turbo (Reviewer)")

        lead, reviewer = detect_agent_names(tmp_path)
        assert lead == "Claude 3 Opus"
        assert reviewer == "GPT-4 Turbo"

    def test_detects_names_with_punctuation(self, tmp_path):
        handoffs = tmp_path / "docs" / "handoffs"
        handoffs.mkdir(parents=True)
        (handoffs / "test.md").write_text("**From:** O'Brien-Smith (Lead)\n**To:** Dr. Watson (Reviewer)")

        lead, reviewer = detect_agent_names(tmp_path)
        assert lead == "O'Brien-Smith"
        assert reviewer == "Dr. Watson"

    def test_partial_detection_returns_default_for_missing(self, tmp_path):
        handoffs = tmp_path / "docs" / "handoffs"
        handoffs.mkdir(parents=True)
        (handoffs / "test.md").write_text("**From:** Alice (Lead)\nSome other content")

        lead, reviewer = detect_agent_names(tmp_path)
        assert lead == "Alice"
        assert reviewer == "Codex"  # default

    def test_scans_multiple_files(self, tmp_path):
        handoffs = tmp_path / "docs" / "handoffs"
        handoffs.mkdir(parents=True)
        (handoffs / "file1.md").write_text("**From:** Alice (Lead)")
        (handoffs / "file2.md").write_text("**To:** Bob (Reviewer)")

        lead, reviewer = detect_agent_names(tmp_path)
        # Should find both across files
        assert lead == "Alice"
        assert reviewer == "Bob"

    def test_stops_after_finding_both(self, tmp_path):
        handoffs = tmp_path / "docs" / "handoffs"
        handoffs.mkdir(parents=True)
        # First file has both
        (handoffs / "aaa.md").write_text("**From:** Alice (Lead)\n**To:** Bob (Reviewer)")
        # Second file has different names (should be ignored)
        (handoffs / "zzz.md").write_text("**From:** Charlie (Lead)\n**To:** Dave (Reviewer)")

        lead, reviewer = detect_agent_names(tmp_path)
        assert lead == "Alice"
        assert reviewer == "Bob"
