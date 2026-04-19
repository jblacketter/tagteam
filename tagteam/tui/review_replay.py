"""Review replay — builds a ConversationEngine script from cycle documents.

Generates a dialogue script where each round becomes a Mayor/Rabbit
exchange, suitable for playback via the existing ConversationEngine.
"""

from __future__ import annotations

from tagteam.parser import read_cycle_rounds
from tagteam.tui.handoff_reader import find_cycle_doc
from tagteam.tui.review_dialogue import strip_markdown, _chunk_text


def build_review_replay(phase: str, step_type: str, project_dir: str | None = None) -> list[dict] | None:
    """Build a ConversationEngine-compatible script from the cycle document.

    Each round becomes:
    1. Mayor dialogue: lead summary (possibly chunked)
    2. Rabbit dialogue: reviewer summary or "(awaiting response)"

    Returns None if no cycle doc exists or parsing fails.
    """
    pdir = project_dir or "."
    cycle_path = find_cycle_doc(phase, step_type, project_dir=pdir)
    if cycle_path is None:
        return None

    rounds = read_cycle_rounds(phase, step_type, pdir)
    if not rounds:
        return None

    nodes: list[dict] = []
    node_id = 0

    def _add_node(speaker: str, text: str, next_id: int | None) -> None:
        nonlocal node_id
        nodes.append({
            "id": f"replay_{node_id}",
            "type": "dialogue",
            "speaker": speaker,
            "text": text,
            "next": f"replay_{next_id}" if next_id is not None else None,
        })
        node_id += 1

    # Opening line
    _add_node("mayor", "Let me walk you through our review so far.", node_id + 1)

    for i, rd in enumerate(rounds):
        is_last = i == len(rounds) - 1
        round_num = rd["round"]

        # Lead's submission
        lead_summary = strip_markdown(rd["lead_summary"]) if rd.get("lead_summary") else None
        lead_action = rd.get("lead_action", "")

        if lead_summary:
            chunks = _chunk_text(lead_summary, max_chunks=2)
            prefix = f"Round {round_num}. "
            first_text = prefix + chunks[0]
            for j, chunk in enumerate(chunks):
                text = first_text if j == 0 else chunk
                # Point to the next chunk, or to the reviewer line
                _add_node("mayor", text, node_id + 1)
        else:
            _add_node("mayor", f"Round {round_num}. I submitted my work for review.", node_id + 1)

        # Reviewer's response
        reviewer_summary = strip_markdown(rd["reviewer_summary"]) if rd.get("reviewer_summary") else None
        action = rd.get("action")

        if reviewer_summary:
            chunks = _chunk_text(reviewer_summary, max_chunks=2)
            for j, chunk in enumerate(chunks):
                next_target = node_id + 1
                _add_node("rabbit", chunk, next_target)
        elif action:
            action_text = {
                "APPROVE": "I approved it.",
                "REQUEST_CHANGES": "I requested changes.",
            }.get(action, f"Action: {action}")
            _add_node("rabbit", action_text, node_id + 1)
        else:
            _add_node("rabbit", "Still waiting on my review.", node_id + 1)

    # Closing line
    _add_node("mayor", "And that brings us to the present.", None)

    return nodes
