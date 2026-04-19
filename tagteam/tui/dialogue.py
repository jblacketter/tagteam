"""Dialogue panel widget.

Supports three modes:
  - Speech: character portrait + text with typing effect
  - Choice: numbered options the player selects with arrow keys + Enter
  - Input: free-text input field

The panel switches modes based on what the conversation engine requests.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.timer import Timer
from textual.widgets import Input, Static


class DialoguePortrait(Static):
    """Displays the speaking character's ASCII portrait."""

    DEFAULT_CSS = """
    DialoguePortrait {
        width: 14;
        height: auto;
        color: #d4a04a;
        padding: 0 1;
    }
    """


class DialogueSpeech(Static):
    """Displays the character name and their current speech text."""

    DEFAULT_CSS = """
    DialogueSpeech {
        width: 1fr;
        height: auto;
        color: #e8d5a3;
        padding: 0 1;
    }
    """


class ChoiceList(Static, can_focus=True):
    """Displays selectable dialogue choices."""

    DEFAULT_CSS = """
    ChoiceList {
        width: 1fr;
        height: auto;
        color: #e8d5a3;
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("up", "move_up", "Up"),
        ("down", "move_down", "Down"),
        ("enter", "select", "Select"),
    ]

    class Selected(Message):
        """Fired when the player selects a choice."""

        def __init__(self, index: int, label: str) -> None:
            self.index = index
            self.label = label
            super().__init__()

    def __init__(self, choices: list[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self._choices = choices
        self._selected = 0
        self._flash_index: int | None = None

    def on_mount(self) -> None:
        self.focus()

    def render(self) -> Text:
        text = Text()
        for i, choice in enumerate(self._choices):
            if self._flash_index is not None and i == self._flash_index:
                text.append(f"  > {choice}\n", style="bold #ffffff")
            elif i == self._selected:
                text.append(f"  > {choice}\n", style="bold #d4a04a")
            else:
                text.append(f"    {choice}\n", style="#8a7a5a")
        return text

    def action_move_up(self) -> None:
        if self._selected > 0:
            self._selected -= 1
            self.refresh()

    def action_move_down(self) -> None:
        if self._selected < len(self._choices) - 1:
            self._selected += 1
            self.refresh()

    def action_select(self) -> None:
        if self._flash_index is not None:
            return  # flash already pending — ignore duplicate
        self._flash_index = self._selected
        self.refresh()
        self.set_timer(0.2, self._post_selection)

    def _post_selection(self) -> None:
        if self._flash_index is None:
            return  # already posted or widget removed
        idx = self._flash_index
        self._flash_index = None
        self.post_message(self.Selected(idx, self._choices[idx]))


class DialoguePanel(Vertical):
    """The full dialogue panel with portrait, speech/choices, and input."""

    DEFAULT_CSS = """
    DialoguePanel {
        height: auto;
        max-height: 14;
        dock: bottom;
        background: #1a1207;
        border-top: solid #5a4a2a;
        padding: 0;
    }
    """

    # --- Messages ---

    class PlayerSubmit(Message):
        """Fired when the player submits text input."""

        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    class ChoiceSelected(Message):
        """Fired when the player selects a dialogue choice."""

        def __init__(self, index: int, label: str) -> None:
            self.index = index
            self.label = label
            super().__init__()

    class TypingComplete(Message):
        """Fired when the typing effect finishes."""
        pass

    # --- State ---

    def __init__(self) -> None:
        super().__init__()
        self._mode = "speech"  # "speech", "choice", "input"
        self._typing_timer: Timer | None = None
        self._fade_timer: Timer | None = None
        self._dialogue_gen = 0  # generation counter to ignore stale timers
        self._typing_text = ""
        self._typing_pos = 0
        self._typing_name = ""
        self._typing_color = ""
        self._typing_skippable = False
        self._text_dimmed = False  # fade-in state

    def compose(self) -> ComposeResult:
        with Horizontal(id="dialogue-row"):
            yield DialoguePortrait(id="portrait")
            yield DialogueSpeech(id="speech")
        yield Input(placeholder="Type your response...", id="player-input")

    def on_mount(self) -> None:
        self._set_input_visible(False)

    # --- Mode switching ---

    def _set_input_visible(self, visible: bool) -> None:
        inp = self.query_one("#player-input", Input)
        inp.display = visible
        if visible:
            inp.focus()
        elif inp.has_focus:
            self.screen.set_focus(None)

    def _clear_choice_list(self) -> None:
        for widget in self.query("ChoiceList"):
            widget.remove()

    # --- Speech mode (with typing effect) ---

    def show_dialogue(
        self, name: str, portrait: str, text: str, color: str, typing_speed: float = 0.03
    ) -> None:
        """Show character dialogue with a typing effect."""
        self._mode = "speech"
        self._set_input_visible(False)
        self._clear_choice_list()

        # Set portrait
        portrait_widget = self.query_one("#portrait", DialoguePortrait)
        portrait_widget.update(Text(portrait, style=f"bold {color}"))

        # Cancel any pending fade or typing timers from previous dialogue
        if self._fade_timer is not None:
            self._fade_timer.stop()
            self._fade_timer = None
        if self._typing_timer is not None:
            self._typing_timer.stop()
            self._typing_timer = None

        # Bump generation so stale timer callbacks are ignored
        self._dialogue_gen += 1
        gen = self._dialogue_gen

        # Start typing effect
        self._typing_name = name
        self._typing_color = color
        self._typing_text = text
        self._typing_pos = 0
        self._typing_skippable = True
        self._typing_speed = typing_speed

        # Fade-in: show name dimmed, then brighten after a brief delay
        self._text_dimmed = True
        self._update_speech_text()

        def _fade_callback() -> None:
            if self._dialogue_gen == gen:
                self._start_typing()

        self._fade_timer = self.set_timer(0.1, _fade_callback)

    def _start_typing(self) -> None:
        """Switch from dimmed to full color and begin the typing effect."""
        self._fade_timer = None
        self._text_dimmed = False
        self._update_speech_text()
        self._typing_timer = self.set_interval(self._typing_speed, self._typing_tick)

    def _typing_tick(self) -> None:
        """Advance the typing effect by one character."""
        if self._typing_pos < len(self._typing_text):
            self._typing_pos += 1
            self._update_speech_text()
        else:
            self._finish_typing()

    def _update_speech_text(self) -> None:
        """Render the speech widget with text revealed up to _typing_pos."""
        speech_widget = self.query_one("#speech", DialogueSpeech)
        text = Text()
        name_style = f"bold {self._typing_color}" if not self._text_dimmed else "#5a4a2a"
        text_style = "#e8d5a3" if not self._text_dimmed else "#5a4a2a"
        text.append(f"{self._typing_name}\n", style=name_style)
        text.append(self._typing_text[: self._typing_pos], style=text_style)
        speech_widget.update(text)

    def _finish_typing(self) -> None:
        """Complete the typing effect."""
        if self._typing_timer is not None:
            self._typing_timer.stop()
            self._typing_timer = None
        self._typing_pos = len(self._typing_text)
        self._update_speech_text()
        self._typing_skippable = False
        self.post_message(self.TypingComplete())

    def skip_typing(self) -> bool:
        """Skip to the end of the typing effect. Returns True if there was
        something to skip."""
        if self._typing_skippable and self._typing_pos < len(self._typing_text):
            self._text_dimmed = False
            self._finish_typing()
            return True
        return False

    # --- Choice mode ---

    def show_choices(self, choices: list[str], portrait: str = "", color: str = "") -> None:
        """Show selectable dialogue choices."""
        self._mode = "choice"
        self._set_input_visible(False)

        # Clear previous choices
        self._clear_choice_list()

        # Update portrait if provided
        if portrait:
            portrait_widget = self.query_one("#portrait", DialoguePortrait)
            portrait_widget.update(Text(portrait, style=f"bold {color}"))

        # Clear speech area
        speech_widget = self.query_one("#speech", DialogueSpeech)
        speech_widget.update("")

        # Mount choice list after the dialogue row
        choice_list = ChoiceList(choices, id="choice-list")
        self.mount(choice_list, before=self.query_one("#player-input"))

    def on_choice_list_selected(self, event: ChoiceList.Selected) -> None:
        """Relay choice selection as a panel-level message."""
        self._clear_choice_list()
        self.post_message(self.ChoiceSelected(event.index, event.label))

    # --- Input mode ---

    def show_input(self, prompt: str = "Type your response...") -> None:
        """Switch to free-text input mode."""
        self._mode = "input"
        self._clear_choice_list()

        inp = self.query_one("#player-input", Input)
        inp.placeholder = prompt
        inp.value = ""
        self._set_input_visible(True)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.post_message(self.PlayerSubmit(event.value.strip()))
            event.input.value = ""
