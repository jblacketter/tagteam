# Project Roadmap

## Overview
AI Handoff Framework - A collaboration framework enabling structured, multi-phase AI-to-AI collaboration with human oversight.

**Tech Stack:** Python 3.8+, YAML configuration, Markdown templates

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

### Phase 2: Template Variable Substitution
- **Status:** Not Started
- **Description:** Templates automatically use configured agent names
- **Key Deliverables:**
  - Variable substitution in templates ({{lead}}, {{reviewer}})
  - Generated docs reflect config

### Phase 3: Migration & Advanced Features
- **Status:** Not Started
- **Description:** Migration path for existing users, multi-agent support
- **Key Deliverables:**
  - `ai-handoff migrate` command
  - Support for 3+ agents
  - Model pattern matching

## Decision Log
See `docs/decision_log.md`

## Getting Started
1. Use `/phase` to check current phase
2. Use `/plan create [phase]` to start planning
3. Use `/status` for project overview
