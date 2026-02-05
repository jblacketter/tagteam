# AI Handoff Framework

A collaboration framework for structured AI-to-AI handoffs with human oversight.

## Overview

This framework provides skills, templates, and checklists for structured AI-to-AI collaboration. It implements a lead/reviewer/arbiter pattern where:

- **Lead**: Plans phases, implements code, creates handoffs
- **Reviewer**: Reviews plans and implementations, provides feedback
- **Arbiter**: Breaks ties, makes final decisions, approves phases (typically Human)

Configure your agents via `ai-handoff.yaml` - use any AI combination (Claude + Codex, Gemini + Claude, etc.).

## Installation
### known issues 
### this is tested on macos and linux, but has some minor issues on windows at the moment.
### TUI automated hand off with the saloon is not yet fully functional. hand off and review cycle requires manual input

### From GitHub

```bash
pip install git+https://github.com/jblacketter/ai-handoff.git
```

### From Local Source

```bash
# Core only
pip install -e /path/to/ai-handoff

# With Terminal UI (requires textual)
pip install -e "/path/to/ai-handoff[tui]"
```

Note: The `[tui]` extra requires quotes or escaping due to shell bracket interpretation.

### Setup a Project

In your project directory:

```bash
python -m ai_handoff setup .
python -m ai_handoff init
```

## Getting Started

### 1. Initialize Your Agents

Run the interactive init command:

```bash
python -m ai_handoff init
```

This will prompt you for:
- Agent 1 name and role (lead or reviewer)
- Agent 2 name and role

Example:
```
AI Handoff Setup
================
Agent 1 name: claude
Agent 1 role (lead/reviewer): lead

Agent 2 name: codex
Agent 2 role (lead/reviewer): reviewer

✓ Created ai-handoff.yaml
```

### 2. Start Your AI Session

Tell your AI agent:

```
Read ai-handoff.yaml to see your role, then read .claude/skills/ for the workflow.
```

### 3. Begin Working

```
/handoff-status           # Check project status
/handoff-plan create [phase]  # Create first phase plan
```

## Skills

| Skill | Purpose | Primary User |
|-------|---------|--------------|
| `/handoff-plan` | Create and update phase plans | Lead |
| `/handoff-handoff` | Create handoff documents | Lead |
| `/handoff-review` | Review plans or implementations | Reviewer |
| `/handoff-implement` | Track implementation progress | Lead |
| `/handoff-phase` | Manage phase lifecycle | Both |
| `/handoff-status` | Quick project status overview | Both |
| `/handoff-decide` | Log decisions with rationale | Both |
| `/handoff-escalate` | Escalate disagreements to arbiter | Both |
| `/handoff-sync` | Generate sync summaries for sessions | Both |
| `/handoff-cycle` | Automated review cycles (reduces copy-paste) | Both |

## Workflow

```
Planning Cycle:
  Lead: /handoff-plan create [phase]
  Lead: /handoff-handoff plan [phase]
  Reviewer: /handoff-review plan [phase]
  Lead: /handoff-handoff read [phase] → incorporate feedback or proceed

Implementation Cycle:
  Lead: /handoff-implement start [phase]
  [Lead implements]
  Lead: /handoff-implement complete [phase]
  Lead: /handoff-handoff impl [phase]
  Reviewer: /handoff-review impl [phase]
  Lead: /handoff-handoff read [phase] → fix issues or proceed

Completion:
  /handoff-phase complete [phase]
  Start next phase...
```

### Automated Review Cycle (Alternative)

Use `/handoff-cycle` to reduce manual copy-paste during multi-round reviews:

```
Lead: /handoff-cycle start [phase] plan
  ↓
Reviewer: /handoff-cycle [phase]  → APPROVE or REQUEST_CHANGES
  ↓
Lead: /handoff-cycle [phase]      → address feedback
  ↓
(repeat until approved or round 5 escalation)
```

Both agents work from one file. Auto-escalates to human after 5 rounds.

## Automated Orchestration

Eliminate manual copy-paste between agents entirely. A watcher daemon monitors a shared state file and automatically sends commands to each agent's terminal when it's their turn.

### Quick Start

```bash
# 1. Create a tmux session with lead, reviewer, and watcher panes
python -m ai_handoff session start

# 2. Attach to the session
tmux attach -t ai-handoff
```

This gives you a 3-pane layout:

```
┌──────────────────┬──────────────────┐
│  Pane 0: Lead    │  Pane 2: Reviewer│
│  (Claude Code)   │  (Codex)         │
├──────────────────┴──────────────────┤
│  Pane 1: Watcher                    │
│  (monitors state, triggers agents)  │
└─────────────────────────────────────┘
```

**Navigating tmux panes:** Press `Ctrl-b` (release), then an arrow key to move between panes. If keyboard navigation doesn't work, enable mouse support:

```bash
tmux set -g mouse on
```

This lets you click directly on panes to switch focus.

### Starting Agents

1. Click on **Pane 0** (top-left) and start your lead agent (e.g. `claude`)
2. Click on **Pane 2** (top-right) and start your reviewer agent (e.g. `codex`)
3. Click on **Pane 1** (bottom) and press Enter to start the watcher

### Running a Cycle

