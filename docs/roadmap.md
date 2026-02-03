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
- **Status:** Not Started
- **Description:** Templates automatically use configured agent names
- **Key Deliverables:**
  - Variable substitution in templates ({{lead}}, {{reviewer}})
  - Generated docs reflect config

### Phase 6: Migration & Advanced Features
- **Status:** Not Started
- **Description:** Migration path for existing users, multi-agent support
- **Key Deliverables:**
  - `ai-handoff migrate` command
  - Support for 3+ agents
  - Model pattern matching

## Decision Log
See `docs/decision_log.md`

## Getting Started
1. Use `/handoff-phase` to check current phase
2. Use `/handoff-plan create [phase]` to start planning
3. Use `/handoff-status` for project overview
