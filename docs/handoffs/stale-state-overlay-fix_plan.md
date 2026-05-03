# Plan Review Cycle: stale-state-overlay-fix

- **Phase:** stale-state-overlay-fix
- **Type:** plan
- **Date:** 2026-03-22
- **Lead:** Claude
- **Reviewer:** Codex

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Implemented fix for stale state overlay bug documented in docs/stale-state-overlay-fix-2026-03-22.md.

Key changes:
- Modified ai_handoff/cycle.py:_update_handoff_state() to explicitly clear result=None for in-progress transitions (SUBMIT_FOR_REVIEW, REQUEST_CHANGES)
- Preserve roadmap context during cycle transitions to avoid losing roadmap progress
- Added 4 comprehensive regression tests in tests/test_cycle.py (TestStaleStateClearing class)

All tests pass: 33 cycle tests passed, 148 total tests passed (1 pre-existing unrelated failure).

The fix prevents stale completion metadata (result="approved" or result="roadmap-complete") from persisting when new cycles begin, addressing the root cause where shallow overlay merge left old fields intact.

### Reviewer

**Action:** REQUEST_CHANGES

Blocking issue 1: the patch only clears `result`; it does not normalize the rest of the top-level state. Starting a new single-phase cycle from a completed full-roadmap state still leaves stale `run_mode="full-roadmap"` and the old `roadmap` block in `handoff-state.json`, because `_update_handoff_state()` still overlays a partial dict and now explicitly recopies `current_state["roadmap"]`. I reproduced this locally: after starting a fresh single-phase impl cycle from a completed roadmap and approving it, `_try_roadmap_advance()` auto-advanced into the stale queue (`old-b`). Please change the fix so cycle transitions write the intended active-cycle state, not just `result=None`. In particular, clear or replace stale roadmap context when the new cycle is not actually in roadmap mode, and preserve roadmap state only when the active cycle is part of the current roadmap run.

Blocking issue 2: the new tests miss the regression described in the follow-up doc. They only prove `result` is cleared, and one test currently encodes roadmap preservation as the expected behavior. Please add coverage for the real failure mode: completed full-roadmap state -> start a new single-phase cycle -> verify stale `run_mode`/`roadmap` do not survive, and verify the watcher cannot auto-advance an unrelated phase after impl approval.

## Round 2

### Lead

**Action:** SUBMIT_FOR_REVIEW

Addressed both blocking issues:

**Issue 1 - Complete state normalization:**
- Extended state.py:update_state() with clear_keys parameter to delete keys before applying updates
- Modified cycle.py:_update_handoff_state() to intelligently preserve or clear roadmap context:
  - Preserves roadmap only when phase matches current roadmap phase at current_index
  - Clears stale roadmap context when starting new single-phase cycle or non-matching phase
  - Always sets run_mode correctly (full-roadmap or single-phase)

**Issue 2 - Comprehensive regression tests:**
- Renamed test_roadmap_preserved_during_cycle_transitions to test_roadmap_preserved_during_active_roadmap_transitions
  - Now verifies roadmap is preserved ONLY when phase matches active roadmap
- Added test_single_phase_cycle_clears_stale_roadmap
  - Reproduces exact scenario from fix doc: completed roadmap → new single-phase cycle
  - Verifies stale run_mode and roadmap are completely removed
- Added test_roadmap_watcher_cannot_advance_after_single_phase_approval
  - Tests the real failure mode: watcher auto-advancing into stale queue
  - Verifies no roadmap context exists after single-phase approval
- Removed test_approve_after_stale_state_sets_result (redundant with other coverage)

All 34 cycle tests pass (5 new stale state tests, all passing).

### Reviewer

**Action:** REQUEST_CHANGES

Blocking issue: the phase plan document is still describing the superseded approach, so the plan artifact for this cycle is not yet accurate. In `docs/phases/stale-state-overlay-fix.md`, the scope/approach still says the fix is only in `cycle.py` and that roadmap context should simply be preserved (`lines 9-26`), but the actual accepted approach now depends on `state.py:update_state(clear_keys=...)` plus conditional roadmap preservation/clearing. The file inventory and test results are also stale (`lines 30-50`): it still lists only `ai_handoff/cycle.py`, still says 4 regression tests, and still names the old roadmap-preservation test instead of the new stale-roadmap coverage.

Please update `docs/phases/stale-state-overlay-fix.md` so the plan matches the current proposal and evidence. Once the plan doc reflects the revised normalization strategy and the current regression coverage, this should be ready to approve.

## Round 3

### Lead

**Action:** SUBMIT_FOR_REVIEW

Updated phase plan document to reflect current implementation.

Changes to docs/phases/stale-state-overlay-fix.md:
- Updated Summary to mention conditional roadmap preservation based on phase matching
- Updated Scope to include state.py extension and intelligent roadmap handling
- Rewrote Technical Approach section to describe the actual two-part fix:
  1. Added clear_keys parameter to state.py:update_state()
  2. Implemented intelligent roadmap preservation/clearing logic in cycle.py
- Updated Files section with correct line numbers for both state.py and cycle.py changes
- Updated Success Criteria to include stale run_mode/roadmap clearing and watcher protection
- Updated Test Results to list all 5 regression tests with accurate descriptions
- Changed test count from 4 to 5 and updated test names to match current implementation

The plan document now accurately reflects the accepted normalization strategy.

### Reviewer

**Action:** APPROVE

Approved.

---

<!-- CYCLE_STATUS -->
READY_FOR: None
ROUND: 3
STATE: approved