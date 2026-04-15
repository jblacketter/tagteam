# AI Handoff Framework

A collaboration framework for structured AI-to-AI handoffs with human oversight. It implements a **lead / reviewer / arbiter** pattern where:

- **Lead** plans phases, implements code, and creates handoffs
- **Reviewer** reviews plans and implementations and provides feedback
- **Arbiter** breaks ties, makes final decisions, and approves phases (typically human)

Configure your agents via `ai-handoff.yaml` and use any AI combination you want.

## Installation

```bash
pip install git+https://github.com/jblacketter/ai-handoff.git
```

## Quick Start

```bash
cd ~/projects/myproject
python -m ai_handoff quickstart
```

This runs framework setup, agent configuration, and session startup (with agents auto-launched) against the current directory. Pass `--dir <path>` to target a different project, or `--backend <name>` to override backend detection.

Session backend selection is automatic:

- macOS + iTerm2: uses the `iterm2` backend
- Any platform with `tmux` on `PATH`: uses the `tmux` backend
- Otherwise: falls back to the `manual` backend and prints the commands to run

## Windows

Windows is usable today for the setup/state/manual workflow, but native terminal automation is not implemented yet.

What works:

- `quickstart`, `setup`, `init`, `state`, `cycle`, and document workflows
- `session start` without crashing when no automation backend is available
- `watch --mode notify` for console-guided manual coordination

What does not work yet:

- native Windows Terminal orchestration
- desktop notifications outside macOS
- automatic command injection unless you are using `tmux` or iTerm2

For the least risky Windows flow today:

```bash
# Quickstart auto-detects manual backend on Windows
python -m ai_handoff quickstart

# Or do each step yourself:
python -m ai_handoff setup
python -m ai_handoff init
python -m ai_handoff session start --backend manual
python -m ai_handoff watch --mode notify
```

If you want automated terminal orchestration on Windows right now, use WSL + `tmux`.

## Usage

**Single phase**: start a review cycle, let the watcher handle the back-and-forth, and stop when the phase completes.

```text
/handoff start my-phase
```

**Full roadmap**: run all incomplete phases end-to-end.

```text
/handoff start --roadmap
/handoff start --roadmap api-gateway
```

### Commands

| Command                       | Purpose                                         | Who  |
| ----------------------------- | ----------------------------------------------- | ---- |
| `/handoff`                    | Auto-detects role + state, does the right thing | Both |
| `/handoff start [phase]`      | Begin a new phase (plan + review cycle)         | Lead |
| `/handoff start [phase] impl` | Begin implementation review for a phase         | Lead |
| `/handoff status`             | Orientation, status check, drift reset          | Both |

**Human-in-the-loop**: add `--confirm` to pause for approval before each automatic send.

```bash
python -m ai_handoff watch --mode notify --confirm
```

> **Manual mode:** You can always run handoffs without automation by pasting `/handoff` command results between agents manually.

## Advanced Setup

```bash
cd ~/projects/myproject
python -m ai_handoff setup
python -m ai_handoff init
python -m ai_handoff session start
```

To force the manual backend:

```bash
python -m ai_handoff session start --backend manual
```

To create the terminals without auto-launching agents:

```bash
python -m ai_handoff session start --no-launch
```

## The Saloon

A graphical dashboard for monitoring and controlling handoff cycles:

```bash
python -m ai_handoff serve --dir ~/projects/myproject
```

## Configuration

Agents are defined in `ai-handoff.yaml`:

```yaml
agents:
  lead:
    name: claude
    command: claude
  reviewer:
    name: codex
    command: codex
```

## CLI Reference

```bash
python -m ai_handoff quickstart                          # Setup + init + session start
python -m ai_handoff session start                       # Auto-detect backend and launch agents
python -m ai_handoff session start --backend manual   # Force manual backend
python -m ai_handoff session start --no-launch          # Create terminals but skip agent launch
python -m ai_handoff session kill
python -m ai_handoff init
python -m ai_handoff setup
python -m ai_handoff state
python -m ai_handoff state diagnose
python -m ai_handoff watch --mode notify
python -m ai_handoff roadmap phases
python -m ai_handoff serve --dir .
python -m ai_handoff upgrade
python -m ai_handoff --help
```

## License

MIT
