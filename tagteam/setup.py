"""
Setup script for Tagteam.

Usage:
    tagteam-setup [target_directory]
    python -m tagteam.setup [target_directory]
"""

import shutil
import sys
from pathlib import Path

from tagteam.config import read_config, validate_config
from tagteam.plugin import (PluginStatus, plugin_status,   # noqa: F401 — re-exported
                            vendored_skill_provenance,
                            legacy_handoff_skill_candidates, render_legacy_skill_note)
from tagteam.templates import render_template

SKILL_RELDIR = Path(".claude") / "skills" / "handoff"


def copy_md_file(src: Path, dst: Path, variables: dict[str, str] | None = None) -> None:
    """Copy a markdown file, applying variable substitution if variables provided."""
    content = src.read_text(encoding="utf-8")
    if variables:
        content = render_template(content, variables)
    dst.write_text(content, encoding="utf-8")


def get_data_dir() -> Path:
    """Get the directory where package data files are stored."""
    return Path(__file__).parent / "data"


def needs_setup(project_dir: str = ".", plugin: PluginStatus | None = None) -> bool:
    """Check if framework setup is needed.

    Readiness is independent of Claude's optional plugin or vendored skill.
    The plugin argument remains accepted for API compatibility.
    """
    target = Path(project_dir)
    from tagteam.contract import contract_text
    try:
        if not contract_text().strip():
            return True
    except (OSError, UnicodeError):
        return True

    templates = target / "templates"
    if not templates.exists() or not any(templates.glob("*.md")):
        return True

    checklists = target / "docs" / "checklists"
    if not checklists.exists() or not any(checklists.glob("*.md")):
        return True

    return False


def run_setup(project_dir: str = ".", *, no_plugin: bool = False,
              report_user_skills: bool = True) -> None:
    """Idempotent setup wrapper. Skips if setup is already complete."""
    if not needs_setup(project_dir) and not (
            no_plugin and not (Path(project_dir) / SKILL_RELDIR / "SKILL.md").is_file()):
        print("Framework files already present — skipping setup.")
        # Phase 49: an already-configured project (quickstart rerun) still
        # gets the one read-only note per invocation.
        if report_user_skills and not no_plugin:
            report_legacy_user_skills()
        return
    main(project_dir, no_plugin=no_plugin, report_user_skills=report_user_skills)


def report_legacy_user_skills() -> bool:
    """Phase 49: print the read-only note about user-level `handoff*` skills
    that may conflict with the plugin. Returns True if anything was printed.
    Never modifies anything."""
    note = render_legacy_skill_note(legacy_handoff_skill_candidates())
    if note:
        print(note)
        return True
    return False


def _sync_handoff_skill(source: Path, target: Path, *, no_plugin: bool) -> None:
    """Phase 48: vendor the handoff skill, or remove the vendored copy when
    the plugin serves it. Removal is gated on content provenance — only a
    directory holding exactly one known tagteam-vendored SKILL.md is deleted;
    anything else is kept, reported, and not vendored over."""
    skills_dst = target / ".claude" / "skills"
    skill_dir = target / SKILL_RELDIR
    status = PluginStatus(False, "--no-plugin") if no_plugin else plugin_status(target)
    print(f"plugin: {status}")
    if status.installed:
        prov = vendored_skill_provenance(skill_dir)
        if prov.removable:
            shutil.rmtree(skill_dir)
            print(f"  removed vendored handoff skill ({prov.reason}) — served by the plugin")
        elif prov.reason == "absent":
            print("  handoff skill served by the plugin — nothing to vendor")
        else:
            print(f"  kept {SKILL_RELDIR}/: {prov.reason}")
        return
    print("Copying skills...")
    skills_src = source / ".claude" / "skills"
    if not skills_src.exists():
        print(f"  Warning: Skills not found at {skills_src}")
        return
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


