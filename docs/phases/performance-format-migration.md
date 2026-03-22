# Phase: Performance Format Migration

## Summary
Replace the markdown-based cycle documents and roadmap with faster formats that eliminate multi-step Edit tool operations. Cycle documents switch to append-only JSONL (for rounds) + a small JSON status file, updated via CLI commands. The roadmap switches to YAML with a CLI setter. This turns 3-4 tool calls per update into a single shell command, significantly reducing agent response time during handoff cycles.

## Goals
1. Eliminate read-modify-write patterns for cycle document updates
2. Replace markdown roadmap edits with a single CLI command
3. Maintain dashboard and TUI compatibility with the new formats
4. Provide migration tooling for existing cycle documents
5. Keep human readability via dashboard rendering (raw files are machine-oriented)

## Scope

### In Scope
- New `ai_handoff/cycle.py` module for JSONL round storage + JSON status
- New CLI commands: `python -m ai_handoff cycle add`, `python -m ai_handoff cycle status`
- Roadmap format migration from markdown to YAML (`docs/roadmap.yaml`)
- New CLI command: `python -m ai_handoff roadmap set-status [phase] [status]`
- Update `parser.py` to read JSONL rounds
- Update `server.py` API endpoints to serve new formats
- Update `SKILL.md` to use CLI commands instead of manual file edits
- Migration utility for existing `.md` cycle files to new format

### Out of Scope
- SQLite (overkill for current scale)
- Changes to `handoff-state.json` (already fast — atomic JSON writes)
- TUI changes beyond reading the new format
- Web dashboard UI redesign

## Technical Approach

### Step 1: Cycle Document Format Change

**New file structure per cycle:**
- `docs/handoffs/[phase]_[type]_status.json` — Tiny status file (~200 bytes)
  ```json
  {"state": "in-progress", "ready_for": "reviewer", "round": 2}
  ```
- `docs/handoffs/[phase]_[type]_rounds.jsonl` — Append-only round log
  ```jsonl
  {"round":1,"role":"lead","action":"SUBMIT_FOR_REVIEW","content":"...","ts":"2026-03-21T..."}
  {"round":1,"role":"reviewer","action":"APPROVE","content":"...","ts":"2026-03-21T..."}
  ```

**New module: `ai_handoff/cycle.py`**
- `add_round(phase, type, role, action, content, project_dir)` — Appends to JSONL, updates status JSON
- `read_status(phase, type, project_dir)` — Reads status JSON
- `read_rounds(phase, type, project_dir)` — Reads all rounds from JSONL
- `list_cycles(project_dir)` — Returns the de-duplicated union of all cycles. Scans `docs/handoffs/` for both `_status.json` files (JSONL-backed cycles) and `_cycle.md` files (legacy). Extracts cycle identifiers (`{phase}_{type}`) from filenames. When both formats exist for the same identifier, JSONL takes precedence — the legacy `.md` entry is excluded. Returns a list of `{id, format, phase, type}` dicts. This is the single discovery function used by `server.py`, TUI, and CLI.

**New CLI commands:**
- `python -m ai_handoff cycle add --phase X --type plan --role lead --action SUBMIT --content "..."` — Single command replaces read→edit→verify
- `python -m ai_handoff cycle status --phase X --type plan` — Show current cycle status
- `python -m ai_handoff cycle rounds --phase X --type plan` — Show all rounds

### Step 2: Roadmap Format Change

**New file: `docs/roadmap.yaml`**
```yaml
title: "AI Handoff Framework"
description: "A collaboration framework enabling structured, multi-phase AI-to-AI collaboration with human oversight."
tech_stack: "Python 3.10+, YAML configuration, Markdown templates, Textual (TUI)"

phases:
  - name: "Configurable Agents Init"
    slug: configurable-agents-init
    status: Complete
    description: "Create interactive init command for configuring AI agents and their roles"
    deliverables:
      - "Interactive init command"
      - "ai-handoff.yaml config file generation"
      - "Skills updated to read config at runtime"
  # ... remaining phases
```

**Updated `roadmap.py`:**
- `parse_roadmap()` reads YAML instead of markdown regex
- New: `set_phase_status(phase_slug, status)` — Updates one field in YAML
- `roadmap queue` CLI continues to work, reading from YAML

