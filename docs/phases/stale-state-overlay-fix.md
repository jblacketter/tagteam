# Phase: Stale State Overlay Fix

## Summary

Fix a handoff-state bug where stale top-level fields from previous cycles persist due to shallow overlay merging. This causes the active cycle to appear inconsistent even when per-cycle status is correct. The fix normalizes state during cycle transitions by conditionally preserving or clearing roadmap context based on phase matching.

## Scope

- Extend `ai_handoff/state.py:update_state()` with `clear_keys` parameter for explicit key deletion
- Fix `ai_handoff/cycle.py:_update_handoff_state()` to normalize state transitions
- Explicitly clear stale completion state (`result` field) for in-progress transitions
- Intelligently preserve roadmap context only when phase matches active roadmap
- Clear stale roadmap context when starting new single-phase cycles
- Add comprehensive regression tests covering real failure modes

## Technical Approach

The bug occurs because `_update_handoff_state()` builds a partial updates dict that gets shallow-merged via `state.update(updates)`. This leaves stale fields like `result="approved"`, `result="roadmap-complete"`, `run_mode="full-roadmap"`, and the entire `roadmap` block from previous cycles.

**Fix implementation:**

1. **Extended `state.py:update_state()`** with optional `clear_keys` parameter
   - Allows explicit deletion of keys before applying updates
   - Properly handles roadmap removal without bypassing history/seq management

2. **Modified `cycle.py:_update_handoff_state()`** to normalize state:
   - Explicitly sets `result=None` for in-progress transitions (`SUBMIT_FOR_REVIEW`, `REQUEST_CHANGES`)
   - Intelligently preserves roadmap **only** when:
     - Current state has `run_mode="full-roadmap"` AND
     - Current state has a `roadmap` with valid queue AND
     - The cycle's phase matches the current roadmap phase at `current_index`
   - Clears stale roadmap context when:
     - Starting a new single-phase cycle OR
     - Phase doesn't match the active roadmap phase
   - Always normalizes `run_mode` to either `"full-roadmap"` or `"single-phase"`

3. **Comprehensive regression tests** covering actual failure scenarios from the bug report

## Files

- `ai_handoff/state.py` - Added `clear_keys` parameter to `update_state()` (lines 47-87)
- `ai_handoff/cycle.py` - Modified `_update_handoff_state()` function (lines 164-229)
- `tests/test_cycle.py` - Added `TestStaleStateClearing` class with 5 regression tests (lines 267-423)

## Success Criteria

- [x] All new regression tests pass
- [x] All existing tests continue to pass (34 passed, 1 skipped in test_cycle.py)
- [x] `SUBMIT_FOR_REVIEW` and `REQUEST_CHANGES` explicitly clear stale `result` field
- [x] Stale `run_mode` and `roadmap` are cleared when starting new single-phase cycles
- [x] Roadmap context is preserved only when phase matches active roadmap
- [x] Watcher cannot auto-advance into stale roadmap queues
- [x] Round 5 auto-escalation still works correctly
- [x] No regressions in existing handoff behavior

## Test Results

All 5 new regression tests pass:
- `test_submit_for_review_clears_stale_result` - Verifies stale result is cleared when starting new cycle
- `test_request_changes_clears_stale_result` - Verifies stale result is cleared on reviewer feedback
- `test_roadmap_preserved_during_active_roadmap_transitions` - Verifies roadmap IS preserved when phase matches
- `test_single_phase_cycle_clears_stale_roadmap` - Reproduces exact bug: completed roadmap → new single-phase cycle clears stale context
- `test_roadmap_watcher_cannot_advance_after_single_phase_approval` - Prevents watcher auto-advancing into stale queue

Full test suite: 34 cycle tests passed, 1 skipped
