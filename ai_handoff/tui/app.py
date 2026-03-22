"""Main Textual Application for the Handoff Saloon.

Assembles the scene and dialogue panel, runs conversations,
and polls handoff-state.json to drive the cuckoo clock, status bar,
and state-transition dialogue.
"""

from __future__ import annotations

import random
from collections import deque
from datetime import date
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding

from ai_handoff.tui.characters import MAYOR_CHARACTER, RABBIT_CHARACTER
from ai_handoff.tui.conversation import ConversationEngine
from ai_handoff.tui.conversations.intro import INTRO, SETUP_INTRO
from ai_handoff.tui.conversations.transitions import (
    ESCALATION_CHOICE_DEFER,
    ESCALATION_CHOICE_MAYOR,
    ESCALATION_CHOICE_RABBIT,
    ESCALATION_CHOICES,
)
from ai_handoff.tui.dialogue import DialoguePanel
from ai_handoff.tui.handoff_reader import extract_last_round, find_cycle_doc
from ai_handoff.tui.map_data import find_docs_path, read_phases
from ai_handoff.tui.map_widget import MapWidget
from ai_handoff.tui.review_dialogue import build_state_dialogue
from ai_handoff.tui.review_replay import build_review_replay
from ai_handoff.tui.scene import SceneWidget
from ai_handoff.tui import sound
from ai_handoff.tui.state_watcher import (
    HandoffState,
    StateChanged,
    find_state_path,
    read_handoff_state,
    state_has_changed,
)
from ai_handoff.tui.status_bar import StatusBar

SPEAKERS = {
    "mayor": MAYOR_CHARACTER,
    "rabbit": RABBIT_CHARACTER,
}

STATE_POLL_INTERVAL = 5.0

# Pigeon flight endpoints (column positions of character sprites)
_MAYOR_COL = 3
_RABBIT_COL = 54


