# Implementation Review: web-dashboard-redesign

- **Phase:** web-dashboard-redesign
- **Type:** impl
- **Date:** 2026-02-17
- **Lead:** Claude
- **Reviewer:** Codex

## Reference
- Phase plan: `docs/phases/web-dashboard-redesign.md`
- Implementation files:
  - `ai_handoff/data/web/sprites.js` (NEW — 12.8KB, pixel art foundation)
  - `ai_handoff/data/web/conversation.js` (NEW — 19.5KB, dialogue engine)
  - `ai_handoff/data/web/index.html` (REWRITTEN — full-width flow layout)
  - `ai_handoff/data/web/styles.css` (REWRITTEN — modern layout + animations)
  - `ai_handoff/data/web/app.js` (REFACTORED — sprites + dialogue integration)
  - `ai_handoff/server.py` (MINOR — added .svg content type)
  - `MANIFEST.in` (MINOR — added *.svg glob)

---

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

**Summary of implementation:**

#### New: `sprites.js` (12.8KB)
- `pixelsToSVG(pixels, pixelSize, id, className)` — shared renderer converting 2D hex color arrays to inline SVG strings
- Character pixel maps: Mayor (12x18), Rabbit (12x18), Clock (8x10), Saloon backdrop (40x12), Portraits (8x10)
- State-reactive renderers: `renderMayor(state)`, `renderRabbit(state)`, `renderClock(state)` swap colors based on status (approved=green, escalated=red, working=blue, aborted=gray)
- Pendulum SVG with CSS animation, cuckoo bird SVG for pop-out effect
- All pixel art uses short color constants (e.g., `const M_HAT = '#2a1a0a'`), null = transparent

#### New: `conversation.js` (19.5KB)
- **`ConversationEngine`** — state machine ported from `ai_handoff/tui/conversation.py`. Indexes nodes by `id`, walks via `advance()`, supports dialogue/choice/input node types
- **`TypewriterEffect`** — 25ms/char typing with skip-on-click and completion callback
- **`DialogueController`** — manages dialogue panel DOM: portrait SVG, speaker name, typewriter text, choice buttons, text input. Supports `playScript()` for full conversation trees and `playLines()` for simple sequences
- **Dialogue scripts** — `INTRO` (10 nodes, 3-branch welcome conversation) and `SETUP_INTRO` (4 nodes, collects lead/reviewer names via input nodes)
- **Transition templates** — all 13 categories from `conversations/transitions.py` (MAYOR_HANDOFF, RABBIT_FEEDBACK, RABBIT_APPROVE, etc.) with random selection via `pickTransition()`
- **`buildStateDialogue(state, prevState)`** — generates dialogue lines from state transitions for auto-playing on poll updates

#### Rewritten: `index.html`
- Full-width flow layout replacing 2-column sidebar
- `<header class="saloon-banner">` — pixel art scene with backdrop + positioned characters + title
- `<section class="dialogue-panel">` — RPG dialogue box (portrait | speaker+text | advance button)
- `<section class="status-section">` — card grid with setup, phase, status, controls, phase map
- Timeline and cycle sections unchanged structurally
- Three `<script>` tags load sprites → conversation → app in order

#### Rewritten: `styles.css` (17.2KB)
- Banner: 200px tall, dark gradient, absolute-positioned characters, responsive at 768px/480px
- Card grid: `grid-template-columns: repeat(auto-fit, minmax(280px, 1fr))`
- Dialogue panel: full-width, flexbox (portrait 60px | body flex-1 | advance button), slide-in animation
- 6 `@keyframes` animations: pendulum-swing, mayor-pulse, cursor-blink, advance-pulse, dialogue-slide-in, cuckoo via class toggle
- `image-rendering: pixelated; image-rendering: crisp-edges` on all sprite SVGs
- Removed all old ASCII art classes (`.saloon-art`, `.saloon-wrapper`, `.ch-*`, `.mayor-dialogue`, `.mayor-hitzone`)

#### Refactored: `app.js` (26.3KB)
- **Removed** (~140 lines): `SALOON_LINES` array, `renderSaloon()`, `animatePendulum()`, `applySaloonState()`, old `showDialogue()`/`closeDialogue()`/mayor dialogue popup functions
- **Added**: `renderBanner()` initializes pixel art scene, `updateBannerCharacters(status)` swaps sprite colors per state, `triggerCuckoo()` for transition pop-outs
- **Added**: `DialogueController` integration — Mayor click plays context-appropriate scripts (SETUP_INTRO for welcome, idle menu for idle, status info for active)
- **Added**: `submitSetupFromDialogue()` — handles setup via dialogue input nodes instead of sidebar form
- **Added**: `playStateDialogue()` — auto-plays transition dialogue on state changes detected in poll loop
- **Kept** (updated DOM refs): mode computation, visibility management, all form submissions, all control actions (approve/changes/escalate/abort), escalation choices, timeline rendering, cycle viewer, phase map, 2s polling

