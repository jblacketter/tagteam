# Skill: /plan

> **Before using this skill**:
> 1. Read `ai-handoff.yaml` in the project root to see your configured role
> 2. Identify whether you are the **lead** or **reviewer** agent
> 3. Follow the instructions for your role below

Create or update phase plans for the project. The lead agent is responsible for planning.

## When to Use
- Starting a new phase of development
- Breaking down the project into implementable phases
- Updating an existing phase plan based on feedback

## Workflow Context

This project uses a Lead / Reviewer workflow:
1. **Lead plans** the phase and creates a handoff document
2. **Reviewer reviews** and provides feedback
3. **Lead revises** if needed (lead has final decision)
4. **Implementation** begins once plan is approved
5. Repeat for each phase

## Instructions

### If You Are the Lead

#### Create Mode (`/handoff-plan create [phase_name]`)

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
- [ ] In Review
- [ ] Approved
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Roles
- Lead: [from ai-handoff.yaml]
- Reviewer: [from ai-handoff.yaml]
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
- [Question for reviewer or human to address]

## Risks
- [Risk 1]: [Mitigation]
```

3. After creating the plan:
   - Prompt: "Phase plan created. Ready to create handoff for review? Use `/handoff-handoff plan [phase_name]`"

#### Update Mode (`/handoff-plan update [phase_name]`)

1. Read the existing phase plan
2. Read any feedback from `docs/handoffs/[phase_name]_plan_feedback.md`
3. Ask what needs to change or apply feedback
4. Update the plan
5. Note what changed in a "Revision History" section

### If You Are the Reviewer

You should not create plans directly. Your role is to review plans created by the lead.

Redirect to: `/handoff-review plan [phase_name]`

### List Mode (`/handoff-plan list`)

1. List all phases in `docs/phases/`
2. Show status of each phase
3. Indicate current active phase

### Show Mode (`/handoff-plan [phase_name]`)

1. Display the phase plan with current status
2. Show completion percentage of success criteria

## Examples

User: `/handoff-plan create foundation`

Response: "Creating phase plan for 'foundation'. Let me gather project context first..."
[Creates docs/phases/foundation.md with filled template]
"Phase plan created. Ready to create handoff for review? Use `/handoff-handoff plan foundation`"

User: `/handoff-plan list`

Response:
```
Phases:
- foundation: Planning (current)
- core-features: Not started
- ui-polish: Not started
```

User: `/handoff-plan update foundation`

Response: "Reading reviewer feedback... [Lists feedback items]. Which items should I incorporate?"
