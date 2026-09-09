"""Phase 37 — lead conversation: talk to the lead agent from the cockpit
(or `tagteam lead "..."`) through the lead's own signed-in CLI.

A **conversation** is an ordered list of turns; each user message spawns
ONE lead turn through the Phase 31 adapters (same provider / executable /
validated args / least-privilege defaults as a headless cycle turn), in the
project cwd, with the message on stdin, streamed to
`.tagteam/conversations/<cid>/<n>.events.jsonl` + `<n>.log`, and appended
to `.tagteam/conversations/<cid>/transcript.md` (the canonical human record;
the DB indexes it).

Continuity: claude — first turn `--session-id <uuid>`, later `--resume`;
codex — `exec … resume <thread_id>` when the installed CLI supports it
(probed once, before spawning), otherwise the budgeted transcript tail is
replayed on stdin. A resume that fails at runtime is surfaced as a failed
turn — never auto-replayed (it may already have used tools).

Every turn claims the project's turn slot (`headless.claim_turn_slot`,
kind=conversation) so it can never run on top of the watcher's lead turn
or the briefer, and `cancel-turn` / `tail` / the cockpit see it like any
other turn. Turn status: running → ok | failed | cancelled; a failed
conversation turn never writes the watcher's pause marker.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from tagteam import headless as h
from tagteam.contract import handoff_command
from tagteam import procs
from tagteam.config import get_headless_spec, validate_config

CONVERSATIONS_RELDIR = Path(".tagteam") / "conversations"
MAX_MESSAGE_BYTES = 32 * 1024
REPLAY_TURNS = 8                     # transcript tail replayed when no resume
REPLAY_BUDGET_CHARS = 24_000
CONVERSATION_ID_RE = re.compile(r"^c-[0-9a-f]{12}$")

DEFAULT_TIMEOUT_S = 30 * 60.0


class LeadChatError(Exception):
    pass


class LeadBusy(LeadChatError):
    """The turn slot is held (a cycle turn, the briefer, or another
    conversation turn)."""

    def __init__(self, marker: dict, reason: str):
        super().__init__(reason)
        self.marker = marker
        self.reason = reason


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- paths ----

def conversations_dir(project_root: str | Path) -> Path:
    return Path(project_root) / CONVERSATIONS_RELDIR


def conversation_dir(project_root: str | Path, cid: str) -> Path:
    """Path for a VALIDATED id, asserted to stay beneath the conversations
    dir (defence in depth: ids are regex-validated and DB-looked-up first)."""
    if not CONVERSATION_ID_RE.match(cid or ""):
        raise LeadChatError(f"invalid conversation id: {cid!r}")
    base = conversations_dir(project_root).resolve()
    p = (base / cid).resolve()
    if not p.is_relative_to(base):
        raise LeadChatError("conversation path escapes the conversations directory")
    return p


def transcript_path(project_root: str | Path, cid: str) -> Path:
    return conversation_dir(project_root, cid) / "transcript.md"


def _turn_paths(project_root: str | Path, cid: str, n: int) -> tuple[Path, Path]:
    d = conversation_dir(project_root, cid)
    return d / f"{n}.log", d / f"{n}.events.jsonl"


# ------------------------------------------------------------- lead spec ----

class LeadSpec:
    def __init__(self, *, ok: bool, errors: list[str], provider: str | None = None,
                 executable: str | None = None, user_args: list[str] | None = None,
                 agent_name: str = "lead", timeout_s: float = DEFAULT_TIMEOUT_S):
        self.ok, self.errors = ok, errors
        self.provider, self.executable = provider, executable
        self.user_args = user_args or []
        self.agent_name, self.timeout_s = agent_name, timeout_s


def resolve_lead(config: dict | None, project_root: str | Path) -> LeadSpec:
    """The lead's headless spec (provider / executable / validated args),
    with the same strictness as `HeadlessEngine.validate()` for that role."""
    errors = [f"tagteam.yaml: {e}" for e in validate_config(config or {})]
    if errors:
        return LeadSpec(ok=False, errors=errors)
    spec = get_headless_spec(config or {}, "lead")
    provider = spec["provider"]
    if provider not in h.ADAPTERS:
        return LeadSpec(ok=False, errors=[
            "agents.lead: cannot determine headless provider (set agents.lead.headless.provider)"
            if provider is None else f"agents.lead: unknown headless provider {provider!r}"])
    try:
        exe = h.resolve_executable(provider, spec["executable"])
        h.build_argv(h.ADAPTERS[provider], exe, spec["args"], project_root)
    except h.HeadlessConfigError as e:
        return LeadSpec(ok=False, errors=[f"agents.lead: {e}"], provider=provider)
    name = ((config or {}).get("agents") or {}).get("lead", {}).get("name") or "lead"
    tmo = spec.get("timeout_minutes")
    return LeadSpec(ok=True, errors=[], provider=provider, executable=exe,
                    user_args=list(spec["args"] or []), agent_name=str(name),
                    timeout_s=(float(tmo) * 60.0) if tmo else DEFAULT_TIMEOUT_S)


# ---------------------------------------------------------- persistence ----

def new_conversation(project_root: str | Path, *, provider: str | None,
                     title: str | None = None) -> dict:
    from tagteam import db
    cid = "c-" + secrets.token_hex(6)
    conn = db.connect(project_dir=str(project_root))
    try:
        row = db.new_conversation(conn, id_=cid, ts=_now_iso(), provider=provider, title=title)
    finally:
        conn.close()
    d = conversation_dir(project_root, cid)
    d.mkdir(parents=True, exist_ok=True)
    tp = transcript_path(project_root, cid)
    if not tp.exists():
        tp.write_text(f"# Lead conversation {cid}\n\n", encoding="utf-8")
    return row


def get_conversation(project_root: str | Path, cid: str) -> dict | None:
    from tagteam import db
    if not CONVERSATION_ID_RE.match(cid or ""):
        return None
    conn = db.connect(project_dir=str(project_root))
    try:
        row = db.get_conversation(conn, cid)
        if row is not None:
            row["turns"] = db.list_conversation_turns(conn, cid)
        return row
    finally:
        conn.close()


def list_conversations(project_root: str | Path, limit: int = 50) -> list[dict]:
    from tagteam import db
    conn = db.connect(project_dir=str(project_root))
    try:
        return db.list_conversations(conn, limit=limit)
    finally:
        conn.close()


def _append_transcript(project_root, cid: str, n: int, who: str, text: str, ts: str) -> None:
    tp = transcript_path(project_root, cid)
    with tp.open("a", encoding="utf-8") as f:
        f.write(f"## {n} · {who} · {ts}\n\n{text.rstrip()}\n\n")


# ------------------------------------------------------------ prompting ----

FIRST_TURN_HEADER = """You are the Lead agent for the tagteam project at {root} ({name}).
The human arbiter is talking to you from the tagteam cockpit. The handoff
skill contract is invoked as {handoff_cmd} (or read with `tagteam contract`);
you may run `tagteam …` commands (`{handoff_cmd} start <phase>` means: follow
that skill). Current handoff state: {state_line}.