class SaloonApp(App):
    """The Handoff Saloon TUI application."""

    TITLE = "The Handoff Saloon"

    CSS = """
    Screen {
        background: #1a1207;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("m", "toggle_map", "Map", show=True),
        Binding("r", "replay_review", "Review", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("space", "skip_or_advance", "Continue", show=False),
        Binding("enter", "skip_or_advance", "Continue", show=False),
    ]

    def __init__(self, project_dir: str | None = None) -> None:
        super().__init__()
        self.project_dir = project_dir
        self._engine: ConversationEngine | None = None
        self._replay_engine: ConversationEngine | None = None
        self._awaiting_advance = False
        self._last_state: HandoffState | None = None
        self._state_path: Path | None = None
        self._intro_complete = False
        self._dialogue_queue: deque[tuple[str, str]] = deque()
        self._escalation_pending = False
        self._poll_failures = 0

    def compose(self) -> ComposeResult:
        yield SceneWidget()
        yield MapWidget(id="map-overlay")
        yield StatusBar(id="status-bar")
        yield DialoguePanel()

    def on_mount(self) -> None:
        """Start the intro conversation and begin state polling."""
        self._state_path = find_state_path(self.project_dir)

        # Choose intro script based on whether we have a project dir
        if self.project_dir is None:
            script = SETUP_INTRO
            complete_cb = self._on_setup_complete
        else:
            script = INTRO
            complete_cb = self._on_conversation_complete

        # Start intro conversation
        self._engine = ConversationEngine(
            script=script,
            on_show_dialogue=self._on_show_dialogue,
            on_show_choices=self._on_show_choices,
            on_show_input=self._on_show_input,
            on_complete=complete_cb,
        )
        self.set_timer(0.5, self._engine.start)

        # Initial map data load
        self._refresh_map()

        # Start state polling
        self._poll_state()
        self.set_interval(STATE_POLL_INTERVAL, self._poll_state)

    # --- State polling ---

    def _poll_state(self) -> None:
        """Read handoff-state.json and post StateChanged if different."""
        current = read_handoff_state(self._state_path)
        if current is None and self._last_state is not None:
            self._poll_failures += 1
            if self._poll_failures >= 3:
                self.log.warning(f"State file read failed {self._poll_failures} consecutive times")
                status_bar = self.query_one("#status-bar", StatusBar)
                status_bar.set_stale(True)
                status_bar.update_state(self._last_state)
            return
        if self._poll_failures > 0:
            self._poll_failures = 0
            status_bar = self.query_one("#status-bar", StatusBar)
            status_bar.set_stale(False)
            # Force re-render to clear [STALE] badge even if state is unchanged
            status_bar.update_state(current or self._last_state)
        if state_has_changed(current, self._last_state):
            previous = self._last_state
            self._last_state = current
            self.post_message(StateChanged(current, previous))

    def on_state_changed(self, event: StateChanged) -> None:
        """Handle handoff state changes — update scene, status bar, map, trigger dialogue."""
        state = event.state
        previous = event.previous

        # Update scene (clock) — also plays tick sound
        scene = self.query_one(SceneWidget)
        scene.update_state(state)
        sound.play("tick")

        # Update status bar with last action
        last_action = self._get_last_action(state)
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.update_state(state, last_action=last_action)

        # Refresh map data (phase statuses may have changed)
        self._refresh_map()

        # Sound and animation triggers based on state
        if state and not state.is_empty:
            # Cuckoo animation for meaningful state changes
            if state.status in ("ready", "done", "escalated", "aborted"):
                scene.animate_cuckoo(state.status)
                sound.play("chime")

            # Pigeon animation when turn changes
            if previous and previous.turn and previous.turn != state.turn:
                sound.play("coo")
                if state.turn == "reviewer":
                    scene.fly_pigeon(_MAYOR_COL, _RABBIT_COL)
                elif state.turn == "lead":
                    scene.fly_pigeon(_RABBIT_COL, _MAYOR_COL)

            # Bell for escalation
            if state.status == "escalated":
                sound.play("bell")

            # Stamp for approval/completion
            if state.status == "done":
                sound.play("stamp")

            # Trigger state-driven dialogue
            self._trigger_state_dialogue(state, previous)

    def _get_last_action(self, state: HandoffState | None) -> str | None:
        """Get the last reviewer action from the cycle document."""
        if not state or not state.phase or not state.step_type:
            return None
        cycle_path = find_cycle_doc(state.phase, state.step_type, project_dir=self.project_dir or ".")
        if cycle_path is None:
            return None
        pdir = self.project_dir or "."
        result = extract_last_round(cycle_path, phase=state.phase,
                                    step_type=state.step_type, project_dir=pdir)
        if result:
            return result.get("action")
        return None

    def _refresh_map(self) -> None:
        """Re-read phase data and update the map widget and scene table indicator."""
        phases = read_phases(self.project_dir)
        self.query_one("#map-overlay", MapWidget).update_phases(phases)
        self.query_one(SceneWidget).update_phases(phases)

    def action_toggle_map(self) -> None:
        """Toggle the map overlay on/off."""
        self.query_one("#map-overlay", MapWidget).toggle()

    # --- Rich state dialogue ---

    def _trigger_state_dialogue(
        self, state: HandoffState, previous: HandoffState | None
    ) -> None:
        """Generate dialogue from a state transition using rich content extraction."""
        lines = build_state_dialogue(state, previous, project_dir=self.project_dir)
        if not lines:
            return

        # Check if this is an escalation — queue the dialogue then show choices
        is_escalation = state.status == "escalated"

        if not self._intro_complete:
            # Pre-intro: queue all lines (coalesce — replace previous queue)
            self._dialogue_queue.clear()
            self._dialogue_queue.extend(lines)
            self._escalation_pending = is_escalation
        elif self._replay_engine:
            # During replay: buffer state dialogue for later
            self._dialogue_queue.extend(lines)
            self._escalation_pending = is_escalation
        else:
            # Normal: show first line, queue the rest
            self._escalation_pending = is_escalation
            first_speaker, first_text = lines[0]
            for line in lines[1:]:
                self._dialogue_queue.append(line)
            self._show_state_dialogue(first_speaker, first_text)

    def _show_state_dialogue(self, speaker: str, text: str) -> None:
        """Show a single line of state-driven dialogue."""
        char = SPEAKERS.get(speaker)
        if char is None:
            return
        self._awaiting_advance = False
        panel = self.query_one(DialoguePanel)
        panel.show_dialogue(
            name=char.name,
            portrait=char.portrait,
            text=text,
            color=char.color,
        )

    def _drain_queue(self) -> None:
        """Show the next queued dialogue line, or show escalation choices."""
        if self._dialogue_queue:
            speaker, text = self._dialogue_queue.popleft()
            self._show_state_dialogue(speaker, text)
        elif self._escalation_pending:
            self._escalation_pending = False
            self._show_escalation_choices()

    def _show_escalation_choices(self) -> None:
        """Present the player with escalation choices."""
        self._awaiting_advance = False
        panel = self.query_one(DialoguePanel)
        panel.show_choices(ESCALATION_CHOICES)

    # --- Review replay ---

    def action_replay_review(self) -> None:
        """Toggle the review replay on/off."""
        if self._replay_engine:
            # Dismiss replay
            self._replay_engine = None
            self._drain_queue()
            return

        state = self._last_state
        if not state or not state.phase or not state.step_type:
            self._show_state_dialogue("mayor", "Nothing to review yet.")
            return

        script = build_review_replay(state.phase, state.step_type, project_dir=self.project_dir)
        if not script:
            self._show_state_dialogue("mayor", "I don't see a review cycle for this phase.")
            return

        self._replay_engine = ConversationEngine(
            script=script,
            on_show_dialogue=self._on_show_dialogue,
            on_show_choices=self._on_show_choices,
            on_show_input=self._on_show_input,
            on_complete=self._on_replay_complete,
        )
        self._replay_engine.start()

    def _on_replay_complete(self) -> None:
        """Called when the review replay finishes."""
        self._replay_engine = None
        self._awaiting_advance = False
        # Drain any state dialogue that accumulated during replay
        self._drain_queue()

    # --- Escalation choice handling ---

    def _handle_escalation_choice(self, index: int) -> None:
        """Process the player's escalation choice."""
        if index == 0:
            # Side with Mayor
            text = random.choice(ESCALATION_CHOICE_MAYOR)
            self._dialogue_queue.append(("mayor", text))
            choice_label = ESCALATION_CHOICES[0]
        elif index == 1:
            # Side with Rabbit
            text = random.choice(ESCALATION_CHOICE_RABBIT)
            self._dialogue_queue.append(("rabbit", text))
            choice_label = ESCALATION_CHOICES[1]
        else:
            # Defer
            text = random.choice(ESCALATION_CHOICE_DEFER)
            self._dialogue_queue.append(("mayor", text))
            choice_label = ESCALATION_CHOICES[2]

        self._drain_queue()
        self._log_escalation_decision(choice_label)

    def _log_escalation_decision(self, choice: str) -> None:
        """Append the player's escalation choice to docs/decision_log.md (idempotent)."""
        state = self._last_state
        if not state:
            return

        docs = find_docs_path(self.project_dir)
        log_path = docs / "decision_log.md"

        # Build unique key
        key = f"{state.phase}_{state.step_type}_round{state.round}"

        # Check for existing entry
        if log_path.exists():
            existing = log_path.read_text(encoding="utf-8")
            if key in existing:
                return  # Already logged
        else:
            # Create with header
            log_path.write_text("# Decision Log\n\n<!-- Add new decisions at the top -->\n\n", encoding="utf-8")

        today = date.today().isoformat()
        entry = (
            f"\n---\n"
            f"## {today}: Escalation — {state.phase} {state.step_type} round {state.round}\n\n"
            f"**Decision:** Player chose: \"{choice}\"\n\n"
            f"**Context:** Review cycle escalated after {state.round} rounds. "
            f"Lead and reviewer could not reach agreement.\n\n"
            f"**Alternatives Considered:**\n"
            f"- Side with Mayor (lead): Accept the current approach\n"
            f"- Side with Rabbit (reviewer): Make the requested changes\n"
            f"- Defer: Player needs more time to decide\n\n"
            f"**Rationale:** Player input via Handoff Saloon TUI\n\n"
            f"**Decided By:** Human\n\n"
            f"**Phase:** {state.phase} ({state.step_type}) — key: {key}\n\n"
            f"**Follow-ups:**\n"
            f"- Agents should read this decision and resume the cycle accordingly\n"
        )

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)

    # --- ConversationEngine callbacks ---

    def _on_show_dialogue(self, speaker: str, text: str) -> None:
        """Show character dialogue with typing effect."""
        char = SPEAKERS.get(speaker)
        if char is None:
            return
        self._awaiting_advance = False
        panel = self.query_one(DialoguePanel)
        panel.show_dialogue(
            name=char.name,
            portrait=char.portrait,
            text=text,
            color=char.color,
        )

    def _on_show_choices(self, choices: list[str]) -> None:
        """Show dialogue choices for the player."""
        self._awaiting_advance = False
        panel = self.query_one(DialoguePanel)
        panel.show_choices(choices)

    def _on_show_input(self, prompt: str) -> None:
        """Show free-text input for the player."""
        self._awaiting_advance = False
        panel = self.query_one(DialoguePanel)
        panel.show_input(prompt)

    def _on_setup_complete(self) -> None:
        """Called when the first-time setup conversation finishes.

        Reads inputs from the setup dialogue, creates the project directory,
        runs scaffolding, writes config, then restarts with the project dir.
        """
        import os
        inputs = self._engine.inputs if self._engine else {}

        # Resolve project directory
        dir_input = (inputs.get("setup_dir_input") or "").strip()
        name_input = (inputs.get("setup_name_input") or "").strip() or "my-project"

        if dir_input:
            project_dir = os.path.abspath(os.path.expanduser(dir_input))
        else:
            project_dir = os.path.abspath(os.path.join("projects", name_input))

        # Create directory if needed
        os.makedirs(project_dir, exist_ok=True)

        config_path = Path(project_dir) / "ai-handoff.yaml"

        # Only run setup scaffolding if this is a new/unconfigured project
        if not config_path.exists():
            from ai_handoff.setup import main as setup_main
            setup_main(project_dir)

            # Write config using non-interactive helper
            from ai_handoff.cli import write_config
            lead = (inputs.get("setup_lead_input") or "").strip() or "claude"
            reviewer = (inputs.get("setup_reviewer_input") or "").strip() or "codex"
            write_config(project_dir, lead, reviewer)

        # Update app state to point at the new project
        self.project_dir = project_dir
        self._state_path = find_state_path(self.project_dir)

        # Now run the normal intro
        self._engine = ConversationEngine(
            script=INTRO,
            on_show_dialogue=self._on_show_dialogue,
            on_show_choices=self._on_show_choices,
            on_show_input=self._on_show_input,
            on_complete=self._on_conversation_complete,
        )
        self._engine.start()

        # Refresh map and state with new project dir
        self._refresh_map()
        self._poll_state()

    def _on_conversation_complete(self) -> None:
        """Called when the intro conversation reaches the end."""
        self._intro_complete = True
        self._engine = None
        self._awaiting_advance = False

        # Drain queued state dialogue
        self._drain_queue()

    # --- Message handlers ---

    def on_dialogue_panel_typing_complete(
        self, event: DialoguePanel.TypingComplete
    ) -> None:
        """Typing effect finished — wait for player to press Enter/Space."""
        self._awaiting_advance = True

    def on_dialogue_panel_choice_selected(
        self, event: DialoguePanel.ChoiceSelected
    ) -> None:
        """Player selected a dialogue choice."""
        # Check if this is an escalation choice (shown after escalation dialogue)
        if (
            self._intro_complete
            and not self._engine
            and not self._replay_engine
            and event.label in ESCALATION_CHOICES
        ):
            self._handle_escalation_choice(event.index)
            return

        # Otherwise, route to active engine
        engine = self._replay_engine or self._engine
        if engine:
            engine.handle_choice(event.index)

    def on_dialogue_panel_player_submit(
        self, event: DialoguePanel.PlayerSubmit
    ) -> None:
        """Player submitted free-text input."""
        engine = self._replay_engine or self._engine
        if engine:
            engine.handle_input(event.text)

    def action_skip_or_advance(self) -> None:
        """Handle Enter/Space — skip typing effect or advance to next node."""
        panel = self.query_one(DialoguePanel)
        if panel.skip_typing():
            return
        if self._awaiting_advance:
            self._awaiting_advance = False
            # Active conversation engine takes priority
            if self._replay_engine:
                self._replay_engine.advance()
            elif self._engine and not self._intro_complete:
                self._engine.advance()
            else:
                self._drain_queue()
