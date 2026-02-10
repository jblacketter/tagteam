# Review Cycle: orchestration-fix (impl)

## Metadata
- **Phase:** orchestration-fix
- **Type:** impl
- **Started:** 2026-02-09
- **Lead:** Claude
- **Reviewer:** Codex

## Reference
- Plan: `docs/phases/orchestration-fix.md`
- Changed file: `ai_handoff/watcher.py`

---

## Round 1

### Lead
**Action:** SUBMIT_FOR_REVIEW

Implementation of watcher fixes in `ai_handoff/watcher.py`. Changes address all items from Codex's plan review feedback.

**Changes made:**

1. **Fixed first-state skip on startup** (lines 197-208)
   - Old: Watcher skipped any state with `updated_dt <= watcher_start`, silently dropping active turns after restart
   - New: On first poll, if state is `ready`, falls through to process it immediately. Logs "Picking up active turn from existing state". Non-actionable states are recorded and waited on normally.

2. **Added pane verification** (lines 30-39, 73-75, 170-176)
   - New `pane_exists()` function uses `tmux display-message` to check pane ID
   - `send_tmux_keys()` checks pane exists before attempting send
   - Startup logs pane status for each configured pane (OK or WARNING)

3. **Added retry with backoff** (lines 57-118)
   - `send_tmux_keys()` now retries `max_retries` times (default 3) with `retry_delay` (default 2s)
   - Each failure is logged with attempt number and error details
   - CLI flags: `--retries N`, `--retry-delay N`

4. **Added pre-send delay** (lines 79-80)
   - Configurable `pre_send_delay` (default 1s) before sending, gives agent CLI time to settle
   - CLI flag: `--send-delay N`

5. **Improved send-keys reliability** (lines 82-100)
   - Sends `C-u` before command to clear any partial input
   - Uses `-l` flag for literal text (prevents tmux key name interpretation)
   - Sends Enter as separate key (not literal)

6. **Added `capture_pane()` utility** (lines 42-54)
   - Captures last N lines of pane content for debugging
   - Available for future readiness detection

7. **Better failure logging** (lines 105-112, 253-259)
   - Failed sends show attempt count and specific error
   - Final failure after all retries shows total attempts
   - macOS notification on persistent failure

**Testing:**
- Module imports cleanly
- 42/45 tests pass (3 pre-existing failures, unchanged)
- No session.py or state.py changes needed yet

**Note:** Full end-to-end validation requires manual testing with real tmux sessions and agent CLIs. The user will provide debugging details when we test. This implementation addresses all the code-level issues; runtime behavior will be validated in the next step.

### Reviewer
**Action:** APPROVE

Approved.

Review outcome:
- No blocking regressions found in `ai_handoff/watcher.py`.
- Startup first-state handling now correctly picks up active `ready` turns after watcher restart.
- Pane validation + retry/backoff + configurable send delay are implemented and wired through CLI flags.
- Send reliability improvements (`C-u`, literal send, separate Enter) are reasonable for tmux-driven CLIs.

Validation notes:
- Static checks passed (`python -m compileall -q ai_handoff/watcher.py`).
- Full `pytest` runs in this environment required disabling plugin autoload; with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` there is 1 unrelated pre-existing failure in `tests/test_migrate.py`.
- End-to-end tmux behavior still requires live manual validation with real agent sessions (expected for this phase).

Non-blocking follow-up:
- Add focused unit tests for watcher startup-state behavior and retry-path logic in a future pass.

---

<!-- CYCLE_STATUS (single source of truth - do not duplicate above) -->
READY_FOR: lead
ROUND: 1
STATE: approved
<!-- /CYCLE_STATUS -->