"""


def _state_line(project_root) -> str:
    try:
        from tagteam.state import read_state
        st = read_state(str(project_root)) or {}
    except Exception:
        st = {}
    if not st.get("phase"):
        return "no active cycle"
    return (f"phase {st.get('phase')} · {st.get('type')} · round {st.get('round')} · "
            f"status {st.get('status')} · turn {st.get('turn')}")


def _replay_tail(project_root, cid: str, turns: list[dict]) -> str:
    """Budgeted transcript tail for providers without session resume."""
    parts: list[str] = []
    for t in turns[-REPLAY_TURNS:]:
        parts.append(f"[you] {t.get('user_text', '')}")
        if t.get("reply"):
            parts.append(f"[lead] {t['reply']}")
    text = "\n\n".join(parts)
    if len(text) > REPLAY_BUDGET_CHARS:
        text = "…\n" + text[-REPLAY_BUDGET_CHARS:]
    return ("Earlier in this conversation (transcript replay — the session could not be "
            "resumed):\n\n" + text + "\n\n---\n\n") if text else ""


# ------------------------------------------------------- reply extraction ----

def extract_reply(provider: str, lines: list[str]) -> str | None:
    """The lead's final text from the retained structured stdout."""
    texts: list[str] = []
    result_text = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        if provider == "claude":
            if t == "result" and isinstance(ev.get("result"), str):
                result_text = ev["result"]
            elif t == "assistant":
                for c in ((ev.get("message") or {}).get("content") or []):
                    if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                        texts.append(str(c["text"]))
        elif provider == "codex":
            it = ev.get("item") or {}
            if t == "item.completed" and it.get("type") == "agent_message" and it.get("text"):
                texts.append(str(it["text"]))
    if result_text:
        return result_text
    return "\n\n".join(texts) if texts else None


