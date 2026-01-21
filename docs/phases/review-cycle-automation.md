# Phase: Review Cycle Automation

## Status
- [x] Planning
- [x] In Review
- [x] Approved
- [x] Implementation
- [x] Implementation Review
- [x] Complete

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary
**What:** Add a `/handoff-cycle` skill that automates the back-and-forth review process between lead and reviewer agents using a single shared cycle document.

**Why:** Currently, review cycles require manual copy-paste of content between agents and creation of multiple handoff/feedback files. This friction increases with each review round. A single command per turn with automatic state tracking will significantly reduce human effort during multi-round reviews.

**Depends on:** configurable-agents-init (complete)

## Scope

### In Scope
- New skill: `/handoff-cycle` with start, continue, and status commands
- Cycle document format with machine-readable status block
- Support for both plan and implementation review cycles
- Automatic round tracking (up to 5 rounds)
- Auto-escalation when rounds exceed limit
- Abort command to cancel cycles mid-review
- Human input pause/resume capability
- Integration with existing skills (plan, implement, phase)

### Out of Scope
- External file watchers or notifications (requires tooling outside the framework)
- API-based direct agent communication
- Automated terminal switching
- Changes to existing handoff/review skills (they remain as alternatives)
- Automatic same-issue detection (hard to specify/test reliably; use round limit instead)

## Technical Approach

### Cycle Document Format

Location: `docs/handoffs/[phase]_[type]_cycle.md`

Structure:
```markdown
# Review Cycle: [phase] ([type])

## Metadata
- **Phase:** [phase name]
- **Type:** plan | impl
- **Started:** [date]
- **Lead:** [from config]
- **Reviewer:** [from config]

## Reference
- Plan: `docs/phases/[phase].md`
- Implementation: [files if impl review]

---

## Round [n]

### Lead
**Action:** SUBMIT_FOR_REVIEW

[Summary of plan/changes]

### Reviewer
**Action:** [APPROVE|REQUEST_CHANGES|NEED_HUMAN]

[Feedback or approval message]

---

<!-- CYCLE_STATUS (single source of truth - do not duplicate above) -->
READY_FOR: [lead|reviewer|human]
ROUND: [n]
STATE: [in-progress|needs-human|approved|escalated|aborted]
<!-- /CYCLE_STATUS -->
```

Note: The `<!-- CYCLE_STATUS -->` block is the authoritative machine-readable state. There is no separate human-readable "Status" section to avoid drift.

### Skill Commands

| Command | Description |
|---------|-------------|
| `/handoff-cycle start [phase] plan` | Lead starts a plan review cycle |
| `/handoff-cycle start [phase] impl` | Lead starts an implementation review cycle |
| `/handoff-cycle [phase]` | Continue the cycle (auto-detects role and type) |
| `/handoff-cycle status [phase]` | Show current cycle status without modifying |
| `/handoff-cycle abort [phase]` | Cancel the cycle (sets STATE: aborted, records reason) |

### State Machine

```
START (lead creates)
    ↓
LEAD_TURN (READY_FOR: reviewer)
    ↓
REVIEWER_TURN
    ├── APPROVE → APPROVED (end)
    ├── REQUEST_CHANGES → LEAD_TURN (round++)
    ├── NEED_HUMAN → NEEDS_HUMAN (pause)
    └── ABORT → ABORTED (end)

LEAD_TURN
    ├── SUBMIT → REVIEWER_TURN
    ├── NEED_HUMAN → NEEDS_HUMAN (pause)
    └── ABORT → ABORTED (end)

NEEDS_HUMAN
    └── Human responds → Previous turn resumes

Round 5 + REQUEST_CHANGES → ESCALATED (end, human decides)

ABORTED (terminal state)
    - Records who aborted (lead/reviewer/human)
    - Records reason for abort
    - Cycle file preserved for history
```

### Actions

**Lead Actions:**
- `SUBMIT_FOR_REVIEW`: Submits plan or updates for review
- `NEED_HUMAN`: Pauses cycle for human input on a question
- `ABORT`: Cancels the cycle with a reason

**Reviewer Actions:**
- `APPROVE`: Accepts the submission, ends cycle successfully
- `REQUEST_CHANGES`: Requests modifications, increments round
- `NEED_HUMAN`: Pauses cycle for human input on a question
- `ABORT`: Cancels the cycle with a reason

