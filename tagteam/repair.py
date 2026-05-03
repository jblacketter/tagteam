"""
Repair state machine for the Phase 28 `db_invalid` sentinel.

When the shadow DB is marked invalid (a dual-write hit a DB-side
error), repair re-builds the DB from the canonical files and clears
the sentinel only after a parity check passes. Backoff prevents retry
storms; a 24-hour threshold escalates to a louder operator signal.

This module is the state machine. Triggers (CLI, retry-on-next-write
hook, watcher periodic poll) live elsewhere and call into here.

Sentinel payload schema (additions over the foundation `db_invalid`
flag file):

    {
      "since": "<iso ts>",                  # set by mark_db_invalid
      "reason": "<str>",                    # set by mark_db_invalid
      "updated_at": "<iso ts>",             # most recent payload write
      "consecutive_failures": <int>,        # 0 until first failed repair
      "last_attempt_at": "<iso ts>",        # set by _record_failure
      "next_attempt_at": "<iso ts>",        # backoff target; readable
                                            # by should_attempt_repair
      "last_failure_reason": "<str>",       # debugging hint
    }

Repair authority: the flag file remains the only source of truth.
Repair updates the flag file payload but does not write to the DB
to record its own state — by definition the DB might be unusable.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# Exponential backoff schedule: 1m, 2m, 4m, 8m, 16m, 32m, capped at
# 1 hour. consecutive_failures = 0 means "first attempt", delay = 1m.
_BACKOFF_BASE_SECONDS = 60
_BACKOFF_CAP_SECONDS = 3600

# After this long without successful repair, the operator-facing
# signal escalates (Saloon banner, watcher WARN log line). Caller of
# `needs_louder_signal` decides where the louder signal goes.
LOUDER_SIGNAL_AFTER_SECONDS = 24 * 3600


# ---------- Public API ----------

def should_attempt_repair(
    project_dir: str | Path,
    *,
    now: datetime | None = None,
) -> bool:
    """True if the `db_invalid` sentinel is set AND the backoff
    window has elapsed, so the caller is allowed to attempt repair.

    Returns False if the sentinel is not set (nothing to repair) or
    if `next_attempt_at` is in the future (still in backoff).
    """
    from tagteam import dualwrite

    if not dualwrite.is_db_invalid(project_dir):
        return False

    info = dualwrite.get_db_invalid_info(project_dir) or {}
    next_attempt = info.get("next_attempt_at")
    if not next_attempt:
        # No prior failed attempt — first try is allowed immediately.
        return True

    if now is None:
        now = datetime.now(timezone.utc)
    try:
        next_attempt_dt = datetime.fromisoformat(next_attempt)
    except ValueError:
        # Unparseable backoff state — be permissive and let the next
        # attempt either succeed or rewrite the field with a valid
        # value.
        return True

    return now >= next_attempt_dt


def attempt_repair(
    project_dir: str | Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one repair attempt. Returns a dict with at least:

      - `success`: bool
      - `reason`: str | None
      - `next_attempt_at`: str | None  (only on failure)

    Holds the project writer lock for the entire repair so it cannot
    interleave with regular dual-writes. Reentrant: callers that
    already hold the lock (e.g. retry-on-next-write inside a
    dual-write critical section) re-acquire harmlessly.

    Repair semantics per the design:
      1. Acquire writer lock.
      2. Re-build the shadow DB from scratch from canonical files
         (delete .tagteam/tagteam.db + sidecars, then re-import).
      3. Run a render parity check on every cycle.
      4. Clear the sentinel only if both import and parity pass.
         Otherwise, record the failure with bounded exponential
         backoff and leave the sentinel set.
    """
    from tagteam import auto_export, dualwrite

    if now is None:
        now = datetime.now(timezone.utc)

    project_path = Path(project_dir)

    with dualwrite.writer_lock(project_path):
        if not dualwrite.is_db_invalid(project_path):
            # Nothing to do — sentinel may have been cleared between
            # `should_attempt_repair` and the lock acquisition.
            return {
                "success": True,
                "reason": "sentinel not set",
                "next_attempt_at": None,
            }

        result = rebuild_db_from_files_and_verify(project_path)
        if not result["success"]:
            return _record_failure_and_return(
                project_path, reason=result["reason"], now=now
            )

        conn = result["conn"]
        try:
            if dualwrite.step_b_active():
                export_results = auto_export.render_all_cycles_to_files(
                    conn, project_path
                )
                failed = [
                    f"{phase}_{cycle_type}"
                    for (phase, cycle_type), ok in export_results.items()
                    if not ok
                ]
                if failed:
                    return _record_failure_and_return(
                        project_path,
                        reason=(
                            "auto_export_failed after repair: "
                            + ", ".join(failed)
                        ),
                        now=now,
                    )

            # Step 4: success — clear sentinel.
            dualwrite.clear_db_invalid(project_path)
            return {"success": True, "reason": None, "next_attempt_at": None}
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def needs_louder_signal(
    project_dir: str | Path,
    *,
    now: datetime | None = None,
) -> bool:
    """True if the sentinel has been set for >= LOUDER_SIGNAL_AFTER_SECONDS
    without successful repair. Used by the watcher / Saloon to decide
    whether to escalate from "logged" to "user-visible alert"."""
    from tagteam import dualwrite

    if not dualwrite.is_db_invalid(project_dir):
        return False

    info = dualwrite.get_db_invalid_info(project_dir) or {}
    since = info.get("since")
    if not since:
        return False

    if now is None:
        now = datetime.now(timezone.utc)
    try:
        since_dt = datetime.fromisoformat(since)
    except ValueError:
        return False

    return (now - since_dt).total_seconds() >= LOUDER_SIGNAL_AFTER_SECONDS