def _session_id_from(provider: str, lines: list[str]) -> str | None:
    usage = h.parse_usage(provider, lines) or {}
    return usage.get("session_id")


# ------------------------------------------------------------------ send ----

class TurnHandle:
    """A started (running) conversation turn: everything `run_turn` needs.
    Owning the slot claim + the DB row means the starter is responsible for
    ending them — `run_turn` always does, on every path."""

    def __init__(self, *, root: Path, cid: str, n: int, claim, spec: LeadSpec, argv: list[str],
                 prompt: str, continuity: str, sid: str | None, stem: str,
                 log_path: Path, events_path: Path, conv: dict, text: str, by: str):
        self.root, self.cid, self.n, self.claim, self.spec = root, cid, n, claim, spec
        self.argv, self.prompt, self.continuity, self.sid, self.stem = argv, prompt, continuity, sid, stem
        self.log_path, self.events_path, self.conv, self.text, self.by = log_path, events_path, conv, text, by


def _end_turn(root: Path, cid: str, n: int, *, status: str, error: str | None,
              reply: str | None = None, continuity: str | None = None,
              session_id: str | None = None, usage_row_id: int | None = None) -> dict | None:
    """Finalize a turn row (never raises)."""
    from tagteam import db
    try:
        conn = db.connect(project_dir=str(root))
        try:
            db.finish_conversation_turn(conn, cid, n, status=status, ts=_now_iso(), error=error,
                                        reply=reply, continuity=continuity, session_id=session_id,
                                        usage_row_id=usage_row_id)
            return db.get_conversation_turn(conn, cid, n)
        finally:
            conn.close()
    except Exception:
        return None


