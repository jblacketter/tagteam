"""
CLI for AI Handoff Framework.

Usage:
    python -m ai_handoff init        - Initialize agent configuration
    python -m ai_handoff setup [dir] - Copy framework files to a project
    python -m ai_handoff migrate     - Migrate legacy projects to use config
    python -m ai_handoff watch       - Start the watcher daemon
    python -m ai_handoff state       - View/update orchestration state
    python -m ai_handoff session     - Manage tmux session
    python -m ai_handoff serve       - Start the web dashboard server
"""

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

GETTING_STARTED = """
Getting Started
===============
Tell your AI agent:

  "Read ai-handoff.yaml to see your role, then read .claude/skills/ for the workflow."

Workflow:
  1. Lead creates phase plans (/handoff-plan create [phase])
  2. Lead creates handoffs for review (/handoff-handoff plan [phase])
  3. Reviewer reviews and provides feedback (/handoff-review plan [phase])
  4. Lead addresses feedback or proceeds to implementation
  5. Repeat for implementation review
"""


def prompt_input(prompt: str, valid_options: list[str] | None = None, lowercase: bool = True) -> str:
    """Get user input with optional validation.

    Args:
        prompt: The prompt to display
        valid_options: List of valid options (compared lowercase)
        lowercase: If True, return lowercase value. If False, preserve case.
    """
    while True:
        raw_value = input(prompt).strip()
        if not raw_value:
            print("  Please enter a value.")
            continue

        # For validation, always compare lowercase
        check_value = raw_value.lower()
        if valid_options and check_value not in valid_options:
            print(f"  Please enter one of: {', '.join(valid_options)}")
            continue

        # Return based on lowercase flag
        return check_value if lowercase else raw_value


def write_config(target_dir: str, lead_name: str, reviewer_name: str) -> Path:
    """Write ai-handoff.yaml to target_dir. Non-interactive.

    This can be called from the CLI init flow, the TUI, or the web dashboard
    without requiring stdin.
    """
    config_path = Path(target_dir) / "ai-handoff.yaml"
    config_content = CONFIG_TEMPLATE.format(
        lead_name=lead_name,
        reviewer_name=reviewer_name,
    )
    config_path.write_text(config_content, encoding="utf-8")
    return config_path


def init_command() -> int:
    """Interactive init command to create ai-handoff.yaml."""
    config_path = Path("ai-handoff.yaml")

    print()
    print("AI Handoff Setup")
    print("================")
    print("This framework coordinates work between two AI agents.")
    print()

    # Check for existing config file (separate from parsing)
    if config_path.exists():
        existing = read_config(config_path)
        if existing:
            agents = existing.get('agents', {})
            lead = agents.get('lead', {}).get('name', 'unknown')
            reviewer = agents.get('reviewer', {}).get('name', 'unknown')

            print(f"ai-handoff.yaml already exists with:")
            print(f"  Lead: {lead}")
            print(f"  Reviewer: {reviewer}")
        else:
            print("ai-handoff.yaml already exists but could not be parsed.")
            print("(File may be empty or malformed)")

        print()
        overwrite = prompt_input("Overwrite? (y/n): ", ['y', 'n', 'yes', 'no'])
        if overwrite not in ['y', 'yes']:
            print("Aborted.")
            return 0
        print()

    # Collect agent info
    print("Enter the names of your two AI agents and their roles.")
    print()

    agent1_name = prompt_input("Agent 1 name: ", lowercase=False)
    agent1_role = prompt_input("Agent 1 role (lead/reviewer): ", ['lead', 'reviewer'])
    print()

    agent2_name = prompt_input("Agent 2 name: ", lowercase=False)

    # Determine required role for agent 2
    required_role = 'reviewer' if agent1_role == 'lead' else 'lead'
    agent2_role = prompt_input(f"Agent 2 role (lead/reviewer): ", ['lead', 'reviewer'])

    # Validate roles
    while agent2_role == agent1_role:
        print(f"  You need one lead and one reviewer. Agent 1 is already the {agent1_role}.")
        agent2_role = prompt_input(f"  Please enter '{required_role}': ", [required_role])

    print()

    # Determine lead and reviewer
    if agent1_role == 'lead':
        lead_name = agent1_name
        reviewer_name = agent2_name
    else:
        lead_name = agent2_name
        reviewer_name = agent1_name

    # Write config using the shared non-interactive function
    write_config(".", lead_name, reviewer_name)

    print(f"Created ai-handoff.yaml")
    print(f"  Lead: {lead_name}")
    print(f"  Reviewer: {reviewer_name}")
    print()
    print(GETTING_STARTED)

    return 0