def rebuild_db_from_files_and_verify(project_dir: str | Path) -> dict[str, Any]:
    """Unconditionally rebuild the DB from canonical files and verify parity.

    Caller owns locking and sentinel management. On success, returns an
    open SQLite connection so callers can continue using the freshly
    verified DB. On failure, closes any opened connection and returns a
    reason suitable for operator-facing output or repair backoff state.
    """
    from tagteam import db
    from tagteam.migrate import _remove_sqlite_db_files

    project_path = Path(project_dir)

    bad_file = _check_all_files(project_path)
    if bad_file is not None:
        return {
            "success": False,
            "reason": (
                f"file_inconsistent: {bad_file['kind']} on "
                f"{bad_file.get('phase','?')}_{bad_file.get('type','?')}: "
                f"{bad_file.get('check') or bad_file.get('detail','')}"
            ),
            "conn": None,
        }

    conn = None
    try:
        db_path = project_path / db.DEFAULT_DB_RELPATH
        _remove_sqlite_db_files(db_path)
        conn = db.connect(db_path=db_path)
        db.import_from_files(project_path, conn)

        bad = _run_parity_unchecked(conn, project_path)
        if bad is not None:
            reason = (
                f"parity check failed after rebuild: {bad['kind']} "
                f"on {bad['phase']}_{bad['type']}"
            )
            try:
                conn.close()
            except Exception:
                pass
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
            "reason": f"repair raised: {type(e).__name__}: {e}",
            "conn": None,
        }


# ---------- Internal helpers ----------

def _compute_next_attempt(
    project_dir: str | Path, now: datetime
) -> datetime:
    """Compute the next allowed attempt time based on the current
    `consecutive_failures` count in the sentinel payload.

    Schedule (delay seconds): 60, 120, 240, 480, 960, 1920, 3600,
    3600, 3600, ... (cap kicks in at consecutive_failures >= 6).
    """
    from tagteam import dualwrite

    info = dualwrite.get_db_invalid_info(project_dir) or {}
    failures = info.get("consecutive_failures", 0)
    # Cap exponent to avoid Python int blow-up; effective delay
    # capped by min() anyway.
    exponent = min(failures, 16)
    delay = min(
        _BACKOFF_BASE_SECONDS * (2 ** exponent),
        _BACKOFF_CAP_SECONDS,
    )
    return now + timedelta(seconds=delay)


def _record_failure_and_return(
    project_dir: Path,
    *,
    reason: str,
    now: datetime,
) -> dict[str, Any]:
    """Persist failure metadata to the sentinel and produce the
    return dict for `attempt_repair`."""
    next_at = _compute_next_attempt(project_dir, now)
    _write_failure_payload(project_dir, reason=reason,
                           next_attempt_at=next_at, now=now)
    return {
        "success": False,
        "reason": reason,
        "next_attempt_at": next_at.isoformat(),
    }


