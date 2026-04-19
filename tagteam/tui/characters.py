"""Character definitions for the Handoff Saloon.

Each character has a name, sprite art, portrait art, and color.
"""

from dataclasses import dataclass

from tagteam.tui.art.mayor import MAYOR, MAYOR_PORTRAIT
from tagteam.tui.art.rabbit import RABBIT, RABBIT_PORTRAIT


@dataclass
class Character:
    name: str
    sprite: str
    portrait: str
    color: str


MAYOR_CHARACTER = Character(
    name="Mayor",
    sprite=MAYOR,
    portrait=MAYOR_PORTRAIT,
    color="#d4a04a",  # warm amber
)

RABBIT_CHARACTER = Character(
    name="Rabbit Bartender",
    sprite=RABBIT,
    portrait=RABBIT_PORTRAIT,
    color="#7a9e6b",  # muted green
)

ALL_CHARACTERS = [MAYOR_CHARACTER, RABBIT_CHARACTER]
