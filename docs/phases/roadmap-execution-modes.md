# Phase: Roadmap Execution Modes

## Status
- [x] Planning
- [x] In Review
- [x] Approved
- [x] Implementation
- [ ] Implementation Review
- [ ] Complete

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary
**What:** Add two execution modes to the handoff workflow:
1. `single-phase` (current behavior)
2. `full-roadmap` (run all remaining roadmap phases in order)

**Why:** When the roadmap is stable, users want to run end-to-end with minimal manual handoff overhead while keeping review quality gates between phases.

**Depends on:** Unified command + orchestration watcher (`/handoff`, `handoff-state.json`, `watch`)

## Command Surface

Exact commands and accepted forms:

| Command | Mode | Behavior |
|---------|------|----------|
| `/handoff start [phase]` | single-phase | Existing behavior, unchanged |
| `/handoff start --roadmap` | full-roadmap | Parse `docs/roadmap.md`, queue all incomplete phases, start at first |
| `/handoff start --roadmap [phase]` | full-roadmap | Same, but start at the named phase (skip earlier incomplete phases) |
| `/handoff` | (any) | Unchanged — auto-detects role + state, acts on turn |
| `/handoff status` | (any) | Shows mode, current phase, queue position, next phase (see below) |

**CLI equivalent for `--roadmap`:**
```
python -m ai_handoff state set --turn reviewer --status ready \
  --phase [first-phase] --type plan --round 1 \
  --run-mode full-roadmap --updated-by Claude
```
(The `--run-mode` flag is a new addition to `state set`.)

**`/handoff status` output in roadmap mode:**
```
Phase: phase-name | Type: plan | Round: 2 | Turn: lead | Status: ready
 Mode: full-roadmap | Progress: 3/7 | Next: next-phase-name
```

## Scope

### In Scope
- Add a user-selectable execution mode when starting work:
  - Keep `/handoff start [phase]` for single-phase mode
  - Add `/handoff start --roadmap [phase?]` for full-roadmap mode
- Implement roadmap queue resolution from `docs/roadmap.md` (remaining/incomplete phases only)
- Persist roadmap run metadata in state so watcher + agents can resume after restarts
- Preserve existing per-phase review gates:
  - plan review cycle must be approved before implementation cycle
  - implementation cycle must be approved before advancing to next roadmap phase
- Auto-advance to next roadmap phase in full-roadmap mode
- Pause and prompt human only on:
  - unresolved open questions
  - permission/approval requests
  - escalations
  - hard failures that block continuation
- Add status output that clearly shows execution mode, current phase, and next phase
- No feature flag; this ships as a first-class mode

### Out of Scope
- Parallel phase execution
- Dependency graph scheduling beyond roadmap order
- Automatic git push/release/deploy steps
- Replacing existing single-phase behavior

## Technical Approach

### 1. State Schema

**New top-level fields** (additive — existing fields unchanged):

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `run_mode` | `"single-phase"` \| `"full-roadmap"` | `"single-phase"` | Execution mode |
| `roadmap` | object \| `null` | `null` | Roadmap progress (only set in full-roadmap mode) |

**`roadmap` object schema** (when `run_mode == "full-roadmap"`):

| Field | Type | Description |
|-------|------|-------------|
| `queue` | `string[]` | Ordered list of phase slugs to execute |
| `current_index` | `int` | 0-based index into `queue` for the active phase |
| `completed` | `string[]` | Phase slugs that have finished (impl approved) |
| `pause_reason` | `string \| null` | Structured reason when paused for human (see Human-Gate) |

**Example state in full-roadmap mode:**
```json
{
  "turn": "reviewer",
  "status": "ready",
  "phase": "roadmap-execution-modes",
  "type": "plan",
  "round": 1,
  "run_mode": "full-roadmap",
  "roadmap": {
    "queue": ["roadmap-execution-modes", "dashboard-v2", "ci-integration"],
    "current_index": 0,
    "completed": [],
    "pause_reason": null
  }
}
```

**CLI additions to `state set`:**
- `--run-mode` (`single-phase` | `full-roadmap`)
- `--roadmap-queue` (comma-separated phase slugs)
- `--roadmap-index` (integer)
- `--roadmap-completed` (comma-separated phase slugs)
- `--roadmap-pause-reason` (string or empty to clear)

### 2. State Transition Table

| Trigger | Precondition | State changes |
|---------|-------------|---------------|
| **Roadmap start** | `run_mode` unset or `single-phase` | `run_mode → "full-roadmap"`, `roadmap.queue → [parsed phases]`, `roadmap.current_index → 0` (or index of named start phase), `phase → queue[0]`, `type → "plan"`, `round → 1`, `turn → "reviewer"`, `status → "ready"` |
| **Plan approved** | `status == "done"`, `result == "approved"`, `type == "plan"` | `turn → "lead"`, `status → "ready"`, `result → null` (lead implements, then runs `/handoff start [phase] impl`) |
| **Impl approved (more phases)** | `status == "done"`, `result == "approved"`, `type == "impl"`, `roadmap.current_index < len(queue) - 1` | `roadmap.completed.append(phase)`, `roadmap.current_index += 1`, `phase → queue[new_index]`, `type → "plan"`, `round → 1`, `turn → "lead"`, `status → "ready"`, `result → null` (lead creates plan, then runs `/handoff start [next-phase]`) |
| **Impl approved (last phase)** | `status == "done"`, `result == "approved"`, `type == "impl"`, `roadmap.current_index == len(queue) - 1` | `roadmap.completed.append(phase)`, `status → "done"`, `result → "roadmap-complete"` |
| **Human-gate pause** | Any active state | `status → "escalated"`, `roadmap.pause_reason → "<category>: <description>"` |
| **Human resume** | `status == "escalated"`, `roadmap.pause_reason != null` | `status → "ready"`, `roadmap.pause_reason → null`, `turn → (as set by human)` |

