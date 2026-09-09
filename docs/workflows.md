# Workflows: Lead/Reviewer Collaboration

This document describes how the lead and reviewer agents collaborate on a
project using tagteam. It is installed by `tagteam setup` and refreshed by
`tagteam upgrade`; the authoritative, versioned contract the agents follow is
the handoff skill.

> **Note**: Agent names are configured in `tagteam.yaml`. Read that file to see
> which agent is the lead and which is the reviewer for your project.

## Roles

| Role | Responsibilities |
|------|------------------|
| **Lead** | Plans phases, implements code, submits work for review |
| **Reviewer** | Reviews plans and implementations, approves or requests changes |
| **Arbiter** (human) | Rules on escalations, answers questions, steers with interjections |

## One contract, three ways in

The handoff contract is one document, served three ways:

| How | Who | Command |
|-----|-----|---------|
| Claude Code plugin (`tagteam`) | Claude | `/tagteam:handoff` |
| Vendored copy at `.claude/skills/handoff/SKILL.md` | any project that still carries one | `/handoff` |
| The tagteam CLI | Codex, or any agent with a shell | `tagteam contract` |

Either slash command follows the same rules. The state file's command line tells
an agent which to use: *"Read the handoff contract (`tagteam contract`; in
Claude Code: /tagteam:handoff) and handoff-state.json, then act on your turn."*

## The cycle

Each phase in `docs/roadmap.md` goes through a **plan** cycle and then an
**impl** cycle. Every cycle is a sequence of rounds recorded under
`docs/handoffs/`, and `handoff-state.json` says whose turn it is.

```
Lead:      /tagteam:handoff start [phase]          → plan cycle, round 1
Reviewer:  /tagteam:handoff                        → APPROVE or REQUEST_CHANGES
Lead:      /tagteam:handoff                        → address feedback, round N+1
   … until APPROVE …
Lead:      implement, then
           /tagteam:handoff start [phase] impl     → impl cycle, round 1
Reviewer:  /tagteam:handoff                        → review the diff
   … until APPROVE …
```

Every turn makes exactly **one** cycle-writing call:

| Action | Who | Command |
|--------|-----|---------|
| Open a cycle | Lead | `tagteam cycle init --phase P --type plan\|impl --lead L --reviewer R --updated-by L --content "…"` |
| Submit a round | Lead | `tagteam cycle add --phase P --type T --role lead --action SUBMIT_FOR_REVIEW --round N --updated-by L --content "…"` |
| Amend mid-review | Lead | `… --action AMEND --round N …` (same round, turn stays with the reviewer) |
| Approve | Reviewer | `… --role reviewer --action APPROVE --round N …` |
| Request changes | Reviewer | `… --role reviewer --action REQUEST_CHANGES --round N` (feedback on stdin) |
| Escalate / ask a human | Reviewer | `… --action ESCALATE` / `… --action NEED_HUMAN` |
| Read the cycle | Both | `tagteam cycle rounds --phase P --type T [--tail N]` |

A cycle ends when the reviewer approves. It escalates to the arbiter when the
reviewer asks for it, asks a question only a human can answer, or after 10
consecutive stale rounds. The arbiter reads `tagteam brief` and rules with
`tagteam rule approve|request-changes|answer`.

## Verification: the one-run rule

An impl submission costs **one** full-suite run — the one on the record. With
`gatekeeper.on_submit: true` in `tagteam.yaml`, the gate runs it inside the
lead's `cycle add` and records the verdict; the lead pre-flights with
`tagteam gate check --skip-tests` and cites the gate. Without the gate, the lead
runs the suite once right before submitting and cites the numbers. The
reviewer reads the diff and does not re-run the suite.

## What runs by itself

| Mode | What happens |
|------|--------------|
| Manual | You paste each agent's handoff output into the other agent yourself |
| `tagteam watch --mode notify\|tmux\|iterm2\|terminal` | The watcher reads `handoff-state.json` and nudges the right terminal on each turn |
| `tagteam watch --mode headless` | Each turn is a fresh agent process (`claude -p` / `codex exec`) fed the contract, the state and the round tail; nobody is at the terminal |
| Gatekeeper (`gatekeeper:` block) | Tests + scope + plan-doc checks before every reviewer turn; a failure bounces the lead |
| Reviewer panel (`panel:` block) | 2–3 narrow lens reviews merged into one reviewer entry |
| Full roadmap (`/tagteam:handoff start --roadmap`) | All incomplete phases, in dependency order, with review gates between them |

## Steering

- `tagteam interject "note" [--to lead|reviewer]` — a note the next turn must honor.
- `tagteam pause --reason "…"` / `tagteam resume` — hold dispatch without losing state.
- `tagteam cancel-turn` — abandon an in-flight headless turn.
- `tagteam serve` — the cockpit: talk to the lead, launch, watch, rule.

## Files tagteam writes

| Path | What |
|------|------|
| `handoff-state.json` | Whose turn, what command, current phase/type/round |
| `docs/handoffs/<phase>_<type>_rounds.jsonl` + `_status.json` | The cycle record |
| `docs/phases/<phase>.md` | The plan (the lead writes it; the reviewer reads it) |
| `docs/roadmap.md` | The phase list and each phase's status |
| `docs/escalations/` | Decision briefs for escalated cycles |
| `.tagteam/` | Watcher and headless runtime state (not for editing) |
