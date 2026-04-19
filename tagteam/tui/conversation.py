"""Conversation engine — drives scripted dialogue flows.

A conversation is a list of node dicts. The engine steps through them,
coordinating with the dialogue panel to show speech, choices, or input.
"""

from __future__ import annotations

from typing import Any, Callable


class ConversationEngine:
    """Walks through a conversation script, driving a dialogue panel."""

    def __init__(
        self,
        script: list[dict[str, Any]],
        on_show_dialogue: Callable,
        on_show_choices: Callable,
        on_show_input: Callable,
        on_complete: Callable | None = None,
    ) -> None:
        self._nodes: dict[str, dict] = {node["id"]: node for node in script}
        self._current_id: str | None = script[0]["id"] if script else None
        self._on_show_dialogue = on_show_dialogue
        self._on_show_choices = on_show_choices
        self._on_show_input = on_show_input
        self._on_complete = on_complete
        self.inputs: dict[str, str] = {}

    @property
    def current_node(self) -> dict | None:
        if self._current_id is None:
            return None
        return self._nodes.get(self._current_id)

    def start(self) -> None:
        """Begin the conversation from the first node."""
        self._process_current()

    def advance(self, next_id: str | None = None) -> None:
        """Move to the next node. For dialogue nodes, next_id is read from
        the node. For choice/input nodes, the caller provides next_id."""
        node = self.current_node
        if node is None:
            return

        target = next_id if next_id is not None else node.get("next")

        if target is None:
            self._current_id = None
            if self._on_complete:
                self._on_complete()
            return

        self._current_id = target
        self._process_current()

    def handle_choice(self, choice_index: int) -> None:
        """Player selected a choice option."""
        node = self.current_node
        if node is None or node["type"] != "choice":
            return
        choices = node["choices"]
        if 0 <= choice_index < len(choices):
            self.advance(choices[choice_index]["next"])

    def handle_input(self, text: str) -> None:
        """Player submitted free-text input."""
        node = self.current_node
        if node is None or node["type"] != "input":
            return
        self.inputs[node["id"]] = text
        self.advance(node.get("next"))

    def _process_current(self) -> None:
        """Process the current node by calling the appropriate callback."""
        node = self.current_node
        if node is None:
            if self._on_complete:
                self._on_complete()
            return

        node_type = node["type"]

        if node_type == "dialogue":
            self._on_show_dialogue(
                speaker=node["speaker"],
                text=node["text"],
            )
        elif node_type == "choice":
            labels = [c["label"] for c in node["choices"]]
            self._on_show_choices(choices=labels)
        elif node_type == "input":
            prompt = node.get("prompt", "Type your response...")
            self._on_show_input(prompt=prompt)
