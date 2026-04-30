"""Shared cycle document parser — extracts structured data from handoff cycles.

Used by both the TUI (handoff_reader.py) and the web dashboard (server.py)
to parse cycle documents into structured round data.

Supports two formats:
  - JSONL + JSON status (new): docs/handoffs/{phase}_{type}_rounds.jsonl
  - Markdown (legacy): docs/handoffs/{phase}_{type}_cycle.md
"""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path


def extract_all_rounds(cycle_path: Path) -> list[dict] | None:
    """Extract all rounds from a handoff cycle document.

    Returns a list of dicts, one per round, each with keys:
        round, lead_text, reviewer_text, lead_summary, reviewer_summary,
        action (reviewer), lead_action
    Returns None if parsing fails.
    """
    try:
        content = cycle_path.read_text(encoding="utf-8")
    except OSError:
        return None

    round_matches = list(re.finditer(r"^## Round (\d+)", content, re.MULTILINE))
    if not round_matches:
        return None

    rounds = []
    for i, match in enumerate(round_matches):
        round_num = int(match.group(1))
        start = match.start()
        end = round_matches[i + 1].start() if i + 1 < len(round_matches) else len(content)
        round_content = content[start:end]

        lead_text = _extract_section(round_content, "### Lead")
        reviewer_text = _extract_section(round_content, "### Reviewer")

        lead_action = None
        if lead_text:
            m = re.search(r"\*\*Action:(?:\*\*)?\s*(\w+)", lead_text)
            if m:
                lead_action = m.group(1)

        action = None
        if reviewer_text:
            m = re.search(r"\*\*Action:(?:\*\*)?\s*(\w+)", reviewer_text)
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


def format_rounds_html(rounds: list[dict]) -> str:
    """Format parsed rounds as HTML for the web dashboard.

    Returns an HTML string with structured round display including
    headers, actions, and content for each round.
    """
    if not rounds:
        return '<p class="no-data">No rounds found.</p>'

    parts = []
    for r in rounds:
        round_num = r["round"]
        parts.append(f'<div class="round-block">')
        parts.append(f'<h3 class="round-header">Round {round_num}</h3>')

        # Lead section
        lead_action = r.get("lead_action") or ""
        lead_summary = escape(r.get("lead_summary") or "No submission yet.")
        parts.append(f'<div class="round-agent lead-section">')
        parts.append(f'<span class="agent-label">Lead</span>')
        if lead_action:
            parts.append(f'<span class="action-badge">{escape(lead_action)}</span>')
        parts.append(f'<p>{lead_summary}</p>')
        parts.append('</div>')

        # Reviewer section
        action = r.get("action") or ""
        reviewer_summary = escape(r.get("reviewer_summary") or "Awaiting response.")
        parts.append(f'<div class="round-agent reviewer-section">')
        parts.append(f'<span class="agent-label">Reviewer</span>')
        if action:
            action_class = "approved" if action == "APPROVE" else "changes" if action == "REQUEST_CHANGES" else ""
            parts.append(f'<span class="action-badge {action_class}">{escape(action)}</span>')
        parts.append(f'<p>{reviewer_summary}</p>')
        parts.append('</div>')

        parts.append('</div>')

    return "\n".join(parts)


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
        if stripped.startswith("**Action:"):
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


def _content_summary(content: str) -> str | None:
    """Extract a summary from JSONL round content (first substantive line)."""
    if not content:
        return None
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("**"):
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("-"):
            continue
        return stripped
    # Fall back to first non-empty line
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def parse_jsonl_rounds(jsonl_path: Path) -> list[dict] | None:
    """Parse JSONL round entries into the same structure as extract_all_rounds().

    Returns a list of dicts with keys: round, lead_text, reviewer_text,
    lead_summary, reviewer_summary, action, lead_action
    """
    if not jsonl_path.exists():
        return None

    entries = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not entries:
        return None

    # Group by round number
    by_round: dict[int, dict] = {}
    for e in entries:
        r = e.get("round", 0)
        if r not in by_round:
            by_round[r] = {"lead": None, "reviewer": None}
        by_round[r][e.get("role", "lead")] = e

    rounds = []
    for round_num in sorted(by_round.keys()):
        pair = by_round[round_num]
        lead_entry = pair.get("lead")
        reviewer_entry = pair.get("reviewer")

        lead_text = lead_entry["content"] if lead_entry else None
        reviewer_text = reviewer_entry["content"] if reviewer_entry else None
        lead_action = lead_entry["action"] if lead_entry else None
        action = reviewer_entry["action"] if reviewer_entry else None

        rounds.append({
            "round": round_num,
            "lead_text": lead_text,
            "reviewer_text": reviewer_text,
            "lead_summary": _content_summary(lead_text) if lead_text else None,
            "reviewer_summary": _content_summary(reviewer_text) if reviewer_text else None,
            "lead_action": lead_action,
            "action": action,
        })

    return rounds if rounds else None


def read_cycle_rounds(phase: str, cycle_type: str,
                      project_dir: str = ".") -> list[dict] | None:
    """Dispatcher: reads rounds from JSONL first, falls back to markdown.

    Single entry point for all round reading. Returns the same structure
    regardless of underlying format.

    When `project_dir == "."`, the project root is resolved via
    `tagteam.state._resolve_project_root()` so the read path matches the
    write path (init/add) and works from subdirectories. Explicit paths
    are honored verbatim.
    """
    if project_dir == ".":
        from tagteam.state import _resolve_project_root
        project_dir = _resolve_project_root()
    handoffs = Path(project_dir) / "docs" / "handoffs"

    # Check JSONL first
    jsonl_path = handoffs / f"{phase}_{cycle_type}_rounds.jsonl"
    if jsonl_path.exists():
        return parse_jsonl_rounds(jsonl_path)

    # Fall back to legacy markdown
    md_path = handoffs / f"{phase}_{cycle_type}_cycle.md"
    if md_path.exists():
        return extract_all_rounds(md_path)

    return None
