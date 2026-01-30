# Skill: /handoff-cycle

> **Before using this skill**:
> 1. Read `ai-handoff.yaml` in the project root to see your configured role
> 2. Identify whether you are the **lead** or **reviewer** agent
> 3. This skill automates the back-and-forth review process

Automate the review cycle between lead and reviewer using a single shared cycle document. Instead of manually creating separate handoff/feedback files each round, both agents work from one file with automatic turn tracking.

**Orchestration integration:** This skill writes `handoff-state.json` at each turn transition. A watcher daemon (`python -m ai_handoff watch`) can monitor this file and automatically notify or trigger the next agent. You MUST run the state update command at the end of every turn.

## When to Use
- After creating a plan that needs review (instead of `/handoff-handoff`)
- After completing implementation that needs review
- When you want to reduce manual copy-paste during multi-round reviews
- To check the current state of an ongoing review cycle

## Workflow Context

This skill replaces the manual handoff/feedback cycle:

**Old flow (manual):**
1. Lead: `/handoff-handoff plan [phase]` → creates handoff
2. Reviewer: `/handoff-review plan [phase]` → creates feedback
3. Lead: reads feedback, updates plan
4. Repeat with new handoff/feedback files each round

**New flow (automated):**
1. Lead: `/handoff-cycle start [phase] plan` → creates cycle file
2. Reviewer: `/handoff-cycle [phase]` → responds in same file
3. Lead: `/handoff-cycle [phase]` → sees feedback, responds
4. Repeat until approved (all in one file)

## Commands

| Command | Description |
|---------|-------------|
| `/handoff-cycle start [phase] plan` | Lead starts a plan review cycle |
| `/handoff-cycle start [phase] impl` | Lead starts an implementation review cycle |
| `/handoff-cycle [phase]` | Continue the cycle (auto-detects your role) |
| `/handoff-cycle status [phase]` | View current status without modifying |
| `/handoff-cycle abort [phase]` | Cancel the cycle with a reason |

## Instructions

### Start a Cycle (`/handoff-cycle start [phase] plan|impl`)

**Lead only.** Creates a new cycle document.

1. Read `ai-handoff.yaml` to get agent names
2. Verify you are the lead agent
3. For plan reviews: verify `docs/phases/[phase].md` exists
4. For impl reviews: verify implementation is complete

5. Create cycle document at `docs/handoffs/[phase]_[type]_cycle.md`:

```markdown
# Review Cycle: [phase] ([type])

## Metadata
- **Phase:** [phase]
- **Type:** plan | impl
- **Started:** [YYYY-MM-DD]
- **Lead:** [lead name from config]
- **Reviewer:** [reviewer name from config]

## Reference
- Plan: `docs/phases/[phase].md`
- Implementation: [list key files if impl review]

---

## Round 1

### Lead
**Action:** SUBMIT_FOR_REVIEW

[Summary of the plan or implementation. For plans, highlight key decisions. For impl, list what was built and any deviations from plan.]

### Reviewer
_awaiting response_

---

<!-- CYCLE_STATUS (single source of truth - do not duplicate above) -->
READY_FOR: reviewer
ROUND: 1
STATE: in-progress
<!-- /CYCLE_STATUS -->
```

6. **Update orchestration state** by running this bash command:
   ```bash
   python -m ai_handoff state set --turn reviewer --status ready --command "/handoff-cycle [phase]" --phase [phase] --type [plan|impl] --round 1 --updated-by [your-agent-name]
   ```
7. Output: "Cycle started. Reviewer should run `/handoff-cycle [phase]` to begin review."

### Continue a Cycle (`/handoff-cycle [phase]`)

Continues an existing cycle based on whose turn it is.

1. Read `ai-handoff.yaml` to determine your role
2. Read the cycle file `docs/handoffs/[phase]_*_cycle.md`
3. Parse the `<!-- CYCLE_STATUS -->` block to get current state

**If STATE is `approved`:**
- Output: "Cycle complete! Plan approved." (or "Implementation approved.")
- For plan: "Next: Run `/handoff-implement start [phase]`"
- For impl: "Next: Run `/handoff-phase complete [phase]`"

**If STATE is `escalated`:**
- Output: "Cycle escalated to human arbiter after [n] rounds."
- Display summary of disagreement points

**If STATE is `aborted`:**
- Output: "Cycle was aborted by [who]: [reason]"

**If STATE is `needs-human`:**
- Output: "Cycle paused for human input. See the Human Input Needed section."

**If READY_FOR does not match your role:**
- Output: "Waiting for [other agent]. Switch to [other agent] terminal."

**If READY_FOR matches your role:**

#### As Lead (READY_FOR: lead)
1. Display the reviewer's feedback from the latest round
2. Ask lead to respond (address feedback or explain disagreement)
3. Update the cycle file:
   - Add a new `## Round [n+1]` section (or update current round)
   - Fill in `### Lead` with action and response
   - Set `### Reviewer` to `_awaiting response_`
   - Update status block: `READY_FOR: reviewer`, increment `ROUND` if new round