def _write_failure_payload(
    project_dir: Path,
    *,
    reason: str,
    next_attempt_at: datetime,
    now: datetime,
) -> None:
    """Update the sentinel flag file with backoff state after a
    failed repair. Preserves the original `since` timestamp so the
    24-hour louder-signal threshold counts from first failure."""
    from tagteam import dualwrite

    flag_path = (
        project_dir / dualwrite.TAGTEAM_DIR
        / dualwrite.DB_INVALID_FLAG_FILENAME
    )
    info = dualwrite.get_db_invalid_info(project_dir) or {}
    info["last_attempt_at"] = now.isoformat()
    info["next_attempt_at"] = next_attempt_at.isoformat()
    info["consecutive_failures"] = info.get("consecutive_failures", 0) + 1
    info["last_failure_reason"] = reason
    info["updated_at"] = now.isoformat()
    # Preserve `since` and `reason` from the original mark_db_invalid
    # call. Don't overwrite `since` — it anchors the louder-signal
    # threshold.
    flag_path.write_text(json.dumps(info) + "\n", encoding="utf-8")


def _check_all_files(project_dir: Path) -> dict | None:
    """Run file-side sanity checks against every cycle on disk plus
    the project-level state file. Returns the first failing result,
    or None if every file is clean."""
    from tagteam import divergence
    import re as _re

    handoffs = project_dir / "docs" / "handoffs"
    if handoffs.is_dir():
        # Find cycles by status filenames: `<phase>_<type>_status.json`
        for f in sorted(handoffs.iterdir()):
            m = _re.match(r"^(.+)_(plan|impl)_status\.json$", f.name)
            if not m:
                continue
            phase, cycle_type = m.group(1), m.group(2)
            sanity = divergence.file_side_sanity(
                project_dir, phase, cycle_type
            )
            if sanity is not None:
                return {
                    "kind": divergence.CHECK_FILE_INCONSISTENT,
                    "phase": phase,
                    "type": cycle_type,
                    **sanity,
                }
        # Also catch cycles with rounds.jsonl but no status (mid-flight).
        for f in sorted(handoffs.iterdir()):
            m = _re.match(r"^(.+)_(plan|impl)_rounds\.jsonl$", f.name)
            if not m:
                continue
            phase, cycle_type = m.group(1), m.group(2)
            status_path = handoffs / f"{phase}_{cycle_type}_status.json"
            if status_path.exists():
                continue  # already checked above
            sanity = divergence.file_side_sanity(
                project_dir, phase, cycle_type
            )
            if sanity is not None:
                return {
                    "kind": divergence.CHECK_FILE_INCONSISTENT,
                    "phase": phase,
                    "type": cycle_type,
                    **sanity,
                }

    state_check = divergence.check_state_file_integrity(project_dir)
    if state_check is not None:
        return {
            "kind": divergence.CHECK_FILE_INCONSISTENT,
            **state_check,
        }
    return None


def _run_parity_unchecked(conn, project_dir: Path) -> dict | None:
    """Run a parity check across every cycle, BYPASSING the
    `db_invalid` gate that `divergence.check_cycle_divergence`
    normally enforces.

    Used during repair: we just rebuilt the DB and the sentinel is
    still set, but we need to know whether parity is now clean
    BEFORE we clear it. Returns the first failing result (excluding
    the `db_invalid` kind, which we are deliberately bypassing) or
    None if every cycle passes.
    """
    from tagteam import db as db_mod, divergence

    cycles = db_mod.list_cycles(conn)
    for c in cycles:
        # Do the file-side sanity + render compare directly,
        # without the sentinel gate.
        sanity = divergence.file_side_sanity(
            project_dir, c["phase"], c["type"]
        )
        if sanity is not None:
            return {
                "kind": divergence.CHECK_FILE_INCONSISTENT,
                "phase": c["phase"],
                "type": c["type"],
                **sanity,
            }
        from tagteam import cycle as cycle_mod
        # Stage 2: cycle.render_cycle is DB-backed; use the dedicated
        # file-side renderer so parity check stays file-vs-DB.
        file_md = cycle_mod.render_cycle_from_files(
            c["phase"], c["type"], str(project_dir)
        )
        db_md = db_mod.render_cycle(conn, c["phase"], c["type"])
        if file_md != db_md:
            return {
                "kind": divergence.CHECK_RENDER_MISMATCH,
                "phase": c["phase"],
                "type": c["type"],
                "detail": "post-repair parity failure",
            }
    return None
