#!/usr/bin/env python3
"""Seed disposable demo projects for the docs/media screenshots (Phase 36).

    python scripts/showcase_seed.py DIR            # DIR must not exist yet
    tagteam serve --theme cockpit --dir DIR/demo/demo-api --port 8080
    tagteam hub --registry DIR/registry.json --port 8090

Builds, under DIR/demo/, three projects with generic names and synthetic
round text authored right here (nothing is copied from a real project):

  demo-api   plan cycle ESCALATED at round 3, a decision brief on disk and
             in the DB, a declining per-round usage series  -> Needs you
  demo-web   impl cycle, reviewer turn owed for hours, no watcher -> Waiting · stale
             (3.7) plus a RUNNING reviewer cycle turn for the Cycle region /
             Activity log: an in-flight marker over a detached `sleep` (its
             pid is printed — kill it when done), a turn log that keeps
             growing for a minute, a passed gate and three finished turns
  demo-docs  impl cycle approved                             -> Quiet
  demo-idle  no cycle yet, roadmap has an open phase, plus a canned Lead
             conversation (two turns)                        -> Start card + Lead panel

plus DIR/registry.json listing exactly those three paths, so `tagteam hub
--registry DIR/registry.json` never reads ~/.tagteam/projects.json.

Pick DIR outside /tmp, /private/tmp and /var/folders (the hub hides those
as scratch), e.g. ~/tagteam-demo. Delete DIR when done. This script imports
tagteam (dev script) and writes only under DIR.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

LEAD, REVIEWER = "Claude", "Codex"

API_ROUNDS = [
    ("lead", "SUBMIT_FOR_REVIEW", 1, "Plan for the rate-limit middleware: token bucket per API key, "
     "limits from config, 429 with Retry-After. Files: middleware/ratelimit.py, tests/test_ratelimit.py."),
    ("reviewer", "REQUEST_CHANGES", 1, "The bucket is keyed on the raw header — hash it before it reaches "
     "the store, and say what happens when the store is unreachable (fail open or closed?)."),
    ("lead", "SUBMIT_FOR_REVIEW", 2, "Key is now sha256(api_key). Store outage: fail OPEN with a warning "
     "log — availability over strictness for this service."),
    ("reviewer", "REQUEST_CHANGES", 2, "Fail-open on a store outage means the limit disappears exactly when "
     "the system is under stress. Fail closed with a short in-memory fallback bucket instead."),
    ("lead", "SUBMIT_FOR_REVIEW", 3, "I disagree: this API's SLO is availability, and a closed limiter "
     "during a Redis blip would page the on-call for a self-inflicted outage. Keeping fail-open, adding "
     "a metric + alert on the fallback path."),
    ("reviewer", "ESCALATE", 3, "Genuine product decision, not a code question: fail-open (availability) "
     "vs fail-closed (protection). Both positions are defensible; the arbiter should pick."),
]

WEB_ROUNDS = [
    ("lead", "SUBMIT_FOR_REVIEW", 1, "Implemented the checkout form validation per the approved plan; "
     "12 new tests, all green."),
    ("reviewer", "REQUEST_CHANGES", 1, "Postal-code rule rejects valid Canadian codes with a space. Add "
     "the case and normalize before validating."),
    ("lead", "SUBMIT_FOR_REVIEW", 2, "Normalized whitespace and casing before the postal-code check; "
     "added CA/UK cases."),
]

DOCS_ROUNDS = [
    ("lead", "SUBMIT_FOR_REVIEW", 1, "Implemented the docs build: mkdocs config, nav from the roadmap, CI job."),
    ("reviewer", "APPROVE", 1, "Approved. Build is reproducible and the nav matches the roadmap."),
]

BRIEF = """# Decision brief — demo-api / plan r3 (escalated)

Crux: what the rate limiter does when its backing store is unreachable.

Lead (Claude): fail OPEN — this API's SLO is availability; a closed limiter
during a Redis blip would page on-call for a self-inflicted outage.
Reviewer (Codex): fail CLOSED with a small in-memory fallback bucket — the
limit must not vanish exactly when the system is under stress.

Recommendation (confidence: medium): fail open, but keep the reviewer's
in-memory bucket as a ceiling — availability first, not unbounded.

    tagteam rule request-changes --content "Fail open, with the in-memory bucket as a ceiling."
    tagteam rule approve --content "Fail open with the metric/alert; ship it."
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _write_yaml(project: Path) -> None:
    (project / "tagteam.yaml").write_text(
        "agents:\n"
        f"  lead:\n    name: {LEAD}\n    command: claude\n"
        f"  reviewer:\n    name: {REVIEWER}\n    command: codex\n"
        "briefer:\n  enabled: true\n"
        "serve:\n  theme: cockpit\n", encoding="utf-8")


