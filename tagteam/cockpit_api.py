"""
Arbiter cockpit API (Phase 34): pure payload builders for the cockpit's
read endpoints, the SSE change signature, and thin wrappers that run the
Phase 32/33 control commands with a captured `out` buffer.

Everything here is unit-testable without HTTP; `tagteam.server` routes are
thin. Builders never raise on missing data — they return the documented
shape with nulls / empty lists so the page can render an honest empty
state. Only programming errors propagate.
"""

from __future__ import annotations

import getpass
import hashlib
import io
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from tagteam import headless as h

SCOPE_DIFF_MAX_BYTES = 200_000
SCOPE_DIFF_MAX_FILES = 400
DEFAULT_TAIL_LINES = 40
MAX_TAIL_LINES = 2000
LOG_SIGNAL_STEP = 8192          # Phase 43: in-flight log growth granularity in the SSE signature


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _age_s(ts: str | None) -> float | None:
    """Seconds since an ISO timestamp, or None when unparsable."""
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0.0, (_now() - t).total_seconds())


def web_user() -> str:
    """`by` identity recorded for browser-initiated controls: `web:<user>`
    where user is the OS user running the server (the browser is local by
    default)."""
    env = os.environ.get("TAGTEAM_ARBITER")
    if env:
        return f"web:{env}"
    try:
        return f"web:{getpass.getuser()}"
    except Exception:
        return "web:arbiter"


# ---------------------------------------------------------------------------
# Watcher liveness (project-bound)
# ---------------------------------------------------------------------------

def _same_dir(a: str | None, b: str | Path) -> bool:
    if not a:
        return False
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


# A real watcher invocation, anchored at the START of the command line:
# `python -m tagteam watch …`, `python …/bin/tagteam watch …` (console
# script), `…/bin/tagteam watch …`, or `tagteam watch …`. Deliberately NOT a
# loose "tagteam.*watch" — a shell whose command text merely mentions the
# words (with the project as cwd) must not count as a watcher.
import re as _re
_PY = r"\S*python[\w.]*(?:\.exe)?"
WATCH_ARGV_RE = _re.compile(
    r"^(?:" + _PY + r"\s+-m\s+tagteam"                       # python -m tagteam watch
    r"|" + _PY + r"\s+\S*[/\\]tagteam(?:\.exe)?"           # python /path/bin/tagteam watch
    r"|(?:\S*[/\\])?tagteam(?:\.exe)?"                     # [/path/]tagteam watch
    r")\s+watch(?:\s|$)")


def watcher_status(project_dir: str | Path, inflight: dict | None = None,
                   procs_snapshot: list[tuple[int, str]] | None = None) -> dict:
    """Is a watcher running FOR THIS PROJECT?  Signals, in order:

    1. the watcher pidfile (`.tagteam/watcher.json`: pid + creation identity
       + mode) — Tagteam's own launch shape puts no project path on argv,
       so this is the primary binding; a dead pid / identity mismatch is
       reported as `stale_pidfile` and never trusted;
    2. a process scan: `tagteam … watch` processes whose argv names the
       project OR whose cwd is the project (older watchers, no pidfile);
    3. the in-flight pointer's watcher pid/identity (headless runner).

    Returns {running, pid, mode, source, stale_pidfile}."""
    from tagteam import procs
    from tagteam import watcher as watcher_mod
    root = Path(project_dir)
    out = {"running": False, "pid": None, "mode": None, "source": None, "stale_pidfile": False}
    rec = watcher_mod.read_pidfile(root)
    if rec is not None:
        pid = rec.get("pid")
        alive = isinstance(pid, int) and pid > 0 and procs.pid_alive(pid)
        ident_ok = True
        if alive and rec.get("ident"):
            now_ident = procs.identity(pid)
            ident_ok = (now_ident is None) or (now_ident == rec.get("ident"))
        if alive and ident_ok:
            out.update({"running": True, "pid": pid, "mode": rec.get("mode"),
                        "source": "pidfile", "started_at": rec.get("started_at")})
            return out
        out["stale_pidfile"] = True
    # process scan (Linux: /proc, no subprocess; macOS/BSD: one `ps -axo`)
    me = os.getpid()
    if procs_snapshot is None:
        procs_snapshot = procs.list_processes(WATCH_ARGV_RE.pattern)
    for pid, argv in procs_snapshot:
        if not WATCH_ARGV_RE.search(argv):
            continue
        if pid == me or not procs.pid_alive(pid):
            continue
        names_project = any(_same_dir(tok.rstrip("/\\"), root) for tok in argv.split()
                            if tok.startswith(("/", "~", "\\")) or (len(tok) > 2 and tok[1] == ":"))
        if names_project or _same_dir(procs.cwd(pid), root):
            mode = None
            if argv and "--mode" in argv:
                try:
                    mode = argv.split("--mode", 1)[1].split()[0]
                except IndexError:
                    mode = None
            out.update({"running": True, "pid": pid, "mode": mode, "source": "process-scan"})
            return out
    # in-flight runner — only a CYCLE turn's runner is a watcher; a
    # conversation turn's runner (the cockpit server / `tagteam lead`) or
    # the briefer's is not (Phase 37 marker `kind`).
    if inflight and inflight.get("watcher_pid") and inflight.get("kind") in (None, "cycle"):
        wpid = inflight.get("watcher_pid")
        try:
            if procs.pid_alive(wpid) and (not inflight.get("watcher_ident")
                                         or procs.identity(wpid) == inflight.get("watcher_ident")):
                out.update({"running": True, "pid": wpid, "mode": "headless", "source": "inflight"})
                return out
        except Exception:
            pass
    return out


# ---------------------------------------------------------------------------
# /api/now
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Phase 68: the headline — "who has the ball", derived once, server-side.
#
# `status_facts()` gathers what the sentence needs WITHOUT opening the
# database (so `tagteam watch status` can use it on any project, in any mode,
# and create nothing); `inflight_liveness()` and `headline()` are pure. The
# front end appends the ticking age and must not re-derive any of this.

TERMINAL_WATCHER_MODES = ("iterm2", "terminal", "tmux")   # a `sent` event here means: typed into the agent's session
OWED_ATTENTION_S = 900                                     # the threshold the old owed chip used


def inflight_liveness(marker: dict | None, *, child_alive: bool | None, owner_gone: bool | None) -> str | None:
    """starting | no-child | running | finishing | unknown | lost.

    `now.inflight.pid_alive` is False for `pid: None` as well as for a dead
    child, and a gate marker has `pid: None` for its whole run — so that flag
    must never decide "the turn is lost". Lost means the marker's OWNER (the
    runner recorded as `watcher_pid`) is definitively gone
    (`headless.slot_owner_gone`), or, for a legacy marker with no runner pid,
    that its recorded child is confirmed dead. No evidence is `unknown`,
    never a guess."""
    if not marker:
        return None
    if owner_gone is True:
        return "lost"
    pid = marker.get("pid")
    if not (isinstance(pid, int) and pid > 0):
        return "no-child" if marker.get("kind") == "gate" else "starting"
    if child_alive is True:
        return "running"
    if child_alive is None:
        return "unknown"
    rpid = marker.get("watcher_pid")
    return "finishing" if (isinstance(rpid, int) and rpid > 0) else "lost"


def _probe_inflight(root: Path) -> dict | None:
    inflight = h.read_inflight(root)
    if inflight is None:
        return None
    inflight = dict(inflight)
    inflight["age_s"] = _age_s(inflight.get("started_at"))
    pid = inflight.get("pid")
    child_alive: bool | None = None
    try:
        from tagteam import procs
        if isinstance(pid, int) and pid > 0:
            child_alive = bool(procs.pid_alive(pid))
        inflight["pid_alive"] = bool(child_alive)          # unchanged contract: False for pid None too
    except Exception:
        inflight["pid_alive"] = None
        child_alive = None
    try:
        owner_gone: bool | None = bool(h.slot_owner_gone(inflight)[0])
    except Exception:
        owner_gone = None
    inflight["liveness"] = inflight_liveness(inflight, child_alive=child_alive, owner_gone=owner_gone)
    return inflight


def status_facts(project_dir: str | Path) -> dict:
    """The headline's inputs, from reads that never open the database:
    handoff-state.json, tagteam.yaml, the in-flight and pause markers, the
    watcher's pidfile/scan, its heartbeat and its event log."""
    from tagteam.state import read_state
    from tagteam.config import read_config, get_agent_names
    from tagteam import watchlog
    root = Path(project_dir)
    state = read_state(str(root)) or {}
    try:
        config = read_config(root / "tagteam.yaml") or {}
    except Exception:
        config = {}
    try:
        lead, reviewer = (get_agent_names(config) if config else (None, None))
    except Exception:
        lead, reviewer = None, None

    owed = None
    turn = state.get("turn")
    if state.get("status") in ("ready", "working") and turn in ("lead", "reviewer"):
        owed = {"role": turn, "agent": lead if turn == "lead" else reviewer,
                "since": state.get("updated_at"), "age_s": _age_s(state.get("updated_at"))}

    inflight = _probe_inflight(root)

    paused = h.read_pause(root)
    if paused is not None:
        paused = dict(paused)
        paused["age_s"] = _age_s(paused.get("ts"))

    try:
        watcher = watcher_status(root, inflight)
    except Exception:
        watcher = {"running": False, "pid": None, "mode": None, "source": None, "stale_pidfile": False}
    # Phase 67: when the watcher last looked (derived state, so no consumer
    # re-derives staleness), the last thing it did that was not chatter, and
    # (Phase 68) its last dispatch.
    try:
        beat = watchlog.beat_view(root, watcher, inflight)
        beat["text"] = watchlog.describe_beat(beat, inflight)
        watcher["beat"] = beat
        watcher["last_event"] = watchlog.last_event(root)
        watcher["last_dispatch"] = watchlog.last_event(root, watchlog.DISPATCH_KINDS)
    except Exception:
        watcher.setdefault("beat", {"state": "none", "age_s": None, "every_s": None, "stale_after_s": None,
                                    "text": "never (no heartbeat recorded)"})
        watcher.setdefault("last_event", None)
        watcher.setdefault("last_dispatch", None)

    return {"state": state, "config": config, "agents": {"lead": lead, "reviewer": reviewer}, "owed": owed,
            "inflight": inflight, "turn_kind": (inflight or {}).get("kind") or ("cycle" if inflight else None),
            "paused": paused, "watcher": watcher}


