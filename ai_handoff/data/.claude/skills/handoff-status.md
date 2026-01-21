# Skill: /status

> **Before using this skill**:
> 1. Read `ai-handoff.yaml` in the project root to see your configured role
> 2. Identify whether you are the **lead** or **reviewer** agent
> 3. This skill works the same for both roles

Get a quick overview of project status, current work, and next steps.

## When to Use
- Starting a new session
- Checking what needs to be done
- Getting oriented in the project
- Before making decisions about what to work on

## Instructions

### Full Status (`/handoff-status`)

Gather and display comprehensive project status:

1. **Current Phase**: Read from `docs/roadmap.md` and `docs/phases/`
2. **Agent Config**: Read from `ai-handoff.yaml`
3. **Pending Reviews**: Check `docs/handoffs/` for items awaiting review
4. **Recent Decisions**: Last 3 entries from `docs/decision_log.md`
5. **Open Questions**: From current phase plan
6. **Next Steps**: Based on current state

Output format:
```markdown
## Project Status

### Current Phase
**[Phase Name]** - [Status]
- Progress: [X/Y] criteria complete
- Lead: [lead agent name from config]
- Reviewer: [reviewer agent name from config]
- Awaiting: [What's needed to proceed]

### Pending Actions
1. [Action 1 - who should do it]
2. [Action 2 - who should do it]

### Recent Handoffs
- [handoff_file.md] - [status: pending/reviewed]

### Open Questions
- [Question needing resolution]

### Quick Commands
- `/[suggested command]` - [what it does]
```

### Phase Status (`/handoff-status [phase_name]`)

Detailed status for a specific phase:

1. Read phase plan
2. Check success criteria completion
3. Check for pending handoffs
4. Check for feedback
5. Display detailed phase status

### Handoff Status (`/handoff-status handoffs`)

1. List all handoff documents
2. Show which are awaiting review
3. Show which have feedback
4. Show which are resolved

### Quick Status (`/handoff-status quick`)

One-line summary:
```
[Phase]: [Status] | Next: [action] | Blockers: [none/description]
```

## Examples

User: `/handoff-status`

Response:
```markdown
## Project Status

### Current Phase
**foundation** - Implementation
- Progress: 2/4 criteria complete
- Lead: [lead agent name]
- Reviewer: [reviewer agent name]
- Awaiting: Complete implementation tasks

### Pending Actions
1. Finish core model implementation (Lead)
2. Run tests (Lead)
3. Create implementation handoff (Lead)

### Recent Handoffs
- foundation_plan_handoff.md - reviewed (approved)

### Open Questions
- None currently

### Quick Commands
- `/handoff-implement status foundation` - see implementation progress
- `/handoff-implement complete foundation` - when done implementing
```

User: `/handoff-status handoffs`

Response:
```markdown
## Handoff Status

| File | Type | Status | Action |
|------|------|--------|--------|
| foundation_plan_handoff.md | Plan | Reviewed | Resolved |
| foundation_plan_feedback.md | Feedback | Complete | Read by lead |

No pending handoffs awaiting review.
```

User: `/handoff-status quick`

Response: `foundation: Implementation | Next: complete core model | Blockers: none`