def _play(project: Path, phase: str, ctype: str, rounds) -> None:
    from tagteam import cycle
    first = True
    for role, action, rnd, text in rounds:
        if first:
            cycle.init_cycle(phase, ctype, LEAD, REVIEWER, text, project_dir=str(project), updated_by=LEAD)
            first = False
        else:
            cycle.add_round(phase, ctype, role, action, rnd, text, project_dir=str(project),
                            updated_by=LEAD if role == "lead" else REVIEWER)


def _age_state(project: Path, hours: float) -> None:
    """Make the current turn look `hours` old (state file `updated_at`)."""
    p = project / "handoff-state.json"
    st = json.loads(p.read_text(encoding="utf-8"))
    then = _iso(datetime.now(timezone.utc) - timedelta(hours=hours))
    st["updated_at"] = then
    if st.get("history"):
        st["history"][-1]["timestamp"] = then
    p.write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8")


def _seed_usage(project: Path, phase: str, ctype: str, series: list[tuple[int, str, str, int, int]]) -> None:
    from tagteam import db
    conn = db.connect(project_dir=str(project))
    try:
        base = datetime.now(timezone.utc) - timedelta(hours=3)
        for i, (rnd, role, provider, inp, out) in enumerate(series):
            db.add_usage(conn, ts=_iso(base + timedelta(minutes=9 * i)), phase=phase, type=ctype, round=rnd,
                         role=role, agent=(LEAD if role == "lead" else REVIEWER), provider=provider,
                         status="ok", exit_code=0, duration_ms=90_000 + 20_000 * (i % 3),
                         input_tokens=inp, output_tokens=out,
                         cache_read_tokens=int(inp * 0.9), cache_write_tokens=0,
                         cost_usd=(round(inp / 1_000_000 * 3 + out / 1_000_000 * 15, 3) if provider == "claude" else None),
                         num_turns=1)
        db.upsert_rate_limit(conn, provider="claude", kind="five_hour", status="allowed",
                             resets_at=_iso(datetime.now(timezone.utc) + timedelta(hours=2, minutes=40)),
                             payload={"utilization": 0.42}, ts=_iso(datetime.now(timezone.utc)))
    finally:
        conn.close()


def _seed_brief(project: Path, phase: str, ctype: str) -> None:
    from tagteam import briefer, db
    ev, why = briefer.event_for_cycle(project, phase, ctype)
    if ev is None:
        raise SystemExit(f"seed: no escalation event for {phase}/{ctype}: {why}")
    conn = db.connect(project_dir=str(project))
    try:
        now = _iso(datetime.now(timezone.utc))
        claimed = db.claim_brief(conn, ts=now, phase=phase, cycle_type=ctype, round_=ev.round,
                                 cycle_state=ev.cycle_state, event_key=ev.event_key, kind="auto",
                                 runner_pid=None, runner_ident=None, event_row_id=ev.event_row_id,
                                 provider="claude")
        if claimed is None:
            raise SystemExit("seed: brief claim refused")
        brief_id, attempt = claimed
        path = briefer.brief_path(project, ev, attempt)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(BRIEF, encoding="utf-8")
        briefer.alias_path(project, phase, ctype).write_text(BRIEF, encoding="utf-8")
        db.set_brief_stem(conn, brief_id, path.stem)
        # project-relative on purpose: the cockpit's Feed prints this path,
        # and the screenshots must not carry an absolute path
        db.finish_brief(conn, brief_id, status="ok", ts=now, stem=path.stem,
                        path=str(path.relative_to(project)).replace("\\", "/"),
                        content=BRIEF, model="claude", duration_ms=48_000)
    finally:
        conn.close()


CONVERSATION = [
    ("Before we start the next phase: the roadmap says 'search index'. I'd rather ship "
     "the CSV export first — customers keep asking. Thoughts?",
     "Reasonable. The CSV export is smaller (one endpoint + a streaming writer) and unblocks "
     "three open tickets; the search index needs the schema change we deferred. I'd swap the "
     "order: export now, index next. If you agree I'll rewrite the roadmap entry and open the "
     "plan cycle with `/tagteam:handoff start csv-export`."),
    ("Agreed — do it.",
     "Done: roadmap reordered (csv-export before search-index). Say `/tagteam:handoff start csv-export` "
     "here or press Start and I'll write the plan for review."),
]


