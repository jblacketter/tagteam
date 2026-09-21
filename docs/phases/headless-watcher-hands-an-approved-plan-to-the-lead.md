# Phase 68a: Headless watcher hands an approved plan to the lead

## Status
- [x] Planning: approved round 1 (2026-09-20) at `3a54c92`
- [x] Implementation: branch `phase/headless-watcher-hands-an-approved-plan-to-the-lead`
- [ ] Implementation Review
- [ ] Complete

## Where this comes from: the 2026-09-20 cockpit trial
The cockpit arc's goal is that the cockpit replaces the three iTerm2 tabs. To
see how far that is from true, one real cycle was run through the cockpit with
nothing but the browser: a scratch project (one phase: `greet.py` + a test),
`tagteam serve --theme cockpit`, "Start the watcher" → `tagteam watch --mode
headless --pidfile`. Screenshots: `.playwright-mcp/trial-*.png` (git-ignored).

What happened, from `tagteam watch log` in that project:

```
23:33:46  turn     >> codex's turn (phase: greeting-script, round: 1)
23:33:46  sent     Command: Read the handoff contract …
23:34:14  info     headless: turn ok — reviewer APPROVE at round 1 (28765 ms)
23:34:24  done     ** Cycle complete: approved
23:34:25  info     Sending completion notice to claude...
                   ← nothing runs. The cockpit shows a Start card and waits.
23:34:43           (arbiter clicks Start → the lead's impl turn runs as a *chat* turn)
23:35:25  turn     >> codex's turn …      23:36:08  turn ok — reviewer APPROVE
23:36:18  done     ** Cycle complete: approved
```

Findings, and where each one goes:

| # | Finding | Goes to |
|---|---|---|
| 1 | After a plan approval the headless watcher sends the lead nothing; the loop stops until a human clicks Start. In iTerm2 the watcher's notice ("The plan for X is approved: implement it now…") keeps it moving. | **this phase** |
| 2 | `tagteam watch status` said `STALE: 37s ago … nothing in flight` seconds after a 33 s turn ended. The Phase 67 heartbeat is written only *before* `tick()`; after a long tick it stays old until the next loop iteration (≤ one interval). A false alarm in 3.14.5. | **this phase** |
| 3 | Because of #1 the lead's implementation ran as a lead *conversation* (`YOU: /tagteam:handoff start … impl`), holds the slot as `kind=conversation`, and renders differently from a cycle turn. With #1 fixed it becomes an ordinary cycle turn. | this phase (consequence) |
| 4 | Reviewer lane = a card with a ~6-line log window; lead lane = chat bubbles. Two looks, neither a terminal. | Phase 68 |
| 5 | Now strip: four equal pills; `watcher: on` has nothing behind it. | Phase 68 |
| 6 | When headless is unavailable, "Start the watcher" offers notify mode ("this page cannot run turns for both agents") without the reason (in the trial: no `docs/roadmap.md`). | Phase 68 |
| 7 | On this repo (iTerm2 workflow): the lanes hold only gate runs — shown under the *reviewer's* name, `BOUNCED` reading as if codex bounced — and the lead lane shows Phase 52 content under the current cycle's header. | Phase 68 |
| 8 | The Start card proposed Phase 44 although three phases were startable. | Phase 69 |

What worked, so it is on record: both headless reviews ran and approved (29 s,
33 s); the lead's turn wrote both files and `python3 test_greet.py` → 3 passed
(run by hand afterwards); the lead lane streamed the turn live; the Phase 67 log
recorded every dispatch kind, including `resumed` after a busy turn slot.

## Implementation notes
- Approval note 1: the new path requires `run_mode == "single-phase"`
  explicitly (default when absent). Two tests pin that a full-roadmap run which
  declined to advance — no roadmap metadata; a staleness guard — is not retried
  through it.
- Approval note 2: the post-tick beat carries the state *after* the tick.
  `_beat_after_tick()` re-reads the state only when `Sink.due()` says a beat
  would be written, so a short tick costs neither a write nor a read.
