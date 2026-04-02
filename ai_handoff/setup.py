"""
Setup script for AI Handoff Framework.

Usage:
    ai-handoff-setup [target_directory]
    python -m ai_handoff.setup [target_directory]
"""

import shutil
import sys
from pathlib import Path

from ai_handoff.config import read_config, validate_config
from ai_handoff.templates import get_template_variables, render_template


def copy_md_file(src: Path, dst: Path, variables: dict[str, str]) -> None:
    """Copy a markdown file, applying variable substitution if variables provided."""
    content = src.read_text(encoding="utf-8")
    if variables:
        content = render_template(content, variables)
    dst.write_text(content, encoding="utf-8")


def get_data_dir() -> Path:
    """Get the directory where package data files are stored."""
    return Path(__file__).parent / "data"


def needs_setup(project_dir: str = ".") -> bool:
    """Check if framework setup is needed.

    Setup is considered complete when all of these exist:
    - .claude/skills/handoff/SKILL.md (skill directory)
    - templates/ directory with at least one .md file
    - docs/checklists/ directory with at least one .md file

    Intentionally excludes project-specific docs (roadmap, decision_log,
    workflows) which may be edited or removed by users.
    """
    target = Path(project_dir)

    skill = target / ".claude" / "skills" / "handoff" / "SKILL.md"
    if not skill.exists():
        return True

    templates = target / "templates"
    if not templates.exists() or not any(templates.glob("*.md")):
        return True

    checklists = target / "docs" / "checklists"
    if not checklists.exists() or not any(checklists.glob("*.md")):
        return True

    return False


def run_setup(project_dir: str = ".") -> None:
    """Idempotent setup wrapper. Skips if setup is already complete."""
    if not needs_setup(project_dir):
        print("Framework files already present — skipping setup.")
        return
    main(project_dir)


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

    # Read config for template variable substitution
    config_path = target / "ai-handoff.yaml"
    config = read_config(config_path)

    # Validate config if present (use 'is not None' so empty {} still gets validated)
    if config is not None:
        errors = validate_config(config)
        if errors:
            print("Warning: Config validation issues:")
            for err in errors:
                print(f"  - {err}")
            print()

    variables = get_template_variables(config)
    if variables:
        print(f"Using agent names from config: lead={variables.get('lead')}, reviewer={variables.get('reviewer')}")
    else:
        print("No config found - templates will have {{variable}} placeholders")
    print()

    # Remove deprecated flat-file skills from previous versions
    # Uses glob to catch any handoff-*.md files, not just known ones
    skills_dst = target / ".claude" / "skills"
    removed = []
    for old_file in skills_dst.glob("handoff-*.md"):
        old_file.unlink()
        removed.append(old_file.name)
    # Also remove bare handoff.md (not matched by handoff-*.md)
    bare_handoff = skills_dst / "handoff.md"
    if bare_handoff.exists():
        bare_handoff.unlink()
        removed.append("handoff.md")
    if removed:
        print(f"Removed {len(removed)} deprecated skill files:")
        for name in removed:
            print(f"  - {name}")
        print()

    # Copy skills (directory-based skills)
    print("Copying skills...")
    skills_src = source / ".claude" / "skills"
    if skills_src.exists():
        for f in skills_src.glob("*.md"):
            shutil.copy2(f, skills_dst / f.name)
            print(f"  - {f.name}")
        for d in skills_src.iterdir():
            if d.is_dir():
                dst_dir = skills_dst / d.name
                if dst_dir.exists():
                    shutil.rmtree(dst_dir)
                shutil.copytree(d, dst_dir)
                print(f"  - {d.name}/ (directory skill)")
    else:
        print(f"  Warning: Skills not found at {skills_src}")

    # Copy templates (with variable substitution)
    print("Copying templates...")
    templates_src = source / "templates"
    templates_dst = target / "templates"
    if templates_src.exists():
        for f in templates_src.glob("*.md"):
            copy_md_file(f, templates_dst / f.name, variables)
            print(f"  - {f.name}")
    else:
        print(f"  Warning: Templates not found at {templates_src}")

    # Copy checklists (with variable substitution)
    print("Copying checklists...")
    checklists_src = source / "checklists"
    checklists_dst = target / "docs" / "checklists"
    if checklists_src.exists():
        for f in checklists_src.glob("*.md"):
            copy_md_file(f, checklists_dst / f.name, variables)
            print(f"  - {f.name}")
    else:
        print(f"  Warning: Checklists not found at {checklists_src}")

    # Copy workflow docs (with variable substitution)
    print("Copying workflow documentation...")
    workflows_src = source / "workflows.md"
    if workflows_src.exists():
        copy_md_file(workflows_src, target / "docs" / "workflows.md", variables)
        print("  - workflows.md")
    else:
        print(f"  Warning: workflows.md not found at {workflows_src}")

    # Initialize files if they don't exist (with variable substitution)
    roadmap_dst = target / "docs" / "roadmap.md"
    if not roadmap_dst.exists():
        print("Creating roadmap template...")
        roadmap_src = source / "templates" / "roadmap.md"
        if roadmap_src.exists():
            copy_md_file(roadmap_src, roadmap_dst, variables)

    decision_log_dst = target / "docs" / "decision_log.md"
    if not decision_log_dst.exists():
        print("Creating decision log...")
        decision_log_src = source / "templates" / "decision_log.md"
        if decision_log_src.exists():
            copy_md_file(decision_log_src, decision_log_dst, variables)

    # Register this project for future upgrades
    from ai_handoff.registry import register_project
    register_project(str(target))

    print()
    print("Setup complete!")
    print()
    print("Next steps:")
    print("  Quick start:  python -m ai_handoff quickstart --dir .")
    print("  Or manually:  python -m ai_handoff init")
    print("                python -m ai_handoff session start --dir . --launch")
    print("  Windows/manual fallback:")
    print("                python -m ai_handoff session start --dir . --backend manual")
    print("                python -m ai_handoff watch --mode notify")


def cli():
    """Command-line entry point."""
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    main(target)


if __name__ == "__main__":
    cli()