### Auto-Escalation Triggers

1. Round 5 reached without approval
2. Either agent explicitly requests escalation via `NEED_HUMAN` with escalation reason

### Behavior by Role

**When Lead runs `/handoff-cycle [phase]`:**
- If `READY_FOR: lead`: Display reviewer feedback, prompt for response, update file
- If `READY_FOR: reviewer`: Display "Waiting for reviewer. Switch to Codex terminal."
- If `READY_FOR: human`: Display "Waiting for human input."
- If `STATE: approved`: Display "Cycle complete. Run `/handoff-implement start [phase]`"
- If `STATE: escalated`: Display escalation summary
- If `STATE: aborted`: Display abort reason and who aborted

**When Reviewer runs `/handoff-cycle [phase]`:**
- If `READY_FOR: reviewer`: Display lead's submission, prompt to review, update file
- If `READY_FOR: lead`: Display "Waiting for lead. Switch to Claude terminal."
- If `READY_FOR: human`: Display "Waiting for human input."
- If `STATE: approved`: Display "Cycle complete."
- If `STATE: escalated`: Display escalation summary
- If `STATE: aborted`: Display abort reason and who aborted

### Human Input Flow

1. Agent sets `STATE: needs-human` and `READY_FOR: human`
2. Agent adds question in a `### Human Input Needed` section
3. Human reads cycle file, adds response in `### Human Response` section
4. Human sets `READY_FOR: [lead|reviewer]` and `STATE: in-progress`
5. Next agent continues from there

### Integration Points

| Existing Skill | Integration |
|----------------|-------------|
| `/handoff-plan create` | Run before `/handoff-cycle start [phase] plan` |
| `/handoff-plan update` | Lead updates plan file, then submits via cycle |
| `/handoff-implement start` | Run after plan cycle approved |
| `/handoff-implement complete` | Run before `/handoff-cycle start [phase] impl` |
| `/handoff-phase complete` | Run after impl cycle approved |

## Files to Create/Modify

### New Files
- `.claude/skills/handoff-cycle.md` - Skill definition
- `ai_handoff/data/.claude/skills/handoff-cycle.md` - Packaged skill copy
- `templates/cycle.md` - Cycle document template
- `ai_handoff/data/templates/cycle.md` - Packaged template copy

### Modified Files
- `README.md` - Add `/handoff-cycle` to skills table and workflow
- `docs/workflows.md` - Add cycle workflow documentation
- `ai_handoff/data/workflows.md` - Packaged workflows copy

## Success Criteria
- [ ] `/handoff-cycle start [phase] plan` creates properly formatted cycle document
- [ ] `/handoff-cycle [phase]` correctly identifies whose turn it is
- [ ] Lead can submit and reviewer can respond within the same file
- [ ] Round counter increments correctly
- [ ] Auto-escalation triggers at round 5
- [ ] `STATE: needs-human` properly pauses the cycle
- [ ] Human can resume cycle by editing file
- [ ] `STATE: approved` properly ends cycle with next-step guidance
- [ ] `/handoff-cycle abort [phase]` sets `STATE: aborted` with reason, preserves file
- [ ] Works for both plan and impl review types
- [ ] Existing skills (`/handoff-plan`, `/handoff-review`) still function as alternatives

## Open Questions (Resolved)
1. Should the cycle file include the full plan content or just reference it?
   - **Resolved:** Reference with brief summary per round (per reviewer feedback)

2. Should we add a `/handoff-cycle abort [phase]` command to cancel a cycle?
   - **Resolved:** Yes, added to spec. Sets `STATE: aborted`, records who and why (per reviewer feedback)

3. Maximum rounds before escalation - is 5 the right number?
   - **Resolved:** 5 is acceptable for MVP; can be made configurable later (per reviewer feedback)

4. Should we detect same-issue being raised repeatedly?
   - **Resolved:** Out of scope for MVP; rely on round limit instead (per reviewer feedback)

## Risks
- **Parsing errors in status block:** Mitigation - robust parsing with clear error messages and recovery suggestions
- **Agents modifying file when not their turn:** Mitigation - clear "not your turn" message, status check before any modification
- **File conflicts if both agents access simultaneously:** Mitigation - unlikely in practice since human switches terminals; document recommends waiting for clear handoff message