- Approval note 3: the normal-advance test first ticks a witnessed `ready`
  state; a separate test pins that a first tick on an already-approved plan
  does nothing.
- The plan-approval macOS notification is kept in the new path (the old code
  sent it before the dead notice). "Sending completion notice…" is no longer
  printed by a headless watcher for *any* completion — it never had anywhere
  to send one; tab, tmux and notify watchers print and send exactly as before.
- The contract needed no change: it already says `start [phase] impl` is what
  the lead runs "or is handed by the watcher after plan approval".
- 9 of the 14 new processor tests fail on the previous `watcher.py`; the other
  5 pin unchanged behaviour (three terminal modes, full-roadmap, first tick).

### Criterion 8 — real processes, 2026-09-20 23:46–23:49 PDT (manual)
Scratch project `trial2` (one phase, `wc.py` + test), plan cycle opened by
hand, then only `tagteam watch --mode headless --pidfile`. Non-`info` events
from `tagteam watch log`:

```
23:46:56  start    Watching handoff-state.json (interval: 10s, mode: headless)
23:46:56  turn     >> codex's turn (phase: word-count, round: 1)
23:46:56  sent     Command: Read the handoff contract …
23:47:36  done     ** Cycle complete: approved
23:47:37  advance  AUTO-ADVANCE: plan approved → lead implements (phase: word-count)
23:47:47  turn     >> claude's turn (phase: word-count, round: 1)
23:47:47  sent     Command: Read the handoff contract …, tagteam.yaml, and …
23:48:31  turn     >> codex's turn (phase: word-count, round: 1)
23:48:31  sent     Command: Read the handoff contract …
23:49:11  done     ** Cycle complete: approved
```

No click, no cockpit. The lead's turn is a cycle turn
(`.tagteam/turns/word-count_plan_r1_lead_….log`), not a conversation. Final
state `impl / done / approved`; `python3 test_wc.py` → 6 passed, exit 0 (run by
hand). `tagteam watch status` sampled every 3 s for the whole run and 18 s
after it: 39 samples, **0 STALE** (the first trial showed one within seconds of
a turn ending). One run — an observation, not a rate.

## Summary
Two engine fixes that make a headless cycle run end to end by itself, as the
iTerm2 one does. No UI.

## Scope
**In**

