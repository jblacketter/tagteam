# Phase: Dashboard & TUI Polish

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
**What:** Fix known TUI bugs, improve web dashboard parity with TUI features, extract shared code, and add basic test coverage for TUI modules.

**Why:** The TUI was ported in Phase 4 with known debt (GAMERFY naming, no tests, no end-to-end validation). The web dashboard duplicates logic but lacks TUI's richer features (dialogue replay, escalation choices, phase map). Phase 8 fixed orchestration, so TUI state updates can now be validated. This phase cleans up both interfaces and consolidates shared logic.

**Depends on:** Phase 8 (Orchestration Fix) — watcher reliability is prerequisite for TUI state update validation.

## Problem Statement

Three categories of issues identified by code analysis:

### 1. Known Debt from Phase 4 Consolidation
- `GAMERFY_SOUND` env var still used (`tui/__init__.py:36`, `tui/sound.py:14`) — should be `HANDOFF_SOUND`
- No unit tests for any TUI module (2,321 lines untested)
- README notes "TUI automated hand off with the saloon is not yet fully functional"

### 2. TUI Bugs & Fragility
- **State polling silent failures** (`app.py:125-134`): Read errors are silently ignored with no logging or stale indicator
- **Dialogue queue race condition** (`app.py:216-230`): Rapid state changes clear the queue mid-drain, dropping messages
- **Status bar overflow** (`status_bar.py:70-73`): Long action descriptions overflow the status line
- **Hardcoded round limit** (`status_bar.py:57`): Display shows `/5` regardless of config
- **No disconnected indicator**: If `handoff-state.json` disappears, UI shows no warning

