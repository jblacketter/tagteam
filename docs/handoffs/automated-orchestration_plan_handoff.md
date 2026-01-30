# Handoff: Automated Agent Orchestration - Plan Review

**Date:** 2026-01-30
**From:** Claude (Lead)
**To:** Codex (Reviewer)
**Type:** Planning Review

## Summary

This phase adds automated turn-taking between the lead and reviewer agents. Instead of the human manually switching terminals and typing commands after each turn, a lightweight Python watcher daemon monitors a JSON state file and sends commands to the correct tmux pane automatically.

Three components:
1. **State file** (`handoff-state.json`) - machine-readable JSON that tracks whose turn it is and what command to run
2. **Skill integration** - `/handoff-cycle` updated to write the state file at each turn boundary
3. **Watcher daemon** - `python -m ai_handoff watch` polls the state file and triggers agents via tmux `send-keys`

## What Needs Review

- **Architecture**: Is file-based state + polling + tmux send-keys the right approach for zero-token-cost orchestration? Are there simpler alternatives I'm missing?
- **State file schema**: Is the JSON structure in the plan adequate? Too much, too little?
- **Safety mechanisms**: Are the safeguards sufficient (atomic writes, timeouts, confirmation mode, max rounds)?
- **Skill integration**: Is updating only `/handoff-cycle` sufficient for the MVP, or should other skills also write state?
- **tmux dependency**: Is it reasonable to require tmux for full automation? Should we support alternative terminal multiplexers?
- **Scope**: Is this the right scope for one phase, or should it be split?

## Specific Questions for Reviewer

1. The state file lives in project root alongside `ai-handoff.yaml`. Should it instead go in `docs/` or a dedicated `.handoff/` directory?
2. The watcher uses simple time-based polling (default 10s) rather than filesystem event watching (`watchdog` library). Polling is simpler and has no extra dependency. Is this the right tradeoff?
3. Should the watcher have a `--confirm` mode where it pauses for human approval before sending each command? I included it in the plan but it adds complexity.
4. Risk: the agent might not reliably write the state file (AI instructions aren't guaranteed to be followed). Is the mitigation (timeout + fallback to parsing CYCLE_STATUS) sufficient?

## Phase Plan Location
`docs/phases/automated-orchestration.md`

## Review Checklist
- [ ] Technical approach is sound
- [ ] Scope is appropriate (not too big/small)
- [ ] Success criteria are testable
- [ ] No major risks overlooked
- [ ] File structure makes sense
- [ ] Dependencies are identified
- [ ] No unnecessary complexity or over-engineering

## Response Instructions
Please provide feedback in `docs/handoffs/automated-orchestration_plan_feedback.md` using the feedback template.

---
*Handoff created by lead. Reviewer: use `/handoff-review plan automated-orchestration` to begin review.*
