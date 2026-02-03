"""Phase data reader for the map widget.

Reads docs/roadmap.md for phase order and docs/phases/*.md for detailed
status (checkbox parsing) and success criteria counts.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# Status checkbox labels in order (lowest to highest)
_STATUS_LABELS = [
    "planning",
    "in review",
    "approved",
    "implementation",
    "implementation review",
    "complete",
]

# Map from highest-checked label to our status string
_LABEL_TO_STATUS = {
    "complete": "complete",
    "implementation review": "impl-review",
    "implementation": "implementation",
    "in review": "in-review",
    "approved": "approved",
    "planning": "planning",
}

# Status to map symbol
STATUS_SYMBOLS = {
    "complete": "\u2713",       # ✓
    "impl-review": "\u25f7",    # ◷
    "implementation": "\u25f7",  # ◷
    "in-review": "\u25f7",      # ◷
    "approved": "\u25cf",       # ●
    "planning": "\u25cf",       # ●
    "not-started": "\u25cb",    # ○
}

# Status to color (matches clock palette)
STATUS_COLORS = {
    "complete": "#7a9e6b",
    "impl-review": "#d4a04a",
    "implementation": "#d4a04a",
    "in-review": "#6a8eae",
    "approved": "#d4a04a",
    "planning": "#8a7a5a",
    "not-started": "#5a5a5a",
}


@dataclass(frozen=True)
class PhaseInfo:
    """Immutable snapshot of a single phase's status."""

    name: str
    status: str  # one of the keys in STATUS_SYMBOLS
    criteria_done: int
    criteria_total: int
    is_current: bool


def find_docs_path(project_dir: str | None = None) -> Path:
    """Resolve the docs/ directory.

    If project_dir is given, uses that directly. Otherwise checks:
    1. HANDOFF_DOCS_PATH environment variable
    2. Current working directory / docs
    """
    if project_dir is not None:
        return Path(project_dir) / "docs"

    env_path = os.environ.get("HANDOFF_DOCS_PATH")
    if env_path:
        p = Path(env_path)
        if p.is_dir():
            return p

    cwd_path = Path.cwd() / "docs"
    return cwd_path


def _parse_phase_names(roadmap_path: Path) -> list[str]:
    """Extract phase names in order from docs/roadmap.md."""
    if not roadmap_path.exists():
        return []

    names: list[str] = []
    for line in roadmap_path.read_text().splitlines():
        # Match "### Phase N: name"
        m = re.match(r"^###\s+Phase\s+\d+:\s+(.+)$", line)
        if m:
            names.append(m.group(1).strip())
    return names


def _parse_phase_status(phase_path: Path) -> str:
    """Determine phase status from the ## Status checkbox list.

    Returns the status string corresponding to the highest checked item.
    """
    if not phase_path.exists():
        return "not-started"

    text = phase_path.read_text()

    # Find the ## Status section
    in_status = False
    highest_checked = ""

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Status"):
            in_status = True
            continue
        if in_status and stripped.startswith("## "):
            break  # next section
        if in_status and stripped.startswith("- [x]"):
            # Extract the label (strip markdown and arrows)
            label = re.sub(r"- \[x\]\s*", "", stripped)
            label = re.sub(r"\s*←.*$", "", label)  # remove "← current" annotations
            label = label.strip().lower()
            if label in _LABEL_TO_STATUS:
                highest_checked = label

    if not highest_checked:
        return "not-started"

    return _LABEL_TO_STATUS[highest_checked]


def _parse_criteria_counts(phase_path: Path) -> tuple[int, int]:
    """Count checked vs total success criteria from ## Success Criteria section."""
    if not phase_path.exists():
        return (0, 0)

    text = phase_path.read_text()

    in_criteria = False
    done = 0
    total = 0

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Success Criteria"):
            in_criteria = True
            continue
        if in_criteria and stripped.startswith("## "):
            break
        if in_criteria:
            if stripped.startswith("- [x]"):
                done += 1
                total += 1
            elif stripped.startswith("- [ ]"):
                total += 1

    return (done, total)


def read_phases(project_dir: str | None = None) -> list[PhaseInfo]:
    """Build the full phase list with status and criteria counts."""
    docs = find_docs_path(project_dir)
    roadmap_path = docs / "roadmap.md"
    names = _parse_phase_names(roadmap_path)

    if not names:
        return []

    phases: list[PhaseInfo] = []
    current_found = False

    for name in names:
        phase_path = docs / "phases" / f"{name}.md"
        status = _parse_phase_status(phase_path)
        done, total = _parse_criteria_counts(phase_path)

        is_current = False
        if not current_found and status not in ("complete", "not-started"):
            is_current = True
            current_found = True

        phases.append(
            PhaseInfo(
                name=name,
                status=status,
                criteria_done=done,
                criteria_total=total,
                is_current=is_current,
            )
        )

    return phases


def compact_indicator(phases: list[PhaseInfo]) -> str:
    """Generate a compact symbol strip for the table indicator.

    Returns a string of status symbols, one per phase, fitting within
    a 10-character budget. Truncates with '…' if needed.
    """
    if not phases:
        return ""

    symbols = [STATUS_SYMBOLS.get(p.status, "\u25cb") for p in phases]

    if len(symbols) > 9:
        return "".join(symbols[:9]) + "\u2026"

    strip = "".join(symbols)
    # Center within 10 chars
    return strip.center(10)
