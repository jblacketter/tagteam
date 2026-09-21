"""Phase 65: other tagteam installs a project carries in its own venv.

A tagteam installed in ``<project>/.venv`` (or ``venv``) runs instead of the
one on PATH for anything launched through that venv — ``.venv/bin/tagteam``,
``python -m tagteam``, a watcher started from it — and nothing used to say
so. This module only *looks*: lstat and bounded reads, no subprocess, no
import of the other copy, and nothing is read through a symlink at any level.

Deliberately free of other tagteam imports (only ``tagteam.__version__``) so
both ``diagnostics`` and ``framework`` can use it without a cycle.
"""
from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

VENV_NAMES = (".venv", "venv")
METADATA_LIMIT = 64 * 1024
_DIST_INFO_RE = re.compile(r"^tagteam-.+\.dist-info$", re.IGNORECASE)
_VERSION_RE = re.compile(r"^Version:[ \t]*(\S+)[ \t]*$", re.MULTILINE)


def _kind(path: Path) -> str:
    """``absent`` | ``symlink`` | ``dir`` | ``file`` | ``other`` — never follows."""
    try:
        st = os.lstat(path)
    except OSError:
        return "absent"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if stat.S_ISDIR(st.st_mode):
        return "dir"
    return "file" if stat.S_ISREG(st.st_mode) else "other"


def _entries(path: Path) -> list[str]:
    try:
        return sorted(e.name for e in os.scandir(path))
    except OSError:
        return []


def _metadata_version(dist_info: Path) -> str | None:
    """The one ``Version:`` header, or None when the file is missing, not a
    plain file, unreadable, or carries none / more than one."""
    meta = dist_info / "METADATA"
    if _kind(meta) != "file":
        return None
    try:
        with open(meta, "rb") as f:
            text = f.read(METADATA_LIMIT).decode("utf-8", errors="replace")
    except OSError:
        return None
    headers = text.replace("\r\n", "\n").split("\n\n", 1)[0]
    found = _VERSION_RE.findall(headers)
    return found[0] if len(found) == 1 else None


def _site_packages(venv: Path) -> list[Path]:
    """Plain-directory ``site-packages`` under a venv: ``lib/python*/`` and
    Windows' ``Lib/``. Each level is checked before it is listed."""
    out: list[Path] = []
    for name in _entries(venv):
        if name.lower() != "lib" or _kind(venv / name) != "dir":
            continue
        lib = venv / name
        if _kind(lib / "site-packages") == "dir":
            out.append(lib / "site-packages")
        for sub in _entries(lib):
            if sub.lower().startswith("python") and _kind(lib / sub) == "dir" \
                    and _kind(lib / sub / "site-packages") == "dir":
                out.append(lib / sub / "site-packages")
    return out


def observe_installs(root: str | Path, *, running: str | None = None,
                     prefix: str | Path | None = None) -> list[dict]:
    """One row per tagteam dist-info found in the project's own venvs.

    ``state``: ``differs`` (a version other than the running one — newer or
    older, this does not judge), ``same``, ``editable`` (a link to a source
    tree: its label says nothing since Phase 64), ``unknown`` (no single
    readable ``Version:``) or ``not scanned`` (a symlink in the way).
    The venv this process runs from is not a shadow and is skipped.
    """
    from tagteam import __version__
    root = Path(root)
    running = running or __version__
    try:
        own = Path(prefix if prefix is not None else sys.prefix).resolve()
    except OSError:
        own = None
    rows: list[dict] = []
    for name in VENV_NAMES:
        venv = root / name
        kind = _kind(venv)
        if kind == "symlink":
            rows.append({"venv": name, "path": name, "version": None, "editable": False,
                         "state": "not scanned", "detail": f"{name} is a symlink"})
            continue
        if kind != "dir":
            continue
        try:
            if own is not None and venv.resolve() == own:
                continue
        except OSError:
            pass
        for sp in _site_packages(venv):
            names = _entries(sp)
            editable = any(n.lower().startswith("__editable__") and "tagteam" in n.lower() for n in names)
            for n in names:
                if not _DIST_INFO_RE.match(n):
                    continue
                rel = (sp / n).relative_to(root).as_posix()
                k = _kind(sp / n)
                if k == "symlink":
                    rows.append({"venv": name, "path": rel, "version": None, "editable": False,
                                 "state": "not scanned", "detail": f"{rel} is a symlink"})
                    continue
                if k != "dir":
                    continue
                version = _metadata_version(sp / n)
                if editable:
                    state = "editable"
                elif version is None:
                    state = "unknown"
                else:
                    state = "same" if version == running else "differs"
                rows.append({"venv": name, "path": rel, "version": version, "editable": editable,
                             "state": state, "detail": ""})
    return rows


def describe(row: dict, running: str) -> str:
    """One report line for a row (without the severity column)."""
    v = row["version"] or "version unknown"
    if row["state"] == "not scanned":
        return f"{row['path']} — not scanned: {row['detail']}"
    if row["state"] == "editable":
        return f"{row['path']} — editable link to a source tree (runs whatever that tree is)"
    if row["state"] == "unknown":
        return f"{row['path']} — version unknown (no single readable Version: header)"
    if row["state"] == "same":
        return f"{row['path']} — tagteam {v}, same as the running one"
    return (f"{row['path']} — tagteam {v}; running {running}. {row['venv']}/bin/tagteam and "
            f"`python -m tagteam` from that venv run different code; upgrade or remove it")


def mismatch_clause(root: str | Path) -> str:
    """`` · .venv: tagteam X (differs from the running Y)`` or ``""``."""
    from tagteam import __version__
    for row in observe_installs(root):
        if row["state"] == "differs":
            return f" · {row['venv']}: tagteam {row['version']} (differs from the running {__version__})"
    return ""
