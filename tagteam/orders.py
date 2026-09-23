"""Phase 70: standing orders.

The arbiter's run-level instructions, made durable. Two kinds, kept apart in
storage, output and delivered text:

- **enforced** — `stop`: when the run stops for the arbiter. `phase` (after
  each phase's implementation is approved — today's single-phase behaviour)
  or `roadmap` (go on to the next ready roadmap phase; stop at the end of the
  roadmap). Escalations, questions and a blocked roadmap stop a run under
  both. The engine applies it, at the impl-approval write (`decide()` in
  `cycle.add_round`, `apply_decision()` in `cycle._derive_top_level_state`).
- **advisory** — numbered free-text notes (git, PR conduct, …). Delivered to
  both agents in every turn; tagteam cannot enforce them (it runs no git).

Where they live: project orders in a committed `tagteam-orders.json` beside
`tagteam.yaml`; a per-run override as the `orders` key of
`handoff-state.json`, dropped when its run ends. Orders are *explicit* when
the file holds a stop or a note, or the state carries an override; without
explicit orders nothing here writes or prints anything (the no-orders
promise, pinned by tests).

`effective()` is the one resolution; every surface calls it.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

ORDERS_FILE = "tagteam-orders.json"
STOP_VALUES = ("phase", "roadmap")
MAX_BYTES = 64 * 1024
BLOCK_BUDGET = 4_000
BLOCK_HEADER = "=== STANDING ORDERS ==="
DEFAULT_STOP = "phase"

# Outcomes recorded as `run_decision` on an approved impl cycle's status.
ENDS_RUN = ("stop", "complete")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _valid_notes(raw) -> list[dict] | None:
    if raw is None:
        return []
    if not isinstance(raw, list):
        return None
    notes = []
    for n in raw:
        if (not isinstance(n, dict) or not isinstance(n.get("id"), int)
                or isinstance(n.get("id"), bool)
                or not isinstance(n.get("text"), str) or not n["text"].strip()):
            return None
        notes.append({"id": n["id"], "text": n["text"], "by": n.get("by"), "ts": n.get("ts")})
    return notes


def load_project(project_dir: str | Path) -> tuple[dict, str | None]:
    """(orders, warning). A missing file is `({}, None)`; a symlink, a
    non-regular file, an oversized, unparseable or malformed file is
    `({}, "…")` — never an exception (this runs on the approval write)."""
    from tagteam import safe_read
    try:
        r = safe_read.read_bounded(Path(project_dir), ORDERS_FILE, MAX_BYTES, truncate=False)
    except Exception as e:                      # pragma: no cover - belt and braces
        return {}, f"{ORDERS_FILE} unreadable ({e.__class__.__name__})"
    if r.state == "absent":
        return {}, None
    if r.state != "file" or r.data is None:
        return {}, f"{ORDERS_FILE}: {r.detail or r.state}"
    try:
        raw = json.loads(r.data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}, f"{ORDERS_FILE} is not valid JSON"
    if not isinstance(raw, dict):
        return {}, f"{ORDERS_FILE} is not a JSON object"
    stop = raw.get("stop")
    notes = _valid_notes(raw.get("advisory"))
    if (stop is not None and stop not in STOP_VALUES) or notes is None:
        return {}, f"{ORDERS_FILE} is malformed (stop must be phase|roadmap|null; advisory a list of {{id, text}})"
    return {"stop": stop, "advisory": notes}, None


def run_override(state: dict | None) -> dict | None:
    o = (state or {}).get("orders")
    if not isinstance(o, dict):
        return None
    stop = o.get("stop")
    notes = _valid_notes(o.get("advisory"))
    return {"stop": stop if stop in STOP_VALUES else None, "advisory": notes or [],
            "set_at": o.get("set_at"), "by": o.get("by")}


def _roadmap_context(state: dict, phase: str | None) -> bool:
    """True when the state is a full-roadmap run whose current queue entry
    is `phase` — exactly the context `_derive_top_level_state` preserves."""
    from tagteam.state import normalize_phase_key
    if state.get("run_mode") != "full-roadmap":
        return False
    rm = state.get("roadmap") or {}
    queue = rm.get("queue") or []
    idx = rm.get("current_index", 0)
    if phase is None:
        return True
    return (isinstance(idx, int) and 0 <= idx < len(queue)
            and normalize_phase_key(phase) == normalize_phase_key(queue[idx]))


def effective(state: dict | None, project_dir: str | Path) -> dict:
    """The one resolution. `stop` precedence: run override > run mode
    (a full-roadmap run chose `roadmap`) > project > default `phase`.
    Advisory: project notes, then this run's."""
    state = state or {}
    project, warn = load_project(project_dir)
    run = run_override(state)
    explicit = bool(project.get("stop") or project.get("advisory") or run is not None)
    if run is not None and run["stop"]:
        stop, source = run["stop"], "run"
    elif state.get("run_mode") == "full-roadmap":
        stop, source = "roadmap", "run-mode"
    elif project.get("stop"):
        stop, source = project["stop"], "project"
    else:
        stop, source = DEFAULT_STOP, "default"
    advisory = [dict(n, source="project") for n in project.get("advisory") or []]
    advisory += [dict(n, source="run") for n in (run or {}).get("advisory") or []]
    return {"explicit": explicit, "stop": stop, "stop_source": source,
            "advisory": advisory, "warn": warn,
            "project": project, "run": run}


