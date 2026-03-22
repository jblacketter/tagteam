# Plan Review Cycle: Performance Format Migration

- **Phase:** performance-format-migration
- **Type:** plan
- **Date:** 2026-03-21
- **Lead:** Claude
- **Reviewer:** Codex

## Reference

Phase plan: `docs/phases/performance-format-migration.md`

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Submitting the Performance Format Migration plan for review. This phase addresses the performance bottleneck when AI agents update cycle documents and the roadmap during handoff workflows.

**Problem:** Each cycle document update currently requires agents to read the full markdown file, craft an exact-match string for the Edit tool, replace content, and sometimes retry — 3-4 tool calls per update. The roadmap has the same issue.

**Proposed solution (two changes):**

1. **Cycle documents → JSONL + JSON status** — Split the monolithic markdown cycle file into an append-only `_rounds.jsonl` (one JSON line per round entry) and a tiny `_status.json`. New CLI command `python -m ai_handoff cycle add` handles everything in one shell call. No more read-modify-write.

2. **Roadmap → YAML + CLI setter** — Replace `docs/roadmap.md` with `docs/roadmap.yaml`. New CLI command `python -m ai_handoff roadmap set-status` updates a single field. No more string-matching markdown edits.

**Key design decisions:**
- Append-only JSONL for rounds (fastest possible write pattern — no reads needed)
- Separate status JSON so status can be updated without touching round history
- Backward compatibility: old `.md` cycle files remain readable by dashboard
- No SQLite — overkill at this scale, JSONL gives most of the same benefit
- Migration utilities provided but non-destructive (old files kept)

Please review `docs/phases/performance-format-migration.md` for completeness, feasibility, and gaps.

### Reviewer

**Action:** REQUEST_CHANGES

The performance goal is sound, but the current plan misses a few code-path and source-of-truth details that would make the migration incomplete.

**Blocking 1: CLI integration is specified against the wrong file.** The plan adds `cycle add`, `cycle status`, `roadmap set-status`, `migrate-cycles`, and `migrate-roadmap`, but the file list omits `ai_handoff/cli.py` and instead says to register subcommands in `ai_handoff/__main__.py`. In this repo `ai_handoff/__main__.py` only delegates to `ai_handoff.cli:main`; all command dispatch and help text live in `ai_handoff/cli.py`. Please update the plan to include the real CLI touchpoints and clarify whether the migration commands are new top-level commands or subcommands under the existing `migrate` surface.

**Blocking 2: `docs/roadmap.yaml` currently becomes a second status source of truth.** Today `ai_handoff/roadmap.py` reads roadmap status from `docs/roadmap.md`, while `ai_handoff/tui/map_data.py` derives visual phase status from the checkbox state inside `docs/phases/*.md`. Adding per-phase `status` fields to `docs/roadmap.yaml` plus `roadmap set-status` creates a divergence unless the plan explicitly defines one authoritative source and how the other representation stays in sync.

**Blocking 3: Compatibility and migration scope is broader than listed.** The plan mentions `parser.py`, `server.py`, and `SKILL.md`, but the current format is also hardcoded in `ai_handoff/tui/map_data.py`, `ai_handoff/tui/handoff_reader.py`, `ai_handoff/tui/review_replay.py`, `ai_handoff/setup.py`, `tests/test_parser.py`, `tests/test_roadmap.py`, `.claude/skills/handoff/SKILL.md`, and `ai_handoff/data/.claude/skills/handoff/SKILL.md`. Please expand the file list and define precedence when migrated JSONL/JSON files exist alongside legacy `_cycle.md` files; otherwise readers can continue using stale markdown after migration.

## Round 2

### Lead

**Action:** SUBMIT_FOR_REVIEW

Addressed all three blocking issues in the phase plan. Here's what changed:

**Blocking 1 (CLI integration):** Fixed. Replaced `__main__.py` with `cli.py` in the file list. Clarified that `cycle` is a new top-level command in `cli.py`, `roadmap set-status` extends the existing `roadmap` command, and migration uses new `--cycles`/`--roadmap` flags on the existing `migrate` command (not new top-level commands).

