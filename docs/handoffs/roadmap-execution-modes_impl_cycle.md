# Review Cycle: roadmap-execution-modes (impl)

## Metadata
- **Phase:** roadmap-execution-modes
- **Type:** impl
- **Started:** 2026-03-02
- **Lead:** Claude
- **Reviewer:** Codex

## Reference
- Plan: `docs/phases/roadmap-execution-modes.md`
- Implementation files:
  - `ai_handoff/roadmap.py` (new — roadmap parser)
  - `ai_handoff/state.py` (modified — new CLI flags + format_state)
  - `ai_handoff/watcher.py` (modified — auto-advance + human-gate)
  - `tests/test_roadmap.py` (new — 29 tests)

---

## Round 1

### Lead
**Action:** SUBMIT_FOR_REVIEW

Implementation of the roadmap execution modes feature per the approved plan.

**Files created:**

1. **`ai_handoff/roadmap.py`** — New roadmap parser module
   - `RoadmapPhase` dataclass (slug, name, status)
   - `parse_roadmap()` — extracts `### Phase N: <name>` headings + `**Status:**` lines
   - `get_incomplete_phases()` — filters to non-Complete phases, raises if all done
   - `build_queue()` — builds ordered slug queue, supports optional `start_phase` arg
   - `_slugify()` — converts phase names to file-safe slugs
   - Error handling per plan: FileNotFoundError for missing roadmap, ValueError for no headings / all complete / unknown start phase

2. **`tests/test_roadmap.py`** — 29 tests covering:
   - `TestSlugify` (4 tests) — basic, special chars, spaces, idempotent
   - `TestParseRoadmap` (5 tests) — all phases, missing file, no headings, missing status defaults, order preservation
   - `TestGetIncompletePhases` (2 tests) — filters complete, all-complete error
   - `TestBuildQueue` (4 tests) — full queue, start phase, not found, already complete
   - `TestStateCLIFlags` (5 tests) — valid modes, set run-mode, invalid mode, set queue, set pause reason
   - `TestFormatState` (4 tests) — single-phase, roadmap progress, last phase, pause display
   - `TestRoadmapAdvance` (5 tests) — noop single-phase, plan→impl, impl→next phase, last phase complete, noop non-approved

**Files modified:**

3. **`ai_handoff/state.py`**
   - Added `VALID_RUN_MODES = {"single-phase", "full-roadmap"}`
   - Added 5 new CLI flags to `_state_set()`: `--run-mode`, `--roadmap-queue`, `--roadmap-index`, `--roadmap-completed`, `--roadmap-pause-reason`
   - Roadmap sub-fields are collected and merged into nested `roadmap` dict
   - `--roadmap-queue` and `--roadmap-completed` accept comma-separated values
   - `--run-mode` validates against `VALID_RUN_MODES`
   - Updated `format_state()` to show mode, progress (N/M), next phase, and pause reason

4. **`ai_handoff/watcher.py`**
   - Added `_try_roadmap_advance()` function implementing the transition table:
     - Plan approved → starts impl cycle (same phase)
     - Impl approved (more phases) → advances to next phase plan cycle
     - Impl approved (last phase) → sets `result: "roadmap-complete"`
     - Returns None for single-phase mode or non-approved results
   - Updated `done` handler in watch loop to call `_try_roadmap_advance()` and reset poll tracking on advance
   - Updated `escalated` handler to show structured `pause_reason` from roadmap or `reason` field, plus resume instructions

**Test results:** 29/29 new tests pass. Full suite: 159/160 pass (1 pre-existing failure in test_migrate.py, unrelated).

**Mapping to success criteria:**
- [x] State schema supports `run_mode` + `roadmap` object
- [x] CLI flags for setting all roadmap metadata
- [x] Parser extracts phases from `docs/roadmap.md` with all 6 error cases
- [x] `build_queue()` supports starting at a specific phase
- [x] Watcher auto-advances plan→impl→next-phase→complete
- [x] Human-gate pauses with structured reason + resume instructions
- [x] `format_state()` shows mode, progress, next phase
- [x] 29 tests covering parser, state transitions, and advance logic

**Not yet implemented** (deferred to follow-up or out of scope for this cycle):
- SKILL.md updates for `/handoff start --roadmap` syntax (documentation update — should this be in this cycle or next?)
- README / workflows.md updates (same question)

### Reviewer
**Action:** REQUEST_CHANGES

Good progress on parser/state scaffolding, but there are blocking workflow regressions in roadmap auto-advance behavior.

1. **Plan approval auto-advances directly to reviewer impl turn without a lead implementation submission**
   - In `_try_roadmap_advance()`, the `current_type == "plan"` branch sets `type: "impl"` and `turn: "reviewer"` immediately.
   - Reference: `ai_handoff/watcher.py:275-291`.
   - Why this blocks: current workflow requires lead to implement and create/submission context before reviewer turn (`/handoff start [phase] impl` semantics). This skips that step and can route reviewer into an impl cycle with no new lead submission.

2. **Impl approval auto-advances phase state but does not create next phase/cycle artifacts before reviewer turn**
   - In the `current_type == "impl"` branch, state is moved to `phase: next_phase`, `type: "plan"`, `turn: "reviewer"` with command dispatch.
   - Reference: `ai_handoff/watcher.py:299-325`.
   - Why this blocks: reviewer can be triggered on a phase whose plan/cycle docs have not been created yet. Auto-advance must either:
     - hand control to lead to create the next plan cycle, or
     - create required docs atomically before setting reviewer-ready.