# ---------------------------------------------------------------------------
# Enforcement: decide (fresh impl approval) / apply (every derive)
# ---------------------------------------------------------------------------

def _convert_or_end(phase: str, project_dir: str | Path) -> dict:
    """`stop: roadmap` on a single-phase run: a new roadmap queue with the
    approved phase first, or the reason the run ends instead. Never raises."""
    from tagteam import roadmap as _rm
    from tagteam.state import normalize_phase_key
    path = Path(project_dir) / "docs" / "roadmap.md"
    try:
        if not path.is_file():
            return {"outcome": "stop", "reason": "roadmap-invalid: docs/roadmap.md not found"}
        phases, problems = _rm.graph_problems(path)
        if problems:
            return {"outcome": "stop", "reason": f"roadmap-invalid: {problems[0]}"}
        key = normalize_phase_key(phase)
        others = [p for p in phases if p.slug != key and not _rm.is_terminal_status(p.status)]
        if not others:
            return {"outcome": "complete", "reason": "roadmap-exhausted"}
        rest, _pulled = _rm.topological_queue(phases)
        queue = [key] + [s for s in rest if normalize_phase_key(s) != key]
        return {"outcome": "convert", "reason": "order", "queue": queue, "current_index": 0}
    except Exception as e:
        first = str(e).splitlines()[0] if str(e) else e.__class__.__name__
        return {"outcome": "stop", "reason": f"roadmap-invalid: {first}"}


def decide(state: dict | None, phase: str, project_dir: str | Path) -> dict | None:
    """Called once, by `add_round`, on a fresh impl APPROVE. None without
    explicit orders (nothing is recorded — today's behaviour). Never raises."""
    try:
        eff = effective(state, project_dir)
    except Exception:
        return None
    if not eff["explicit"]:
        return None
    stop, source = eff["stop"], eff["stop_source"]
    base = {"stop": stop, "source": source, "ts": _now_iso()}
    if stop == "phase":
        return {"outcome": "stop", "reason": "order", **base}
    if _roadmap_context(state or {}, phase):
        return {"outcome": "continue", "reason": "order", **base}
    return {**_convert_or_end(phase, project_dir), **base}