def start_turn(project_root: str | Path, cid: str, text: str, *, config: dict | None,
               by: str = "arbiter", resume_probe: Callable[[str], bool] | None = None) -> TurnHandle:
    """Validate, decide continuity, CLAIM the slot, create the `running`
    turn row and the marker fields — synchronously, so a Busy answers
    immediately and the turn number is known before the agent runs.
    Owns cleanup: any failure after the claim releases the slot, and any
    failure after the row exists ends the row as `failed`."""
    from tagteam import db
    root = Path(project_root)
    from tagteam.participants import check_participants, ParticipantMismatch
    try:
        fresh = check_participants(root)
    except ParticipantMismatch as exc:
        raise LeadChatError(str(exc)) from exc
    if fresh is not None:
        config = fresh
    if not CONVERSATION_ID_RE.match(cid or ""):
        raise LeadChatError(f"invalid conversation id: {cid!r}")
    if not isinstance(text, str) or not text.strip():
        raise LeadChatError("message is empty")
    if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise LeadChatError(f"message exceeds {MAX_MESSAGE_BYTES} bytes")
    spec = resolve_lead(config, root)
    if not spec.ok:
        raise LeadChatError("lead is not configured for headless turns: " + "; ".join(spec.errors))
    conv = get_conversation(root, cid)
    if conv is None:
        raise LeadChatError(f"unknown conversation: {cid}")
    if any(t.get("status") == "running" for t in conv["turns"]):
        raise LeadChatError("a turn of this conversation is still running")

    prior = conv["turns"]
    n = (prior[-1]["n"] + 1) if prior else 1
    log_path, events_path = _turn_paths(root, cid, n)
    started_at = _now_iso()
    me = os.getpid()
    me_ident = procs.identity(me)
    stem = f"conversation_{cid}_t{n}_{h._stamp()}"

    # -- continuity decision, BEFORE spawning
    adapter = h.ADAPTERS[spec.provider]
    session_id = conv.get("session_id")
    sid = None
    if spec.provider == "claude":
        sid = session_id or str(uuid.uuid4())
        argv = h.build_conversation_argv(adapter, spec.executable, spec.user_args, root,
                                         session_id=sid, resume=bool(session_id))
        continuity = "resumed session" if session_id else "new session"
        prompt_prefix = "" if session_id else FIRST_TURN_HEADER.format(
            root=root, name=root.name, state_line=_state_line(root),
            handoff_cmd=handoff_command(root))
    else:
        probe = resume_probe or h.codex_resume_supported
        can_resume = bool(session_id) and probe(spec.executable)
        argv = h.build_conversation_argv(adapter, spec.executable, spec.user_args, root,
                                         session_id=session_id or "", resume=can_resume)
        if can_resume:
            continuity, prompt_prefix = "resumed session", ""
        else:
            continuity = "transcript replay" if prior else "new session"
            prompt_prefix = ("" if prior else FIRST_TURN_HEADER.format(
                root=root, name=root.name, state_line=_state_line(root),
                handoff_cmd=handoff_command(root))) + _replay_tail(root, cid, prior)
    prompt = prompt_prefix + text

    # -- claim the slot (fail closed)
    try:
        claim = h.claim_turn_slot(root, kind=h.SLOT_KIND_CONVERSATION, role="lead", fields={
            "phase": None, "type": None, "round": None, "role": "lead",
            "agent": spec.agent_name, "provider": spec.provider, "stem": stem,
            "log_path": str(log_path), "events_path": str(events_path),
            "started_at": started_at, "pid": None, "child_ident": None,
            "watcher_pid": me, "watcher_ident": me_ident,
            "conversation_id": cid, "turn_n": n, "state_line": _state_line(root), "by": by,
        })
    except h.SlotBusy as busy:
        raise LeadBusy(busy.marker, busy.reason)
    # -- from here on the claim (and then the row) are ours to end
    turn = None
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        conn = db.connect(project_dir=str(root))
        try:
            turn = db.add_conversation_turn(conn, conversation_id=cid, ts=started_at, user_text=text,
                                            owner_pid=me, owner_ident=me_ident,
                                            log_path=str(log_path), events_path=str(events_path))
        finally:
            conn.close()
        n = turn["n"]
        _append_transcript(root, cid, n, "you", text, started_at)
    except BaseException as e:
        h.release_turn_slot(claim)
        if turn is not None:
            _end_turn(root, cid, turn["n"], status="failed", error=f"setup failed: {type(e).__name__}: {e}")
        raise
    return TurnHandle(root=root, cid=cid, n=n, claim=claim, spec=spec, argv=argv, prompt=prompt,
                      continuity=continuity, sid=sid, stem=stem, log_path=log_path,
                      events_path=events_path, conv=conv, text=text, by=by)


