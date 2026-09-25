# Phase 74b: Owed start after a roadmap advance

## Status
- [x] Planning: none needed. This is a small fix that was already implemented, so it goes straight to an impl cycle, as the arbiter's rule for small fixes says.
- [ ] Implementation: branch `fix/owed-start-participant-guard`
- [ ] Implementation Review
- [ ] Complete

## Summary
In full-roadmap mode, every phase boundary stalled. After an impl was approved, the watcher
handed the lead the next phase's plan. Then both of these were refused with a participant
mismatch:
- the watcher's dispatch to the lead;
- the lead's own `tagteam cycle init`.

The run only continued once the watcher was restarted on a patched tree.

## Evidence
From Liminal's full-roadmap run (`~/projects/journaling/Liminal/.tagteam/watcher-events.jsonl`),
tagteam 3.14.9. The watcher was pid 13833 in iTerm2 mode. Times are UTC; 05:46 UTC is 22:46 PDT
on 2026-09-24.

```
2026-09-25T05:46:26 advance  AUTO-ADVANCE: impl approved → lead starts next phase (said-it-before)
2026-09-25T05:46:36 refused  REFUSED: Participant mismatch: cycle lead/reviewer=(None, None);
                             configured lead/reviewer=('claude', 'codex'). …
2026-09-25T06:01:48 watchdog Watchdog: state still 'ready' after 15m — re-sending command (1/2)
2026-09-25T06:01:48 refused  REFUSED: Participant mismatch: cycle lead/reviewer=(None, None); …
2026-09-25T06:03:30 stop     Watcher stopped.
```

The watcher that replaced it (pid 75414) ran this fix, because the tagteam install is editable
from this tree. Each later boundary auto-advanced cleanly (Phases 35 → 36 → 37 → 38).

## Cause
When the impl is approved, `watcher._try_roadmap_advance` writes this state:
`{phase: <next>, type: plan, round: 1, turn: lead, status: ready}`. It also moves
`roadmap.current_index` forward. It creates no cycle, because the lead's `cycle init` does that.

`participants.check_participants` looks up the cycle for the state's (phase, type) and finds
nothing. The state's status is `ready`, so it calls `compare((None, None))`, which raises
`ParticipantMismatch`. This check runs on two paths:
- the watcher's dispatch;
- `cycle init`. Its `proposed=` path also goes through the state's target.

The single-phase plan → impl advance (`_advance_plan_to_impl`) is not affected. It leaves the
state on the approved plan cycle, which does exist.

## Fix
`tagteam/participants.py`: `_owed_start(state, phase, kind)` skips the comparison only when
the state is exactly the one the roadmap advance writes:
- turn `lead`, status `ready`, `run_mode` `full-roadmap`;
- type `plan`, and (phase, kind) is the state's target;
- `roadmap.queue[roadmap.current_index] == phase`.

Every other state with no cycle is still refused. `cycle init` still compares the proposed
names against the configuration, so a role switch at the boundary is still caught.

## Tests
`tests/test_participants.py`:
- Two positive cases drive the real `_try_roadmap_advance`:
  - dispatch is allowed;
  - the lead's `init_cycle` succeeds and records the configured names.
- One refusal case: `proposed=('codex', 'claude')` at the boundary.
- Seven near-miss states, each still refused:
  - a reviewer turn;
  - `working`;
  - `impl`;
  - `single-phase`;
  - a queue entry for another phase;
  - an out-of-range index;
  - no roadmap.
