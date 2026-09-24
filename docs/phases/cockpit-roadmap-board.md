# Phase 69: Cockpit roadmap board

## Status
- [ ] Planning: plan cycle open (round 2 — r1: exhaustive grouping incl. run-completed/aborted; one board-wide launch guard + endpoint refusal)
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Summary
The cockpit shows one phase: the Needs-you **Start** card. It is chosen by
`launch._next_after()`, which is **document order, blind to dependencies**, so
it can offer a phase whose `Depends on:` is unmet. The live trial on
2026-09-20 saw this: the card proposed Phase 44 while three other phases were
startable. The arbiter cannot see the rest of the roadmap, cannot tell ready
from blocked, and cannot choose.

Add a **roadmap board**:
- one server-side reading of the roadmap (a CLI command first, then the
  cockpit);
- a **Roadmap** tab with **Done · In progress · Up next**, where Up next is
  split into **ready** and **blocked** (with what each one waits for);
- `roadmap check` problems shown inline;
- **Start on any ready phase**.

The separate Start card goes away. The tabs are regrouped by intent: **Now ·
Roadmap · Rules · History · Usage**.

## Scope
**In**
- `roadmap.board(root)`: the one reading, as a dict. File-only.
- New CLI `tagteam roadmap board [--json]`.
- `GET /api/roadmap`.
- A dependency-aware launch intent that can target a chosen ready phase.
- The Roadmap tab.
- Removing the Start card.
- The tab regroup.

**Out**
- Editing the roadmap from the page: reordering, status changes, adding
  phases.
- Full-roadmap-mode controls (`start --roadmap`). The standing order
  `stop: roadmap` from Phase 70 already covers "run to the end".
- The hub (multi-project) page, except that its `launch_intent` call keeps
  working unchanged.

## Technical approach

