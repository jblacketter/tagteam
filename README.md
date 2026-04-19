# Tagteam

A collaboration framework for structured AI-to-AI handoffs with human oversight. One AI leads, another reviews, and you arbitrate — the whole cycle runs phase by phase from a roadmap.

## How it works

- **Lead** (one AI agent) plans each phase and implements the approved plan.
- **Reviewer** (a second AI agent) reviews both the plan and the implementation.
- **Arbiter** (you, the human) breaks ties and approves phases.

Work progresses phase by phase. Each phase is listed in `docs/roadmap.md` and goes through two review cycles: plan, then implementation. If the two agents can't make progress in 10 rounds, control escalates to the human arbiter.

State is tracked in `handoff-state.json` (current turn) and `docs/handoffs/<phase>_<type>_rounds.jsonl` + `_status.json` (per-cycle rounds). Either agent can pick up where the other left off at any time.

## Quick Start

```bash
pip install tagteam
cd ~/projects/myproject
tagteam quickstart
```

You'll be prompted for your two agent names, then quickstart sets up the workspace and starts a handoff session. It auto-detects the best terminal backend available on your machine:

- **iTerm2** (macOS, default when iTerm2 is installed) — opens three labeled tabs in a single window, auto-launching iTerm2 if it isn't already running.
- **tmux** (Linux, WSL, or macOS without iTerm2) — creates one `tmux` session with three labeled panes.
- **manual** (anywhere else, including Windows without WSL) — prints the three commands for you to run in terminals you open yourself.

When quickstart finishes it prints what to paste into the Lead and Reviewer agents to kick off the first handoff. Override the auto-detection with `--backend iterm2|tmux|manual` if you need a specific one.

## Running a handoff

**Single phase** — start a plan review, let the watcher handle the back-and-forth, and stop when the phase completes.

```text
/handoff start my-phase
```

**Full roadmap** — run all incomplete phases end-to-end.

```text
/handoff start --roadmap
/handoff start --roadmap api-gateway
```

| Command                         | Purpose                                         | Who  |
| ------------------------------- | ----------------------------------------------- | ---- |
| `/handoff`                      | Auto-detects role + state, does the right thing | Both |
| `/handoff start [phase]`        | Begin a new phase (plan + review cycle)         | Lead |
| `/handoff start [phase] impl`   | Begin implementation review for a phase         | Lead |
| `/handoff status`               | Orientation, status check, drift reset          | Both |

**Human-in-the-loop** — add `--confirm` to pause for approval before each automatic send.

```bash
tagteam watch --mode notify --confirm
```

## Other platforms

<details>
<summary>tmux (explicit invocation)</summary>

```bash
tagteam quickstart --backend tmux
```

Creates one `tmux` session named `tagteam` with three labeled panes (Lead, Watcher, Reviewer). Attach later with `tmux attach -t tagteam`.

</details>

<details>
<summary>Windows / manual fallback</summary>

On Windows without WSL, terminal automation isn't available. Quickstart prints the commands for you to run yourself in three terminals:

```bash
tagteam quickstart --backend manual
```

You can also run each step individually:

```bash
tagteam setup
tagteam init
tagteam session start --backend manual
tagteam watch --mode notify
```

For full automation on Windows today, use WSL with `tmux`.

</details>

<details>
<summary>Advanced setup (run each step yourself)</summary>

```bash
tagteam setup               # copy skills, templates, docs
tagteam init                # interactive agent config → tagteam.yaml
tagteam session start       # create terminals and auto-launch agents
```

Options:

- `tagteam session start --no-launch` — create terminals but don't start agents
- `tagteam session start --backend <name>` — force a specific backend
- `tagteam session kill` — close the current session

> **Manual mode:** you can always run handoffs without any automation by pasting `/handoff` output between agents yourself.

</details>

## The Saloon

A graphical dashboard for monitoring and controlling handoff cycles:

```bash
tagteam serve --dir ~/projects/myproject
```

## Configuration

Agents are defined in `tagteam.yaml`:

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
tagteam quickstart                     # Setup + init + session start
tagteam session start                  # Auto-detect backend, launch agents
tagteam session start --backend manual # Force manual backend
tagteam session start --no-launch      # Create terminals, skip agent launch
tagteam session kill
tagteam init
tagteam setup
tagteam state
tagteam state diagnose
tagteam watch --mode notify
tagteam roadmap phases
tagteam serve --dir .
tagteam upgrade
tagteam --help
```

## License

MIT
