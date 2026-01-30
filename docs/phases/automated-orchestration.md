# Phase: Automated Agent Orchestration

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
**What:** Add a file-based state machine and watcher daemon so agents can automatically take turns without the human manually copy-pasting between terminals.

**Why:** Currently, the human must wait for each agent to finish, switch terminals, and type the next command. This is tedious and time-consuming, especially during multi-round review cycles. Automating the handoff reduces human involvement to observation (or optional approval gates).

**Depends on:** Phase 2 (Review Cycle Automation) - Complete

## Problem Statement

The current workflow for a single plan review cycle:

1. Human tells Claude: `/handoff-cycle start phase-3 plan`
2. Human waits for Claude to finish
3. Human switches to Codex terminal
4. Human tells Codex: `/handoff-cycle phase-3`
5. Human waits for Codex to finish
6. If changes requested: Human switches back to Claude terminal
7. Human tells Claude: `/handoff-cycle phase-3`
8. Repeat steps 2-7 until approved

For a full phase (plan review + implementation + impl review), this means 10-20 manual interventions. Multiply by the number of phases in a project.

## Scope

### In Scope
- Machine-readable state file (`handoff-state.json`) for turn coordination
- Python watcher daemon (`python -m ai_handoff watch`) that monitors state changes
- tmux integration for sending commands to agent terminals automatically
- Updates to `/handoff-cycle` skill to write state file on turn transitions
- CLI commands: `watch`, `state`, `session`

### Out of Scope
- API-based orchestration (no purchased tokens)
- Support for more than 2 agents (future phase)
- Windows support (tmux is Unix-only; user is on macOS)
- Non-interactive `claude --print` mode orchestration (loses session context)
- Automated starting of new phases (human decides when to start phases)

## Technical Approach

### Architecture: Three Components