def apply_decision(updates: dict, decision: dict | None, *, fresh: bool) -> None:
    """Re-apply a RECORDED decision onto the top-level updates `_derive`
    built (after its own roadmap preservation). Never re-resolves orders.
    stop/complete → single-phase (idempotent; cannot start a phase).
    continue → nothing (the run's own roadmap, `completed` included, is kept
    by the preservation rule). convert → a new roadmap only on the fresh
    write; a sync keeps whatever the preservation rule kept."""
    if not isinstance(decision, dict):
        return
    outcome = decision.get("outcome")
    if outcome in ENDS_RUN:
        updates.pop("roadmap", None)
        updates["run_mode"] = "single-phase"
    elif outcome == "convert" and fresh:
        updates["run_mode"] = "full-roadmap"
        updates["roadmap"] = {"queue": list(decision.get("queue") or []),
                              "current_index": int(decision.get("current_index") or 0),
                              "completed": []}


def keep_run_override(decision: dict | None, *, fresh: bool) -> bool:
    """False only on the fresh write whose outcome ends the run."""
    return not (fresh and isinstance(decision, dict) and decision.get("outcome") in ENDS_RUN)


def describe_decision(decision: dict | None) -> str | None:
    if not isinstance(decision, dict):
        return None
    outcome, reason = decision.get("outcome"), decision.get("reason") or ""
    if outcome == "continue":
        return "continued (the roadmap run goes on to the next ready phase)"
    if outcome == "convert":
        return "converted to a roadmap run (the next ready phase follows)"
    if outcome == "complete":
        return "roadmap exhausted (nothing left to run; the run stopped)"
    if outcome == "stop" and reason.startswith("roadmap-invalid"):
        return f"stopped ({reason.replace('roadmap-invalid', 'roadmap invalid', 1)})"
    if outcome == "stop":
        return "stopped (order: stop after each phase)"
    return None


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

_STOP_WORDS = {
    "phase": ("When this phase's implementation is approved the run stops for "
              "the arbiter."),
    "roadmap": ("When this phase's implementation is approved the run goes on to "
                "the next ready roadmap phase, to the end of the roadmap. "
                "Escalations and questions for the arbiter still stop it."),
}
_SOURCE_WORDS = {"run": "this run", "run-mode": "this roadmap run",
                 "project": "project", "default": "default"}


def render_block(eff: dict) -> str:
    """The STANDING ORDERS block ('' without explicit orders), capped."""
    if not eff or not eff.get("explicit"):
        return ""
    lines = [BLOCK_HEADER,
             f"Enforced — the engine applies this; do not second-guess it "
             f"({_SOURCE_WORDS.get(eff['stop_source'], eff['stop_source'])}):",
             f"  stop: {eff['stop']} — {_STOP_WORDS[eff['stop']]}"]
    notes = eff.get("advisory") or []
    if notes:
        lines += ["Advisory — tagteam delivers these but does not enforce them; "
                  "follow them. Where a this-run note conflicts with a project "
                  "note, the this-run note wins:"]
        for n in notes:
            lines.append(f"  #{n['id']} ({'this run' if n['source'] == 'run' else 'project'}): {n['text']}")
    text = "\n".join(lines)
    if len(text) > BLOCK_BUDGET:
        text = (text[:BLOCK_BUDGET].rstrip()
                + "\n  … truncated — run `tagteam orders` for the full list")
    return text + "\n\n"


def state_line(state: dict | None, project_dir: str | Path,
               decision: dict | None = None) -> str | None:
    """`Orders:` line for `tagteam state` — None without explicit orders."""
    eff = effective(state, project_dir)
    if not eff["explicit"]:
        return None
    parts = [f"stop: {eff['stop']} ({eff['stop_source']})"]
    n = len(eff["advisory"])
    parts.append(f"{n} advisory")
    said = describe_decision(decision)
    if said:
        parts.append(f"last approval: {said}")
    if eff["warn"]:
        parts.append(f"warn: {eff['warn']}")
    return " · ".join(parts)