def _type_word(t) -> str:
    return {"plan": "plan", "impl": "implementation"}.get(str(t or ""), str(t or ""))


def _sent_age_s(facts: dict) -> float | None:
    ev = ((facts.get("watcher") or {}).get("last_dispatch")) or {}
    return _age_s(ev.get("ts")) if ev.get("ts") else None


def turn_delivered(facts: dict) -> bool:
    """Was the owed turn typed into the agent's own session? Only a terminal
    watcher's `sent` says so: notify's `sent` is a notification and headless's
    precedes the engine's dispatch. A missing seq never matches."""
    ev = ((facts.get("watcher") or {}).get("last_dispatch")) or {}
    seq = (facts.get("state") or {}).get("seq")
    return (ev.get("kind") == "sent" and ev.get("mode") in TERMINAL_WATCHER_MODES
            and seq is not None and ev.get("seq") is not None and ev.get("seq") == seq)


def _working_text(facts: dict) -> tuple[str, str | None, str | None]:
    inf = facts.get("inflight") or {}
    agents = facts.get("agents") or {}
    kind = facts.get("turn_kind") or "cycle"
    role = inf.get("role")
    rnd = f" · round {inf['round']}" if inf.get("round") is not None else ""
    if kind == "gate":
        return "Pre-check running" + rnd, "gatekeeper", None
    if kind == "panel":
        return "Review panel running" + rnd, "reviewer", None
    if kind == "briefer":
        return "Writing the decision brief", None, None
    agent = inf.get("agent") or (agents.get("lead") if role == "lead" else agents.get("reviewer")) \
        or inf.get("provider") or (role or "an agent")
    if inf.get("liveness") == "finishing":
        return f"{agent}'s turn has ended — recording the result", role, agent
    if kind == "conversation":
        return f"{agent} is answering you", "lead", agent
    if role == "reviewer":
        return f"{agent} is reviewing" + rnd, role, agent
    ctype = str(inf.get("type") or "")
    # A `start <phase> impl` turn runs under the PLAN cycle's marker (the impl cycle does not exist
    # until the turn creates it) — seen live as "claude is working on the plan" while it was
    # implementing. The state's command says which it is.
    cmd = str((facts.get("state") or {}).get("command") or "")
    if role == "lead" and _re.search(r"\bstart\s+\S+\s+impl\b", cmd):
        ctype = "impl"
    verb = "is implementing" if ctype == "impl" else "is working on the plan" if ctype == "plan" else "is working"
    return f"{agent} {verb}" + rnd, role, agent


def headline(facts: dict, launch: dict | None = None) -> dict:
    """One sentence for "who has the ball". First match wins; the order and
    every row are specified in docs/phases/cockpit-turn-bar-and-watcher-drawer.md.
    `text` never contains an age: `age_s` / `age_of` carry the one age the
    sentence is about and each surface appends it exactly once."""
    state = facts.get("state") or {}
    inf, owed, paused = facts.get("inflight"), facts.get("owed"), facts.get("paused")
    watcher = facts.get("watcher") or {}
    beat = watcher.get("beat") or {}
    status = state.get("status")

    def out(st, tone, text, age_s=None, age_of=None, role=None, agent=None):
        return {"state": st, "tone": tone, "text": text, "age_s": age_s, "age_of": age_of if age_s is not None else None,
                "role": role, "agent": agent}

    roadmap_reason = ((state.get("roadmap") or {}).get("pause_reason")) if isinstance(state.get("roadmap"), dict) else None
    if status in ("escalated", "needs-human") or roadmap_reason:
        age = _age_s(state.get("updated_at"))
        if roadmap_reason:
            return out("needs-you", "attention", f"Waiting on you — roadmap paused: {roadmap_reason}", age, "owed", "you")
        if status == "needs-human":
            who = state.get("updated_by")
            return out("needs-you", "attention",
                       f"Waiting on you — a question from {who}" if who else "Waiting on you — a question", age, "owed", "you")
        return out("needs-you", "attention", "Waiting on you — escalated", age, "owed", "you")

    if inf:
        text, role, agent = _working_text(facts)
        if inf.get("liveness") == "lost":
            who = f"{agent}'s turn" if agent else "The running " + {"gate": "pre-check", "panel": "review panel",
                                                                   "briefer": "decision brief"}.get(facts.get("turn_kind"), "turn")
            return out("turn-lost", "danger", f"{who} was abandoned — the process running it is gone",
                       inf.get("age_s"), "turn", role, agent)
        return out("working", "working", text, inf.get("age_s"), "turn", role, agent)

    if launch and launch.get("status") == "pending":
        lead = (facts.get("agents") or {}).get("lead") or "the lead"
        what = ", ".join(x for x in (launch.get("phase"), _type_word(launch.get("type"))) if x)
        return out("launching", "working", f"Starting {what} — {lead} is on it" if what else f"Starting — {lead} is on it",
                   launch.get("age_s"), "turn", "lead", lead)

    if owed:
        agent = owed.get("agent") or owed.get("role")
        role = owed.get("role")
        delivered = turn_delivered(facts)
        if paused:
            by = paused.get("by")
            head = f"Paused by {by}" if by else "Paused"
            text = (f"{head} — {agent} has its turn; further hand-offs are held" if delivered
                    else f"{head} — {agent}'s turn is held")
            return out("paused", "attention", text, paused.get("age_s"), "pause", role, agent)
        # A delivered turn's age counts from the dispatch, not from the submission: the owed age
        # includes the pre-check that ran in between (seen live: "sent to its terminal · 7m", seconds
        # after a 7½-minute gate run).
        sent_age = _sent_age_s(facts) if delivered else None
        wait_age, wait_of = (sent_age, "sent") if sent_age is not None else (owed.get("age_s"), "owed")
        if not watcher.get("running"):
            text = (f"Waiting on {agent} — its turn was sent; the watcher has since stopped, so the next hand-off will not happen"
                    if delivered else f"Waiting on {agent} — the watcher is off, nothing will start its turn")
            return out("watcher-off", "attention", text, wait_age, wait_of, role, agent)
        mode = watcher.get("mode")
        if beat.get("state") == "stale":
            if delivered:
                return out("watcher-stale", "attention", f"Waiting on {agent} — it has its turn; the watcher has stopped looking",
                           beat.get("age_s"), "beat", role, agent)
            return out("stalled", "danger", f"Stalled: {agent} is owed a turn and the watcher has stopped looking",
                       beat.get("age_s"), "beat", role, agent)
        if mode == "headless" and beat.get("state") in ("fresh", "in-turn"):
            return out("starting", "working", f"Starting {agent}'s turn…", owed.get("age_s"), "owed", role, agent)
        if mode in TERMINAL_WATCHER_MODES:
            text = (f"Waiting on {agent} — its turn was sent to its terminal" if delivered
                    else f"Waiting on {agent} — the watcher will send its turn to its terminal")
        elif mode == "notify":
            text = f"Waiting on {agent} — the watcher only notifies you; run the turn in {agent}'s session"
        elif mode == "headless":
            text = f"Waiting on {agent} — a headless watcher is running but has not reported in"
        else:
            text = f"Waiting on {agent} — a watcher is running"
        tone = "attention" if (wait_age is not None and wait_age > OWED_ATTENTION_S) else "waiting"
        return out("waiting", tone, text, wait_age, wait_of, role, agent)

    what = ", ".join(x for x in (state.get("phase"), _type_word(state.get("type"))) if x)
    if status == "done":
        result = state.get("result")
        if result == "roadmap-complete":
            return out("approved", "ok", "Roadmap complete")
        if result == "approved":
            return out("approved", "ok", f"Approved — {what}" if what else "Approved")
        return out("approved", "idle", f"Cycle complete — {result or 'done'}" + (f": {what}" if what else ""))
    if status == "aborted":
        return out("aborted", "idle", f"Aborted — {what}" if what else "Aborted")
    return out("idle", "idle", "Nothing in progress")


def now_payload(project_dir: str | Path) -> dict:
    """State, owed role (+ how long owed), in-flight, pause marker, watcher
    liveness, briefer flag, agents, and a pending-notes count."""
    from tagteam.config import get_briefer_spec
    from tagteam.cycle import read_status
    root = Path(project_dir)
    facts = status_facts(root)             # Phase 68: the DB-free facts, shared with `tagteam watch status`
    state, config = facts["state"], facts["config"]
    lead, reviewer = facts["agents"]["lead"], facts["agents"]["reviewer"]
    owed, inflight, paused, watcher = facts["owed"], facts["inflight"], facts["paused"], facts["watcher"]
    try:
        briefer_enabled = bool(get_briefer_spec(config).get("enabled")) if config else False
    except Exception:
        briefer_enabled = False

    phase, ctype = state.get("phase"), state.get("type")
    cycle_status = None
    if phase and ctype:
        try:
            cycle_status = read_status(phase, ctype, str(root))
        except Exception:
            cycle_status = None

    pending_notes = 0
    try:
        from tagteam import db
        conn = db.connect(project_dir=str(root))
        try:
            rows = db.get_interjections(conn, undelivered_only=True, include_retired=False)
            pending_notes = len([r for r in rows
                                 if r.get("phase") is None
                                 or (r.get("phase") == phase and r.get("type") == ctype)])
        finally:
            conn.close()
    except Exception:
        pending_notes = 0

    # Phase 38: gatekeeper flag + last decision for the strip chip
    # ("gate ✓ r3" / "gate ↩ r3"); read from the round entries, no DB.
    gatekeeper = {"enabled": False, "last": None}
    try:
        from tagteam.gatekeeper import resolve_gatekeeper, last_gate_summary
        gs = resolve_gatekeeper(config) if config else None
        gatekeeper["enabled"] = bool(gs and gs.enabled)
        gatekeeper["on"] = list(gs.on) if gs else []
        if phase and ctype:
            gatekeeper["last"] = last_gate_summary(str(root), phase, ctype)
    except Exception:
        pass

    # Phase 43: what KIND of process holds the slot, the launch the Start
    # card must acknowledge, and the newest terminal turn — the strip and
    # the Cycle region name them; nothing here is inferred from absence.
    turn_kind = (inflight or {}).get("kind") or ("cycle" if inflight else None)
    try:
        launch = launch_view(root)
    except Exception:
        launch = None
    try:
        last = last_turn(activity_payload(root, limit=ACTIVITY_DEFAULT_LIMIT)["items"])
    except Exception:
        last = None

    return {
        "ts": _now_iso(),
        "state": state,
        "cycle": cycle_status,
        "owed": owed,
        "inflight": inflight,
        "turn_kind": turn_kind,
        "launch": launch,
        "headline": headline(facts, launch),
        "last_turn": last,
        "paused": paused,
        "watcher": watcher,
        "briefer_enabled": briefer_enabled,
        "gatekeeper": gatekeeper,
        "agents": {"lead": lead, "reviewer": reviewer},
        "pending_notes": pending_notes,
        "project_dir": str(root),
    }


