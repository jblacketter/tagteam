---
name: handoff
description: Unified command for the AI handoff workflow. Auto-detects role and state, then executes the appropriate action.
---

# Skill: /tagteam:handoff

Unified command for the AI handoff workflow. Reads your role and current state, then does the right thing.

## Setup
1. Read `tagteam.yaml` → determine your role (lead or reviewer)
2. Read `handoff-state.json` → determine current state
3. Follow the instructions for your situation below

**One contract, three ways in (3.10).** This document is served by the `tagteam` Claude Code plugin as `/tagteam:handoff`. A project that still carries a vendored copy at `.claude/skills/handoff/SKILL.md` invokes the same contract as `/handoff` — either name, same rules. An agent without Claude Code's plugin skills (Codex, or any shell) reads it with `tagteam contract`. Wherever this document says `/tagteam:handoff`, substitute the name that exists in your session.

## Commands

| Command | Description |
|---------|-------------|
| `/tagteam:handoff` | Main command — auto-detects role + state, does the right thing |
| `/tagteam:handoff start [phase]` | Lead starts a plan review cycle (single-phase mode) |
| `/tagteam:handoff start [phase] impl` | Lead starts an implementation review cycle |
| `/tagteam:handoff start --roadmap` | Lead starts full-roadmap mode (all incomplete phases) |
| `/tagteam:handoff start --roadmap [phase]` | Lead starts full-roadmap mode from a specific phase |
| `/tagteam:handoff status` | Show current state and orientation for both agents |

---

## `/tagteam:handoff` — Main Command

**Step 1:** Read `tagteam.yaml` (your role) and `handoff-state.json` (state). To read the active cycle, run `tagteam cycle rounds --phase [phase] --type [type]` (works for both JSONL and legacy markdown cycles). Add `--tail N` to read only the last N entries when you just need the latest feedback — it saves context tokens on long cycles.

**Headless turns.** If you were started by `tagteam watch --mode headless`, your prompt already contains this contract, the current state, and the round tail, and there is no human at the terminal. The contract is identical: do the work, make exactly one cycle-writing call (`tagteam cycle add` / `cycle init`) with `--updated-by [your-agent-name]`, then stop. The orchestrator verifies that call happened and pauses (with a notification) if it did not.

**Arbiter interjections.** The human arbiter can leave notes with `tagteam interject`. In a headless turn they appear in your prompt under `=== ARBITER INTERJECTIONS (unconsumed) ===`; interactively they appear as an `interjections` list on the round in `tagteam cycle rounds` output. Treat them as authoritative instructions for this cycle (they may already have been addressed in earlier rounds — verify before acting), and mention in your submission how you handled them.

**Standing orders.** The arbiter's run-level instructions (`tagteam orders`) come with every turn: in a headless prompt under `=== STANDING ORDERS ===`, and interactively on stderr above `tagteam cycle rounds` output. There are two kinds. **Enforced:** `stop` (`phase` = the run stops for the arbiter after each phase's implementation is approved; `roadmap` = it goes on to the next ready roadmap phase). The engine applies it when the impl approval is recorded, by setting `run_mode`/`roadmap` in the state, so act on the state and don't second-guess it. Escalations and questions stop a run either way. **Advisory:** numbered notes such as "commit at phase end but hold the PR for my approval". Tagteam delivers these but cannot enforce them, so following them is up to you. A note marked *this run* wins over a project note it conflicts with. Mention in your submission how you handled any advisory note that applied.

**Step 2 — CRITICAL: You MUST begin every `/tagteam:handoff` response with this status banner:**

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

- **No state file or empty:** "No active cycle. Lead should run `/tagteam:handoff start [phase]`."
- **Approved / done:** Check `run_mode` and `result` in state:
  - If `result == "roadmap-complete"`: "Roadmap complete — all phases finished!"
  - If plan → "Plan approved! Implement, then `/tagteam:handoff start [phase] impl`."
  - If impl and `run_mode == "full-roadmap"` → "Implementation approved! Watcher will auto-advance to next phase." (The watcher sets `turn: lead` for the next phase — lead runs `/tagteam:handoff start [next-phase]`.)
  - If impl (single-phase) → "Implementation approved! Start next phase." (If standing orders are in force, `tagteam orders` shows what the approval did: the run stopped, or it was converted to a roadmap run, in which case `run_mode` is `full-roadmap` and the bullet above applies.)
