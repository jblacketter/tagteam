# Phase: Orchestration Fix

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
**What:** Debug and fix the watcher daemon + tmux `send-keys` integration so agents automatically pick up tasks when it's their turn — no manual copy-paste.

**Why:** The automated orchestration feature (`python -m ai_handoff watch --mode tmux`) was built in Phase 3 but never worked reliably. The user reports "a lot of issues with typing into the tmux sessions." This is the highest-impact feature to fix because it eliminates the human bottleneck of copying commands between terminals.

**Depends on:** Phase 7 (Unified Command) — the simplified `/handoff` command makes orchestration simpler since only one command needs to be sent.

## Problem Statement

The `watcher.py` polls `handoff-state.json` and sends commands to tmux panes via `send-keys`. Known/suspected issues:

1. **tmux `send-keys` to CLI agents** — Claude Code and Codex have interactive TUI-style interfaces. `send-keys` types characters into the active pane, but the agent CLI may not be in a state to accept a typed command (e.g., mid-output, in a prompt, not at the command input).

2. **Pane targeting** — Session creates panes 0 (lead), 1 (watcher), 2 (reviewer). The watcher defaults to `ai-handoff:0.0` and `ai-handoff:0.2`. If pane indices shift (e.g., user rearranges), commands go to the wrong pane.

3. **Timing** — The watcher sends a command immediately when state changes to `ready`. But the target agent may still be processing its previous response. There's no "ready to receive" signal.

4. **First-state skip** — The watcher skips any state that existed before it started (`updated_dt <= watcher_start`). If the watcher restarts mid-cycle, it may miss the current turn.

5. **No feedback loop** — After sending keys, the watcher has no way to know if the command was received and executed. If it fails silently, the cycle stalls.

## Scope

### In Scope
- Debug tmux `send-keys` with real Claude Code and Codex sessions
- Fix watcher to reliably send commands to agent CLIs
- Fix session.py if pane layout/targeting is wrong
- Add retry/verification for send-keys failures
- User-reported issues (to be added during debugging)

### Out of Scope
- Non-tmux orchestration (e.g., API-based agent triggering)
- TUI/Saloon integration (Phase 9)
- Windows support (tmux is macOS/Linux only)

## Technical Approach

### Phase 1: Reproduce and diagnose (with user)
1. Start a tmux session: `python -m ai_handoff session start`
2. Start agents in panes 0 and 2
3. Start watcher in pane 1: `python -m ai_handoff watch --mode tmux`
4. Trigger a state change and observe what happens
5. Document specific failure modes

### Phase 2: Fix identified issues
Based on known code analysis, likely fixes include:

- **Agent CLI readiness:** May need to detect when the agent CLI is at its input prompt before sending keys. Could use tmux `capture-pane` to check pane content.
- **Send-keys reliability:** May need to add a delay, use `send-keys -l` (literal), or break the command into smaller chunks.
- **Pane verification:** Add a check that the target pane exists and is the right process before sending.
- **Retry logic:** If send-keys fails, retry with backoff instead of just logging.
- **Watcher restart resilience:** On startup, process the current state even if it predates the watcher start time.

### Phase 3: Validate end-to-end
1. Run a full plan review cycle automatically
2. Verify watcher sends `/handoff` to the right pane at the right time
3. Verify agent processes the command and updates state
4. Verify the cycle completes without manual intervention

## Files to Create/Modify
- `ai_handoff/watcher.py` — Fix send-keys logic, add retry/verification
- `ai_handoff/session.py` — Fix pane layout/targeting if needed
- `ai_handoff/state.py` — Only if state format changes needed

## Success Criteria
- [x] Watcher successfully sends command to the correct tmux pane
- [x] Agent CLI receives and executes the command
- [x] A full plan review cycle completes (Claude creates plan → watcher relays → Codex reviews and approves)
- [x] Watcher handles agent-not-ready gracefully (idle detection via capture-pane, waits up to 5m)
- [x] Watcher restart mid-cycle picks up the current state

## Open Questions
- What specific failures does the user observe? (To be filled in during debugging)
- Do Claude Code and Codex both accept typed input the same way via tmux send-keys?
- Is there a "ready" signal we can detect from the agent CLI (e.g., prompt character)?

## Risks
- **Agent CLI compatibility:** Different agent CLIs may handle tmux input differently. This may require agent-specific workarounds.
- **Race conditions:** Timing between state changes and agent readiness is inherently tricky.
