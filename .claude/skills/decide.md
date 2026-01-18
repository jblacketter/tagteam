# Skill: /decide

Log decisions to maintain project history and rationale.

## When to Use
- After making an architectural or implementation choice
- When resolving disagreements between Claude and Codex
- When the human arbiter makes a decision
- When choosing between alternatives

## Decision Log Location

`docs/decision_log.md` - Created on first use if doesn't exist.

## Instructions

### Log Decision (`/decide [summary]`)

1. If summary not provided, ask:
   - What was decided?
   - What was the context?
   - What alternatives were considered?

2. Determine who decided:
   - **Claude**: Claude made the call (within their authority as lead)
   - **Codex**: Codex's suggestion was accepted
   - **Human**: User/arbiter made the decision
   - **Consensus**: Claude and Codex agreed

3. Append to `docs/decision_log.md`:

```markdown
---
## [YYYY-MM-DD]: [Decision Title]

**Decision:** [Clear statement of what was decided]

**Context:** [Why this decision was needed]

**Alternatives Considered:**
- [Option 1]: [Pros/cons]
- [Option 2]: [Pros/cons]

**Rationale:** [Why this option was chosen]

**Decided By:** [Claude/Codex/Human/Consensus]

**Phase:** [Which phase this relates to]

**Follow-ups:**
- [Any actions triggered by this decision]
```

4. Confirm: "Decision logged: [title]. See `docs/decision_log.md`"

### View Decisions (`/decide list`)

1. Read `docs/decision_log.md`
2. Display summary table:

```markdown
## Recent Decisions

| Date | Decision | By | Phase |
|------|----------|-----|-------|
| 2026-01-17 | Use framework X | Claude | foundation |
| 2026-01-16 | Database choice | Consensus | foundation |
```

### View Decision (`/decide show [keyword]`)

1. Search decision log for keyword
2. Display full decision entry

### Dispute Resolution

When Claude and Codex disagree:

1. Log both positions in the decision entry
2. Set "Decided By: Pending - Human Arbiter"
3. Clearly state what the human needs to decide
4. Prompt: "This decision needs human input. [Summary of options]"

After human decides:
1. Update decision with human's choice
2. Update "Decided By: Human"
3. Note the resolution

## Examples

User: `/decide use framework X instead of Y`

Response:
"Logging decision...

What was the context for this choice?"

User: "Simpler for our use case"

Response:
"Decision logged:
- **Use framework X over Y**
- Rationale: Simpler for our use case
- Decided by: Claude
- Phase: foundation

See `docs/decision_log.md`"

User: `/decide list`

Response: Shows table of all decisions.

User: `/decide show database`

Response: Shows full entry for the database decision.
