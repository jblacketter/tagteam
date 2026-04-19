# Phase: Saloon Launchpad — Graphical Onboarding & Live Handoff View

## Summary
Revive the web Saloon as the primary graphical entry point for new tagteam projects. Today `tagteam quickstart` in a terminal is the canonical first-run path; the Saloon web dashboard exists but "never quite worked" as an onboarding hub. This phase makes the Saloon the front door: pick Lead + Reviewer, enter the first phase prompt, and launch the 3-pane handoff (Lead / Watcher / Reviewer) without leaving the browser. It also kills the slow typewriter rendering so dialogue feels instantaneous, and polishes the pixel-art sprites.

## Motivation
Jack's observations, 2026-04-18:
1. Graphics could be nicer than the current look.
2. The Saloon should be a **graphical place to start** a tagteam project — select agents, write the first prompt, launch the 3 panes.
3. The Saloon must never get in the way. Rendering the conversation takes too long today; dialogue should appear **instantly**.

## Scope

### 1. Instant dialogue rendering
- Remove the per-character typewriter effect from `conversation.js`. Dialogue text renders in full as soon as the node is entered.
- Keep the conversation engine's state machine (advance button, choices, input nodes) — only the typing animation is removed.
- Remove the "click to skip" affordance (no longer needed) and associated tap/click handlers that only existed to skip typing.
- Keep a one-line CSS fade-in (≤120ms opacity) so text doesn't pop jarringly, but no per-character delay.

### 2. Sprite polish
- Refine the four SVG pixel sprites in `sprites.js` (Mayor, Bartender, Watcher, Clock): tighten palettes, add subtle 2-frame idle animation (breathing/bob) via CSS keyframes, sharpen outlines.
- Scope does **not** include replacing pixel-art style with higher-fidelity illustration. Same aesthetic, nicer execution.
- Backdrop: one pass on `banner-backdrop` CSS — clearer horizon line, softer gradient.

### 3. Onboarding launchpad flow
Replace the current thin "Setup" card with a guided first-run flow the user walks through while in the Saloon:

1. **Welcome** — Mayor greets, explains in two dialogue lines what tagteam does.
2. **Agents** — form collects Lead name and Reviewer name (existing fields, repositioned in the dialogue panel).
3. **First prompt** — new textarea: "What should your agents work on first?" This becomes the initial phase description.
4. **Launch** — single "Open the Saloon" button that hits `POST /api/launch`. The endpoint performs the steps listed in §5 in order (config → phase file → cycle init → non-attaching session launch). On success the browser transitions the Saloon from "welcome" to "live" mode.

After launch, the Saloon transitions from "welcome" mode to "live" mode (status + timeline + cycle view already implemented).

### 4. Live 3-pane view inside the Saloon
Add a new "panes" section to the dashboard that shows live log tails for Lead / Watcher / Reviewer while the handoff runs. This is a **view**, not a terminal emulator — it tails the real agent panes via a new server endpoint.

- New `GET /api/watcher/logs?n=50` endpoint. Response shape: `{"lead": {"content": "...", "available": true} | {"available": false, "reason": "..."}, "watcher": {...}, "reviewer": {...}, "backend": "tmux" | "iterm2" | "manual"}`.
- Backend dispatch:
  - **tmux**: `watcher.capture_pane("tagteam:0.0", n)` for Lead, `tagteam:0.1` for Watcher, `tagteam:0.2` for Reviewer (pane layout confirmed in `session.py:145-151`). If the session doesn't exist, each slot returns `available: false, reason: "no-session"`.
  - **iterm2**: read the session file; call `iterm.get_session_contents(session_id, n)` for each of the three recorded session IDs. Missing session file → `available: false, reason: "no-session"`. Dead session ID → `available: false, reason: "dead-session"`.
  - **manual**: all three slots `available: false, reason: "backend=manual"`.
- Frontend: three columns (mirrors tmux layout): Lead | Watcher | Reviewer. Each column shows the log content in a monospace scroll area, or a "Not live yet" / "Manual backend — panes are external" placeholder when unavailable. Auto-updates on the existing poll cadence (piggyback on the current polling loop; don't add a second timer).
- This is a **monitoring** view — actual agent interaction still happens in the real terminal panes launched by `session start`. The Saloon mirrors their output so the user can watch progress without switching apps.

### 5. Server endpoint for launch
`POST /api/launch` — body: `{ lead, reviewer, first_prompt }`. Performs a strict sequence and rolls back visible changes if a later step fails.

**Sequence:**
1. **Validate input.** `lead` and `reviewer` must match `^[a-zA-Z][a-zA-Z0-9_-]{0,31}$`. `first_prompt` is 1–2000 chars. Derive `slug` by lowercasing, replacing non-alphanumeric with `-`, collapsing repeats, trimming, and truncating to 40 chars; validate against `^[a-z0-9][a-z0-9-]*$`. Reject if any validation fails (`400` with structured error).
2. **Write `tagteam.yaml`.** If it exists with matching `lead`/`reviewer`, reuse. If it exists with different names, `400` with a clear message. If missing, call the programmatic config writer (use `tagteam.config` helpers where they exist; add a minimal `write_config(path, lead, reviewer)` helper only if no suitable function is present — inspect `tagteam/config.py` first and prefer reuse over duplication).
3. **Write `docs/phases/<slug>.md`.** Template: a simple Summary section containing the verbatim `first_prompt`, plus an empty Scope/Approach/Success Criteria scaffold so the Lead has structure to iterate on during plan review.
4. **Init cycle + state.** Call `tagteam.cycle.init_cycle(phase=slug, cycle_type="plan", lead=lead, reviewer=reviewer, content=<first-line-of-first_prompt>, updated_by="saloon")`. This atomically creates the cycle rounds file and sets `handoff-state.json` to `turn: reviewer, status: ready, phase: <slug>, type: plan, round: 1` — so when agents come up, the Reviewer is the first to act.
5. **Launch session (non-attaching).** Extend `tagteam.session.ensure_session` with a keyword-only `attach_existing: bool = True` parameter. When `False`, the tmux branch skips `subprocess.run(["tmux", "attach", ...])` on the existing-session path and just returns `"exists"`. The iTerm2 branch already doesn't attach (it creates a window); no change needed there beyond accepting the kwarg for symmetry. Call `ensure_session(project_dir, launch=True, attach_existing=False)` from the handler.
6. **Return.** `200 {"status": "launched"|"exists", "backend": "...", "phase": slug}` on success. On any step's failure, return the structured error; on step-5 failure after steps 2-4 succeeded, leave the config/phase/cycle artifacts in place (they're recoverable via CLI) but include `partial: true` in the response so the UI shows actionable guidance rather than silently spinning.

