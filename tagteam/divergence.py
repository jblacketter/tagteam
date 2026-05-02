"""
Divergence detection for Phase 28 Step A.

Runs after every dual-write to compare the file-side and DB-side
representations of a cycle. Classifies any mismatch into one of three
kinds:

  - `db_invalid`         — the `db_invalid` sentinel is set; comparison
                           is skipped because the DB is in an unknown
                           state.
  - `file_inconsistent`  — the file side itself failed a sanity check
                           (parseability, round/status coherence,
                           filename/phase identity). This is a
                           pre-existing hazard, not a dual-write bug —
                           classify it separately so dual-write
                           monitoring is not flooded by torn-write
                           false positives.
  - `render_mismatch`    — real divergence between file and DB renders.

Hash-only by default. Set `TAGTEAM_DIVERGENCE_FULL_DIFF=1` to include
the full unified diff in the diagnostic payload (useful for debugging,
but logs cycle content so it is opt-in).

This module does not log diagnostics itself — it returns a structured
result and lets the caller (the integration code in `cycle`/`state`)
decide whether to call `db.add_diagnostic`. The module-level
`log_divergence_if_needed` helper bundles the common pattern.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Result kinds. Strings rather than an Enum so they round-trip cleanly
# through JSON and are stable across schema changes.
CHECK_OK = "ok"
CHECK_DB_INVALID = "db_invalid"
CHECK_FILE_INCONSISTENT = "file_inconsistent"
CHECK_RENDER_MISMATCH = "render_mismatch"

# Fields a status.json file is required to carry. Subset of the actual
# schema written by `tagteam.cycle.init_cycle` — `baseline` is optional
# (present only for impl cycles that captured one) and explicitly not
# required here.
REQUIRED_STATUS_FIELDS = {
    "state", "round", "phase", "type", "lead", "reviewer",
}
# `ready_for` is intentionally NOT required: the file renderer treats
# a missing key as "?" (cycle.py:526 `status.get('ready_for', '?')`)
# and the DB schema has `ready_for_present` to preserve the same
# missing-vs-null distinction at parity. Treating missing
# `ready_for` as `file_inconsistent` would block the parity checker
# from exercising a supported legacy/edge shape.

ENV_FULL_DIFF = "TAGTEAM_DIVERGENCE_FULL_DIFF"


# ---------- Public API ----------

def check_cycle_divergence(
    conn,
    project_dir: str | Path,
    phase: str,
    cycle_type: str,
) -> dict[str, Any]:
    """Run a divergence check for one cycle.

    Pipeline:
      1. If `db_invalid` is set, return CHECK_DB_INVALID without
         touching the DB. Caller may still want to log this; the
         result includes the sentinel info.
      2. Run file-side sanity checks. Any failure short-circuits with
         CHECK_FILE_INCONSISTENT and the failing check name.
      3. Render both sides and compare. Mismatch → CHECK_RENDER_MISMATCH
         with hashes (and optionally full diff).
      4. Otherwise CHECK_OK.

    Never raises on storage errors — those are surfaced as result
    payloads. The caller's job is to log appropriately.
    """
    from tagteam.dualwrite import is_db_invalid, get_db_invalid_info

    project_dir = Path(project_dir)

    if is_db_invalid(project_dir):
        info = get_db_invalid_info(project_dir) or {}
        return {
            "kind": CHECK_DB_INVALID,
            "phase": phase,
            "type": cycle_type,
            "since": info.get("since"),
            "reason": info.get("reason"),
        }

    sanity = file_side_sanity(project_dir, phase, cycle_type)
    if sanity is not None:
        return {
            "kind": CHECK_FILE_INCONSISTENT,
            "phase": phase,
            "type": cycle_type,
            **sanity,
        }

    # Render comparison.
    try:
        from tagteam import cycle as cycle_mod
        from tagteam import db as db_mod
    except ImportError as e:
        return {
            "kind": CHECK_RENDER_MISMATCH,
            "phase": phase,
            "type": cycle_type,
            "detail": f"import_failed: {e}",
        }

    file_md = cycle_mod.render_cycle(phase, cycle_type, str(project_dir))
    db_md = db_mod.render_cycle(conn, phase, cycle_type)

    if file_md is None and db_md is None:
        # Neither side has the cycle. Not a divergence — could happen
        # if the caller asked about a cycle that doesn't exist on
        # either side yet. Caller decides whether that's interesting.
        return {"kind": CHECK_OK, "phase": phase, "type": cycle_type}

    if (file_md is None) != (db_md is None):
        return {
            "kind": CHECK_RENDER_MISMATCH,
            "phase": phase,
            "type": cycle_type,
            "detail": "missing_on_one_side",
            "file_present": file_md is not None,
            "db_present": db_md is not None,
        }

    if file_md != db_md:
        result = {
            "kind": CHECK_RENDER_MISMATCH,
            "phase": phase,
            "type": cycle_type,
            "file_sha": _hash(file_md),
            "db_sha": _hash(db_md),
            "ndiff_lines": _count_diff_lines(file_md, db_md),
        }
        if _full_diff_enabled():
            result["full_diff"] = _unified_diff(file_md, db_md)
        return result

    return {"kind": CHECK_OK, "phase": phase, "type": cycle_type}


def file_side_sanity(
    project_dir: str | Path,
    phase: str,
    cycle_type: str,
) -> dict[str, Any] | None:
    """Run file-side sanity checks for a single cycle.

    Returns None if all checks pass, otherwise a dict with `check`
    naming the failing check and `detail` describing the failure.

    Checks:
      1. `_rounds.jsonl` parseable (every line a valid JSON object).
      2. `_status.json` parseable.
      3. `_status.json` has all required fields.
      4. `_status.json.round == max(round) in _rounds.jsonl`.
      5. `_status.json.phase` and `.type` match the filename.

    The fifth check is a Codex-suggested addition beyond the four in
    the design doc — cheap and catches a specific class of file
    corruption (status JSON contents drifted from filename).
    """
    project_dir = Path(project_dir)
    handoffs = project_dir / "docs" / "handoffs"
    rounds_path = handoffs / f"{phase}_{cycle_type}_rounds.jsonl"
    status_path = handoffs / f"{phase}_{cycle_type}_status.json"

    # Check 1: rounds.jsonl parseable.
    rounds: list[dict[str, Any]] = []
    if rounds_path.exists():
        try:
            text = rounds_path.read_text(encoding="utf-8")
        except OSError as e:
            return {"check": "rounds_jsonl_readable", "detail": str(e)}
        for line_num, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as e:
                return {
                    "check": "rounds_jsonl_parseable",
                    "detail": f"line {line_num}: {e}",
                }
            # `1`, `"x"`, `[]` are valid JSON but not round entries.
            # Catch them explicitly so downstream code (which calls
            # `.get` and uses `round` in `max()`) doesn't crash on
            # malformed input.
            if not isinstance(parsed, dict):
                return {
                    "check": "rounds_jsonl_object_shape",
                    "detail": (
                        f"line {line_num}: expected JSON object, "
                        f"got {type(parsed).__name__}"
                    ),
                }
            # `round` must be an int — `bool` is an int subclass in
            # Python, so exclude it explicitly. Strings or floats
            # would break downstream `max()` comparisons.
            round_val = parsed.get("round")
            if not isinstance(round_val, int) or isinstance(round_val, bool):
                return {
                    "check": "rounds_jsonl_round_type",
                    "detail": (
                        f"line {line_num}: round must be an integer, "
                        f"got {type(round_val).__name__}: {round_val!r}"
                    ),
                }
            rounds.append(parsed)

    # Check 2 + 3: status.json parseable and complete.
    status: dict[str, Any] | None = None
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return {"check": "status_json_parseable", "detail": str(e)}
        except OSError as e:
            return {"check": "status_json_readable", "detail": str(e)}

        # `[]`, `"x"`, `42` are valid JSON but not status objects.
        # Catch them explicitly so downstream `.keys()` / `.get(...)`
        # don't raise AttributeError. Without this guard, repair's
        # _check_all_files pre-check would crash and escape repair's
        # backoff handling entirely.
        if not isinstance(status, dict):
            return {
                "check": "status_json_object_shape",
                "detail": (
                    f"expected JSON object, got "
                    f"{type(status).__name__}"
                ),
            }

        missing = REQUIRED_STATUS_FIELDS - set(status.keys())
        if missing:
            return {
                "check": "status_required_fields",
                "detail": f"missing: {sorted(missing)}",
            }

    # Check 4: status.round vs max(round) in rounds.
    if status is not None and rounds:
        max_round = max(r.get("round", 0) for r in rounds)
        if status.get("round") != max_round:
            return {
                "check": "status_round_matches_rounds_max",
                "detail": (
                    f"status.round={status.get('round')} "
                    f"max(rounds)={max_round}"
                ),
            }

    # Check 5: status.phase/type vs filename identity.
    if status is not None:
        if status.get("phase") != phase:
            return {
                "check": "status_phase_matches_filename",
                "detail": (
                    f"status.phase={status.get('phase')!r} "
                    f"expected={phase!r}"
                ),
            }
        if status.get("type") != cycle_type:
            return {
                "check": "status_type_matches_filename",
                "detail": (
                    f"status.type={status.get('type')!r} "
                    f"expected={cycle_type!r}"
                ),
            }

    return None


def check_state_file_integrity(
    project_dir: str | Path,
) -> dict[str, Any] | None:
    """Project-level state-file sanity check.

    Distinct from `file_side_sanity` (which is per-cycle): this one
    checks `handoff-state.json` for parseability and that the cycle
    it points to actually exists on disk. The callsite for this is
    typically a once-per-pass check during full-project parity, not
    once-per-cycle.

    Returns None if OK, otherwise `{check, detail}`.
    """
    project_dir = Path(project_dir)
    state_path = project_dir / "handoff-state.json"

    if not state_path.exists():
        # Absent state file is valid (project hasn't started a cycle).
        return None

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"check": "state_json_parseable", "detail": str(e)}
    except OSError as e:
        return {"check": "state_json_readable", "detail": str(e)}

    # Same gotcha as status_json_object_shape: a valid-JSON-but-not-
    # an-object state file would crash downstream `.get(...)` calls,
    # potentially escaping repair's backoff handling.
    if not isinstance(state, dict):
        return {
            "check": "state_json_object_shape",
            "detail": (
                f"expected JSON object, got {type(state).__name__}"
            ),
        }

    phase = state.get("phase")
    cycle_type = state.get("type")
    if phase and cycle_type:
        handoffs = project_dir / "docs" / "handoffs"
        status_path = handoffs / f"{phase}_{cycle_type}_status.json"
        if not status_path.exists():
            return {
                "check": "state_references_existing_cycle",
                "detail": (
                    f"state.phase={phase!r} state.type={cycle_type!r} "
                    f"but {status_path.name} not found"
                ),
            }

    return None


def log_divergence_if_needed(
    conn,
    project_dir: str | Path,
    phase: str,
    cycle_type: str,
) -> dict[str, Any]:
    """Run `check_cycle_divergence` and log a diagnostic to the DB if
    the result is anything other than `ok`. Returns the result dict
    so the caller can react further if needed (e.g. mark DB invalid
    on `render_mismatch`).

    Convenience wrapper for the integration code, which will call
    this from `cycle.add_round`, `cycle.init_cycle`, etc. after each
    dual-write.
    """
    result = check_cycle_divergence(conn, project_dir, phase, cycle_type)
    if result["kind"] == CHECK_OK:
        return result

    # `db_invalid` is informational — log it so an operator sees that
    # divergence checks are being skipped. `file_inconsistent` and
    # `render_mismatch` are the real signals.
    try:
        from tagteam import db as db_mod
        db_mod.add_diagnostic(
            conn,
            kind=result["kind"],
            payload=result,
            ts=datetime.now(timezone.utc).isoformat(),
        )
    except Exception:
        # Diagnostic logging must never break the caller — if the DB
        # is the very thing that's broken, this would otherwise mask
        # the root cause. Swallow.
        pass
    return result


# ---------- Helpers ----------

def _hash(text: str) -> str:
    """Short stable hash for diagnostic payloads. SHA-256 truncated to
    16 hex chars is plenty for distinguishing renders without bloating
    diagnostics rows."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _count_diff_lines(a: str, b: str) -> int:
    """Number of `+`/`-` lines in a unified diff of `a` vs `b`.
    Rough size signal that doesn't leak content."""
    diff = difflib.unified_diff(
        a.splitlines(), b.splitlines(), n=0,
    )
    count = 0
    for line in diff:
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            count += 1
    return count


def _unified_diff(a: str, b: str) -> str:
    """Full unified diff text. Only included in payloads when
    `TAGTEAM_DIVERGENCE_FULL_DIFF=1` because it logs cycle content."""
    return "".join(difflib.unified_diff(
        a.splitlines(keepends=True),
        b.splitlines(keepends=True),
        fromfile="file",
        tofile="db",
        n=2,
    ))


def _full_diff_enabled() -> bool:
    return os.environ.get(ENV_FULL_DIFF) == "1"