3. **User-facing command surface for roadmap mode is still not wired**
   - The implementation handoff explicitly marks SKILL/README/workflow updates as “Not yet implemented,” but these were in the approved plan scope and are necessary to actually start roadmap mode from `/handoff`.
   - Reference: `docs/handoffs/roadmap-execution-modes_impl_cycle.md:112-115`, plan scope/files in `docs/phases/roadmap-execution-modes.md`.
   - Why this blocks: without `/handoff` skill updates, users cannot invoke the new mode as designed; only low-level `state set` paths exist.

Requested fix direction:
- Preserve lead submission points between plan->impl and phase->phase transitions.
- Ensure any automatic transition that sets reviewer-ready guarantees corresponding cycle document context already exists.
- Complete command-surface integration in skill/docs for roadmap start and status behavior.

---

## Round 2

### Lead
**Action:** SUBMIT_FOR_REVIEW

Addressed all three blocking items from Codex's Round 1 review.

**1. Plan approval now hands to lead (not reviewer):**
- `_try_roadmap_advance()` plan branch: changed `turn → "lead"`, removed `type → "impl"` and `round → 1` overwrites
- Lead must implement and run `/handoff start [phase] impl` to create cycle docs before reviewer sees anything
- Test updated: `test_plan_approved_hands_to_lead` verifies `turn == "lead"`

**2. Impl approval now hands to lead for next phase (not reviewer):**
- `_try_roadmap_advance()` impl-more-phases branch: changed `turn → "lead"`
- Lead must run `/handoff start [next-phase]` to create plan/cycle docs before reviewer is triggered
- Test updated: `test_impl_approved_hands_to_lead_for_next_phase` verifies `turn == "lead"`
- Last-phase branch unchanged (sets `result: "roadmap-complete"`, no turn routing needed)

**3. SKILL.md wired with full roadmap command surface:**
- Added `/handoff start --roadmap` and `/handoff start --roadmap [phase]` to commands table
- Added new `## /handoff start --roadmap [phase?]` section with:
  - Step-by-step instructions for parsing roadmap, building queue, creating cycle docs
  - Exact `state set` CLI example with `--run-mode`, `--roadmap-queue`, `--roadmap-index` flags
  - Lifecycle description: plan → lead implements → impl → advance (all via lead submission points)
  - Status output example showing mode/progress/next
- Updated "Approved / done" state handling to describe `roadmap-complete` and roadmap auto-advance behavior
- Synced packaged copy at `ai_handoff/data/.claude/skills/handoff/SKILL.md`

**4. Plan transition table corrected:**
- Updated `docs/phases/roadmap-execution-modes.md` transition table to show `turn → "lead"` for both plan-approved and impl-approved-more-phases transitions

**All 29 tests pass.** Full suite: 159/160 (1 pre-existing failure in test_migrate.py, unrelated).

### Reviewer
**Action:** REQUEST_CHANGES

Round 1 blockers are mostly addressed, but one workflow-critical issue remains:

1. **Auto-advance hands to lead with a generic command that can route to the wrong `/handoff` path**
   - In `_try_roadmap_advance()` the plan-approved and impl-approved branches now correctly set `turn: "lead"`, but `command` is still set to:
     - `"Read .claude/skills/handoff/SKILL.md and handoff-state.json, then act on your turn"`
   - References: `ai_handoff/watcher.py` plan branch and impl-more-phases branch in `_try_roadmap_advance()`.
   - Why this blocks:
     - For plan approval, the intended next action is explicit: lead should run `/handoff start [phase] impl`.
     - For impl approval with more phases, intended next action is: lead should run `/handoff start [next-phase]`.
     - A generic `/handoff` command at this point can send lead into the standard "your turn" flow (address feedback / append round) instead of starting the next required cycle artifact creation.

Requested fix:
- Set transition-specific `command` values during roadmap auto-advance:
  - Plan approved -> lead: command should direct `/handoff start [current-phase] impl`
  - Impl approved (more phases) -> lead: command should direct `/handoff start [next-phase]`
- Add/adjust tests to assert these command values on transition.

---

## Round 3

### Lead
**Action:** SUBMIT_FOR_REVIEW

Fixed the transition-specific command values in `_try_roadmap_advance()`.

**Changes to `ai_handoff/watcher.py`:**
- Plan approved branch: `command` changed from generic string to `f"/handoff start {phase} impl"`
- Impl approved (more phases) branch: `command` changed from generic string to `f"/handoff start {next_phase}"`

**Changes to `tests/test_roadmap.py`:**
- `test_plan_approved_hands_to_lead`: added assertion `command == "/handoff start api-gateway impl"`
- `test_impl_approved_hands_to_lead_for_next_phase`: added assertion `command == "/handoff start dashboard"`

All 29 tests pass.

### Reviewer
**Action:** APPROVE

Approved.

Round 2 blocker is resolved:
- `_try_roadmap_advance()` now sets transition-specific lead commands:
  - plan approved -> `"/handoff start [current-phase] impl"`
  - impl approved (more phases) -> `"/handoff start [next-phase]"`
- Tests were updated to assert these exact command values:
  - `test_plan_approved_hands_to_lead`
  - `test_impl_approved_hands_to_lead_for_next_phase`

Validation notes:
- `pytest -q tests/test_roadmap.py` passes (29 tests).
- `pytest -q tests/test_iterm.py tests/test_state_watcher.py` passes (43 tests).

Non-blocking follow-up:
- Consider syncing README/workflow docs with the new roadmap-mode command surface in a docs pass.

---

<!-- CYCLE_STATUS (single source of truth - do not duplicate above) -->
READY_FOR: lead
ROUND: 3
STATE: approved
<!-- /CYCLE_STATUS -->
