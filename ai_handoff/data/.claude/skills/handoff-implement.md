# Skill: /implement

> **Before using this skill**:
> 1. Read `ai-handoff.yaml` in the project root to see your configured role
> 2. Identify whether you are the **lead** or **reviewer** agent
> 3. Follow the instructions for your role below

Begin implementation of an approved phase plan. The lead agent is responsible for implementation.

## When to Use
- After a phase plan has been approved by the reviewer
- When ready to start writing code
- To resume implementation of a phase in progress

## Prerequisites
- Phase plan exists in `docs/phases/[phase_name].md`
- Phase status is "Approved" (planning review complete)

## Instructions

### If You Are the Lead

#### Start Implementation (`/handoff-implement start [phase_name]`)

1. Verify prerequisites:
   - Check phase plan exists
   - Check phase status is "Approved" or "Planning Complete"
   - If not approved, prompt: "Phase not yet approved. Run `/handoff-review plan [phase_name]` first."

2. Read the phase plan thoroughly
3. Update phase status to "Implementation"
4. Create implementation tracking in `docs/phases/[phase_name]_impl.md`:

```markdown
# Implementation Log: [Phase Name]

**Started:** [YYYY-MM-DD]
**Lead:** [Your agent name from config]
**Plan:** docs/phases/[phase_name].md

## Progress

### Session 1 - [Date]
- [ ] [Task from plan]
- [ ] [Task from plan]

## Files Created
- (none yet)

## Files Modified
- (none yet)

## Decisions Made
- (log any implementation decisions here)

## Issues Encountered
- (log any problems and solutions)
```

5. Begin implementation following the plan

#### Resume Implementation (`/handoff-implement resume [phase_name]`)

1. Read the implementation log
2. Read the phase plan
3. Check which tasks are complete
4. Continue from where left off

#### Status Check (`/handoff-implement status [phase_name]`)

1. Read implementation log
2. Compare against phase plan success criteria
3. Report:
   - Tasks completed
   - Tasks remaining
   - Any blockers
   - Estimated remaining work items

#### Complete Implementation (`/handoff-implement complete [phase_name]`)

1. Verify all success criteria are met
2. Update implementation log with final status
3. Update phase status to "Implementation Review"
4. Create implementation handoff: `/handoff-handoff impl [phase_name]`
5. Prompt: "Implementation complete. Handoff created for reviewer."

### If You Are the Reviewer

You do not implement directly. Your role is to review implementations created by the lead.

To check implementation status: `/handoff-implement status [phase_name]`
To review completed implementation: `/handoff-review impl [phase_name]`

## Implementation Guidelines

### Code Standards
- Follow existing project conventions
- Keep implementations simple and focused
- Avoid over-engineering
- Add comments only where logic isn't self-evident
- Handle errors appropriately

### Testing
- Write tests for key functionality
- Verify the app runs without errors
- Test the success criteria manually if needed

### Documentation
- Update implementation log as you work
- Note any deviations from the plan
- Document decisions made during implementation

## Examples

User: `/handoff-implement start foundation`

Response: Checks prerequisites, reads plan, creates implementation log.
"Starting implementation of 'foundation' phase.
Plan includes:
- Set up project structure
- Create database
- Implement core model

Beginning with project structure..."
[Proceeds to implement]

User: `/handoff-implement status foundation`

Response:
```
Foundation Phase Implementation:
- [x] Project structure
- [x] Database setup
- [ ] Core model
- [ ] Basic routes

2/4 tasks complete. No blockers.
```

User: `/handoff-implement complete foundation`

Response: Verifies criteria, updates log, creates handoff.
"Implementation complete. All success criteria met.
Handoff created for reviewer at `docs/handoffs/foundation_impl_handoff.md`"
