# Phase 69: Cockpit roadmap board

## Status
- [ ] Planning: plan cycle open (round 1)
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

**Grouping**, first match wins:
1. **done**: the roadmap status is terminal.
2. **in_progress**: the state's current phase, while its cycle exists and
   has not ended with an impl approval that the roadmap already marks
   terminal; or a phase whose status declares work under way (it starts with
   `In progress`, `In review` or `✅ Approved`, the words this repo and the
   seeded roadmap use). The row shows its status text verbatim. An approved,
   unmerged phase (like 71b today) therefore reads "in progress", with its
   own status explaining it is awaiting merge.
3. **ready** / **blocked**: the remaining actionable phases, split by
   `roadmap.ready_phases` / `blocked_phases`, using the active run's
   `completed` list exactly as `tagteam roadmap ready` does. They are in
   topological order (`topological_queue`), so "ready" lists what the queue
   would pick first at the top.

If the graph has problems, the groups are still built from `parse_roadmap`
(so a broken roadmap still shows its phases), but **no row offers Start**,
and each problem is listed with its `(line N)` from Phase 66.

### 2. Start on a chosen phase: one server-side rule
`launch.launch_intent(root, *, phase=None, …)` gains an optional `phase`,
and its "next phase" branches stop using `_next_after`:
- **No active cycle, or the current impl is approved:** the intent may
  target **any ready phase**, meaning in the board's ready group, with no
  graph problems. Without a `phase`, it targets the first ready phase in
  topological order. That fixes the document-order bug for the hub and for
  every other existing caller.
- **Plan approved, awaiting impl:** the only intent is `start <phase> impl`
  for *that* phase. Another phase cannot be started over an approved,
  unimplemented plan. The board shows the reason on the ready rows, and the
  in-progress row offers "Start implementation".
- **A cycle in progress, dispatch paused, not set up:** no intent, the
  reason as today.

`board()` attaches to each ready row the intent `launch_intent(root,
phase=row)` returns. That is the **same** object `POST /api/start/launch`
already receives, so the launch path keeps its guards. `launch()` recomputes
the live intent **for the phase the client named**
(`launch_intent(root, phase=intent["phase"])`) and requires `command` and
`observed` to match, exactly as today. A phase that stopped being ready, or
a state that moved on, gets a 409 "state changed — refresh and start
again". The orphan reconciliation in `cockpit_api` (line ~1212), which
compares launch keys, recomputes the intent for the row's own phase. The hub
calls `launch_intent` without `phase` and gets the dependency-aware default.

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
  **Start implementation** when its plan is approved and the headline says
  nothing is already running (the 68b rule: never offer Start beside a turn
  that is running).
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
- `tagteam/launch.py`: `launch_intent(phase=)`, a dependency-aware default,
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
5. **`tagteam roadmap board [--json]`** and `GET /api/roadmap` return the
   same structure. Both are file-only, create nothing, and work read-only.
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

## Risks and open questions for the reviewer
- **Removing `_next_after` changes what the hub and `/api/start` propose**
  whenever document order and dependency order differ. That is the bug
  being fixed, but it is a behaviour change, and it is pinned by criterion 1.
- **"Declared in-progress" status words** (`In progress` / `In review` /
  `✅ Approved`) are a small closed list, matched at the start of the status.
  Any other non-terminal status is ready or blocked. The list is one
  constant.
- **Split?** The tab regroup (§5) is independent of the board. If you'd
  rather review it separately, it can become 69b.
