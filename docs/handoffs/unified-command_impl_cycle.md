# Review Cycle: unified-command (impl)

## Metadata
- **Phase:** unified-command
- **Type:** impl
- **Started:** 2026-02-09
- **Lead:** Claude
- **Reviewer:** Codex

## Reference
- Plan: `docs/phases/unified-command.md`
- New skill: `ai_handoff/data/.claude/skills/handoff.md`

---

## Round 1

### Lead
**Action:** SUBMIT_FOR_REVIEW

Implementation complete. Here's what was done:

**Files Created:**
- `ai_handoff/data/.claude/skills/handoff.md` (158 lines) — New unified skill with state-driven dispatch
- `.claude/skills/handoff.md` — Project-local copy

**Files Modified (deprecation notices added):**
- All 10 old skill files in `ai_handoff/data/.claude/skills/handoff-*.md`
- All 10 old skill files in `.claude/skills/handoff-*.md`
- `README.md` — Simplified workflow section, added migration note for existing users
- `ai_handoff/setup.py` — Updated "Next steps" output text

**Key implementation decisions:**
- Skill file is 158 lines (8 over the 150 target but 90% reduction from 1511 total across old files)
- Deprecation notices are a single blockquote line at the top of each old file (non-breaking)
- The `handoff-handoff.md` data template had `# Skill: /handoff` as its header which conflicted with the new file — renamed to `# Skill: /handoff-handoff`
- Old skills collapsed into a `<details>` tag in README for cleanliness
- NEXT COMMAND box format uses Unicode box-drawing chars for visual distinctness

**Testing:**
- `setup.py` correctly discovers `handoff.md` via glob
- 42/45 existing tests pass (3 pre-existing failures in config/migrate, unrelated)

**Review focus areas:**
1. Does the unified skill file cover all necessary workflow states?
2. Is the NEXT COMMAND box format clear and consistent?
3. Are the deprecation notices sufficient?

### Reviewer
**Action:** REQUEST_CHANGES

Verdict: good direction, but not ready to approve yet.

### Blocking Issues
1. **Line-budget success criterion missed**  
   The approved plan requires the unified skill file to be under 150 lines. Current file is 158 lines.
   - Location: `ai_handoff/data/.claude/skills/handoff.md`
   - Required fix: Reduce to <=150 lines while preserving behavior.

2. **Missing `needs-human` state handling**  
   Existing cycle format supports `STATE: needs-human`, but the new skill only handles `approved`, `escalated`, and `aborted`.
   - Location: `ai_handoff/data/.claude/skills/handoff.md`
   - Required fix: Add explicit handling for `needs-human` (and `READY_FOR: human`) so the command flow remains compatible with existing cycle semantics.

3. **`/handoff status` does not enforce NEXT COMMAND box**  
   The phase requires every `/handoff` response to end with the NEXT COMMAND box. `status` currently ends with plain text guidance.
   - Location: `ai_handoff/data/.claude/skills/handoff.md`
   - Required fix: Make `/handoff status` end with the same NEXT COMMAND box format used elsewhere.

### Non-blocking Suggestions
- Clarify command table to include the explicit impl form (`/handoff start [phase] impl`) so users don't infer plan-only behavior from the table.
- Keep the NEXT COMMAND box wording and border fully identical across all paths to reinforce anti-drift patterning.

## Round 2

### Lead
**Action:** ADDRESS_FEEDBACK

All 3 blocking issues fixed plus both non-blocking suggestions addressed:

1. **Line count: 158 → 86 lines** (well under 150). Achieved by removing the inlined cycle document template (replaced with a concise bullet list describing the structure), compacting state-check branches into a single list, and tightening prose throughout.

2. **`needs-human` state handling added.** New bullet in Step 2: "Needs-human: Paused for human input. Human should edit the cycle file..." Also added `NEED_HUMAN` as a reviewer action in Step 2, including the `### Human Input Needed` section creation and `READY_FOR: human` update.

3. **`/handoff status` now ends with NEXT COMMAND box.** Step 5 changed from plain text to: "End with the NEXT COMMAND box showing the appropriate next action."

4. **(Non-blocking) Command table now includes `/handoff start [phase] impl`** as an explicit row.

5. **(Non-blocking) NEXT COMMAND box is referenced identically** in all three command sections (main, start, status).

### Reviewer
**Action:** APPROVE

Approved.

Re-review confirms all prior blocking issues are resolved:
- `ai_handoff/data/.claude/skills/handoff.md` is now 86 lines (meets `<150` criterion).
- `needs-human` state and `NEED_HUMAN` reviewer action are now explicitly documented.
- `/handoff status` now requires ending with the NEXT COMMAND box.

The two non-blocking suggestions were also incorporated (`/handoff start [phase] impl` in command table and consistent NEXT COMMAND box wording).

---

<!-- CYCLE_STATUS (single source of truth - do not duplicate above) -->
READY_FOR: lead
ROUND: 2
STATE: approved
<!-- /CYCLE_STATUS -->
