"""Event-driven watcher trigger backed by ``watchdog`` filesystem events.

The watcher's per-tick logic lives in ``tagteam.watcher._StateProcessor``.
This module provides an alternate trigger source: rather than polling
``handoff-state.json`` on a fixed interval, subscribe to filesystem
events and call the processor whenever the state file changes.

Why three event types? ``tagteam.state.write_state`` writes a temp file
(``.handoff-state.tmp``) and atomically ``replace()``s it onto
``handoff-state.json``. Depending on the watchdog backend (FSEvents on
macOS, inotify on Linux), this surfaces as ``on_moved`` (with
``dest_path == handoff-state.json``), ``on_created``, or ``on_modified``.
Subscribing to all three guarantees we catch the write regardless of
backend. De-duplication by ``state["seq"]`` inside ``_StateProcessor``
prevents an event storm (e.g. create + modify in quick succession) from
double-processing.

The ``heartbeat_s`` cadence is a safety net for missed events on broken
filesystems (NFS, some Docker bind mounts). It also keeps the
opportunistic shadow-DB repair attempts running on a regular schedule
even when no state changes occur.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable


def is_available() -> bool:
    """Return True if ``watchdog`` is importable in this environment."""
    try:
        import watchdog  # noqa: F401
        return True
    except ImportError:
        return False


def watch_with_events(
    state_path: Path,
    on_change: Callable[[], None],
    heartbeat_s: float = 30.0,
) -> None:
    """Block until interrupted, calling ``on_change()`` on filesystem
    events for ``state_path`` and on every ``heartbeat_s`` heartbeat.

    The ``on_change`` callback is expected to read the current state and
    invoke ``_StateProcessor.tick(state)`` (which is idempotent for
    unchanged seq values). It is also called on each heartbeat so
    opportunistic repair and the watchdog re-send timer keep running.
    """
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    target = str(state_path.resolve())

    class _Handler(FileSystemEventHandler):
        def _maybe_fire(self, path: str) -> None:
            if path == target:
                on_change()

        def on_modified(self, event) -> None:  # type: ignore[override]
            if not event.is_directory:
                self._maybe_fire(event.src_path)

        def on_created(self, event) -> None:  # type: ignore[override]
            if not event.is_directory:
                self._maybe_fire(event.src_path)

        def on_moved(self, event) -> None:  # type: ignore[override]
            # Atomic rename of .handoff-state.tmp → handoff-state.json
            # surfaces as a move whose dest_path is our target.
            dest = getattr(event, "dest_path", None)
            if dest:
                self._maybe_fire(dest)

    observer = Observer()
    observer.schedule(_Handler(), str(state_path.parent), recursive=False)
    observer.start()
    try:
        # Fire once on startup so any current state is processed
        # immediately (mirrors poll mode's first-iteration behavior).
        on_change()
        while True:
            time.sleep(heartbeat_s)
            on_change()
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
