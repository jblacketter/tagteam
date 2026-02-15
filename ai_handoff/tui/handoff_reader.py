"""Handoff document reader — parses cycle markdown to extract dialogue.

Reads structured handoff cycle documents from docs/handoffs/ and
extracts round content for display as character dialogue.

Delegates parsing to the shared ai_handoff.parser module.
"""

from __future__ import annotations

from pathlib import Path

from ai_handoff.parser import extract_all_rounds  # noqa: F401


def find_cycle_doc(phase: str, step_type: str, project_dir: str = ".") -> Path | None:
    """Find the handoff cycle document for a phase and step type.

    Looks for: docs/handoffs/{phase}_{step_type}_cycle.md
    """
    path = Path(project_dir) / "docs" / "handoffs" / f"{phase}_{step_type}_cycle.md"
    if path.exists():
        return path
    return None


def extract_last_round(cycle_path: Path) -> dict | None:
    """Extract the last round's content from a handoff cycle document.

    Returns:
        Dict with keys: round, lead_text, reviewer_text, lead_summary,
        reviewer_summary, action, lead_action
        Or None if parsing fails.
    """
    rounds = extract_all_rounds(cycle_path)
    if not rounds:
        return None
    return rounds[-1]
