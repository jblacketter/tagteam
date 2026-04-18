"""
CLI for AI Handoff Framework.

Usage:
    python -m ai_handoff init        - Initialize agent configuration
    python -m ai_handoff setup [dir] - Copy framework files to a project
    python -m ai_handoff migrate     - Migrate legacy projects to use config
    python -m ai_handoff watch       - Start the watcher daemon
    python -m ai_handoff state       - View/update orchestration state
    python -m ai_handoff session     - Manage orchestration sessions
    python -m ai_handoff serve       - Start the web dashboard server
"""

from __future__ import annotations

import sys
from pathlib import Path

from ai_handoff.config import read_config


CONFIG_TEMPLATE = """# AI Handoff Configuration
# Defines the two AI agents and their roles in the collaboration workflow.

agents:
  lead:
    name: {lead_name}
  reviewer:
    name: {reviewer_name}
"""

HANDOFF_EXPLAINER = """
How the handoff works:

  Lead (one AI agent) plans each phase and implements the approved plan.
  Reviewer (a second AI agent) reviews both the plan and the implementation.
  Arbiter (you, the human) breaks ties and approves phases.

Work progresses phase-by-phase. Each phase is listed in docs/roadmap.md and
goes through two review cycles: plan, then implementation. If the two agents
can't make progress in 10 rounds, control escalates to the human arbiter.

State is tracked in handoff-state.json (current turn) and
docs/handoffs/<phase>_<type>_rounds.jsonl plus <phase>_<type>_status.json
(per-cycle rounds). Either agent can pick up where the other left off at
any time.
"""

GETTING_STARTED = """
Getting Started
===============
Start a session with agents and watcher (run from project root):

  ai-handoff session start

If you are on Windows or another unsupported platform, use the manual backend:

  ai-handoff session start --backend manual
  ai-handoff watch --mode notify

Or use quickstart (runs setup + init + session with backend auto-detection):

  ai-handoff quickstart
"""


def prompt_input(
    prompt: str,
    valid_options: list[str] | None = None,
    lowercase: bool = True,
) -> str:
    """Get user input with optional validation."""
    while True:
        raw_value = input(prompt).strip()
        if not raw_value:
            print("  Please enter a value.")
            continue

        check_value = raw_value.lower()
        if valid_options and check_value not in valid_options:
            print(f"  Please enter one of: {', '.join(valid_options)}")
            continue

        return check_value if lowercase else raw_value


def write_config(target_dir: str, lead_name: str, reviewer_name: str) -> Path:
    """Write ai-handoff.yaml to target_dir. Non-interactive."""
    config_path = Path(target_dir) / "ai-handoff.yaml"
    config_content = CONFIG_TEMPLATE.format(
        lead_name=lead_name,
        reviewer_name=reviewer_name,
    )
    config_path.write_text(config_content, encoding="utf-8")
    return config_path


def needs_init(project_dir: str = ".") -> bool:
    """Check if agent configuration is needed."""
    return not (Path(project_dir) / "ai-handoff.yaml").exists()


def run_init(project_dir: str = ".", show_explainer: bool = False) -> bool:
    """Run interactive init if config is missing. Requires TTY.

    show_explainer=False by default so callers like quickstart can print the
    explainer themselves exactly once. Standalone CLI dispatch passes True.
    """
    if not needs_init(project_dir):
        print("Agent configuration already exists; skipping init.")
        return True

    if not sys.stdin.isatty():
        print("Error: No ai-handoff.yaml found and stdin is not interactive.")
        print("  Run 'ai-handoff init' interactively first.")
        return False

    import os

    original_dir = os.getcwd()
    try:
        os.chdir(project_dir)
        init_command(show_explainer=show_explainer)
    finally:
        os.chdir(original_dir)
    return True


