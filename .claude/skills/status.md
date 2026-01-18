# Skill: /status

Get a quick overview of project status, current work, and next steps.

## When to Use
- Starting a new session
- Checking what needs to be done
- Getting oriented in the project
- Before making decisions about what to work on

## Instructions

### Full Status (`/status`)

Gather and display comprehensive project status:

1. **Current Phase**: Read from `docs/roadmap.md` and `docs/phases/`
2. **Pending Reviews**: Check `docs/handoffs/` for items awaiting review
3. **Recent Decisions**: Last 3 entries from `docs/decision_log.md`
4. **Open Questions**: From current phase plan
5. **Next Steps**: Based on current state

Output format:
```markdown
## Project Status

### Current Phase
**[Phase Name]** - [Status]
- Progress: [X/Y] criteria complete
- Lead: [Claude/Codex]
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

### Phase Status (`/status [phase_name]`)

Detailed status for a specific phase:

1. Read phase plan
2. Check success criteria completion
3. Check for pending handoffs
4. Check for feedback
5. Display detailed phase status

### Handoff Status (`/status handoffs`)

1. List all handoff documents
2. Show which are awaiting review
3. Show which have feedback
4. Show which are resolved

### Quick Status (`/status quick`)

One-line summary:
```
[Phase]: [Status] | Next: [action] | Blockers: [none/description]
```

## Examples

User: `/status`

Response:
```markdown
## Project Status

### Current Phase
**foundation** - Implementation
- Progress: 2/4 criteria complete
- Lead: Claude
- Awaiting: Complete implementation tasks

### Pending Actions
1. Finish core model implementation (Claude)
2. Run tests (Claude)
3. Create implementation handoff (Claude)

### Recent Handoffs
- foundation_plan_handoff.md - reviewed (approved)

### Open Questions
- None currently

### Quick Commands
- `/implement status foundation` - see implementation progress
- `/implement complete foundation` - when done implementing
```

User: `/status handoffs`

Response:
```markdown
## Handoff Status

| File | Type | Status | Action |
|------|------|--------|--------|
| foundation_plan_handoff.md | Plan | Reviewed | Resolved |
| foundation_plan_feedback.md | Feedback | Complete | Read by Claude |

No pending handoffs awaiting review.
```

User: `/status quick`

Response: `foundation: Implementation | Next: complete core model | Blockers: none`