def main(target_dir: str = ".", *, no_plugin: bool = False,
         report_user_skills: bool = True) -> None:
    """
    Copy framework files to the target project directory.

    Args:
        target_dir: Target directory (defaults to current directory)
        no_plugin: force vendoring the handoff skill even when the plugin is
            installed (Phase 48). There is no flag that forces removal.
        report_user_skills: print the Phase 49 note about user-level
            `handoff*` skills that may conflict with the plugin. `upgrade`
            passes False per project and prints one aggregate note itself.
    """
    source = get_data_dir()
    target = Path(target_dir).resolve()

    print("Tagteam Setup")
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

    # Validate project configuration without baking roles into shipped templates
    config_path = target / "tagteam.yaml"
    config = read_config(config_path)

    # Validate config if present (use 'is not None' so empty {} still gets validated)
    if config is not None:
        errors = validate_config(config)
        if errors:
            print("Warning: Config validation issues:")
            for err in errors:
                print(f"  - {err}")
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

    # Handoff skill: vendor, or hand over to the plugin (Phase 48)
    _sync_handoff_skill(source, target, no_plugin=no_plugin)
    if report_user_skills and not no_plugin:
        report_legacy_user_skills()

    # Copy templates
    print("Copying templates...")
    templates_src = source / "templates"
    templates_dst = target / "templates"
    if templates_src.exists():
        for f in templates_src.glob("*.md"):
            copy_md_file(f, templates_dst / f.name)
            print(f"  - {f.name}")
    else:
        print(f"  Warning: Templates not found at {templates_src}")

    # Copy checklists
    print("Copying checklists...")
    checklists_src = source / "checklists"
    checklists_dst = target / "docs" / "checklists"
    if checklists_src.exists():
        for f in checklists_src.glob("*.md"):
            copy_md_file(f, checklists_dst / f.name)
            print(f"  - {f.name}")
    else:
        print(f"  Warning: Checklists not found at {checklists_src}")

    # Copy workflow docs
    print("Copying workflow documentation...")
    workflows_src = source / "workflows.md"
    if workflows_src.exists():
        copy_md_file(workflows_src, target / "docs" / "workflows.md")
        print("  - workflows.md")
    else:
        print(f"  Warning: workflows.md not found at {workflows_src}")

    # Initialize files if they don't exist
    roadmap_dst = target / "docs" / "roadmap.md"
    if not roadmap_dst.exists():
        print("Creating roadmap template...")
        roadmap_src = source / "templates" / "roadmap.md"
        if roadmap_src.exists():
            copy_md_file(roadmap_src, roadmap_dst)

    decision_log_dst = target / "docs" / "decision_log.md"
    if not decision_log_dst.exists():
        print("Creating decision log...")
        decision_log_src = source / "templates" / "decision_log.md"
        if decision_log_src.exists():
            copy_md_file(decision_log_src, decision_log_dst)

    # Instruction adapters are seeded only when absent. Existing project rules
    # belong to the user, even when they contain outdated workflow references.
    for name in ("AGENTS.md", "CLAUDE.md"):
        pointer = target / name
        try:
            with pointer.open("x", encoding="utf-8") as out:
                out.write("# Project workflow\n\nRead `tagteam.yaml` for current roles, "
                          "`docs/workflows.md` for onboarding, and run "
                          "`tagteam contract` for the authoritative workflow.\n")
        except FileExistsError:
            pass

    # Register this project for future upgrades
    from tagteam.registry import register_project
    register_project(str(target))

    print()
    print("Setup complete!")
    print()
    print("Next steps:")
    print("  Quick start:  tagteam quickstart")
    print("  Or manually:  tagteam init")
    print("                tagteam session start")
    print("  Windows/manual fallback:")
    print("                tagteam session start --backend manual")
    print("                tagteam watch --mode notify")


def cli():
    """Command-line entry point."""
    args = [a for a in sys.argv[1:] if a != "--no-plugin"]
    target = args[0] if args else "."
    main(target, no_plugin="--no-plugin" in sys.argv[1:])


if __name__ == "__main__":
    cli()
