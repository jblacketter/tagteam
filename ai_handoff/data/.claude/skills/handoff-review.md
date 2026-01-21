# Skill: /review

> **Before using this skill**:
> 1. Read `ai-handoff.yaml` in the project root to see your configured role
> 2. Identify whether you are the **lead** or **reviewer** agent
> 3. Follow the instructions for your role below

Run structured reviews on plans or implementations. The reviewer agent is primarily responsible for reviews.

## When to Use
- After receiving a handoff from the lead
- When reviewing a phase plan
- When reviewing implemented code
- For quality checks before phase completion

## Review Types

1. **Plan Review**: Review a phase plan before implementation
2. **Implementation Review**: Review code after implementation
3. **Quick Review**: Check recent changes without formal handoff

## Instructions

### If You Are the Reviewer

#### Plan Review (`/handoff-review plan [phase_name]`)

1. Read the handoff document: `docs/handoffs/[phase_name]_plan_handoff.md`
2. Read the phase plan: `docs/phases/[phase_name].md`
3. Read project requirements for context
4. Read `ai-handoff.yaml` to get agent names
5. Apply the planning checklist:

```markdown
# Plan Review: [Phase Name]

**Reviewer:** [Your agent name from config]
**Date:** [YYYY-MM-DD]

## Checklist

### Scope & Feasibility
- [ ] Scope is clearly defined
- [ ] Scope is appropriately sized for one phase
- [ ] Technical approach is feasible
- [ ] Dependencies are correctly identified

### Technical Design
- [ ] Architecture decisions are sound
- [ ] File structure is logical
- [ ] Follows project conventions
- [ ] No over-engineering

### Success Criteria
- [ ] Criteria are specific and testable
- [ ] Criteria match the stated scope
- [ ] All major deliverables have criteria

### Risks & Questions
- [ ] Major risks are identified
- [ ] Mitigations are reasonable
- [ ] Open questions are appropriate

## Verdict: [APPROVE / REQUEST CHANGES / NEEDS DISCUSSION]

## Feedback

### Agreements
- [What looks good]

### Suggested Changes
- [Change 1 with rationale]
- [Change 2 with rationale]

### Questions
- [Question needing clarification]

### Blocking Issues (if REQUEST CHANGES)
- [Issue that must be fixed before proceeding]
```

5. Save to `docs/handoffs/[phase_name]_plan_feedback.md`
6. Prompt: "Review complete. Lead can read feedback with `/handoff-handoff read [phase_name]`"

#### Implementation Review (`/handoff-review impl [phase_name]`)

1. Read the implementation handoff
2. Read the phase plan for expected behavior
3. Read the actual code files listed in handoff
4. Apply the code review checklist:

```markdown
# Implementation Review: [Phase Name]

**Reviewer:** [Your agent name from config]
**Date:** [YYYY-MM-DD]

## Files Reviewed
- [List files with line counts]

## Checklist

### Correctness
- [ ] Implementation matches the plan
- [ ] Success criteria are met
- [ ] No obvious bugs
- [ ] Edge cases handled

### Code Quality
- [ ] Code is readable and clear
- [ ] No unnecessary complexity
- [ ] Error handling is appropriate
- [ ] No hardcoded values that should be config

### Security
- [ ] No injection vulnerabilities
- [ ] No XSS vulnerabilities
- [ ] Input validation present
- [ ] Secrets not hardcoded

### Testing
- [ ] Tests exist for key functionality
- [ ] Tests pass
- [ ] Test coverage is reasonable

## Verdict: [APPROVE / REQUEST CHANGES / NEEDS DISCUSSION]

## Feedback

### Looks Good
- [What was done well]

### Issues Found
1. **[SEVERITY]** [Issue description]
   - Location: `file:line`
   - Suggested fix: [How to fix]

### Suggestions (non-blocking)
- [Optional improvement]
```

5. Save to `docs/handoffs/[phase_name]_impl_feedback.md`

### If You Are the Lead

You can self-review using the same checklists, but typically the reviewer agent handles formal reviews.

#### Quick Review (`/handoff-review`)

1. Check for pending handoffs in `docs/handoffs/`
2. Check recent file changes
3. Ask user what to review if unclear
4. Run appropriate review type

## Examples

User: `/handoff-review plan foundation`

Response: Reads handoff and plan, runs checklist, saves feedback.
"Review complete: APPROVE with minor suggestions. Feedback saved."

User: `/handoff-review impl foundation`

Response: Reads implementation handoff, reviews code files, runs checklist.
"Review complete: REQUEST CHANGES - 1 blocking issue found. See feedback."

User: `/handoff-review`

Response: "Pending handoffs: foundation_plan_handoff.md (awaiting review). Run `/handoff-review plan foundation`?"
