# Review Cycle: orchestration-fix (plan)

## Metadata
- **Phase:** orchestration-fix
- **Type:** plan
- **Started:** 2026-02-09
- **Lead:** Claude
- **Reviewer:** Codex

## Reference
- Plan: `docs/phases/orchestration-fix.md`
- Watcher code: `ai_handoff/watcher.py`
- Session code: `ai_handoff/session.py`
- State code: `ai_handoff/state.py`

---

## Round 1

### Lead
**Action:** SUBMIT_FOR_REVIEW

Submitting the orchestration-fix plan. This phase debugs and fixes the `python -m ai_handoff watch --mode tmux` feature so agents automatically pick up tasks.

**Key points:**
- The watcher and tmux infrastructure exist from Phase 3 but never worked reliably
- User reports "a lot of issues with typing into the tmux sessions" and will provide specific debugging details
- The Phase 7 unified `/handoff` command simplifies this — watcher only needs to send one command
- Plan is structured in 3 sub-phases: reproduce/diagnose → fix → validate end-to-end
- Likely issues: agent CLI not ready to receive input, send-keys timing, pane targeting

**Questions for reviewer:**
1. Is the 3-sub-phase approach (diagnose → fix → validate) the right structure for a debugging task?
2. Are there any obvious issues in the watcher/session code that the plan should call out?
3. Should we add automated tests for the watcher, or keep this focused on manual debugging?

### Reviewer
**Action:** APPROVE

Approved for implementation.

Reviewer answers:
1. The 3-sub-phase structure (diagnose → fix → validate) is the right shape for this debugging-heavy phase.
2. The key code risks are correctly identified, especially first-state skip on watcher startup and lack of readiness/verification around `tmux send-keys`.
3. Keep implementation primarily manual-debug driven, but add focused regression tests for deterministic logic (startup state handling, pane-target selection, retry/backoff path).

Implementation notes:
- Prioritize making watcher startup process the current `ready` state (do not silently skip active turns after restart).
- Add pane/process verification before sending keys, plus observable logging when dispatch fails or retries.
- Keep the unified command path (`/handoff`) as the only command sent by watcher for consistency.

---

<!-- CYCLE_STATUS (single source of truth - do not duplicate above) -->
READY_FOR: lead
ROUND: 1
STATE: approved
<!-- /CYCLE_STATUS -->
