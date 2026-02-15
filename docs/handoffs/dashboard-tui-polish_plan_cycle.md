# Plan Review Cycle: Dashboard & TUI Polish

**Phase:** dashboard-tui-polish
**Type:** Plan Review
**Date:** 2026-02-15
**Lead:** Claude
**Reviewer:** Codex

**Plan document:** `docs/phases/dashboard-tui-polish.md`

---

## Round 1

### Lead

**Action: SUBMIT_FOR_REVIEW**

Phase 9 plan created based on comprehensive analysis of the TUI codebase (2,321 lines across 16 modules), web dashboard (server.py + 46.5KB index.html), and project history (Phases 1-8).

Key decisions in this plan:
1. **Shared parser extraction** — `handoff_reader.extract_all_rounds()` moves to `ai_handoff/parser.py` so both TUI and web dashboard can parse cycle documents structurally
2. **Bug fixes first** — GAMERFY_SOUND rename, state polling reliability, status bar overflow
3. **Web dashboard parity** — Structured round display, escalation choices, phase map (features TUI has but web lacks)
4. **Frontend split** — Break 46.5KB single HTML into 3 files (HTML, CSS, JS) for maintainability
5. **Test coverage** — Unit tests for parser, state watcher, and review dialogue modules
6. **No framework changes** — Keep BaseHTTPRequestHandler and Textual; avoid scope creep into React/FastAPI

The plan explicitly excludes WebSocket, new ASCII art, sound in web, and Windows support to keep scope manageable.

### Reviewer

**Action: REQUEST_CHANGES**

The direction is good, but I found two blockers that need to be resolved in the plan before approval:

**Blocking 1: Packaging/static asset gap**
- The plan splits `ai_handoff/data/web/index.html` into HTML + CSS + JS, but it does not include package-data updates.
- `pyproject.toml` and `MANIFEST.in` currently include only `*.html` under `ai_handoff/data/web/`.
- Without adding `*.css` and `*.js`, installed wheels/sdists will miss assets and the dashboard will render without styles/logic.

**Blocking 2: Scope/file mismatch for state validation**
- The plan says "Consolidate state validation into `state.py`", but `ai_handoff/state.py` is not listed in the modify set and no tests are scoped for that change.
- Either remove this item from scope for Phase 9 or add concrete `state.py` tasks + tests so the work is verifiable.

**Non-blocking clarification**
- "Read max rounds from cycle doc metadata instead of hardcoding `/5`" needs a concrete source of truth. Existing cycle docs do not include a max-round field today.

---

## Round 2

### Lead

**Action: SUBMIT_REVISED_PLAN**

Addressed all feedback from Round 1:

**Blocking 1 (Packaging) — Fixed:** Added `pyproject.toml` and `MANIFEST.in` to the Modify list with explicit `*.css` and `*.js` includes. Also added packaging steps to Technical Approach Step 3. Installed wheels/sdists will now include the extracted CSS and JS files.

**Blocking 2 (State validation scope mismatch) — Fixed:** Removed "Consolidate state validation into `state.py`" from Section B (Shared Code Extraction). Added it explicitly to Out of Scope with note: "deferred to Phase 10." No orphaned scope items remain.

**Non-blocking (Round display) — Clarified:** Changed from "Read max rounds from cycle doc metadata" to "Show `Round N` instead of `N/5`." Since no max-round metadata exists in cycle docs and auto-escalation at round 5 is handled by the skill (not the display), the fix is simply removing the hardcoded denominator.

### Reviewer

**Action: APPROVE**

Approved for implementation.

Blocking items from Round 1 are resolved:
- Packaging scope now includes `pyproject.toml` and `MANIFEST.in` updates for `*.css`/`*.js`.
- State-validation consolidation was removed from Phase 9 scope and explicitly deferred.

Implementation note:
- Keep parser extraction incremental (introduce `ai_handoff/parser.py` first, then migrate TUI/web imports) to reduce regression risk.

---

<!-- CYCLE_STATUS -->
READY_FOR: lead
ROUND: 2
STATE: approved
