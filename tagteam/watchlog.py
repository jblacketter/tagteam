"""The watcher's event log and heartbeat (Phase 67).

The watcher narrates everything it does through ``watcher._log()``; until this
phase that narration went to stdout and nowhere else. A ``Sink`` installed for
the lifetime of a watcher also records it:

``.tagteam/watcher-events.jsonl``  one JSON object per line, bounded (512 KB
                                   live + one rotated ``.1``)
``.tagteam/watcher-beat.json``     when the loop last looked at the state

Writing is synchronous, bounded and best-effort. Every path component is
lstat-checked (no symlink, plain directories, a regular-file leaf), the leaf
is opened ``O_NOFOLLOW | O_NONBLOCK`` and re-checked with ``fstat`` before a
byte is written — so a FIFO or symlink at the path is refused, not blocked on
or written through — and every failure is swallowed. That narrows the window
for a swapped path; it is not a defence against an attacker who can replace
the project's directories while the watcher runs. ``.tagteam/`` is never
created here: a project without it records nothing.

Imports nothing from ``watcher`` so the CLI and ``cockpit_api`` can read
without loading it; ``procs`` is imported lazily.
"""
from __future__ import annotations

import json
import os
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from tagteam.safe_read import _dir_chain, read_bounded

DIR = ".tagteam"
EVENTS_NAME = "watcher-events.jsonl"
BEAT_NAME = "watcher-beat.json"
EVENTS_REL = f"{DIR}/{EVENTS_NAME}"
ROTATED_REL = f"{EVENTS_REL}.1"
BEAT_REL = f"{DIR}/{BEAT_NAME}"

MAX_BYTES = 512 * 1024
MSG_LIMIT = 500
BEAT_THROTTLE_S = 5.0
STALE_FACTOR = 3
DEFAULT_EVERY_S = 30.0
_READ_LIMIT = MAX_BYTES + 64 * 1024     # a live file may exceed MAX_BYTES by one record

KINDS = ("info", "start", "stop", "turn", "sent", "send-failed", "paused", "resumed",
         "watchdog", "gate", "panel", "done", "escalated", "aborted", "refused", "stuck", "advance",
         "error")
DISPATCH_KINDS = ("turn", "sent", "send-failed", "paused", "resumed", "refused")
_CTX_KEYS = ("phase", "type", "round", "turn", "seq")

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open_regular(root: Path, rel: str, flags: int) -> int | None:
    """An fd on ``rel`` for writing, or None. The parent must be a chain of
    plain directories; an existing leaf must be a regular file by ``lstat``
    (``O_NOFOLLOW`` alone is 0 on some platforms) and again by ``fstat`` after
    the open. Never blocks: ``O_NONBLOCK`` makes a FIFO open return at once."""
    parent = Path(rel).parent.as_posix()
    if _dir_chain(root, parent)[0] != "dir":
        return None
    path = root / rel
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        st = None
    except OSError:
        return None
    if st is not None and not stat.S_ISREG(st.st_mode):
        return None
    try:
        fd = os.open(path, flags | _NOFOLLOW | _NONBLOCK | _CLOEXEC, 0o644)
    except OSError:
        return None
    try:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            return fd
    except OSError:
        pass
    os.close(fd)
    return None


def effective_every_s(every_s) -> float:
    """The cadence a reader may expect: the loop's own interval, but never
    less than the write throttle (a 1 s poll still beats only every 5 s)."""
    try:
        v = float(every_s)
    except (TypeError, ValueError):
        v = 0.0
    if not v > 0:
        v = DEFAULT_EVERY_S
    return max(v, BEAT_THROTTLE_S)