def current_decision(state: dict | None, project_dir: str | Path) -> dict | None:
    """The recorded decision of the state's approved impl cycle (files only
    — never opens the database)."""
    s = state or {}
    if s.get("type") != "impl" or s.get("status") != "done" or not s.get("phase"):
        return None
    p = Path(project_dir) / "docs" / "handoffs" / f"{s['phase']}_impl_status.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("run_decision")
    except (OSError, ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

class OrdersError(Exception):
    """A refused orders write; `str()` is what the CLI prints."""


def _write_project(project_dir: Path, orders: dict) -> None:
    from tagteam import dualwrite, safe_read
    dualwrite.refuse_if_read_only(f"`tagteam orders` write refused: {ORDERS_FILE}")
    probe = safe_read._lstat_chain(project_dir, ORDERS_FILE)
    if probe.state not in ("absent", "file"):
        raise OrdersError(f"refused: {probe.detail or ORDERS_FILE + ' is not a regular file'}")
    body = {"version": 1, "stop": orders.get("stop"), "advisory": orders.get("advisory") or []}
    data = (json.dumps(body, indent=2) + "\n").encode("utf-8")
    tmp = project_dir / f".{ORDERS_FILE}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        os.replace(tmp, project_dir / ORDERS_FILE)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _write_run(project_dir: Path, mutate) -> dict | None:
    """Read-modify-write the state's `orders` key WITHOUT bumping `seq`: a
    seq bump reads as a new turn to a watcher (it would re-dispatch the owed
    turn). Held under the writer lock; `write_state` keeps seq/history."""
    from tagteam import dualwrite
    from tagteam.state import read_state, write_state
    dualwrite.refuse_if_read_only("`tagteam orders --run` write refused: handoff-state.json")
    with dualwrite.writer_lock(project_dir):
        state = read_state(str(project_dir))
        if state is None:
            raise OrdersError("no handoff-state.json — a run override needs a tagteam project with state; "
                              "set a project order instead (drop --run)")
        cur = run_override(state) or {"stop": None, "advisory": []}
        new = mutate({"stop": cur["stop"], "advisory": list(cur["advisory"])})
        if new is None or (not new.get("stop") and not new.get("advisory")):
            state.pop("orders", None)
            result = None
        else:
            result = {"stop": new.get("stop"), "advisory": new.get("advisory") or [],
                      "set_at": _now_iso(), "by": new.get("by") or cur.get("by")}
            if result["stop"] is None:
                result.pop("stop")
            state["orders"] = result
        write_state(state, str(project_dir))
    return result


def _next_id(notes: list[dict]) -> int:
    return max((n["id"] for n in notes), default=0) + 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

USAGE = """\
Usage: tagteam orders [--json]
       tagteam orders stop phase|roadmap [--run] [--by NAME]
       tagteam orders stop --unset [--run]
       tagteam orders add "<note>" [--run] [--by NAME]
       tagteam orders remove ID [--run]
       tagteam orders clear --run

Enforced (the engine applies it): stop — `phase` stops the run after each
phase's implementation is approved; `roadmap` goes on to the next ready
roadmap phase. Escalations and questions stop a run either way.
Advisory (delivered, not enforced): free-text notes such as "hold the PR
for my approval". Project orders live in tagteam-orders.json (commit it);
--run sets an override for the current run (or the next one, if none is
active), dropped when that run ends."""


def _show(project_dir: Path, as_json: bool, out=None) -> int:
    from tagteam.state import read_state
    out = out or sys.stdout
    state = read_state(str(project_dir)) or {}
    eff = effective(state, project_dir)
    decision = current_decision(state, project_dir)
    if as_json:
        print(json.dumps({"explicit": eff["explicit"], "stop": eff["stop"],
                          "stop_source": eff["stop_source"], "advisory": eff["advisory"],
                          "warn": eff["warn"], "file": ORDERS_FILE,
                          "run": eff["run"], "last_decision": decision}, indent=2), file=out)
        return 0
    if eff["warn"]:
        print(f"warn: {eff['warn']} — treated as no project orders", file=out)
    print(f"Project orders: {ORDERS_FILE}"
          + ("" if (project_dir / ORDERS_FILE).exists() else " (none)"), file=out)
    print("", file=out)
    print("Enforced (the engine does this):", file=out)
    print(f"  stop: {eff['stop']}  [{eff['stop_source']}]", file=out)
    print(f"  At the next impl approval: "
          + ("the run stops for you." if eff["stop"] == "phase"
             else "the run goes on to the next ready roadmap phase."), file=out)
    print("", file=out)
    print("Advisory (delivered, not enforced):", file=out)
    if not eff["advisory"]:
        print("  (none)", file=out)
    for n in eff["advisory"]:
        tag = "this run" if n["source"] == "run" else "project"
        print(f"  #{n['id']} [{tag}] {n['text']}", file=out)
    said = describe_decision(decision)
    if said:
        print("", file=out)
        print(f"Last approval: {said}", file=out)
    return 0


def orders_command(args: list[str], project_root: str | Path | None = None) -> int:
    from tagteam.state import _resolve_project_root
    from tagteam.controls import _who
    root = Path(project_root) if project_root else Path(_resolve_project_root())
    args = list(args)
    if args and args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    run = "--run" in args
    args = [a for a in args if a != "--run"]
    by = None
    if "--by" in args:
        i = args.index("--by")
        if i + 1 >= len(args):
            print("--by needs a name", file=sys.stderr)
            return 2
        by = args[i + 1]
        del args[i:i + 2]
    if not args or args == ["--json"]:
        if run:
            print("--run applies to writes only", file=sys.stderr)
            return 2
        return _show(root, as_json=bool(args))
    sub, rest = args[0], args[1:]
    who = _who(by)
    try:
        if sub == "stop":
            if rest == ["--unset"]:
                value = None
            elif len(rest) == 1 and rest[0] in STOP_VALUES:
                value = rest[0]
            else:
                print("usage: tagteam orders stop phase|roadmap|--unset [--run]", file=sys.stderr)
                return 2

            def mutate(o):
                o["stop"] = value
                o["by"] = who
                return o
            done = f"stop {'unset' if value is None else value}"
        elif sub == "add":
            text = " ".join(rest).strip()
            if not text:
                print('usage: tagteam orders add "<note>" [--run]', file=sys.stderr)
                return 2

            def mutate(o):
                nid = _next_id(o["advisory"])
                o["advisory"].append({"id": nid, "text": text, "by": who, "ts": _now_iso()})
                o["by"] = who
                o["_added"] = nid
                return o
            done = "advisory note added"
        elif sub == "remove":
            try:
                rid = int(rest[0]) if len(rest) == 1 else None
            except ValueError:
                rid = None
            if rid is None:
                print("usage: tagteam orders remove ID [--run]", file=sys.stderr)
                return 2

            def mutate(o):
                if not any(n["id"] == rid for n in o["advisory"]):
                    raise OrdersError(f"no advisory note #{rid} in the {'run' if run else 'project'} orders")
                o["advisory"] = [n for n in o["advisory"] if n["id"] != rid]
                return o
            done = f"advisory note #{rid} removed"
        elif sub == "clear":
            if not run or rest:
                print("usage: tagteam orders clear --run   (project orders: edit or remove "
                      f"{ORDERS_FILE}, or use stop --unset / remove)", file=sys.stderr)
                return 2

            def mutate(o):
                return None
            done = "run override cleared"
        else:
            print(USAGE, file=sys.stderr)
            return 2

        if run:
            _write_run(root, mutate)
            where = "this run"
        else:
            project, warn = load_project(root)
            if warn:
                raise OrdersError(f"refused: {warn} — fix or remove it first")
            new = mutate({"stop": project.get("stop"), "advisory": list(project.get("advisory") or [])})
            new.pop("_added", None)
            _write_project(root, new)
            where = ORDERS_FILE
    except OrdersError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Orders: {done} ({where}).")
    return _show(root, as_json=False)
