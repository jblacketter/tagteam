# Skill: /escalate

Escalate a disagreement or decision to the human arbiter.

## When to Use
- Claude and Codex disagree on approach and can't resolve
- A decision is outside the scope of AI authority
- Technical uncertainty requires human input
- After 2 review cycles without resolution

## Instructions

### Create Escalation (`/escalate [topic]`)

1. Gather context:
   - What is the disagreement or decision needed?
   - What does Claude think?
   - What does Codex think?
   - What are the tradeoffs?

2. Create escalation document at `docs/escalations/[date]_[topic].md`:

```markdown
# Escalation: [Topic]

**Date:** [YYYY-MM-DD]
**Phase:** [Current phase]
**Status:** Awaiting Human Decision

## Summary
[Brief description of what needs to be decided]

## Claude's Position
[What Claude recommends and why]

## Codex's Position
[What Codex recommends and why]

## Key Tradeoffs
| Factor | Option A | Option B |
|--------|----------|----------|
| [Factor 1] | [Impact] | [Impact] |
| [Factor 2] | [Impact] | [Impact] |

## Impact of Decision
- If Option A: [consequences]
- If Option B: [consequences]

## Recommendation
[If Claude/Codex have a joint recommendation despite disagreement]

## Human Decision
_To be filled in by human arbiter_

- [ ] Option A
- [ ] Option B
- [ ] Other: _______________

**Rationale:** _________________

**Decided on:** _________________
```

3. Prompt: "Escalation created. Human needs to review `docs/escalations/[file].md` and mark their decision."

### List Escalations (`/escalate list`)

1. List all files in `docs/escalations/`
2. Show status of each (pending/resolved)

### Resolve Escalation (`/escalate resolve [topic]`)

1. Read the escalation document
2. Check that human has marked a decision
3. Log the decision using `/decide`
4. Update escalation status to "Resolved"
5. Apply the decision to current work

## Examples

User: `/escalate database choice`

Response: "Creating escalation for 'database choice'...

What is Claude's position?"
[Gathers info and creates document]

"Escalation created. Please review `docs/escalations/2026-01-17_database_choice.md` and mark your decision."
