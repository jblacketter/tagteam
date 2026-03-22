# Implementation Review Cycle: Cycle Storage & CLI

- **Phase:** cycle-storage-cli
- **Type:** impl
- **Date:** 2026-03-21
- **Lead:** Claude
- **Reviewer:** Codex

## Reference

Phase plan: `docs/phases/cycle-storage-cli.md`

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Implementation complete for the Cycle Storage & CLI phase. All code changes follow the approved plan.

**Files created:**
- `ai_handoff/cycle.py` — Core module: JSONL/JSON storage, status transitions, CLI commands (`init`, `add`, `status`, `rounds`, `render`), centralized `list_cycles()` with format de-duplication
- `tests/test_cycle.py` — 21 unit tests covering init, add_round, transitions, render, list_cycles, and a full integration test

**Files modified:**
- `ai_handoff/parser.py` — Added `parse_jsonl_rounds()`, `read_cycle_rounds()` dispatcher, and `_content_summary()` helper
- `ai_handoff/cli.py` — Registered `cycle` command + updated help text
- `ai_handoff/server.py` — `/api/cycles` uses `list_cycles()`, `/api/cycle/<id>` uses `render_cycle()` with legacy fallback, `/api/rounds/<id>` uses `read_cycle_rounds()` dispatcher, `_get_phases()` uses `list_cycles()` + `read_cycle_status()`
- `ai_handoff/data/web/app.js` — Format-agnostic cycle identifiers (`phase_type` instead of `phase_type_cycle.md`)
- `ai_handoff/tui/handoff_reader.py` — `find_cycle_doc()` checks JSONL first, `extract_last_round()` handles both formats
- `ai_handoff/tui/review_replay.py` — Uses `read_cycle_rounds()` dispatcher
- `.claude/skills/handoff/SKILL.md` — CLI commands instead of manual file edits
- `ai_handoff/data/.claude/skills/handoff/SKILL.md` — Package-bundled copy synced
- `tests/test_parser.py` — Added 8 tests for JSONL parsing and dispatcher

**Key implementation details:**
- `add_round()` applies status transitions automatically based on action (transition table from plan)
- `init_cycle()` is atomic: writes both status JSON and first JSONL entry in one call
- Content input: `--content` for inline, stdin for detailed feedback (heredocs)
- `list_cycles()` scans for both `_status.json` and `_cycle.md`, JSONL takes precedence
- `render_cycle()` synthesizes markdown from JSONL — used by `/api/cycle/<id>` for dashboard

**Test results:** 48 tests pass (21 cycle + 27 parser including 8 new JSONL tests)

### Reviewer

**Action:** REQUEST_CHANGES

**Blocking 1: The new `cycle` read commands do not honor legacy markdown fallback, so the updated `/handoff` hot path breaks on existing cycles.**

The implementation updates the skill to tell both agents to read submissions with `python -m ai_handoff cycle rounds --phase [phase] --type [plan|impl]`, but `_cli_rounds()` still calls `read_rounds()` directly, which only reads JSONL. `_cli_status()` and `_cli_render()` have the same issue because they call JSONL-only helpers too. On this branch, the active implementation cycle is still a legacy markdown file, and all three commands fail:

- `python -m ai_handoff cycle rounds --phase cycle-storage-cli --type impl` → `No rounds found for: cycle-storage-cli_impl`
- `python -m ai_handoff cycle status --phase cycle-storage-cli --type impl` → `No cycle found: cycle-storage-cli_impl`
- `python -m ai_handoff cycle render --phase cycle-storage-cli --type impl` → `No cycle found: cycle-storage-cli_impl`

This violates the phase goal that legacy `_cycle.md` files remain readable, and it breaks the just-updated skill on any project that still has markdown-backed cycles. Please route the CLI read paths through the same dispatcher/fallback logic already added in `parser.py`/`server.py`, and add regression coverage for legacy markdown cycles.

**Blocking 2: The TUI JSONL reader still fails for projects opened with `--dir` because `extract_last_round()` defaults to `.` and its callers do not pass the project directory.**

`extract_last_round()` only works for JSONL when it can call `read_cycle_rounds()` with the correct `project_dir`, but the current TUI call sites still invoke it as `extract_last_round(cycle_path)` with no phase/type/project context. That means once `find_cycle_doc()` returns a JSONL path for a project outside the current working directory, the read falls back to `./docs/handoffs/...` and returns `None`. I reproduced this by importing `ai_handoff/tui/handoff_reader.py` directly and calling `extract_last_round()` on a temp JSONL cycle: it returned `None` unless I explicitly passed `phase`, `step_type`, and `project_dir`.

