"""Fake agent CLI for headless-engine tests.

Installed into a temp PATH dir as ``claude`` / ``codex`` (POSIX shell
script or Windows ``.cmd`` shim) so tests exercise the same
``shutil.which`` + PATHEXT resolution as the real CLIs.

Behaviour is driven by environment variables:

  FAKE_AGENT_FLAVOR   claude | codex           (event-stream shape)
  FAKE_AGENT_MODE     ok | no_round | nonzero | hang | grandchild_hang |
                      malformed | stderr_noise | wrong_round | amend
  FAKE_AGENT_CAPTURE  path: argv + stdin prompt are dumped here as JSON
  FAKE_AGENT_PIDFILE  path: grandchild pid is written here (grandchild_hang)
  FAKE_AGENT_SLEEP    seconds between emitted events (default 0.25)
  FAKE_AGENT_HOLD     path: in `chat` mode, after the first event, wait until this
                      file exists before replying (polled every 20 ms, capped at
                      30 s) — lets a test assert "still running" without a race
  FAKE_AGENT_SIDE_EFFECT  JSON list of actions run before exiting in
                      `nonzero` mode (retry-gate tests):
                        {"write": [relpath, content]}   write a file (append)
                        {"git": ["commit","-am","x"]}   run a git command
                        {"cycle_add": true}             perform the owed transition
  FAKE_AGENT_FAIL_TIMES / FAKE_AGENT_COUNTER  in `flaky` mode: exit 3 for the
                      first N invocations (counter file), then behave like `ok`
  panel mode (Phase 39): `panel` acts as one lens — FAKE_PANEL_VERDICTS
                      (JSON {lens: behaviour}) / FAKE_PANEL_VERDICT: approve |
                      request-changes | escalate | need-human | need-human-noq |
                      no-file | bad-json | bad-shape | rogue | hang | nonzero |
                      approve-with-blocker
  brief modes (Phase 33): `brief` writes the file named in the prompt's
                      === OUTPUT PATH === block with the five headings;
                      `brief_partial` writes three headings; `brief_nofile`
                      exits 0 without writing; `brief_hang` writes nothing and sleeps

The fake reads the composed prompt from stdin, parses the CURRENT STATE
block, and in ``ok`` mode performs the transition a real agent would by
running ``python -m tagteam cycle add|init`` in the cwd it was spawned
in (the project root). Events are emitted incrementally with sleeps so
tests can assert the log grows before exit.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _sleep() -> None:
    time.sleep(float(os.environ.get("FAKE_AGENT_SLEEP", "0.25")))


def _hold() -> None:
    path = os.environ.get("FAKE_AGENT_HOLD")
    if not path:
        return
    deadline = time.monotonic() + 30
    while not os.path.exists(path) and time.monotonic() < deadline:
        time.sleep(0.02)


def _parse_state(prompt: str) -> dict:
    marker = "=== CURRENT STATE (handoff-state.json) ==="
    end = "=== ROUND TAIL"
    if marker not in prompt:
        return {}
    body = prompt.split(marker, 1)[1]
    body = body.split(end, 1)[0]
    try:
        return json.loads(body.strip())
    except ValueError:
        return {}


def _tagteam(*args: str) -> int:
    return subprocess.call([sys.executable, "-m", "tagteam", *args],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _agent_name(prompt: str) -> str:
    # 'You are the lead (Claude) in a tagteam...'
    try:
        head = prompt.split("\n", 1)[0]
        return head.split("(", 1)[1].split(")", 1)[0]
    except Exception:
        return "fake"


def _do_transition(prompt: str, state: dict, mode: str) -> None:
    role = state.get("turn")
    phase = state.get("phase")
    ctype = state.get("type", "plan")
    rnd = int(state.get("round") or 0)
    name = _agent_name(prompt)
    command = state.get("command") or ""
    # start command → cycle init
    parts = command.strip().split()
    if len(parts) >= 3 and parts[0] == "/handoff" and parts[1] == "start":
        slug = parts[2]
        t = "impl" if len(parts) > 3 and parts[3] == "impl" else "plan"
        _tagteam("cycle", "init", "--phase", slug, "--type", t,
                 "--updated-by", name, "--content", f"fake {t} submission")
        return
    if role == "lead":
        target = rnd + 1
        if mode == "wrong_round":
            target = rnd + 5
        if mode == "amend":
            _tagteam("cycle", "add", "--phase", phase, "--type", ctype,
                     "--role", "lead", "--action", "AMEND", "--round", str(rnd),
                     "--updated-by", name, "--content", "fake amend")
            return
        _tagteam("cycle", "add", "--phase", phase, "--type", ctype,
                 "--role", "lead", "--action", "SUBMIT_FOR_REVIEW",
                 "--round", str(target), "--updated-by", name,
                 "--content", "fake lead submission")
    else:
        target = rnd if mode != "wrong_round" else rnd + 5
        _tagteam("cycle", "add", "--phase", phase, "--type", ctype,
                 "--role", "reviewer", "--action", "REQUEST_CHANGES",
                 "--round", str(target), "--updated-by", name,
                 "--content", "fake review")


def main() -> int:
    flavor = os.environ.get("FAKE_AGENT_FLAVOR", "claude")
    mode = os.environ.get("FAKE_AGENT_MODE", "ok")
    # The engine writes the prompt as UTF-8 bytes; decode explicitly so a
    # Windows console code page (cp1252) cannot mangle non-ASCII text.
    prompt = sys.stdin.buffer.read().decode("utf-8", "replace")
    capture = os.environ.get("FAKE_AGENT_CAPTURE")
    if capture:
        with open(capture, "w", encoding="utf-8") as f:
            json.dump({"argv": sys.argv, "prompt": prompt, "cwd": os.getcwd(),
                       "read_only": os.environ.get("TAGTEAM_READ_ONLY")}, f)
    state = _parse_state(prompt)

    if flavor == "claude":
        _emit({"type": "system", "subtype": "init", "session_id": "fake-sess",
               "model": "fake-model", "permissionMode": "acceptEdits", "tools": []})
        # Phase 34: the real CLI emits a rate-limit frame; the engine records
        # the latest per kind into `rate_limits`.
        _emit({"type": "rate_limit_event",
               "rate_limit_info": {"status": "allowed", "resetsAt": 1786785000,
                                   "rateLimitType": "five_hour",
                                   "overageStatus": "allowed", "isUsingOverage": False}})
    else:
        tid = "fake-thread"
        if mode == "chat":
            tid = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else "fake-thread-" + str(os.getpid())
        _emit({"type": "thread.started", "thread_id": tid})
    _sleep()

    if mode == "chat":
        # Phase 37 lead conversation: echo the message; prove continuity by
        # quoting the previous message when the argv carries a resume token
        # (`--resume <sid>` for claude, `resume <thread>` for codex) — the
        # "memory" is a per-session file next to the capture path.
        argv = sys.argv
        sid = None
        resumed = False
        if flavor == "claude":
            for flag in ("--resume", "--session-id"):
                if flag in argv:
                    sid = argv[argv.index(flag) + 1]
                    resumed = flag == "--resume"
        else:
            if "resume" in argv:
                sid = argv[argv.index("resume") + 1]
                resumed = True
            else:
                sid = "fake-thread-" + str(os.getpid())
        mem_dir = os.environ.get("FAKE_AGENT_MEMDIR") or os.getcwd()
        mem = os.path.join(mem_dir, f".fake-mem-{sid}") if sid else None
        prev = None
        if resumed and mem and os.path.exists(mem):
            with open(mem, encoding="utf-8") as f:
                prev = f.read()
        msg = prompt.strip().splitlines()[-1] if prompt.strip() else ""
        reply = f"echo: {msg}" + (f" (earlier you said: {prev})" if prev else "")
        if mem:
            with open(mem, "w", encoding="utf-8") as f:
                f.write(msg)
        if os.environ.get("FAKE_AGENT_CHAT_HTML"):
            reply = "<img src=x onerror=\"document.title='pwned'\"><script>document.title='pwned'</script>" + reply
        _hold()
        _sleep()
        if flavor == "claude":
            _emit({"type": "assistant", "message": {"content": [{"type": "text", "text": reply}]},
                   "session_id": sid})
            _emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                   "session_id": sid, "total_cost_usd": 0.001,
                   "usage": {"input_tokens": 5, "output_tokens": 7, "cache_read_input_tokens": 0,
                             "cache_creation_input_tokens": 0},
                   "result": reply})
        else:
            _emit({"type": "item.completed", "item": {"type": "agent_message", "text": reply}})
            _emit({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 7,
                                                       "cached_input_tokens": 0,
                                                       "cache_write_input_tokens": 0}})
        return 0

    if mode == "panel":
        # Phase 39 lens turn: FAKE_PANEL_VERDICTS = JSON {lens: behaviour}
        # (or FAKE_PANEL_VERDICT for every lens): approve | request-changes |
        # escalate | need-human | need-human-noq | no-file | bad-json |
        # bad-shape | rogue | hang | nonzero | approve-with-blocker.
        lens = os.environ.get("TAGTEAM_PANEL_LENS", "?")
        try:
            table = json.loads(os.environ.get("FAKE_PANEL_VERDICTS") or "{}")
        except ValueError:
            table = {}
        beh = table.get(lens) or os.environ.get("FAKE_PANEL_VERDICT", "approve")
        vpath = None
        marker = "WRITE your verdict as JSON to exactly this path and stop:"
        if marker in prompt:
            for line in prompt.split(marker, 1)[1].splitlines()[1:4]:
                if line.strip():
                    vpath = line.strip()
                    break
        if beh == "hang":
            time.sleep(600)
            return 0
        if beh in ("rogue", "rogue-unset"):
            # a misbehaving lens that writes the cycle itself. Phase 50: the
            # lens child runs under TAGTEAM_READ_ONLY, so `rogue` is refused
            # by the CLI; `rogue-unset` clears the switch (a write through a
            # path the switch does not cover) so the panel's post-hoc
            # detection is exercised. FAKE_ROGUE_OUT records the attempt.
            env = dict(os.environ)
            if beh == "rogue-unset":
                env.pop("TAGTEAM_READ_ONLY", None)
            r = subprocess.run([sys.executable, "-m", "tagteam", "cycle", "add", "--phase", state.get("phase", "?"),
                                "--type", state.get("type", "impl"), "--role", "reviewer", "--action", "REQUEST_CHANGES",
                                "--round", str(state.get("round", 1)), "--updated-by", "rogue-lens",
                                "--content", "rogue lens wrote this"], check=False, capture_output=True,
                               text=True, env=env)
            rogue_out = os.environ.get("FAKE_ROGUE_OUT")
            if rogue_out:
                with open(rogue_out, "w", encoding="utf-8") as f:
                    json.dump({"rc": r.returncode, "stderr": r.stderr}, f)
        elif vpath and beh != "no-file":
            os.makedirs(os.path.dirname(vpath) or ".", exist_ok=True)
            if beh == "bad-json":
                body = "{not json"
            else:
                sev_finding = [{"title": f"{lens} finding", "detail": f"detail from {lens}", "where": "src.py:1",
                                "severity": "blocker" if lens == "correctness" else "major"}]
                minor = [{"title": f"{lens} nit", "detail": "polish", "severity": "minor"}]
                if beh == "approve":
                    d = {"verdict": "APPROVE", "summary": f"{lens} looks good", "findings": minor}
                elif beh == "approve-with-blocker":
                    d = {"verdict": "APPROVE", "summary": "oops", "findings": sev_finding}
                elif beh == "request-changes":
                    d = {"verdict": "REQUEST_CHANGES", "summary": f"{lens} objects", "findings": sev_finding + minor}
                elif beh == "escalate":
                    d = {"verdict": "ESCALATE", "summary": f"{lens} cannot decide: needs the arbiter", "findings": []}
                elif beh == "need-human":
                    d = {"verdict": "NEED_HUMAN", "summary": f"{lens} question", "findings": [],
                         "question": f"{lens}: which behaviour did you intend?"}
                elif beh == "need-human-noq":
                    d = {"verdict": "NEED_HUMAN", "summary": "no question", "findings": []}
                elif beh == "bad-shape":
                    d = {"verdict": "MAYBE"}
                else:
                    d = {"verdict": "APPROVE", "summary": "default", "findings": []}
                body = json.dumps(d)
            with open(vpath, "w", encoding="utf-8") as f:
                f.write(body)
        _emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "DONE"}]}}
              if flavor == "claude" else
              {"type": "item.completed", "item": {"type": "agent_message", "text": "DONE"}})
        _sleep()
        if flavor == "claude":
            _emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                   "session_id": f"fake-lens-{lens}", "total_cost_usd": 0.01,
                   "usage": {"input_tokens": 70, "output_tokens": 30,
                             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                   "result": "DONE"})
        else:
            _emit({"type": "turn.completed", "usage": {"input_tokens": 70, "output_tokens": 30,
                                                       "cached_input_tokens": 0, "cache_write_input_tokens": 0}})
        return 3 if beh == "nonzero" else 0

    if mode.startswith("brief"):
        out_path = None
        if "=== OUTPUT PATH ===" in prompt:
            block = prompt.split("=== OUTPUT PATH ===", 1)[1]
            for line in block.splitlines()[1:6]:
                line = line.strip()
                if line and not line.startswith("Write the brief") and not line.startswith("First line") \
                        and not line.startswith("Print DONE") and not line.startswith("==="):
                    out_path = line
                    break
        if mode == "brief_hang":
            time.sleep(600)
            return 0
        if mode != "brief_nofile" and out_path:
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            heads = ["## Positions", "## Crux", "## Evidence", "## Recommendation", "## Rulings"]
            if mode == "brief_partial":
                heads = heads[:3]
            body = ["<!-- fake brief -->", "# Decision brief (fake)"]
            for hd in heads:
                body += [hd, f"fake text for {hd[3:].lower()}", ""]
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(body))
        _emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "DONE"}]}}
              if flavor == "claude" else
              {"type": "item.completed", "item": {"type": "agent_message", "text": "DONE"}})
        _sleep()
        if flavor == "claude":
            _emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                   "session_id": "fake-brief", "total_cost_usd": 0.02,
                   "usage": {"input_tokens": 100, "output_tokens": 50,
                             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                   "result": "DONE"})
        else:
            _emit({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50,
                                                       "cached_input_tokens": 0,
                                                       "cache_write_input_tokens": 0}})
        return 0

    if mode == "stderr_noise":
        sys.stderr.write("warning: something noisy on stderr\n")
        sys.stderr.flush()

    if mode == "hang":
        _emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "hanging"}]}}
              if flavor == "claude" else
              {"type": "item.completed", "item": {"type": "agent_message", "text": "hanging"}})
        time.sleep(600)
        return 0

    if mode == "grandchild_hang":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        pidfile = os.environ.get("FAKE_AGENT_PIDFILE")
        if pidfile:
            with open(pidfile, "w") as f:
                f.write(str(child.pid))
        _emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "spawned grandchild"}]}}
              if flavor == "claude" else
              {"type": "item.completed", "item": {"type": "agent_message", "text": "spawned grandchild"}})
        time.sleep(600)
        return 0

    if flavor == "claude":
        _emit({"type": "assistant", "message": {"model": "fake-model", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "tagteam cycle add ..."}}]}})
    else:
        _emit({"type": "item.started", "item": {"type": "command_execution",
                                                "command": "tagteam cycle add ..."}})
    _sleep()

    if mode == "flaky":
        counter = os.environ.get("FAKE_AGENT_COUNTER")
        fails = int(os.environ.get("FAKE_AGENT_FAIL_TIMES", "1"))
        n = 0
        if counter and os.path.exists(counter):
            n = int(open(counter).read().strip() or 0)
        if counter:
            with open(counter, "w") as f:
                f.write(str(n + 1))
        if n < fails:
            sys.stderr.write("fatal: flaky failure\n")
            return 3
        mode = "ok"

    if mode == "nonzero":
        for action in json.loads(os.environ.get("FAKE_AGENT_SIDE_EFFECT", "[]")):
            if "write" in action:
                path, content = action["write"]
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(content)
            elif "git" in action:
                subprocess.call(["git", *action["git"]], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
            elif action.get("cycle_add"):
                _do_transition(prompt, state, "ok")
        sys.stderr.write("fatal: fake failure\n")
        return 3

    if mode in ("ok", "malformed", "stderr_noise", "wrong_round", "amend"):
        _do_transition(prompt, state, mode)

    _sleep()
    if mode == "malformed":
        sys.stdout.write("this is not json and there is no usage\n")
        sys.stdout.flush()
        return 0

    if flavor == "claude":
        _emit({"type": "result", "subtype": "success", "is_error": False,
               "num_turns": 2, "session_id": "fake-sess", "total_cost_usd": 0.01,
               "usage": {"input_tokens": 11, "output_tokens": 22,
                         "cache_read_input_tokens": 33,
                         "cache_creation_input_tokens": 44},
               "result": "done"})
    else:
        _emit({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}})
        _emit({"type": "turn.completed", "usage": {"input_tokens": 11, "output_tokens": 22,
                                                   "cached_input_tokens": 33,
                                                   "cache_write_input_tokens": 44}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
