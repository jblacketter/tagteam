# Phase: progress-based-escalation

## Summary
Replace the hard round-5 auto-escalation with progress-based stale-round detection. Cycles that are making progress (lead submitting different content each round) should be allowed to continue. Only escalate when the lead re-submits identical content 5+ times (genuinely stuck).

## Scope
- `ai_handoff/cycle.py`: Core escalation logic
- `tests/test_cycle.py`: Updated and new tests
- `.claude/skills/handoff/SKILL.md` + `ai_handoff/data/.claude/skills/handoff/SKILL.md`: Doc updates
- `docs/workflows.md` + `ai_handoff/data/workflows.md`: Diagram updates

## Technical Approach
1. Add `_count_stale_rounds()` helper that reads the JSONL round log, extracts lead SUBMIT_FOR_REVIEW entries, and counts consecutive identical submissions from the end
2. Replace `if action == "REQUEST_CHANGES" and round_num >= 5` with stale-round check
3. Pass `auto_escalate` flag from `add_round()` to `_update_handoff_state()` instead of duplicating detection logic
4. Add `STALE_ROUND_LIMIT = 5` constant for configurability

## Files Changed
- `ai_handoff/cycle.py` — new `_count_stale_rounds()`, updated `add_round()` and `_update_handoff_state()`
- `tests/test_cycle.py` — replaced `test_round5_request_changes_auto_escalates` with two tests: `test_round5_with_progress_does_not_escalate` and `test_stale_rounds_auto_escalate`
- `.claude/skills/handoff/SKILL.md` — updated escalation description
- `ai_handoff/data/.claude/skills/handoff/SKILL.md` — same
- `docs/workflows.md` — updated cycle diagram
- `ai_handoff/data/workflows.md` — same

## Success Criteria
- [ ] Cycles with changing content do NOT auto-escalate at round 5
- [ ] Cycles with 5+ identical lead submissions DO auto-escalate
- [ ] All existing tests pass (35 passed, 1 skipped)
- [ ] Manual ESCALATE and NEED_HUMAN actions still work as before
