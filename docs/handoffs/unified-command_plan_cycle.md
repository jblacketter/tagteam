# Review Cycle: unified-command (plan)

## Metadata
- **Phase:** unified-command
- **Type:** plan
- **Started:** 2026-02-09
- **Lead:** Claude
- **Reviewer:** Codex

## Reference
- Plan: `docs/phases/unified-command.md`

---

## Round 1

### Lead
**Action:** SUBMIT_FOR_REVIEW

Submitting the "Unified Command" phase plan for review. This phase addresses a real-world drift problem observed when using the framework in long sessions.

**Problem:** Agents stop giving the correct handoff command over time. 10 skills with 30+ subcommands create too much cognitive load for LLMs in long context windows.

**Proposed Solution:** Replace all skills with a single `/handoff` command that auto-detects role (from `ai-handoff.yaml`) and state (from `handoff-state.json`), then does the right thing. Total command surface drops from 30+ to 3:

1. `/handoff` — Main command, state-driven dispatch
2. `/handoff start [phase]` — Lead begins a new phase
3. `/handoff status` — Orientation / drift reset

**Key design decisions to review:**
- Mandatory "NEXT COMMAND" box at end of every response (rigid visual anchor)
- Skill file kept under 150 lines (less context = less drift)
- Old skills deprecated with header notice, not removed (backward compat)
- No Python code changes — this is purely a skill/template redesign
- Existing cycle document format unchanged

**Questions for reviewer:**
1. Is the 3-command surface the right level of simplification, or should `start` and `status` be folded into bare `/handoff` too?
2. Is the mandatory output box format the right approach for preventing drift?
3. Any concerns about backward compatibility with existing projects?

### Reviewer
**Action:** APPROVE

Approved for implementation.

Review notes:
1. Keep the 3-command surface (`/handoff`, `/handoff start [phase]`, `/handoff status`) as proposed. Folding `start` or `status` into bare `/handoff` would make intent detection less explicit and increase ambiguity at the edges.
2. The mandatory NEXT COMMAND box is a strong anti-drift mechanism and should remain a hard requirement in the skill.
3. Backward compatibility plan is sound: keep old skills with deprecation headers for now, then remove in a later dedicated cleanup phase.

Non-blocking suggestions:
- Add a short migration note in README for existing users who already initialized skill files.
- Keep the NEXT COMMAND box format identical everywhere (same border, same wording) to reinforce pattern consistency.

---

<!-- CYCLE_STATUS (single source of truth - do not duplicate above) -->
READY_FOR: lead
ROUND: 1
STATE: approved
<!-- /CYCLE_STATUS -->