In the lead pane, start a review cycle as normal:

```
/handoff-cycle start my-phase plan
```

The skill automatically writes `handoff-state.json`. The watcher detects the turn change and sends `/handoff-cycle my-phase` to the reviewer's pane. The reviewer processes it, updates state, and the watcher sends back to the lead. This repeats until the cycle is approved, escalated, or aborted -- with zero manual intervention.

### Orchestration Commands

```bash
python -m ai_handoff session start    # Create tmux layout
python -m ai_handoff session attach   # Attach to existing session
python -m ai_handoff session kill     # Destroy the session
python -m ai_handoff watch            # Start watcher (runs in session pane)
python -m ai_handoff watch --mode notify  # Desktop notifications only (no auto-send)
python -m ai_handoff watch --confirm  # Pause for human approval before each send
python -m ai_handoff state            # View current orchestration state
python -m ai_handoff state reset      # Clear state file
```

### Without tmux

If you prefer separate terminal windows, run the watcher in notify mode:

```bash
python -m ai_handoff watch --mode notify
```

This prints turn changes and sends macOS desktop notifications. You still switch terminals manually, but the watcher tells you exactly when and what command to run.

## Dashboard

The Handoff Saloon is a web dashboard for monitoring and controlling handoff cycles. The Mayor character guides you through setup and provides contextual help.

### Starting the Dashboard

```bash
# Existing project
python -m ai_handoff serve --dir ~/projects/myproject

# New project (Mayor will guide you through setup)
mkdir ~/projects/newproject
python -m ai_handoff serve --dir ~/projects/newproject
```

Open **http://localhost:8080** in your browser. Use `--port 3000` for a different port.

### Dashboard Modes

- **Welcome** (no `ai-handoff.yaml`): Click the Mayor to get started. He'll guide you through entering your agent names, which creates the config file.
- **Idle** (config exists, no active handoff): Click the Mayor to start a new phase, see a status summary, or learn how it works. Timeline shows past activity.
- **Active** (handoff in progress): Full controls — Approve, Request Changes, Escalate, Abort. Mayor provides contextual tips. Timeline and cycle viewer update live.

### Dashboard Controls

| Button | Action |
|--------|--------|
| Approve | Mark the current handoff as done/approved |
| Req Changes | Bump the round and switch turn to the other agent |
| Escalate | Flag for human intervention |
| Abort | Cancel the current cycle (prompts for reason) |

The saloon scene reflects state visually: clock turns blue when working, characters turn green on approval, red on escalation, and muted on abort.

## Terminal UI (TUI)

The Handoff Saloon also comes as a terminal-based UI with ASCII art characters, sound effects, and an immersive dialogue system.

### Installation

```bash
pip install ai-handoff[tui]
```

### Running the TUI

```bash
# Existing project
python -m ai_handoff tui --dir ~/projects/myproject

# New project (Mayor will guide you through setup)
python -m ai_handoff tui

# With sound effects
python -m ai_handoff tui --dir ~/projects/myproject --sound
```

### TUI Features

- **ASCII Saloon Scene** — Mayor (lead agent) and Rabbit Bartender (reviewer) characters
- **Dialogue System** — Agent output rendered as character speech with typing effect
- **Cuckoo Clock** — Shows whose turn it is with animated state changes
- **Phase Map** — Press `m` to see project phase progress
- **Review Replay** — Press `r` to replay past review cycles as conversation
- **Sound Effects** — Optional tick, chime, bell, and stamp sounds (enable with `--sound`)
- **First-Time Setup** — If no project exists, the Mayor guides you through creating one
- **Escalation Handling** — When agents disagree, you make the call via dialogue choices

### TUI Controls

| Key | Action |
|-----|--------|
| `m` | Toggle phase map overlay |
| `r` | Replay last review cycle |
| `q` | Quit |
| Space/Enter | Advance dialogue or skip typing |

## Configuration

The `ai-handoff.yaml` file in your project root defines your agents:

```yaml
agents:
  lead:
    name: claude
  reviewer:
    name: codex
```

Re-run `python -m ai_handoff init` to change agents or swap roles.

## Directory Structure

When integrated into a project:

```
your-project/
├── ai-handoff.yaml     # Agent configuration
├── .claude/
│   └── skills/         # Skill definitions
├── docs/
│   ├── phases/         # Phase plan documents
│   ├── handoffs/       # Handoff and feedback documents
│   ├── escalations/    # Human arbiter decisions
│   ├── checklists/     # Review checklists
│   ├── roadmap.md      # Project phases overview
│   ├── workflows.md    # Collaboration process
│   ├── decision_log.md # Decision history
│   └── sync_state.md   # Current sync state
└── templates/          # Document templates
```

## CLI Commands

```bash
python -m ai_handoff init          # Configure agents interactively
python -m ai_handoff setup .       # Copy framework files to project
python -m ai_handoff serve --dir . # Start the web dashboard
python -m ai_handoff tui --dir .   # Launch the terminal UI
python -m ai_handoff watch         # Start watcher daemon
python -m ai_handoff state         # View/update orchestration state
python -m ai_handoff session start # Create tmux session
python -m ai_handoff --help        # Show help
```

## License

MIT
