# Project Roadmap

## Overview
AI Handoff Framework - A collaboration framework enabling structured, multi-phase AI-to-AI collaboration with human oversight.

**Tech Stack:** Python 3.10+, YAML configuration, Markdown templates, Textual (TUI)

**Workflow:** Lead / Reviewer with Human Arbiter

## Phases

### Phase 1: Configurable Agents Init
- **Status:** Complete
- **Description:** Create interactive init command for configuring AI agents and their roles
- **Key Deliverables:**
  - Interactive `python -m ai_handoff init` command
  - `ai-handoff.yaml` config file generation
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
  - `python -m ai_handoff watch` watcher daemon (notify + tmux modes)
  - `python -m ai_handoff state` CLI for viewing/updating state
  - `python -m ai_handoff session` tmux session management
  - `/handoff-cycle` skill updated with state file integration

### Phase 4: TUI Consolidation
- **Status:** Complete
- **Description:** Consolidate the gamerfy TUI into ai-handoff as a subpackage. Adds `python -m ai_handoff tui` command with `--dir` flag, first-time user setup via TUI dialogue, and sound effects.
- **Key Deliverables:**
  - `ai_handoff/tui/` subpackage with ASCII saloon scene, dialogue system, map widget
  - `python -m ai_handoff tui [--dir PATH]` CLI subcommand
  - First-time user flow with project scaffolding via TUI
  - `pip install ai-handoff[tui]` optional dependency
  - Sound effects bundled in package

### Phase 5: Template Variable Substitution
- **Status:** Complete
- **Description:** Templates automatically use configured agent names
- **Key Deliverables:**
  - `ai_handoff/templates.py` module with `render_template()` and `get_template_variables()`
  - Variable substitution in 8 templates (`{{lead}}`, `{{reviewer}}`)
  - `setup.py` reads config and substitutes variables when copying templates
  - Generated docs reflect config when `setup` runs after `init`

### Phase 6: Migration & Advanced Features
- **Status:** Complete
- **Description:** Migration tooling for legacy projects, centralized config parsing with validation
- **Key Deliverables:**
  - `python -m ai_handoff migrate` command with auto-detection and backups
  - Centralized `ai_handoff/config.py` module (read, validate, get_agent_names)
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
  - Shared `ai_handoff/parser.py` used by both TUI and web dashboard
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
  - `ai_handoff/cycle.py` module (JSONL/JSON storage, CLI commands, centralized discovery)
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

## Decision Log
See `docs/decision_log.md`

## Getting Started
1. Use `/handoff-phase` to check current phase
2. Use `/handoff-plan create [phase]` to start planning
3. Use `/handoff-status` for project overview