class Sink:
    """One watcher's writer. ``watch_with_events`` calls the processor from
    the observer thread *and* the main loop, so rotation + append and
    throttle + replace each run under one lock."""

    def __init__(self, root: str | Path, mode: str, every_s: float, pid: int | None = None):
        from tagteam import procs
        self.root = Path(root)
        self.mode = mode
        self.every_s = float(every_s)
        self.pid = os.getpid() if pid is None else pid
        self.ident = procs.identity(self.pid)
        self.started_at = _now_iso()
        self._lock = threading.Lock()
        self._last_beat = 0.0

    # -- events ---------------------------------------------------------
    def event(self, msg: str, kind: str = "info", **ctx) -> bool:
        try:
            rec = {"ts": _now_iso(), "pid": self.pid, "mode": self.mode,
                   "kind": kind if kind in KINDS else "info", "msg": str(msg)[:MSG_LIMIT]}
            for k in _CTX_KEYS:
                if ctx.get(k) is not None:
                    rec[k] = ctx[k]
            line = (json.dumps(rec, ensure_ascii=False, default=str) + "\n").encode("utf-8")
            with self._lock:
                return self._append(line)
        except Exception:
            return False

    def _append(self, line: bytes) -> bool:
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        fd = _open_regular(self.root, EVENTS_REL, flags)
        if fd is None:
            return False
        try:
            if os.fstat(fd).st_size > MAX_BYTES:
                os.close(fd)
                fd = None
                # rename the entry; a link planted at `.1` is replaced, not followed
                os.replace(self.root / EVENTS_REL, self.root / ROTATED_REL)
                fd = _open_regular(self.root, EVENTS_REL, flags)
                if fd is None:
                    return False
            os.write(fd, line)
            return True
        except OSError:
            return False
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    # -- heartbeat ------------------------------------------------------
    def due(self) -> bool:
        """Would `beat()` write now? Lets a caller skip work (re-reading the
        state) that a throttled beat would throw away."""
        with self._lock:
            return not self._last_beat or time.monotonic() - self._last_beat >= BEAT_THROTTLE_S

    def beat(self, state: dict | None = None, force: bool = False) -> bool:
        try:
            with self._lock:
                now = time.monotonic()
                if not force and self._last_beat and now - self._last_beat < BEAT_THROTTLE_S:
                    return False
                self._last_beat = now
                st = state if isinstance(state, dict) else {}
                rec = {"pid": self.pid, "ident": self.ident, "mode": self.mode,
                       "started_at": self.started_at, "ts": _now_iso(), "every_s": self.every_s,
                       "seq": st.get("seq"), "status": st.get("status"), "turn": st.get("turn")}
                return self._write_beat(json.dumps(rec).encode("utf-8"))
        except Exception:
            return False

    def _write_beat(self, data: bytes) -> bool:
        tmp_rel = f"{BEAT_REL}.{self.pid}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = _open_regular(self.root, tmp_rel, flags)
        if fd is None:
            # a leftover at the temp name (file, link or FIFO): remove the entry once and retry
            if _dir_chain(self.root, DIR)[0] != "dir":
                return False
            try:
                os.unlink(self.root / tmp_rel)
            except OSError:
                return False
            fd = _open_regular(self.root, tmp_rel, flags)
            if fd is None:
                return False
        try:
            os.write(fd, data)
        except OSError:
            os.close(fd)
            self._unlink(tmp_rel)
            return False
        os.close(fd)
        try:
            os.replace(self.root / tmp_rel, self.root / BEAT_REL)
            return True
        except OSError:
            self._unlink(tmp_rel)
            return False

    def _unlink(self, rel: str) -> None:
        try:
            os.unlink(self.root / rel)
        except OSError:
            pass

    def close(self) -> None:
        """Remove this process's beat (never another watcher's). The event
        log stays: history outlives the process."""
        try:
            with self._lock:
                if _dir_chain(self.root, DIR)[0] != "dir":
                    return
                rec = read_beat(self.root)
                if rec is not None and rec.get("pid") == self.pid:
                    self._unlink(BEAT_REL)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# readers

def _records(root: Path, rel: str) -> list[dict]:
    r = read_bounded(root, rel, _READ_LIMIT, truncate=True)
    if r.state != "file" or not r.data:
        return []
    out = []
    for raw in r.data.split(b"\n"):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def read(root: str | Path, n: int = 50) -> list[dict]:
    """The newest ``n`` events, oldest first. Malformed lines are skipped."""
    root = Path(root)
    n = max(1, int(n))
    live = _records(root, EVENTS_REL)
    if len(live) < n:
        live = _records(root, ROTATED_REL) + live
    return live[-n:]


def last_event(root: str | Path, kinds: tuple[str, ...] | None = None) -> dict | None:
    """Newest retained event that is not ``info`` (or whose kind is in
    ``kinds``): the live file first, then the rotated one. Bounded by the
    files' own byte cap, not by a record count — chatter must not be able to
    hide a dispatch that is still on disk."""
    root = Path(root)
    for rel in (EVENTS_REL, ROTATED_REL):
        for rec in reversed(_records(root, rel)):
            k = rec.get("kind")
            if (kinds is None and k != "info") or (kinds is not None and k in kinds):
                return rec
    return None


def read_beat(root: str | Path) -> dict | None:
    r = read_bounded(Path(root), BEAT_REL, 64 * 1024, truncate=False)
    if r.state != "file" or not r.data:
        return None
    try:
        rec = json.loads(r.data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return rec if isinstance(rec, dict) else None


def _age_s(ts, now: datetime) -> float | None:
    try:
        t = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0.0, (now - t).total_seconds())


def _blocked_by(beat: dict, inflight: dict | None) -> bool:
    """Is the beat's own watcher legitimately blocked? True when the in-flight
    record's *runner* is that watcher: a headless cycle turn, a gate, a panel
    or a brief — each runs synchronously inside the watcher loop. A lead
    conversation is run by the server (another pid) and never matches; nor
    does a marker left by a different or earlier watcher."""
    if not isinstance(inflight, dict) or inflight.get("watcher_pid") != beat.get("pid"):
        return False
    a, b = inflight.get("watcher_ident"), beat.get("ident")
    return not (a and b and a != b)