#### Minor: `server.py`
- Added `".svg": "image/svg+xml"` to `_CONTENT_TYPES` dict (line 51)

#### Minor: `MANIFEST.in`
- Added `*.svg` to web includes glob

**Verification:**
- All 3 JS files pass Node.js syntax check (`new Function(...)`)
- `server.py` imports cleanly
- Existing test suite: 28 passed, 1 pre-existing failure (unrelated `test_stops_after_finding_both`)
- No TUI code modified, no API endpoints changed, no pyproject.toml changes needed

### Reviewer

**Action:** REQUEST_CHANGES

Good visual and structural upgrade overall, but there are blocking behavior regressions in the new dialogue flows:

1. **Cancel still overwrites config in setup dialogue flow**
   - In `submitSetupFromDialogue()`, the `onComplete` callback always calls `submitSetupOverwrite(...)`, even when the user selected `Cancel`.
   - Reference: `ai_handoff/data/web/app.js:296`, `ai_handoff/data/web/app.js:315`.

2. **Cancel still overwrites config in setup form fallback**
   - In `submitSetup()`, the overwrite confirmation script has both choices ending with `next: null`, and `onComplete` unconditionally calls `submitSetupOverwrite(...)`, so `Cancel` still overwrites.
   - Reference: `ai_handoff/data/web/app.js:381`, `ai_handoff/data/web/app.js:393`.

3. **Idle dialogue always opens the phase form**
   - In `onMayorClick()` idle mode, the callback always sets `activeForm = 'phase'` after script completion, even for `How it works` or `Never mind`.
   - Reference: `ai_handoff/data/web/app.js:231`.

Please gate follow-up actions on the actual selected branch (or store explicit choice outcomes in the dialogue engine callbacks) so cancel/info-only paths do not trigger phase/config mutations.

Verification notes:
- JS syntax checks pass for `sprites.js`, `conversation.js`, and `app.js`.
- Test suite currently shows 1 pre-existing unrelated failure in `tests/test_migrate.py::TestDetectAgentNames::test_stops_after_finding_both`.

---

## Round 2

### Lead

**Action:** SUBMIT_FOR_REVIEW (addressing feedback)

All three bugs fixed:

**1. Cancel still overwrites config in setup dialogue flow (Bug #1)**
- `submitSetupFromDialogue()` overwrite callback now checks `lastNodeId === 'do_overwrite'` before calling `submitSetupOverwrite()`. Cancel path (which ends with `next: null` from the choice node) gets `lastNodeId === 'overwrite_q'`, so it does nothing.

**2. Cancel still overwrites config in setup form fallback (Bug #2)**
- Added a `ow_confirm` dialogue node after the "Overwrite" choice (was previously `next: null`). Cancel still ends at `ow` node. Callback gates on `lastNodeId === 'ow_confirm'`.

**3. Idle dialogue always opens the phase form (Bug #3)**
- `onMayorClick()` idle mode callback now checks `lastNodeId === 'start_phase'` before setting `activeForm = 'phase'`. "How it works" ends at `how_rabbit` and "Never mind" ends at `idle_menu` — neither triggers form open.

**Underlying mechanism:** Added `lastNodeId` tracking to `ConversationEngine` — set on every `advance()` call, passed as second argument to `onComplete(inputs, lastNodeId)`. Propagated through `DialogueController.playScript()`.

**Verification:**
- All 3 JS files pass syntax check
- Test suite: 28 passed, 1 pre-existing failure (unrelated)

### Reviewer

**Action:** APPROVE

Approved.

The three blocking issues from Round 1 are fixed:
- Overwrite-on-cancel is now gated by `lastNodeId` in `submitSetupFromDialogue()` (`do_overwrite` path only).
- Overwrite-on-cancel in manual setup fallback is now gated by `lastNodeId` with explicit `ow_confirm` path.
- Idle mayor dialogue now opens the phase form only when `lastNodeId === 'start_phase'`.

Validation notes:
- `pytest -q` still shows the same unrelated pre-existing failure in `tests/test_migrate.py::TestDetectAgentNames::test_stops_after_finding_both`.
- JS syntax checks pass for `ai_handoff/data/web/sprites.js`, `ai_handoff/data/web/conversation.js`, and `ai_handoff/data/web/app.js`.

---

## CYCLE_STATUS
- READY_FOR: lead
- ROUND: 2
- STATE: approved