- **Escalated:** "Escalated to human arbiter." (If `roadmap.pause_reason` is set instead — `blocked: …` / `roadmap invalid: …` / `stale queue: …` — this is a full-roadmap pause, not a ruling: the arbiter unblocks and runs `tagteam roadmap resume`.) The arbiter reads `tagteam brief` (a decision brief, if the escalation briefer is enabled) and rules with `tagteam rule approve|request-changes --content "…"` — or from the cockpit's Needs-you card (`tagteam serve --theme cockpit`), which runs the same command.
- **Needs-human:** "Paused for human input." The arbiter answers with `tagteam rule answer --to lead|reviewer --content "…"` (the answer arrives as an interjection and the cycle is re-armed for that role). Do not hand-edit cycle files.
- **Aborted:** "Cycle was aborted. See cycle file for reason."
- **Not your turn:** "Waiting for [other agent]. Tell them to run `/tagteam:handoff`."
- **Your turn:** See below.

#### As Lead (your turn)
1. Read the reviewer's latest feedback: `tagteam cycle rounds --phase [phase] --type [plan|impl]` (or `--tail 1` for just the last entry)
2. Address the feedback: update the plan or implementation files
3. Add your round and update state in one command: `tagteam cycle add --phase [phase] --type [plan|impl] --role lead --action SUBMIT_FOR_REVIEW --round [N+1] --updated-by [your-agent-name] --content "summary of changes"`

**Dispatch paused?** If the write prints `note: watcher dispatch is PAUSED (…)`, the turn was recorded but the other agent will **not** be woken: an arbiter `tagteam pause` marker is held (it survives watcher restarts and new cycles — the age and the state it was set on are in the note). Say so in your response; the arbiter releases it with `tagteam resume`. `tagteam cycle status` shows `dispatch:` at any time.

**Verification budget — the one-run rule (impl cycles).** A submission costs **one** full-suite run, the one on the record. Run focused tests while you work; then:
- `gatekeeper.on_submit: true` (and the type is gated): the gate's run *is* the run — your `tagteam cycle add … SUBMIT_FOR_REVIEW` runs it and prints the verdict. Pre-flight with `tagteam gate check --skip-tests` (scope + plan-doc, seconds). Do **not** run the suite yourself before submitting; cite the gate in your submission (`gate: see entry @ <commit>`). `--no-gate` skips only the synchronous run — use it only when a watcher (or `tagteam gate run`) will gate the submission; you still do not run the suite.
- gate off / not on for this type: run the full suite **once**, right before you submit, and cite it (`full suite: N passed, M skipped @ <commit>`).
Never run the suite *and* let the gate run it on the same tree (`gate check` without `--skip-tests` on an `on_submit` project is the one explicit exception and says so). Keep the submission to what the reviewer needs (what changed, where, how it was verified); the reviewer reads the diff, not a narrative.

When `TAGTEAM_STEP_B=1`, `docs/handoffs/<phase>_<type>.md` is auto-rendered on every cycle write. Do not hand-edit that file; update the cycle with `tagteam cycle add` instead. If a write produces no markdown update, check `handoff-diagnostics.jsonl` for an auto-export diagnostic.

**Read-only helpers.** Any process you delegate to while it is your turn — a brief drafter, a verifier subagent, a panel lens — must run with `TAGTEAM_READ_ONLY=1` in its environment. With it set, the CLI refuses every cycle-writing command (`cycle add`/`init`, `state set`, `rule`, `interject`, `pause`/`resume`, `gate run`, …) before anything touches disk, and never creates or migrates the project database; read commands (`cycle rounds`/`status`, `gate status`, `panel status`, `state`, `contract`, …) work as usual. The helper returns text; the turn's one cycle-writing call stays with you. Tagteam sets the variable itself for the children it spawns (panel lenses, the escalation briefer); headless lead/reviewer turns never get it.

**The helper kit (Claude Code plugin, Phase 73).** The plugin ships three cheap, write-less helpers for the lead. Delegate the bulky steps to them and keep the digest instead of the transcript:

| Agent | Use it for |
|---|---|
| `tagteam:test-runner` | one focused test file or test id while you work — never the full suite (the one-run rule stays with you and the gate) |
| `tagteam:verifier` | re-checking one claim before it goes into a submission; it answers CONFIRMED / REFUTED / UNVERIFIABLE with evidence |
| `tagteam:explore` | a search across many files, including earlier rounds in `docs/handoffs/`; it returns pointers, not dumps (no Bash) |

