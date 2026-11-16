# Phase 77: Panel turn notice

## Status
- [x] Planning (approved round 1, 2026-11-15)
- [x] Implementation
- [x] Implementation Review: approved round 1 (2026-11-15); gate: 2,713 passed, 5 skipped at `9946059`
- [x] Complete (merged 2026-11-16, PR #67)

## Summary
When the reviewer panel takes a reviewer turn, nothing tells the arbiter. The watcher runs the
lenses as headless `codex exec` children and logs them in the watcher tab, and the interactive
reviewer pane is (correctly) never sent the turn. To someone watching the reviewer pane, the
reviewer looks asleep.

Field report (QA, 3.14.11, 2026-10-29, Phase 48 impl r1 to r8): the arbiter read the silent Codex
pane as a dispatch bug and nudged it on every impl round. Each nudge made Codex review a second
time, outside the panel. One of those extra reviews was saved as a separate supplement file and
never entered the merged verdict. The same report was made on 2026-09-12. Evidence:

- The watcher log shows `Sent to codex` for all four plan rounds of the session. The Codex pane's
  scrollback holds those four prompts. The arbiter's manual nudges start only at the first impl
  round, and each impl round's log ends `panel: merged ... (reviewer not dispatched)`.
- QA is the only one of the arbiter's ~50 registered projects with `panel.enabled: true`, which
  is why the arbiter calls this "new behavior": every other project sends every reviewer turn to
  the pane.

This phase makes a panel turn visible where the arbiter is looking. It does not change what the
panel does, when it runs, or how its verdict is merged.

## Scope

### 1. A desktop notification when a panel run starts
`run_panel()` gains an optional `notify: Callable[[str, str], None] | None` argument, the same
shape the briefer already takes (`briefer.run_briefer(..., notify=...)`). Right after a panel row
is claimed (the point that logs `panel: <event_key> attempt N (<kind>) - lenses: ...`,
`tagteam/panel.py:707`), it calls, best-effort:

    notify("Tagteam", "Panel reviewing <phase> <type> r<N> (<lenses>). The <Reviewer> pane is not used for this turn.")

`<Reviewer>` is `spec.reviewer_name` capitalised, or "reviewer" when unset. The watcher passes
`notify=notify_macos` from `_maybe_panel`. A notifier failure never changes the panel result
(caught like the briefer's).

Fires once per claimed attempt, never on the paths that return before a claim: `not-ready`,
`stale`, `not-applicable` (a plan cycle when `on: [impl]`), `deferred`/slot busy (which retries
every tick and would spam), or an already-decided row. A manual `tagteam panel run` passes no
notifier, so it stays silent.

### 2. The verdict says it is in
When `_maybe_panel` gets `merged`, it also notifies:

    "Panel: <APPROVE|REQUEST_CHANGES> on <phase> <type> r<N>. Turn is <Lead>'s."

(or "Cycle approved" when the merged approve closes the cycle). This is the moment the arbiter
would otherwise have nudged the pane. A `fallback` result needs no new notice: the ordinary
reviewer dispatch that follows is itself visible in the pane.

### 3. The watcher's start banner says the pane will be quiet
The existing line `Panel: on (impl cycles; lenses: ...; reviewer via codex)`
(`tagteam/watcher.py:1531`) gains a second line, printed only in a pane mode (iterm2, terminal,
tmux):

    Panel: on impl turns the reviewer pane is not sent the turn; the lenses run here.

### 4. Contract note
One sentence added to the gatekeeper/panel paragraph of the contract, for the arbiter and the
reviewer: on a paneled cycle type, the interactive reviewer is not woken; do not prompt it for
that turn, because a review made outside the panel is not part of the verdict.

## Out of scope / rejected
- **Writing a notice into the reviewer pane's composer** (the first idea). Rejected: text left in
  Codex's composer would be prefixed to the next real dispatch (a panel `fallback`, or the next
  plan turn) or to the arbiter's own prompt, and `_recover_stuck_codex` sends an extra Enter when
  it sees a message sitting in the composer, so a stray notice could be submitted as a prompt.
- **Renaming the reviewer tab while the panel runs.** Rejected after measuring: the agent CLIs set
  their own tab titles (the QA panes read `QA (codex)` and a session title from Claude Code, not
  the `Reviewer`/`Lead` names `create_session` set), so a title tagteam writes would be
  overwritten.
- Changing the panel's behaviour, dispatching the pane in parallel, or cockpit work (the cockpit
  already shows panel runs in its activity strip).

## Technical Approach
- `tagteam/panel.py`: `run_panel(..., notify=None)`; one guarded call after the claim log line.
- `tagteam/watcher.py`: `_maybe_panel` passes `notify=notify_macos` and notifies on `merged`
  using the round's verdict from the `PanelResult`/merged entry; the banner line in the start
  summary, gated on pane modes.
- Contract text: the panel paragraph in `plugin/skills/handoff/SKILL.md` and its packaged copy
  `tagteam/data/.claude/skills/handoff/SKILL.md` (byte-identical today; both updated).

## Files
- `tagteam/panel.py`
- `tagteam/watcher.py`
- `plugin/skills/handoff/SKILL.md`, `tagteam/data/.claude/skills/handoff/SKILL.md` (one sentence)
- `tests/test_panel.py`, `tests/test_watcher.py` (or the closest existing watcher panel tests)
- `docs/roadmap.md`, `docs/phases/panel-turn-notice.md`

## Success Criteria
1. A panel run that claims an attempt calls the notifier exactly once with the phase, type,
   round and lens names; the not-ready, stale, not-applicable, deferred and already-decided paths
   call it zero times (tests with a recording notifier).
2. A raising notifier does not change the `PanelResult` (test).
3. `_maybe_panel` on `merged` notifies once with the verdict; on `fallback` it does not (tests,
   with `run_panel` patched as the existing watcher panel tests do).
4. The start summary prints the pane line for iterm2/terminal/tmux and not for headless (test).
5. Full suite green through the gate.
6. Arbiter acceptance (post-merge, on QA): the next paneled impl round raises a "Panel reviewing"
   notification and a verdict notification, with no text sent to the Codex pane.

## Closeout
`tagteam report --phase panel-turn-notice`:

    Phase report: panel-turn-notice — plan approved r1 · impl approved r1
      plan   1 round · 0 change requests · 0 bounces
      impl   1 round · 0 change requests · 0 bounces · gate 1 run, 6m 04s
      time   start→approve 427h 30m · implementation before first submit 4m 46s
      usage  no usage rows stored under this phase

The 427 hours are the plan waiting for its review from 2026-10-29 to 2026-11-15, not work.
Success criterion 6 (a real notification on QA) is still open: it is the arbiter's check after merge.
