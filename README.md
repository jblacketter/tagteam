# AI Handoff Framework

A collaboration framework for structured AI-to-AI handoffs with human oversight.

## Overview

This framework provides skills, templates, and checklists for structured AI-to-AI collaboration. It implements a lead/reviewer/arbiter pattern where:

- **Lead**: Plans phases, implements code, creates handoffs
- **Reviewer**: Reviews plans and implementations, provides feedback
- **Arbiter**: Breaks ties, makes final decisions, approves phases (typically Human)

Configure your agents via `ai-handoff.yaml` - use any AI combination (Claude + Codex, Gemini + Claude, etc.).

## Installation

Install from GitHub:

```bash
pip install git+https://github.com/jblacketter/ai-handoff.git
```

Then in your project directory:

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
I am [agent name]. Read ai-handoff.yaml to confirm my role,
then read .claude/skills/ to understand the workflow.
```

Replace `[agent name]` with the actual agent name (e.g., "claude" or "codex").

### 3. Begin Working

```
/status           # Check project status
/plan create [phase]  # Create first phase plan
```

## Skills

| Skill | Purpose | Primary User |
|-------|---------|--------------|
| `/plan` | Create and update phase plans | Lead |
| `/handoff` | Create handoff documents | Lead |
| `/review` | Review plans or implementations | Reviewer |
| `/implement` | Track implementation progress | Lead |
| `/phase` | Manage phase lifecycle | Both |
| `/status` | Quick project status overview | Both |
| `/decide` | Log decisions with rationale | Both |
| `/escalate` | Escalate disagreements to arbiter | Both |
| `/sync` | Generate sync summaries for sessions | Both |

## Workflow

```
Planning Cycle:
  Lead: /plan create [phase]
  Lead: /handoff plan [phase]
  Reviewer: /review plan [phase]
  Lead: /handoff read [phase] → incorporate feedback or proceed

Implementation Cycle:
  Lead: /implement start [phase]
  [Lead implements]
  Lead: /implement complete [phase]
  Lead: /handoff impl [phase]
  Reviewer: /review impl [phase]
  Lead: /handoff read [phase] → fix issues or proceed

Completion:
  /phase complete [phase]
  Start next phase...
```

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
python -m ai_handoff init       # Configure agents interactively
python -m ai_handoff setup .    # Copy framework files to project
python -m ai_handoff --help     # Show help
```

## License

MIT
