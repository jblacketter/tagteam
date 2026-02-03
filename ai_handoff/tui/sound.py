"""Sound effects — non-blocking audio playback.

Opt-in via GAMERFY_SOUND=1 environment variable. Uses macOS ``afplay``
for playback. Silently no-ops when sound is disabled or ``afplay`` is
not available.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_ENABLED = os.environ.get("GAMERFY_SOUND", "").strip().lower() in ("1", "true", "yes")
_SOUND_DIR = Path(__file__).parent / "sounds"


def play(name: str) -> None:
    """Play a named sound effect (non-blocking).

    Looks for ``sounds/{name}.wav`` relative to this module.
    No-op if sound is disabled or the file/binary is missing.
    """
    if not _ENABLED:
        return
    path = _SOUND_DIR / f"{name}.wav"
    if not path.exists():
        return
    try:
        subprocess.Popen(
            ["afplay", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass  # afplay not available (non-macOS)