Tagteam sets `TAGTEAM_READ_ONLY=1` for the kit's Bash itself: the plugin's PreToolUse hook rewrites each of their commands to start with `export TAGTEAM_READ_ONLY=1; ` and blocks the command if the tagteam CLI can't guard it. The agents' own instruction to add that prefix is a courtesy, not the enforcement. The guard needs a Claude Code version that reports which agent is calling; `tagteam doctor` says whether yours does. Without that, do not delegate to `test-runner` or `verifier`.

**Mid-review amendment.** If new info arrives (e.g., the human arbiter answers an open question) while the reviewer is still on your submission and you haven't been handed back the turn, run:

```
tagteam cycle add --phase [phase] --type [plan|impl] --role lead --action AMEND --round [N] --updated-by [your-agent-name] --content "<what changed and why>"
```

This appends an amendment to the active round without bumping the round number or returning the turn. The reviewer sees the amendment in the `tagteam cycle rounds` output on their next `/tagteam:handoff`. AMEND only works when the cycle is mid-review (`ready_for: reviewer`) and the `--round` matches the active round; mismatches error.

**Gatekeeper pre-checks (when `gatekeeper.enabled: true` in `tagteam.yaml`).** A deterministic gate runs between your `SUBMIT_FOR_REVIEW` and the reviewer's turn: the project's test command, the implementation-work scope check (an impl submission must contain real changes since the plan was approved) and a plan-doc check. Before you submit, run `tagteam gate check` (`--skip-tests` when `on_submit` is on) — if it fails, fix first; the gate will bounce you otherwise. With `gatekeeper.on_submit: true` the gate runs **inside your `cycle add`/`cycle init` call** (no watcher needed) and the command tells you the outcome: `gate: pass … tell <Reviewer> to run /tagteam:handoff`, or `gate: bounce … the turn is already back with you`. A bounce hands the turn straight back to you as a `GATE_BOUNCE` entry on your round (the failing output is in it; `tagteam gate status` shows the full report) — address it and re-submit with `--round [N+1]` exactly like a REQUEST_CHANGES.

**Reviewer panel (when `panel.enabled: true` in `tagteam.yaml`).** The reviewer's turn on the paneled cycle types may be taken by a panel of 2–3 lens reviews (correctness / scope / verification by default) whose verdicts are merged into ONE reviewer entry — its content starts `PANEL: APPROVE — …` or `PANEL: REQUEST_CHANGES — …` with findings grouped by lens (`## correctness`, `## scope`, …), written as `updated_by: <Reviewer> panel`. Treat it exactly like a reviewer response: address every group, then re-submit with `--round [N+1]`. If a lens failed, the entry says so (`<lens>: lens failed (…)`) — that axis was not assessed. When the panel could not decide (a lens failed and none objected) the ordinary reviewer turn happens instead, so a plain reviewer entry is also possible on a paneled cycle.

#### As Reviewer (your turn)
1. Read the lead's submission: `tagteam cycle rounds --phase [phase] --type [plan|impl] --tail 1` (use `--tail 2`, or no `--tail`, only when earlier rounds matter for this review)
   - If the reviewer panel is enabled for this cycle type, your turn may already have been taken by the panel (a `PANEL:` reviewer entry with `updated_by: <you> panel`) — then there is nothing for you to do until the lead's next submission. You are asked to review yourself only when the panel fell back (a lens failed and none objected) or is not enabled; `tagteam panel status` shows what happened.
   - If the gatekeeper is enabled, the round tail also carries the gate's entry (`role: gatekeeper`, `GATE: PASS | tests ok (…) | scope N paths | plan-doc ok`): the tests already ran and the scope was already checked before your turn — start from those facts. A `GATE_PASS` whose content begins `GATE: checks failed but bounce cap … reached` means the lead hit the bounce cap; the failures are in the entry and the decision is yours (review anyway, request changes, or escalate).
   - **Do not re-run the full suite.** When a gate entry (`GATE: PASS … / checked: HEAD <sha>`) or the submission reports a full-suite result for the submitted commit, take it as fact — the lead is accountable for it, and the suite is expensive. Verify by reading the diff and, at most, running the test files it touches. Re-run the whole suite only if you have a concrete reason to doubt the report (the checked commit differs from HEAD, the numbers do not add up, a focused run fails) — and say why in your entry. If the gate is on but no `GATE:` entry has appeared yet, the gate is still owed (`tagteam gate status`) — wait for it rather than substituting your own run.
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

**Step 4 — CRITICAL: You MUST end every `/tagteam:handoff` response with this exact box:**

```
┌──────────────────────────────────────────────────────────┐
│ NEXT: Tell [agent name] to run:  /tagteam:handoff        │
└──────────────────────────────────────────────────────────┘
```