**New CLI command:**
- `python -m ai_handoff roadmap set-status [phase-slug] [status]`

### Step 3: Update All Consumers

**Precedence rule:** When both JSONL and legacy `.md` files exist for the same cycle, JSONL takes priority. Readers check for `_rounds.jsonl` first; if absent, fall back to `_cycle.md`. This ensures migrated cycles use the new format while unmigrated ones keep working.

**Cycle document consumers (all must support both formats):**
- **`ai_handoff/parser.py`:** Add `parse_jsonl_rounds()` returning the same round dict structure as `extract_all_rounds()`. Add `read_cycle_rounds(phase, type, project_dir)` dispatcher that checks JSONL first, falls back to markdown.
- **`ai_handoff/server.py`:** Update `_get_phases()`, `_extract_cycle_state()`, `/api/cycles`, `/api/rounds/<file>` to use the new dispatcher. Status comes from `_status.json` when present. Keep `/api/cycle/<filename>` working by synthesizing markdown from JSONL when the `.md` file doesn't exist (see frontend section below).
- **`ai_handoff/data/web/app.js`:** Update the cycle viewer frontend:
  - `autoSelectCycle()` currently builds `phase + '_' + type + '_cycle.md'` — change to use a cycle identifier without the `.md` extension (e.g. `phase + '_' + type`), letting the server resolve the format.
  - `loadCycleList()` fetches `/api/cycles` — the server will return cycle identifiers (not filenames) that work for both formats.
  - `loadCycleDoc(id)` fetches `/api/cycle/<id>` — the server synthesizes a human-readable view from JSONL+status or serves the raw `.md`, whichever exists. The frontend doesn't need to know the storage format.
  - `loadRounds(id)` fetches `/api/rounds/<id>` — already returns structured JSON, just needs the server to support JSONL source.
- **`ai_handoff/tui/handoff_reader.py`:** Update `find_cycle_doc()` to return a path or format indicator. Update `extract_last_round()` to use the dispatcher.
- **`ai_handoff/tui/app.py`:** `_get_last_action()` calls through `handoff_reader.py` — no direct changes needed if `handoff_reader` is updated.
- **`ai_handoff/tui/review_dialogue.py`:** `_get_round_data()` calls through `handoff_reader.py` — same as above.
- **`ai_handoff/tui/review_replay.py`:** `build_review_replay()` calls `extract_all_rounds()` — update to use dispatcher.

**Roadmap consumers:**
- **`ai_handoff/roadmap.py`:** Split parsing into two functions:
  - `_parse_roadmap_md(path)` — The existing markdown regex parser, renamed to a private helper. Used only by `migrate --roadmap` to read legacy `docs/roadmap.md`.
  - `parse_roadmap(roadmap_path)` — The public API, rewritten to read YAML. All runtime consumers call this.
  - This ensures `migrate --roadmap` can read legacy markdown while all other code paths use YAML.
- **`ai_handoff/tui/map_data.py`:** `_parse_phase_names()` currently reads `roadmap.md` for phase ordering. Update to read `roadmap.yaml`. Note: `_parse_phase_status()` reads `docs/phases/*.md` checkbox state for TUI visual status — this is a separate concern (implementation progress within a phase) and remains unchanged. The roadmap `status` field tracks the overall phase lifecycle (Planning/In Progress/Complete), while phase doc checkboxes track granular task progress.

**Skill files (both copies):**
- **`.claude/skills/handoff/SKILL.md`:** Replace manual cycle file editing with CLI commands.
- **`ai_handoff/data/.claude/skills/handoff/SKILL.md`:** Package-bundled copy, must stay in sync.

**Templates:**
- **`templates/cycle.md`** and **`ai_handoff/data/templates/cycle.md`:** Keep for backward compatibility but add a note that new cycles use JSONL format.
- **`templates/roadmap.md`** and **`ai_handoff/data/templates/roadmap.md`:** Replace with YAML versions (`roadmap.yaml`).

### Step 4: CLI Integration

All new commands are registered in **`ai_handoff/cli.py`** (not `__main__.py`, which only delegates to `cli.py:main`).

