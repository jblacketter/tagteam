"""
Auto-export of cycle markdown for Phase 28 Step B.

After Step B activates, every cycle write triggers a re-render of
that cycle's markdown to `docs/handoffs/<phase>_<type>.md`. The
.md files replace the legacy `_rounds.jsonl` + `_status.json` pair
as the git-committable conversation log; the byte-identical output
is the load-bearing parity contract.

This module exposes the pure "render and write" function. The
hook that calls it from cycle writers lives in
`tagteam.cycle._shadow_db_after_cycle_write` and friends, gated by
the Step B activation flag (added in a subsequent commit).

The function is intentionally simple: render via `db.render_cycle`
(already proven byte-identical against the corpus), atomic-write
to disk, return success. No locking inside this module — the
caller is expected to hold the project writer lock already.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def render_cycle_to_file(
    conn: sqlite3.Connection,
    project_dir: str | Path,
    phase: str,
    cycle_type: str,
) -> bool:
    """Render a cycle from the DB and write it to
    `docs/handoffs/<phase>_<type>.md` under `project_dir`.

    Returns True on success, False if the cycle doesn't exist in
    the DB (no render available) or the write failed for any
    reason. Never raises — auto-export is best-effort post-write
    plumbing and must not propagate failures back to the caller's
    write path.

    Atomic write: write to a sibling `.tmp` then rename, so a
    crash mid-write can't leave a half-written .md file in the
    git-tracked output.
    """
    from tagteam import db as db_mod

    project_dir = Path(project_dir)
    md = db_mod.render_cycle(conn, phase, cycle_type)
    if md is None:
        return False

    handoffs = project_dir / "docs" / "handoffs"
    try:
        handoffs.mkdir(parents=True, exist_ok=True)
        out_path = handoffs / f"{phase}_{cycle_type}.md"
        tmp_path = handoffs / f".{phase}_{cycle_type}.md.tmp"
        # `cycle.render_cycle` produces output without a trailing
        # newline; normalize to a single trailing newline so the
        # committed .md follows the usual POSIX text-file shape.
        if not md.endswith("\n"):
            md = md + "\n"
        tmp_path.write_text(md, encoding="utf-8")
        tmp_path.replace(out_path)
        return True
    except OSError:
        return False