Replace `[agent name]` with the next agent's name. For completed/escalated/needs-human states, replace with the appropriate next action.

---

## `/tagteam:handoff start [phase]` — Start a New Phase

**Lead only.** Append `impl` to start an implementation review instead of a plan review.

1. Read `tagteam.yaml` to confirm you are the lead
2. Create or verify the phase plan at `docs/phases/[phase].md` (Summary, Scope, Technical Approach, Files, Success Criteria)
3. Create the cycle and update state in one command: `tagteam cycle init --phase [phase] --type [plan|impl] --lead [lead-name] --reviewer [reviewer-name] --updated-by [your-agent-name] --content "summary of initial submission"`
   If it prints `note: watcher dispatch is PAUSED (…)`, the reviewer will not be woken until the arbiter runs `tagteam resume` — say so in your response.
4. Begin your response with the status banner (showing the newly created state).
5. End with the NEXT COMMAND box.

**`/tagteam:handoff start [phase] impl` means implement first.** This command is what you run (or are handed by the watcher after plan approval) when the *plan* cycle is approved and the implementation review must begin. Before step 3:
- Read the approved plan at `docs/phases/[phase].md` and the plan cycle's history (`tagteam cycle rounds --phase [phase] --type plan`).
- Implement the plan in full and run the project's verification (tests) until it passes.
- Only then run `tagteam cycle init --type impl` — **exactly once**, with a submission that summarizes what was implemented. If an impl cycle for this phase already exists, do not create another; act on it with `/tagteam:handoff` instead.
An impl cycle opened over an unchanged tree is a contract violation, not a formality.

---

## `/tagteam:handoff start --roadmap [phase?]` — Start Full-Roadmap Mode

**Lead only.** Runs all remaining roadmap phases end-to-end with review gates.

1. Read `tagteam.yaml` to confirm you are the lead
2. Build the phase queue using the CLI:
   - All incomplete phases: `tagteam roadmap queue`
   - Starting from a specific phase: `tagteam roadmap queue [phase-slug]`
   - This prints a comma-separated list of phase slugs (e.g. `api-gateway,dashboard,ci-integration`). Since 3.4 the roadmap is a DAG: phases may carry `- **Depends on:** …` lines and the queue is a stable topological order (identical to document order when no phase has one). With a start phase, unmet dependency ancestors are pulled in ahead of it (a `note:` on stderr says which). `tagteam roadmap check` explains a refused roadmap; `tagteam roadmap ready` lists what can start now.
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
     --command "Read the handoff contract (\`tagteam contract\`; in Claude Code: /tagteam:handoff) and handoff-state.json, then act on your turn" \
     --updated-by [your-agent-name]
   ```
7. Begin with the status banner. End with the NEXT COMMAND box.

**Lifecycle in full-roadmap mode:**
- Each phase goes through: plan cycle → (lead implements) → impl cycle → (advance)
- After plan approval, watcher sets `turn: lead` — lead implements and runs `/tagteam:handoff start [phase] impl`
- After impl approval, watcher advances to the next **ready** phase (re-reading the roadmap: phases completed elsewhere are skipped, a phase whose dependencies are not yet satisfied is never started) and sets `turn: lead` — lead runs `/tagteam:handoff start [next-phase]`
- If everything left is blocked, the run pauses (`status: escalated`, `roadmap.pause_reason: "blocked: …"`); the arbiter merges the dependency / fixes the roadmap and runs `tagteam roadmap resume`
- After the last phase's impl approval, state is set to `result: "roadmap-complete"`

**`/tagteam:handoff status` in roadmap mode shows:**
```
Phase: phase-name | Type: plan | Round: 2 | Turn: lead | Status: ready
 Mode: full-roadmap | Progress: 3/7 | Next: next-phase-name
```

---

## `/tagteam:handoff status` — Orientation & Reset

For both agents. Re-reads everything and gives a full orientation.

1. Read `tagteam.yaml` → show role assignment
2. Read `handoff-state.json` → show current state
3. Read active cycle via `tagteam cycle status --phase [phase] --type [type]` and `tagteam cycle rounds --phase [phase] --type [type]` → show round, last action, and the `dispatch:` line (`not paused`, or `PAUSED (age, by): reason`)
4. Begin with the status banner: `Phase: [phase] | Type: [plan/impl] | Round: [N] | Turn: [agent] | Status: [state]` and description line
5. Show role assignments and cycle details below the banner
6. End with the NEXT COMMAND box showing the appropriate next action.
