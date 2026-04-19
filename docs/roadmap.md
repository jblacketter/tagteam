# Project Roadmap

## Overview
Tagteam - A collaboration framework enabling structured, multi-phase AI-to-AI collaboration with human oversight.

**Tech Stack:** Python 3.10+, YAML configuration, Markdown templates, Textual (TUI)

**Workflow:** Lead / Reviewer with Human Arbiter

## Phases

### Phase 1: Configurable Agents Init
- **Status:** Complete
- **Description:** Create interactive init command for configuring AI agents and their roles
- **Key Deliverables:**
  - Interactive `python -m tagteam init` command
  - `tagteam.yaml` config file generation
  - Skills updated to read config at runtime
  - Getting started documentation

### Phase 2: Review Cycle Automation
- **Status:** Complete
- **Description:** Automate the back-and-forth review process with a single cycle document
- **Key Deliverables:**
  - `/handoff-cycle` skill for automated review cycles
  - Single cycle document format with status tracking
  - Auto-escalation after 5 rounds
  - Human input pause/resume capability

### Phase 3: Automated Agent Orchestration
- **Status:** Complete
- **Description:** File-based state machine and watcher daemon for automated turn-taking between agents
- **Key Deliverables:**
  - `handoff-state.json` state file with atomic read/write
  - `python -m tagteam watch` watcher daemon (notify + tmux modes)
  - `python -m tagteam state` CLI for viewing/updating state
  - `python -m tagteam session` tmux session management
  - `/handoff-cycle` skill updated with state file integration

### Phase 4: TUI Consolidation
- **Status:** Complete
- **Description:** Consolidate the gamerfy TUI into tagteam as a subpackage. Adds `python -m tagteam tui` command with `--dir` flag, first-time user setup via TUI dialogue, and sound effects.
- **Key Deliverables:**
  - `tagteam/tui/` subpackage with ASCII saloon scene, dialogue system, map widget
  - `python -m tagteam tui [--dir PATH]` CLI subcommand
  - First-time user flow with project scaffolding via TUI
  - `pip install tagteam[tui]` optional dependency
  - Sound effects bundled in package

### Phase 5: Template Variable Substitution
- **Status:** Complete
- **Description:** Templates automatically use configured agent names
- **Key Deliverables:**
  - `tagteam/templates.py` module with `render_template()` and `get_template_variables()`
  - Variable substitution in 8 templates (`{{lead}}`, `{{reviewer}}`)
  - `setup.py` reads config and substitutes variables when copying templates
  - Generated docs reflect config when `setup` runs after `init`

### Phase 6: Migration & Advanced Features
- **Status:** Complete
- **Description:** Migration tooling for legacy projects, centralized config parsing with validation
- **Key Deliverables:**
  - `python -m tagteam migrate` command with auto-detection and backups
  - Centralized `tagteam/config.py` module (read, validate, get_agent_names)
  - Forward-compatible `model_patterns` schema field with overlap validation
  - Unit tests for config and migration modules

### Phase 7: Unified Command (Command Drift Fix)
- **Status:** Complete
- **Description:** Replace 10 skill files (~30+ subcommands) with a single `/handoff` command that auto-detects role and state. Fixes agent drift in long context windows.
- **Key Deliverables:**
  - Single `/handoff` skill file (<150 lines)
  - State-driven auto-dispatch (reads role + state, does the right thing)
  - Mandatory NEXT COMMAND output box on every response
  - Deprecation notices on old skill files
  - 3 commands total: `/handoff`, `/handoff start [phase]`, `/handoff status`