4. **Update orchestration state** by running:
   ```bash
   python -m ai_handoff state set --turn reviewer --status ready --command "/handoff-cycle [phase]" --phase [phase] --round [new-round-number] --updated-by [your-agent-name]
   ```
5. Output: "Response submitted. Reviewer should run `/handoff-cycle [phase]`"

#### As Reviewer (READY_FOR: reviewer)
1. Display the lead's submission
2. Read the referenced plan or implementation files
3. Ask reviewer to choose an action:
   - **APPROVE**: Accept and end cycle
   - **REQUEST_CHANGES**: Provide feedback, continue cycle
   - **NEED_HUMAN**: Pause for human input
   - **ABORT**: Cancel the cycle

4. Update the cycle file based on action:

   **If APPROVE:**
   - Fill in `### Reviewer` with approval message
   - Update status: `STATE: approved`
   - **Update orchestration state:**
     ```bash
     python -m ai_handoff state set --status done --result approved --updated-by [your-agent-name]
     ```
   - Output: "Approved! Lead can proceed."

   **If REQUEST_CHANGES:**
   - Fill in `### Reviewer` with feedback
   - Check if this is round 5:
     - If yes: Set `STATE: escalated`, then update orchestration state:
       ```bash
       python -m ai_handoff state set --status escalated --updated-by [your-agent-name]
       ```
       Output escalation message.
     - If no: Set `READY_FOR: lead`, then **update orchestration state:**
       ```bash
       python -m ai_handoff state set --turn lead --status ready --command "/handoff-cycle [phase]" --phase [phase] --round [current-round] --updated-by [your-agent-name]
       ```
   - Output: "Feedback submitted. Lead should run `/handoff-cycle [phase]`"

   **If NEED_HUMAN:**
   - Add `### Human Input Needed` section with question
   - Set `STATE: needs-human`, `READY_FOR: human`
   - **Update orchestration state:**
     ```bash
     python -m ai_handoff state set --status escalated --updated-by [your-agent-name]
     ```
   - Output: "Cycle paused. Human should edit the cycle file to respond."

   **If ABORT:**
   - Set `STATE: aborted`
   - Add `### Aborted` section with who and reason
   - **Update orchestration state:**
     ```bash
     python -m ai_handoff state set --status aborted --reason "[reason]" --updated-by [your-agent-name]
     ```
   - Output: "Cycle aborted."

### View Status (`/handoff-cycle status [phase]`)

Read-only view of current cycle state.

1. Read the cycle file
2. Parse status block
3. Display:
   - Current round
   - Whose turn
   - State
   - Summary of last action

### Abort a Cycle (`/handoff-cycle abort [phase]`)

Cancel an in-progress cycle.

1. Read `ai-handoff.yaml` to get your role
2. Ask for abort reason
3. Update cycle file:
   - Add `### Aborted` section with who (your role) and reason
   - Set `STATE: aborted`
4. **Update orchestration state:**
   ```bash
   python -m ai_handoff state set --status aborted --reason "[reason]" --updated-by [your-agent-name]
   ```
5. Output: "Cycle aborted. File preserved for history."

## Human Input Flow

When an agent sets `STATE: needs-human`:

1. Human reads the `### Human Input Needed` section
2. Human adds a `### Human Response` section with their answer
3. Human edits the status block:
   - Change `READY_FOR:` to `lead` or `reviewer` (whoever should continue)
   - Change `STATE:` back to `in-progress`
4. The designated agent runs `/handoff-cycle [phase]` to continue

## Auto-Escalation

At round 5, if the reviewer chooses REQUEST_CHANGES:
- The cycle automatically escalates
- `STATE` is set to `escalated`
- A summary of all rounds is displayed
- Human arbiter must make a decision

## Examples

**Starting a plan review:**
```
User: /handoff-cycle start foundation plan
Agent: "Cycle started for 'foundation' plan review.
        Reviewer should run `/handoff-cycle foundation` to begin review."
```

**Continuing as reviewer:**
```
User: /handoff-cycle foundation
Agent: "Reading cycle... It's your turn to review.

        Lead submitted:
        [summary of plan]

        Referenced plan: docs/phases/foundation.md

        Choose action:
        1. APPROVE - Plan looks good
        2. REQUEST_CHANGES - Need modifications
        3. NEED_HUMAN - Need human input
        4. ABORT - Cancel this cycle"

User: "2 - need to add error handling section"

Agent: "Feedback recorded. Lead should run `/handoff-cycle foundation`"
```

**Checking status:**
```
User: /handoff-cycle status foundation
Agent: "Cycle: foundation (plan)
        Round: 2 of 5
        Turn: lead
        State: in-progress
        Last action: Reviewer requested changes"
```
