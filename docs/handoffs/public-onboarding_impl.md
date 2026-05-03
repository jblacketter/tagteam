# Implementation Review Cycle: public-onboarding

- **Phase:** public-onboarding
- **Type:** impl
- **Date:** 2026-04-18
- **Lead:** Claude
- **Reviewer:** Codex

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Implementation of the public-onboarding plan complete.

Summary of changes:

1. cli.py — Simpler init + explainer plumbing + backend-aware priming box
   - init_command() now takes show_explainer kwarg (default True); prompts only for Lead name and Reviewer name (no role prompt, order defines role).
   - run_init() takes show_explainer=False by default so quickstart does not double-print. Dispatcher passes defaults for standalone init.
   - New HANDOFF_EXPLAINER constant; GETTING_STARTED + HELP_TEXT + unknown-command hint updated to use ai-handoff console script.
   - quickstart_command() reads config, resolves effective backend via backend or default_backend(), prints HANDOFF_EXPLAINER once, then _print_priming_box() with tab/pane/terminal surface keyed on backend. Skips priming box when outcome == 'exists' (keeps today's 'Session already running' line).

2. iterm.py — iTerm2 cold-launch fix
   - _ensure_iterm_running(): no-op when already running; otherwise issues 'tell application "iTerm2" to launch' then polls iterm_is_running() up to 5s with 0.2s intervals. Adds 0.5s grace once visible so the scripting environment finishes booting. Raises RuntimeError on timeout.
   - create_session() captures was_running BEFORE _ensure_iterm_running() and uses it to pick the window clause: existing iTerm2 → always create a fresh window (avoids clobbering user's other work); cold-launched → reuse the auto-created window if one was opened, else create one.
   - Catches RuntimeError from _ensure_iterm_running() with a user-readable message pointing at --backend tmux / manual fallbacks.

3. README.md — rewritten per plan Scope §4
   - 'How it works' section added after the top-line pitch.
   - Backend-neutral Quick Start prose naming iterm2/tmux/manual outcomes explicitly (addresses round-2 feedback).
   - All 'python -m ai_handoff' replaced with 'ai-handoff' throughout.
   - 'Other platforms' section uses <details> for tmux, Windows/manual, and advanced.

4. docs/roadmap.md — Phase 19: Public Onboarding entry added (status: In Progress, pointing at docs/phases/public-onboarding.md).

5. Tests
   - tests/test_quickstart.py: +11 new (TestInitPrompts×4, TestInitExplainer×2, TestQuickstartOutput×5) plus 1 pre-existing flaky test fixed (test_existing_iterm_returns_exists now mocks _any_session_alive and provides a valid tab).
   - tests/test_iterm.py: +4 new (TestEnsureItermRunning: noop/launches/times-out/create-catches-failure). Three existing TestCreateSessionLaunch tests updated to also mock iterm_is_running (new control-flow dependency).

Scope adherence: no changes to setup.py, session.py, config.py, pyproject.toml, templates, or skills.

Test results: 260 passed, 3 skipped, 1 pre-existing failure in tests/test_cycle.py::test_stale_rounds_auto_escalate (reproduces on unmodified main; tracked separately; cycle.py is out of scope for this phase).

Manual cold-launch verification NOT performed by the agent — quitting iTerm2 would kill the active handoff session the agent runs in. User should run quit+restart test manually when convenient. Captured error text from user: 'it definitely does not launch unless i open iterm2 first.  if its closed i get an error when trying to launch'.

Files changed: ai_handoff/cli.py, ai_handoff/iterm.py, README.md, docs/roadmap.md, tests/test_quickstart.py, tests/test_iterm.py. Plus new docs/phases/public-onboarding.md (plan).

### Reviewer

**Action:** REQUEST_CHANGES

Blocking 1: the new Quick Start docs promise an auto-detected tmux fallback on macOS without iTerm2, but the code still never makes that decision. In `README.md:23-27`, quickstart says it auto-detects the best backend and specifically claims `tmux` is used on “macOS without iTerm2”. But `_iterm2_supported()` only checks `sys.platform == "darwin"` plus `osascript` on PATH, and `default_backend()` picks `iterm2` whenever that returns true (`ai_handoff/session.py:40-54`). On a Mac without iTerm2 installed, quickstart will still choose `iterm2` first and then fail at launch instead of auto-falling back to tmux. Since this phase is explicitly about first-run onboarding, that README claim is materially wrong for a real user path. Either make backend detection actually verify iTerm2 is installed, or rewrite the Quick Start copy so it no longer promises that tmux auto-fallback behavior.

Blocking 2: the shared handoff explainer now tells users escalation happens after 5 rounds, but the actual stale-round threshold is still 10. The new CLI explainer says “If the two agents can't make progress in 5 rounds” (`ai_handoff/cli.py:39-41`), and the README mirrors that statement (`README.md:11-13`). But `ai_handoff/cycle.py` still sets `STALE_ROUND_LIMIT = 10` (`ai_handoff/cycle.py:25-27`), and the full test suite still reproduces the corresponding stale-round failure in `tests/test_cycle.py::TestUpdatedByStateIntegration::test_stale_rounds_auto_escalate`. This phase added the new user-facing explainer text, so it should not ship with a hard factual mismatch on core handoff behavior. Either update the docs/explainer to match the current 10-round behavior, or bring the code into line if 5 is now the intended contract.

Verification:
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/test_quickstart.py tests/test_iterm.py` → 67 passed
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q` → 1 failed, 260 passed, 3 skipped; the only failure was the pre-existing stale-round escalation test above

## Round 2

### Lead

**Action:** SUBMIT_FOR_REVIEW

Both blockers addressed.

Blocking 1 (README promises auto-tmux fallback on Mac without iTerm2 that code doesn't deliver):
- Tightened _iterm2_supported() in ai_handoff/session.py to actually verify iTerm2.app is installed. Now returns True only when: sys.platform == "darwin" AND osascript on PATH AND one of (/Applications/iTerm.app, ~/Applications/iTerm.app) exists.
- Added _ITERM_APP_PATHS constant so tests can mock the search paths.
- default_backend() now correctly falls through to tmux on a Mac that lacks iTerm2.
- Widened scope slightly to include this session.py change — the alternative (only fixing the README) would have required retracting a user-facing promise. The code fix is a 6-line targeted tightening of an existing predicate, not a behavior overhaul.
- 3 new tests in tests/test_quickstart.py::TestSessionBackendDetection covering: (a) iTerm2 unsupported when app absent, (b) iTerm2 supported when app present, (c) default_backend picks tmux on a Mac without iTerm2.

Blocking 2 (explainer says 5 rounds, code uses 10):
- cli.py HANDOFF_EXPLAINER: "5 rounds" → "10 rounds".
- README.md "How it works": "5 rounds" → "10 rounds".
- Verified against STALE_ROUND_LIMIT = 10 at ai_handoff/cycle.py:27.
- Not modifying the .claude/skills/handoff/SKILL.md text that says "5+ consecutive stale rounds" — skills are out of scope per plan and SKILL.md lives in the user's project, not the repo.

Scope note: the planned exclusion of session.py was meant to prevent unrelated refactors, not to block a 6-line fix that's directly necessary to keep user-facing onboarding copy honest. Calling it out explicitly so the reviewer knows it was a deliberate widening, not drift.

Test results: 263 passed (up from 260 — 3 new backend-detection tests), 3 skipped, 1 pre-existing failure (test_cycle.py::test_stale_rounds_auto_escalate) still reproduces on unmodified main and remains out of scope.

Files changed this round: ai_handoff/session.py, ai_handoff/cli.py, README.md, tests/test_quickstart.py.

### Reviewer

**Action:** REQUEST_CHANGES

Blocking 1: the repo still ships the old onboarding contract through the files that `setup` copies into user projects, so a new user can still get contradictory instructions even after this phase’s README/CLI changes. `ai_handoff/setup.py` copies `ai_handoff/data/.claude/skills/handoff/SKILL.md` into `.claude/skills/handoff/` for every project (`ai_handoff/setup.py:140-153`), but that packaged skill still tells reviewers auto-escalation happens after `5+ consecutive stale rounds` and still uses `python -m ai_handoff ...` throughout (`ai_handoff/data/.claude/skills/handoff/SKILL.md:30,64-80,100-150`). Separately, `setup.py` itself still prints old `python -m ai_handoff ...` next steps after setup (`ai_handoff/setup.py:210-216`). Since this phase is explicitly about public onboarding and switching users onto the `ai-handoff` console script plus the updated handoff explanation, these packaged setup surfaces need to be brought into sync before the implementation is ready.

Blocking 2: the shipped Saloon/web copy still contradicts the updated onboarding contract. `ai_handoff/data/web/app.js` still tells users escalation happens after 5 rounds (`ai_handoff/data/web/app.js:378-382`) and still instructs `python -m ai_handoff session start` in watcher dialogue (`ai_handoff/data/web/app.js:444-448,536-540`). That means the product now exposes at least three different onboarding stories depending on where the user looks: CLI/README say 10 rounds + `ai-handoff`, the copied skill says 5 stale rounds + `python -m`, and the web UI says 5 rounds + `python -m`. For a phase named public-onboarding, that inconsistency is still user-visible and should be fixed in the shipped assets, not just the top-level README.

Verification:
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/test_quickstart.py tests/test_iterm.py` → 70 passed
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q` → 1 failed, 263 passed, 3 skipped; still only the known stale-round cycle failure

## Round 3

### Lead

**Action:** SUBMIT_FOR_REVIEW

Both consistency blockers addressed across the shipped surfaces.

Blocking 1 (packaged skill + setup.py next-steps still ship old copy):
- ai_handoff/data/.claude/skills/handoff/SKILL.md — replaced every `python -m ai_handoff` with `ai-handoff` (13 occurrences, verified with grep); updated "5+ consecutive stale rounds" → "10+ consecutive stale rounds". Every new project provisioned via `ai-handoff setup` / `ai-handoff quickstart` will now get the consistent skill.
- ai_handoff/setup.py — the "Next steps" block printed at the end of setup now uses `ai-handoff quickstart` / `ai-handoff init` / `ai-handoff session start` instead of `python -m ai_handoff ...`.
- .claude/skills/handoff/SKILL.md — synced this repo's own working copy from the packaged data/ copy so contributors of ai-handoff itself see the same text.

Blocking 2 (Saloon/web copy still contradicts onboarding contract):
- ai_handoff/data/web/app.js line 381 — Bartender idle dialogue updated: "after 5 rounds" → "after 10 rounds".
- ai_handoff/data/web/app.js line 447 and 540 — Watcher dialogue tmux-start hints updated: `python -m ai_handoff session start` → `ai-handoff session start`.

Scope: this round widens scope again to three files I'd originally marked out of scope: setup.py, data/.claude/skills/handoff/SKILL.md, data/web/app.js. The alternative is shipping v1 with three different onboarding stories across the CLI, skill, and Saloon — exactly the inconsistency Codex flagged. Calling out the widening explicitly so the reviewer can confirm the scope shift is acceptable.

Surfaces NOT changed:
- ai_handoff/data/.claude/settings.local.json references `python -m ai_handoff` in bash allow-patterns (user's project permissions, not user-facing docs) — left alone.
- docs/roadmap.md Phase 2 description ("Auto-escalation after 5 rounds") is a historical record of what that phase delivered at the time; the current limit is 10 because later phases changed it. Left as historical accuracy.
- tests/test_cycle.py:275 comment ("5 stale submissions") is a pre-existing test comment; test_cycle.py is out of scope and has the known pre-existing failure.
- ai_handoff/watcher.py:493,539 references to "5 min" are a separate concept (re-send timer), not the stale-round threshold.

Test results: 263 passed, 3 skipped, 1 pre-existing unrelated failure — no regressions from this round.

Files changed this round: ai_handoff/data/.claude/skills/handoff/SKILL.md, ai_handoff/setup.py, ai_handoff/data/web/app.js, .claude/skills/handoff/SKILL.md.

### Reviewer

**Action:** APPROVE

Approved.

---

<!-- CYCLE_STATUS -->
READY_FOR: None
ROUND: 3
STATE: approved