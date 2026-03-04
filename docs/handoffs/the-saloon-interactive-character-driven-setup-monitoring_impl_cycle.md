# Implementation Review Cycle: The Saloon — Interactive Character-Driven Setup & Monitoring

- **Phase:** the-saloon-interactive-character-driven-setup-monitoring
- **Type:** impl
- **Date:** 2026-03-03
- **Lead:** Claude
- **Reviewer:** Codex

## Reference

Phase plan: `docs/phases/saloon-interactive-setup.md`

## Implementation Summary

### Modified Files

1. **`ai_handoff/data/web/sprites.js`** — New Watcher character
   - Added Watcher color constants (deputy/ranger theme: brown hat, green vest, brass star)
   - Added `watcherPixels(state)` — 12×18 pixel art with state-dependent color overrides
   - Added `watcherPortraitPixels()` — 8×10 portrait for dialogue panel
   - Added `renderWatcher(state)` public function
   - Updated `renderPortrait()` to handle 'watcher' and 'bartender' speakers
   - Exported `renderWatcher` in public API

2. **`ai_handoff/data/web/index.html`** — Banner layout
   - Added `<div class="banner-char banner-watcher" id="banner-watcher">` after Rabbit
   - Added `title="Click the Bartender"` to Rabbit element
   - Added `title="Click the Watcher"` to Watcher element

3. **`ai_handoff/data/web/styles.css`** — Character interactivity
   - Added `.banner-rabbit` and `.banner-watcher` cursor/hover styles
   - Added `.char-glow` generic glow animation for guiding users between characters
   - Migrated `.mayor-glow` to use shared `char-glow-pulse` keyframes
   - Adjusted banner gap from 60px → 40px to fit 4 elements
   - Updated responsive breakpoints for 4-element banner

4. **`ai_handoff/data/web/conversation.js`** — Multi-character dialogue
   - Added `SETUP_FLOW_MAYOR` script (welcomes user, collects Lead agent name)
   - Added `SETUP_FLOW_BARTENDER` script (collects Reviewer agent name)
   - Added `SETUP_FLOW_WATCHER` script (offers tmux setup, explains daemon)
   - Added `WATCHER_MONITORING` and `WATCHER_TURN_CHANGE` transition templates
   - Exported all new scripts

5. **`ai_handoff/data/web/app.js`** — Character interactions & setup flow
   - Added `bannerWatcher` DOM ref and `setupState` tracking object
   - Added `setCharGlow(charName)` / `clearAllGlows()` glow management
   - Updated `updateBannerCharacters()` to render Watcher sprite
   - Refactored `onMayorClick()` for multi-character setup flow
   - Added `onRabbitClick()` — setup flow step, idle menu (review history, explain reviews), active status
   - Added `onWatcherClick()` — setup flow step, idle menu (watcher status, start session), active status
   - Added `fetchWatcherStatus()` API helper
   - Updated `updateVisibility()` glow logic for setup guidance
   - Updated `init()` to auto-start multi-character setup flow in welcome mode

6. **`ai_handoff/server.py`** — Monitoring API
   - Added `_get_watcher_status()` — checks for running watcher daemon via `pgrep`
   - Added `_get_session_status()` — checks for tmux session via `tmux list-sessions`
   - Added `GET /api/watcher/status` endpoint
   - Added `GET /api/session/status` endpoint

### Setup Flow

The guided multi-character setup replaces the old single-character flow:

1. Page loads → Mayor auto-greets, glows, collects Lead agent name
2. Mayor says "Go talk to the Bartender" → Bartender glows
3. User clicks Bartender → collects Reviewer name → config saved
4. Bartender says "Talk to the Watcher" → Watcher glows
5. User clicks Watcher → offers tmux setup choice → setup complete

### Key Decisions

- **Watcher design:** Deputy/ranger theme with wide-brim hat, forest green vest, brass star badge
- **Glow system:** Generic `.char-glow` CSS class applied/removed by JS, reusable across all characters
- **Setup state:** Tracked in JS `setupState` object (step, leadName, reviewerName, wantsTmux)
- **Config persistence:** Config saved after Bartender step (when both names are collected), Watcher step is optional
- **Watcher API:** Uses `pgrep` and `tmux list-sessions` for lightweight daemon/session detection

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Implementation complete for Phase 11. All 6 files modified, all JS and Python syntax verified, existing tests pass (51 passed, 1 pre-existing failure unrelated to changes).

### Reviewer

**Action:** REQUEST_CHANGES

Good progress on character integration and baseline APIs, but there are workflow-critical gaps against the approved phase scope.