This will break state-driven TUI behavior for migrated projects launched via `python -m ai_handoff tui --dir /path/to/project`, including review dialogue and last-action lookup. Please thread `project_dir` (and ideally phase/type) through the affected TUI call sites and add tests for JSONL-backed cycles outside cwd.

**Blocking 3: Full-roadmap mode in the skill still instructs the lead to manually create `_plan_cycle.md`, so that workflow never enters the new storage path.**

Single-phase `/handoff start [phase]` was updated to use `cycle init`, but `/handoff start --roadmap [phase?]` still says `Create docs/handoffs/[phase]_plan_cycle.md (same format as single-phase)`. That leaves a supported workflow on manual markdown cycle creation, which contradicts the phase goal of replacing markdown-based active cycle writes with CLI-driven JSONL/status storage. Please update the roadmap-start instructions in both skill copies to create the first cycle via `cycle init` as well.

## Round 2

### Lead

**Action:** SUBMIT_FOR_REVIEW

Addressed all three blocking issues:

**Blocking 1 (CLI legacy fallback):** Fixed `_cli_rounds()`, `_cli_status()`, and `_cli_render()` in `cycle.py` to fall back to legacy markdown. `_cli_rounds()` now uses `read_cycle_rounds()` dispatcher. `_cli_status()` extracts STATE/READY_FOR/ROUND from CYCLE_STATUS block in markdown. `_cli_render()` reads raw markdown when no JSONL exists. Verified: `cycle rounds --phase cycle-storage-cli --type impl` now works on this branch's own legacy cycle file. Added 3 regression tests in `TestCLILegacyFallback`.

**Blocking 2 (TUI project_dir):** Updated both TUI call sites to pass `phase`, `step_type`, and `project_dir` to `extract_last_round()`: `app.py:199` and `review_dialogue.py:190`. Added test `TestExtractLastRoundWithProjectDir.test_jsonl_outside_cwd` (skipped if textual not installed).

**Blocking 3 (Roadmap SKILL.md):** Updated `/handoff start --roadmap` section in both SKILL.md copies to use `cycle init` instead of manual `_plan_cycle.md` creation.

**Test results:** 51 passed, 1 skipped (textual).

### Reviewer

**Action:** REQUEST_CHANGES

**Blocking 1: The skill still hardcodes direct `_cycle.md` reads in the main `/handoff` flow, so newly created JSONL-backed cycles are not fully supported end-to-end.**

The storage and CLI pieces now work, but both skill copies still instruct agents to read a markdown cycle file directly:

- `.claude/skills/handoff/SKILL.md:30` says Step 1 is to read `docs/handoffs/[phase]_[type]_cycle.md`
- `.claude/skills/handoff/SKILL.md:153` says `/handoff status` should "Read active cycle file"
- the package-bundled copy has the same stale instructions

That was accurate for legacy storage, but Phase A changes the active write path to JSONL + status JSON. A fresh cycle created with `python -m ai_handoff cycle init ...` has no `_cycle.md` file, so an agent following the current skill literally will still look for a file that does not exist before it ever reaches the newer `cycle rounds` guidance. This is still a workflow blocker because the skill is the contract agents use to drive the handoff loop.

Please update those remaining read/orientation steps to use the new format-aware commands or explicitly say "read the active cycle via `cycle rounds` / `cycle render`" rather than naming `_cycle.md` as the source of truth.

## Round 3

### Lead

**Action:** SUBMIT_FOR_REVIEW

Fixed the remaining stale `_cycle.md` references in both SKILL.md copies:

- **Step 1** (line 30): Changed from "read the active cycle file `docs/handoffs/[phase]_[type]_cycle.md`" to "run `python -m ai_handoff cycle rounds --phase [phase] --type [type]`"
- **`/handoff status`** (line 153): Changed from "Read active cycle file" to "Read active cycle via `cycle status` and `cycle rounds` commands"
- Package-bundled copy synced

The skill now directs agents through format-aware CLI commands for all cycle reads. No remaining references to `_cycle.md` as a direct read target.

### Reviewer

**Action:** APPROVE

Approved. The implementation now covers the format-aware read/write path end-to-end: CLI reads fall back to legacy markdown, TUI readers receive the correct project context for JSONL cycles, and both skill copies route agents through CLI-based cycle access instead of hardcoded markdown files.

---

<!-- CYCLE_STATUS -->
READY_FOR: reviewer
ROUND: 3
STATE: approved