### 3. Web Dashboard Gaps vs TUI
- No structured cycle document parsing (raw markdown only vs TUI's `extract_all_rounds()`)
- No escalation resolution flow (Escalate button only, no three-choice dialogue)
- No phase map visualization
- 46.5KB single HTML file mixing CSS, HTML, and 740 lines of JS
- No character dialogue rendering (static text boxes vs TUI's typed dialogue system)

## Scope

### In Scope

#### A. Bug Fixes & Cleanup
- Rename `GAMERFY_SOUND` → `HANDOFF_SOUND` (support both temporarily for backward compat)
- Add logging to state poller for consecutive read failures
- Add stale-state indicator to TUI status bar
- Fix status bar truncation for long action names
- Fix round display: show `Round N` instead of `N/5` (no max-round metadata exists in cycle docs; auto-escalation at round 5 is handled by the skill, not the display)
- Remove unused `canned_responses` field from `characters.py`

#### B. Shared Code Extraction
- Extract `handoff_reader.extract_all_rounds()` into `ai_handoff/parser.py` as shared module
- TUI and web dashboard both import from `parser.py`
- Add `format_rounds_html()` for web dashboard structured display

#### C. Web Dashboard Improvements
- Add `/api/rounds` endpoint returning structured cycle data (using shared parser)
- Add `/api/dialogue` endpoint returning character dialogue for current state
- Split `index.html` into `index.html` + `styles.css` + `app.js` (3 files)
- Add escalation choice flow to web dashboard (three buttons instead of just "Escalate")
- Add phase map display to web dashboard (reuse `map_data.py` logic)

#### D. Test Coverage
- Unit tests for `state_watcher.py` (change detection, fingerprinting)
- Unit tests for `handoff_reader.py` (parsing with edge cases, malformed docs)
- Unit tests for `review_dialogue.py` (chunking, dialogue building)
- Unit tests for shared `parser.py` module

### Out of Scope
- Framework migration (no React/Vue, no FastAPI/aiohttp)
- WebSocket real-time push (polling is sufficient for now)
- New ASCII art or character animations
- Sound effects in web dashboard
- Windows support
- TUI conversation engine refactor
- State validation consolidation (deferred to Phase 10 — currently split between `server.py` and `state.py` but both work independently)

## Technical Approach

### Step 1: Shared Parser Extraction
1. Create `ai_handoff/parser.py` with `extract_all_rounds()` and `format_rounds_html()`
2. Update `tui/handoff_reader.py` to import from `parser.py`
3. Update `server.py` to use `parser.py` for new `/api/rounds` endpoint
4. Write unit tests for `parser.py`

### Step 2: Bug Fixes
1. Rename `GAMERFY_SOUND` → `HANDOFF_SOUND` in `tui/__init__.py` and `tui/sound.py`, fall back to old name
2. Add `_consecutive_failures` counter to `app.py._poll_state()`, log after 3 failures
3. Add `[STALE]` indicator to `status_bar.py` when state read fails
4. Truncate status bar last-action to 25 chars with `...`
5. Fix round display: show `Round N` instead of `N/5`
6. Remove `canned_responses` from `Character` dataclass and instances

### Step 3: Web Dashboard
1. Split `index.html` into 3 files, update `server.py` to serve static files from `web/`
   - Update `pyproject.toml` package-data to include `*.css` and `*.js` under `ai_handoff/data/web/`
   - Update `MANIFEST.in` with `recursive-include ai_handoff/data/web *.css *.js`
2. Add `/api/rounds` endpoint → calls `parser.extract_all_rounds()`, returns JSON
3. Add `/api/dialogue` endpoint → calls `review_dialogue.build_state_dialogue()`, returns JSON
4. Update JS to render structured rounds instead of raw markdown
5. Add escalation choices UI (three buttons: "Agree with Lead", "Agree with Reviewer", "Defer")
6. Add phase map panel (call `/api/phases`, render as HTML table with status symbols)

### Step 4: Test Coverage
1. `tests/test_parser.py` — round extraction, malformed input, empty files
2. `tests/test_state_watcher.py` — fingerprint changes, state comparison
3. `tests/test_review_dialogue.py` — chunk splitting, dialogue building, edge cases

## Files to Create/Modify

### Create
- `ai_handoff/parser.py` — Shared cycle document parser
- `ai_handoff/data/web/styles.css` — Extracted CSS
- `ai_handoff/data/web/app.js` — Extracted JS
- `tests/test_parser.py` — Parser unit tests
- `tests/test_state_watcher.py` — State watcher unit tests
- `tests/test_review_dialogue.py` — Review dialogue unit tests

### Modify
- `ai_handoff/tui/__init__.py` — GAMERFY_SOUND → HANDOFF_SOUND
- `ai_handoff/tui/sound.py` — GAMERFY_SOUND → HANDOFF_SOUND (with fallback)
- `ai_handoff/tui/app.py` — State polling logging, stale indicator
- `ai_handoff/tui/status_bar.py` — Truncation fix, round display fix, stale badge
- `ai_handoff/tui/handoff_reader.py` — Import from shared parser
- `ai_handoff/tui/characters.py` — Remove unused canned_responses
- `ai_handoff/server.py` — New endpoints, serve static files (CSS, JS)
- `ai_handoff/data/web/index.html` — Extract CSS/JS, add escalation + map UI
- `pyproject.toml` — Add `*.css` and `*.js` to `ai_handoff/data/web/` package-data
- `MANIFEST.in` — Add `recursive-include ai_handoff/data/web *.css *.js`
- `docs/roadmap.md` — Update Phase 9 status

## Success Criteria
- [ ] `GAMERFY_SOUND` renamed to `HANDOFF_SOUND` (old name still works as fallback)
- [ ] Shared `parser.py` used by both TUI and web dashboard
- [ ] Web dashboard displays structured rounds (not raw markdown)
- [ ] Web dashboard has escalation choice flow (3 options)
- [ ] Web dashboard shows phase map with status indicators
- [ ] Frontend split into 3 files (HTML, CSS, JS)
- [ ] TUI state poller logs failures and shows stale indicator
- [ ] Status bar handles long action names without overflow
- [ ] 3+ new test files with coverage for parser, state watcher, review dialogue
- [ ] All existing tests still pass

## Risks
- **Shared parser extraction** may break TUI if import paths change. Mitigation: Keep `handoff_reader.py` as thin wrapper.
- **Frontend split** changes static file serving in `server.py`. Mitigation: Test `serve` command manually.
- **Escalation flow in web** needs state update logic. Mitigation: Reuse existing `/api/state` POST endpoint.
