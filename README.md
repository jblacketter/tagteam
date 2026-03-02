# AI Handoff Framework

A collaboration framework for structured AI-to-AI handoffs with human oversight.

## Overview

This framework provides skills, templates, and checklists for structured AI-to-AI collaboration. It implements a lead/reviewer/arbiter pattern where:

- **Lead**: Plans phases, implements code, creates handoffs
- **Reviewer**: Reviews plans and implementations, provides feedback
- **Arbiter**: Breaks ties, makes final decisions, approves phases (typically Human)

Configure your agents via `ai-handoff.yaml` - use any AI combination (Claude + Codex, Gemini + Claude, etc.).

## Installation

### Known Issues

- Tested on macOS and Linux.
- Windows has minor issues.
- The Saloon TUI's automated handoff flow is still evolving; some handoff/review steps may require manual input.

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

### Upgrading Existing Projects

If you already have skill files from a previous version, re-run setup to get the new unified `/handoff` command:

```bash
python -m ai_handoff setup .
```

This adds the new `handoff/SKILL.md` directory-based skill and marks the old individual skills as deprecated. Your existing cycle documents and state files are unchanged.

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
Read ai-handoff.yaml to see your role, then read .claude/skills/handoff/SKILL.md for the workflow.
```

### 3. Begin Working

```
/handoff status          # Check project status and orientation
/handoff start [phase]   # Create first phase plan and start review cycle
```

## Commands

The entire workflow uses a single unified command:

| Command                  | Purpose                                           | Who  |
| ------------------------ | ------------------------------------------------- | ---- |
| `/handoff`               | Main command — reads state, does the right thing | Both |
| `/handoff start [phase]` | Begin a new phase (plan + review cycle)           | Lead |
| `/handoff start [phase] impl` | Begin implementation review for a phase     | Lead |
| `/handoff status`        | Orientation, status check, drift reset            | Both |

The `/handoff` command auto-detects your role from `ai-handoff.yaml` and the current state from `handoff-state.json`, then acts accordingly. Every response ends with the exact next command to copy-paste.

<details>
<summary>Legacy skills (deprecated)</summary>

The following individual skills still exist for backward compatibility but are replaced by `/handoff`:

`/handoff-plan`, `/handoff-handoff`, `/handoff-review`, `/handoff-implement`, `/handoff-phase`, `/handoff-status`, `/handoff-decide`, `/handoff-escalate`, `/handoff-sync`, `/handoff-cycle`

</details>

## Workflow

```
Lead:     /handoff start [phase]     → creates plan + review cycle
Reviewer: /handoff                   → reviews, APPROVE or REQUEST_CHANGES
Lead:     /handoff                   → addresses feedback
Reviewer: /handoff                   → reviews again
  ↓
(repeat until approved or round 5 auto-escalation)
  ↓
Lead:     /handoff start [phase] impl → starts implementation review
Reviewer: /handoff                    → reviews implementation
  ↓
(approved → next phase)
```

Both agents work from one cycle document. Auto-escalates to human after 5 rounds.

## Automated Orchestration

Eliminate manual copy-paste between agents entirely. A watcher daemon monitors a shared state file and automatically sends commands to each agent's terminal when it's their turn.

### Quick Start (iTerm2 Default)

```bash
# 1. Create Lead / Watcher / Reviewer tabs in iTerm2
python -m ai_handoff session start --dir ~/projects/myproject

# 2. In the Watcher tab, start auto-send mode
python -m ai_handoff watch --mode iterm2
```

This creates three iTerm2 tabs: **Lead**, **Watcher**, and **Reviewer**.

### Starting Agents

1. In the **Lead** tab, start your lead agent (e.g. `claude`)
2. In the **Reviewer** tab, start your reviewer agent (e.g. `codex`)
3. In the **Watcher** tab, run `python -m ai_handoff watch --mode iterm2`

### Running a Cycle

In the lead tab, start a review cycle:

```
/handoff start my-phase
```

The skill writes `handoff-state.json`. The watcher detects the turn change, waits for the target agent to be idle, then sends the handoff command to the reviewer's tab. The reviewer processes it, updates state, and the watcher sends back to the lead tab. This repeats until the cycle is approved, escalated, or aborted.

### Orchestration Commands

```bash
python -m ai_handoff session start --dir ~/projects/myproject  # Create iTerm2 tabs (default backend)
python -m ai_handoff watch --mode iterm2                       # Auto-send to iTerm2 Lead/Reviewer tabs
python -m ai_handoff session start --backend tmux              # Create tmux session (legacy backend)
python -m ai_handoff session attach --backend tmux             # Attach to existing tmux session
python -m ai_handoff watch --mode tmux                         # Auto-send to tmux panes
python -m ai_handoff watch --mode notify                       # Desktop notifications only (no auto-send)
python -m ai_handoff watch --confirm  # Pause for human approval before each send
python -m ai_handoff state            # View current orchestration state
python -m ai_handoff state reset      # Clear state file
python -m ai_handoff session kill     # Destroy orchestration session
```

### Notification-Only Mode

If you prefer separate terminal windows and manual switching, run the watcher in notify mode:

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

| Button      | Action                                            |
| ----------- | ------------------------------------------------- |
| Approve     | Mark the current handoff as done/approved         |
| Req Changes | Bump the round and switch turn to the other agent |
| Escalate    | Flag for human intervention                       |
| Abort       | Cancel the current cycle (prompts for reason)     |

The saloon scene reflects state visually: clock turns blue when working, characters turn green on approval, red on escalation, and muted on abort.

## Terminal UI (TUI)

The Handoff Saloon also comes as a terminal-based UI with ASCII art characters, sound effects, and an immersive dialogue system.

### TUI Installation

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

| Key         | Action                          |
| ----------- | ------------------------------- |
| `m`         | Toggle phase map overlay        |
| `r`         | Replay last review cycle        |
| `q`         | Quit                            |
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
python -m ai_handoff watch --mode iterm2 # Start watcher daemon for iTerm2 tabs
python -m ai_handoff state         # View/update orchestration state
python -m ai_handoff session start # Create session (default: iTerm2)
python -m ai_handoff --help        # Show help
```

## License

MIT