def init_command(show_explainer: bool = True) -> int:
    """Interactive init command to create ai-handoff.yaml.

    Prompts for two agent names: lead first, reviewer second. No role prompt —
    order defines role.
    """
    config_path = Path("ai-handoff.yaml")

    print()
    print("AI Handoff Setup")
    print("================")
    print("This framework coordinates work between two AI agents.")
    print()

    if config_path.exists():
        existing = read_config(config_path)
        if existing:
            agents = existing.get("agents", {})
            lead = agents.get("lead", {}).get("name", "unknown")
            reviewer = agents.get("reviewer", {}).get("name", "unknown")

            print("ai-handoff.yaml already exists with:")
            print(f"  Lead: {lead}")
            print(f"  Reviewer: {reviewer}")
        else:
            print("ai-handoff.yaml already exists but could not be parsed.")
            print("(File may be empty or malformed)")

        print()
        overwrite = prompt_input("Overwrite? (y/n): ", ["y", "n", "yes", "no"])
        if overwrite not in ["y", "yes"]:
            print("Aborted.")
            return 0
        print()

    print("Enter the names of your two AI agents (first is Lead, second is Reviewer).")
    print()

    lead_name = prompt_input("Lead agent name: ", lowercase=False)
    reviewer_name = prompt_input("Reviewer agent name: ", lowercase=False)
    print()

    write_config(".", lead_name, reviewer_name)

    print("Created ai-handoff.yaml")
    print(f"  Lead: {lead_name}")
    print(f"  Reviewer: {reviewer_name}")

    if show_explainer:
        print(HANDOFF_EXPLAINER)
    print(GETTING_STARTED)
    return 0


def setup_command(target_dir: str = ".") -> int:
    """Copy framework files to target directory."""
    from ai_handoff.setup import main as setup_main

    setup_main(target_dir)
    return 0


_BACKEND_SURFACE = {
    "iterm2": "tab",
    "tmux": "pane",
    "manual": "terminal",
}


def _print_priming_box(lead_name: str, reviewer_name: str, surface: str) -> None:
    """Print a boxed 'SESSION READY' message with backend-appropriate terminology."""
    prime_body = (
        "Read ai-handoff.yaml and .claude/skills/handoff/"
        "SKILL.md, then type /handoff"
    )
    lines = [
        "SESSION READY",
        "",
        f"In the Lead {surface}, tell {lead_name}:",
        f'  "{prime_body}"',
        "",
        f"In the Reviewer {surface}, tell {reviewer_name} the same.",
    ]
    width = max(len(line) for line in lines) + 4
    print("╔" + "═" * (width - 2) + "╗")
    for line in lines:
        print("║ " + line.ljust(width - 4) + " ║")
    print("╚" + "═" * (width - 2) + "╝")


def quickstart_command(args: list[str]) -> int:
    """Run setup + init + session start in one command."""
    from ai_handoff.session import SUPPORTED_BACKENDS, default_backend, ensure_session
    from ai_handoff.setup import run_setup

    project_dir = "."
    backend = None
    i = 0
    while i < len(args):
        if args[i] == "--dir" and i + 1 < len(args):
            project_dir = args[i + 1]
            i += 2
        elif args[i] == "--backend" and i + 1 < len(args):
            backend = args[i + 1]
            i += 2
        else:
            i += 1

    if backend is not None and backend not in SUPPORTED_BACKENDS:
        print(f"Invalid backend: {backend}. Use 'iterm2', 'tmux', or 'manual'.")
        return 1

    project_dir = str(Path(project_dir).resolve())

    print("AI Handoff - Quick Start")
    print("========================")
    print(f"Project: {project_dir}")
    print()

    print("[1/3] Framework setup...")
    run_setup(project_dir)
    print()

    print("[2/3] Agent configuration...")
    if not run_init(project_dir, show_explainer=False):
        return 1
    print()

    print("[3/3] Starting session...")
    outcome = ensure_session(project_dir, backend, launch=True)
    if outcome == "error":
        return 1

    effective_backend = backend or default_backend()
    surface = _BACKEND_SURFACE.get(effective_backend, "terminal")

    config = read_config(Path(project_dir) / "ai-handoff.yaml") or {}
    agents = config.get("agents", {})
    lead_name = agents.get("lead", {}).get("name", "Lead")
    reviewer_name = agents.get("reviewer", {}).get("name", "Reviewer")

    print(HANDOFF_EXPLAINER)

    if outcome == "exists":
        print("Session already running. Switch to it to continue.")
        return 0

    _print_priming_box(lead_name, reviewer_name, surface)
    return 0


