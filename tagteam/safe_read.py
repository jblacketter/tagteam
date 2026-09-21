"""Guarded, bounded file reads for look-only diagnostics.

Moved out of ``diagnostics`` in Phase 65 so ``installs`` can share the same
safeguards without importing ``diagnostics`` (which imports ``framework``):
every path component is lstat-checked, the leaf is opened ``O_NOFOLLOW |
O_NONBLOCK`` and re-checked with ``fstat`` — so a file swapped for a symlink
or a FIFO between the check and the open is refused, not followed or blocked
on — and the read is capped. No other tagteam imports.
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Read:
    state: str                  # absent | file | unsupported | skipped | unreadable
    detail: str = ""
    data: bytes | None = None
    size: int | None = None
    truncated: bool = False


def _lstat_chain(root: Path, rel: str) -> Read:
    parts = Path(rel).parts
    cur = root
    for i, part in enumerate(parts):
        cur = cur / part
        shown = cur.relative_to(root).as_posix()
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            return Read("absent")
        except OSError as e:
            return Read("unreadable", f"{shown} unreadable ({e.__class__.__name__})")
        if stat.S_ISLNK(st.st_mode):
            return Read("unsupported", f"{shown} is a symlink")
        if i < len(parts) - 1:
            if not stat.S_ISDIR(st.st_mode):
                return Read("unsupported", f"{shown} is not a directory")
        elif stat.S_ISDIR(st.st_mode):
            return Read("unsupported", f"{shown} is a directory")
        elif not stat.S_ISREG(st.st_mode):
            return Read("unsupported", f"{shown} is not a regular file")
        else:
            return Read("file", size=st.st_size)
    return Read("absent")


def _dir_chain(root: Path, rel: str) -> tuple[str, str]:
    """(``dir`` | ``absent`` | ``unsupported``, detail) — every component a
    plain directory, never a symlink."""
    cur = root
    for part in Path(rel).parts:
        cur = cur / part
        shown = cur.relative_to(root).as_posix()
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            return "absent", ""
        except OSError as e:
            return "unsupported", f"{shown} unreadable ({e.__class__.__name__})"
        if stat.S_ISLNK(st.st_mode):
            return "unsupported", f"{shown} is a symlink"
        if not stat.S_ISDIR(st.st_mode):
            return "unsupported", f"{shown} is not a directory"
    return "dir", ""


def read_bounded(root: Path, rel: str, limit: int, *, truncate: bool) -> Read:
    """Read ``rel`` under ``root`` only if every component is a plain
    directory and the leaf a regular file. Over ``limit``: the first
    ``limit`` bytes when ``truncate``, else ``skipped`` and nothing read."""
    r = _lstat_chain(root, rel)
    if r.state != "file":
        return r
    if r.size is not None and r.size > limit and not truncate:
        return Read("skipped", f"over size limit ({limit // 1024} KB)", size=r.size)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(root / rel, flags)
    except OSError as e:
        return Read("unreadable", f"{rel} unreadable ({e.__class__.__name__})")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return Read("unsupported", f"{rel} is not a regular file")
        chunks, left = [], limit + 1
        while left > 0:
            b = os.read(fd, min(65536, left))
            if not b:
                break
            chunks.append(b)
            left -= len(b)
        data = b"".join(chunks)
    except OSError as e:
        return Read("unreadable", f"{rel} unreadable ({e.__class__.__name__})")
    finally:
        os.close(fd)
    if len(data) > limit:
        if not truncate:
            return Read("skipped", f"over size limit ({limit // 1024} KB)", size=st.st_size)
        return Read("file", data=data[:limit], size=st.st_size, truncated=True)
    return Read("file", data=data, size=st.st_size)