def _seed_conversation(project: Path) -> None:
    """A canned two-turn Lead conversation: DB rows + transcript + events."""
    from tagteam import db, lead_chat
    conv = lead_chat.new_conversation(project, provider="claude", title="roadmap order before the next phase")
    cid = conv["id"]
    conn = db.connect(project_dir=str(project))
    try:
        base = datetime.now(timezone.utc) - timedelta(minutes=25)
        for i, (you, lead) in enumerate(CONVERSATION):
            ts = _iso(base + timedelta(minutes=6 * i))
            t = db.add_conversation_turn(conn, conversation_id=cid, ts=ts, user_text=you,
                                         owner_pid=None, owner_ident=None)
            log_path, events_path = lead_chat._turn_paths(project, cid, t["n"])
            events_path.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": lead}]}}) + "\n"
                                   + json.dumps({"type": "result", "result": lead, "session_id": "demo-session",
                                                 "usage": {"input_tokens": 1200, "output_tokens": 180}}) + "\n",
                                   encoding="utf-8")
            log_path.write_text(f"[claude] {lead}\n[tagteam] conversation turn ok\n", encoding="utf-8")
            db.finish_conversation_turn(conn, cid, t["n"], status="ok", ts=_iso(base + timedelta(minutes=6 * i + 1)),
                                        session_id="demo-session", reply=lead,
                                        continuity="new session" if i == 0 else "resumed session",
                                        log_path=str(log_path), events_path=str(events_path))
            lead_chat._append_transcript(project, cid, t["n"], "you", you, ts)
            lead_chat._append_transcript(project, cid, t["n"], "Claude", lead, _iso(base + timedelta(minutes=6 * i + 1)))
        db.update_conversation(conn, cid, session_id="demo-session", continuity="resumed session",
                               last_ts=_iso(base + timedelta(minutes=7)))
    finally:
        conn.close()