**Blocking 2 (Source of truth):** Clarified. Added a "Source of Truth Clarification" section. The two status concepts are distinct: (1) lifecycle status (Planning/In Progress/Complete) lives in `roadmap.yaml` — this is what `roadmap.py` and `roadmap set-status` touch; (2) task progress (checkboxes in `docs/phases/*.md`) lives in phase docs — this is what `map_data.py` reads for TUI visuals. They don't conflict because they track different granularities. `roadmap.yaml` is the single authority for lifecycle status.

**Blocking 3 (Broader scope):** Expanded the file list from 9 to 18 entries. Now includes all consumers: `tui/handoff_reader.py`, `tui/review_replay.py`, `tui/map_data.py`, `setup.py`, both copies of `SKILL.md`, template files, and test files. Added explicit precedence rule: JSONL takes priority over `.md` when both exist for the same cycle. Readers check for `_rounds.jsonl` first, fall back to `_cycle.md`.

Updated plan: `docs/phases/performance-format-migration.md`

### Reviewer

**Action:** REQUEST_CHANGES

The revised plan resolves the earlier issues, but one runtime consumer is still missing from the migration scope.

**Blocking 1: The web dashboard frontend still hardcodes markdown cycle filenames and document loading.** `ai_handoff/data/web/app.js` currently auto-selects `currentState.phase + '_' + currentState.type + '_cycle.md'`, populates the dropdown from `/api/cycles`, and renders `/api/cycle/<filename>` as markdown. The updated plan covers `server.py` changes for `/api/cycles` and `/api/rounds/<file>`, but it does not cover `ai_handoff/data/web/app.js` or define what happens to `/api/cycle/<filename>` once new cycles are JSONL + status JSON only. Please update the plan to either:

1. include the frontend changes in `ai_handoff/data/web/app.js` and define the new API contract for selecting/loading cycles, or
2. explicitly keep `/api/cycle/<filename>` as a synthesized human-readable document endpoint and describe how the server will map JSONL/status files back to that view.

Without that, the dashboard cycle viewer and auto-selection path will break for newly created cycles even if the backend parser supports JSONL.

## Round 3

### Lead

**Action:** SUBMIT_FOR_REVIEW

Addressed the frontend gap. Updated the plan with both approaches combined:

1. **Server-side synthesis:** `/api/cycle/<id>` will synthesize a human-readable markdown view from JSONL+status when no `.md` file exists. This keeps the existing render pipeline working and means the frontend doesn't need to understand storage formats.

2. **Frontend updates:** `app.js` switches from hardcoded `_cycle.md` filenames to format-agnostic cycle identifiers. The server resolves the format transparently. Specifically:
   - `autoSelectCycle()` builds `phase + '_' + type` (no `.md` suffix)
   - `/api/cycles` returns cycle identifiers, not filenames
   - `loadCycleDoc()` and `loadRounds()` pass identifiers — server handles format resolution

Added `ai_handoff/data/web/app.js` to the file list (now 19 files total).

Updated plan: `docs/phases/performance-format-migration.md`

### Reviewer

**Action:** REQUEST_CHANGES

Most of the plan is now in good shape, but the roadmap migration path is internally inconsistent.

**Blocking 1: `migrate --roadmap` cannot rely on `parse_roadmap()` after `roadmap.py` is converted to YAML.** The plan says this phase will switch `ai_handoff/roadmap.py::parse_roadmap()` from markdown parsing to YAML parsing, then later says `python -m ai_handoff migrate --roadmap` will parse `docs/roadmap.md` using the existing `parse_roadmap()`. Those two statements conflict. Once `parse_roadmap()` reads YAML, it can no longer be the function that migrates legacy markdown roadmaps.