def setup_command(target_dir: str = ".") -> int:
    """Copy framework files to target directory (delegates to setup.py)."""
    from ai_handoff.setup import main as setup_main
    setup_main(target_dir)
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
        print(f"{'=' * 60}")
        print(f"Project: {project_dir}")
        print(f"{'=' * 60}")
        try:
            setup_main(project_dir)
        except Exception as e:
            print(f"  ERROR: {e}")
            failed.append(project_dir)
        print()

    if failed:
        print(f"Completed with {len(failed)} error(s):")
        for p in failed:
            print(f"  - {p}")
        return 1

    print(f"All {len(projects)} project(s) upgraded successfully.")
    return 0


HELP_TEXT = """\
AI Handoff Framework

Usage: python -m ai_handoff <command>

Commands:
  init          Create ai-handoff.yaml configuration interactively
  setup [dir]   Copy framework files to a project directory
  migrate       Migrate legacy projects to use ai-handoff.yaml
  watch         Start the watcher daemon for automated orchestration
  roadmap       Query roadmap phases and build execution queue
  state         View or update the orchestration state file
  session       Manage orchestration session (start/kill)
  serve         Start the web dashboard server
  tui           Launch the Handoff Saloon terminal UI
  upgrade       Re-run setup on all registered projects (after pip upgrade)

Workflow:
  1. Run 'python -m ai_handoff setup' to copy framework files
  2. Run 'python -m ai_handoff init' to configure your agents
  3. Start your AI with the getting started prompt

Automated orchestration (iTerm2):
  1. Run 'python -m ai_handoff session start --dir ~/projects/myproject'
  2. Start Claude Code in the Lead tab, Codex in the Reviewer tab
  3. Press Enter in the Watcher tab to begin monitoring

  Use --backend tmux for legacy tmux-based orchestration.

Dashboard:
  Run 'python -m ai_handoff serve --dir ~/projects/myproject' to open
  the Handoff Saloon dashboard at http://localhost:8080
  For a new project, the Mayor will guide you through setup.

Terminal UI:
  Run 'python -m ai_handoff tui --dir ~/projects/myproject' to open
  the Handoff Saloon in your terminal (requires: pip install ai-handoff[tui])
"""


def main() -> int:
    """Main CLI entry point."""
    if len(sys.argv) < 2:
        print(HELP_TEXT)
        return 1

    command = sys.argv[1].lower()

    if command == "init":
        return init_command()
    elif command == "setup":
        target = sys.argv[2] if len(sys.argv) > 2 else "."
        return setup_command(target)
    elif command == "migrate":
        from ai_handoff.migrate import migrate_command
        return migrate_command(sys.argv[2:])
    elif command == "watch":
        from ai_handoff.watcher import watch_command
        return watch_command(sys.argv[2:])
    elif command == "roadmap":
        from ai_handoff.roadmap import roadmap_command
        return roadmap_command(sys.argv[2:])
    elif command == "state":
        from ai_handoff.state import state_command
        return state_command(sys.argv[2:])
    elif command == "session":
        from ai_handoff.session import session_command
        return session_command(sys.argv[2:])
    elif command == "serve":
        from ai_handoff.server import serve_command
        return serve_command(sys.argv[2:])
    elif command == "tui":
        try:
            from ai_handoff.tui import tui_command
        except ImportError:
            print("The TUI requires the 'textual' package.")
            print("Install it with: pip install ai-handoff[tui]")
            return 1
        return tui_command(sys.argv[2:])
    elif command == "upgrade":
        return upgrade_command()
    elif command in ["-h", "--help", "help"]:
        print(HELP_TEXT)
        return 0
    else:
        print(f"Unknown command: {command}")
        print("Run 'python -m ai_handoff --help' for usage.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
