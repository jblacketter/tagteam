"""Character definitions for the Handoff Saloon.

Each character has a name, sprite art, portrait art, color, and
a set of canned responses for the phase 1 demo flow.
"""

from dataclasses import dataclass, field

from ai_handoff.tui.art.mayor import MAYOR, MAYOR_PORTRAIT
from ai_handoff.tui.art.rabbit import RABBIT, RABBIT_PORTRAIT


@dataclass
class Character:
    name: str
    sprite: str
    portrait: str
    color: str
    canned_responses: list[str] = field(default_factory=list)


MAYOR_CHARACTER = Character(
    name="Mayor",
    sprite=MAYOR,
    portrait=MAYOR_PORTRAIT,
    color="#d4a04a",  # warm amber
    canned_responses=[
        "Interesting. Let me think on that. Bartender, did you hear that?",
        "A fair point. We shall take that under advisement.",
        "Indeed. The town could use someone with your perspective.",
        "Let us proceed carefully. Every decision shapes Handoff Hollow.",
        "Duly noted. I shall have the Bartender weigh in on this matter.",
    ],
)

RABBIT_CHARACTER = Character(
    name="Rabbit Bartender",
    sprite=RABBIT,
    portrait=RABBIT_PORTRAIT,
    color="#7a9e6b",  # muted green
    canned_responses=[
        "I hear everything. Go on.",
        "Hmm. Interesting choice. I've seen worse.",
        "Yeah, that tracks. Keep talking.",
        "Not bad. Not bad at all.",
        "I've been polishing this glass for three years. I've heard it all.",
    ],
)

ALL_CHARACTERS = [MAYOR_CHARACTER, RABBIT_CHARACTER]
