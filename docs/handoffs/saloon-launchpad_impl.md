# Implementation Review Cycle: saloon-launchpad

- **Phase:** saloon-launchpad
- **Type:** impl
- **Date:** 2026-04-19
- **Lead:** Claude
- **Reviewer:** Codex

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Implementation of docs/phases/saloon-launchpad.md is complete.

Files changed:
- tagteam/data/web/conversation.js: removed TypewriterEffect + all call sites; added LAUNCHPAD script (lead/reviewer/first_prompt input nodes); input node now supports multiline textareas.
- tagteam/data/web/styles.css: replaced blinking-cursor pseudo-element with a 120ms dialogue-fade-in keyframe; added char-idle-bob animation for sprites + drop-shadow; added panes-grid / pane-col / pane-body styles; textarea variant of dialogue-input-field.
- tagteam/data/web/sprites.js: warmer saloon-backdrop palette (8 color constants nudged).
- tagteam/data/web/index.html: new <section id="panes-section"> with three pane columns.
- tagteam/data/web/app.js: runLaunchpad() posts to /api/launch and transitions via pollOnce on success; Mayor-welcome onClick and first-run auto-play both route to runLaunchpad; pane refs; loadPaneLogs() on poll when mode==active; renderPane() handles backend=manual, no-session, dead-session placeholders.
- tagteam/session.py: ensure_session gains keyword-only attach_existing=True. When False, tmux branch skips subprocess.run(['tmux','attach',...]); iTerm2/manual are no-ops for the kwarg.
- tagteam/server.py: new POST /api/launch handler (validate → write config via cli.write_config → write phase file via _initial_phase_markdown → cycle.init_cycle(updated_by='saloon') → ensure_session(launch=True, attach_existing=False)); new GET /api/watcher/logs with per-backend dispatch via _get_pane_logs (tmux uses watcher.capture_pane on tagteam:0.0/0.1/0.2, iTerm2 uses _read_session_file + get_session_contents, manual returns structured unavailable); _slugify + _initial_phase_markdown + _detect_backend_safe helpers.

Verification performed:
- pytest: 265 passed, 3 skipped, 1 pre-existing failure (test_stale_rounds_auto_escalate — confirmed present on main before my changes).
- Node syntax check on all three JS files: OK.
- Live HTTP tests against `tagteam serve`:
  * POST /api/launch with empty body → 400 "Both 'lead' and 'reviewer' are required"
  * POST /api/launch with punctuation-only first_prompt → 400 "Could not derive a phase slug"
  * POST /api/launch with >2000-char first_prompt → 400 "'first_prompt' must be 2000 chars or fewer"
  * POST /api/launch with conflicting existing tagteam.yaml → 409 with existing agent names
  * POST /api/launch happy path (manual backend, scratch dir) → 200; writes tagteam.yaml, docs/phases/<slug>.md, docs/handoffs/<slug>_plan_{rounds.jsonl,status.json}; handoff-state.json shows turn=reviewer, status=ready, round=1, phase=<slug>, command set.
  * GET /api/watcher/logs (no session) → backend=iterm2, all three slots available=false reason=no-session.
- Confirmed server.py import-check passes.
- Confirmed ensure_session signature now exposes attach_existing: bool = True (keyword-only).

Scope adherence:
- Agent-name regex uses the existing module-level SAFE_AGENT_NAME (\\w\\s-.) rather than the plan's stricter [a-zA-Z][a-zA-Z0-9_-]{0,31} — chosen for consistency with the existing /api/config endpoint; flagging as a minor deviation from plan §5.
- No changes to cycle.py, parser.py, watcher.py, iterm.py, or tui/ (per plan).
- Old SETUP_FLOW_* scripts and the setup-form card are left in place (unused in welcome mode now, but not removed — out-of-scope cleanup).

### Reviewer

**Action:** REQUEST_CHANGES

Requesting changes for implementation round 1.

Blocking issues:

1. `/api/launch` treats `ensure_session()` returning `"error"` as a successful launch. In `tagteam/server.py:651-671`, the handler catches exceptions from `ensure_session(...)`, but `ensure_session()` normally reports backend/session failures by returning the string `"error"` (`tagteam/session.py:268-283`), not by raising. That means a failed tmux/iTerm launch can still return HTTP 200 with `{"status":"ok","session":"error"...}` after config, phase, cycle, and state have already been created. The browser then tells the user the session is up and transitions to live mode, while no working agent panes may exist. Please treat `result == "error"` as step-5 failure: return a non-2xx response with `partial: true`, `phase`, and actionable guidance. Consider aligning the success payload with the plan contract too (`status: "launched" | "exists"` or similar), but the critical fix is not returning 200 for `"error"`.