**New top-level command: `cycle`**
- `python -m ai_handoff cycle add --phase X --type plan --role lead --action SUBMIT --content "..."` — Appends round to JSONL + updates status JSON
- `python -m ai_handoff cycle status --phase X --type plan` — Show current cycle status
- `python -m ai_handoff cycle rounds --phase X --type plan` — Show all rounds
- `python -m ai_handoff cycle init --phase X --type plan --lead Claude --reviewer Codex` — Create new cycle (status JSON + empty JSONL)

**Extended existing command: `roadmap`**
- `python -m ai_handoff roadmap set-status [phase-slug] [status]` — New subcommand added to existing `roadmap_command()` in `roadmap.py`

**Extended existing command: `migrate`**
- `python -m ai_handoff migrate --cycles` — New flag on existing `migrate_command()` in `migrate.py`. Converts `_cycle.md` files to JSONL + status JSON.
- `python -m ai_handoff migrate --roadmap` — New flag. Converts `docs/roadmap.md` to `docs/roadmap.yaml`.

### Step 5: Migration Utility

- Runs under the existing `migrate` command surface (not new top-level commands)
- Non-destructive: keeps old files, creates new ones alongside
- `--cycles`: For each `_cycle.md`, creates `_rounds.jsonl` + `_status.json` using `parser.py` to extract rounds
- `--roadmap`: Parses `docs/roadmap.md` using the private `_parse_roadmap_md()` helper (the legacy markdown parser preserved specifically for migration), then writes `docs/roadmap.yaml`

## Source of Truth Clarification

**Roadmap phase status** has two related but distinct meanings today:
1. **Lifecycle status** (`docs/roadmap.md` → Planning/In Progress/Complete) — used by `roadmap.py` for queue building and ordering
2. **Task progress** (`docs/phases/*.md` checkboxes) — used by `map_data.py` for TUI visual indicators

This phase consolidates #1 into `docs/roadmap.yaml` as the single authoritative source for lifecycle status. The `roadmap set-status` CLI command writes here. Task progress (#2) remains in phase docs — it's a different granularity and doesn't conflict.

## Files to Modify/Create

| File | Action | Purpose |
|------|--------|---------|
| `ai_handoff/cycle.py` | Create | JSONL round storage + JSON status management + CLI |
| `ai_handoff/cli.py` | Modify | Register `cycle` command, dispatch to `cycle.py` |
| `ai_handoff/roadmap.py` | Modify | Switch to YAML parsing, add `set-status` subcommand |
| `ai_handoff/migrate.py` | Modify | Add `--cycles` and `--roadmap` migration flags |
| `ai_handoff/parser.py` | Modify | Add JSONL round parser + format dispatcher |
| `ai_handoff/server.py` | Modify | Update API endpoints to use format dispatcher, synthesize markdown view from JSONL |
| `ai_handoff/data/web/app.js` | Modify | Use format-agnostic cycle identifiers instead of `.md` filenames |
| `ai_handoff/tui/handoff_reader.py` | Modify | Use format dispatcher for cycle lookups |
| `ai_handoff/tui/review_replay.py` | Modify | Use format dispatcher for replay |
| `ai_handoff/tui/map_data.py` | Modify | Read phase names from `roadmap.yaml` |
| `.claude/skills/handoff/SKILL.md` | Modify | Use CLI commands instead of manual edits |
| `ai_handoff/data/.claude/skills/handoff/SKILL.md` | Modify | Package-bundled copy, keep in sync |
| `ai_handoff/setup.py` | Modify | Copy YAML roadmap template instead of markdown |
| `docs/roadmap.yaml` | Create | YAML roadmap (replaces roadmap.md) |
| `templates/roadmap.yaml` | Create | YAML roadmap template (replaces roadmap.md template) |
| `ai_handoff/data/templates/roadmap.yaml` | Create | Package-bundled YAML roadmap template |
| `tests/test_cycle.py` | Create | Unit tests for cycle module |
| `tests/test_parser.py` | Modify | Add tests for JSONL parsing + dispatcher |
| `tests/test_roadmap.py` | Modify | Update tests for YAML format |

## Success Criteria
1. Agents can update cycle documents with a single CLI command (no Edit tool needed)
2. Agents can update roadmap status with a single CLI command
3. Dashboard renders JSONL rounds identically to old markdown rounds
4. Existing cycle documents remain readable (backward compatibility)
5. All new CLI commands have unit test coverage
6. Measurable reduction in tool calls per handoff turn (from ~4 to 1)