1. **Plan approved → the lead's owed turn (headless, single-phase).**
   `_StateProcessor._handle_done`: when `mode == "headless"`, the state is
   `type: plan`, `result: approved`, and `_try_roadmap_advance()` did not act
   (i.e. not full-roadmap), apply the same transition full-roadmap mode applies
   at this point:

   ```
   turn: lead · status: ready · result: None
   command: "<handoff_command(project)> start <phase> impl"
   ```

   via `update_state(..., expected_seq=seq)` with the same two staleness guards
   (state already `type: impl`; lead already submitted). `run_mode` is left
   untouched. The shared part of `_try_roadmap_advance`'s plan branch is
   factored into one helper both callers use — no second copy of the guards.
   - The next tick sees a new seq, `_handle_ready` runs, and
     `engine.run_owed_turn()` spawns the lead with a `start … impl` command —
     a shape the engine already identifies (`TurnIdentity.is_start`,
     `target_type`) and verifies (an impl cycle for that phase must exist
     afterwards). Nothing in `headless.py` changes.
   - A held pause marker holds it like any other owed turn; the turn-slot,
     gate and watchdog rules are untouched.
   - Logged as `kind="advance"`: `AUTO-ADVANCE: plan approved → lead
     implements (phase: X)` — the same line full-roadmap prints.
   - The dead "Sending completion notice…" line is no longer printed in
     headless mode, where it described something that did not happen.
   - **Impl approved** in single-phase mode: unchanged — the run stops and
     the arbiter is notified. That is the documented default ("notify me at the
     end of each phase"); changing it is Phase 70 (standing orders).
   - **Not on restart:** a watcher started on an already-`done` state records
     it and waits (existing first-poll rule) — the same as a tab watcher, which
     sends no notice for an approval it did not witness. The cockpit's Start
     card remains the way to pick such a cycle up.
   - Tab, tmux and notify modes: unchanged.
2. **Heartbeat after the tick** (`tagteam/watcher.py`): `_beat(state)` is also
   called when `processor.tick()` returns, in the poll loop and in the event
   loop's `on_change`. The 5 s throttle makes it free for a short tick and
   writes it for a long one, so `watch status` stops reading STALE in the
   window between a turn ending and the next iteration.
3. **Docs** — `CLAUDE.md` (watcher paragraph), `README.md`'s headless section
   if it describes the plan→impl step, the contract
   (`tagteam/data/.claude/skills/handoff/SKILL.md` + the plugin copy, kept
   identical) **only if** its text says a headless single-phase run waits for
   the arbiter after plan approval — checked during implementation; contract
   hashes regenerated if it changes.

**Out**
- Every cockpit file (Phase 68). The Start card keeps working; after this
  phase it is simply not needed for the plan→impl step while a headless
  watcher is running.
- Auto-advancing to the *next phase* in single-phase mode (that is
  full-roadmap mode / Phase 70).
- A configuration switch for the new behaviour. It matches what every tab
  watcher already does; `tagteam pause` holds it. If the arbiter wants "stop
  after plan approval", that is a standing order (Phase 70).
- Changing how a lead *conversation* turn is rendered or slotted.

## Technical approach
- Why reuse the full-roadmap transition instead of spawning the lead directly
  from `_handle_done`: the state machine stays the single source of truth —
  the cockpit, `tagteam state`, the watchdog and the pause marker all see an
  ordinary owed lead turn; the engine's existing `start` verification applies;
  and there is one code path for "plan approved → implement", not two.
- Why headless only: in tab modes the typed notice works and the lead's long
  session is the user's; rewriting that path would change working behaviour
  for no gain (arbiter rule: don't rewrite working code).
- Risk: a project whose arbiter relied on the headless loop *stopping* after
  plan approval to read the plan first. Mitigation: release note; `tagteam
  pause` before approval holds the hand-off; Phase 70 makes it a rule.

## Files
- Modified: `tagteam/watcher.py`, `tests/test_watcher.py` and/or
  `tests/test_headless.py` (processor + engine-facing tests),
  `tests/test_watchlog.py` (beat after tick), `CLAUDE.md`, `README.md`,
  `docs/roadmap.md`; the contract pair + hashes only if the check in Scope 3
  says so.

## Success criteria
1. Headless processor, single-phase, state `done / plan / approved`: one tick
   writes `turn: lead, status: ready, command: "… start <phase> impl"`,
   `run_mode` still `single-phase`, logs one `advance` event, prints no
   "Sending completion notice"; the following tick calls
   `engine.run_owed_turn` with that state.
2. Guards: state already `type: impl` → no write; lead already submitted → no
   write; seq moved between read and write → no write, `SKIP` logged.
3. `impl / approved` single-phase headless → no advance, notification as
   today. `plan / approved` in iterm2, tmux and notify modes → behaviour and
   printed lines identical to today (existing tests unchanged).
4. Full-roadmap plan→impl advance still passes its existing tests through the
   shared helper.
5. First tick on an already-`done/plan/approved` state → recorded, no advance.
6. A held pause marker holds the advanced turn; resume dispatches it once.
7. Beat: a tick that takes longer than the throttle leaves a beat newer than
   the tick's end (poll loop and event loop); a short tick writes no second
   beat.
8. End to end, real processes, in a scratch project (reported as manual, with
   the `watch log`): plan cycle opened by hand → headless watcher → reviewer
   approves → lead implements and opens the impl cycle **with no click** →
   reviewer approves → watcher idle; `watch status` never reads STALE while
   idle between turns.
9. Full suite green via the gate; checkout clean afterwards.