### 1. `roadmap.board(root) -> dict`: the one reading
It reads files only (`docs/roadmap.md`, `handoff-state.json` and the current
cycle's status file), opens no database and creates nothing, so it is allowed
under `TAGTEAM_READ_ONLY`.

```
{ "problems": [...],            # graph_problems(): identity + edge problems (blocking)
  "warnings": [...],            # roadmap check's warn: lines (e.g. [Name] placeholders)
  "groups": {
    "done":        [row...],    # terminal status (is_terminal_status)
    "in_progress": [row...],    # see below
    "ready":       [row...],    # up next: dependencies all met
    "blocked":     [row...] },  # up next: with `unmet`
  "current": {phase, type, round, state, next_step} | null,
  "run": {"mode", "completed"} }
row = {slug, number, name, status, depends_on, unmet, line, start: intent|null}
```

**Grouping: one pure classifier, exhaustive** (plan r1 review).
`roadmap.classify(phases, *, state, cycle_status, completed) -> groups` is
pure (no I/O, no intents). `board()` and `launch_intent()` both call it; they
never call each other, so there is no recursion. Rules, first match wins:

1. **done**
   - `done_by: "roadmap"`: the roadmap status is terminal.
   - `done_by: "run"`: the phase is in the active full-roadmap run's
     `completed` list, even though the document status is stale (say, still
     `Not started`). The row shows the provenance: "completed in this run —
     the roadmap still says: Not started".
2. **in_progress**, with the reason on the row:
   - `current`: the state's current phase, while its cycle is live
     (`in-progress`, `escalated`, `needs-human`) or its plan is approved and
     not yet implemented;
   - `approved`: the current impl cycle is approved but the roadmap is not
     yet terminal. It is awaiting merge, and its status explains that;
   - `declared`: any other phase whose status starts with `In progress`,
     `In review` or `✅ Approved`. It is **not** offered Start. The row says
     "the roadmap marks this in progress — change its status to start it
     again", which is the explicit recovery for a stale declaration.
3. **ready** / **blocked**: every other phase, split by unmet dependencies
   (the run's `completed` counts as met).
   - **An aborted current cycle is not in progress.** It falls here like any
     other unfinished phase, and so can be started again, with a note:
     "last cycle aborted (round N)". That matches the launch state machine,
     which already treats `aborted` as non-blocking.

**Invariant:** every parsed phase appears in exactly one group. That is
asserted in `classify()` (a missed phase is a bug, raised in tests) and
pinned by a test over every fixture.

If the graph has problems, the groups are still built from `parse_roadmap`
(so a broken roadmap still shows its phases), but **no row offers Start**,
and each problem is listed with its `(line N)` from Phase 66.

### 2. Start on a chosen phase: one server-side rule
`launch.launch_intent(root, *, phase=None, …)` gains an optional `phase`,
and its "next phase" branches stop using `_next_after`. Readiness comes from
`classify()`:
- **No active cycle, the current impl approved, or the current cycle
  aborted:** the intent may target **any ready phase**, provided there are no
  graph problems. Without a `phase`, it targets the first ready phase in
  topological order. That fixes the document-order bug for the hub and for
  every other existing caller.
- **Plan approved, awaiting impl:** the only intent is `start <phase> impl`
  for *that* phase. The board shows the reason on the ready rows.
- **A cycle in progress, dispatch paused, not set up:** no intent, the
  reason as today.

`board()` attaches to each ready row the intent `launch_intent(root,
phase=row)` returns. That is the same object `POST /api/start/launch`
already receives, so `launch()` recomputes the live intent **for the phase
the client named** and requires `command` and `observed` to match; a phase
that stopped being ready gets a 409. The orphan reconciliation in
`cockpit_api` recomputes the intent for the row's own phase. The hub calls
without `phase` and gets the dependency-aware default.

**One board-wide launch guard** (plan r1 review). Before this phase, the
Start card showed nothing while anything was busy (`inflight`, a pending
launch, or the headline `working` / `starting` / `launching`). The board
keeps that rule for **every** Start it shows: the ready rows and "Start
implementation".
- `cockpit_api.roadmap_payload()` = `board()` plus
  `launch: {available: bool, reason}`. It is computed server-side from the
  same facts the old card used: the in-flight marker (a lead conversation
  turn included: "the lead is busy in a conversation"), a `pending` launch
  row, and the headline state. When `available` is false, **no** row shows a
  runnable Start; every Start slot shows the reason instead.
- **The endpoint enforces it too.** `launch()` gains an up-front refusal,
  inside its claim lock: if **another** launch row is `pending`, or the turn
  slot is held, it returns 409 "another start is in progress — wait for it
  to finish" **without** creating a launch row. Today a second, different
  Start would claim its own row and then fail on the busy slot. It would
  deliver no second turn, but it would leave a noisy failed launch; now it
  is refused cleanly. The same-intent idempotency (a double click gives the
  existing 202) is unchanged.
- The CLI `roadmap board` is file-only and prints commands, not buttons, so
  it carries no launch guard.

### 3. The Roadmap tab
- There are four sections in intent order: **In progress** (open), **Up
  next — ready** (open), **Up next — blocked** (open, with each row's "waits
  for: X, Y"), and **Done** (collapsed, with a count).
- Each row shows: number, name, status (verbatim, one line, the full text
  on expand), and `depends on`.
- **Ready rows:**
  - **Start** (headless valid): the existing `/api/start/launch` confirm and
    flow, fed the row's intent.
  - **Copy command** (always).
  - When the plan-approved rule blocks them, the reason replaces the
    buttons.
- **The in-progress row** for the current cycle shows its round/state, plus
  **Start implementation** when its plan is approved and `launch.available`
  is true. The same board-wide guard applies to every Start, including the
  68a automatic plan→impl handoff: while that turn is starting or working,
  nothing is offered.
- **Problems** go in a banner at the top, each with its line; **warnings**
  go in a quieter line.
- The payload is presented, never derived. The slice never works out
  grouping, readiness or dependencies itself, and a test pins that.

### 4. The Start card goes away
- The Needs-you `start` card, and the reason text it carried, are removed.
- When nothing needs you and something is ready, the quiet Needs-you line
  says "Nothing needs you — <n> phase(s) ready on the **Roadmap** tab" (a
  link to the tab). The count comes from the server (`/api/start` gains
  `ready_count`).
- A failed launch for the current intent (the Phase 43 inline error) moves
  to the row it belongs to.

### 5. The tab regroup: Now · Roadmap · Rules · History · Usage
| New tab | Holds (existing panels, IDs kept) |
|---|---|
| **Now** | Notes (the interjection form + list) and the *all activity* disclosure (moved from Rounds) |
| **Roadmap** | new |
| **Rules** | unchanged |
| **History** | Rounds (the feed) and Diff, one above the other, with Diff collapsed until opened (so it still loads lazily) |
| **Usage** | unchanged |

- Existing element IDs are kept, so SSE refresh, the node tests and the
  Chromium tests keep addressing the same nodes.
- A saved tab from an older page (`feed` / `diff` / `notes`) maps to
  History / History / Now.
- The notes count badge moves to **Now**.
- The default tab is **Now**.

### 6. CLI
`tagteam roadmap board [--json]` prints the four groups: slug, status,
unmet dependencies for blocked rows, and the start command for ready rows.
Problems and warnings come first. It is a read, so it goes into
`READ_ONLY_COMMANDS` (`roadmap` already allows `queue/phases/check/graph/
ready`; `board` joins them).

## Files
- `tagteam/roadmap.py`: `board()`, `_group()`, the `board` subcommand.
- `tagteam/roadmap.py`: `classify()`, the pure classifier shared by `board()` and `launch_intent()`.
- `tagteam/launch.py`: `launch_intent(phase=)`, a dependency-aware default, the up-front "another start is in progress" refusal,
  the plan-approved rule, and `launch()` recomputing for the named phase.
  `_next_after` is removed, with its callers updated.
- `tagteam/cockpit_api.py`: the orphan check recomputes for the row's
  phase; `/api/start` gains `ready_count`.
- `tagteam/server.py`: `GET /api/roadmap`.
- `tagteam/cli.py`: `READ_ONLY_COMMANDS["roadmap"]` += `board`.
- `tagteam/data/web/cockpit.html` / `.css` / `.js`: the Roadmap slice, the
  tab regroup, and removal of the Start card.
- Docs: how-tagteam-works (the Start card section is rewritten as the
  Roadmap tab), README (`roadmap board`).
- Tests: `tests/test_roadmap_board.py` (new), plus updates to
  `tests/test_launch*.py`, `tests/test_cockpit_*.py` and
  `tests/test_hub*.py` where they pin document order.

## Success criteria
1. **Dependency-aware default.** With a roadmap where document order ≠
   dependency order (A, B depends on C, C), the default intent (hub, `/api/
   start`) targets **A**. After A is approved it targets **C**, never B. This
   is a regression test for the old `_next_after` behaviour.
2. **Choosing a phase.** Every ready row carries a `start` intent that
   `POST /api/start/launch` accepts, run end to end in a scratch project. A
   blocked phase, a phase made unready after the payload was read, and a
   phase while another cycle is active are each refused with a 409 and
   nothing started.
3. **The plan-approved rule.** With a plan approved and not implemented, no
   ready row can be started, and the in-progress row offers "Start
   implementation", only when nothing is running.
4. **Grouping is pinned by a table test** covering terminal, current cycle,
   declared in-progress statuses (including `✅ Approved`), ready, blocked
   (with `unmet`), an active full-roadmap run's `completed`, and a roadmap
   with graph problems (groups shown, no Start, problems with their lines).
5. **`tagteam roadmap board [--json]`** and the `board` part of
   `GET /api/roadmap` return the same structure. `board()` is file-only,
   creates nothing, and works read-only, including on the graph-error path.
   The API adds only the `launch` guard, computed from the facts `/api/now`
   already reads.
6. **The Start card is gone.** The quiet line points at the Roadmap tab with
   the server's ready count. No existing Needs-you card changes otherwise.
7. **The tab regroup:** five tabs in the stated order, the old saved-tab
   names mapped, the notes badge on Now, and Diff still loading only when
   opened. The existing cockpit tests pass, updated only where they named a
   tab.
8. **Looked at in a real page** (a scratch project with a dependency graph).
   The ready, blocked and in-progress rows are shown; Start from a ready row
   runs the lead's first turn; a problem roadmap is shown. Screenshots are
   listed, and states that were only table-tested are named.
9. **Exhaustive grouping** (plan r1 review):
   - every parsed phase appears exactly once in every fixture;
   - A completed in the active run while its roadmap status is still
     `Not started`, with B current: A is `done_by: run` with the stale status
     shown, and B is in progress;
   - a current **aborted** cycle whose dependencies are met: it is **ready**,
     with the "last cycle aborted" note, and its Start intent is accepted;
   - a `declared` in-progress phase shows the recovery text and no Start.
10. **Board-wide launch guard** (plan r1 review), with two ready phases A and
    B:
    - start A (a blocking send stub: pending, before any cycle exists) and
      read the board: `launch.available` is false, and neither A nor B has a
      runnable Start;
    - POST a stale Start for B: 409 "another start is in progress", no launch
      row, no second lead turn;
    - an unrelated lead conversation turn in flight makes `available` false,
      with that reason;
    - during the automatic plan→impl handoff (headline starting/working),
      "Start implementation" is not offered.
    Tested at the API level and in real Chromium.

## Risks and open questions for the reviewer
- **Removing `_next_after` changes what the hub and `/api/start` propose**
  whenever document order and dependency order differ. That is the bug
  being fixed, but it is a behaviour change, and it is pinned by criterion 1.
- **"Declared in-progress" status words** (`In progress` / `In review` /
  `✅ Approved`) are a small closed list, matched at the start of the status.
  Any other non-terminal status is ready or blocked. The list is one
  constant.
- **The tab regroup stays in this phase** (the reviewer agreed in r1).