# ---------------------------------------------------------------------------
# /api/rounds (extended), /api/interjections, /api/briefs
# ---------------------------------------------------------------------------

def rounds_payload(project_dir: str | Path, phase: str, ctype: str) -> dict:
    """Rounds with additive `entries`, `rulings`, `interjections` per round
    (exactly what `tagteam cycle rounds` prints) plus the legacy `html`."""
    from tagteam.cycle import tail_rounds
    from tagteam.parser import format_rounds_html
    try:
        rounds = tail_rounds(phase, ctype, None, str(project_dir))
    except Exception:
        rounds = []
    return {"rounds": rounds, "html": format_rounds_html(rounds) if rounds else ""}


def _interjection_status(r: dict) -> str:
    if r.get("retired_ts"):
        return "retired"
    if r.get("delivered_ts"):
        return "delivered"
    return "pending"


def interjections_payload(project_dir: str | Path, phase: str | None,
                          ctype: str | None) -> dict:
    """Notes for the cycle (plus unscoped notes), newest last, with a
    derived `status` (pending / delivered / retired)."""
    from tagteam import db
    rows: list[dict] = []
    try:
        conn = db.connect(project_dir=str(project_dir))
        try:
            if phase and ctype:
                rows = db.get_interjections(conn, phase=phase, cycle_type=ctype)
                rows += [r for r in db.get_interjections(conn) if r["phase"] is None]
            else:
                rows = db.get_interjections(conn)
        finally:
            conn.close()
    except Exception:
        rows = []
    for r in rows:
        r["status"] = _interjection_status(r)
    rows.sort(key=lambda r: r["id"])
    return {"interjections": rows,
            "pending": len([r for r in rows if r["status"] == "pending"])}


def briefs_payload(project_dir: str | Path, phase: str | None, ctype: str | None) -> dict:
    """Brief history (newest first), without content (see /api/brief/<id>)."""
    from tagteam import db
    rows: list[dict] = []
    try:
        conn = db.connect(project_dir=str(project_dir))
        try:
            rows = db.brief_history(conn, phase, ctype)
        finally:
            conn.close()
    except Exception:
        rows = []
    for r in rows:
        r["has_content"] = bool(r.get("content"))
        r.pop("content", None)
    return {"briefs": rows}


def brief_payload(project_dir: str | Path, brief_id: int) -> dict | None:
    from tagteam import db
    try:
        conn = db.connect(project_dir=str(project_dir))
        try:
            return db.get_brief(conn, int(brief_id))
        finally:
            conn.close()
    except Exception:
        return None


def brief_current_payload(project_dir: str | Path, phase: str | None, ctype: str | None) -> dict:
    """The current escalation event's successful brief, or its attempt
    state — the same selection rule as `tagteam brief`."""
    from tagteam import db
    from tagteam import briefer
    if not phase or not ctype:
        return {"event": None, "reason": "no cycle selected", "brief": None, "attempts": []}
    ev, why = briefer.event_for_cycle(project_dir, phase, ctype)
    if ev is None:
        return {"event": None, "reason": why, "brief": None, "attempts": []}
    event = {"phase": ev.phase, "type": ev.type, "round": ev.round,
             "cycle_state": ev.cycle_state, "role": ev.role, "action": ev.action,
             "ts": ev.ts, "content": ev.content, "event_key": ev.event_key}
    brief = None
    attempts: list[dict] = []
    try:
        conn = db.connect(project_dir=str(project_dir))
        try:
            brief = db.successful_brief_for_event(conn, ev.event_key)
            if brief is None:
                try:
                    from tagteam.config import read_config
                    cfg = read_config(Path(project_dir) / "tagteam.yaml") or {}
                    briefer.sweep_abandoned(project_dir, briefer.resolve_briefer(cfg, project_dir).timeout_s)
                except Exception:
                    pass
                attempts = db.briefs_for_event(conn, ev.event_key)
                for a in attempts:
                    a.pop("content", None)
        finally:
            conn.close()
    except Exception:
        pass
    return {"event": event, "reason": "ok", "brief": brief, "attempts": attempts}


# ---------------------------------------------------------------------------
# /api/usage
# ---------------------------------------------------------------------------

def usage_series(rows: list[dict]) -> list[dict]:
    """Per-turn series for the churn curve (oldest first)."""
    out = []
    for r in rows:
        out.append({
            "id": r.get("id"), "ts": r.get("ts"), "phase": r.get("phase"),
            "type": r.get("type"), "round": r.get("round"), "role": r.get("role"),
            "agent": r.get("agent"), "provider": r.get("provider"), "status": r.get("status"),
            "input": r.get("input_tokens"), "output": r.get("output_tokens"),
            "cache_read": r.get("cache_read_tokens"), "cache_write": r.get("cache_write_tokens"),
            "cost": r.get("cost_usd"), "duration": r.get("duration_ms"),
        })
    return out


def usage_payload(project_dir: str | Path, phase: str | None = None,
                  ctype: str | None = None, role: str | None = None) -> dict:
    """`usage.aggregate()` roll-ups + by-agent buckets + per-turn series +
    the latest rate-limit signal(s)."""
    from tagteam import db
    from tagteam import usage as usage_mod
    rows: list[dict] = []
    limits: list[dict] = []
    try:
        conn = db.connect(project_dir=str(project_dir))
        try:
            rows = db.get_usage(conn, phase=phase, cycle_type=ctype)
            limits = db.latest_rate_limits(conn)
        finally:
            conn.close()
    except Exception:
        rows, limits = [], []
    if role:
        rows = [r for r in rows if r.get("role") == role]
    agg = usage_mod.aggregate(rows)
    by_agent: dict[str, dict] = {}
    for r in rows:
        key = f"{r.get('agent') or '?'} ({r.get('role') or '?'})"
        usage_mod._add(by_agent.setdefault(key, usage_mod._empty_bucket()), r)
    by_agent = {k: usage_mod._finish(v) for k, v in by_agent.items()}
    for lim in limits:
        lim["resets_in_s"] = None
        if lim.get("resets_at"):
            age = _age_s(lim["resets_at"])
            if age is not None:
                # _age_s clamps at 0 for the future; compute signed seconds.
                try:
                    t = datetime.fromisoformat(lim["resets_at"])
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=timezone.utc)
                    lim["resets_in_s"] = (t - _now()).total_seconds()
                except (TypeError, ValueError):
                    lim["resets_in_s"] = None
    return {
        "filter": {"phase": phase, "type": ctype, "role": role},
        "by_role": agg["by_role"],
        "by_cycle": agg["by_cycle"],
        "by_agent": by_agent,
        "totals": agg["totals"],
        "series": usage_series(rows),
        "rate_limits": limits,
    }


# ---------------------------------------------------------------------------
# /api/scope-diff
# ---------------------------------------------------------------------------

# Tagteam bookkeeping the cockpit never shows as phase work: the CLI's own
# artifact set (docs/handoffs/, handoff-state.json, …) plus the .tagteam/
# runtime directory, applied at FILE level after directory expansion.
_COCKPIT_ARTIFACT_PREFIXES = (".tagteam/",)


def is_cockpit_artifact(path: str) -> bool:
    from tagteam.cycle import _is_tagteam_artifact
    return _is_tagteam_artifact(path) or any(path.startswith(pre) for pre in _COCKPIT_ARTIFACT_PREFIXES)


def _git_out(project_dir: str, *args: str, timeout: float = 30.0) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", project_dir, *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return 1, ""


def _split_unified(diff_text: str) -> dict[str, str]:
    """Split a multi-file unified diff into {path: patch} by `diff --git`
    headers (path = the b/ side, or a/ side for deletions)."""
    out: dict[str, str] = {}
    cur_path = None
    cur: list[str] = []
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if cur_path is not None:
                out[cur_path] = "".join(cur)
            cur = [line]
            rest = line[len("diff --git "):].rstrip("\n")
            # "a/<p> b/<p>" — take the b/ side (last token that starts with b/)
            b_idx = rest.rfind(" b/")
            cur_path = rest[b_idx + 3:] if b_idx >= 0 else rest
        else:
            cur.append(line)
    if cur_path is not None:
        out[cur_path] = "".join(cur)
    return out


