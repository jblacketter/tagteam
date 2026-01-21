# Skill: /handoff

> **Before using this skill**:
> 1. Read `ai-handoff.yaml` in the project root to see your configured role
> 2. Identify whether you are the **lead** or **reviewer** agent
> 3. Follow the instructions for your role below

Create handoff documents for the Lead/Reviewer cycle. This is the bridge between planning and review.

## When to Use
- After the lead completes a phase plan (planning handoff)
- After the lead completes phase implementation (implementation handoff)
- When transitioning between lead and reviewer

## Handoff Types

1. **Planning Handoff**: Lead -> Reviewer for plan review
2. **Implementation Handoff**: Lead -> Reviewer for code review

## Instructions

### If You Are the Lead

#### Create Planning Handoff (`/handoff-handoff plan [phase_name]`)

1. Read the phase plan from `docs/phases/[phase_name].md`
2. Read `ai-handoff.yaml` to get agent names
3. Create `docs/handoffs/[phase_name]_plan_handoff.md`:

```markdown
# Handoff: [Phase Name] - Plan Review

**Date:** [YYYY-MM-DD]
**From:** [Lead agent name] (Lead)
**To:** [Reviewer agent name] (Reviewer)
**Type:** Planning Review

## Summary
[Brief summary of what the phase plan covers]

## What Needs Review
- Technical approach feasibility
- Scope completeness
- Success criteria clarity
- Risk assessment
- File/structure decisions

## Specific Questions for Reviewer
1. [Specific question about the plan]
2. [Another question]

## Phase Plan Location
`docs/phases/[phase_name].md`

## Review Checklist
- [ ] Technical approach is sound
- [ ] Scope is appropriate (not too big/small)
- [ ] Success criteria are testable
- [ ] No major risks overlooked
- [ ] File structure makes sense
- [ ] Dependencies are identified

## Response Instructions
Please provide feedback in `docs/handoffs/[phase_name]_plan_feedback.md` using the feedback template.

---
*Handoff created by lead. Reviewer: use `/handoff-review plan [phase_name]` to begin review.*
```

4. Update phase status to "In Review"
5. Prompt: "Planning handoff created. Reviewer can now run `/handoff-review plan [phase_name]`"

#### Create Implementation Handoff (`/handoff-handoff impl [phase_name]`)

1. Read the phase plan
2. Gather list of files created/modified
3. Create `docs/handoffs/[phase_name]_impl_handoff.md`:

```markdown
# Handoff: [Phase Name] - Implementation Review

**Date:** [YYYY-MM-DD]
**From:** [Lead agent name] (Lead)
**To:** [Reviewer agent name] (Reviewer)
**Type:** Implementation Review

## Summary
[What was implemented in this phase]

## Files Created
- `path/to/file` - [description]

## Files Modified
- `path/to/existing` - [what changed]

## Implementation Notes
[Key decisions made during implementation, any deviations from plan]

## Testing Done
- [Test 1 and result]
- [Test 2 and result]

## Success Criteria Status
- [x] [Completed criterion]
- [ ] [Pending criterion - explain why]

## Known Issues
- [Issue if any]

## Review Focus Areas
1. [Specific area needing careful review]
2. [Another area]

---
*Handoff created by lead. Reviewer: use `/handoff-review impl [phase_name]` to begin review.*
```

### If You Are the Reviewer

You receive handoffs, not create them. To read a handoff:

1. Check `docs/handoffs/` for pending handoffs
2. Read the handoff document
3. Use `/handoff-review plan [phase]` or `/handoff-review impl [phase]` to perform your review

### Read Feedback (`/handoff-handoff read [phase_name]`)

1. Check for feedback files:
   - `docs/handoffs/[phase_name]_plan_feedback.md`
   - `docs/handoffs/[phase_name]_impl_feedback.md`
2. Display feedback with action items highlighted
3. Prompt: "Use `/handoff-plan update [phase_name]` to incorporate feedback"

### List Handoffs (`/handoff-handoff list`)

1. List all handoff documents in `docs/handoffs/`
2. Show status: pending review, feedback received, resolved

## Examples

User: `/handoff-handoff plan foundation`

Response: Creates planning handoff document and updates phase status.
"Handoff created at `docs/handoffs/foundation_plan_handoff.md`. Reviewer can now review."

User: `/handoff-handoff read foundation`

Response: "Reviewer provided feedback on the foundation plan:
1. **AGREE**: Good technical choice
2. **SUGGEST**: Add an index for performance
3. **QUESTION**: Should we support feature X?

Use `/handoff-plan update foundation` to incorporate this feedback."
