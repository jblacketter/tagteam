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

    # Phase 61: docs/workflows.md is the one framework file every project
    # gets; templates/ and docs/checklists/ are retired and say nothing.
    return not (target / "docs" / "workflows.md").is_file()


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


def report_legacy_workflow(target: Path) -> bool:
    """Phase 53: one pointer line when the project carries recognized legacy
    workflow artifacts. Report-only; never changes setup's exit code."""
    from tagteam.diagnostics import legacy_findings, summary_line
    try:
        line = summary_line(legacy_findings(target), target)
    except Exception as exc:  # a report must never break setup
        line = f"note: legacy workflow scan failed ({exc.__class__.__name__})"
    if line:
        print(line)
    return bool(line)


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

    # Every write — framework directories, managed files, the once-only seeds
    # (roadmap, decision log, AGENTS.md / CLAUDE.md pointers) and the manifest —
    # goes through the engine, which checks each path's lexical parents and
    # its preimage before touching disk. Nothing here writes directly.
    plan = framework.build_plan(target, data_dir=source, no_plugin=no_plugin,
                                accept=accept, force=force)
    print(f"plugin: {plan.plugin}")
    if not preview:
        framework.apply(plan)
    print(framework.format_report(plan))
    report_legacy_workflow(target)
    if report_user_skills and not no_plugin:
        report_legacy_user_skills()

    if preview:
        refused = plan.refused
        if refused:
            print(f"{len(refused)} path(s) would be refused.")
        return 1 if refused else 0

    # Register this project for future upgrades — unless there is no project
    # directory to come back to (the target itself was refused).
    if not plan.root_outcome.startswith("refused"):
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
