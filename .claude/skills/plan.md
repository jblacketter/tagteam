# Skill: /plan

Create or update phase plans for the project. Claude is always the lead for planning.

## When to Use
- Starting a new phase of development
- Breaking down the project into implementable phases
- Updating an existing phase plan based on feedback

## Workflow Context

This project uses a Claude (Lead) / Codex (Reviewer) workflow:
1. **Claude plans** the phase and creates a handoff document
2. **Codex reviews** and provides feedback
3. **Claude revises** if needed (Claude has final decision)
4. **Implementation** begins once plan is approved
5. Repeat for each phase

## Instructions

### Create Mode (`/plan create [phase_name]`)

1. Gather context:
   - Read project requirements/brief
   - Read `docs/roadmap.md` for overall project structure
   - Check `docs/phases/` for completed phases
   - Check `docs/decision_log.md` for relevant decisions

2. Create the phase plan in `docs/phases/[phase_name].md`:

```markdown
# Phase: [Phase Name]

## Status
- [ ] Planning
- [ ] In Review (Codex)
- [ ] Approved
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary
**What:** [What this phase accomplishes]
**Why:** [Why this phase is needed now]
**Depends on:** [Previous phase or "None"]

## Scope

### In Scope
- [Specific deliverable 1]
- [Specific deliverable 2]

### Out of Scope
- [What is explicitly NOT included]

## Technical Approach
[Describe the implementation approach, architecture decisions, patterns to use]

## Files to Create/Modify
- `path/to/file` - [purpose]

## Success Criteria
- [ ] [Testable criterion 1]
- [ ] [Testable criterion 2]

## Open Questions
- [Question for Codex or Human to address]

## Risks
- [Risk 1]: [Mitigation]
```

3. After creating the plan:
   - Prompt: "Phase plan created. Ready to create handoff for Codex review? Use `/handoff plan [phase_name]`"

### Update Mode (`/plan update [phase_name]`)

1. Read the existing phase plan
2. Read any feedback from `docs/handoffs/[phase_name]_plan_feedback.md`
3. Ask what needs to change or apply feedback
4. Update the plan
5. Note what changed in a "Revision History" section

### List Mode (`/plan list`)

1. List all phases in `docs/phases/`
2. Show status of each phase
3. Indicate current active phase

### Show Mode (`/plan [phase_name]`)

1. Display the phase plan with current status
2. Show completion percentage of success criteria

## Examples

User: `/plan create foundation`

Response: "Creating phase plan for 'foundation'. Let me gather project context first..."
[Creates docs/phases/foundation.md with filled template]
"Phase plan created. Ready to create handoff for Codex review? Use `/handoff plan foundation`"

User: `/plan list`

Response:
```
Phases:
- foundation: Planning (current)
- core-features: Not started
- ui-polish: Not started
```

User: `/plan update foundation`

Response: "Reading Codex feedback... [Lists feedback items]. Which items should I incorporate?"
