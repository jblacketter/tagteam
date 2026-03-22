# Phase: Cycle Storage & CLI

## Summary
Replace markdown-based cycle documents with append-only JSONL rounds + JSON status, and add CLI commands so agents never manually edit cycle files. This is the highest-ROI performance change: it removes repeated read/modify/write of markdown from the active handoff loop.

Based on recommendations in `docs/design/handoff-performance-recommendations.md`.

## Goals
1. Agents update cycle documents with a single CLI command (no Edit tool)
2. Append-only writes — no full-file rewrites on each turn
3. All consumers (dashboard, TUI, server) read the new format
4. Legacy `_cycle.md` files remain readable (backward compatibility)
5. Synthesized markdown view available on demand

## Scope

### In Scope
- New `ai_handoff/cycle.py` module (storage + CLI)
- CLI commands: `cycle init`, `cycle add`, `cycle status`, `cycle rounds`, `cycle render`
- Update `SKILL.md` to use CLI commands instead of manual edits
- Update all consumers to read JSONL/JSON format
- Centralized cycle discovery with format precedence (JSONL > legacy md)

### Out of Scope
- Roadmap format migration (lower ROI, separate phase if needed)
- Migration of existing `_cycle.md` files (Phase B)
- Migration tests (Phase B)
- SQLite (future option)

## Technical Approach

### Step 1: New Module — `ai_handoff/cycle.py`

**File structure per cycle:**
- `docs/handoffs/{phase}_{type}_status.json` — Tiny status file (~200 bytes)
  ```json
  {"state": "in-progress", "ready_for": "reviewer", "round": 2, "phase": "...", "type": "plan", "lead": "Claude", "reviewer": "Codex", "date": "2026-03-21"}
  ```
- `docs/handoffs/{phase}_{type}_rounds.jsonl` — Append-only round log
  ```jsonl
  {"round":1,"role":"lead","action":"SUBMIT_FOR_REVIEW","content":"...","ts":"2026-03-21T12:00:00Z"}
  {"round":1,"role":"reviewer","action":"REQUEST_CHANGES","content":"...","ts":"2026-03-21T12:05:00Z"}
  ```

**Functions:**
- `init_cycle(phase, type, lead, reviewer, content, project_dir)` — Atomic initialization: creates status JSON + JSONL with the lead's first `SUBMIT_FOR_REVIEW` entry in a single operation. Status is set to `state: "in-progress"`, `ready_for: "reviewer"`, `round: 1`. The `content` parameter is the lead's initial submission text. This ensures no observable state where the cycle exists but has no round data.
- `add_round(phase, type, role, action, round, content, project_dir)` — Appends one line to JSONL and applies status transitions based on the `action` parameter:

  **Status transition rules (applied to `_status.json`):**
  | Action | `state` | `ready_for` | `round` |
  |--------|---------|-------------|---------|
  | `SUBMIT_FOR_REVIEW` | `in-progress` | `reviewer` | set to caller's `round` value |
  | `REQUEST_CHANGES` | `in-progress` | `lead` | unchanged (stays at current) |
  | `APPROVE` | `approved` | _(cleared)_ | unchanged |
  | `ESCALATE` | `escalated` | `human` | unchanged |
  | `NEED_HUMAN` | `needs-human` | `human` | unchanged |

  The `round` parameter is passed explicitly by the caller. Lead passes N+1 when starting a new round; reviewer passes N to stay within the current round. `add_round` does not auto-increment.

- `read_status(phase, type, project_dir)` — Reads status JSON
- `read_rounds(phase, type, project_dir)` — Reads all rounds from JSONL
- `render_cycle(phase, type, project_dir)` — Synthesizes human-readable markdown from JSONL + status
- `list_cycles(project_dir)` — De-duplicated union of JSONL-backed and legacy `_cycle.md` cycles. Scans `docs/handoffs/` for `_status.json` and `_cycle.md` files, extracts `{phase}_{type}` identifiers. JSONL takes precedence when both exist. Returns list of `{id, format, phase, type}` dicts. Single discovery function for all consumers.

**Content input modes:** Both `cycle init` and `cycle add` accept content via two paths:
- `--content "short text"` — Inline, for simple summaries and approvals
- stdin (when `--content` is omitted) — For detailed multi-line feedback. Agents use heredocs:
  ```bash
  python -m ai_handoff cycle add --phase X --type plan --role reviewer \
    --action REQUEST_CHANGES --round 1 <<'EOF'
  **Blocking 1:** The parser doesn't handle edge cases.

  - `extract_rounds()` fails when content has backticks
  - Multi-paragraph feedback gets truncated
  EOF
  ```
  The `'EOF'` quoting prevents shell expansion, so backticks, quotes, and special characters are safe. This preserves review depth without fragile shell escaping.