def upgrade_command() -> int:
    """Re-run setup on all registered projects."""
    from ai_handoff.registry import get_registered_projects
    from ai_handoff.setup import main as setup_main

    projects = get_registered_projects()

    if not projects:
        print("No registered projects found.")
        print()
        print("Projects are registered automatically when you run 'ai-handoff setup'.")
        print("Run 'ai-handoff setup <dir>' in each project directory first.")
        return 0

    print(f"Upgrading {len(projects)} registered project(s)...")
    print()

    failed = []
    for project_dir in projects:
        print("=" * 60)
        print(f"Project: {project_dir}")
        print("=" * 60)
        try:
            setup_main(project_dir)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            failed.append(project_dir)
        print()

    if failed:
        print(f"Completed with {len(failed)} error(s):")
        for project_dir in failed:
            print(f"  - {project_dir}")
        return 1

    print(f"All {len(projects)} project(s) upgraded successfully.")
    return 0


HELP_TEXT = """\
AI Handoff Framework

Usage: ai-handoff <command>

Quick start (from project root):
  ai-handoff quickstart

  This runs setup, agent configuration, and session start in one command.
  The session backend is auto-detected unless you pass --backend.

Commands:
  quickstart    Setup + init + session start in one command
  init          Create ai-handoff.yaml configuration interactively
  setup [dir]   Copy framework files to a project directory
  session       Manage orchestration session (start/kill/attach)
  watch         Start the watcher daemon for automated orchestration
  state         View or update the orchestration state file
  roadmap       Query roadmap phases and build execution queue
  cycle         Manage cycle documents (init, add, status, rounds, render)
  serve         Start the web dashboard server
  tui           Launch the Handoff Saloon terminal UI
  migrate       Migrate legacy projects to use ai-handoff.yaml
  upgrade       Re-run setup on all registered projects (after pip upgrade)

Advanced setup (individual steps, from project root):
  ai-handoff setup
  ai-handoff init
  ai-handoff session start

Manual workflow fallback:
  ai-handoff session start --backend manual
  ai-handoff watch --mode notify
"""


def main() -> int:
    """Main CLI entry point."""
    if len(sys.argv) < 2:
        print(HELP_TEXT)
        return 1

    command = sys.argv[1].lower()

    if command == "quickstart":
        return quickstart_command(sys.argv[2:])
    if command == "init":
        return init_command()
    if command == "setup":
        target = sys.argv[2] if len(sys.argv) > 2 else "."
        return setup_command(target)
    if command == "migrate":
        from ai_handoff.migrate import migrate_command

        return migrate_command(sys.argv[2:])
    if command == "watch":
        from ai_handoff.watcher import watch_command

        return watch_command(sys.argv[2:])
    if command == "roadmap":
        from ai_handoff.roadmap import roadmap_command

        return roadmap_command(sys.argv[2:])
    if command == "cycle":
        from ai_handoff.cycle import cycle_command

        return cycle_command(sys.argv[2:])
    if command == "state":
        from ai_handoff.state import state_command

        return state_command(sys.argv[2:])
    if command == "session":
        from ai_handoff.session import session_command

        return session_command(sys.argv[2:])
    if command == "serve":
        from ai_handoff.server import serve_command

        return serve_command(sys.argv[2:])
    if command == "tui":
        try:
            from ai_handoff.tui import tui_command
        except ImportError:
            print("The TUI requires the 'textual' package.")
            print("Install it with: pip install ai-handoff[tui]")
            return 1
        return tui_command(sys.argv[2:])
    if command == "upgrade":
        return upgrade_command()
    if command in ["-h", "--help", "help"]:
        print(HELP_TEXT)
        return 0

    print(f"Unknown command: {command}")
    print("Run 'ai-handoff --help' for usage.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
