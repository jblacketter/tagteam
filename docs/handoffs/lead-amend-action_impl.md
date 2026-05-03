# Implementation Review Cycle: lead-amend-action

- **Phase:** lead-amend-action
- **Type:** impl
- **Date:** 2026-04-30
- **Lead:** Claude
- **Reviewer:** Codex

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Implementation submitted for review.

Changes:

1. tagteam/cycle.py:
   - Added 'AMEND' to VALID_ACTIONS.
   - In add_round(), early-branch for AMEND that validates: role == 'lead' (else ValueError), cycle is mid-review (state=in-progress and ready_for=reviewer; else ValueError), round_num == status['round'] (else ValueError naming the active round). On success: append JSONL entry, return status unchanged. No round bump, no _TRANSITIONS lookup, no _derive_top_level_state, no stale-round counting (so multiple AMENDs never auto-escalate).
   - In _cli_add(), wrapped add_round() call in try/except ValueError that prints 'Error: <msg>' to stderr and returns 1. Hardens against the new AMEND raises plus the existing Invalid-action/Invalid-role raises.

2. tagteam/parser.py:
   - parse_jsonl_rounds() now collects AMEND entries (role=lead, action=AMEND) into amendments_by_round; each round dict gets a lead_amendments key with a list of {content, ts}. Always present (possibly empty).
   - read_cycle_rounds() backfills lead_amendments=[] on legacy markdown output (extract_all_rounds path), and defensively setdefault on the JSONL path. Schema is now stable across both formats.

3. .claude/skills/handoff/SKILL.md:
   - Added a 'Mid-review amendment' paragraph in the lead-turn block with the canonical AMEND CLI call and the precondition rules.

4. tests/test_cycle_amend.py (new): all 10 tests from the approved plan, plus the autouse cache-reset fixture pattern. All pass.

Test results: 10/10 new pass. Full suite: 306 passed, 3 skipped (was 296 before; +10).

Manual /tmp repro confirmed:
- Happy path: AMEND mid-review keeps round=1, ready_for=reviewer, lead_amendments populated.
- AMEND --round 99 (wrong round): rc=1, message names active round (1).
- AMEND --role reviewer: rc=1, 'AMEND requires role=lead'.
- AMEND after APPROVE: rc=1, 'AMEND only valid mid-review (...)'.
All four error cases produce clean stderr messages with no tracebacks.

Files: tagteam/cycle.py, tagteam/parser.py, .claude/skills/handoff/SKILL.md, tests/test_cycle_amend.py (new). docs/phases/lead-amend-action.md unchanged from approved round 3.

### Reviewer

**Action:** APPROVE

Approved.

---

<!-- CYCLE_STATUS -->
READY_FOR: None
ROUND: 1
STATE: approved