1. **Setup flow does not reliably include the Watcher step**
   - In `onRabbitClick()`, the Bartender completion callback calls `submitSetupFromDialogue(...)` before/during the handoff to Watcher (`ai_handoff/data/web/app.js:296-302`).
   - On successful config save, `agentConfig` is set (`app.js:505`), so `getMode()` returns `idle` instead of `welcome` (`app.js:155-158`).
   - `onWatcherClick()` only runs the setup script when `mode === 'welcome'` and `setupState.step === 'watcher'` (`app.js:358-366`), so the intended guided sequence can be skipped/broken.

2. **Setup-resume persistence is missing**
   - `setupState` exists only in-memory (`app.js:72-77`) and is reinitialized on reload.
   - There is no persistence layer (e.g., localStorage/server) and no restore path in `init()`, so the requirement to resume mid-setup after closing/reopening the browser is not met.

3. **Watcher does not show daemon/session status in active handoff mode**
   - Active Watcher click path returns static text only (`app.js:406-413`).
   - `fetchWatcherStatus()` only calls `/api/watcher/status` (`app.js:477`) and does not include `/api/session/status`.
   - Backend adds `/api/session/status` (`ai_handoff/server.py:273-275`) but frontend does not consume it, so daemon/session visibility is incomplete.

Requested fix direction:
- Make the guided setup deterministic: Mayor → Bartender → Watcher, then complete.
- Persist setup progress and restore it on reload.
- In Watcher active interactions, fetch and present both watcher daemon and session status.

## Round 2

### Lead

**Action:** SUBMIT_FOR_REVIEW

Addressed all 3 reviewer issues in `app.js`:

**1. Setup flow determinism** — Click handlers now check `setupState.step` BEFORE `getMode()`. The Bartender and Watcher setup scripts fire based on setup state, not mode, so config being saved (changing mode to `idle`) no longer breaks the flow. Added `saveConfigSilent()` for the Bartender step to save config without interrupting the guided sequence. Mayor click redirects to the correct character if setup is mid-flow.

**2. Setup-resume persistence** — Added `persistSetupState()`, `restoreSetupState()`, and `clearSetupState()` using `localStorage` (key: `ai-handoff-setup`). Setup state is persisted after each step transition. On `init()`, if saved setup state exists with an incomplete step, the app restores it and glows the correct character. Completed state is cleared.

**3. Watcher active mode** — Replaced `fetchWatcherStatus()` with `fetchWatcherAndSessionStatus()` that calls both `/api/watcher/status` and `/api/session/status` via `Promise.all()`, then displays combined daemon PID + tmux session info. Used in both active mode and idle "Watcher status" menu.

All JS syntax verified. No new files added.

### Reviewer

**Action:** REQUEST_CHANGES

Most of the requested fixes landed, but one blocking setup-resume bug remains:

1. **Setup resume still fails in welcome mode (pre-config mid-flow)**
   - In `init()`, the condition is:
     - `if (mode === 'welcome' || (restoreSetupState() && setupState.step !== 'complete'))`
   - Because `||` short-circuits, when `mode === 'welcome'` is true, `restoreSetupState()` is never called.
   - Result: if a user closes/reopens mid-flow before config is saved (for example after Mayor step, waiting on Bartender), the saved localStorage step is not restored and flow restarts from Mayor.
   - Reference: `ai_handoff/data/web/app.js:1081`.

Requested fix:
- Always call `restoreSetupState()` first, then branch using restored state + mode.
- Verify resume works for both:
  - `step: bartender` while still in `welcome` mode
  - `step: watcher` after config save (`idle` mode)

What looks good in this round:
- Watcher flow gating now keys off `setupState.step` first.
- Active/idle Watcher status now uses both `/api/watcher/status` and `/api/session/status`.

## Round 3

### Lead

**Action:** SUBMIT_FOR_REVIEW

Fixed the setup-resume short-circuit bug in `init()` (`app.js`):

- `restoreSetupState()` is now called unconditionally **before** checking `getMode()`
- Logic branches on `inSetupFlow` (restored + incomplete step) first, then `mode === 'welcome'` for fresh start, then clears stale state
- Covers both reviewer-requested scenarios:
  - `step: bartender` in `welcome` mode (pre-config) — restores and glows Bartender
  - `step: watcher` in `idle` mode (post-config) — restores and glows Watcher
  - `step: mayor` resume — restores and glows Mayor

JS syntax verified.

### Reviewer

**Action:** APPROVE

Approved.

Round 2 blocker is resolved:
- `restoreSetupState()` is now called before mode branching in `init()`.
- Resume logic now correctly branches through `inSetupFlow`, avoiding the prior short-circuit.
- Restored-step handling covers `bartender`, `watcher`, and `mayor`.

Previously requested monitoring behavior remains in place:
- Watcher status path uses both `/api/watcher/status` and `/api/session/status`.

---

<!-- CYCLE_STATUS -->
READY_FOR: lead
ROUND: 3
STATE: approved
