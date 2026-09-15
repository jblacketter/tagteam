"""
Setup script for Tagteam.

Usage:
    tagteam-setup [target_directory] [--no-plugin] [--preview] [--accept PATH]... [--force]
    python -m tagteam.setup [target_directory] [...]

Since Phase 52 this is a thin caller of :mod:`tagteam.framework`: managed
files are classified against the package and the project's manifest, only
provably tagteam-written content is refreshed, everything else is kept and
reported. See that module for the rules.
"""

import sys
from pathlib import Path

from tagteam.config import read_config, validate_config
from tagteam.plugin import (PluginStatus, plugin_status,   # noqa: F401 — re-exported
                            vendored_skill_provenance,
                            legacy_handoff_skill_candidates, render_legacy_skill_note)
from tagteam.templates import render_template

SKILL_RELDIR = Path(".claude") / "skills" / "handoff"


def copy_md_file(src: Path, dst: Path, variables: dict[str, str] | None = None) -> None:
    """Copy a markdown file, applying variable substitution if variables provided.
    Compatibility helper for older/custom callers; framework files no longer
    go through it."""
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


_POINTER = ("# Project workflow\n\nRead `tagteam.yaml` for current roles, "
            "`docs/workflows.md` for onboarding, and run "
            "`tagteam contract` for the authoritative workflow.\n")


def main(target_dir: str = ".", *, no_plugin: bool = False,
         report_user_skills: bool = True, preview: bool = False,
         accept: tuple[str, ...] | list[str] = (), force: bool = False) -> int:
    """
    Bring the framework files in ``target_dir`` up to the installed package.

    Args:
        target_dir: Target directory (defaults to current directory)
        no_plugin: force vendoring the handoff skill even when the plugin is
            installed (Phase 48). There is no flag that forces removal.
        report_user_skills: print the Phase 49 note about user-level
            `handoff*` skills that may conflict with the plugin. `upgrade`
            passes False per project and prints one aggregate note itself.
        preview: classify and report, write nothing (Phase 52).
        accept: managed paths whose custom content may be overwritten (or,
            for a legacy flat skill, deleted) — one path per entry.
        force: lift the git-recoverability refusal for accepted paths.

    Returns 0, or 1 when any path was refused.
    """
    from tagteam import framework

    source = get_data_dir()
    target = Path(target_dir).resolve()

    print("Tagteam Setup" + (" — preview (nothing will be written)" if preview else ""))
    print("==========================")
    print(f"Source: {source}")
    print(f"Target: {target}")
    print()

    # Verify source exists
    if not source.exists():
        print(f"Error: Data directory not found at {source}")
        print("The package may not be installed correctly.")
        return 1

    if not preview:
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

    plan = framework.build_plan(target, data_dir=source, no_plugin=no_plugin,
                                accept=accept, force=force)
    print(f"plugin: {plan.plugin}")
    if not preview:
        framework.apply(plan)
    print(framework.format_report(plan))
    if report_user_skills and not no_plugin:
        report_legacy_user_skills()

    if preview:
        refused = plan.refused
        if refused:
            print(f"{len(refused)} path(s) would be refused.")
        return 1 if refused else 0

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
                out.write(_POINTER)
        except FileExistsError:
            pass

    # Register this project for future upgrades
    from tagteam.registry import register_project
    register_project(str(target))

    refused = plan.refused
    print()
    if refused:
        print(f"Setup complete with {len(refused)} refused path(s) — see the report above.")
    else:
        print("Setup complete!")
    print()
    print("Next steps:")
    print("  Quick start:  tagteam quickstart")
    print("  Or manually:  tagteam init")
    print("                tagteam session start")
    print("  Windows/manual fallback:")
    print("                tagteam session start --backend manual")
    print("                tagteam watch --mode notify")
    return 1 if refused else 0


def parse_setup_args(argv: list[str]) -> tuple[str, dict]:
    """``[dir] [--no-plugin] [--preview] [--accept PATH]... [--force]`` →
    (target, kwargs for :func:`main`). Shared by ``tagteam setup`` and the
    module entry point."""
    target = "."
    opts: dict = {"no_plugin": False, "preview": False, "accept": [], "force": False}
    positional: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--no-plugin":
            opts["no_plugin"] = True
        elif a in ("--preview", "--dry-run"):
            opts["preview"] = True
        elif a == "--force":
            opts["force"] = True
        elif a == "--accept":
            if i + 1 >= len(argv):
                raise ValueError("--accept needs a path")
            opts["accept"].append(argv[i + 1])
            i += 1
        elif a.startswith("--accept="):
            opts["accept"].append(a[len("--accept="):])
        elif a.startswith("-"):
            raise ValueError(f"unknown option {a}")
        else:
            positional.append(a)
        i += 1
    if len(positional) > 1:
        raise ValueError("setup takes at most one directory")
    if positional:
        target = positional[0]
    opts["accept"] = tuple(opts["accept"])
    return target, opts


def cli():
    """Command-line entry point."""
    try:
        target, opts = parse_setup_args(sys.argv[1:])
    except ValueError as e:
        print(f"setup: {e}")
        return 2
    return main(target, **opts)


if __name__ == "__main__":
    sys.exit(cli())
