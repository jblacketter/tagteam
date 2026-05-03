"""
Migration command for Tagteam.

Migrates projects that were set up before Phase 1 (without tagteam.yaml)
to use the new configuration-based workflow.
"""

import re
import shutil
import tempfile
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
    if "--to-step-b" in args:
        return migrate_to_step_b_command(args)
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
            _remove_sqlite_db_files(db_path)
            print(f"--force: removed existing DB ({n_cycles} cycles, "
                  f"{n_rounds} rounds) and rebuilding.")

    if dry_run:
        # Use a throwaway in-memory DB so we can report counts without
        # touching the real one.
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.executescript(db._SCHEMA_V1)
        conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION}")
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


_STEP_B_CYCLE_FILE_RE = re.compile(
    r"^(.+)_(plan|impl)_(status\.json|rounds\.jsonl)$"
)
STEP_B_READERS_READY = True


def _step_b_readers_ready() -> bool:
    """True only after Stage 2 DB-backed cycle readers have landed.

    Moving legacy cycle files before readers switch away from
    `_rounds.jsonl` / `_status.json` breaks historical CLI/TUI/web
    reads. Stage 2 (DB-backed runtime readers with legacy file
    fallback in `tagteam/cycle.py`) flipped this to True.
    """
    return STEP_B_READERS_READY


def migrate_to_step_b_command(args: list[str]) -> int:
    """Activate Phase 28 Step B cycle markdown auto-export.

    Rebuilds the DB from legacy cycle JSON/JSONL files, renders
    `<phase>_<type>.md` for every cycle, then moves legacy cycle files
    to `.tagteam/legacy/`. The file move is resumable: reruns rebuild
    from both active `docs/handoffs/` files and already-moved legacy
    files.
    """
    project_dir = _arg_value(args, "--dir") or "."
    project_path = Path(project_dir).resolve()
    handoffs = project_path / "docs" / "handoffs"
    legacy_dir = project_path / ".tagteam" / "legacy"

    if not _step_b_readers_ready():
        print(
            "Error: migrate --to-step-b requires Stage 2 DB-backed cycle "
            "readers. Refusing to move _rounds.jsonl/_status.json files "
            "while runtime reads still depend on them."
        )
        return 1

    if not handoffs.is_dir() and not legacy_dir.is_dir():
        print(f"Error: no docs/handoffs or .tagteam/legacy in {project_path}.")
        return 1

    from tagteam import auto_export, db, dualwrite

    with dualwrite.writer_lock(project_path):
        rebuild = _rebuild_step_b_db_from_sources(project_path)
        if not rebuild["success"]:
            print(f"Error: {rebuild['reason']}")
            return 1

        conn = rebuild["conn"]
        failures: list[tuple[str, str, str]] = []
        rendered = 0
        moved = 0
        try:
            handoffs.mkdir(parents=True, exist_ok=True)
            legacy_dir.mkdir(parents=True, exist_ok=True)

            for cycle in db.list_cycles(conn):
                phase = cycle["phase"]
                cycle_type = cycle["type"]
                expected = db.render_cycle(conn, phase, cycle_type)
                md_path = handoffs / f"{phase}_{cycle_type}.md"
                needs_render = True
                if expected is not None and md_path.exists():
                    try:
                        needs_render = md_path.read_text(
                            encoding="utf-8"
                        ) != expected
                    except OSError:
                        needs_render = True

                if needs_render:
                    ok = auto_export.render_cycle_to_file(
                        conn, project_path, phase, cycle_type
                    )
                    if not ok:
                        failures.append((phase, cycle_type, "render_failed"))
                        continue
                    rendered += 1
                    try:
                        if md_path.read_text(encoding="utf-8") != expected:
                            failures.append(
                                (phase, cycle_type, "render_mismatch")
                            )
                            continue
                    except OSError as e:
                        failures.append(
                            (phase, cycle_type, f"render_read_failed: {e}")
                        )
                        continue

                for suffix in ("rounds.jsonl", "status.json"):
                    src = handoffs / f"{phase}_{cycle_type}_{suffix}"
                    if not src.exists():
                        continue
                    dst = legacy_dir / src.name
                    try:
                        if dst.exists():
                            dst.unlink()
                        shutil.move(str(src), str(dst))
                        moved += 1
                    except OSError as e:
                        failures.append(
                            (phase, cycle_type, f"move_failed: {e}")
                        )

            if failures:
                print(f"migrate --to-step-b completed with "
                      f"{len(failures)} failure(s):")
                for phase, cycle_type, reason in failures:
                    print(f"  {phase}_{cycle_type}: {reason}")
                return 1

            print(
                "Step B migration complete: "
                f"{len(db.list_cycles(conn))} cycles, "
                f"{rendered} markdown render(s), {moved} legacy file move(s)."
            )
            return 0
        finally:
            if conn is not None:
                conn.close()


def _step_b_source_files(project_path: Path) -> dict[str, Path]:
    """Return legacy cycle source files for Step B rebuild.

    `.tagteam/legacy` is read first and active `docs/handoffs` files
    override it. That makes a rerun after operator edits prefer the
    still-active pre-activation source.
    """
    out: dict[str, Path] = {}
    for directory in (
        project_path / ".tagteam" / "legacy",
        project_path / "docs" / "handoffs",
    ):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.is_file() and _STEP_B_CYCLE_FILE_RE.match(path.name):
                out[path.name] = path
    return out


def _rebuild_step_b_db_from_sources(project_path: Path) -> dict:
    """Rebuild DB from active + already-moved Step B legacy files."""
    from tagteam import db
    from tagteam import repair

    source_files = _step_b_source_files(project_path)
    if not source_files:
        return {
            "success": False,
            "reason": "no legacy cycle files found",
            "conn": None,
        }

    conn = None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_project = Path(tmp)
            tmp_handoffs = tmp_project / "docs" / "handoffs"
            tmp_handoffs.mkdir(parents=True)
            for name, src in source_files.items():
                shutil.copy2(src, tmp_handoffs / name)

            state_path = project_path / "handoff-state.json"
            if state_path.exists():
                shutil.copy2(state_path, tmp_project / "handoff-state.json")

            bad_file = repair._check_all_files(tmp_project)
            if bad_file is not None:
                return {
                    "success": False,
                    "reason": (
                        f"file_inconsistent: {bad_file['kind']} on "
                        f"{bad_file.get('phase','?')}_"
                        f"{bad_file.get('type','?')}: "
                        f"{bad_file.get('check') or bad_file.get('detail','')}"
                    ),
                    "conn": None,
                }

            db_path = project_path / db.DEFAULT_DB_RELPATH
            _remove_sqlite_db_files(db_path)
            conn = db.connect(db_path=db_path)
            db.import_from_files(tmp_project, conn)

            bad = repair._run_parity_unchecked(conn, tmp_project)
            if bad is not None:
                reason = (
                    f"parity check failed after rebuild: {bad['kind']} "
                    f"on {bad['phase']}_{bad['type']}"
                )
                conn.close()
                return {"success": False, "reason": reason, "conn": None}

            return {"success": True, "reason": None, "conn": conn}
    except Exception as e:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        return {
            "success": False,
            "reason": f"step-b rebuild raised: {type(e).__name__}: {e}",
            "conn": None,
        }


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


def _remove_sqlite_db_files(db_path: Path) -> None:
    """Remove a SQLite DB and its WAL sidecars if present."""
    for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        path.unlink(missing_ok=True)