### 3. Roadmap Parsing

- Parser module: `ai_handoff/roadmap.py`
- Input: `docs/roadmap.md`
- Extracts `### Phase N: <name>` headings and `- **Status:** <status>` lines
- Returns ordered list of `{slug: str, name: str, status: str}`
- Filters to phases where `status != "Complete"` for the queue

**Error behavior for malformed/missing data:**

| Condition | Behavior |
|-----------|----------|
| `docs/roadmap.md` missing | Fail fast: exit with error `"docs/roadmap.md not found. Create it before using --roadmap mode."` |
| No `### Phase` headings found | Fail fast: exit with error `"No phases found in docs/roadmap.md. Expected '### Phase N: <name>' headings."` |
| Heading exists but no `**Status:**` line | Treat as incomplete (queue it) — this is the safe default |
| All phases already `Complete` | Fail fast: exit with message `"All roadmap phases are complete. Nothing to run."` |
| Named start phase not found in roadmap | Fail fast: exit with error `"Phase '<name>' not found in docs/roadmap.md."` |
| Phase in queue has no `docs/phases/<slug>.md` | **Do not fail.** The plan cycle is where lead creates that file. Proceed normally — lead will create the plan doc as part of `/handoff start`. |

### 4. Human-Gate Policy

Reuse existing `status: "escalated"` with a structured `reason` field (already accepted by `state set --reason`).

**Reason categories** (prefix convention for deterministic watcher behavior):

| Prefix | Trigger | Watcher action |
|--------|---------|----------------|
| `needs-human: <desc>` | Cycle sets `STATE: needs-human` | Stop sending commands, print reason |
| `needs-permission: <desc>` | Agent requests explicit human approval | Stop sending commands, print reason |
| `dispute: <desc>` | Round 5 auto-escalation | Stop sending commands, print reason |
| `error: <desc>` | Unrecoverable execution failure | Stop sending commands, print reason + suggest `state set` to resume |

**Watcher logic:** When `status == "escalated"`, check `roadmap.pause_reason` (or `reason` field). Do NOT send any commands. Print the reason and suggested resolution to the watcher pane. Resume only when state is manually set back to `status: "ready"`.

This reuses the existing `escalated` status and `--reason` flag — no new status values needed.

### 5. Lifecycle Rules
- `single-phase` mode: keep current lifecycle unchanged. `run_mode` defaults to `"single-phase"` (or is absent — treat missing as single-phase).
- `full-roadmap` mode:
  - start at first incomplete phase (or user-selected starting point if provided)
  - run plan cycle → impl cycle for that phase
  - on impl approval, watcher automatically advances: creates plan cycle for next phase
  - finish run when queue is exhausted (`result: "roadmap-complete"`)

### 6. Backward Compatibility
- Existing commands and automation continue to work for single-phase users.
- Missing `run_mode` field treated as `"single-phase"` — no migration needed.
- Existing cycle doc format remains unchanged.
- New roadmap mode metadata is additive in state.

## Files to Create/Modify
- `ai_handoff/state.py` - extend state schema and CLI fields for roadmap mode metadata
- `ai_handoff/watcher.py` - support roadmap run progression and human-gate pauses
- `ai_handoff/parser.py` or new `ai_handoff/roadmap.py` - parse roadmap phases/status
- `.claude/skills/handoff/SKILL.md` - document mode selection and roadmap flow
- `ai_handoff/data/.claude/skills/handoff/SKILL.md` - packaged skill sync
- `README.md` - user-facing docs for full-roadmap mode
- `docs/workflows.md` - workflow documentation updates
- `ai_handoff/data/workflows.md` - packaged workflow sync
- `tests/` - unit tests for roadmap parsing/state transitions/watcher behavior

## Success Criteria
- [ ] User can choose `single-phase` or `full-roadmap` when starting work
- [ ] `single-phase` behavior remains unchanged
- [ ] Full-roadmap mode resolves a valid ordered phase queue from `docs/roadmap.md`
- [ ] Full-roadmap mode auto-advances phase-to-phase after impl approval
- [ ] Reviewer/lead approval gates still enforced for every phase
- [ ] Workflow pauses and prompts human only for open questions/permissions/escalations/blockers
- [ ] Watcher can resume an in-progress roadmap run after restart without losing position
- [ ] `/handoff status` clearly reports mode + current/next roadmap phase
- [ ] New tests cover parser, state transitions, and pause/advance logic

## Open Questions
All resolved in Round 2:
- ~~Should roadmap mode default to first incomplete phase automatically, or require explicit confirmation of the starting phase?~~ **Resolved:** Default to first incomplete. Optional `[phase]` arg to `/handoff start --roadmap [phase]` to override.
- ~~Should "permission needed" be represented as a dedicated status value or as `escalated` with a structured reason?~~ **Resolved:** Reuse `status: "escalated"` with prefix-categorized `reason` field (`needs-human:`, `needs-permission:`, `dispute:`, `error:`). No new statuses.
- ~~Should roadmap mode allow skipping a blocked phase and continuing, or always stop until human resolves?~~ **Resolved:** Always stop. Human must manually resume via `state set`. Skipping phases is out of scope for v1.

## Risks
- **Roadmap format drift:** Parsing may break on non-standard roadmap edits.
  - **Mitigation:** strict parser + actionable validation errors + test fixtures.
- **State complexity growth:** More metadata increases chance of inconsistent transitions.
  - **Mitigation:** centralized transition helpers + invariant checks + tests.
- **Over-automation surprises:** Users may not expect phase auto-advance.
  - **Mitigation:** explicit mode banner and clear status output showing next action.