**Non-attaching primitive.** The `attach_existing=False` change is small and localized — it guards the single `tmux attach` call in `session.py:270-271`. No other backend needs behavioral changes. Confirm by grepping for `tmux attach` in the codebase before implementing.

### 6. Never-in-the-way principles (cross-cutting)
- No blocking modal during launch — use a small inline spinner on the launch button; the Saloon stays interactive.
- Polling cadence unchanged (existing exponential backoff from phase 18).
- Error banner (already from phase 18) is the channel for any launch/config errors.

## Technical Approach

### Files to modify
| File | Change |
|------|--------|
| `tagteam/data/web/conversation.js` | Remove `TypewriterEffect` class + all call sites. Render `node.text` immediately. Remove skip-typewriter click/tap handlers. |
| `tagteam/data/web/sprites.js` | Refine palettes and outlines for all four sprites. Add idle animation hooks. |
| `tagteam/data/web/styles.css` | Dialogue fade-in keyframe, sprite idle animations, backdrop polish, 3-column panes layout. |
| `tagteam/data/web/index.html` | Replace standalone setup card with dialogue-embedded onboarding flow; add panes section (hidden until live mode). |
| `tagteam/data/web/app.js` | New launch flow wiring; `POST /api/launch` integration; panes log tail polling; transition from welcome → live mode. |
| `tagteam/server.py` | New `POST /api/launch` and `GET /api/watcher/logs` handlers; input validation; backend dispatch for logs. |
| `tagteam/session.py` | Add `attach_existing: bool = True` kwarg to `ensure_session`; guard the `tmux attach` call at line 271. No other backend changes. |

### Files likely untouched
- `tagteam/cycle.py` — launch calls the existing `init_cycle()` public function; no changes needed.
- `tagteam/parser.py`, `tagteam/watcher.py` — `capture_pane` is reused as-is; no changes needed.
- `tagteam/iterm.py` — `get_session_contents` is reused as-is.
- `tagteam/tui/` — TUI is out of scope for this phase.

## Resolved questions (from reviewer round 1)
1. **Panes view scope** — confirmed log-tail only. Pty/terminal emulation is deferred to a separate phase.
2. **Sprite palette** — keep color values inline in the SVG strings; a palette-token refactor is not justified by the current sprite count.
3. **Config writing** — prefer reusing helpers in `tagteam/config.py` over duplicating `init` CLI behavior. If no suitable helper exists, add a minimal `write_config` function rather than invoking `init` non-interactively.

## Success Criteria
1. Dialogue text renders instantly; no per-character typing visible.
2. Pixel sprites show a subtle idle animation and look noticeably polished vs. current (eyeball check — acceptance is Jack's call).
3. A fresh project with no `tagteam.yaml` can be fully onboarded from the browser: open dashboard → walk through dialogue → click launch → 3 panes come up with agents running AND a plan cycle is live (reviewer turn, round 1, phase slug matches the user's first prompt).
4. Launch flow writes a valid `tagteam.yaml`, `docs/phases/<slug>.md`, and initializes `handoff-state.json` + cycle rounds via `cycle.init_cycle()`.
5. `POST /api/launch` does not block the HTTP handler on any backend. On tmux, even when the session already exists, the handler returns within a normal request timeout (no `tmux attach` invocation).
6. Invalid agent names, empty first prompt, or slug collisions produce a user-visible error without crashing the server. Partial-failure responses (`partial: true`) give the user actionable guidance.
7. Live mode shows three log-tail columns that update as the agents work on tmux and iTerm2 backends; on manual backend, columns show a clear "backend=manual" placeholder.
8. Existing handoff flows (manual CLI, TUI, quickstart) continue to work unchanged — no regressions. `ensure_session` without `attach_existing` keyword behaves identically to today.
9. Run against a fresh project dir to validate end-to-end on at least one of tmux or iTerm2.

## Out of scope
- Full pty/terminal emulation inside the browser.
- Replacing pixel art with a different art style.
- TUI changes.
- Windows launch path (iTerm2/tmux only — same constraint as current `session start`).
