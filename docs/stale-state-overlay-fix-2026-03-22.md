# Stale State Overlay Fix

Date: 2026-03-22
Status: Documented for follow-up in `~/projects/ai-handoff`

## Summary

A handoff-state bug can leave a new cycle with stale top-level fields from the previous cycle or previous roadmap run. In practice, this made the reviewer/lead state look inconsistent even though the per-cycle status file was correct.

The concrete symptom we hit was:
- the active cycle had correctly moved to `lead` / `ready`,
- but `handoff-state.json` still retained stale `result: "roadmap-complete"` and stale `roadmap` metadata from the previous completed roadmap,
- which made Claude appear stuck or misoriented until the top-level state was manually normalized.

## Where The Bug Lives

Relevant code paths:
- `ai_handoff/cycle.py`
- `ai_handoff/state.py`
- `ai_handoff/watcher.py` only consumes the bad state; it is not the root cause.

The key issue is in the interaction between:
- `ai_handoff/cycle.py:_update_handoff_state()`
- `ai_handoff/state.py:update_state()`

### Current behavior

`_update_handoff_state()` builds a partial `updates` dict and calls `update_state()`.

For example, these transitions only set a few keys:
- `SUBMIT_FOR_REVIEW` sets `turn=reviewer`, `status=ready`
- `REQUEST_CHANGES` sets `turn=lead`, `status=ready`
- `APPROVE` sets `status=done`, `result=approved`

Then `state.update(updates)` in `update_state()` performs a shallow overlay merge.

That means any old fields not explicitly overwritten survive indefinitely, including:
- `result`
- nested `roadmap` contents
- any old phase-completion metadata that no longer matches the active cycle

## Reproduction Pattern

One reliable way to reproduce the bug:

1. Finish a full-roadmap cycle so `handoff-state.json` contains something like:
   - `status: done`
   - `result: roadmap-complete`
   - a populated `roadmap.completed`
   - a nonzero `roadmap.current_index`
2. Start a new plan or implementation cycle.
3. Let the reviewer send `REQUEST_CHANGES`, or otherwise advance the cycle using the normal `cycle add` path.
4. Observe that the new cycle's per-cycle status file is correct, but the top-level `handoff-state.json` may still carry stale `result` and stale roadmap metadata from the previous completed roadmap.

## Why This Broke The Session

In the session where this surfaced:
- the cycle status for Phase 9 was correct,
- the reviewer action correctly handed the turn back to `lead`,
- but the top-level state still looked partly like the old completed Phase 8 roadmap.

That mismatch was enough to confuse the agent workflow, because the agents and watcher consult `handoff-state.json` first.

## Manual Recovery Used

We recovered by manually rewriting the top-level state to a coherent Phase 9 state and bumping `updated_at` so the watcher would re-notify.

The important part of the recovery was explicitly clearing stale completion state and replacing the roadmap block, for example:

```python
from ai_handoff.state import update_state

update_state({
    "phase": "phase-9-security-compliance-automation",
    "type": "plan",
    "turn": "lead",
    "status": "ready",
    "round": 1,
    "updated_by": "codex",
    "command": "Read .claude/skills/handoff/SKILL.md and handoff-state.json, then act on your turn",
    "run_mode": "full-roadmap",
    "result": None,
    "roadmap": {
        "queue": [
            "security-compliance-automation",
            "performance-token-optimization",
            "visual-dashboards-monitoring",
            "discord-integration",
        ],
        "current_index": 0,
        "completed": [],
        "pause_reason": None,
    },
}, ".")
```

After that normalization:
- `python -m ai_handoff state` matched the active cycle,
- progress was shown correctly for the new roadmap,
- and the watcher could pick up the fresh `updated_at` and notify the lead again.

## Recommended Fix

The fix should be in state normalization, not in the watcher.

### Preferred approach

When `cycle.py` updates `handoff-state.json`, it should write a normalized state for the active cycle instead of overlaying only the changed keys.

At minimum:
- `SUBMIT_FOR_REVIEW` should explicitly clear stale completion fields such as `result`
- `REQUEST_CHANGES` should explicitly clear stale completion fields such as `result`
- when entering a new roadmap context, the top-level `roadmap` block should be fully replaced, not partially inherited from the previous run
- transitions into a non-complete cycle should never leave `result=approved` or `result=roadmap-complete` behind

### Practical implementation options

Option A: normalize in `cycle.py`
- Expand `_update_handoff_state()` so it builds the full intended top-level state for each transition.
- For in-progress transitions, explicitly set `result=None`.
- Where roadmap context is known, explicitly write the correct `roadmap` object.

Option B: add clearing semantics in `state.py`
- Extend `update_state()` to support deletion or replacement semantics, such as a `clear_keys` parameter.
- This is more generic, but it pushes cycle-specific correctness into the low-level state layer.

Recommendation: use Option A first.
- The bug is caused by cycle transitions not fully describing the intended state.
- `state.py` is currently a generic atomic write helper and should stay simple if possible.

## Suggested Regression Tests

Add regression coverage for the stale overlay case.

Suggested tests:
- a test that starts from a synthetic `handoff-state.json` containing `result: roadmap-complete`, then runs a new `SUBMIT_FOR_REVIEW`, and verifies `result` is cleared
- a test that starts from a completed roadmap state, then records `REQUEST_CHANGES`, and verifies the top-level state becomes `lead` / `ready` without stale completion state
- a test that verifies a new roadmap's `roadmap.queue`, `roadmap.current_index`, and `roadmap.completed` replace the previous roadmap instead of inheriting it
- optionally, a watcher-oriented regression test verifying that the corrected state produces the expected next-turn notification without manual cleanup

## Code Pointers

Useful places to patch:
- `ai_handoff/cycle.py` around `_STATE_TRANSITIONS` and `_update_handoff_state()`
- `ai_handoff/state.py` around `update_state()` if you choose to add explicit clear/replace semantics

Useful behavior to preserve:
- bounded history handling in `state.py`
- `expected_seq` staleness checks
- watcher auto-advance behavior after approved plan/impl cycles

## Bottom Line

The bug is not that reviewer-to-lead handoff failed.
The bug is that the top-level state update was only a shallow overlay, so it preserved stale completion metadata from a previous roadmap and made the new active cycle look inconsistent.

The permanent fix is to make cycle-driven state updates write a normalized active-cycle state, especially clearing `result` and replacing stale roadmap metadata when a new cycle begins.