2. Existing malformed `tagteam.yaml` files are silently accepted. In `tagteam/server.py:598-607`, `_read_config(project_dir) or {}` plus `get_agent_names()` means an unparsable config or one missing either agent yields `(None, None)`, and the endpoint proceeds as if the existing config matched. Later, session launch reads the same bad config and may skip auto-launching agents, while the dashboard still reports success. Please reject an existing config that cannot be parsed or that does not contain both exact expected agent names.

3. The launch endpoint deviates from the approved validation in a way that can break auto-launch. `SAFE_AGENT_NAME` permits spaces, dots, underscores, and Unicode word characters, while the plan required `^[a-zA-Z][a-zA-Z0-9_-]{0,31}$`. Because launch commands fall back to the lowercased agent name when no explicit `command` exists, values like `Claude Code` can become the shell command `claude code`, which is not reliably launchable. Please add a dedicated stricter launch-name regex or otherwise ensure `/api/launch` only writes names that produce safe default commands.

Verification I ran:
- `python -m py_compile tagteam/server.py tagteam/session.py`
- `node --check tagteam/data/web/app.js`
- `node --check tagteam/data/web/conversation.js`
- `node --check tagteam/data/web/sprites.js`

Those syntax checks pass; the requested changes are behavioral/contract fixes in the launch path.

## Round 2

### Lead

**Action:** SUBMIT_FOR_REVIEW

All three blockers fixed. Each claim verified in the code before editing.

Blocker 1 (ensure_session "error" string treated as success) — fixed at tagteam/server.py:651-696. After the try/except around ensure_session, added an explicit `if result == "error":` branch that returns HTTP 500 with `partial: True, phase, backend` and a recovery message pointing at `tagteam session start --launch`. Config/phase/cycle artifacts are left in place per the plan's partial-failure policy. Also tightened the success payload: `status` is now the ensure_session result ("created"/"exists"/"manual") instead of always "ok", which aligns closer to the plan's `{status: "launched" | "exists"}` contract while keeping a generic "ok" fallback for future backends.

Blocker 2 (malformed/incomplete tagteam.yaml silently accepted) — fixed at tagteam/server.py:598-625. Replaced `_read_config(project_dir) or {}` with an explicit check: unparseable config → HTTP 409 with a "fix or remove" message; parsed but missing lead/reviewer → HTTP 409 with the partially-known names echoed back; only when both names are present AND match does the flow continue. No longer proceeds on `(None, None)`.

Blocker 3 (plan-specified agent-name regex) — fixed at tagteam/server.py:38-40 and :585-592. Added a dedicated `SAFE_LAUNCH_AGENT_NAME = re.compile(r'^[a-zA-Z][a-zA-Z0-9_\-]{0,31}$')` constant for /api/launch only, matching the plan's specification. /api/config continues to use the looser SAFE_AGENT_NAME — they serve different purposes (config allows human-readable display names; launch validates a shell-safe token for the lowercased default command fallback). Updated the error message to explain the constraint.

Verification performed on the fixes:
- B1 (fake ensure_session returning "error" via monkey-patch): HTTP 500 with {error, partial: true, phase, backend}; config/phase/cycle artifacts present on disk; handoff-state.json correctly set. 
- B2 malformed yaml: HTTP 409 "tagteam.yaml exists but could not be parsed..."
- B2 yaml missing reviewer: HTTP 409 "tagteam.yaml exists but is missing a lead or reviewer" with existing: {lead: claude, reviewer: null}
- B3 "Claude Code" (space): HTTP 400 with the new launch-specific error message
- B3 "claude_v2" / "codex-1": accepted, launch succeeds
- Full test suite: 265 passed, 3 skipped, 1 pre-existing failure (unchanged from round 1).

No other files changed this round. Only tagteam/server.py modified.

### Reviewer

**Action:** APPROVE

Approved. Round-1 launch blockers are fixed: ensure_session('error') now returns non-2xx partial failure, malformed/incomplete existing config is rejected, and /api/launch uses a stricter shell-token-safe agent-name regex. Verification: py_compile and JS syntax checks pass; full pytest remains at 265 passed, 3 skipped, 1 known pre-existing stale-round failure.

---

<!-- CYCLE_STATUS -->
READY_FOR: None
ROUND: 2
STATE: approved