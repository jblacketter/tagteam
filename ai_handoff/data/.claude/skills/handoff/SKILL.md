---
name: handoff
description: Unified command for the AI handoff workflow. Auto-detects role and state, then executes the appropriate action.
---

# Skill: /handoff

Unified command for the AI handoff workflow. Reads your role and current state, then does the right thing.

## Setup
1. Read `ai-handoff.yaml` → determine your role (lead or reviewer)
2. Read `handoff-state.json` → determine current state
3. Follow the instructions for your situation below

## Commands

| Command | Description |
|---------|-------------|
| `/handoff` | Main command — auto-detects role + state, does the right thing |
| `/handoff start [phase]` | Lead starts a plan review cycle for a new phase |
| `/handoff start [phase] impl` | Lead starts an implementation review cycle |
| `/handoff status` | Show current state and orientation for both agents |

---

## `/handoff` — Main Command

**Step 1:** Read `ai-handoff.yaml` (your role), `handoff-state.json` (state), and the active cycle file `docs/handoffs/[phase]_[type]_cycle.md`.

**Step 2 — CRITICAL: You MUST begin every `/handoff` response with this status banner:**

```
Phase: [phase] | Type: [plan/impl] | Round: [N] | Turn: [agent] | Status: [status]
 [Human-readable description of what's happening]
```

Use values from `handoff-state.json`. For the description line, use context-appropriate text:
- Your turn (lead): "Addressing reviewer feedback." or "Submitting for review."
- Your turn (reviewer): "Reviewing lead's submission."
- Not your turn: "Waiting for [agent]'s response."
- Approved: "Cycle complete — approved!"
- Escalated: "Escalated to human arbiter."
- No state: "No active handoff cycle."

If there is no state file, show: `Phase: — | Type: — | Round: — | Turn: — | Status: none`

**Step 3:** Check state and act:

- **No state file or empty:** "No active cycle. Lead should run `/handoff start [phase]`."
- **Approved / done:** If plan → "Plan approved! Implement, then `/handoff start [phase] impl`." If impl → "Implementation approved! Start next phase."
- **Escalated:** "Escalated to human arbiter. Waiting for decision in the cycle file."
- **Needs-human:** "Paused for human input. Human should edit the cycle file's `Human Input Needed` section, set `STATE: in-progress`, and set `READY_FOR:` to the appropriate role."
- **Aborted:** "Cycle was aborted. See cycle file for reason."
- **Not your turn:** "Waiting for [other agent]. Tell them to run `/handoff`."
- **Your turn:** See below.

#### As Lead (your turn)
1. Read the reviewer's latest feedback from the cycle file
2. Address the feedback: update the plan or implementation files
3. Add a new `## Round [N+1]` section to the cycle file with `### Lead` (your response) and `### Reviewer` (`_awaiting response_`)
4. Update CYCLE_STATUS: `READY_FOR: reviewer`, increment `ROUND`
5. Run: `python -m ai_handoff state set --turn reviewer --status ready --command "Read .claude/skills/handoff/SKILL.md and handoff-state.json, then act on your turn" --phase [phase] --round [N+1] --updated-by [your-agent-name]`

#### As Reviewer (your turn)
1. Read the lead's submission and the referenced plan/implementation files
2. Choose ONE action:
   - **APPROVE** — Set `STATE: approved`. Run: `python -m ai_handoff state set --status done --result approved --updated-by [your-agent-name]`
   - **REQUEST_CHANGES** — Write specific feedback. If round 5: set `STATE: escalated`, run: `python -m ai_handoff state set --status escalated --updated-by [your-agent-name]`. Otherwise: set `READY_FOR: lead`, run: `python -m ai_handoff state set --turn lead --status ready --command "Read .claude/skills/handoff/SKILL.md and handoff-state.json, then act on your turn" --phase [phase] --round [round] --updated-by [your-agent-name]`
   - **ESCALATE** — Set `STATE: escalated`. Run: `python -m ai_handoff state set --status escalated --updated-by [your-agent-name]`
   - **NEED_HUMAN** — Add `### Human Input Needed` section with your question. Set `STATE: needs-human`, `READY_FOR: human`. Run: `python -m ai_handoff state set --status escalated --updated-by [your-agent-name]`

**Step 4 — CRITICAL: You MUST end every `/handoff` response with this exact box:**

```
┌──────────────────────────────────────────────────┐
│ NEXT: Tell [agent name] to run:  /handoff        │
└──────────────────────────────────────────────────┘
```

Replace `[agent name]` with the next agent's name. For completed/escalated/needs-human states, replace with the appropriate next action.

---

## `/handoff start [phase]` — Start a New Phase

**Lead only.** Append `impl` to start an implementation review instead of a plan review.

1. Read `ai-handoff.yaml` to confirm you are the lead
2. Create or verify the phase plan at `docs/phases/[phase].md` (Summary, Scope, Technical Approach, Files, Success Criteria)
3. Create `docs/handoffs/[phase]_[plan|impl]_cycle.md` with:
   - Metadata block (phase, type, date, lead name, reviewer name)
   - Reference to plan file (or implementation files if impl)
   - `## Round 1` with `### Lead` (Action: SUBMIT_FOR_REVIEW, summary) and `### Reviewer` (`_awaiting response_`)
   - CYCLE_STATUS block: `READY_FOR: reviewer`, `ROUND: 1`, `STATE: in-progress`
4. Run: `python -m ai_handoff state set --turn reviewer --status ready --command "Read .claude/skills/handoff/SKILL.md and handoff-state.json, then act on your turn" --phase [phase] --type [plan|impl] --round 1 --updated-by [your-agent-name]`
5. Begin your response with the status banner (showing the newly created state).
6. End with the NEXT COMMAND box.

---

## `/handoff status` — Orientation & Reset

For both agents. Re-reads everything and gives a full orientation.

1. Read `ai-handoff.yaml` → show role assignment
2. Read `handoff-state.json` → show current state
3. Read active cycle file → show round, last action
4. Begin with the status banner: `Phase: [phase] | Type: [plan/impl] | Round: [N] | Turn: [agent] | Status: [state]` and description line
5. Show role assignments and cycle details below the banner
6. End with the NEXT COMMAND box showing the appropriate next action.