def run_turn(handle: TurnHandle, *, run: Callable | None = None,
             on_line: Callable[[str], None] | None = None) -> dict:
    """Spawn the lead for a started turn and finalize it. EVERY path ends
    with the slot released and the row in ok|failed|cancelled — including
    an unexpected runner exception (recorded as failed, then re-raised)."""
    from tagteam import db
    root, cid, n, spec = handle.root, handle.cid, handle.n, handle.spec

    def _on_spawn(pid: int) -> None:
        h.update_turn_slot(handle.claim, pid=pid, child_ident=procs.identity(pid))

    env = dict(os.environ)
    for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(k, None)
    runner = run or h.run_process
    spawn_error = None
    out = None
    try:
        try:
            out = runner(handle.argv, handle.prompt, root, events_path=handle.events_path,
                         log_path=handle.log_path, provider=spec.provider, timeout_s=spec.timeout_s,
                         on_line=on_line, on_spawn=_on_spawn, env=env)
        except h.SpawnError as e:
            spawn_error = str(e)
            out = h.RunOutput(exit_code=None, timed_out=False, duration_ms=0)
        finally:
            h.release_turn_slot(handle.claim)
    except BaseException as e:   # unexpected runner failure: the row must not stay `running`
        _end_turn(root, cid, n, status="failed", error=f"runner error: {type(e).__name__}: {e}",
                  continuity=handle.continuity)
        try:
            _append_transcript(root, cid, n, spec.agent_name,
                               f"(no reply — failed: runner error: {type(e).__name__}: {e})", _now_iso())
        except Exception:
            pass
        raise

    try:
        cancel = h.read_cancel(root)
        cancelled_by = None
        if cancel is not None and cancel.get("stem") == handle.stem:
            h.clear_cancel(root)
            cancelled_by = cancel.get("by") or "arbiter"
        try:
            lines = handle.events_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        reply = extract_reply(spec.provider, lines)
        new_sid = _session_id_from(spec.provider, lines) or (handle.sid if spec.provider == "claude" else None)
        if cancelled_by:
            status, error = "cancelled", f"cancelled by {cancelled_by}"
        elif spawn_error:
            status, error = "failed", f"could not start {spec.provider} ({spec.executable}): {spawn_error}"
        elif out.timed_out:
            status, error = "failed", f"turn exceeded {spec.timeout_s / 60:.0f} min timeout"
        elif out.exit_code != 0:
            status, error = "failed", f"{spec.provider} exited {out.exit_code}"
        else:
            status, error = "ok", None
        usage_row_id = None
        conn = db.connect(project_dir=str(root))
        try:
            try:
                usage = h.parse_usage(spec.provider, lines) or {}
                fields = dict(ts=_now_iso(), phase=None, type=None, round=None, role="lead",
                              agent=spec.agent_name, provider=spec.provider,
                              status=("ok" if status == "ok" else ("cancelled" if status == "cancelled" else "nonzero_exit")),
                              exit_code=out.exit_code, duration_ms=out.duration_ms,
                              log_path=str(handle.log_path), kind="conversation")
                fields.update({k: usage.get(k) for k in ("model", "input_tokens", "output_tokens",
                                                         "cache_read_tokens", "cache_write_tokens",
                                                         "cost_usd", "num_turns", "session_id")})
                usage_row_id = db.add_usage(conn, **fields)
            except Exception:
                usage_row_id = None
            db.finish_conversation_turn(conn, cid, n, status=status, ts=_now_iso(),
                                        session_id=new_sid, usage_row_id=usage_row_id, error=error,
                                        reply=reply, continuity=handle.continuity)
            upd = {"last_ts": _now_iso(), "continuity": handle.continuity}
            if new_sid and status == "ok":
                upd["session_id"] = new_sid
            if not handle.conv.get("title") and n == 1:
                upd["title"] = " ".join(handle.text.split())[:60]
            db.update_conversation(conn, cid, **upd)
            turn = db.get_conversation_turn(conn, cid, n)
        finally:
            conn.close()
        with handle.log_path.open("a", encoding="utf-8", errors="replace") as f:
            f.write(f"[tagteam] conversation turn {status}" + (f": {error}" if error else "") + "\n")
        _append_transcript(root, cid, n, spec.agent_name,
                           reply if reply else f"(no reply — {status}{': ' + error if error else ''})",
                           _now_iso())
        return turn
    except BaseException as e:   # persistence failure after the run: never leave `running`
        _end_turn(root, cid, n, status="failed", error=f"finalize failed: {type(e).__name__}: {e}",
                  continuity=handle.continuity)
        raise


def start_worker(target: Callable[[], None], name: str) -> None:
    """Run `target` on a daemon thread. One seam for both the cockpit's Send
    and the composite launch, so a dispatch failure (thread resource
    exhaustion) is handled — and testable — in one place."""
    import threading
    threading.Thread(target=target, name=name, daemon=True).start()


def abort_turn(handle: TurnHandle, reason: str) -> dict | None:
    """Owner-safe abort of a STARTED turn that no worker will run (e.g. the
    dispatch thread could not be started): release only this handle's slot
    token and end its row as failed. Never raises."""
    try:
        h.release_turn_slot(handle.claim)
    except Exception:
        pass
    row = _end_turn(handle.root, handle.cid, handle.n, status="failed",
                    error=f"aborted before running: {reason}", continuity=handle.continuity)
    try:
        _append_transcript(handle.root, handle.cid, handle.n, handle.spec.agent_name,
                           f"(no reply — aborted before running: {reason})", _now_iso())
    except Exception:
        pass
    return row


