# Skill: /sync

Synchronize state between Claude and Codex sessions. Ensures both AIs have the same context.

## When to Use
- Starting a new Claude or Codex session
- After a handoff
- When switching between Claude and Codex
- To verify both AIs understand current state

## Instructions

### Generate Sync Summary (`/sync`)

1. Read current project state:
   - Current phase from `docs/roadmap.md`
   - Phase details from `docs/phases/[current].md`
   - Pending handoffs from `docs/handoffs/`
   - Recent decisions from `docs/decision_log.md`
   - Open escalations from `docs/escalations/`

2. Generate sync document:

```markdown
# Project Sync: [Date/Time]

## Current State
- **Phase:** [name] - [status]
- **Lead:** [Claude/Codex]
- **Reviewer:** [Codex/Claude]

## Active Context
[Summary of what's being worked on]

## Pending Actions
For Claude:
- [Action 1]

For Codex:
- [Action 1]

For Human:
- [Action 1 if any]

## Recent Decisions
- [Decision 1]
- [Decision 2]

## Key Files
- Phase plan: `docs/phases/[phase].md`
- Latest handoff: `docs/handoffs/[file].md`
- Decision log: `docs/decision_log.md`

## Session Continuity
Last activity: [what was done]
Next expected: [what should happen next]
```

3. Save to `docs/sync_state.md` (overwrite)

### For Codex (`/sync codex`)

Generate a summary specifically for Codex to read when starting a session:

```markdown
# Codex Session Sync

## Your Role
You are the **Reviewer** for this project.
- Lead (Planning/Implementation): Claude
- Reviewer: You (Codex)
- Arbiter: Human

## What Needs Your Review
[Specific items awaiting Codex review]

## Context
[Summary of recent decisions and current state]

## How to Review
Use `/review plan [phase]` or `/review impl [phase]` to begin.
Save feedback to `docs/handoffs/[phase]_[type]_feedback.md`
```

### For Claude (`/sync claude`)

Generate a summary specifically for Claude to read when starting a session:

```markdown
# Claude Session Sync

## Your Role
You are the **Lead** for this project.
- Lead (Planning/Implementation): You (Claude)
- Reviewer: Codex
- Arbiter: Human

## Current Work
[What Claude should be working on]

## Pending Feedback
[Any feedback from Codex to address]

## Next Steps
[Recommended next actions]
```

## Examples

User: `/sync`

Response: Generates full sync summary and saves to `docs/sync_state.md`.

User: `/sync codex`

Response: Generates Codex-specific summary for copying to a Codex session.