Please update the plan to define a concrete migration strategy, for example:
1. keep a dedicated legacy markdown parser for migration only, or
2. split roadmap parsing into separate markdown and YAML helpers, with `migrate --roadmap` explicitly calling the markdown one and normal runtime paths calling the YAML one.

Without that separation, the roadmap migration path is not implementable as written.

## Round 4

### Lead

**Action:** SUBMIT_FOR_REVIEW

Addressed the roadmap migration inconsistency. Updated the plan to split `roadmap.py` parsing into two functions:

- `_parse_roadmap_md(path)` — Private helper. The existing markdown regex parser, renamed. Used **only** by `migrate --roadmap` to read legacy `docs/roadmap.md`.
- `parse_roadmap(roadmap_path)` — Public API, rewritten to read YAML. All runtime consumers call this.

This cleanly separates the migration path (reads markdown) from the runtime path (reads YAML). The Step 5 migration description now explicitly references `_parse_roadmap_md()` instead of `parse_roadmap()`.

Updated plan: `docs/phases/performance-format-migration.md`

### Reviewer

**Action:** REQUEST_CHANGES

The plan is close, but the cycle-discovery path is still inconsistent with the backward-compatibility goal.

**Blocking 1: `list_cycles()` as written would drop legacy cycles, and `/api/cycles` would either miss them or duplicate migrated ones.** In Step 1, `ai_handoff/cycle.py` defines `list_cycles(project_dir)` as "scans for `_status.json` files". That conflicts with Step 3, which says readers and `/api/cycles` must work for both JSONL-backed cycles and legacy `_cycle.md` files. If `list_cycles()` only scans `_status.json`, unmigrated cycles disappear from the dashboard. If the server separately scans both formats without a defined merge rule, migrated phases can show up twice.

Please update the plan to make cycle discovery explicit:
1. define whether `list_cycles()` returns the de-duplicated union of JSONL/status cycles and legacy `_cycle.md` cycles, with JSONL taking precedence when both exist, or
2. state that `server.py` owns that union/de-duplication logic and `list_cycles()` is JSONL-only by design.

Without that, the compatibility story for `/api/cycles` and the dashboard cycle picker is still incomplete.

## Round 5

### Lead

**Action:** SUBMIT_FOR_REVIEW

Addressed the cycle discovery gap. Updated `list_cycles()` in the plan (Step 1) to be the single discovery function that returns the de-duplicated union of both formats:

- Scans `docs/handoffs/` for both `_status.json` (JSONL-backed) and `_cycle.md` (legacy) files
- Extracts cycle identifiers (`{phase}_{type}`) from filenames
- When both formats exist for the same identifier, JSONL takes precedence — legacy entry excluded
- Returns `{id, format, phase, type}` dicts
- Used by `server.py`, TUI, and CLI as the single source for cycle listing — no separate scanning logic elsewhere

This resolves the duplication/omission concern: one function, one merge rule, one place.

Updated plan: `docs/phases/performance-format-migration.md`

### Reviewer

**Action:** REQUEST_CHANGES

The plan is substantially improved, but one gap remains and this is now round 5, so I’m escalating to the human arbiter.

**Blocking 1: Migration command coverage still lacks an explicit test plan.** The phase adds two new `migrate` flags, `--cycles` and `--roadmap`, and the success criteria say all new CLI commands should have unit test coverage. However, the file list only adds `tests/test_cycle.py`, updates `tests/test_parser.py`, and updates `tests/test_roadmap.py`; it does not include `tests/test_migrate.py` or another explicit place where the new migration flag behavior will be tested. Since `migrate --roadmap` now depends on a split legacy/runtime parser path and `migrate --cycles` introduces conversion behavior, those code paths need direct tests in the plan, not just implied coverage elsewhere.

**Arbiter question:** Is it acceptable to approve this plan with migration-command test coverage implied but not explicitly listed, or should the phase plan explicitly add migration tests (most likely in `tests/test_migrate.py`) before approval?

---

<!-- CYCLE_STATUS -->
READY_FOR: human
ROUND: 5
STATE: escalated
