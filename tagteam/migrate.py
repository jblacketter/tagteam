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
        args: Command-line arguments. Supports:
          --dry-run          preview without writing
          --to-sqlite        migrate runtime state to .tagteam/tagteam.db
          --force            (with --to-sqlite) overwrite existing DB content
          --db PATH          (with --to-sqlite) override default DB location
          --dir PATH         (with --to-sqlite) project dir, defaults to cwd

    Returns:
        Exit code (0 for success, 1 for error)
    """
    if "--to-sqlite" in args:
        return migrate_to_sqlite_command(args)

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


def migrate_to_sqlite_command(args: list[str]) -> int:
    """Migrate runtime state from JSON/JSONL files to a SQLite database.

    Reads docs/handoffs/* and handoff-state.json, populates
    .tagteam/tagteam.db. The source files are not modified — this is
    a one-way import. The database becomes canonical only when the
    rest of the production port (dual-write, then DB-only) lands.
    """
    dry_run = "--dry-run" in args
    force = "--force" in args
    project_dir = _arg_value(args, "--dir") or "."
    db_override = _arg_value(args, "--db")

    project_path = Path(project_dir).resolve()
    handoffs = project_path / "docs" / "handoffs"
    if not handoffs.is_dir():
        print(f"Error: {handoffs} not found.")
        print("This command migrates an existing tagteam project's runtime")
        print("state into a SQLite database. Run from a project root with")
        print("docs/handoffs/ already populated.")
        return 1

    from tagteam import db

    db_path = Path(db_override) if db_override else (
        project_path / db.DEFAULT_DB_RELPATH
    )

    print(f"Source:      {project_path}")
    print(f"Target DB:   {db_path}")
    print()

    # Refuse to clobber existing data unless --force.
    pre_existing = db_path.exists()
    if pre_existing:
        conn = db.connect(db_path=db_path)
        try:
            n_cycles = conn.execute("SELECT COUNT(*) FROM cycles").fetchone()[0]
            n_rounds = conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0]
        finally:
            conn.close()
        if (n_cycles or n_rounds) and not force:
            print(f"Error: target DB already contains data "
                  f"({n_cycles} cycles, {n_rounds} rounds).")
            print("Re-running would duplicate rows. Use --force to override,")
            print("or delete the DB file first.")
            return 1
        if (n_cycles or n_rounds) and force:
            # --force on a populated DB: delete and rebuild, since
            # re-importing on top would duplicate every row.
            db_path.unlink()
            print(f"--force: removed existing DB ({n_cycles} cycles, "
                  f"{n_rounds} rounds) and rebuilding.")

    if dry_run:
        # Use a throwaway in-memory DB so we can report counts without
        # touching the real one.
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.executescript(db._SCHEMA_V1)
        conn.execute("PRAGMA user_version = 1")
        report = db.import_from_files(project_path, conn)
        conn.close()
        print("Dry run — nothing written.")
        print(f"  Would import: {report['cycles']} cycles, "
              f"{report['rounds']} rounds, "
              f"{report['history_entries']} history entries.")
        return 0

    conn = db.connect(db_path=db_path)
    try:
        report = db.import_from_files(project_path, conn)
    finally:
        conn.close()

    print(f"Imported: {report['cycles']} cycles, "
          f"{report['rounds']} rounds, "
          f"{report['history_entries']} history entries.")
    print(f"Database:  {db_path}")
    print()
    print("Source files unchanged. The DB is not yet canonical — runtime")
    print("still reads from JSON/JSONL files until the dual-write phase lands.")
    return 0


def _arg_value(args: list[str], flag: str) -> str | None:
    """Return the value following `flag` in args, or None if not present.

    Supports both `--flag value` and `--flag=value` forms.
    """
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None
