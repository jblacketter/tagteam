"""
Project registry for AI Handoff Framework.

Tracks which directories have been set up with ai-handoff,
so the 'upgrade' command can refresh them all at once.

Registry location: ~/.ai-handoff/projects.json
"""

import json
from pathlib import Path

REGISTRY_DIR = Path.home() / ".ai-handoff"
REGISTRY_FILE = REGISTRY_DIR / "projects.json"


def _read_registry() -> list[str]:
    """Read the list of registered project paths."""
    if not REGISTRY_FILE.exists():
        return []
    try:
        data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def _write_registry(paths: list[str]) -> None:
    """Write the list of registered project paths."""
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(
        json.dumps(paths, indent=2) + "\n", encoding="utf-8"
    )


def register_project(project_dir: str) -> None:
    """Add a project directory to the registry (idempotent)."""
    resolved = str(Path(project_dir).resolve())
    projects = _read_registry()
    if resolved not in projects:
        projects.append(resolved)
        _write_registry(projects)


def get_registered_projects() -> list[str]:
    """Return all registered project paths, filtering out ones that no longer exist."""
    projects = _read_registry()
    existing = [p for p in projects if Path(p).is_dir()]
    if len(existing) != len(projects):
        _write_registry(existing)
    return existing


def unregister_project(project_dir: str) -> None:
    """Remove a project directory from the registry."""
    resolved = str(Path(project_dir).resolve())
    projects = _read_registry()
    if resolved in projects:
        projects.remove(resolved)
        _write_registry(projects)
