# Skill: /phase

> **Before using this skill**:
> 1. Read `ai-handoff.yaml` in the project root to see your configured role
> 2. Identify whether you are the **lead** or **reviewer** agent
> 3. Follow the instructions for your role below

Manage phase lifecycle and track overall project progress.

## When to Use
- To check current project phase
- To advance to the next phase
- To mark a phase as complete
- To see the project roadmap

## Phase Lifecycle

```
Planning -> In Review -> Approved -> Implementation -> Impl Review -> Complete
   ^                        |              |                            |
   |                        v              v                            v
   +---- (if rejected) -----+   (continue) +-------- (if rejected) ----+
```

## Instructions

### Current Phase (`/handoff-phase` or `/handoff-phase current`)

1. Read `docs/roadmap.md` for phase list
2. Check status of each phase in `docs/phases/`
3. Read `ai-handoff.yaml` to get agent names
4. Identify and display current active phase
5. Show phase status and next steps

Output:
```markdown
## Current Phase: [Phase Name]

**Status:** [Planning/In Review/Approved/Implementation/Impl Review]
**Lead:** [Lead agent name from config]
**Reviewer:** [Reviewer agent name from config]

### Next Action
[What should happen next based on status]

### Progress
[X/Y] success criteria complete
```

### List Phases (`/handoff-phase list`)

1. Read `docs/roadmap.md`
2. For each phase, check status in `docs/phases/[phase].md`
3. Display:

```markdown
## Project Phases

| Phase | Status | Lead | Reviewer |
|-------|--------|------|----------|
| foundation | Complete | [lead name] | [reviewer name] |
| core-features | Implementation | [lead name] | [reviewer name] |
| ui-polish | Not Started | - | - |
```

### Advance Phase (`/handoff-phase advance [phase_name]`)

Based on current status, advance to next state:

1. **Planning -> In Review**
   - Verify plan exists
   - Create handoff if not exists
   - Update status

2. **In Review -> Approved**
   - Verify feedback received
   - Verify no blocking issues
   - Update status

3. **Approved -> Implementation**
   - Prompt to use `/handoff-implement start [phase]`

4. **Implementation -> Impl Review**
   - Verify implementation complete
   - Create impl handoff if not exists
   - Update status

5. **Impl Review -> Complete**
   - Verify review passed
   - Update status
   - Archive phase documents
   - Prompt for next phase

### Complete Phase (`/handoff-phase complete [phase_name]`)

1. Verify all success criteria met
2. Verify review passed
3. Update phase status to "Complete"
4. Update `docs/roadmap.md`
5. Prompt: "Phase complete. Ready to plan next phase?"

### Create Roadmap (`/handoff-phase roadmap`)

1. If `docs/roadmap.md` doesn't exist, create it:

```markdown
# Project Roadmap

## Overview
[Project description]

## Phases

### Phase 1: [Name]
- Status: [Not Started/In Progress/Complete]
- Description: [Brief description]

### Phase 2: [Name]
- Status: Not Started
- Description: [Brief description]

## Decision Log
See `docs/decision_log.md`
```

## Examples

User: `/handoff-phase`

Response:
```
Current Phase: foundation
Status: Implementation
Lead: [lead agent name]

Next Action: Complete remaining implementation tasks.
Use `/handoff-implement status foundation` for details.

Progress: 2/4 success criteria complete
```

User: `/handoff-phase list`

Response: Shows table of all phases with status.

User: `/handoff-phase advance foundation`

Response: "Foundation phase advanced from 'Implementation' to 'Impl Review'.
Handoff created for reviewer at `docs/handoffs/foundation_impl_handoff.md`"
