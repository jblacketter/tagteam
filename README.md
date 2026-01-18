# AI Handoff Framework

A reusable collaboration framework for Claude (Lead) and Codex (Reviewer) workflows.

## Overview

This framework provides skills, templates, and checklists for structured AI-to-AI collaboration with human oversight. It implements a lead/reviewer/arbiter pattern where:

- **Lead (Claude)**: Plans phases, implements code, creates handoffs
- **Reviewer (Codex)**: Reviews plans and implementations, provides feedback
- **Arbiter (Human)**: Breaks ties, makes final decisions, approves phases

## Installation

### As a Git Submodule
```bash
git submodule add <repo-url> .ai-handoff
cp -r .ai-handoff/.claude .claude
cp -r .ai-handoff/templates templates
cp -r .ai-handoff/checklists docs/checklists
```

### Manual Setup
Copy the `.claude/skills/`, `templates/`, and `checklists/` directories to your project.

## Skills

| Skill | Purpose | Primary User |
|-------|---------|--------------|
| `/plan` | Create and update phase plans | Claude (Lead) |
| `/handoff` | Create handoff documents | Claude (Lead) |
| `/review` | Review plans or implementations | Codex (Reviewer) |
| `/implement` | Track implementation progress | Claude (Lead) |
| `/phase` | Manage phase lifecycle | Both |
| `/status` | Quick project status overview | Both |
| `/decide` | Log decisions with rationale | Both |
| `/escalate` | Escalate disagreements to arbiter | Both |
| `/sync` | Generate sync summaries for sessions | Both |

## Workflow

```
Planning Cycle:
  Claude: /plan create [phase]
  Claude: /handoff plan [phase]
  Codex: /review plan [phase]
  Claude: /handoff read [phase] → incorporate feedback or proceed

Implementation Cycle:
  Claude: /implement start [phase]
  [Claude implements]
  Claude: /implement complete [phase]
  Claude: /handoff impl [phase]
  Codex: /review impl [phase]
  Claude: /handoff read [phase] → fix issues or proceed

Completion:
  /phase complete [phase]
  Start next phase...
```

## Directory Structure

When integrated into a project:

```
your-project/
├── .claude/
│   └── skills/          # Skill definitions
├── docs/
│   ├── phases/          # Phase plan documents
│   ├── handoffs/        # Handoff and feedback documents
│   ├── escalations/     # Human arbiter decisions
│   ├── checklists/      # Review checklists
│   ├── roadmap.md       # Project phases overview
│   ├── workflows.md     # Collaboration process
│   ├── decision_log.md  # Decision history
│   └── sync_state.md    # Current sync state
└── templates/           # Document templates
```

## Quick Start

1. Copy framework files to your project
2. Create `docs/roadmap.md` with your project phases
3. Initialize `docs/decision_log.md`
4. Run `/status` to verify setup
5. Start with `/plan create [first-phase]`

## License

MIT
