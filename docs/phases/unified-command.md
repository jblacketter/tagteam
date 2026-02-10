# Phase: Unified Command (Command Drift Fix)

## Status
- [x] Planning
- [x] In Review
- [x] Approved
- [x] Implementation
- [x] Implementation Review
- [x] Complete

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary
**What:** Replace 10 skill files (~30+ subcommands) with a single `/handoff` command that auto-detects role and state, then does the right thing.

**Why:** In long context windows, agents drift from the workflow — they stop giving the handoff command, give the wrong one, or dump large summaries instead. Root cause: too many commands create cognitive load that degrades with context length. A single, state-driven command eliminates ambiguity.

**Depends on:** None (all 6 prior phases complete)

## Problem Statement

Current command surface (10 skills, 30+ subcommands):
```
/handoff-plan create|update|list|[phase]
/handoff-handoff plan|impl|read|list [phase]
/handoff-review plan|impl [phase]
/handoff-implement start|resume|status|complete [phase]
/handoff-phase current|list|advance|complete|roadmap [phase]
/handoff-status [phase]|handoffs|quick
/handoff-decide [summary]|list|show [keyword]
/handoff-escalate [topic]|list|resolve [topic]
/handoff-sync |reviewer|lead
/handoff-cycle start|status|abort [phase]
```

Observed failure modes:
1. Agent gives summary text instead of the next command
2. Agent gives wrong command (e.g., `/handoff-review` when it should be `/handoff-cycle`)
3. Agent remembers to give command early in session, forgets later
4. Reminding the agent works once, then it drifts again

## Scope

### In Scope
- New unified `/handoff` skill file (single file, <150 lines)
- Deprecation notices on old skill files
- Updated README workflow section
- Updated `setup.py` next-steps output

### Out of Scope
- Python code changes to `state.py`, `watcher.py`, etc. (state infrastructure works fine)
- Removing old skill files (backward compatibility)
- Changes to cycle document format (it works, just needs simpler skill wrapping)
- TUI or web dashboard changes

## Technical Approach

### Design: State-Driven Single Command

The new `/handoff` skill reads state and role, then dispatches automatically:

```
Agent runs /handoff
    ↓
Read ai-handoff.yaml → my role (lead/reviewer)
Read handoff-state.json → current state
    ↓
┌─────────────────────────────────────────────┐
│ No active state?                            │
│   → "No active cycle. Run /handoff start"   │
│                                             │
│ My turn?                                    │
│   → Do my job (plan/review/respond)         │
│   → Update cycle doc + state                │
│   → Output: NEXT COMMAND box                │
│                                             │
│ Not my turn?                                │
│   → "Waiting for [agent]. /handoff"         │
│                                             │
│ Approved?                                   │
│   → "Done! Next: implement or complete"     │
│                                             │
│ Escalated?                                  │
│   → "Human needed. See escalation."         │
└─────────────────────────────────────────────┘
```

### Command Surface (3 total, down from 30+)

| Command | Who | When |
|---------|-----|------|
| `/handoff` | Both | Main command — does the right thing |
| `/handoff start [phase]` | Lead | Begin a new phase (creates plan + cycle) |
| `/handoff status` | Both | Quick orientation / reset when drifting |

### Mandatory Output Format

Every `/handoff` response MUST end with:

```
┌──────────────────────────────────────────────┐
│ NEXT: Tell [agent name] to run:  /handoff    │
└──────────────────────────────────────────────┘
```

This rigid visual pattern:
- Is easy for the LLM to reproduce consistently
- Gives the user a single, copy-pasteable command every time
- Self-reinforces — every invocation re-anchors the pattern

### Drift Prevention Mechanisms

1. **Short skill file** — Under 150 lines. Less context = less drift.
2. **State re-read every invocation** — Agent re-orients on every call.
3. **Single command name** — No ambiguity about which command to output.
4. **Rigid output block** — The NEXT COMMAND box is a strong anchor pattern.
5. **`/handoff status` as reset** — User can say "run /handoff status" to fully re-orient a drifting agent.

### Backward Compatibility

- Old skill files get a deprecation header pointing to `/handoff`
- Old files remain functional (not deleted)
- New projects get `/handoff` as the primary skill
- Existing cycle document format unchanged

## Files to Create/Modify

### Create
- `ai_handoff/data/.claude/skills/handoff.md` — The new unified skill (~150 lines)

### Modify
- `ai_handoff/data/.claude/skills/handoff-plan.md` — Add deprecation notice
- `ai_handoff/data/.claude/skills/handoff-handoff.md` — Add deprecation notice
- `ai_handoff/data/.claude/skills/handoff-review.md` — Add deprecation notice
- `ai_handoff/data/.claude/skills/handoff-implement.md` — Add deprecation notice
- `ai_handoff/data/.claude/skills/handoff-phase.md` — Add deprecation notice
- `ai_handoff/data/.claude/skills/handoff-status.md` — Add deprecation notice
- `ai_handoff/data/.claude/skills/handoff-decide.md` — Add deprecation notice
- `ai_handoff/data/.claude/skills/handoff-escalate.md` — Add deprecation notice
- `ai_handoff/data/.claude/skills/handoff-sync.md` — Add deprecation notice
- `ai_handoff/data/.claude/skills/handoff-cycle.md` — Add deprecation notice
- `README.md` — Update workflow section with simplified commands
- `ai_handoff/setup.py` — Update "Next steps" output text

### Also update (project-local copies, this repo)
- `.claude/skills/handoff.md` — Copy of the new skill for this project
- `.claude/skills/handoff-*.md` — Deprecation notices on existing project copies

## Success Criteria
- [x] Single `/handoff` skill file under 150 lines
- [x] `/handoff` auto-detects role and state correctly
- [x] Every `/handoff` response ends with the NEXT COMMAND box
- [x] `/handoff start [phase]` creates plan + cycle doc + state
- [x] `/handoff status` provides full orientation summary
- [x] Old skills have deprecation notice pointing to `/handoff`
- [x] README reflects simplified 3-command workflow
- [x] `setup.py` next-steps references `/handoff` instead of old commands

## Open Questions
- Should `/handoff start [phase]` handle both plan and impl cycles, or just plan? (Proposal: It asks the lead what type — plan or impl — keeping the command surface minimal)
- Should old skill files be removed entirely in a future phase? (Proposal: Keep deprecated for now, remove in a later cleanup)
- Should the deprecation notice be a small header or should the old files be gutted to just the redirect? (Proposal: Small header — keeps them functional as fallback)

## Risks
- **LLM skill loading**: Some AI tools may only load skills by exact name. If `/handoff` doesn't get recognized as a skill, agents can't use it. Mitigation: Test with Claude Code and Codex.
- **Existing project migration**: Users with existing projects need to re-run `setup` to get the new skill. Mitigation: Document in README.
