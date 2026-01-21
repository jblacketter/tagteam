# Skill: /sync

> **Before using this skill**:
> 1. Read `ai-handoff.yaml` in the project root to see your configured role
> 2. Identify whether you are the **lead** or **reviewer** agent
> 3. This skill works the same for both roles

Synchronize state between lead and reviewer sessions. Ensures both AI agents have the same context.

## When to Use
- Starting a new session with either agent
- After a handoff
- When switching between lead and reviewer
- To verify both agents understand current state

## Instructions

### Generate Sync Summary (`/handoff-sync`)

1. Read current project state:
   - Current phase from `docs/roadmap.md`
   - Phase details from `docs/phases/[current].md`
   - Pending handoffs from `docs/handoffs/`
   - Recent decisions from `docs/decision_log.md`
   - Open escalations from `docs/escalations/`

2. Read `ai-handoff.yaml` to get agent names

3. Generate sync document:

```markdown
# Project Sync: [Date/Time]

## Current State
- **Phase:** [name] - [status]
- **Lead:** [Lead agent name from config]
- **Reviewer:** [Reviewer agent name from config]

## Active Context
[Summary of what's being worked on]

## Pending Actions
For Lead ([lead name]):
- [Action 1]

For Reviewer ([reviewer name]):
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

4. Save to `docs/sync_state.md` (overwrite)

### For Reviewer (`/handoff-sync reviewer`)

Generate a summary specifically for the reviewer agent to read when starting a session:

```markdown
# Reviewer Session Sync

## Your Role
You are the **Reviewer** for this project.
- Lead (Planning/Implementation): [Lead agent name]
- Reviewer: You ([Reviewer agent name])
- Arbiter: Human

## What Needs Your Review
[Specific items awaiting reviewer attention]

## Context
[Summary of recent decisions and current state]

## How to Review
Use `/handoff-review plan [phase]` or `/handoff-review impl [phase]` to begin.
Save feedback to `docs/handoffs/[phase]_[type]_feedback.md`
```

### For Lead (`/handoff-sync lead`)

Generate a summary specifically for the lead agent to read when starting a session:

```markdown
# Lead Session Sync

## Your Role
You are the **Lead** for this project.
- Lead (Planning/Implementation): You ([Lead agent name])
- Reviewer: [Reviewer agent name]
- Arbiter: Human

## Current Work
[What the lead should be working on]

## Pending Feedback
[Any feedback from reviewer to address]

## Next Steps
[Recommended next actions]
```

## Examples

User: `/handoff-sync`

Response: Generates full sync summary and saves to `docs/sync_state.md`.

User: `/handoff-sync reviewer`

Response: Generates reviewer-specific summary for copying to a reviewer session.