### Phase 8: Orchestration Fix
- **Status:** Complete
- **Description:** Fix the watcher daemon and tmux send-keys integration so agents automatically pick up tasks when it's their turn
- **Key Deliverables:**
  - `_log()` helper with `flush=True` for visible watcher output in tmux panes
  - Escape x3 + C-c input clearing (C-u doesn't work in TUI agents)
  - C-m submit instead of Enter (reliable across Claude Code and Codex)
  - Agent idle detection via `capture-pane` (waits for prompt before sending)
  - Universal text command instead of `/handoff` for cross-agent compatibility
  - Directory-based skill format (`handoff/SKILL.md` with YAML frontmatter)
  - `setup.py` copies directory-based skills alongside flat `.md` files
  - `session.py` creates 3-column layout with mouse mode and pane labels
  - `--dir` flag for `session start` to set working directory

### Phase 9: Dashboard & TUI Polish
- **Status:** Complete
- **Description:** Fix TUI bugs (GAMERFY naming, silent poll failures, status bar overflow), extract shared parser for TUI and web dashboard, improve web dashboard with escalation choices, phase map, and structured round display, split 46KB HTML into 3 files, add unit test coverage
- **Key Deliverables:**
  - Shared `tagteam/parser.py` used by both TUI and web dashboard
  - `GAMERFY_SOUND` → `HANDOFF_SOUND` rename with backward-compat fallback
  - TUI state poller: failure logging + `[STALE]` indicator
  - Status bar: action truncation (25 chars), `Round N` display (no `/5`)
  - Web dashboard split: `index.html` + `styles.css` + `app.js`
  - Web dashboard: escalation choice buttons, phase map, structured rounds
  - 39 new unit tests across 3 test files (parser, state_watcher, review_dialogue)

### Phase 10: Web Dashboard Redesign
- **Status:** Complete
- **Description:** Redesign from ASCII art to pixel art sprites with modern responsive layout, RPG dialogue system with typewriter effects, and JavaScript conversation engine
- **Key Deliverables:**
  - SVG pixel art sprites (Mayor, Rabbit, Clock, Saloon backdrop) via `sprites.js`
  - JavaScript dialogue engine with typewriter effect, portraits, conversation trees
  - Full-width banner + responsive card grid layout
  - CSS animations: pendulum swing, cuckoo pop-out, mayor glow pulse
  - Character reactions to state changes (color shifts)

### Phase 11: The Saloon — Interactive Character-Driven Setup & Monitoring
- **Status:** Complete
- **Description:** Transform the dashboard into a full saloon experience with three independently clickable characters (Mayor, Bartender, Watcher). First-time setup becomes a guided multi-character conversation. The new Watcher character provides agent monitoring and daemon control.
- **Key Deliverables:**
  - New Watcher character (pixel art sprite + portrait + dialogue)
  - All three characters independently clickable with domain-specific menus
  - Guided multi-character setup flow (Mayor → Bartender → Watcher)
  - Character glow system to guide users between characters
  - Watcher monitoring API (daemon status, tmux session control, log tails)
  - Setup state persistence (resume mid-flow)
- **Phase Plan:** `docs/phases/saloon-interactive-setup.md`

### Phase 12: Cycle Storage & CLI (Performance)
- **Status:** Complete
- **Description:** Replace markdown-based cycle documents with append-only JSONL rounds + JSON status files, updated via CLI commands. Eliminates repeated read/modify/write of markdown from the active handoff loop.
- **Key Deliverables:**
  - `tagteam/cycle.py` module (JSONL/JSON storage, CLI commands, centralized discovery)
  - CLI commands: `cycle init`, `cycle add`, `cycle status`, `cycle rounds`, `cycle render`
  - Format dispatcher in `parser.py` (JSONL first, legacy markdown fallback)
  - All consumers updated: `server.py`, `app.js`, `handoff_reader.py`, `review_replay.py`
  - SKILL.md updated to use CLI commands instead of manual file edits
  - Synthesized markdown view via `cycle render` for human readability
  - 56 unit tests across `test_cycle.py` and `test_parser.py`
- **Phase Plan:** `docs/phases/cycle-storage-cli.md`

### Phase 13: Unified State Command (Performance)
- **Status:** Complete
- **Description:** Merge `cycle add` and `state set` into a single command via `--updated-by` flag, cutting agent tool calls per handoff turn from 2 to 1.
- **Key Deliverables:**
  - `--updated-by` flag on `cycle init` and `cycle add` — auto-updates `handoff-state.json`
  - `_STATE_TRANSITIONS` table and `_update_handoff_state()` helper in `cycle.py`
  - Round-5 auto-escalation preserved in unified command path
  - SKILL.md updated to single-command flow for all agent actions
  - Regression test for round-5 escalation behavior

### Phase 14: Sharing Readiness
- **Status:** Complete
- **Description:** Make tagteam ready for wider sharing — simplify README (remove manual setup, promote automated), mark Saloon as WIP, improve watcher robustness (seq-based change detection, re-send watchdog, retry loop), add `session start --launch` for auto-starting agents
- **Key Deliverables:**
  - README restructured: single automated workflow, one-line manual mention, Saloon marked WIP
  - `config.py`: `get_launch_commands()` helper, `command` field validation, no-PyYAML fallback
  - `session.py` / `iterm.py`: `--launch` flag auto-starts agents and watcher
  - `watcher.py`: seq-based change detection, 5-min re-send watchdog for stuck `ready` states
  - Tests for new config helpers and launch behavior
- **Phase Plan:** `docs/phases/sharing-readiness.md`

### Phase 15: Onboarding Polish
- **Status:** Complete
- **Description:** Simplify the first-run experience by unifying `init` + `setup` + `session start --launch` into a single streamlined flow. Reduce the number of commands a new user needs to go from install to running their first handoff.
- **Key Deliverables:**
  - `quickstart` command: setup + init + session in one command
  - `ensure_session()` with idempotent behavior (tmux auto-attach, iTerm skip)
  - `needs_setup()` 3-point check, `run_init()` with TTY guard
  - README, HELP_TEXT, GETTING_STARTED all updated
  - 21 new tests
- **Phase Plan:** `docs/phases/onboarding-polish.md`

### Phase 16: Stale Handoff Diagnostics
- **Status:** Complete
- **Description:** Build structured logging and diagnostics for debugging stale handoffs.
- **Key Deliverables:**
  - `state diagnose` command with 7 diagnostic checks
  - Enriched history entries (phase, round, updated_by)
  - Seq mismatch side-channel logging (`handoff-diagnostics.jsonl`)
  - `--check-agents` for agent responsiveness via session discovery
  - History anomaly detection (oscillation, repeated escalations)
  - 19 new tests
- **Phase Plan:** `docs/phases/stale-handoff-diagnostics.md`

### Phase 17: Test Coverage & Isolation
- **Status:** Complete
- **Description:** Fix pre-existing test failures, add TUI test isolation.
- **Key Deliverables:**
  - Fixed `detect_agent_names()` — sorted glob, found-flags prevent overwrite
  - TUI tests skip gracefully via `pytest.importorskip("textual")`
  - Added `[tool.pytest.ini_options]` to pyproject.toml
  - Full suite: 228 passed, 3 skipped, 0 failed
- **Phase Plan:** `docs/phases/test-coverage-isolation.md`

### Phase 18: Saloon Production Ready
- **Status:** Complete
- **Description:** Polish the web dashboard to production quality.
- **Key Deliverables:**
  - User-visible error banner for failed API calls (app.js)
  - Exponential backoff polling (2s → 30s max, auto-recovery)
  - State POST validation — rejects unknown fields (server.py)
  - WIP banner removed from README
- **Phase Plan:** `docs/phases/saloon-production-ready.md`

### Phase 19: Public Onboarding
- **Status:** Complete
- **Description:** Make tagteam ready to share with the world. Simpler init prompts, shared handoff explainer in CLI + README, iTerm2 cold-launch fix, README trimmed to a single backend-neutral Quick Start using the `tagteam` console script, prominent post-quickstart priming box.
- **Key Deliverables:**
  - `init` prompts simplified to 2 questions (lead name, reviewer name) — no role prompt
  - `HANDOFF_EXPLAINER` printed once per quickstart path via `show_explainer` plumbing
  - `_ensure_iterm_running()` launches iTerm2 from a fully-quit state; window-count guard prevents duplicate windows
  - README rewritten with "How it works" section and backend-neutral Quick Start
  - `python -m tagteam` replaced with `tagteam` throughout docs
  - Backend-aware priming box (tab/pane/terminal) at end of quickstart
- **Phase Plan:** `docs/phases/public-onboarding.md`

## Backlog

### Terminal.app backend (macOS, optional)
- **Status:** Not started
- **Motivation:** Terminal.app ships with every Mac, so a `terminal` backend would remove the iTerm2 install step for new macOS users. Default stays `iterm2` (richer scripting); Terminal.app is opt-in via `--backend terminal`.
- **Sketch:**
  - New `tagteam/terminal.py` mirroring `iterm.py` against Terminal.app's AppleScript (`do script`, `tell tab N of window M`)
  - Add `"terminal"` to `SUPPORTED_BACKENDS` and `_validate_backend()` in `session.py`
  - Extend `_parse_backend` / `ensure_session` dispatch
- **Known tradeoff:** Terminal.app has no stable session IDs — stale-session recovery must fall back to window+tab index tracking, which is more fragile than iTerm2's session-ID model. Expect the module to be less robust under user tab rearrangement.

## Decision Log
See `docs/decision_log.md`

## Getting Started
1. Use `/handoff-phase` to check current phase
2. Use `/handoff-plan create [phase]` to start planning
3. Use `/handoff-status` for project overview
