"""
Migration command for Tagteam.

Migrates projects that were set up before Phase 1 (without tagteam.yaml)
to use the new configuration-based workflow.
"""

import re
import shutil
from datetime import datetime
from pathlib import Path


def detect_agent_names(project_dir: Path) -> tuple[str, str]:
    """Scan handoff docs for agent names. Returns (lead, reviewer) or defaults.

    Handles names with spaces/punctuation by matching everything before " (Lead)" or " (Reviewer)".
    Falls back to defaults (Claude, Codex) if no matches found.
    """
    lead_name = "Claude"  # default
    reviewer_name = "Codex"  # default
    lead_found = False
    reviewer_found = False

    handoffs_dir = project_dir / "docs" / "handoffs"
    if handoffs_dir.exists():
        for md_file in sorted(handoffs_dir.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue

            # Match "**From:** <name> (Lead)" — name can include spaces/punctuation
            if not lead_found:
                if match := re.search(r"\*\*From:\*\*\s+(.+?)\s+\(Lead\)", content):
                    lead_name = match.group(1).strip()
                    lead_found = True
            # Match "**To:** <name> (Reviewer)"
            if not reviewer_found:
                if match := re.search(r"\*\*To:\*\*\s+(.+?)\s+\(Reviewer\)", content):
                    reviewer_name = match.group(1).strip()
                    reviewer_found = True

            if lead_found and reviewer_found:
                break

    return lead_name, reviewer_name


def migrate_command(args: list[str]) -> int:
    """Migrate a project to use tagteam configuration.

    Args:
        args: Command-line arguments (supports --dry-run)

    Returns:
        Exit code (0 for success, 1 for error)
    """
    dry_run = "--dry-run" in args
    project_dir = Path(".")

    # 1. Check if tagteam.yaml exists
    config_path = project_dir / "tagteam.yaml"
    if config_path.exists():
        print("tagteam.yaml already exists. Nothing to migrate.")
        return 0

    # 2. Detect existing setup
    has_skills = (project_dir / ".claude" / "skills").exists()
    has_templates = (project_dir / "templates").exists()
    has_docs = (project_dir / "docs").exists()

    if not has_skills and not has_templates and not has_docs:
        print("No existing setup detected.")
        print("Run 'python -m tagteam setup' to set up a new project.")
        return 1

    # 3. Detect agent names from existing docs
    lead_name, reviewer_name = detect_agent_names(project_dir)
    detected = lead_name != "Claude" or reviewer_name != "Codex"

    # 4. Preview or execute
    if dry_run:
        print("Migration preview (--dry-run):")
        print()
        print(f"  Would create tagteam.yaml with:")
        print(f"    Lead: {lead_name}" + (" (detected)" if lead_name != "Claude" else " (default)"))
        print(f"    Reviewer: {reviewer_name}" + (" (detected)" if reviewer_name != "Codex" else " (default)"))
        print()
        if has_templates:
            print(f"  Would backup templates/ to tagteam-backups/<timestamp>/")
        print()
        print("Run without --dry-run to execute migration.")
        return 0

    # 5. Create backup if templates exist
    if has_templates:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        backup_dir = project_dir / "tagteam-backups" / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(project_dir / "templates", backup_dir / "templates")
        print(f"Backed up templates/ to {backup_dir}/")

    # 6. Write config
    from tagteam.cli import write_config
    write_config(str(project_dir), lead_name, reviewer_name)
    print(f"Created tagteam.yaml")
    print(f"  Lead: {lead_name}" + (" (detected from docs)" if detected and lead_name != "Claude" else ""))
    print(f"  Reviewer: {reviewer_name}" + (" (detected from docs)" if detected and reviewer_name != "Codex" else ""))

    # 7. Prompt to re-run setup
    print()
    print("To update templates with agent names, run:")
    print("  python -m tagteam setup")
    return 0
