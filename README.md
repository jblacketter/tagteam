# AI Handoff Framework

A collaboration framework for structured AI-to-AI handoffs with human oversight. It implements a **lead / reviewer / arbiter** pattern where:

- **Lead** — plans phases, implements code, creates handoffs
- **Reviewer** — reviews plans and implementations, provides feedback
- **Arbiter** — breaks ties, makes final decisions, approves phases (typically human)

Configure your agents via `ai-handoff.yaml` — use any AI combination (Claude + Codex, Gemini + Claude, etc.).

## Installation

```bash
# From GitHub
pip install git+https://github.com/jblacketter/ai-handoff.git

# From local source
pip install -e /path/to/ai-handoff

# With web dashboard support
pip install -e "/path/to/ai-handoff[tui]"
```

Then set up your project:

```bash
cd ~/projects/myproject
python -m ai_handoff setup .
python -m ai_handoff init      # prompts for agent names and roles
```

This creates `ai-handoff.yaml` and copies the `/handoff` skill into `.claude/skills/`.

## Choose Your Workflow

There are three ways to use ai-handoff. Start with Manual to learn the flow, move to Automated when you're confident in your roadmap, or use the Saloon dashboard for a graphical interface.

---

### 1. Manual Handoff

Human triggers `/handoff` between agents each turn — you're in the loop for every exchange.

**Best for:** new projects, hands-on oversight, learning the workflow.

**Setup:**

1. Run `python -m ai_handoff init` to create `ai-handoff.yaml`
2. Tell each agent: *"Read ai-handoff.yaml to see your role, then read .claude/skills/handoff/SKILL.md for the workflow."*

**Usage:**

```
/handoff start my-phase        # Lead creates plan, starts review cycle
/handoff                       # Copy-paste to reviewer — reviews and responds
/handoff                       # Copy-paste to lead — addresses feedback
                               # Repeat until approved (auto-escalates after 5 rounds)
/handoff start my-phase impl   # Lead starts implementation review
/handoff                       # Reviewer reviews implementation
```

The `/handoff` command auto-detects your role from `ai-handoff.yaml` and the current state from `handoff-state.json`, then acts accordingly. Every response ends with the exact next command to copy-paste.

**Workflow:**

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

**Commands:**

| Command | Purpose | Who |
| --- | --- | --- |
| `/handoff` | Auto-detects role + state, does the right thing | Both |
| `/handoff start [phase]` | Begin a new phase (plan + review cycle) | Lead |
| `/handoff start [phase] impl` | Begin implementation review for a phase | Lead |
| `/handoff status` | Orientation, status check, drift reset | Both |

---

### 2. Automated Handoff

A watcher daemon monitors the shared state file and automatically sends `/handoff` to each agent's terminal when it's their turn. No copy-paste needed.

**Best for:** confident in your roadmap, longer-running work, less babysitting.

**Setup:**

```bash
# 1. Create Lead / Watcher / Reviewer tabs (iTerm2 default)
python -m ai_handoff session start --dir ~/projects/myproject

# 2. Start your agents in their tabs
#    Lead tab:     start your lead agent (e.g. claude)
#    Reviewer tab: start your reviewer agent (e.g. codex)

# 3. In the Watcher tab, start auto-send mode
python -m ai_handoff watch --mode iterm2
```

**Single phase** — start a review cycle, watcher handles the back-and-forth, pauses when the phase completes:

```
/handoff start my-phase
```

**Full roadmap** — runs all incomplete phases end-to-end, automatically advancing through plan review → implementation → next phase:

```
/handoff start --roadmap              # All incomplete phases
/handoff start --roadmap api-gateway  # Start from a specific phase
```

**Human-in-the-loop** — add `--confirm` to pause for your approval before each automatic send:

```bash
python -m ai_handoff watch --mode iterm2 --confirm
```

**Notification-only mode** — prints turn changes and sends desktop notifications without auto-sending. You switch terminals manually:

```bash
python -m ai_handoff watch --mode notify
```

**Key commands:**

```bash
python -m ai_handoff session start --dir .            # Create iTerm2 tabs (default)
python -m ai_handoff session start --backend tmux      # Create tmux session
python -m ai_handoff session attach --backend tmux     # Attach to existing tmux session
python -m ai_handoff session kill                      # Destroy session
python -m ai_handoff watch --mode iterm2               # Auto-send to iTerm2 tabs
python -m ai_handoff watch --mode tmux                 # Auto-send to tmux panes
python -m ai_handoff watch --mode notify               # Desktop notifications only
python -m ai_handoff state                             # View orchestration state
python -m ai_handoff state reset                       # Clear state file
```

---

### 3. The Saloon (Web Dashboard)

A graphical interface for setup, monitoring, and controlling handoff cycles. The Mayor character guides you through setup and provides contextual help.

> **Status:** The dashboard is functional but still evolving — some features are in progress.

**Start it:**

```bash
python -m ai_handoff serve --dir ~/projects/myproject
# Open http://localhost:8080 (use --port 3000 for a different port)
```

**What it does:**

- **Welcome mode** (no config yet) — Mayor guides you through creating `ai-handoff.yaml`
- **Idle mode** (config exists, no active handoff) — start new phases, see status, view timeline of past activity
- **Active mode** (handoff in progress) — full arbiter controls with live-updating timeline and cycle viewer

**Arbiter controls:**

| Button | Action |
| --- | --- |
| Approve | Mark the current handoff as approved |
| Req Changes | Bump the round and switch turn |
| Escalate | Flag for human intervention |
| Abort | Cancel the current cycle |

The saloon scene reflects state visually: clock turns blue when working, characters turn green on approval, red on escalation.

There is also a **terminal UI** version with ASCII art, sound effects, and a dialogue system:

```bash
pip install ai-handoff[tui]
python -m ai_handoff tui --dir ~/projects/myproject
python -m ai_handoff tui --sound   # with sound effects
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

### Directory Structure

```
your-project/
├── ai-handoff.yaml       # Agent configuration
├── handoff-state.json    # Orchestration state (auto-managed)
├── .claude/
│   └── skills/           # Skill definitions
├── docs/
│   ├── phases/           # Phase plan documents
│   ├── handoffs/         # Handoff and feedback documents
│   ├── escalations/      # Human arbiter decisions
│   ├── checklists/       # Review checklists
│   ├── roadmap.md        # Project phases overview
│   └── decision_log.md   # Decision history
└── templates/            # Document templates
```

## CLI Reference

```bash
python -m ai_handoff init                              # Configure agents interactively
python -m ai_handoff setup .                           # Copy framework files to project
python -m ai_handoff serve --dir .                     # Start the web dashboard
python -m ai_handoff tui --dir .                       # Launch the terminal UI
python -m ai_handoff watch --mode iterm2               # Start watcher daemon
python -m ai_handoff state                             # View orchestration state
python -m ai_handoff roadmap queue                     # List incomplete phases
python -m ai_handoff roadmap phases                    # List all phases with status
python -m ai_handoff session start                     # Create session (default: iTerm2)
python -m ai_handoff session kill                      # Destroy session
python -m ai_handoff upgrade                           # Re-run setup on all registered projects
python -m ai_handoff migrate                           # Migrate legacy projects
python -m ai_handoff --help                            # Show help
```

## License

MIT
