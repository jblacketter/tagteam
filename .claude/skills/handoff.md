# Skill: /handoff

Create handoff documents for the Claude/Codex review cycle. This is the bridge between planning and review.

## When to Use
- After Claude completes a phase plan (planning handoff)
- After Claude completes phase implementation (implementation handoff)
- When transitioning between Claude and Codex

## Handoff Types

1. **Planning Handoff**: Claude -> Codex for plan review
2. **Implementation Handoff**: Claude -> Codex for code review

## Instructions

### Create Planning Handoff (`/handoff plan [phase_name]`)

1. Read the phase plan from `docs/phases/[phase_name].md`
2. Create `docs/handoffs/[phase_name]_plan_handoff.md`:

```markdown
# Handoff: [Phase Name] - Plan Review

**Date:** [YYYY-MM-DD]
**From:** Claude (Lead)
**To:** Codex (Reviewer)
**Type:** Planning Review

## Summary
[Brief summary of what the phase plan covers]

## What Needs Review
- Technical approach feasibility
- Scope completeness
- Success criteria clarity
- Risk assessment
- File/structure decisions

## Specific Questions for Codex
1. [Specific question about the plan]
2. [Another question]

## Phase Plan Location
`docs/phases/[phase_name].md`

## Review Checklist for Codex
- [ ] Technical approach is sound
- [ ] Scope is appropriate (not too big/small)
- [ ] Success criteria are testable
- [ ] No major risks overlooked
- [ ] File structure makes sense
- [ ] Dependencies are identified

## Response Instructions
Please provide feedback in `docs/handoffs/[phase_name]_plan_feedback.md` using the feedback template.

---
*Handoff created by Claude. Codex: use `/review plan [phase_name]` to begin review.*
```

3. Update phase status to "In Review (Codex)"
4. Prompt: "Planning handoff created. Codex can now run `/review plan [phase_name]`"

### Create Implementation Handoff (`/handoff impl [phase_name]`)

1. Read the phase plan
2. Gather list of files created/modified
3. Create `docs/handoffs/[phase_name]_impl_handoff.md`:

```markdown
# Handoff: [Phase Name] - Implementation Review

**Date:** [YYYY-MM-DD]
**From:** Claude (Lead)
**To:** Codex (Reviewer)
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
*Handoff created by Claude. Codex: use `/review impl [phase_name]` to begin review.*
```

### Read Feedback (`/handoff read [phase_name]`)

1. Check for feedback files:
   - `docs/handoffs/[phase_name]_plan_feedback.md`
   - `docs/handoffs/[phase_name]_impl_feedback.md`
2. Display feedback with action items highlighted
3. Prompt: "Use `/plan update [phase_name]` to incorporate feedback"

### List Handoffs (`/handoff list`)

1. List all handoff documents in `docs/handoffs/`
2. Show status: pending review, feedback received, resolved

## Examples

User: `/handoff plan foundation`

Response: Creates planning handoff document and updates phase status.
"Handoff created at `docs/handoffs/foundation_plan_handoff.md`. Codex can now review."

User: `/handoff read foundation`

Response: "Codex provided feedback on the foundation plan:
1. **AGREE**: Good technical choice
2. **SUGGEST**: Add an index for performance
3. **QUESTION**: Should we support feature X?

Use `/plan update foundation` to incorporate this feedback."
