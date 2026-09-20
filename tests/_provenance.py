"""Which shipped files are frozen provenance, and the tag each is pinned to.

One definition, two users: the tag-pin tests in test_framework.py iterate
exactly these sets, and the shipped-docs audit in test_plugin.py may exempt a
path only if it is in one of them — so an exempt file cannot drift.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "tagteam" / "data"
HISTORY = DATA / "history"
FROZEN_TAG = "v3.14.1"          # data/templates + data/checklists: retired-path sources, frozen since Phase 63


def frozen_paths() -> list[Path]:
    """data/templates/*.md and data/checklists/*.md — pinned to FROZEN_TAG."""
    return sorted((DATA / "templates").glob("*.md")) + sorted((DATA / "checklists").glob("*.md"))


def history_paths() -> list[Path]:
    """data/history/<tag>/… — each pinned to its own <tag> (Phase 62)."""
    return sorted(p for p in HISTORY.rglob("*") if p.is_file())


def pinned_relpaths() -> set[str]:
    return {p.relative_to(REPO).as_posix() for p in frozen_paths() + history_paths()}


def has_tag(tag: str) -> bool:
    r = subprocess.run(["git", "-C", str(REPO), "tag", "--list", tag], capture_output=True, text=True)
    return r.returncode == 0 and tag in r.stdout.split()


def tagged_blob(tag: str, source_rel: str) -> bytes | None:
    for pkg in ("tagteam", "ai_handoff"):             # the package was renamed along the way
        r = subprocess.run(["git", "-C", str(REPO), "show", f"{tag}:{pkg}/data/{source_rel}"], capture_output=True)
        if r.returncode == 0:
            return r.stdout
    return None


def tagged_listing(tag: str, subdir: str) -> list[str]:
    r = subprocess.run(["git", "-C", str(REPO), "ls-tree", "--name-only", f"{tag}:tagteam/data/{subdir}"],
                       capture_output=True, text=True)
    return sorted(n for n in r.stdout.split() if n.endswith(".md")) if r.returncode == 0 else []
