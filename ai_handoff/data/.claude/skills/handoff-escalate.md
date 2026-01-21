# Skill: /escalate

> **Before using this skill**:
> 1. Read `ai-handoff.yaml` in the project root to see your configured role
> 2. Identify whether you are the **lead** or **reviewer** agent
> 3. This skill works the same for both roles

Escalate a disagreement or decision to the human arbiter.

## When to Use
- Lead and reviewer disagree on approach and can't resolve
- A decision is outside the scope of AI authority
- Technical uncertainty requires human input
- After 2 review cycles without resolution

## Instructions

### Create Escalation (`/handoff-escalate [topic]`)

1. Read `ai-handoff.yaml` to get agent names

2. Gather context:
   - What is the disagreement or decision needed?
   - What does the lead think?
   - What does the reviewer think?
   - What are the tradeoffs?

3. Create escalation document at `docs/escalations/[date]_[topic].md`:

```markdown
# Escalation: [Topic]

**Date:** [YYYY-MM-DD]
**Phase:** [Current phase]
**Status:** Awaiting Human Decision

## Summary
[Brief description of what needs to be decided]

## Lead's Position
**Agent:** [Lead agent name from config]
[What the lead recommends and why]

## Reviewer's Position
**Agent:** [Reviewer agent name from config]
[What the reviewer recommends and why]

## Key Tradeoffs
| Factor | Option A | Option B |
|--------|----------|----------|
| [Factor 1] | [Impact] | [Impact] |
| [Factor 2] | [Impact] | [Impact] |

## Impact of Decision
- If Option A: [consequences]
- If Option B: [consequences]

## Recommendation
[If lead/reviewer have a joint recommendation despite disagreement]

## Human Decision
_To be filled in by human arbiter_

- [ ] Option A
- [ ] Option B
- [ ] Other: _______________

**Rationale:** _________________

**Decided on:** _________________
```

4. Prompt: "Escalation created. Human needs to review `docs/escalations/[file].md` and mark their decision."

### List Escalations (`/handoff-escalate list`)

1. List all files in `docs/escalations/`
2. Show status of each (pending/resolved)

### Resolve Escalation (`/handoff-escalate resolve [topic]`)

1. Read the escalation document
2. Check that human has marked a decision
3. Log the decision using `/handoff-decide`
4. Update escalation status to "Resolved"
5. Apply the decision to current work

## Examples

User: `/handoff-escalate database choice`

Response: "Creating escalation for 'database choice'...

What is the lead's position?"
[Gathers info and creates document]

"Escalation created. Please review `docs/escalations/2026-01-17_database_choice.md` and mark your decision."