**CLI surface (registered in `cli.py`):**
- `python -m ai_handoff cycle init --phase X --type plan --lead Claude --reviewer Codex --content "initial submission"`
- `python -m ai_handoff cycle add --phase X --type plan --role lead --action SUBMIT_FOR_REVIEW --round N --content "..."`
- `python -m ai_handoff cycle add --phase X --type plan --role reviewer --action REQUEST_CHANGES --round N <<'EOF'` (stdin for detailed feedback)
- `python -m ai_handoff cycle status --phase X --type plan`
- `python -m ai_handoff cycle rounds --phase X --type plan`
- `python -m ai_handoff cycle render --phase X --type plan`

### Step 2: Update SKILL.md

Replace manual cycle file editing instructions with CLI commands:

**Lead's turn (before):** Create `## Round N+1` section, edit CYCLE_STATUS block manually
**Lead's turn (after):** Run `python -m ai_handoff cycle add --phase X --type plan --role lead --action SUBMIT_FOR_REVIEW --round 2 --content "summary of changes"`

**Reviewer's turn (before):** Edit `### Reviewer` section, edit CYCLE_STATUS block manually
**Reviewer's turn (after, simple):** Run `python -m ai_handoff cycle add --phase X --type plan --role reviewer --action APPROVE --round 2 --content "Approved. No issues found."`
**Reviewer's turn (after, detailed feedback):**
```bash
python -m ai_handoff cycle add --phase X --type plan --role reviewer \
  --action REQUEST_CHANGES --round 2 <<'EOF'
**Blocking 1:** The transition table is missing...

- Detail here
- More detail
EOF
```

**Starting a cycle (before):** Manually create `_cycle.md` with metadata + Round 1 + CYCLE_STATUS
**Starting a cycle (after):** Run `python -m ai_handoff cycle init --phase X --type plan --lead Claude --reviewer Codex --content "initial submission"` (single atomic command — creates cycle and first round together, no invalid intermediate state)

### Step 3: Update Consumers

**Precedence rule:** JSONL format takes priority over legacy `.md`. Readers check for `_rounds.jsonl` first; if absent, fall back to `_cycle.md`.

**`ai_handoff/parser.py`:**
- Add `parse_jsonl_rounds(jsonl_path)` — Returns same round dict structure as `extract_all_rounds()`
- Add `read_cycle_rounds(phase, type, project_dir)` — Dispatcher: checks JSONL first, falls back to markdown. Single entry point for all round reading.

**`ai_handoff/server.py`:**
- `/api/cycles` — Uses `list_cycles()` from `cycle.py`. Returns cycle identifiers (not filenames).
- `/api/cycle/<id>` — Calls `render_cycle()` for JSONL-backed cycles, reads raw `.md` for legacy. Returns human-readable markdown either way.
- `/api/rounds/<id>` — Uses `read_cycle_rounds()` dispatcher. Returns structured JSON.
- `_get_phases()` and `_extract_cycle_state()` — Use `list_cycles()` and `read_status()` for JSONL-backed cycles.

**`ai_handoff/data/web/app.js`:**
- `autoSelectCycle()` — Build `phase + '_' + type` (no `.md` suffix). Server resolves format.
- `loadCycleList()` — Receives cycle identifiers from updated `/api/cycles`.
- `loadCycleDoc(id)` and `loadRounds(id)` — Pass identifiers. Server handles format resolution transparently.

**`ai_handoff/tui/handoff_reader.py`:**
- `find_cycle_doc()` — Returns format-aware result (JSONL path or legacy `.md` path)
- `extract_last_round()` — Uses `read_cycle_rounds()` dispatcher

**`ai_handoff/tui/review_replay.py`:**
- `build_review_replay()` — Uses `read_cycle_rounds()` dispatcher instead of direct `extract_all_rounds()`

**Both SKILL.md copies:**
- `.claude/skills/handoff/SKILL.md` — Updated instructions
- `ai_handoff/data/.claude/skills/handoff/SKILL.md` — Package-bundled copy, kept in sync

## Files to Modify/Create

| File | Action | Purpose |
|------|--------|---------|
| `ai_handoff/cycle.py` | Create | JSONL/JSON storage, CLI, discovery, render |
| `ai_handoff/cli.py` | Modify | Register `cycle` command |
| `ai_handoff/parser.py` | Modify | Add JSONL parser + format dispatcher |
| `ai_handoff/server.py` | Modify | Use cycle.py for discovery, status, rendering |
| `ai_handoff/data/web/app.js` | Modify | Format-agnostic cycle identifiers |
| `ai_handoff/tui/handoff_reader.py` | Modify | Use format dispatcher |
| `ai_handoff/tui/review_replay.py` | Modify | Use format dispatcher |
| `.claude/skills/handoff/SKILL.md` | Modify | CLI commands instead of manual edits |
| `ai_handoff/data/.claude/skills/handoff/SKILL.md` | Modify | Package-bundled copy |
| `tests/test_cycle.py` | Create | Unit tests for cycle module |
| `tests/test_parser.py` | Modify | Add JSONL parsing + dispatcher tests |

## Success Criteria
1. Agents update cycles with one CLI command (no Edit tool, no file reads)
2. Dashboard renders JSONL-backed cycles identically to legacy markdown
3. Legacy `_cycle.md` files still appear in dashboard and TUI
4. `cycle render` produces clean human-readable markdown from JSONL
5. All new code has unit test coverage
