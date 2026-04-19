"""Handoff document reader — parses cycle data for display as character dialogue.

Reads structured handoff cycle documents from docs/handoffs/ and
extracts round content. Supports both JSONL (new) and markdown (legacy) formats.

Delegates parsing to the shared tagteam.parser module.
"""

from __future__ import annotations

from pathlib import Path

from tagteam.parser import extract_all_rounds, read_cycle_rounds  # noqa: F401


def find_cycle_doc(phase: str, step_type: str, project_dir: str = ".") -> Path | None:
    """Find the handoff cycle document for a phase and step type.

    Checks for JSONL-backed cycle first (returns rounds.jsonl path),
    then falls back to legacy _cycle.md.
    """
    handoffs = Path(project_dir) / "docs" / "handoffs"

    # JSONL takes precedence
    jsonl_path = handoffs / f"{phase}_{step_type}_rounds.jsonl"
    if jsonl_path.exists():
        return jsonl_path

    # Legacy markdown
    md_path = handoffs / f"{phase}_{step_type}_cycle.md"
    if md_path.exists():
        return md_path

    return None


def extract_last_round(cycle_path: Path, phase: str = "",
                       step_type: str = "", project_dir: str = ".") -> dict | None:
    """Extract the last round's content from a handoff cycle.

    Supports both JSONL and legacy markdown formats.

    Returns:
        Dict with keys: round, lead_text, reviewer_text, lead_summary,
        reviewer_summary, action, lead_action
        Or None if parsing fails.
    """
    # If it's a JSONL file, use the dispatcher
    if cycle_path.suffix == ".jsonl":
        # Extract phase/type from filename if not provided
        if not phase or not step_type:
            # Filename format: {phase}_{type}_rounds.jsonl
            name = cycle_path.stem  # e.g. "my-phase_plan_rounds"
            if name.endswith("_rounds"):
                name = name[:-len("_rounds")]
            parts = name.rsplit("_", 1)
            if len(parts) == 2:
                phase, step_type = parts[0], parts[1]
        if phase and step_type:
            rounds = read_cycle_rounds(phase, step_type, project_dir or str(cycle_path.parent.parent.parent))
            if rounds:
                return rounds[-1]
        return None

    # Legacy markdown
    rounds = extract_all_rounds(cycle_path)
    if not rounds:
        return None
    return rounds[-1]