def scope_diff_payload(project_dir: str | Path, phase: str, ctype: str, *,
                       max_bytes: int = SCOPE_DIFF_MAX_BYTES,
                       max_files: int = SCOPE_DIFF_MAX_FILES) -> dict:
    """Scope-diff paths + a capped, per-file `git diff` against the cycle
    baseline (committed and uncommitted changes together; untracked files
    as all-additions). Binary files are listed with `patch: null`.
    Truncation is flagged per file (`truncated`) and overall."""
    from tagteam.cycle import compute_scope_diff, ScopeDiffError
    root = str(Path(project_dir))
    try:
        info = compute_scope_diff(phase, ctype, root)
    except ScopeDiffError as e:
        return {"phase": phase, "type": ctype, "error": str(e), "paths": [],
                "files": [], "truncated": False, "omitted_files": 0, "baseline": None}
    paths = info["paths"]
    # Expand collapsed untracked directories (`git status --porcelain` lists
    # `newpkg/` for a whole new tree) into the files git would add
    # (respecting .gitignore), and drop Tagteam bookkeeping at file level.
    file_paths: list[str] = []
    for p in paths:
        if p.endswith("/"):
            rc, out = _git_out(root, "ls-files", "--others", "--exclude-standard", "-z", "--", p, timeout=20)
            members = [m for m in out.split("\0") if m] if rc == 0 else []
            if not members and (Path(root) / p).is_dir():
                # tracked-but-dirty dir won't appear collapsed; be safe anyway
                rc2, out2 = _git_out(root, "ls-files", "-z", "--", p, timeout=20)
                members = [m for m in out2.split("\0") if m] if rc2 == 0 else []
            file_paths.extend(members)
        else:
            file_paths.append(p)
    file_paths = sorted({fp for fp in file_paths if not is_cockpit_artifact(fp)})

    files: list[dict] = []
    total = 0
    truncated_any = False
    listed = file_paths[:max_files]
    omitted = max(0, len(file_paths) - len(listed))
    if omitted:
        truncated_any = True

    tracked: list[str] = []
    untracked: list[str] = []
    for p in listed:
        rc, _ = _git_out(root, "ls-files", "--error-unmatch", "--", p, timeout=10)
        # Only an untracked NEW file needs the --no-index path; anything the
        # index knows (or a staged deletion, which no longer exists on disk)
        # diffs against the baseline directly.
        if rc == 0 or not (Path(root) / p).exists():
            tracked.append(p)
        else:
            untracked.append(p)
    base = info["diff_base"]
    # Status vs the baseline: added if the path is absent from the baseline
    # tree, deleted if absent from the working tree, else modified.
    in_base: set[str] = set()
    if tracked:
        rc, out = _git_out(root, "ls-tree", "-r", "--name-only", "-z", base, "--", *tracked, timeout=20)
        if rc == 0:
            in_base = {m for m in out.split("\0") if m}

    numstat: dict[str, tuple[int | None, int | None]] = {}
    patches: dict[str, str] = {}
    if tracked:
        rc, out = _git_out(root, "diff", "--numstat", base, "--", *tracked)
        if rc == 0:
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) >= 3:
                    a, d, pth = parts[0], parts[1], "\t".join(parts[2:])
                    numstat[pth] = (None if a == "-" else int(a), None if d == "-" else int(d))
        rc, out = _git_out(root, "diff", base, "--", *tracked)
        if rc == 0:
            patches.update(_split_unified(out))
    for p in untracked:
        rc, out = _git_out(root, "diff", "--no-index", "--numstat", "--", os.devnull, p, timeout=10)
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                a, d = parts[0], parts[1]
                numstat[p] = (None if a == "-" else int(a), None if d == "-" else int(d))
        rc, out = _git_out(root, "diff", "--no-index", "--", os.devnull, p, timeout=10)
        if out:
            split = _split_unified(out)
            # --no-index headers name the temp path; take the only patch
            patches[p] = next(iter(split.values()), out) if split else out

    for p in listed:
        adds, dels = numstat.get(p, (None, None))
        binary = (p in numstat and adds is None and dels is None)
        patch = None if binary else patches.get(p)
        exists = (Path(root) / p).exists()
        if p in untracked:
            status = "untracked"
        elif not exists:
            status = "deleted"
        elif p not in in_base:
            status = "added"
        else:
            status = "modified"
        entry = {"path": p, "status": status, "binary": binary,
                 "additions": adds, "deletions": dels, "patch": None, "truncated": False,
                 "bytes": len(patch.encode("utf-8", "replace")) if patch else 0}
        if patch is not None:
            size = entry["bytes"]
            if total + size > max_bytes:
                room = max(0, max_bytes - total)
                entry["patch"] = patch.encode("utf-8", "replace")[:room].decode("utf-8", "replace")
                entry["truncated"] = True
                truncated_any = True
                total = max_bytes
            else:
                entry["patch"] = patch
                total += size
        files.append(entry)

    return {
        "phase": phase, "type": ctype, "error": None,
        "baseline": info["baseline"], "diff_base": base,
        "paths": paths, "file_paths": file_paths,
        "committed": info["committed"], "uncommitted": info["uncommitted"],
        "files": files, "truncated": truncated_any, "omitted_files": omitted,
        "bytes": total, "max_bytes": max_bytes, "max_files": max_files,
    }


# ---------------------------------------------------------------------------
# Phase 43: activity read model — every agent turn, one outcome vocabulary
# ---------------------------------------------------------------------------
#
# Read-only merge of what the engine already records: the in-flight marker
# (running), `usage` rows (finished cycle turns, panel lenses, briefer),
# `conversation_turns`, `gates`, `panels`, and pending / failed `launches`.
# Nothing here writes; no schema is touched. Every raw status is normalised
# to ONE vocabulary so the strip, the lanes, the activity rows and the
# Needs-you cards say the same word for the same thing.

OUTCOME_RUNNING = "running"
OUTCOME_FINISHED = "finished"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_FAILED = "failed"
OUTCOME_TIMED_OUT = "timed_out"
OUTCOME_PROCESS_GONE = "process_gone"
OUTCOME_ORPHANED = "orphaned"
OUTCOMES = (OUTCOME_RUNNING, OUTCOME_FINISHED, OUTCOME_CANCELLED, OUTCOME_FAILED,
            OUTCOME_TIMED_OUT, OUTCOME_PROCESS_GONE, OUTCOME_ORPHANED)

# raw status (per source) → vocabulary. Anything unknown → failed (never
# "finished" by accident: an outcome we cannot name is not a success).
_RAW_OUTCOME = {
    # headless / usage / conversation turns
    "running": OUTCOME_RUNNING, "ok": OUTCOME_FINISHED, "cancelled": OUTCOME_CANCELLED,
    "timeout": OUTCOME_TIMED_OUT, "nonzero_exit": OUTCOME_FAILED, "no_round": OUTCOME_FAILED,
    "spawn_failed": OUTCOME_FAILED, "failed": OUTCOME_FAILED, "error": OUTCOME_FAILED,
    "partial": OUTCOME_FINISHED,
    # gates (pass / bounce are decisions of a finished run) and panels
    "pass": OUTCOME_FINISHED, "bounce": OUTCOME_FINISHED, "merged": OUTCOME_FINISHED,
    "fallback": OUTCOME_FINISHED, "superseded": OUTCOME_FINISHED,
    "abandoned": OUTCOME_ORPHANED,
    # launches
    "pending": OUTCOME_RUNNING, "succeeded": OUTCOME_FINISHED,
}
ACTIVITY_DEFAULT_LIMIT = 50
ACTIVITY_MAX_LIMIT = 200
_STEM_RE_TEXT = r"^[A-Za-z0-9._-]+$"


def normalize_outcome(raw: str | None, *, error: str | None = None,
                      pid_alive: bool | None = None) -> str:
    """Map a recorded status (+ error text / marker liveness) to OUTCOMES."""
    if raw == "running" and pid_alive is False:
        return OUTCOME_PROCESS_GONE
    err = (error or "").lower()
    if err.startswith("orphaned"):
        return OUTCOME_ORPHANED
    return _RAW_OUTCOME.get(str(raw or "").lower(), OUTCOME_FAILED)