def beat_view(root: str | Path, watcher: dict | None = None, inflight: dict | None = None,
              now: datetime | None = None) -> dict:
    """The one reading of the heartbeat the CLI and the API share.

    state: none | previous | fresh | in-turn | stale
    """
    from tagteam import procs
    out = {"state": "none", "age_s": None, "every_s": None, "stale_after_s": None}
    beat = read_beat(root)
    if beat is None:
        return out
    now = now or datetime.now(timezone.utc)
    every = effective_every_s(beat.get("every_s"))
    age = _age_s(beat.get("ts"), now)
    out.update({"age_s": age, "every_s": every, "stale_after_s": STALE_FACTOR * every})
    if age is None:
        return out
    pid = beat.get("pid")
    alive = isinstance(pid, int) and pid > 0 and procs.pid_alive(pid)
    previous = not alive
    if alive and beat.get("ident"):
        live_ident = procs.identity(pid)
        # an identity that cannot be read does not by itself demote the beat
        previous = live_ident is not None and live_ident != beat.get("ident")
    if not previous and isinstance(watcher, dict) and watcher.get("running") \
            and isinstance(watcher.get("pid"), int) and watcher.get("pid") != pid:
        previous = True
    if previous:
        out["state"] = "previous"
    elif age <= out["stale_after_s"]:
        out["state"] = "fresh"
    elif _blocked_by(beat, inflight):
        out["state"] = "in-turn"
    else:
        out["state"] = "stale"
    return out


# ---------------------------------------------------------------------------
# CLI: `tagteam watch status` / `tagteam watch log` — reads, allowed under
# TAGTEAM_READ_ONLY.

def _ago(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    s = int(seconds)
    if s < 90:
        return f"{s}s ago"
    if s < 5400:
        return f"{s // 60}m ago"
    return f"{s // 3600}h {s % 3600 // 60:02d}m ago"


def _local_hms(ts) -> str:
    try:
        t = datetime.fromisoformat(str(ts))
    except ValueError:
        return "--:--:--"
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone().strftime("%H:%M:%S")


def describe_beat(view: dict, inflight: dict | None = None) -> str:
    state, ago = view.get("state"), _ago(view.get("age_s"))
    if state == "none":
        return "never (no heartbeat recorded)"
    if state == "previous":
        return f"previous watcher, {ago}"
    if state == "fresh":
        return ago
    if state == "in-turn":
        who = (inflight or {}).get("agent") or (inflight or {}).get("kind") or "a turn"
        return f"{ago} — busy running {who}"
    return (f"STALE: {ago}, expected every {view.get('every_s'):g}s, "
            "nothing in flight that would block the loop")


def format_event(rec: dict, width: int | None = None) -> str:
    msg = str(rec.get("msg") or "").strip()
    if width is not None and len(msg) > width:
        msg = msg[:width - 1].rstrip() + "…"
    return f"{_local_hms(rec.get('ts'))}  {str(rec.get('kind') or 'info'):<12}{msg}"


def status_lines(root: str | Path) -> list[str]:
    from tagteam import headless
    from tagteam.cockpit_api import watcher_status
    root = Path(root)
    inflight = headless.read_inflight(root)
    ws = watcher_status(root, inflight)
    if ws.get("running"):
        head = f"watcher: running (pid {ws.get('pid')}, mode {ws.get('mode') or '?'}"
        head += f", started {_local_hms(ws['started_at'])})" if ws.get("started_at") else ")"
    else:
        head = "watcher: not running" + (" (stale pidfile)" if ws.get("stale_pidfile") else "")
    lines = [head, "last look: " + describe_beat(beat_view(root, ws, inflight), inflight)]
    ev = last_event(root, DISPATCH_KINDS)
    lines.append("last dispatch: " + (format_event(ev, width=100) if ev else "none recorded"))
    info = headless.read_pause(root)
    lines.append("dispatch: " + ("not paused" if info is None
                                 else headless.describe_pause(info) + " — tagteam resume to release"))
    return lines


def command(args: list[str], root: str | Path) -> int:
    sub, rest = args[0], args[1:]
    if sub == "status":
        print("\n".join(status_lines(root)))
        return 0
    n, as_json, i = 30, False, 0
    while i < len(rest):
        if rest[i] in ("-n", "--lines") and i + 1 < len(rest):
            try:
                n = int(rest[i + 1])
            except ValueError:
                print(f"watch log: -n takes a number, not {rest[i + 1]!r}")
                return 2
            i += 2
        elif rest[i] == "--json":
            as_json = True
            i += 1
        else:
            print(f"watch log: unknown option {rest[i]!r} (use -n N, --json)")
            return 2
    events = read(root, max(1, min(n, 5000)))
    if as_json:
        for rec in events:
            print(json.dumps(rec, ensure_ascii=False))
    elif not events:
        print("No watcher events recorded yet (a watcher records them once it runs here).")
    else:
        print("\n".join(format_event(r) for r in events))
    return 0
