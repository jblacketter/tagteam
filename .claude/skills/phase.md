# Skill: /phase

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

### Current Phase (`/phase` or `/phase current`)

1. Read `docs/roadmap.md` for phase list
2. Check status of each phase in `docs/phases/`
3. Identify and display current active phase
4. Show phase status and next steps

Output:
```markdown
## Current Phase: [Phase Name]

**Status:** [Planning/In Review/Approved/Implementation/Impl Review]
**Lead:** Claude
**Reviewer:** Codex

### Next Action
[What should happen next based on status]

### Progress
[X/Y] success criteria complete
```

### List Phases (`/phase list`)

1. Read `docs/roadmap.md`
2. For each phase, check status in `docs/phases/[phase].md`
3. Display:

```markdown
## Project Phases

| Phase | Status | Lead | Reviewer |
|-------|--------|------|----------|
| foundation | Complete | Claude | Codex |
| core-features | Implementation | Claude | Codex |
| ui-polish | Not Started | - | - |
```

### Advance Phase (`/phase advance [phase_name]`)

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
   - Prompt to use `/implement start [phase]`

4. **Implementation -> Impl Review**
   - Verify implementation complete
   - Create impl handoff if not exists
   - Update status

5. **Impl Review -> Complete**
   - Verify review passed
   - Update status
   - Archive phase documents
   - Prompt for next phase

### Complete Phase (`/phase complete [phase_name]`)

1. Verify all success criteria met
2. Verify review passed
3. Update phase status to "Complete"
4. Update `docs/roadmap.md`
5. Prompt: "Phase complete. Ready to plan next phase?"

### Create Roadmap (`/phase roadmap`)

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

User: `/phase`

Response:
```
Current Phase: foundation
Status: Implementation
Lead: Claude

Next Action: Complete remaining implementation tasks.
Use `/implement status foundation` for details.

Progress: 2/4 success criteria complete
```

User: `/phase list`

Response: Shows table of all phases with status.

User: `/phase advance foundation`

Response: "Foundation phase advanced from 'Implementation' to 'Impl Review'.
Handoff created for Codex at `docs/handoffs/foundation_impl_handoff.md`"
