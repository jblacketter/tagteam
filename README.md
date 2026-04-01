# AI Handoff Framework

A collaboration framework for structured AI-to-AI handoffs with human oversight. It implements a **lead / reviewer / arbiter** pattern where:

- **Lead** — plans phases, implements code, creates handoffs
- **Reviewer** — reviews plans and implementations, provides feedback
- **Arbiter** — breaks ties, makes final decisions, approves phases (typically human)

Configure your agents via `ai-handoff.yaml` — use any AI combination (Claude + Codex, Gemini + Claude, etc.).

## Installation
```bash
pip install git+https://github.com/jblacketter/ai-handoff.git
```

## Quick Start
One command to set up and launch everything:
```bash
python -m ai_handoff quickstart --dir ~/projects/myproject
```

This runs framework setup, agent configuration (interactive), and starts a session with agents and watcher auto-launched.

<details>
<summary>Advanced: individual setup steps</summary>

```bash
cd ~/projects/myproject
python -m ai_handoff setup .           # copy framework files
python -m ai_handoff init              # configure agents interactively
python -m ai_handoff session start --dir . --launch   # start session
```

Or create tabs without auto-launching agents:

```bash
python -m ai_handoff session start --dir ~/projects/myproject
#   Lead tab:     start your lead agent (e.g. claude)
#   Reviewer tab: start your reviewer agent (e.g. codex)
#   Watcher tab:  python -m ai_handoff watch --mode iterm2
```

</details>

## Usage

**Single phase** — start a review cycle, watcher handles the back-and-forth, pauses when the phase completes:

```
/handoff start my-phase
```

**Full roadmap** — runs all incomplete phases end-to-end, automatically advancing through plan review → implementation → next phase:

```
/handoff start --roadmap              # All incomplete phases
/handoff start --roadmap api-gateway  # Start from a specific phase
```

### Commands


| Command                       | Purpose                                         | Who  |
| ----------------------------- | ----------------------------------------------- | ---- |
| `/handoff`                    | Auto-detects role + state, does the right thing | Both |
| `/handoff start [phase]`      | Begin a new phase (plan + review cycle)         | Lead |
| `/handoff start [phase] impl` | Begin implementation review for a phase         | Lead |
| `/handoff status`             | Orientation, status check, drift reset          | Both |

**Human-in-the-loop** — add `--confirm` to pause for your approval before each automatic send:

```bash
python -m ai_handoff watch --mode iterm2 --confirm
```

> **Manual mode:** You can also run handoffs without the watcher by pasting `/handoff` command results between agents manually.

---

### The Saloon (Web Dashboard)

A graphical dashboard for monitoring and controlling handoff cycles:

```bash
python -m ai_handoff serve --dir ~/projects/myproject
```

## Configuration

Agents are defined in `ai-handoff.yaml` (created by `init` or `quickstart`):

```yaml
agents:
  lead:
    name: claude
    command: claude              # optional, defaults to lowercase name
  reviewer:
    name: codex
    command: codex
```

## CLI Reference

```bash
python -m ai_handoff quickstart --dir .                # Setup + init + launch in one command
python -m ai_handoff session start --dir . --launch    # Create session and auto-start agents
python -m ai_handoff session kill                      # Destroy session
python -m ai_handoff init                              # Configure agents interactively
python -m ai_handoff setup .                           # Copy framework files to project
python -m ai_handoff state                             # View orchestration state
python -m ai_handoff state diagnose                    # Diagnose stuck handoffs
python -m ai_handoff watch --mode iterm2               # Start watcher daemon
python -m ai_handoff roadmap phases                    # List all phases with status
python -m ai_handoff serve --dir .                     # Start the web dashboard
python -m ai_handoff upgrade                           # Re-run setup after pip upgrade
python -m ai_handoff --help                            # Show all commands
```

## License

MIT
