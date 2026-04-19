---
name: handoff
description: Unified command for the AI handoff workflow. Auto-detects role and state, then executes the appropriate action.
---

# Skill: /handoff

Unified command for the AI handoff workflow. Reads your role and current state, then does the right thing.

## Setup
1. Read `tagteam.yaml` → determine your role (lead or reviewer)
2. Read `handoff-state.json` → determine current state
3. Follow the instructions for your situation below

## Commands

| Command | Description |
|---------|-------------|
| `/handoff` | Main command — auto-detects role + state, does the right thing |
| `/handoff start [phase]` | Lead starts a plan review cycle (single-phase mode) |
| `/handoff start [phase] impl` | Lead starts an implementation review cycle |
| `/handoff start --roadmap` | Lead starts full-roadmap mode (all incomplete phases) |
| `/handoff start --roadmap [phase]` | Lead starts full-roadmap mode from a specific phase |
| `/handoff status` | Show current state and orientation for both agents |

---

## `/handoff` — Main Command

**Step 1:** Read `tagteam.yaml` (your role) and `handoff-state.json` (state). To read the active cycle, run `tagteam cycle rounds --phase [phase] --type [type]` (works for both JSONL and legacy markdown cycles).

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
- **Approved / done:** Check `run_mode` and `result` in state:
  - If `result == "roadmap-complete"`: "Roadmap complete — all phases finished!"
  - If plan → "Plan approved! Implement, then `/handoff start [phase] impl`."
  - If impl and `run_mode == "full-roadmap"` → "Implementation approved! Watcher will auto-advance to next phase." (The watcher sets `turn: lead` for the next phase — lead runs `/handoff start [next-phase]`.)
  - If impl (single-phase) → "Implementation approved! Start next phase."
- **Escalated:** "Escalated to human arbiter. Waiting for decision in the cycle file."
- **Needs-human:** "Paused for human input. Human should edit the cycle file's `Human Input Needed` section, set `STATE: in-progress`, and set `READY_FOR:` to the appropriate role."
- **Aborted:** "Cycle was aborted. See cycle file for reason."
- **Not your turn:** "Waiting for [other agent]. Tell them to run `/handoff`."
- **Your turn:** See below.

#### As Lead (your turn)
1. Read the reviewer's latest feedback: `tagteam cycle rounds --phase [phase] --type [plan|impl]`
2. Address the feedback: update the plan or implementation files
3. Add your round and update state in one command: `tagteam cycle add --phase [phase] --type [plan|impl] --role lead --action SUBMIT_FOR_REVIEW --round [N+1] --updated-by [your-agent-name] --content "summary of changes"`

#### As Reviewer (your turn)
1. Read the lead's submission: `tagteam cycle rounds --phase [phase] --type [plan|impl]`
2. Review the referenced plan/implementation files
3. Choose ONE action (all commands update both cycle and state in one call):
   - **APPROVE:** `tagteam cycle add --phase [phase] --type [plan|impl] --role reviewer --action APPROVE --round [N] --updated-by [your-agent-name] --content "Approved."`
   - **REQUEST_CHANGES:** For detailed feedback, use stdin with a heredoc. The system auto-escalates to the human arbiter when it detects 10+ consecutive stale rounds (lead re-submitting identical content with no progress).
     ```
     tagteam cycle add --phase [phase] --type [plan|impl] --role reviewer --action REQUEST_CHANGES --round [N] --updated-by [your-agent-name] <<'EOF'
     Your detailed feedback here. Backticks, quotes, and special chars are safe.
     EOF
     ```
   - **ESCALATE:** `tagteam cycle add --phase [phase] --type [plan|impl] --role reviewer --action ESCALATE --round [N] --updated-by [your-agent-name] --content "Reason."`
   - **NEED_HUMAN:** `tagteam cycle add --phase [phase] --type [plan|impl] --role reviewer --action NEED_HUMAN --round [N] --updated-by [your-agent-name] --content "Question for human."`

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

1. Read `tagteam.yaml` to confirm you are the lead
2. Create or verify the phase plan at `docs/phases/[phase].md` (Summary, Scope, Technical Approach, Files, Success Criteria)
3. Create the cycle and update state in one command: `tagteam cycle init --phase [phase] --type [plan|impl] --lead [lead-name] --reviewer [reviewer-name] --updated-by [your-agent-name] --content "summary of initial submission"`
4. Begin your response with the status banner (showing the newly created state).
5. End with the NEXT COMMAND box.

---

## `/handoff start --roadmap [phase?]` — Start Full-Roadmap Mode

**Lead only.** Runs all remaining roadmap phases end-to-end with review gates.

1. Read `tagteam.yaml` to confirm you are the lead
2. Build the phase queue using the CLI:
   - All incomplete phases: `tagteam roadmap queue`
   - Starting from a specific phase: `tagteam roadmap queue [phase-slug]`
   - This prints a comma-separated list of phase slugs (e.g. `api-gateway,dashboard,ci-integration`)
3. The first slug in the output is the starting phase
4. Create the plan for the first phase at `docs/phases/[phase].md` if it doesn't exist
5. Create the cycle via CLI: `tagteam cycle init --phase [phase] --type plan --lead [lead-name] --reviewer [reviewer-name] --content "summary of initial submission"`
6. Run:
   ```
   tagteam state set --turn reviewer --status ready \
     --phase [first-phase] --type plan --round 1 \
     --run-mode full-roadmap \
     --roadmap-queue [comma-separated-slugs-from-step-2] \
     --roadmap-index 0 \
     --command "Read .claude/skills/handoff/SKILL.md and handoff-state.json, then act on your turn" \
     --updated-by [your-agent-name]
   ```
7. Begin with the status banner. End with the NEXT COMMAND box.

**Lifecycle in full-roadmap mode:**
- Each phase goes through: plan cycle → (lead implements) → impl cycle → (advance)
- After plan approval, watcher sets `turn: lead` — lead implements and runs `/handoff start [phase] impl`
- After impl approval, watcher advances to next phase and sets `turn: lead` — lead runs `/handoff start [next-phase]`
- After the last phase's impl approval, state is set to `result: "roadmap-complete"`

**`/handoff status` in roadmap mode shows:**
```
Phase: phase-name | Type: plan | Round: 2 | Turn: lead | Status: ready
 Mode: full-roadmap | Progress: 3/7 | Next: next-phase-name
```

---

## `/handoff status` — Orientation & Reset

For both agents. Re-reads everything and gives a full orientation.

1. Read `tagteam.yaml` → show role assignment
2. Read `handoff-state.json` → show current state
3. Read active cycle via `tagteam cycle status --phase [phase] --type [type]` and `tagteam cycle rounds --phase [phase] --type [type]` → show round, last action
4. Begin with the status banner: `Phase: [phase] | Type: [plan/impl] | Round: [N] | Turn: [agent] | Status: [state]` and description line
5. Show role assignments and cycle details below the banner
6. End with the NEXT COMMAND box showing the appropriate next action.