def _seed_running_turn(project: Path, phase: str, ctype: str) -> dict:
    """Phase 43 (3.7): a reviewer cycle turn IN FLIGHT — the marker's pid is
    a detached `sleep 3600` (so liveness is real), its log grows for ~60 s
    in the background (so the Activity row streams), and the history has a
    gate pass + three finished turns. Returns {pid, log_path}."""
    import os
    import subprocess
    from tagteam import db, headless as h, procs
    d = h.turns_dir(project); d.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    conn = db.connect(project_dir=str(project))
    try:
        # finished turns for the history (usage rows: the record of a cycle turn)
        for i, (rnd, role, status, mins) in enumerate([(1, "lead", "ok", 62), (1, "reviewer", "ok", 48),
                                                       (2, "lead", "ok", 21)]):
            stem = f"{phase}_{ctype}_r{rnd}_{role}_demo{i}"
            (d / f"{stem}.log").write_text(
                f"[tagteam] {_iso(now - timedelta(minutes=mins))} spawning: "
                f"{'claude -p' if role == 'lead' else 'codex exec'} …\n[{role}] read the round tail\n"
                f"[{role}] {'edited 3 files, ran the focused tests' if role == 'lead' else 'read the diff, ran tests/test_checkout.py'}\n"
                f"[tagteam] exit 0\n", encoding="utf-8")
            db.add_usage(conn, ts=_iso(now - timedelta(minutes=mins - 4)), phase=phase, type=ctype, round=rnd,
                         role=role, agent=(LEAD if role == "lead" else REVIEWER),
                         provider=("claude" if role == "lead" else "codex"), status=status, exit_code=0,
                         duration_ms=240_000, input_tokens=180_000, output_tokens=6_000,
                         cache_read_tokens=150_000, cache_write_tokens=0, num_turns=1,
                         log_path=str(d / f"{stem}.log"))
        gid = db.claim_gate(conn, ts=_iso(now - timedelta(minutes=16)), phase=phase, cycle_type=ctype, round_=2,
                            submission_seq=3, event_key="demo-gate-r2", kind="auto",
                            runner_pid=os.getpid(), runner_ident=procs.identity(os.getpid()))[0]
        (d / f"{phase}_{ctype}_r2_gate_demo.log").write_text(
            "[gate] .venv/bin/python -m pytest -q\n[gate] 212 passed in 171.4s\n[gate] scope: 6 paths\n[gate] plan-doc ok\n",
            encoding="utf-8")
        db.finish_gate(conn, gid, status="pass", ts=_iso(now - timedelta(minutes=13)), duration_s=181.0,
                       stem=f"{phase}_{ctype}_r2_gate_demo", reason="tests ok (212 passed) · scope 6 paths · plan-doc ok")
    finally:
        conn.close()
    # the running reviewer turn: a live pid + a growing log
    stem = f"{phase}_{ctype}_r2_reviewer_live"
    log = d / f"{stem}.log"
    started = now - timedelta(seconds=41)
    sleeper = subprocess.Popen(["sleep", "3600"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    log.write_text(f"[tagteam] {_iso(started)} spawning: codex exec --json …\n"
                   f"[tagteam] spawned pid {sleeper.pid}\n[codex] reading the round tail (r2)\n"
                   f"[codex] tool: git diff --stat main...HEAD\n[codex] tool: sed -n 1,80p tests/test_checkout.py\n",
                   encoding="utf-8")
    marker = {"kind": "cycle", "role": "reviewer", "agent": REVIEWER, "provider": "codex",
              "phase": phase, "type": ctype, "round": 2, "stem": stem, "log_path": str(log),
              "events_path": str(d / f"{stem}.events.jsonl"), "started_at": _iso(started),
              "pid": sleeper.pid, "child_ident": procs.identity(sleeper.pid),
              "watcher_pid": sleeper.pid, "watcher_ident": procs.identity(sleeper.pid),
              "owner_token": "demo", "by": "seed"}
    h.inflight_path(project).write_text(json.dumps(marker, indent=2), encoding="utf-8")
    # keep the log growing for a while (background, detached) so the running row visibly streams
    grow = ("import time,sys\np=sys.argv[1]\nlines=['[codex] tool: pytest -q tests/test_checkout.py',"
            "'[codex] 14 passed in 3.2s','[codex] reading src/checkout/validate.py','[codex] tool: rg postal src/',"
            "'[codex] drafting the review: postal-code normalisation looks right; checking the CA case',"
            "'[codex] tool: python -c \"import checkout\"']\n"
            "for i in range(60):\n    time.sleep(3)\n    open(p,'a').write(lines[i%len(lines)]+'\\n')\n")
    subprocess.Popen([sys.executable, "-c", grow, str(log)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return {"pid": sleeper.pid, "log_path": str(log)}


def seed(root: Path) -> dict:
    if root.exists():
        raise SystemExit(f"seed: {root} already exists — pick a fresh directory")
    demo = root / "demo"
    projects = {}
    for name in ("demo-api", "demo-web", "demo-docs", "demo-idle"):
        p = demo / name
        (p / "docs" / "handoffs").mkdir(parents=True)
        (p / "docs" / "escalations").mkdir(parents=True)
        (p / ".tagteam").mkdir()
        _write_yaml(p)
        # No vendored .claude/skills/handoff/: demos look like plugin installs
        # (the Start card shows /tagteam:handoff), and headless validation
        # falls back to the packaged contract (headless.resolve_skill_path).
        (p / "docs" / "roadmap.md").write_text(
            "# Roadmap\n\n### Phase 1: demo\n- **Status:** In progress\n", encoding="utf-8")
        projects[name] = p

    _play(projects["demo-api"], "rate-limit-middleware", "plan", API_ROUNDS)
    _seed_usage(projects["demo-api"], "rate-limit-middleware", "plan", [
        (1, "reviewer", "codex", 1_412_000, 6_100), (1, "lead", "claude", 52, 14_800),
        (2, "reviewer", "codex", 968_000, 5_300), (2, "lead", "claude", 61, 12_100),
        (3, "reviewer", "codex", 611_000, 4_200), (3, "lead", "claude", 48, 9_900),
    ])
    _seed_brief(projects["demo-api"], "rate-limit-middleware", "plan")
    _age_state(projects["demo-api"], hours=1.2)

    _play(projects["demo-web"], "checkout-validation", "impl", WEB_ROUNDS)
    _age_state(projects["demo-web"], hours=5.5)
    running = _seed_running_turn(projects["demo-web"], "checkout-validation", "impl")

    _play(projects["demo-docs"], "docs-build", "impl", DOCS_ROUNDS)
    _age_state(projects["demo-docs"], hours=30)

    (projects["demo-idle"] / "docs" / "roadmap.md").write_text(
        "# Roadmap\n\n### Phase 1: Auth Cleanup\n- **Status:** Complete\n\n"
        "### Phase 2: CSV Export\n- **Status:** Not started\n\n"
        "### Phase 3: Search Index\n- **Status:** Not started\n", encoding="utf-8")
    _seed_conversation(projects["demo-idle"])

    reg = root / "registry.json"
    reg.write_text(json.dumps([str(p.resolve()) for p in projects.values()], indent=2) + "\n", encoding="utf-8")
    return {"registry": str(reg), "projects": {k: str(v) for k, v in projects.items()},
            "running_turn": running}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    info = seed(Path(argv[0]).expanduser())
    print(json.dumps(info, indent=2))
    print()
    print("cockpit:  tagteam serve --theme cockpit --dir", info["projects"]["demo-api"], "--port 8080")
    print("cycle:    tagteam serve --theme cockpit --dir", info["projects"]["demo-web"], "--port 8082",
          f"   (running reviewer turn; sleeper pid {info['running_turn']['pid']} — kill it when done)")
    print("hub:      tagteam hub --registry", info["registry"], "--port 8090")
    return 0


if __name__ == "__main__":
    sys.exit(main())