```
┌─────────────────────────────────────────────────────────────┐
│                    ORCHESTRATION SYSTEM                       │
│                                                               │
│  ┌──────────┐    ┌──────────────────┐    ┌──────────────┐    │
│  │  Skills   │───►│ handoff-state.json│◄───│   Watcher    │    │
│  │ (in agent)│    │  (state file)     │    │  (daemon)    │    │
│  └──────────┘    └──────────────────┘    └──────┬───────┘    │
│                                                  │            │
│                                          ┌───────▼───────┐   │
│                                          │  tmux session  │   │
│                                          │  ┌────┬────┐   │   │
│                                          │  │Lead│Rev.│   │   │
│                                          │  │pane│pane│   │   │
│                                          │  └────┴────┘   │   │
│                                          └───────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**Component 1: State File (`handoff-state.json`)**

The single source of truth for coordination. Lives in project root alongside `ai-handoff.yaml`.

```json
{
  "turn": "reviewer",
  "status": "ready",
  "command": "/handoff-cycle phase-3",
  "phase": "phase-3",
  "type": "plan",
  "round": 1,
  "updated_at": "2026-01-30T10:30:00Z",
  "updated_by": "claude",
  "history": [
    {
      "turn": "lead",
      "status": "working",
      "action": "SUBMIT_FOR_REVIEW",
      "timestamp": "2026-01-30T10:25:00Z"
    }
  ]
}
```

Field definitions:
- `turn`: Which agent should act next (`"lead"` or `"reviewer"`)
- `status`: `"ready"` (waiting for agent to pick up), `"working"` (agent is processing), `"done"` (cycle complete), `"escalated"`, `"aborted"`
- `command`: The exact skill command the next agent should run
- `phase`: Current phase name
- `type`: `"plan"` or `"impl"`
- `round`: Current review round
- `updated_at`: ISO timestamp of last state change
- `updated_by`: Which agent last wrote the file
- `history`: Array of past state transitions (for debugging/audit)

**Component 2: Skill Integration**

The `/handoff-cycle` skill instructions are updated so that agents write `handoff-state.json` at each turn boundary. This is the key integration point -- the agent writes the state file as the last step of each turn.

Turn transitions in the cycle skill:
- Lead starts cycle → `{turn: "reviewer", status: "ready", command: "/handoff-cycle <phase>"}`
- Reviewer picks up → `{turn: "reviewer", status: "working"}` (briefly)
- Reviewer approves → `{status: "done"}`
- Reviewer requests changes → `{turn: "lead", status: "ready", command: "/handoff-cycle <phase>"}`
- Lead addresses feedback → `{turn: "reviewer", status: "ready", command: "/handoff-cycle <phase>"}`
- Escalation → `{status: "escalated"}`

The state file update uses atomic writes (write to `.handoff-state.tmp`, then rename) to prevent partial reads.

**Component 3: Watcher Daemon (`python -m ai_handoff watch`)**

A Python process that polls `handoff-state.json` and acts on state transitions.

```
python -m ai_handoff watch [--interval 10] [--mode notify|tmux] [--lead-pane NAME] [--reviewer-pane NAME]
```

Modes:
- **`notify`** (default): Prints state changes to terminal with timestamps. Sends macOS desktop notification via `osascript`. Human reads notification and types command.
- **`tmux`**: Automatically sends the command to the correct tmux pane via `tmux send-keys`. Fully hands-free.

Watcher behavior:
1. Read `ai-handoff.yaml` to get agent names
2. Poll `handoff-state.json` every N seconds (default: 10)
3. On state change:
   - If `status` changed to `"ready"`: the agent identified by `turn` needs to act
     - `notify` mode: print message + desktop notification
     - `tmux` mode: `tmux send-keys -t <pane-name> "<command>" Enter`
   - If `status` changed to `"done"`: print completion message
   - If `status` changed to `"escalated"`: print escalation alert
4. Log all transitions with timestamps

Example watcher output:
```
[10:30:00] Watching handoff-state.json (interval: 10s, mode: tmux)
[10:30:00] State: lead is working on phase-3 plan review
[10:32:15] → Turn change: reviewer's turn (round 1)
[10:32:15] → Sending to reviewer pane: /handoff-cycle phase-3
[10:34:42] → Turn change: lead's turn (round 2, changes requested)
[10:34:42] → Sending to lead pane: /handoff-cycle phase-3
[10:36:10] → Cycle complete: APPROVED
```

### CLI Commands

| Command | Description |
|---------|-------------|
| `python -m ai_handoff watch` | Start the watcher daemon |
| `python -m ai_handoff state` | View current state (one-shot) |
| `python -m ai_handoff state reset` | Clear the state file |
| `python -m ai_handoff session start` | Create tmux session with named panes |

### tmux Session Layout

`python -m ai_handoff session start` creates:

```
┌─────────────────────────────────────────────────┐
│                  ai-handoff                      │
├────────────────────┬────────────────────────────┤
│                    │                             │
│    lead-pane       │    reviewer-pane            │
│    (Claude Code)   │    (Codex)                  │
│                    │                             │
│                    │                             │
├────────────────────┴────────────────────────────┤
│    watcher-pane                                  │
│    (python -m ai_handoff watch --mode tmux)      │
└─────────────────────────────────────────────────┘
```

The user can also skip `session start` and manually arrange terminals. The `--lead-pane` and `--reviewer-pane` flags let the watcher target existing tmux panes by name.

### Safety Mechanisms

1. **Human approval gate (optional)**: `python -m ai_handoff watch --confirm` pauses before sending each command, requiring the human to press Enter. Good for first-time use.

2. **Max rounds**: Inherits from `/handoff-cycle` (auto-escalate at round 5).

3. **Timeout**: If no state change for N minutes (configurable), watcher alerts the human that an agent may be stuck.

4. **State file locking**: Atomic writes prevent corruption. Agents write to `.handoff-state.tmp` then rename.

5. **Manual override**: Human can edit `handoff-state.json` directly to reset, skip, or redirect the workflow. `python -m ai_handoff state reset` clears it.

### Why This Approach Over Alternatives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **Keywords in markdown** | Simple | Fragile parsing, mixes concerns | Rejected |
| **API-based orchestration** | Most flexible | Costs tokens, different architecture | Rejected (user constraint) |
| **MCP server as coordinator** | Real-time, bidirectional | Over-engineered for file-based workflow, complex setup | Rejected |
| **Agent-side polling (agent runs `while` loop)** | No daemon needed | Agents can't natively poll; wastes context window | Rejected |
| **File state + watcher daemon** | Simple, reliable, no token cost, works with any CLI agent | Requires tmux for full automation | **Selected** |
| **Git-based coordination** | Version controlled | Slow (commit/push/pull cycle), overkill | Rejected |

### Integration with Existing Skills

Only `/handoff-cycle` needs modification in this phase. The change is additive -- at the end of each turn's instructions, add: "Write the current state to `handoff-state.json`."

Other skills (`/handoff-handoff`, `/handoff-review`, etc.) can be integrated in a future phase if we want to automate the non-cycle workflow too. For now, the cycle workflow is the primary target since it already has turn tracking.

## Files to Create/Modify

### New Files
- `ai_handoff/state.py` - State file read/write module (atomic writes, schema validation)
- `ai_handoff/watcher.py` - Watcher daemon (polling loop, notification, tmux integration)
- `ai_handoff/session.py` - tmux session management (create, attach, send-keys)

### Modified Files
- `ai_handoff/cli.py` - Add `watch`, `state`, `session` subcommands
- `.claude/skills/handoff-cycle.md` - Add state file write instructions at each turn transition
- `ai_handoff/data/.claude/skills/handoff-cycle.md` - Same update in packaged data
- `docs/workflows.md` - Add automated orchestration workflow section
- `docs/roadmap.md` - Add this phase

## Success Criteria
- [ ] `handoff-state.json` is written automatically when `/handoff-cycle` transitions turns
- [ ] `python -m ai_handoff watch --mode notify` correctly detects and announces turn changes
- [ ] `python -m ai_handoff watch --mode tmux` sends commands to correct tmux panes
- [ ] `python -m ai_handoff state` displays current state in human-readable format
- [ ] `python -m ai_handoff session start` creates working tmux layout
- [ ] Full cycle test: lead starts cycle → watcher triggers reviewer → reviewer responds → watcher triggers lead → approved (zero manual intervention except initial start)
- [ ] Atomic writes prevent state file corruption
- [ ] Watcher handles edge cases: state file missing, agent stuck, cycle complete

## Open Questions
1. Should the watcher support a `--dry-run` mode that shows what it would do without sending keys?
2. Should the state file live in `docs/` (with other handoff artifacts) or project root (next to `ai-handoff.yaml`)? Root seems cleaner for machine-readable coordination.
3. For the packaged version (`pip install ai-handoff`), should `watchdog` be an optional dependency, or is polling sufficient? Polling at 10s intervals is plenty responsive for this use case.
4. Should we add a `/handoff-auto` skill that agents can use to explicitly write state? Or is it better to just have the cycle skill do it implicitly?

## Risks
- **Agent doesn't write state file**: If the agent forgets or the skill instructions aren't followed precisely, the watcher hangs. *Mitigation*: Timeout alerts. Could also add a backup mechanism that parses the cycle document's CYCLE_STATUS block as a fallback.
- **tmux pane naming**: If panes aren't named correctly, send-keys goes to wrong place. *Mitigation*: Validate pane names on watcher startup.
- **Session context**: Long-running sessions may hit context limits. *Mitigation*: This is inherent to the agents, not our orchestration. `/handoff-sync` already exists for context recovery.
- **Claude Code / Codex compatibility**: Different CLI tools may handle skill invocation differently. *Mitigation*: Test with both. The command format (`/handoff-cycle <phase>`) is plain text input that both should handle.

## Revision History
| Version | Date | Changes |
|---------|------|---------|
| v1 | 2026-01-30 | Initial plan |