def send(project_root: str | Path, cid: str, text: str, *, config: dict | None,
         by: str = "arbiter", run: Callable | None = None,
         on_line: Callable[[str], None] | None = None,
         resume_probe: Callable[[str], bool] | None = None) -> dict:
    """Run ONE lead turn for `text` in conversation `cid` (synchronous:
    `start_turn` + `run_turn`). Returns the finished turn row. Raises
    LeadBusy when the slot is held, LeadChatError on invalid input / config."""
    handle = start_turn(project_root, cid, text, config=config, by=by, resume_probe=resume_probe)
    return run_turn(handle, run=run, on_line=on_line)


# ------------------------------------------------------------- cancel ----

def cancel(project_root: str | Path, cid: str, *, by: str = "arbiter") -> tuple[bool, str]:
    """Cancel the running turn of `cid` (same binding rule as cancel-turn)."""
    from tagteam.controls import bind_inflight
    root = Path(project_root)
    marker = h.read_inflight(root)
    if not marker or marker.get("kind") != h.SLOT_KIND_CONVERSATION or marker.get("conversation_id") != cid:
        return False, "no running turn for this conversation"
    ok, why = bind_inflight(marker)
    if not ok:
        return False, why
    h.write_cancel(root, {"stem": marker.get("stem"), "pid": marker["pid"], "by": by})
    h.kill_pid_tree(marker["pid"])
    return True, f"cancel requested for turn {marker.get('turn_n')} (pid {marker['pid']})"


# ---------------------------------------------------------- reconcile ----

def reconcile(project_root: str | Path) -> list[dict]:
    """Mark `running` turns whose owner is definitively gone as failed
    (orphaned). Returns the rows it changed. Fail closed on unverifiable."""
    from tagteam import db
    changed = []
    conn = db.connect(project_dir=str(project_root))
    try:
        for t in db.running_conversation_turns(conn):
            pid = t.get("owner_pid")
            gone = False
            if not isinstance(pid, int) or pid <= 0 or not procs.pid_alive(pid):
                gone = True
            else:
                rec = t.get("owner_ident")
                now = procs.identity(pid) if rec else None
                if rec and now is not None and now != rec:
                    gone = True
            if gone:
                ts = _now_iso()
                db.finish_conversation_turn(conn, t["conversation_id"], t["n"], status="failed", ts=ts,
                                            error=f"orphaned at {ts} (owner process gone)")
                changed.append(t)
    finally:
        conn.close()
    return changed


# ---------------------------------------------------------- events/SSE ----

def turn_events(project_root: str | Path, cid: str, *, after: str | None = None,
                provider: str | None = None) -> list[dict]:
    """Every retained conversation event after cursor `after` (id form
    `<n>:<seq>`; the terminal status event is `<n>:end`). Deterministic and
    replayable — the SSE endpoint replays from here, then follows."""
    from tagteam import db
    root = Path(project_root)
    conn = db.connect(project_dir=str(root))
    try:
        conv = db.get_conversation(conn, cid)
        turns = db.list_conversation_turns(conn, cid) if conv else []
    finally:
        conn.close()
    if conv is None:
        return []
    prov = provider or conv.get("provider") or "claude"
    a_n, a_seq = _parse_cursor(after)
    out: list[dict] = []
    for t in turns:
        n = int(t["n"])
        if n < a_n:
            continue
        seq = 0
        ev_path = Path(t["events_path"]) if t.get("events_path") else None
        lines = []
        if ev_path and ev_path.exists():
            try:
                lines = ev_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines = []
        for line in lines:
            seq += 1
            if n == a_n and a_seq is not None and (a_seq == "end" or seq <= int(a_seq)):
                continue
            rendered = h.render_event(prov, line)
            if rendered is None:
                continue
            out.append({"id": f"{n}:{seq}", "type": "line", "turn": n, "text": rendered})
        if t.get("status") != "running":
            if n == a_n and a_seq == "end":
                continue
            out.append({"id": f"{n}:end", "type": "end", "turn": n, "status": t["status"],
                        "error": t.get("error"), "reply": t.get("reply"),
                        "continuity": t.get("continuity")})
    return out


