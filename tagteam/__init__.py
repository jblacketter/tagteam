"""
Tagteam

A collaboration framework for structured AI-to-AI handoffs with human oversight.
Configure your lead and reviewer agents via tagteam.yaml.
"""

import re as _re
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path as _Path

# Phase 64: an editable install's metadata is written once, at install time,
# while the code it points at moves with every commit — so a source tree
# answers for itself. This is a deliberately narrow reader of the one
# release-controlled `[project]` table (scripts/release.py edits the same
# line), not a TOML parser: anything missing, duplicated, commented out or
# not a plain "double-quoted" string means "not sure", and not sure means the
# installed metadata, exactly as before. No wheel install has a
# pyproject.toml beside the package; the name check guards the odd layout
# where some other project's file sits there.
# The header's comment is bounded to its own line (`[^\n]*`): under DOTALL a
# bare `.*` would run to the end of the file and leave an empty table.
_PROJECT_TABLE_RE = _re.compile(r"^\[project\][ \t]*(?:#[^\n]*)?$(.*?)(?=^[ \t]*\[|\Z)", _re.M | _re.S)


def _one_string(table: str, key: str) -> "str | None":
    # The value is read only from the shape release.py writes: the bare key at
    # column zero. Ambiguity is counted more widely — an indented or quoted
    # spelling of the same key is still a second assignment, and any second
    # assignment means "not sure".
    found = _re.findall(rf'^{key}[ \t]*=[ \t]*"([^"\\\n]*)"[ \t]*(?:#[^\n]*)?$', table, _re.M)
    any_assignment = _re.findall(rf"""^[ \t]*["']?{key}["']?[ \t]*=""", table, _re.M)
    return found[0] if len(found) == 1 and len(any_assignment) == 1 and found[0] else None


def _source_tree_version(package_dir: "_Path | None" = None) -> "str | None":
    """The version declared by the `pyproject.toml` beside the package, when
    that file is tagteam's own. None on any doubt or failure — never raises."""
    try:
        base = _Path(package_dir) if package_dir is not None else _Path(__file__).resolve().parent
        text = (base.parent / "pyproject.toml").read_text(encoding="utf-8")
        tables = _PROJECT_TABLE_RE.findall(text)
        if len(tables) != 1 or _one_string(tables[0], "name") != "tagteam":
            return None
        return _one_string(tables[0], "version")
    except Exception:       # OSError, UnicodeError, anything odd about the path
        return None


def _resolve_version(package_dir: "_Path | None" = None) -> str:
    tree = _source_tree_version(package_dir)
    if tree is not None:
        return tree
    try:
        return _pkg_version("tagteam")
    except PackageNotFoundError:
        return "0.0.0+unknown"


__version__ = _resolve_version()
