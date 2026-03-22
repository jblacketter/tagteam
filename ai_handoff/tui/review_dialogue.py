"""Rich state dialogue — builds dialogue sequences from handoff state + cycle docs.

Replaces the template one-liners from Phase 3 with actual content extracted
from cycle documents, chunked into adventure-game-style dialogue lines.
Falls back to templates when parsing fails.
"""

from __future__ import annotations

import random
import re

from ai_handoff.tui.conversations.transitions import (
    ABORTED,
    ESCALATION_MAYOR,
    ESCALATION_RABBIT,
    MAYOR_APPROVED,
    MAYOR_HANDOFF,
    MAYOR_WORKING,
    RABBIT_APPROVE,
    RABBIT_FEEDBACK,
    RABBIT_WORKING,
)
from ai_handoff.tui.handoff_reader import extract_last_round, find_cycle_doc
from ai_handoff.tui.state_watcher import HandoffState

# Max characters per dialogue chunk
_CHUNK_SIZE = 140
# Max chunks per speaker per transition
_MAX_CHUNKS = 3


def strip_markdown(text: str) -> str:
    """Strip common markdown formatting for clean dialogue text."""
    # Remove heading prefixes
    text = re.sub(r"^#{1,4}\s+", "", text, flags=re.MULTILINE)
    # Remove bold/italic markers
    text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
    text = re.sub(r"(?<!\w)_{1,2}([^_]+)_{1,2}(?!\w)", r"\1", text)
    # Remove bullet prefixes
    text = re.sub(r"^[\s]*[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\s]*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Remove inline code backticks
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Collapse whitespace
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _chunk_text(text: str, max_len: int = _CHUNK_SIZE, max_chunks: int = _MAX_CHUNKS) -> list[str]:
    """Split text into dialogue-sized chunks at sentence boundaries."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining and len(chunks) < max_chunks:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        # Find a sentence boundary near max_len
        cut = max_len
        # Look for period, question mark, or exclamation near the cut point
        for sep in [". ", "? ", "! "]:
            idx = remaining.rfind(sep, 0, max_len + 20)
            if idx > max_len // 2:
                cut = idx + len(sep)
                break
        else:
            # Fall back to space near max_len
            idx = remaining.rfind(" ", 0, max_len + 10)
            if idx > max_len // 2:
                cut = idx + 1

        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()

    # If there's leftover and we hit max_chunks, append with ellipsis
    if remaining and len(chunks) >= max_chunks:
        chunks[-1] = chunks[-1].rstrip(".") + "..."

    return chunks


def build_state_dialogue(
    state: HandoffState,
    previous: HandoffState | None,
    project_dir: str | None = None,
) -> list[tuple[str, str]]:
    """Generate a sequence of (speaker, text) lines for a state transition.

    Reads the cycle document for rich content. Falls back to template
    lines if parsing fails.
    """
    if state.status == "ready" and state.turn == "reviewer":
        return _build_handoff_dialogue(state, project_dir)

    if state.status == "ready" and state.turn == "lead":
        return _build_feedback_dialogue(state, project_dir)

    if state.status == "done" and state.result == "approved":
        return [
            ("rabbit", random.choice(RABBIT_APPROVE)),
            ("mayor", random.choice(MAYOR_APPROVED)),
        ]

    if state.status == "done" and previous and previous.status != "done":
        return [("mayor", random.choice(MAYOR_APPROVED))]

    if state.status == "escalated":
        return _build_escalation_dialogue(state, project_dir)

    if state.status == "working":
        if previous and previous.turn != state.turn:
            if state.turn == "lead":
                return [("mayor", random.choice(MAYOR_WORKING))]
            return [("rabbit", random.choice(RABBIT_WORKING))]
        return []

    if state.status == "aborted":
        template = random.choice(ABORTED)
        return [("mayor", template.format(reason=state.reason or "No reason given."))]

    return []


def _build_handoff_dialogue(state: HandoffState, project_dir: str | None = None) -> list[tuple[str, str]]:
    """Mayor hands off to reviewer — with lead submission summary."""
    intro = random.choice(MAYOR_HANDOFF)
    lines: list[tuple[str, str]] = [("mayor", intro)]

    round_data = _get_round_data(state, project_dir)
    if round_data and round_data.get("lead_summary"):
        summary = strip_markdown(round_data["lead_summary"])
        for chunk in _chunk_text(summary):
            lines.append(("mayor", chunk))

    return lines


def _build_feedback_dialogue(state: HandoffState, project_dir: str | None = None) -> list[tuple[str, str]]:
    """Reviewer returns feedback — with actual feedback content."""
    round_data = _get_round_data(state, project_dir)

    if round_data and round_data.get("reviewer_summary"):
        summary = strip_markdown(round_data["reviewer_summary"])
        chunks = _chunk_text(summary)
        template = random.choice(RABBIT_FEEDBACK)
        # First chunk gets the template framing
        first = template.format(feedback=chunks[0])
        lines: list[tuple[str, str]] = [("rabbit", first)]
        for chunk in chunks[1:]:
            lines.append(("rabbit", chunk))
        return lines

    # Fallback to template
    template = random.choice(RABBIT_FEEDBACK)
    return [("rabbit", template.format(feedback="Take a look."))]


def _build_escalation_dialogue(state: HandoffState, project_dir: str | None = None) -> list[tuple[str, str]]:
    """Escalation — character addresses player with disagreement content."""
    lines: list[tuple[str, str]] = []

    if state.turn == "lead":
        lines.append(("mayor", random.choice(ESCALATION_MAYOR)))
    else:
        lines.append(("rabbit", random.choice(ESCALATION_RABBIT)))

    # Try to extract the disagreement content
    round_data = _get_round_data(state, project_dir)
    if round_data and round_data.get("reviewer_summary"):
        summary = strip_markdown(round_data["reviewer_summary"])
        for chunk in _chunk_text(summary, max_chunks=2):
            lines.append(("rabbit", chunk))

    return lines


def _get_round_data(state: HandoffState, project_dir: str | None = None) -> dict | None:
    """Try to get the last round's data from the cycle document."""
    if not state.phase or not state.step_type:
        return None
    cycle_path = find_cycle_doc(state.phase, state.step_type, project_dir=project_dir or ".")
    if cycle_path is None:
        return None
    pdir = project_dir or "."
    return extract_last_round(cycle_path, phase=state.phase,
                              step_type=state.step_type, project_dir=pdir)