def _parse_cursor(after: str | None) -> tuple[int, str | None]:
    if not after:
        return 0, None
    m = re.match(r"^(\d+):(\d+|end)$", str(after))
    if not m:
        return 0, None
    return int(m.group(1)), m.group(2)


def is_turn_running(project_root: str | Path, cid: str) -> bool:
    from tagteam import db
    conn = db.connect(project_dir=str(project_root))
    try:
        return any(t.get("status") == "running" for t in db.list_conversation_turns(conn, cid))
    finally:
        conn.close()


# ------------------------------------------------------------------ CLI ----

LEAD_USAGE = """Usage:
  tagteam lead "message" [--new] [--conversation ID] [--json]
  tagteam lead --list [--json]

Talk to the lead agent from the terminal — the same engine as the cockpit's
Lead panel (resumable session, transcript under .tagteam/conversations/).
Without --conversation the most recent conversation is continued; --new
starts a fresh one. Exit 0 = the lead replied; 1 = the turn failed;
3 = the lead is busy (a cycle turn or another conversation holds the slot);
2 = usage error."""


def lead_command(args: list[str], project_root: str | Path | None = None, out=None) -> int:
    import sys as _sys
    out = out or _sys.stdout
    if project_root is None:
        from tagteam.state import _resolve_project_root
        project_root = _resolve_project_root()
    root = Path(project_root)
    from tagteam.config import read_config
    cfg = read_config(root / "tagteam.yaml") or {}
    want_json = "--json" in args
    args = [a for a in args if a != "--json"]
    if not args or args in (["-h"], ["--help"]):
        print(LEAD_USAGE, file=out)
        return 0 if args else 2
    if args[0] == "--list":
        reconcile(root)
        rows = list_conversations(root)
        if want_json:
            print(json.dumps(rows, indent=1, default=str), file=out)
        elif not rows:
            print("No conversations yet. `tagteam lead \"hello\"` starts one.", file=out)
        else:
            for r in rows:
                print(f"{r['id']}  {r.get('turns', 0):>3} turn(s)  {r.get('last_ts') or r.get('created_at')}  "
                      f"{r.get('continuity') or ''}  {r.get('title') or ''}", file=out)
        return 0
    new = False
    cid = None
    text_parts: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--new":
            new = True
        elif a == "--conversation" and i + 1 < len(args):
            cid = args[i + 1]; i += 1
        elif a.startswith("-"):
            print(f"Unknown argument: {a}\n{LEAD_USAGE}", file=out)
            return 2
        else:
            text_parts.append(a)
        i += 1
    text = " ".join(text_parts).strip()
    if not text:
        print("A message is required.\n" + LEAD_USAGE, file=out)
        return 2
    spec = resolve_lead(cfg, root)
    if not spec.ok:
        print("The lead is not configured for headless turns:\n  " + "\n  ".join(spec.errors), file=out)
        return 2
    reconcile(root)
    if cid is None and not new:
        rows = list_conversations(root, limit=1)
        cid = rows[0]["id"] if rows else None
    if cid is None or new:
        cid = new_conversation(root, provider=spec.provider)["id"]
    elif get_conversation(root, cid) is None:
        print(f"Unknown conversation: {cid}", file=out)
        return 2
    try:
        turn = send(root, cid, text, config=cfg, by="cli:" + (os.environ.get("USER") or "user"))
    except LeadBusy as busy:
        msg = f"lead is busy — {busy.reason} (stem {busy.marker.get('stem')}); wait, or `tagteam interject`"
        print(json.dumps({"ok": False, "busy": True, "message": msg}) if want_json else msg, file=out)
        return 3
    except LeadChatError as e:
        print(json.dumps({"ok": False, "message": str(e)}) if want_json else str(e), file=out)
        return 2
    if want_json:
        print(json.dumps({"ok": turn["status"] == "ok", "conversation_id": cid, "turn": turn}, indent=1,
                         default=str), file=out)
    else:
        print(f"[{cid} · turn {turn['n']} · {turn.get('continuity')}]", file=out)
        print(turn.get("reply") or f"(no reply — {turn['status']}: {turn.get('error')})", file=out)
        if turn["status"] != "ok":
            print(f"log: {turn.get('log_path')}", file=out)
    return 0 if turn["status"] == "ok" else 1
