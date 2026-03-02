# Review Cycle: roadmap-execution-modes (plan)

## Metadata
- **Phase:** roadmap-execution-modes
- **Type:** plan
- **Started:** 2026-03-02
- **Lead:** Claude
- **Reviewer:** Codex

## Reference
- Plan: `docs/phases/roadmap-execution-modes.md`

---

## Round 1

### Lead
**Action:** SUBMIT_FOR_REVIEW

Submitting the roadmap execution modes plan for review.

Plan summary:
- Add two start modes:
  - `single-phase` (existing behavior)
  - `full-roadmap` (runs remaining roadmap phases end-to-end)
- Keep current review quality gates unchanged:
  - plan cycle approval required before implementation cycle
  - implementation approval required before moving to next phase
- In full-roadmap mode, auto-advance phase-by-phase using `docs/roadmap.md` order.
- Pause and involve human only when blocked by open questions, permission requirements, escalations, or hard failures.
- Persist roadmap run metadata in state so watcher/agents can resume after restart.
- No feature flag; this is a first-class workflow mode.

Implementation outline in the plan:
- Extend state schema/CLI metadata for run mode + roadmap progress.
- Add roadmap parser logic for extracting incomplete phases.
- Update watcher/flow logic to advance phases automatically and pause on human-gate conditions.
- Update `/handoff` skill and user docs to expose both modes clearly.
- Add targeted tests for parser behavior, state transitions, pause/advance rules, and resume.

Review focus requested:
1. Are the mode boundaries and lifecycle transitions clear enough to implement safely?
2. Do the human-gate conditions cover the right interruption points?
3. Should permission/open-question handling be a dedicated status vs structured `escalated` reason?
4. Any gaps in success criteria or test coverage expectations?

### Reviewer
**Action:** REQUEST_CHANGES

Strong direction overall, but I’m requesting changes before implementation so the execution contract is concrete and testable.

Blocking items:

1. Define the exact user-facing command surface now.
   - The plan currently says roadmap start syntax will be finalized later.
   - Please specify exact commands and accepted forms (for example, whether roadmap mode is `/handoff start --roadmap`, `/handoff start roadmap`, or both), plus how `/handoff status` reports mode progress.

2. Specify concrete state schema and transitions.
   - The plan mentions adding mode metadata but not exact fields.
   - Please define precise keys (field names + types) and include a transition table for:
     - roadmap start
     - plan approved -> impl start (same phase)
     - impl approved -> next phase start
     - queue completion
     - pause/resume conditions

3. Make human-gate behavior explicit in terms of state values.
   - “permission/open questions/escalations/blockers” is good intent, but watcher behavior needs deterministic triggers.
   - Please define whether these map to existing `status` values (`escalated`, `aborted`, etc.) with structured reasons, or introduce new statuses and update validation accordingly.

4. Define behavior when roadmap data is incomplete.
   - Clarify what happens if a queued phase has no `docs/phases/[phase].md` yet, or roadmap headings are malformed.
   - Expected behavior should be explicit: auto-create scaffold, fail fast, or pause for human.

---

## Round 2

### Lead
**Action:** SUBMIT_FOR_REVIEW

Addressed all four blocking items from Codex's Round 1 review. Changes made to `docs/phases/roadmap-execution-modes.md`:

**1. Exact command surface (new section added):**
- `/handoff start [phase]` — single-phase (unchanged)
- `/handoff start --roadmap` — full-roadmap, starts at first incomplete phase
- `/handoff start --roadmap [phase]` — full-roadmap, starts at named phase
- `/handoff status` output example showing mode + progress + next phase
- CLI equivalent using `--run-mode` flag on `state set`

**2. Concrete state schema and transitions (Section 1 & 2 rewritten):**
- New fields: `run_mode` (string enum), `roadmap` (object with `queue`, `current_index`, `completed`, `pause_reason`)
- New CLI flags: `--run-mode`, `--roadmap-queue`, `--roadmap-index`, `--roadmap-completed`, `--roadmap-pause-reason`
- Full transition table covering: roadmap start, plan approved → impl, impl approved → next phase, last phase complete, human-gate pause, human resume

**3. Human-gate behavior mapped to state values (Section 4 rewritten):**
- Reuses existing `status: "escalated"` — no new statuses
- Structured `reason` field with prefix categories: `needs-human:`, `needs-permission:`, `dispute:`, `error:`
- Watcher logic: when `status == "escalated"`, stop sending commands, print reason
- Resume: human sets `status: "ready"` via `state set`

**4. Behavior for incomplete/malformed roadmap (Section 3, error table):**
- Missing `docs/roadmap.md`: fail fast with actionable error
- No headings found: fail fast
- Heading without `**Status:**`: treat as incomplete (safe default, queue it)
- All phases complete: fail fast with message
- Named start phase not found: fail fast
- Phase has no `docs/phases/<slug>.md`: proceed normally — lead creates it during plan cycle

**Open Questions:** All three resolved with the decisions above.

### Reviewer
**Action:** APPROVE

Approved.

Round 1 blockers are now resolved in the plan:
- Command surface is explicit (`/handoff start [phase]`, `/handoff start --roadmap`, `/handoff start --roadmap [phase]`, and `/handoff status` roadmap output expectations).
- State schema is concrete (`run_mode`, `roadmap.queue/current_index/completed/pause_reason`) with transition table coverage for start, phase advancement, completion, and pause/resume.
- Human-gate behavior is deterministic via existing `status: "escalated"` and structured reason categories.
- Missing/malformed roadmap behavior is defined with clear fail-fast vs proceed semantics.

Non-blocking note:
- During implementation, ensure the command parser for `/handoff start --roadmap [phase]` and state CLI flags is documented with unambiguous examples in both skill docs and README to avoid argument-order drift.

---

<!-- CYCLE_STATUS (single source of truth - do not duplicate above) -->
READY_FOR: lead
ROUND: 2
STATE: approved
<!-- /CYCLE_STATUS -->
