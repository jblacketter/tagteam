"""
Setup script for AI Handoff Framework.

Usage:
    ai-handoff-setup [target_directory]
    python -m ai_handoff.setup [target_directory]
"""

import shutil
import sys
from pathlib import Path


def get_data_dir() -> Path:
    """Get the directory where package data files are stored."""
    return Path(__file__).parent / "data"


def main(target_dir: str = ".") -> None:
    """
    Copy framework files to the target project directory.

    Args:
        target_dir: Target directory (defaults to current directory)
    """
    source = get_data_dir()
    target = Path(target_dir).resolve()

    print("AI Handoff Framework Setup")
    print("==========================")
    print(f"Source: {source}")
    print(f"Target: {target}")
    print()

    # Verify source exists
    if not source.exists():
        print(f"Error: Data directory not found at {source}")
        print("The package may not be installed correctly.")
        return

    # Create directory structure
    dirs_to_create = [
        ".claude/skills",
        "docs/phases",
        "docs/handoffs",
        "docs/escalations",
        "docs/checklists",
        "templates",
    ]

    print("Creating directories...")
    for d in dirs_to_create:
        (target / d).mkdir(parents=True, exist_ok=True)

    # Copy skills
    print("Copying skills...")
    skills_src = source / ".claude" / "skills"
    skills_dst = target / ".claude" / "skills"
    if skills_src.exists():
        for f in skills_src.glob("*.md"):
            shutil.copy2(f, skills_dst / f.name)
            print(f"  - {f.name}")
    else:
        print(f"  Warning: Skills not found at {skills_src}")

    # Copy templates
    print("Copying templates...")
    templates_src = source / "templates"
    templates_dst = target / "templates"
    if templates_src.exists():
        for f in templates_src.glob("*.md"):
            shutil.copy2(f, templates_dst / f.name)
            print(f"  - {f.name}")
    else:
        print(f"  Warning: Templates not found at {templates_src}")

    # Copy checklists
    print("Copying checklists...")
    checklists_src = source / "checklists"
    checklists_dst = target / "docs" / "checklists"
    if checklists_src.exists():
        for f in checklists_src.glob("*.md"):
            shutil.copy2(f, checklists_dst / f.name)
            print(f"  - {f.name}")
    else:
        print(f"  Warning: Checklists not found at {checklists_src}")

    # Copy workflow docs
    print("Copying workflow documentation...")
    workflows_src = source / "workflows.md"
    if workflows_src.exists():
        shutil.copy2(workflows_src, target / "docs" / "workflows.md")
        print("  - workflows.md")
    else:
        print(f"  Warning: workflows.md not found at {workflows_src}")

    # Initialize files if they don't exist
    roadmap_dst = target / "docs" / "roadmap.md"
    if not roadmap_dst.exists():
        print("Creating roadmap template...")
        roadmap_src = source / "templates" / "roadmap.md"
        if roadmap_src.exists():
            shutil.copy2(roadmap_src, roadmap_dst)

    decision_log_dst = target / "docs" / "decision_log.md"
    if not decision_log_dst.exists():
        print("Creating decision log...")
        decision_log_src = source / "templates" / "decision_log.md"
        if decision_log_src.exists():
            shutil.copy2(decision_log_src, decision_log_dst)

    print()
    print("Setup complete!")
    print()
    print("Next steps:")
    print("  1. Run 'python -m ai_handoff init' to configure your agents")
    print("  2. Start your AI with: 'I am [agent]. Read ai-handoff.yaml and .claude/skills/'")
    print("  3. Then ask your AI to run /status or /plan create [phase]")


def cli():
    """Command-line entry point."""
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    main(target)


if __name__ == "__main__":
    cli()