def _stem_of(log_path: str | None) -> str | None:
    if not log_path:
        return None
    name = Path(str(log_path)).name
    for suf in (".events.jsonl", ".log"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name or None


def _iso_minus_ms(ts: str | None, ms) -> str | None:
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        from datetime import timedelta
        return (t - timedelta(milliseconds=float(ms or 0))).isoformat()
    except (TypeError, ValueError):
        return ts


def _item(**kw) -> dict:
    base = {"id": None, "source": None, "kind": None, "role": None, "agent": None,
            "phase": None, "type": None, "round": None, "status": None,
            "raw_status": None, "started_at": None, "ended_at": None,
            "duration_ms": None, "log_path": None, "stem": None, "detail": None,
            "ref": None, "pid_alive": None}
    base.update(kw)
    return base


def _rows(conn, sql: str, args=()) -> list[dict]:
    try:
        cur = conn.execute(sql, args)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        return []


def _activity_from_db(conn, limit: int) -> list[dict]:
    items: list[dict] = []
    # usage rows: cycle turns (kind NULL/'cycle'), panel lenses ('panel:<lens>'),
    # briefer (role 'briefer'). Conversation rows come from conversation_turns.
    from tagteam import db as _db
    # Phase 58: a turn belongs to the cycle entry it was dispatched to produce
    # (v10 target identity) when recorded; older rows / schemas use the stored cycle.
    has_target = {"target_phase", "target_type", "target_round"} <= _db.table_columns(conn, "usage")
    target_cols = ", target_phase, target_type, target_round" if has_target else ""
    for r in _rows(conn, "SELECT id, ts, phase, type, round, role, agent, status, duration_ms,"
                         " log_path, kind" + target_cols + " FROM usage ORDER BY id DESC LIMIT ?", (limit,)):
        if r.get("target_phase") and r.get("target_type"):
            r["phase"], r["type"] = r["target_phase"], r["target_type"]
            if r.get("target_round") is not None:
                r["round"] = r["target_round"]
        kind = r.get("kind") or ""
        if kind == "conversation":
            continue
        if kind.startswith("panel:"):
            akind, detail = "panel_lens", kind.split(":", 1)[1]
        elif r.get("role") == "briefer" or kind == "briefer":
            akind, detail = "briefer", "decision brief"
        else:
            akind, detail = "cycle", None
        stem = _stem_of(r.get("log_path"))
        items.append(_item(
            id=(f"turn:{stem}" if stem else f"usage:{r['id']}"), source="usage", kind=akind, role=r.get("role"),
            agent=r.get("agent"), phase=r.get("phase"), type=r.get("type"), round=r.get("round"),
            status=normalize_outcome(r.get("status")), raw_status=r.get("status"),
            started_at=_iso_minus_ms(r.get("ts"), r.get("duration_ms")), ended_at=r.get("ts"),
            duration_ms=r.get("duration_ms"), log_path=r.get("log_path"), stem=stem,
            detail=detail, ref=({"log": stem} if stem else None)))
    for r in _rows(conn, "SELECT id, conversation_id, n, ts, user_text, status, log_path,"
                         " finished_at, error FROM conversation_turns ORDER BY id DESC LIMIT ?", (limit,)):
        stem = _stem_of(r.get("log_path"))
        dur = None
        if r.get("ts") and r.get("finished_at"):
            a, b = _age_s(r["ts"]), _age_s(r["finished_at"])
            if a is not None and b is not None:
                dur = int(max(0.0, a - b) * 1000)
        text = str(r.get("user_text") or "").strip().split("\n")[0]
        items.append(_item(
            id=f"conversation:{r['id']}", source="conversation", kind="conversation", role="lead",
            agent=None, status=normalize_outcome(r.get("status"), error=r.get("error")),
            raw_status=r.get("status"), started_at=r.get("ts"), ended_at=r.get("finished_at"),
            duration_ms=dur, log_path=r.get("log_path"), stem=stem,
            detail=(text[:160] + ("…" if len(text) > 160 else "")) or None,
            ref={"conversation": r.get("conversation_id"), "turn": r.get("n")}))
    for r in _rows(conn, "SELECT id, phase, type, round, kind, status, started_at, finished_at,"
                         " duration_s, stem, reason FROM gates ORDER BY id DESC LIMIT ?", (limit,)):
        st = r.get("status")
        items.append(_item(
            id=(f"turn:{r['stem']}" if r.get("stem") else f"gate:{r['id']}"), source="gate", kind="gate", role="gatekeeper", agent="gate",
            phase=r.get("phase"), type=r.get("type"), round=r.get("round"),
            status=normalize_outcome(st), raw_status=st, started_at=r.get("started_at"),
            ended_at=r.get("finished_at"),
            duration_ms=(int(float(r["duration_s"]) * 1000) if r.get("duration_s") is not None else None),
            log_path=None, stem=r.get("stem"),
            detail=(f"{st}" + (f" — {r['reason']}" if r.get("reason") else "")) if st else None,
            ref=({"log": r["stem"]} if r.get("stem") else None)))
    for r in _rows(conn, "SELECT id, phase, type, round, kind, status, started_at, finished_at,"
                         " duration_s, stem, decision, reason FROM panels ORDER BY id DESC LIMIT ?", (limit,)):
        st = r.get("status")
        items.append(_item(
            id=(f"turn:{r['stem']}" if r.get("stem") else f"panel:{r['id']}"), source="panel", kind="panel", role="reviewer", agent="panel",
            phase=r.get("phase"), type=r.get("type"), round=r.get("round"),
            status=normalize_outcome(st), raw_status=st, started_at=r.get("started_at"),
            ended_at=r.get("finished_at"),
            duration_ms=(int(float(r["duration_s"]) * 1000) if r.get("duration_s") is not None else None),
            log_path=None, stem=r.get("stem"),
            detail=(f"{st}" + (f" — {r['decision']}" if r.get("decision") else "")
                    + (f" ({r['reason']})" if r.get("reason") else "")) if st else None,
            ref=({"log": r["stem"]} if r.get("stem") else None)))
    for r in _rows(conn, "SELECT id, status, intent_json, conversation_id, turn_n, created_at,"
                         " finished_at, error FROM launches WHERE status != 'succeeded'"
                         " ORDER BY id DESC LIMIT ?", (limit,)):
        try:
            intent = json.loads(r.get("intent_json") or "{}")
        except ValueError:
            intent = {}
        st = r.get("status")
        items.append(_item(
            id=f"launch:{r['id']}", source="launch", kind="launch", role="lead", agent=None,
            phase=intent.get("phase"), type=intent.get("type"), round=None,
            status=normalize_outcome(st, error=r.get("error")), raw_status=st,
            started_at=r.get("created_at"), ended_at=r.get("finished_at"), duration_ms=None,
            detail=(intent.get("command") or "launch") + (f" — {r['error']}" if r.get("error") else ""),
            ref=({"conversation": r["conversation_id"], "turn": r.get("turn_n")}
                 if r.get("conversation_id") else None)))
    return items


def _merge_inflight(items: list[dict], inflight: dict | None) -> None:
    """Fold the in-flight marker into the list: a running row it matches
    (by stem, or by conversation ref) is marked running with liveness;
    otherwise a new running item is prepended."""
    if not inflight:
        return
    pid = inflight.get("pid")
    alive = None
    try:
        from tagteam import procs
        alive = bool(isinstance(pid, int) and pid > 0 and procs.pid_alive(pid))
    except Exception:
        alive = None
    if pid is None:
        alive = None            # claimed, not yet spawned — not "gone"
    status = OUTCOME_PROCESS_GONE if alive is False else OUTCOME_RUNNING
    stem = inflight.get("stem")
    kind = inflight.get("kind") or "cycle"
    cid, tn = inflight.get("conversation_id"), inflight.get("turn_n")
    for it in items:
        same_stem = stem and it.get("stem") == stem
        same_conv = (kind == "conversation" and cid and it.get("ref")
                     and it["ref"].get("conversation") == cid and it["ref"].get("turn") == tn)
        if same_stem or same_conv:
            if it["status"] == OUTCOME_RUNNING or it["raw_status"] in ("running", "pending"):
                it["status"] = status
                it["pid_alive"] = alive
                it["log_path"] = it.get("log_path") or inflight.get("log_path")
                it["agent"] = it.get("agent") or inflight.get("agent")
                it["stem"] = it.get("stem") or stem
                if it["ref"] is None and stem:
                    it["ref"] = {"log": stem}
            return
    akind = {"cycle": "cycle", "conversation": "conversation", "briefer": "briefer",
             "gate": "gate", "panel": "panel"}.get(kind, kind)
    items.insert(0, _item(
        id=(f"turn:{stem}" if stem else "inflight:slot"), source="inflight", kind=akind, role=inflight.get("role"),
        agent=inflight.get("agent") or inflight.get("provider"), phase=inflight.get("phase"),
        type=inflight.get("type"), round=inflight.get("round"), status=status,
        raw_status="running", started_at=inflight.get("started_at"), ended_at=None,
        duration_ms=None, log_path=inflight.get("log_path"), stem=stem, pid_alive=alive,
        detail=None,
        ref=({"conversation": cid, "turn": tn} if (kind == "conversation" and cid)
             else ({"log": stem} if stem else None))))


def activity_payload(project_dir: str | Path, limit: int = ACTIVITY_DEFAULT_LIMIT) -> dict:
    """{items: [...], truncated} — every recorded agent turn for the project,
    newest first (running first among equals), each with a normalised
    `status` from OUTCOMES, a stable `id` (`turn:<stem>` for any turn with a
    log stem — so the in-flight row and its later record are ONE row —
    else `<source>:<rowid>`), and a `ref` the UI can open (`{"log": stem}`
    or `{"conversation": cid, "turn": n}`)."""
    root = Path(project_dir)
    try:
        limit = max(1, min(int(limit or ACTIVITY_DEFAULT_LIMIT), ACTIVITY_MAX_LIMIT))
    except (TypeError, ValueError):
        limit = ACTIVITY_DEFAULT_LIMIT
    items: list[dict] = []
    try:
        from tagteam import db
        conn = db.connect(project_dir=str(root))
        try:
            items = _activity_from_db(conn, limit + 1)
        finally:
            conn.close()
    except Exception:
        items = []
    _merge_inflight(items, h.read_inflight(root))
    # one row per id: a stem-bearing turn appears once whatever recorded it
    # (a terminal record wins over a running one; otherwise first wins)
    by_id: dict = {}
    for it in items:
        cur = by_id.get(it["id"])
        if cur is None:
            by_id[it["id"]] = it
        elif cur["status"] in (OUTCOME_RUNNING, OUTCOME_PROCESS_GONE) and it["status"] not in (OUTCOME_RUNNING, OUTCOME_PROCESS_GONE):
            by_id[it["id"]] = it
    items = list(by_id.values())
    # a launch that reached its lead turn IS that conversation turn (the
    # turn row carries the outcome and the log); keep launch rows only for
    # launches that never got a turn (still claiming, or failed before one)
    conv_refs = {(it["ref"].get("conversation"), it["ref"].get("turn")) for it in items
                 if it["kind"] == "conversation" and it.get("ref") and it["ref"].get("conversation")}
    items = [it for it in items if not (it["kind"] == "launch" and it.get("ref")
                                        and (it["ref"].get("conversation"), it["ref"].get("turn")) in conv_refs)]
    for it in items:
        it["age_s"] = _age_s(it.get("started_at"))
    def _key(it):
        running = it["status"] in (OUTCOME_RUNNING, OUTCOME_PROCESS_GONE)
        return (1 if running else 0, str(it.get("started_at") or ""), str(it.get("id")))
    items.sort(key=_key, reverse=True)
    truncated = len(items) > limit
    return {"items": items[:limit], "truncated": truncated, "limit": limit}


def last_turn(items: list[dict]) -> dict | None:
    """The newest terminal agent turn (launch rows are not turns)."""
    for it in items:
        if it.get("kind") == "launch":
            continue
        if it.get("status") in (OUTCOME_RUNNING, OUTCOME_PROCESS_GONE):
            continue
        return it
    return None


def launch_view(project_dir: str | Path) -> dict | None:
    """The launch the Start card must acknowledge, or None. The persisted
    row's status is finalised lazily by the launcher, so the *effective*
    status is derived here: pending → its lead turn (running → pending,
    ok → gone, failed/cancelled → failed, no turn + owner gone → failed);
    failed → shown only while it is recent (24 h) and for the CURRENT
    intent; succeeded → None."""
    root = Path(project_dir)
    try:
        from tagteam import db
        conn = db.connect(project_dir=str(root))
        try:
            rows = _rows(conn, "SELECT id, key, status, intent_json, conversation_id, turn_n,"
                               " created_at, updated_at, finished_at, error, owner_pid, owner_ident"
                               " FROM launches ORDER BY id DESC LIMIT 1")
            row = rows[0] if rows else None
            turn = None
            if row and row.get("conversation_id") and row.get("turn_n") is not None:
                t = _rows(conn, "SELECT status, error, log_path, finished_at FROM conversation_turns"
                                " WHERE conversation_id = ? AND n = ?",
                          (row["conversation_id"], int(row["turn_n"])))
                turn = t[0] if t else None
        finally:
            conn.close()
    except Exception:
        return None
    if not row:
        return None
    try:
        intent = json.loads(row.get("intent_json") or "{}")
    except ValueError:
        intent = {}
    status, error, finished = row.get("status"), row.get("error"), row.get("finished_at")
    if status == "pending":
        if turn is not None:
            ts = turn.get("status")
            if ts == "running":
                status = "pending"
            elif ts == "ok":
                status = "succeeded"
            else:
                status, error = "failed", f"lead turn {ts}: {turn.get('error') or 'no reply'}"
                finished = turn.get("finished_at")
        else:
            gone = False
            try:
                from tagteam import launch as _launch
                gone = _launch._owner_gone(row.get("owner_pid"), row.get("owner_ident"))
            except Exception:
                gone = False
            if gone:
                status, error = "failed", "orphaned: the launching process died"
    if status == "succeeded":
        return None
    if status == "failed":
        age = _age_s(finished or row.get("updated_at") or row.get("created_at"))
        if age is None or age > 86400:
            return None
        try:
            from tagteam import launch as _launch
            # Phase 69: the board can start any ready phase — compare against the
            # intent for THIS row's own phase, not the default next phase
            if _launch.launch_key(_launch.launch_intent(root, phase=intent.get("phase"))) != row.get("key"):
                return None
        except Exception:
            return None
    return {"status": status, "command": intent.get("command"), "phase": intent.get("phase"),
            "type": intent.get("type"), "conversation_id": row.get("conversation_id"),
            "turn_n": row.get("turn_n"), "created_at": row.get("created_at"),
            "age_s": _age_s(row.get("created_at")), "finished_at": finished, "error": error,
            "log_path": (turn or {}).get("log_path")}


# ---------------------------------------------------------------------------
# /api/tail
# ---------------------------------------------------------------------------

def turn_log_path(project_dir: str | Path, stem: str | None, events: bool = False) -> Path | None:
    """`.tagteam/turns/<stem>.log` for a validated stem (strictly under the
    turns dir; no separators, no traversal), or None."""
    import re as _re
    if not stem or not _re.match(_STEM_RE_TEXT, str(stem)) or ".." in str(stem):
        return None
    d = h.turns_dir(project_dir).resolve()
    p = (d / f"{stem}{'.events.jsonl' if events else '.log'}")
    try:
        if p.resolve().parent != d:
            return None
    except OSError:
        return None
    return p


def tail_payload(project_dir: str | Path, lines: int = DEFAULT_TAIL_LINES,
                 events: bool = False, stem: str | None = None) -> dict:
    """Last N lines of the in-flight turn log (or the most recent) — the
    same resolution as `tagteam tail --no-follow`. Phase 43: `stem=` reads
    that turn's log only (a finished activity row's [log])."""
    root = Path(project_dir)
    lines = max(1, min(int(lines or DEFAULT_TAIL_LINES), MAX_TAIL_LINES))
    inflight = h.read_inflight(root)
    if stem is not None:
        target = turn_log_path(root, stem, events)
        if target is None or not target.exists():
            return {"path": None, "lines": [], "inflight": bool(inflight and inflight.get("stem") == stem),
                    "stem": stem, "message": f"no turn log for {stem!r}"}
        text = h._tail_lines(target, lines)
        return {"path": str(target), "lines": text.splitlines() if text else [],
                "inflight": bool(inflight and inflight.get("stem") == stem), "stem": stem, "message": None}
    target = None
    if inflight is not None:
        try:
            target = Path(inflight["events_path" if events else "log_path"])
        except KeyError:
            target = None
    if target is None or not target.exists():
        target = h._latest_log(root, events)
    if target is None:
        return {"path": None, "lines": [], "inflight": inflight is not None,
                "stem": (inflight or {}).get("stem"), "message":
                "No headless turn logs found (.tagteam/turns/ is empty). "
                "Start the watcher with `tagteam watch --mode headless`."}
    text = h._tail_lines(target, lines)
    return {"path": str(target), "lines": text.splitlines() if text else [],
            "inflight": inflight is not None, "stem": (inflight or {}).get("stem"),
            "message": None}


# ---------------------------------------------------------------------------
# SSE signature
# ---------------------------------------------------------------------------

def events_signature(project_dir: str | Path) -> dict:
    """Cheap change signals sampled by the SSE loop: state seq, rounds count
    of the current cycle, max ids of interjections / briefs / usage /
    rate_limits, briefs status digest, pause marker, inflight stem+pid."""
    from tagteam.state import read_state
    root = Path(project_dir)
    state = read_state(str(root)) or {}
    phase, ctype = state.get("phase"), state.get("type")
    sig: dict = {"seq": state.get("seq"), "phase": phase, "type": ctype,
                 "turn": state.get("turn"), "status": state.get("status"),
                 "rounds": None, "interjections": None, "briefs": None,
                 "briefs_status": None, "usage": None, "rate_limits": None,
                 "paused": False, "inflight": None}
    if phase and ctype:
        for d in (root / "docs" / "handoffs", root / ".tagteam" / "legacy"):
            rp = d / f"{phase}_{ctype}_rounds.jsonl"
            if rp.exists():
                try:
                    st = rp.stat()
                    sig["rounds"] = [st.st_size, int(st.st_mtime_ns // 1_000_000)]
                except OSError:
                    pass
                break
    try:
        from tagteam import db
        conn = db.connect(project_dir=str(root))
        try:
            def _max(table):
                try:
                    return conn.execute(f"SELECT MAX(id) FROM {table}").fetchone()[0]
                except Exception:
                    return None
            sig["interjections"] = _max("interjections")
            sig["briefs"] = _max("briefs")
            sig["usage"] = _max("usage")
            sig["rate_limits"] = _max("rate_limits")
            try:
                sig["briefs_status"] = conn.execute(
                    "SELECT COALESCE(SUM(CASE status WHEN 'running' THEN 1 ELSE 0 END),0),"
                    " COALESCE(SUM(CASE status WHEN 'ok' THEN 1 ELSE 0 END),0),"
                    " COALESCE(SUM(CASE status WHEN 'partial' THEN 1 ELSE 0 END),0),"
                    " COALESCE(SUM(CASE status WHEN 'failed' THEN 1 ELSE 0 END),0),"
                    " COALESCE(SUM(CASE status WHEN 'abandoned' THEN 1 ELSE 0 END),0)"
                    " FROM briefs").fetchone()
                sig["briefs_status"] = list(sig["briefs_status"]) if sig["briefs_status"] else None
            except Exception:
                sig["briefs_status"] = None
            # retirement / delivery change rows without changing max(id)
            try:
                sig["interjections_state"] = conn.execute(
                    "SELECT COALESCE(SUM(delivered_ts IS NOT NULL),0),"
                    " COALESCE(SUM(retired_ts IS NOT NULL),0) FROM interjections").fetchone()
                sig["interjections_state"] = list(sig["interjections_state"])
            except Exception:
                sig["interjections_state"] = None
            try:
                sig["rate_limits_ts"] = conn.execute("SELECT MAX(ts) FROM rate_limits").fetchone()[0]
            except Exception:
                sig["rate_limits_ts"] = None
            # Phase 43: a lead-conversation turn starting / ending and a
            # launch appearing / finalising are changes the page must see.
            try:
                sig["conversation_turns"] = list(conn.execute(
                    "SELECT MAX(id), COALESCE(SUM(status = 'running'),0), MAX(finished_at)"
                    " FROM conversation_turns").fetchone())
            except Exception:
                sig["conversation_turns"] = None
            try:
                sig["launches"] = list(conn.execute(
                    "SELECT MAX(id), COALESCE(SUM(status = 'pending'),0), MAX(updated_at)"
                    " FROM launches").fetchone())
            except Exception:
                sig["launches"] = None
        finally:
            conn.close()
    except Exception:
        pass
    sig["paused"] = h.pause_path(root).exists()
    inflight = h.read_inflight(root)
    if inflight is not None:
        pid = inflight.get("pid")
        alive = None
        try:
            from tagteam import procs
            alive = bool(isinstance(pid, int) and pid > 0 and procs.pid_alive(pid))
        except Exception:
            alive = None
        # Phase 43: log growth in LOG_SIGNAL_STEP-byte steps — coarse on
        # purpose (the running row streams its own lines; the global signal
        # only has to move while the engine does, not per line).
        log_step = None
        try:
            lp = inflight.get("log_path")
            if lp:
                log_step = int(Path(str(lp)).stat().st_size // LOG_SIGNAL_STEP)
        except (OSError, TypeError, ValueError):
            log_step = None
        sig["inflight"] = {"stem": inflight.get("stem"), "pid": pid, "alive": alive,
                           "kind": inflight.get("kind"), "log_step": log_step,
                           "age_s": int(_age_s(inflight.get("started_at")) or 0)}
    # watcher liveness (pidfile pid alive?) — a watcher dying is a change too
    try:
        from tagteam import watcher as watcher_mod
        from tagteam import procs
        rec = watcher_mod.read_pidfile(root)
        if rec is not None:
            wpid = rec.get("pid")
            sig["watcher"] = {"pid": wpid, "alive": bool(isinstance(wpid, int) and wpid > 0
                                                          and procs.pid_alive(wpid))}
        else:
            sig["watcher"] = None
    except Exception:
        sig["watcher"] = None
    return sig


def signature_id(sig: dict) -> str:
    """Stable id for an SSE frame — hash of the signature WITHOUT the
    volatile inflight age (so an unchanged turn does not re-fire)."""
    stable = dict(sig)
    if stable.get("inflight"):
        stable["inflight"] = {k: v for k, v in stable["inflight"].items() if k != "age_s"}
    raw = json.dumps(stable, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Write wrappers (thin: identical behavior to the CLI commands)
# ---------------------------------------------------------------------------

def _run(fn, args: list[str], project_dir: str | Path) -> dict:
    """Run a Phase 32/33 command function with a captured `out`; map the
    exit code to {ok, message}. Never raises."""
    buf = io.StringIO()
    try:
        rc = fn(args, project_root=str(project_dir), out=buf)
    except Exception as e:  # contract: never surfaces a traceback
        return {"ok": False, "message": f"{type(e).__name__}: {e}", "rc": 1,
                "cli": _cli_line(fn, args)}
    return {"ok": rc == 0, "message": buf.getvalue().strip(), "rc": rc,
            "cli": _cli_line(fn, args)}


_FN_NAMES = {"pause_command": "pause", "resume_command": "resume",
             "interject_command": "interject", "cancel_turn_command": "cancel-turn",
             "rule_command": "rule", "brief_command": "brief", "orders_command": "orders",
             "config_command": "config"}


def _cli_line(fn, args: list[str]) -> str:
    import shlex
    name = _FN_NAMES.get(getattr(fn, "__name__", ""), getattr(fn, "__name__", "?"))
    return "tagteam " + " ".join([name] + [shlex.quote(a) for a in args])


def cli_preview(action: str, params: dict) -> str:
    """The exact CLI line an action would run (shown in confirmations)."""
    fn, args = _plan(action, params, by=web_user())
    return _cli_line(fn, args)


def _plan(action: str, params: dict, *, by: str):
    """Map (action, params) → (command function, argv). Raises ValueError
    for unknown actions / bad params (mapped to 400 by the server)."""
    from tagteam import controls, briefer
    params = params or {}

    def _s(key, default=""):
        v = params.get(key, default)
        return ("" if v is None else str(v)).strip()

    if action == "pause":
        args = ["--by", by]
        reason = _s("reason")
        if reason:
            args = ["--reason", reason] + args
        return controls.pause_command, args
    if action == "resume":
        return controls.resume_command, []
    if action == "interject":
        note = _s("note")
        if not note:
            raise ValueError("'note' is required")
        args = [note, "--by", by]
        to = _s("to")
        if to:
            if to not in ("lead", "reviewer"):
                raise ValueError("'to' must be lead or reviewer")
            args += ["--to", to]
        return controls.interject_command, args
    if action == "interject/retire":
        try:
            rid = int(params.get("id"))
        except (TypeError, ValueError):
            raise ValueError("'id' must be an integer")
        return controls.interject_command, ["--retire", str(rid), "--by", by]
    if action == "cancel-turn":
        return controls.cancel_turn_command, ["--by", by]
    if action == "brief/generate":
        return briefer.brief_command, ["--generate"]
    if action == "rule":
        verb = _s("ruling")
        if verb not in ("approve", "request-changes", "answer"):
            raise ValueError("'ruling' must be approve, request-changes or answer")
        args = [verb]
        content = _s("content")
        if content:
            args += ["--content", content]
        elif verb != "approve":
            raise ValueError(f"'{verb}' requires 'content'")
        args += ["--by", by]
        to = _s("to")
        if verb == "answer":
            args += ["--to", to or "reviewer"]
        elif to:
            raise ValueError("'to' only applies to answer")
        return controls.rule_command, args
    if action == "orders":                 # Phase 71: the Rules tab
        return _plan_orders(params, by)
    if action == "config/set":             # Phase 71b: safe tagteam.yaml edits
        return _plan_config(params, by)
    raise ValueError(f"Unknown action: {action}")


def run_action(action: str, params: dict, project_dir: str | Path,
               by: str | None = None) -> dict:
    """Perform a cockpit control. Returns {ok, message, rc, cli}. Unknown
    action / invalid params → {ok: False, error: ..., rc: 400}."""
    try:
        fn, args = _plan(action, params, by=by or web_user())
    except ValueError as e:
        return {"ok": False, "message": str(e), "rc": 400, "cli": None}
    if action == "config/set" and (params or {}).get("preview") is True:
        return config_preview(project_dir, params)
    return _run(fn, args, project_dir)


def config_preview(project_dir: str | Path, params: dict) -> dict:
    """Phase 71b: the preview a confirmation shows — the diff, what the
    engine will do, and `base` (the sha256 the confirmed write must send
    back). A read: no lock, nothing written."""
    from tagteam import config_edit as ce
    key, value = params["key"], params["value"]
    try:
        p = ce.preview(project_dir, key, value)
    except ce.Refused as e:
        return {"ok": False, "message": str(e), "rc": 1, "cli": None}
    text = ce._fmt_plan(p, preview_only=True, by=None, root=Path(project_dir))
    kind = ce.SAFE_KEYS[key][0]
    return {"ok": True, "noop": p.noop, "diff": p.diff, "base": p.base, "engine": p.engine,
            "notes": p.notes, "message": text,
            "cli": f"tagteam config set {key} {ce.render(kind, value)} --expect {p.base}"}


def watcher_events_payload(project_dir: str | Path, n: int = 50, chatter: bool = True) -> dict:
    """GET /api/watcher/events — the newest `n` watcher events, oldest first.
    `chatter=0` (Phase 68) leaves the routine `info` lines out before counting."""
    from tagteam import watchlog
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 50
    return {"events": watchlog.read(project_dir, max(1, min(n, 500)), include_info=bool(chatter))}


# ---------------------------------------------------------------------------
# Phase 71: GET /api/rules — what governs this project's runs
# ---------------------------------------------------------------------------
# Every engine row comes from the resolver the engine itself uses (never a
# raw `get_*_spec` getter, which assumes a validated block), so a row states
# what RUNS. File-only: no database, nothing created, allowed read-only.

ORDER_PRESETS = {
    "stop": [
        {"value": "phase", "label": "Stop after each phase for my review", "recommended": True},
        {"value": "roadmap", "label": "Run to the end of the roadmap unless there is a question",
         "recommended": False},
    ],
    "advisory": [
        {"text": "Commit at the end of each phase, but hold the PR for my approval", "recommended": True},
        {"text": "Open the PR when the phase is done", "recommended": False},
        {"text": "Keep commits small and describe how each change was verified", "recommended": False},
    ],
}
ORDER_TEXT_MAX = 500

_TYPE_WORDS = {"impl": "implementation reviews", "plan": "plan reviews"}
_APPLIES = {"submit": "each submission", "approval": "each approval", "watcher": "when the watcher starts"}


def _types_words(types) -> str:
    types = [t for t in (types or []) if t in _TYPE_WORDS]
    if set(types) >= {"impl", "plan"}:
        return "every review"
    if len(types) == 1:
        return f"{_TYPE_WORDS[types[0]]} only"
    return "no review type"


_EDIT_LABELS = {"gatekeeper.enabled": "Gate", "gatekeeper.on_submit": "Runs at submission",
                "panel.enabled": "Panel", "briefer.enabled": "Brief", "watcher.resend_minutes": "Minutes"}


def _edits(config: dict, keys: list[str], effective_main, problems) -> list[dict]:
    """Phase 71b: the row's editable keys with their SAVED values. The
    control shows `saved`; `not_in_effect` says why the engine differs."""
    from tagteam import config_edit as ce
    out = []
    for i, key in enumerate(keys):
        state, value = ce.saved(config, key)
        kind = ce.SAFE_KEYS[key][0]
        e = {"key": key, "kind": kind, "label": _EDIT_LABELS[key], "saved_state": state,
             "saved": value if state == "set" else None, "not_in_effect": None}
        if i == 0 and state == "set" and value != effective_main:
            e["not_in_effect"] = (f"saved: {ce.render(kind, value)} · not in effect: "
                                  + ("; ".join(problems) or "the engine uses a different value"))
        out.append(e)
    return out


def _gate_row(config: dict) -> dict:
    from tagteam.gatekeeper import resolve_gatekeeper
    g = resolve_gatekeeper(config)
    applies = "submit" if g.on_submit else "watcher"
    edits = _edits(config, ["gatekeeper.enabled", "gatekeeper.on_submit"], g.enabled, g.problems)
    row = {"key": "gate", "label": "Gate before the reviewer's turn", "on": g.enabled, "edits": edits,
           "source": "tagteam.yaml gatekeeper", "change": "gatekeeper.enabled",
           "applies": _APPLIES[applies], "warnings": [f"gatekeeper: {p}" for p in g.problems]}
    if not g.enabled:
        row["value"] = "off"
        row["text"] = "No gate: the reviewer's turn starts as soon as the lead submits."
        if g.problems:
            row["text"] += " (The gatekeeper block is invalid, so the gate is OFF.)"
        return row
    checks = ["the test suite" if g.tests_command else "no tests (no test command configured)",
              "the implementation-scope check" if g.scope else "no scope check (scope is off)",
              "the plan-doc check"]
    when = ("inside the lead's submission" if g.on_submit
            else "in the watcher, before it hands the turn to the reviewer")
    row["value"] = f"on · {_types_words(g.on)}"
    row["text"] = (f"On {_types_words(g.on)}, {when}: {', '.join(checks)}. A failure hands the "
                   f"turn back to the lead; after {g.max_bounces} bounces in a round the reviewer "
                   f"gets the turn with the failures.")
    return row


def _panel_row(config: dict, root: Path) -> dict:
    from tagteam.panel import resolve_panel
    p = resolve_panel(config, root)
    edits = _edits(config, ["panel.enabled"], p.enabled, p.problems)
    # Any problem with a PRESENT block is shown — a malformed mapping or enable value included
    # (impl r1 review); an absent block, or a valid disabled one, has none and stays quiet.
    row = {"key": "panel", "label": "Reviewer panel", "on": p.enabled, "edits": edits,
           "source": "tagteam.yaml panel", "change": "panel.enabled",
           "applies": _APPLIES["watcher"],
           "warnings": [f"panel: {x}" for x in p.problems] if "panel" in config else []}
    if not p.enabled:
        row["value"] = "off"
        row["text"] = "One reviewer takes every review turn."
        if row["warnings"]:
            row["text"] += " (The panel is configured but cannot run, so it is OFF.)"
        return row
    phases = f", phases {', '.join(p.phases)} only" if p.phases else ""
    row["value"] = f"on · {len(p.lenses)} lenses"
    row["text"] = (f"On {_types_words(p.on)}{phases}, {len(p.lenses)} lens reviews "
                   f"({', '.join(p.lens_names)}) take the reviewer's turn; their verdicts are "
                   f"merged into one reviewer entry.")
    return row


def _briefer_row(config: dict, root: Path) -> dict:
    from tagteam.briefer import resolve_briefer
    b = resolve_briefer(config, root)
    edits = _edits(config, ["briefer.enabled"], b.enabled, b.problems)
    row = {"key": "briefer", "label": "Escalation brief", "on": b.enabled, "edits": edits,
           "source": "tagteam.yaml briefer", "change": "briefer.enabled",
           "applies": _APPLIES["watcher"],
           "warnings": [f"briefer: {x}" for x in b.problems] if "briefer" in config else []}
    if not b.enabled:
        row["value"] = "off"
        row["text"] = "No automatic decision brief when a cycle escalates."
        if row["warnings"]:
            row["text"] += " (The briefer is configured but cannot run, so it is OFF.)"
        return row
    row["value"] = f"on · {b.provider}"
    row["text"] = (f"When a cycle escalates, {b.provider} writes a decision brief for you "
                   f"(`tagteam brief`).")
    return row


def _resend_row(config: dict) -> dict:
    from tagteam.config import resolve_watcher
    minutes, problems = resolve_watcher(config)
    text = ("A turn is never re-sent." if minutes == 0 else
            f"If a turn is still owed after {minutes} min, a terminal-mode watcher types the "
            f"command into that terminal again (a headless watcher never re-sends).")
    if problems:
        text += " (The watcher block is invalid, so the default applies.)"
    return {"key": "resend", "label": "Re-send a stuck turn", "on": minutes > 0,
            "edits": _edits(config, ["watcher.resend_minutes"], minutes, problems),
            "value": "never" if minutes == 0 else f"{minutes} min", "text": text,
            "source": "tagteam.yaml watcher" if isinstance(config.get("watcher"), dict) and not problems
            else "default", "change": "watcher.resend_minutes", "applies": _APPLIES["watcher"],
            "warnings": [f"watcher: {x}" for x in problems]}


def _stale_row() -> dict:
    from tagteam.cycle import STALE_ROUND_LIMIT
    return {"key": "stale", "label": "Auto-escalation", "on": True,
            "value": f"{STALE_ROUND_LIMIT} stale rounds",
            "text": (f"{STALE_ROUND_LIMIT} consecutive unchanged re-submissions escalate the "
                     f"cycle to you. A round number alone never does."),
            "source": "built in", "change": None, "applies": _APPLIES["submit"], "warnings": []}


def _stop_block(state: dict, eff: dict) -> dict:
    project = (eff.get("project") or {}).get("stop")
    run = (eff.get("run") or {}).get("stop") if eff.get("run") else None
    source = eff["stop_source"]
    shadowed = source if (source in ("run", "run-mode") and project is not None
                          and project != eff["stop"]) else None
    what = {"phase": "the run stops after each phase", "roadmap": "the run goes on through the roadmap"}
    why = {"run": "set for this run", "run-mode": "this is a full-roadmap run",
           "project": "project order", "default": "default — no order set"}[source]
    note = None
    if shadowed:
        by = "set for this run" if shadowed == "run" else "this is a full-roadmap run"
        note = (f"This run uses “{eff['stop']}” ({by}), so your project setting "
                f"“{project}” applies from the next run.")
    return {"effective": eff["stop"], "source": source, "project": project, "run": run,
            "run_mode_roadmap": state.get("run_mode") == "full-roadmap",
            "project_shadowed_by": shadowed,
            "has_run_override": eff.get("run") is not None,
            "effective_text": f"Now: {what[eff['stop']]} ({why}).",
            "shadow_note": note}


def _watcher_stale_config(root: Path) -> dict | None:
    """A running watcher that started before tagteam.yaml last changed still
    uses the settings it loaded. The page cannot see those settings."""
    from tagteam import watchlog
    try:
        view = watchlog.beat_view(root)
        if view.get("state") in ("none", "previous"):
            return None
        beat = watchlog.read_beat(root) or {}
        started = beat.get("started_at")
        cfg = root / "tagteam.yaml"
        if not started or not cfg.is_file():
            return None
        st = datetime.fromisoformat(str(started))
        if st.tzinfo is None:
            st = st.replace(tzinfo=timezone.utc)
        mtime = datetime.fromtimestamp(cfg.stat().st_mtime, tz=timezone.utc)
        if mtime > st:
            return {"started_at": st.isoformat(), "config_mtime": mtime.isoformat(),
                    "pid": beat.get("pid")}
    except Exception:
        return None
    return None


def rules_payload(project_dir: str | Path) -> dict:
    from tagteam import orders
    from tagteam.config import read_config
    from tagteam.state import read_state
    root = Path(project_dir)
    warnings: list[str] = []
    cfg_path = root / "tagteam.yaml"
    config = read_config(cfg_path) if cfg_path.is_file() else None
    if cfg_path.is_file() and not isinstance(config, dict):
        warnings.append("tagteam.yaml could not be read — the rows show the engine's defaults")
    config = config if isinstance(config, dict) else {}
    state = read_state(str(root)) or {}
    eff = orders.effective(state, root)
    if eff["warn"]:
        warnings.append(f"{eff['warn']} — treated as no project orders")
    stop = _stop_block(state, eff)
    stop_row = {"key": "stop", "label": "When a run stops for you", "on": True,
                "value": eff["stop"], "text": orders._STOP_WORDS[eff["stop"]],
                "source": {"run": "standing order (this run)", "run-mode": "this full-roadmap run",
                           "project": "standing order (project)", "default": "default"}[eff["stop_source"]],
                "change": "orders", "applies": _APPLIES["approval"], "warnings": []}
    enforced = [stop_row]
    for build in (lambda: _gate_row(config), lambda: _panel_row(config, root),
                  lambda: _briefer_row(config, root), lambda: _resend_row(config), _stale_row):
        try:
            enforced.append(build())
        except Exception as e:   # one broken row never hides the others
            warnings.append(f"a rule could not be read ({type(e).__name__})")
    for r in enforced:
        warnings.extend(r.get("warnings") or [])
    advisory = [{"id": n["id"], "scope": n["source"], "text": n["text"], "by": n.get("by")}
                for n in eff["advisory"]]
    return {"enforced": enforced, "advisory": advisory, "stop": stop,
            "last_decision": orders.describe_decision(orders.current_decision(state, root)),
            "presets": ORDER_PRESETS, "warnings": warnings,
            "watcher_stale_config": _watcher_stale_config(root),
            "orders_file": orders.ORDERS_FILE, "text_max": ORDER_TEXT_MAX,
            # a malformed/unsafe file is refused by every project write — say so, don't invite one
            "project_orders_ok": eff["warn"] is None}


def _plan_config(params: dict, by: str):
    """POST /api/config/set → `tagteam config set KEY VALUE --expect SHA`.
    Strict JSON types (the Phase 71 r1 lesson): a bool key takes a JSON
    boolean, `resend_minutes` a non-negative JSON integer. A confirmed write
    must carry the preview's `expect` (its `base`)."""
    from tagteam import config_edit as ce
    key = params.get("key")
    if not isinstance(key, str) or key not in ce.SAFE_KEYS:
        raise ValueError(f"'key' must be one of: {', '.join(ce.SAFE_KEYS)}")
    kind = ce.SAFE_KEYS[key][0]
    value = params.get("value")
    if kind == "bool" and not isinstance(value, bool):
        raise ValueError(f"'{key}' takes a JSON boolean")
    if kind == "minutes" and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise ValueError(f"'{key}' takes a whole number of minutes >= 0")
    args = ["set", key, ce.render(kind, value)]
    if params.get("preview") is True:
        return ce.config_command, args + ["--preview"]
    expect = params.get("expect")
    if not isinstance(expect, str) or not _re.fullmatch(r"[0-9a-f]{64}", expect):
        raise ValueError("'expect' (the preview's base) is required to write")
    return ce.config_command, args + ["--expect", expect, "--by", by]


def _plan_orders(params: dict, by: str):
    """POST /api/orders → `tagteam orders …` argv."""
    from tagteam import orders
    op = str(params.get("op") or "").strip()
    # A supplied scope must be a real boolean: "true" / 1 must not quietly mean the PROJECT.
    if "run" in params and params["run"] is not None and not isinstance(params["run"], bool):
        raise ValueError("'run' must be true or false")
    run = params.get("run") is True
    tail = (["--run"] if run else [])
    if op == "stop":
        value = str(params.get("value") or "").strip()
        if value not in ("phase", "roadmap", "unset"):
            raise ValueError("'value' must be phase, roadmap or unset")
        return orders.orders_command, ["stop", "--unset" if value == "unset" else value] + tail + ["--by", by]
    if op == "add":
        text = params.get("text")
        text = "" if text is None else str(text).strip()
        if not text:
            raise ValueError("'text' is required")
        if "\n" in text or "\r" in text:
            raise ValueError("'text' must be one line")
        if len(text) > ORDER_TEXT_MAX:
            raise ValueError(f"'text' is limited to {ORDER_TEXT_MAX} characters")
        return orders.orders_command, ["add", text] + tail + ["--by", by]
    if op == "remove":
        raw = params.get("id")
        # an actual integer (or an integer string) — never a bool, never a truncated float
        if isinstance(raw, bool) or not (isinstance(raw, int) or (isinstance(raw, str) and raw.strip().isdigit())):
            raise ValueError("'id' must be an integer")
        return orders.orders_command, ["remove", str(int(raw))] + tail
    if op == "clear-run":
        return orders.orders_command, ["clear", "--run"]
    raise ValueError("'op' must be stop, add, remove or clear-run")



# ---------------------------------------------------------------------------
# Phase 69: GET /api/roadmap — the board plus ONE board-wide launch guard
# ---------------------------------------------------------------------------

def launch_availability(project_dir: str | Path, now: dict | None = None) -> dict:
    """{available, reason}: may the page offer ANY Start right now? The
    facts the old Start card used, server-side: an in-flight turn (a lead
    conversation turn included), a pending launch, or a headline that says a
    turn is starting / working / launching. Also enforced by `launch()`."""
    n = now if now is not None else now_payload(project_dir)
    inflight = n.get("inflight")
    if inflight:
        kind = inflight.get("kind") or ""
        who = "the lead is busy in a conversation" if kind in ("lead", "conversation", "chat") \
            else f"a turn is running ({kind or 'turn'})"
        return {"available": False, "reason": who + " — Start is offered again when it ends"}
    launch = n.get("launch") or {}
    if launch.get("status") == "pending":
        return {"available": False, "reason": "a start is in progress — wait for it to finish"}
    hl = (n.get("headline") or {}).get("state")
    if hl in ("working", "starting", "launching"):
        return {"available": False, "reason": "a turn is starting or running — Start is offered again when it ends"}
    return {"available": True, "reason": ""}


def roadmap_payload(project_dir: str | Path) -> dict:
    from tagteam import roadmap as _rm
    b = _rm.board(project_dir)
    n = now_payload(project_dir)
    b["launch"] = launch_availability(project_dir, n)
    lv = n.get("launch") or {}
    if lv.get("status") == "failed" and lv.get("phase"):          # a failed Start shows on its row
        for rows in b["groups"].values():
            for r in rows:
                if r["slug"] == lv["phase"]:
                    r["last_failed"] = {"error": lv.get("error"), "finished_at": lv.get("finished_at"),
                                        "log_path": lv.get("log_path")}
    return b
