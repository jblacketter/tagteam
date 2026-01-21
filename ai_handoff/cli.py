"""
CLI for AI Handoff Framework.

Usage:
    python -m ai_handoff init       - Initialize agent configuration
    python -m ai_handoff setup [dir] - Copy framework files to a project
"""

import os
import sys
from pathlib import Path

# PyYAML is optional - we'll use simple string formatting if not available
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


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


def read_existing_config(config_path: Path) -> dict | None:
    """Read existing config file and return parsed data."""
    if not config_path.exists():
        return None

    try:
        content = config_path.read_text()
        if HAS_YAML:
            data = yaml.safe_load(content)
            return data
        else:
            # Simple parsing without PyYAML
            lead_name = None
            reviewer_name = None
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if 'lead:' in line and i + 1 < len(lines):
                    next_line = lines[i + 1]
                    if 'name:' in next_line:
                        lead_name = next_line.split('name:')[1].strip()
                elif 'reviewer:' in line and i + 1 < len(lines):
                    next_line = lines[i + 1]
                    if 'name:' in next_line:
                        reviewer_name = next_line.split('name:')[1].strip()
            if lead_name and reviewer_name:
                return {'agents': {'lead': {'name': lead_name}, 'reviewer': {'name': reviewer_name}}}
    except Exception:
        pass
    return None


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
        existing = read_existing_config(config_path)
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

    # Write config
    config_content = CONFIG_TEMPLATE.format(
        lead_name=lead_name,
        reviewer_name=reviewer_name
    )
    config_path.write_text(config_content)

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


def main() -> int:
    """Main CLI entry point."""
    if len(sys.argv) < 2:
        print("AI Handoff Framework")
        print()
        print("Usage: python -m ai_handoff <command>")
        print()
        print("Commands:")
        print("  init         Create ai-handoff.yaml configuration")
        print("  setup [dir]  Copy framework files to a project directory")
        print()
        return 1

    command = sys.argv[1].lower()

    if command == "init":
        return init_command()
    elif command == "setup":
        target = sys.argv[2] if len(sys.argv) > 2 else "."
        return setup_command(target)
    elif command in ["-h", "--help", "help"]:
        print("AI Handoff Framework")
        print()
        print("Usage: python -m ai_handoff <command>")
        print()
        print("Commands:")
        print("  init         Create ai-handoff.yaml configuration interactively")
        print("  setup [dir]  Copy framework files to a project directory")
        print()
        print("Workflow:")
        print("  1. Run 'python -m ai_handoff setup' to copy framework files")
        print("  2. Run 'python -m ai_handoff init' to configure your agents")
        print("  3. Start your AI with the getting started prompt")
        print()
        return 0
    else:
        print(f"Unknown command: {command}")
        print("Run 'python -m ai_handoff --help' for usage.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
