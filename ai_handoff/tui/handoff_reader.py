"""Handoff document reader — parses cycle markdown to extract dialogue.

Reads structured handoff cycle documents from docs/handoffs/ and
extracts round content for display as character dialogue.
"""

from __future__ import annotations

import re
from pathlib import Path


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


def extract_all_rounds(cycle_path: Path) -> list[dict] | None:
    """Extract all rounds from a handoff cycle document.

    Returns a list of dicts, one per round, each with keys:
        round, lead_text, reviewer_text, lead_summary, reviewer_summary,
        action (reviewer), lead_action
    Returns None if parsing fails.
    """
    try:
        content = cycle_path.read_text()
    except OSError:
        return None

    round_matches = list(re.finditer(r"^## Round (\d+)", content, re.MULTILINE))
    if not round_matches:
        return None

    rounds = []
    for i, match in enumerate(round_matches):
        round_num = int(match.group(1))
        # Slice from this round heading to the next (or end of content)
        start = match.start()
        end = round_matches[i + 1].start() if i + 1 < len(round_matches) else len(content)
        round_content = content[start:end]

        lead_text = _extract_section(round_content, "### Lead")
        reviewer_text = _extract_section(round_content, "### Reviewer")

        # Extract actions
        lead_action = None
        if lead_text:
            m = re.search(r"\*\*Action:\*\*\s*(\w+)", lead_text)
            if m:
                lead_action = m.group(1)

        action = None
        if reviewer_text:
            m = re.search(r"\*\*Action:\*\*\s*(\w+)", reviewer_text)
            if m:
                action = m.group(1)

        lead_summary = _extract_summary(lead_text) if lead_text else None
        reviewer_summary = _extract_summary(reviewer_text) if reviewer_text else None

        rounds.append({
            "round": round_num,
            "lead_text": lead_text,
            "reviewer_text": reviewer_text,
            "lead_summary": lead_summary,
            "reviewer_summary": reviewer_summary,
            "lead_action": lead_action,
            "action": action,
        })

    return rounds


def _extract_section(content: str, heading: str) -> str | None:
    """Extract text between a heading and the next heading or end."""
    pattern = re.escape(heading) + r"\s*\n(.*?)(?=\n### |\n## |\n---|\n<!-- CYCLE_STATUS|$)"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def _extract_summary(text: str) -> str | None:
    """Extract the first substantive line after the Action declaration."""
    lines = text.split("\n")
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("**Action:**"):
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("**Non-blocking"):
            continue
        if stripped.startswith("**Blocking"):
            continue
        if stripped == "_awaiting response_":
            continue
        return stripped
    return None
