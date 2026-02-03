# Phase: Migration & Advanced Features

## Status
- [x] Planning
- [x] In Review
- [x] Approved
- [x] Implementation
- [x] Implementation Review
- [x] Complete

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary
**What:** Add migration tooling for legacy projects, centralized config parsing with validation, and forward-compatible schema extensions — without refactoring to N-agent support (deferred to future phase).
**Why:** Projects set up before Phase 1 lack `ai-handoff.yaml` and can't benefit from template variable substitution. Config parsing is duplicated across 4 modules. This phase consolidates config handling and provides an upgrade path for existing projects.
**Depends on:** Phase 5 (Template Variable Substitution) — Complete

## Scope

### In Scope
1. **`python -m ai_handoff migrate` command**
   - Detect projects using old manual setup (no ai-handoff.yaml)
   - Auto-detect agent names from existing handoff docs (scan for "From: X (Lead)" patterns)
   - Generate config with detected names or defaults (Claude=lead, Codex=reviewer)
   - `--dry-run` flag to preview changes without writing
   - Backup templates to `ai-handoff-backups/YYYY-MM-DD_HHMMSS/` before re-running setup
   - Optionally re-run setup to apply template variable substitution

2. **Config validation and centralized parsing**
   - New `ai_handoff/config.py` module as single source for config operations
   - `read_config(path)` — centralized config reading (PyYAML with fallback)
   - `validate_config(config)` — returns list of error messages
   - Require exactly one lead and one reviewer (current 2-agent model)
   - Validate agent names are non-empty strings
   - Update `cli.py`, `setup.py`, `watcher.py`, `server.py` to use centralized config module

3. **Config schema extension (forward-compatible)**
   - Accept optional `model_patterns` field in agent config (for future use)
   - Validate patterns are list of non-empty strings if present
   - Error on pattern overlap (not warning) to prevent ambiguous future identification

### Out of Scope (Deferred to Future Phase)
- Support for 3+ agents — requires state machine refactoring
- Multiple leads or multiple reviewers — requires routing logic
- Agent specialties — requires handoff routing
- `id` field for filesystem-safe slugs — not needed until multi-agent
- Skills directory relocation (`.ai-handoff/skills/`) — not needed until multi-agent
- Arbiter configuration in YAML — keep as implicit Human for now
- Windows `sync-skills` command — low priority
- Agent self-identification at runtime (`AI_HANDOFF_AGENT`, `AI_MODEL_ID`) — no clear integration point in current 2-agent architecture; watcher uses pane position, skills are markdown instructions

## Technical Approach

### Migration Command

```python
# ai_handoff/migrate.py

def detect_agent_names(project_dir: Path) -> tuple[str, str]:
    """Scan handoff docs for agent names. Returns (lead, reviewer) or defaults.

    Handles names with spaces/punctuation by matching everything before " (Lead)" or " (Reviewer)".
    Falls back to defaults (Claude, Codex) if no matches found.
    """
    lead_name = "Claude"  # default
    reviewer_name = "Codex"  # default

    handoffs_dir = project_dir / "docs" / "handoffs"
    if handoffs_dir.exists():
        for md_file in handoffs_dir.glob("*.md"):
            content = md_file.read_text()
            # Match "**From:** <name> (Lead)" — name can include spaces/punctuation
            if match := re.search(r"\*\*From:\*\*\s+(.+?)\s+\(Lead\)", content):
                lead_name = match.group(1).strip()
            # Match "**To:** <name> (Reviewer)"
            if match := re.search(r"\*\*To:\*\*\s+(.+?)\s+\(Reviewer\)", content):
                reviewer_name = match.group(1).strip()
            if lead_name != "Claude" and reviewer_name != "Codex":
                break  # Found both

    return lead_name, reviewer_name


def migrate_command(args: list[str]) -> int:
    """Migrate a project to use ai-handoff configuration."""
    dry_run = "--dry-run" in args
    project_dir = Path(".")

    # 1. Check if ai-handoff.yaml exists
    config_path = project_dir / "ai-handoff.yaml"
    if config_path.exists():
        print("ai-handoff.yaml already exists. Nothing to migrate.")
        return 0

    # 2. Detect existing setup
    has_skills = (project_dir / ".claude/skills").exists()
    has_templates = (project_dir / "templates").exists()
    has_docs = (project_dir / "docs").exists()

    if not has_skills and not has_templates and not has_docs:
        print("No existing setup detected. Run 'python -m ai_handoff setup' first.")
        return 1

    # 3. Detect agent names from existing docs
    lead_name, reviewer_name = detect_agent_names(project_dir)

    # 4. Preview or execute
    if dry_run:
        print("Migration preview (--dry-run):")
        print(f"  Would create ai-handoff.yaml with:")
        print(f"    Lead: {lead_name}")
        print(f"    Reviewer: {reviewer_name}")
        if has_templates:
            print(f"  Would backup templates/ to ai-handoff-backups/<timestamp>/")
        print("  Run without --dry-run to execute.")
        return 0

    # 5. Create backup if templates exist
    if has_templates:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        backup_dir = project_dir / "ai-handoff-backups" / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(project_dir / "templates", backup_dir / "templates")
        print(f"Backed up templates/ to {backup_dir}/")

    # 6. Write config
    from ai_handoff.cli import write_config
    write_config(str(project_dir), lead_name, reviewer_name)
    print(f"Created ai-handoff.yaml (Lead: {lead_name}, Reviewer: {reviewer_name})")

    # 7. Prompt to re-run setup
    print("\nTo update templates with agent names, run:")
    print("  python -m ai_handoff setup")
    return 0
```

### Centralized Config Module

New `ai_handoff/config.py`:

```python
"""Centralized configuration handling for AI Handoff Framework."""

from pathlib import Path

# PyYAML is optional
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def read_config(config_path: Path | str) -> dict | None:
    """Read and parse ai-handoff.yaml.

    Args:
        config_path: Path to config file

    Returns:
        Parsed config dict, or None if file doesn't exist or is invalid
    """
    path = Path(config_path)
    if not path.exists():
        return None

    try:
        content = path.read_text()
        if HAS_YAML:
            return yaml.safe_load(content)

        # Fallback parsing without PyYAML
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


def validate_config(config: dict) -> list[str]:
    """Validate ai-handoff.yaml structure.

    Args:
        config: Parsed config dict

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    if not isinstance(config, dict):
        return ["Config must be a YAML mapping"]

    agents = config.get("agents")
    if not isinstance(agents, dict):
        errors.append("Missing 'agents' section")
        return errors

    # Validate lead
    lead = agents.get("lead")
    if not isinstance(lead, dict) or not lead.get("name"):
        errors.append("Missing or invalid 'agents.lead.name'")

    # Validate reviewer
    reviewer = agents.get("reviewer")
    if not isinstance(reviewer, dict) or not reviewer.get("name"):
        errors.append("Missing or invalid 'agents.reviewer.name'")

    # Validate model_patterns if present
    all_patterns = []
    for role in ["lead", "reviewer"]:
        agent = agents.get(role, {})
        patterns = agent.get("model_patterns")
        if patterns is not None:
            if not isinstance(patterns, list):
                errors.append(f"'agents.{role}.model_patterns' must be a list")
            elif not all(isinstance(p, str) and p for p in patterns):
                errors.append(f"'agents.{role}.model_patterns' must contain non-empty strings")
            else:
                all_patterns.append((role, [p.lower() for p in patterns]))

    # Check for pattern overlap (error, not warning)
    if len(all_patterns) == 2:
        role1, patterns1 = all_patterns[0]
        role2, patterns2 = all_patterns[1]
        for p1 in patterns1:
            for p2 in patterns2:
                if p1 in p2 or p2 in p1:
                    errors.append(
                        f"Pattern overlap: '{p1}' ({role1}) and '{p2}' ({role2}) "
                        f"could match the same model identifier"
                    )

    return errors


def get_agent_names(config: dict) -> tuple[str | None, str | None]:
    """Extract lead and reviewer names from config.

    Args:
        config: Parsed config dict

    Returns:
        (lead_name, reviewer_name) tuple, with None for missing values
    """
    agents = config.get("agents", {})
    lead = agents.get("lead", {})
    reviewer = agents.get("reviewer", {})
    return lead.get("name"), reviewer.get("name")
```

### Config Schema (Extended)

```yaml
# ai-handoff.yaml
agents:
  lead:
    name: Claude
    model_patterns:      # Optional: reserved for future agent self-identification
      - "claude"
      - "anthropic"
  reviewer:
    name: Codex
    model_patterns:      # Optional: reserved for future agent self-identification
      - "codex"
```

### Integration Updates

Update existing modules to use centralized config:

**cli.py**: Replace `read_existing_config()` with `from ai_handoff.config import read_config`

**setup.py**: Replace `read_config()` with import from config module, add validation call

**watcher.py**: Replace `read_config()` with import from config module

**server.py**: Replace `_read_config()` with import from config module

## Files to Create/Modify

### Create
- `ai_handoff/config.py` — Centralized config reading, validation, helper functions
- `ai_handoff/migrate.py` — Migration command implementation
- `tests/test_config.py` — Unit tests for config validation and pattern overlap detection
- `tests/test_migrate.py` — Unit tests for migration detection and agent name extraction

### Modify
- `ai_handoff/cli.py` — Add `migrate` subcommand, use `config.read_config()`
- `ai_handoff/setup.py` — Use `config.read_config()`, call `validate_config()` on load
- `ai_handoff/watcher.py` — Use `config.read_config()`
- `ai_handoff/server.py` — Use `config.read_config()`

## Success Criteria
- [x] `python -m ai_handoff migrate` detects projects without config and creates one
- [x] `python -m ai_handoff migrate --dry-run` previews changes without writing
- [x] `migrate` auto-detects agent names from existing handoff docs ("From: X (Lead)" patterns)
- [x] `migrate` backs up templates/ to `ai-handoff-backups/<timestamp>/` before changes
- [x] Config validation catches missing/invalid lead and reviewer names
- [x] `model_patterns` field accepted in config (forward-compatible for future use)
- [x] Pattern overlap between agents produces a validation error (not warning)
- [x] All modules use centralized `config.read_config()` (no duplicate parsing)
- [x] Existing functionality (init, setup, watch, state, serve) continues to work
- [x] Unit tests pass for config validation, pattern overlap, and migration detection

## Open Questions
- None (resolved: migrate will auto-detect names, pattern overlap is an error)

## Risks
- **Migration backup size:** Large projects may have many template files. Mitigation: only backup `templates/` directory, not all docs.
- **Pattern overlap edge cases:** Substring matching means "code" overlaps with "codex". Mitigation: error message explains the overlap clearly.
