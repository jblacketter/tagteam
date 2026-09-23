"""
Cycle storage and CLI for structured handoff rounds.

Replaces markdown-based cycle documents with append-only JSONL rounds
and a small JSON status file, updated via CLI commands.

File structure per cycle:
    docs/handoffs/{phase}_{type}_status.json   — cycle metadata + state
    docs/handoffs/{phase}_{type}_rounds.jsonl   — append-only round log
"""

import copy
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# git's well-known empty-tree object SHA. Diffing HEAD against this lists
# every path in HEAD — used by `scope-diff` when baseline.sha is null
# (plan-init happened in a no-commit repo).
_GIT_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# Tagteam-managed paths that scope-diff must exclude. These are
# bookkeeping artifacts of the review system itself, not phase work,
# and they are written by `init_cycle` / `add_round` / state updates
# AFTER baseline capture — so they would otherwise appear in current_dirty
# without being in baseline_dirty.
_TAGTEAM_ARTIFACT_FILES = frozenset({
    "handoff-state.json",
    ".handoff-state.tmp",
    "handoff-diagnostics.jsonl",
    "tagteam-orders.json",        # Phase 70: the arbiter's standing orders are not implementation work
})
_TAGTEAM_ARTIFACT_PREFIXES = ("docs/handoffs/",)


def _is_tagteam_artifact(path: str) -> bool:
    """True if `path` (project-relative, forward-slash) is a tagteam
    bookkeeping file that should be excluded from `scope-diff` output."""
    if path in _TAGTEAM_ARTIFACT_FILES:
        return True
    return any(path.startswith(p) for p in _TAGTEAM_ARTIFACT_PREFIXES)

VALID_ACTIONS = {
    "SUBMIT_FOR_REVIEW", "REQUEST_CHANGES", "APPROVE",
    "ESCALATE", "NEED_HUMAN", "AMEND",
}
VALID_ROLES = {"lead", "reviewer"}
VALID_TYPES = {"plan", "impl"}

# Phase 38: the gatekeeper writes round entries too (never through the CLI's
# add path — only via `ensure_gate_applied`).
ROLE_GATEKEEPER = "gatekeeper"
GATE_PASS = "GATE_PASS"
GATE_BOUNCE = "GATE_BOUNCE"
GATE_ACTIONS = {GATE_PASS, GATE_BOUNCE}

# Auto-escalate after this many consecutive rounds with no progress
# (lead re-submitting identical content = stuck, not converging)
STALE_ROUND_LIMIT = 10

# Status transitions keyed by action (cycle status.json)
_TRANSITIONS = {
    "SUBMIT_FOR_REVIEW": {"state": "in-progress", "ready_for": "reviewer"},
    "REQUEST_CHANGES":   {"state": "in-progress", "ready_for": "lead"},
    "APPROVE":           {"state": "approved",    "ready_for": None},
    "ESCALATE":          {"state": "escalated",   "ready_for": "human"},
    "NEED_HUMAN":        {"state": "needs-human",  "ready_for": "human"},
}

from tagteam.contract import STANDARD_TURN_COMMAND as _STATE_COMMAND   # noqa: E402


def _resolve(project_dir: str) -> str:
    """Resolve "." to the git repo root so cycle writes always target the
    repo's docs/handoffs/ regardless of cwd. Explicit paths are honored.

    Fixes the nested-project silent-write bug (Issue #1, 2026-04-24): running
    `tagteam cycle init` from a subdir of a tagteam project used to write
    into that subdir instead of the repo root.
    """
    if project_dir == ".":
        from tagteam.state import _resolve_project_root
        return _resolve_project_root()
    return project_dir


def _handoffs_dir(project_dir: str) -> Path:
    return Path(_resolve(project_dir)) / "docs" / "handoffs"


def _status_path(phase: str, cycle_type: str, project_dir: str) -> Path:
    return _handoffs_dir(project_dir) / f"{phase}_{cycle_type}_status.json"


def _rounds_path(phase: str, cycle_type: str, project_dir: str) -> Path:
    return _handoffs_dir(project_dir) / f"{phase}_{cycle_type}_rounds.jsonl"


def _legacy_status_path(phase: str, cycle_type: str,
                        project_dir: str) -> Path | None:
    """Find a status JSON file: docs/handoffs/ first, then .tagteam/legacy/.

    Returns None if neither location has the file. Used by readers and
    parity checks that must keep working after `migrate --to-step-b`
    moves files out of docs/handoffs/.
    """
    primary = _status_path(phase, cycle_type, project_dir)
    if primary.exists():
        return primary
    legacy = (Path(_resolve(project_dir)) / ".tagteam" / "legacy"
              / f"{phase}_{cycle_type}_status.json")
    if legacy.exists():
        return legacy
    return None


def _legacy_rounds_path(phase: str, cycle_type: str,
                        project_dir: str) -> Path | None:
    """Find a rounds JSONL file: docs/handoffs/ first, then .tagteam/legacy/.
    Returns None if neither location has the file."""
    primary = _rounds_path(phase, cycle_type, project_dir)
    if primary.exists():
        return primary
    legacy = (Path(_resolve(project_dir)) / ".tagteam" / "legacy"
              / f"{phase}_{cycle_type}_rounds.jsonl")
    if legacy.exists():
        return legacy
    return None


class CycleReadError(Exception):
    """Raised when a runtime cycle read cannot be satisfied.

    Currently raised when `dualwrite.is_db_invalid()` is set AND the
    legacy file source is unavailable (e.g. operator ran
    `migrate --to-step-b` before the DB recovered). The exception
    message contains an operator recovery hint.
    """


def _git(project_dir: str, *args: str) -> tuple[int, str]:
    """Run a git command and return (returncode, stdout). Never raises."""
    try:
        r = subprocess.run(
            ["git", "-C", project_dir, *args],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode, r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return 1, ""


def _capture_baseline(project_dir: str, source: str) -> dict | None:
    """Snapshot git HEAD + working-tree drift for use in impl scope audits.

    Returns a dict with keys {sha, dirty_paths, captured_at, source}, or
    None if the directory is not a git repo. Never raises; on any error
    returns None or partial data with sha=None.

    `dirty_paths` preserves the porcelain status prefix (e.g. " M docs/foo.md")
    so reviewers can see staged/unstaged distinctions. `scope-diff` strips
    the prefix when doing path comparisons.
    """
    sha_rc, sha_out = _git(project_dir, "rev-parse", "HEAD")
    porc_rc, porc_out = _git(project_dir, "status", "--porcelain")
    if sha_rc != 0 and porc_rc != 0:
        return None
    sha = sha_out.strip() if sha_rc == 0 else None
    dirty = sorted(line for line in porc_out.splitlines() if line) if porc_rc == 0 else []
    return {
        "sha": sha,
        "dirty_paths": dirty,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
    }


def _blob_sha256(project_dir: str, rel_path: str) -> str | None:
    """sha256 of the working-tree file at `rel_path` (None if absent /
    unreadable / a directory)."""
    import hashlib
    p = Path(project_dir) / rel_path
    try:
        if not p.is_file():
            return None
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def capture_impl_boundary(project_dir: str, source: str) -> dict | None:
    """Phase 38: snapshot HEAD + the CONTENT HASHES of every dirty/untracked
    path, so the gate can tell "still dirty but unchanged" from "changed".
    {sha, dirty: {path: sha256|null}, captured_at, source}; None if not a
    git repo. Never raises."""
    sha_rc, sha_out = _git(project_dir, "rev-parse", "HEAD")
    # --untracked-files=all: an untracked DIRECTORY must be captured file
    # by file, or a file added to it later would hide behind the same
    # collapsed `dir/` key.
    porc_rc, porc_out = _git(project_dir, "status", "--porcelain", "--untracked-files=all")
    if sha_rc != 0 and porc_rc != 0:
        return None
    sha = sha_out.strip() if sha_rc == 0 else None
    dirty: dict[str, str | None] = {}
    if porc_rc == 0:
        for line in porc_out.splitlines():
            if not line:
                continue
            path = _porcelain_path(line)
            dirty[path] = _blob_sha256(project_dir, path)
    return {"sha": sha, "dirty": dirty, "captured_at": datetime.now(timezone.utc).isoformat(),
            "source": source}


def _resolve_impl_boundary_for_cycle(phase: str, cycle_type: str, project_dir: str) -> dict | None:
    """Impl init copies the plan cycle's boundary; a plan cycle has none yet
    (it is captured on approval). None when the plan has no boundary
    (approved before 3.2.0 / legacy)."""
    if cycle_type != "impl":
        return None
    b = read_impl_boundary(phase, "plan", project_dir)
    if b is not None:
        b = copy.deepcopy(b)
        b["source"] = "copied-from-plan"
    return b


def read_rounds_file(phase: str, cycle_type: str, project_dir: str) -> list[dict]:
    """The cycle's round entries from the canonical rounds FILE, every key
    intact (the DB-first `read_rounds` view drops keys the `rounds` table
    has no column for — the gate's `gate_event`/`gate_id`/`gate_attempt`
    among them). [] when no file exists."""
    project_dir = _resolve(project_dir)
    p = _legacy_rounds_path(phase, cycle_type, project_dir)
    return _read_rounds_from_file(p) if p is not None else []


def read_impl_boundary(phase: str, cycle_type: str, project_dir: str) -> dict | None:
    """The cycle's `impl_boundary` from its canonical status FILE (the
    DB-first `read_status` view does not carry it — schema v8 adds no
    cycle columns; the status file is the store of record for this key,
    exactly as it is written)."""
    project_dir = _resolve(project_dir)
    for p in (_status_path(phase, cycle_type, project_dir),
              _legacy_status_path(phase, cycle_type, project_dir)):
        if p is None or not Path(p).exists():
            continue
        st = _read_status_from_file(Path(p)) or {}
        b = st.get("impl_boundary")
        if isinstance(b, dict):
            return b
    return None


def _porcelain_path(line: str) -> str:
    """Strip the leading 3-char status prefix from a porcelain status line.

    For rename entries (`R  old -> new`), returns the new-path side, since
    that's the post-rename path that will appear in subsequent diffs.
    """
    body = line[3:] if len(line) > 3 else line
    if " -> " in body:
        body = body.split(" -> ", 1)[1]
    # Git quotes paths with special chars; strip surrounding quotes
    # without trying to fully decode escape sequences (rare edge case).
    if len(body) >= 2 and body.startswith('"') and body.endswith('"'):
        body = body[1:-1]
    return body


# --- Core functions ---

def _count_stale_rounds(phase: str, cycle_type: str, project_dir: str) -> int:
    """Count consecutive recent rounds with no progress.

    Progress means the lead's SUBMIT_FOR_REVIEW content changed from
    their previous submission.  If the lead keeps re-submitting identical
    content, those rounds are "stale" — the cycle is stuck, not converging.

    Returns the number of consecutive stale rounds (from most recent backward).
    """
    # Called inside add_round AFTER the new round was appended to the
    # file but BEFORE the shadow DB sync, so the DB is stale here.
    # Read directly from the file source.
    rounds = _read_rounds_from_file(
        _rounds_path(phase, cycle_type, project_dir)
    )

    # Extract lead submissions in order
    submissions = [
        r["content"] for r in rounds
        if r["role"] == "lead" and r["action"] == "SUBMIT_FOR_REVIEW"
    ]

    if len(submissions) < 2:
        return 0

    # Count consecutive identical submissions from the end
    stale = 0
    for i in range(len(submissions) - 1, 0, -1):
        if submissions[i] == submissions[i - 1]:
            stale += 1
        else:
            break

    return stale


def _resolve_baseline_for_cycle(phase: str, cycle_type: str,
                                project_dir: str) -> dict | None:
    """Decide the baseline value to record on `init_cycle`.

    Plan cycles capture fresh. Impl cycles propagate forward from the
    matching plan cycle's status JSON when its `baseline` block is
    non-null (regardless of whether `baseline.sha` inside is null).
    Falls back to fresh capture only when the plan status is missing,
    has no `baseline` key, or has `baseline == None` (plan ran outside
    a git repo).
    """
    if cycle_type == "plan":
        return _capture_baseline(project_dir, source="plan-init")

    # Use the Stage 2 read_status helper so plan-baseline propagation
    # works whether the plan status is in the DB, docs/handoffs/, or
    # .tagteam/legacy/ (post Step B activation). The CycleReadError
    # case is fine to let propagate — if the operator must repair, they
    # need to know.
    plan_status = read_status(phase, "plan", project_dir)
    if plan_status:
        plan_baseline = plan_status.get("baseline")
        if isinstance(plan_baseline, dict):
            propagated = copy.deepcopy(plan_baseline)
            propagated["source"] = "copied-from-plan"
            return propagated

    print(
        f"[tagteam] warning: no plan-cycle baseline for phase '{phase}'; "
        f"capturing impl baseline from current state. This may include "
        f"changes already made during implementation.",
        file=sys.stderr,
    )
    return _capture_baseline(project_dir, source="impl-init-fallback")


def init_cycle(phase: str, cycle_type: str, lead: str, reviewer: str,
               content: str, project_dir: str = ".",
               updated_by: str | None = None) -> dict:
    """Create a new cycle atomically with the lead's first submission.

    Writes rounds JSONL + status JSON, then derives handoff-state.json
    from the cycle status so the top-level state is always in sync.
    `updated_by` defaults to `lead` (since init always submits as lead).

    Phase 28 Step A: also performs a shadow DB write under the project
    writer lock, then runs a divergence check. Files are canonical;
    DB-write failures mark `db_invalid` and do not raise.
    """
    from tagteam import dualwrite

    project_dir = _resolve(project_dir)
    from tagteam.participants import check_participants
    check_participants(project_dir, proposed=(lead, reviewer))
    handoffs = _handoffs_dir(project_dir)
    handoffs.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()

    baseline = _resolve_baseline_for_cycle(phase, cycle_type, project_dir)

    status = {
        "state": "in-progress",
        "ready_for": "reviewer",
        "round": 1,
        "phase": phase,
        "type": cycle_type,
        "lead": lead,
        "reviewer": reviewer,
        "date": now[:10],
        "baseline": baseline,
    }
    # Phase 38: an impl cycle inherits the plan's implementation boundary
    # (written only when one exists — plan cycles and legacy/non-git projects
    # carry no key, so flag-off status files are byte-identical to 3.1).
    _ib = _resolve_impl_boundary_for_cycle(phase, cycle_type, project_dir)
    if _ib is not None:
        status["impl_boundary"] = _ib

    entry = {
        "round": 1,
        "role": "lead",
        "action": "SUBMIT_FOR_REVIEW",
        "content": content,
        "ts": now,
    }

    sp = _status_path(phase, cycle_type, project_dir)
    rp = _rounds_path(phase, cycle_type, project_dir)

    with dualwrite.writer_lock(project_dir):
        check_participants(project_dir, proposed=(lead, reviewer))
        # File writes — canonical during Step A.
        rp.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        sp.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

        _derive_top_level_state(
            phase, cycle_type, project_dir,
            updated_by=updated_by or lead,
        )

        # Shadow DB write + divergence check.
        _shadow_db_after_cycle_write(project_dir, phase, cycle_type)
        _auto_export_cycle_md(project_dir, phase, cycle_type)

    return status


RULING_PREFIX = "[ARBITER RULING by "


# Phase 39: keys a round entry owns — `add_round(meta=)` may add keys,
# never these.
ENTRY_RESERVED_KEYS = frozenset({"round", "role", "action", "content", "ts", "updated_by", "summary"})


def _validate_entry_meta(meta: dict | None, updated_by: str | None) -> dict | None:
    """Phase 39: validate `add_round(meta=)` BEFORE any lock/write.
    Returns the meta to merge (None when absent/empty)."""
    if meta is None:
        return None
    if not isinstance(meta, dict):
        raise ValueError("add_round meta must be a dict")
    if not meta:
        return None
    bad_keys = [k for k in meta if not isinstance(k, str)]
    if bad_keys:
        raise ValueError(f"add_round meta keys must be strings: {bad_keys!r}")
    reserved = sorted(k for k in meta if k in ENTRY_RESERVED_KEYS)
    if reserved:
        raise ValueError(f"add_round meta cannot set reserved entry keys: {reserved}")
    try:
        json.dumps(meta)
    except (TypeError, ValueError) as e:
        raise ValueError(f"add_round meta must be JSON-serialisable: {e}")
    if not updated_by or not str(updated_by).strip():
        raise ValueError("add_round meta requires an explicit updated_by (entry-level attribution)")
    return dict(meta)


def add_round(phase: str, cycle_type: str, role: str, action: str,
              round_num: int, content: str, project_dir: str = ".",
              updated_by: str | None = None, *,
              _skip_stale_gate: bool = False,
              meta: dict | None = None) -> dict:
    """Append a round entry to the JSONL log, update cycle status,
    and derive handoff-state.json from the new cycle status.

    If `updated_by` is not provided, it is inferred from the cycle
    status (`lead` field for role=lead, `reviewer` field for
    role=reviewer). This keeps the top-level state in sync even when
    a caller forgets to pass `--updated-by`.

    Phase 39 — `meta` (in-process satellites only, not the CLI): extra
    JSON keys merged into the entry atomically with the transition (e.g.
    the panel's `panel_event` / `panel_id` / `panel_lenses`). Reserved
    keys (`ENTRY_RESERVED_KEYS`) are rejected before the lock; a
    non-empty `meta` REQUIRES an explicit `updated_by`, which is written
    into the entry (`entry["updated_by"]`) so entry-level and state-level
    attribution are the same string. `meta=None` writes are byte-identical
    to before (no `updated_by` key on the entry). Not honoured by AMEND.
    """
    from tagteam import dualwrite

    if action not in VALID_ACTIONS:
        raise ValueError(f"Invalid action: {action}. Must be one of: {', '.join(sorted(VALID_ACTIONS))}")
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role}. Must be one of: {', '.join(sorted(VALID_ROLES))}")
    meta = _validate_entry_meta(meta, updated_by)
    if meta is not None and action == "AMEND":
        raise ValueError("add_round meta is not supported for AMEND")

    project_dir = _resolve(project_dir)
    from tagteam.participants import check_participants
    if not _skip_stale_gate:
        check_participants(project_dir, cycle=(phase, cycle_type))
    now = datetime.now(timezone.utc).isoformat()

    # AMEND: lead-only mid-review update. No round advance, no state
    # transition, no top-level state derive (turn/round/status are stable),
    # no stale-round detection (AMENDs are progress, not staleness).
    if action == "AMEND":
        if role != "lead":
            raise ValueError("AMEND requires role=lead")
        status = read_status(phase, cycle_type, project_dir) or {}
        if status.get("state") != "in-progress" or status.get("ready_for") != "reviewer":
            raise ValueError(
                "AMEND only valid mid-review (after SUBMIT_FOR_REVIEW, "
                "before REQUEST_CHANGES/APPROVE)."
            )
        active_round = status.get("round")
        if round_num != active_round:
            raise ValueError(
                f"AMEND --round {round_num} does not match the active round "
                f"({active_round}). Pass --round {active_round}."
            )
        entry = {
            "round": round_num, "role": role, "action": action,
            "content": content, "ts": now,
        }
        rp = _rounds_path(phase, cycle_type, project_dir)

        with dualwrite.writer_lock(project_dir):
            if not _skip_stale_gate:
                check_participants(project_dir, cycle=(phase, cycle_type))
            with open(rp, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            # AMEND is rounds-only on the DB side too — no status
            # mutation, no state derive.
            _shadow_db_after_amend(project_dir, phase, cycle_type, entry)
            _auto_export_cycle_md(project_dir, phase, cycle_type)

        return status

    entry = {
        "round": round_num,
        "role": role,
        "action": action,
        "content": content,
        "ts": now,
    }
    if meta is not None:
        # entry-level attribution first (reserved, from the argument), then
        # the satellite's keys — validated above, none of them reserved
        entry["updated_by"] = str(updated_by).strip()
        entry.update(meta)

    # Append to JSONL
    rp = _rounds_path(phase, cycle_type, project_dir)

    with dualwrite.writer_lock(project_dir):
        if not _skip_stale_gate:
            check_participants(project_dir, cycle=(phase, cycle_type))
        with open(rp, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        # Update status — read from file directly (DB returns a
        # different field order, which would shift fields when we
        # write back to disk and break parity-corpus golden files).
        status = _read_status_from_file(
            _status_path(phase, cycle_type, project_dir)
        ) or {}
        prior_cycle_state = status.get("state")
        transition = _TRANSITIONS[action]
        status["state"] = transition["state"]
        status["ready_for"] = transition["ready_for"]

        # Auto-escalate only when the cycle is stuck (no progress),
        # not merely because it reached a certain round number.
        # (`_skip_stale_gate` is set only by `add_ruling`: an arbiter's
        # REQUEST_CHANGES after auto-escalation must hand the turn back to
        # the lead, not immediately re-escalate.)
        auto_escalate = False
        if action == "REQUEST_CHANGES" and not _skip_stale_gate:
            stale = _count_stale_rounds(phase, cycle_type, project_dir)
            if stale >= STALE_ROUND_LIMIT:
                auto_escalate = True
                status["state"] = "escalated"
                status["ready_for"] = "human"

        # Only advance round when caller provides a higher value
        if round_num > status.get("round", 0):
            status["round"] = round_num

        # Phase 38: the implementation-work boundary is captured the moment
        # a PLAN cycle is approved (reviewer APPROVE or arbiter ruling) —
        # before implementation can begin — and later copied onto the impl
        # cycle. The gate's scope check measures work since this snapshot.
        if cycle_type == "plan" and action == "APPROVE" and not status.get("impl_boundary"):
            _ib = capture_impl_boundary(project_dir, source="plan-approve")
            if _ib is not None:                       # not a git repo → no key at all
                status["impl_boundary"] = _ib

        # Phase 70: a fresh impl APPROVE decides, ONCE, what the standing
        # orders make of the run, and records it on the cycle status. Every
        # derive (this one, and any later `state sync`) applies the recorded
        # decision and never re-resolves. No explicit orders → no key at all.
        # Only a real transition INTO approved decides: a repeated APPROVE of
        # an already-approved cycle keeps (and re-applies, not fresh) the
        # decision it has — it must never re-decide a stopped run.
        fresh_decision = False
        if (cycle_type == "impl" and action == "APPROVE" and status.get("state") == "approved"
                and prior_cycle_state != "approved"):
            from tagteam import orders as _orders
            from tagteam.state import read_state as _read_state
            _dec = _orders.decide(_read_state(project_dir) or {}, phase, project_dir)
            status.pop("run_decision", None)
            if _dec is not None:
                status["run_decision"] = _dec
                fresh_decision = True

        sp = _status_path(phase, cycle_type, project_dir)
        sp.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

        # Infer updated_by from the cycle roster when the caller didn't supply it,
        # so the top-level state always stays in sync with the per-cycle source
        # of truth.
        resolved_updated_by = updated_by
        if not resolved_updated_by:
            if role == "lead":
                resolved_updated_by = status.get("lead") or role
            else:
                resolved_updated_by = status.get("reviewer") or role

        _derive_top_level_state(
            phase, cycle_type, project_dir,
            updated_by=resolved_updated_by,
            fresh_decision=fresh_decision,
        )

        # Shadow DB write + divergence check.
        _shadow_db_after_cycle_write(project_dir, phase, cycle_type)
        _auto_export_cycle_md(project_dir, phase, cycle_type)

    if fresh_decision:
        from tagteam import orders as _orders
        print(f"note: standing orders: {_orders.describe_decision(status['run_decision'])}",
              file=sys.stderr)
    return status


def add_ruling(phase: str, cycle_type: str, action: str, content: str,
               by: str, project_dir: str = ".") -> dict:
    """Phase 33: record an arbiter ruling as a reviewer-role entry at the
    current round — the arbiter takes the reviewer's seat — with the
    `[ARBITER RULING by <name>]` prefix and `updated_by = by`. Applies the
    plain transition WITHOUT the stale-round auto-escalation gate, and
    otherwise does everything `add_round` does (canonical rounds file,
    status, shadow DB, auto-export, top-level state). Valid only while the
    cycle is `escalated` or `needs-human`."""
    if action not in ("APPROVE", "REQUEST_CHANGES"):
        raise ValueError("add_ruling supports APPROVE and REQUEST_CHANGES")
    if not by or not by.strip():
        raise ValueError("add_ruling requires the arbiter's name")
    project_dir = _resolve(project_dir)
    status = read_status(phase, cycle_type, project_dir) or {}
    if status.get("state") not in ("escalated", "needs-human"):
        raise ValueError(
            f"cycle {phase}_{cycle_type} is {status.get('state')!r}, not "
            f"escalated/needs-human — nothing to rule on")
    round_num = int(status.get("round") or 0)
    body = (content or "").strip()
    text = f"{RULING_PREFIX}{by}] {body}" if body else f"{RULING_PREFIX}{by}] {action}"
    return add_round(phase, cycle_type, "reviewer", action, round_num, text,
                     project_dir, updated_by=by, _skip_stale_gate=True)


def rearm(phase: str, cycle_type: str, ready_for: str, by: str,
          project_dir: str = ".") -> dict:
    """Phase 33: after `NEED_HUMAN`/`ESCALATE`, re-arm the cycle to
    `in-progress / ready_for <role>` without a rounds entry (the arbiter's
    answer is recorded as an interjection). Updates the canonical status
    file, the shadow DB, auto-export and the top-level state under the
    writer lock. Valid only while the cycle is escalated/needs-human."""
    from tagteam import dualwrite
    if ready_for not in VALID_ROLES:
        raise ValueError(f"ready_for must be lead or reviewer, got {ready_for!r}")
    project_dir = _resolve(project_dir)
    status = _read_status_from_file(_status_path(phase, cycle_type, project_dir)) \
        or read_status(phase, cycle_type, project_dir) or {}
    if status.get("state") not in ("escalated", "needs-human"):
        raise ValueError(
            f"cycle {phase}_{cycle_type} is {status.get('state')!r}, not "
            f"escalated/needs-human — nothing to re-arm")
    with dualwrite.writer_lock(project_dir):
        status["state"] = "in-progress"
        status["ready_for"] = ready_for
        sp = _status_path(phase, cycle_type, project_dir)
        sp.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
        _derive_top_level_state(phase, cycle_type, project_dir, updated_by=by)
        _shadow_db_after_cycle_write(project_dir, phase, cycle_type)
        _auto_export_cycle_md(project_dir, phase, cycle_type)
    return status


_CYCLE_STATE_TO_TOP_LEVEL = {
    # Maps per-cycle (state, ready_for) → (turn, status, result)
    # `None` for turn means leave unset (e.g. escalated, done).
    ("in-progress", "reviewer"):  ("reviewer", "ready",     None),
    ("in-progress", "lead"):      ("lead",     "ready",     None),
    ("approved",    None):        (None,       "done",      "approved"),
    ("escalated",   "human"):     (None,       "escalated", None),
    ("needs-human", "human"):     (None,       "escalated", None),
}


def _derive_top_level_state(phase: str, cycle_type: str,
                            project_dir: str,
                            updated_by: str | None = None,
                            fresh_decision: bool = False) -> dict | None:
    """Rewrite handoff-state.json to reflect the given cycle's current status.

    Per-cycle status is the source of truth. This reads the cycle's
    status JSON, maps it into top-level fields via the invertible
    mapping above, preserves roadmap context when the phase matches the
    active roadmap phase, and writes atomically. Uses replace=True so
    stale fields from prior cycles cannot leak via shallow merge.

    Returns the new state dict, or None if the cycle status is missing.
    """
    from tagteam.state import (
        read_state, update_state, normalize_phase_key, VALID_TURNS,
    )

    project_dir = _resolve(project_dir)
    # Runs inside add_round/init_cycle's writer lock, BEFORE the shadow
    # DB write. The DB is stale by definition at this point — read the
    # canonical file directly.
    cycle_status = _read_status_from_file(
        _status_path(phase, cycle_type, project_dir)
    )
    if cycle_status is None:
        return None

    cstate = cycle_status.get("state")
    ready_for = cycle_status.get("ready_for")
    mapping = _CYCLE_STATE_TO_TOP_LEVEL.get((cstate, ready_for))
    if mapping is None:
        # Unknown combination — fail safe: leave state alone, surface via
        # diagnose rather than writing a broken top-level.
        return None
    turn, status, result = mapping

    updates: dict = {
        "phase": phase,
        "type": cycle_type,
        "round": cycle_status.get("round"),
        "status": status,
        "command": _STATE_COMMAND,
    }
    if turn in VALID_TURNS:
        updates["turn"] = turn
    if result is not None:
        updates["result"] = result
    if updated_by:
        updates["updated_by"] = updated_by

    # Preserve roadmap context only when this phase is the current roadmap phase.
    current_state = read_state(project_dir) or {}
    roadmap = current_state.get("roadmap")
    if roadmap and current_state.get("run_mode") == "full-roadmap":
        queue = roadmap.get("queue") or []
        idx = roadmap.get("current_index", 0)
        if 0 <= idx < len(queue):
            if normalize_phase_key(phase) == normalize_phase_key(queue[idx]):
                updates["roadmap"] = roadmap
                updates["run_mode"] = "full-roadmap"

    if "run_mode" not in updates:
        updates["run_mode"] = "single-phase"

    # Phase 70: apply the RECORDED standing-orders decision of an approved
    # impl cycle (never re-resolve: `state sync` must not re-decide), and
    # carry the run override through the replace-write. Only the fresh
    # approval write (`fresh_decision`, passed by `add_round` alone) may
    # materialise a converted run's queue or drop the override.
    decision = cycle_status.get("run_decision") if (cycle_type == "impl" and cstate == "approved") else None
    if decision is not None:
        from tagteam import orders as _orders
        _orders.apply_decision(updates, decision, fresh=fresh_decision)
        keep_orders = _orders.keep_run_override(decision, fresh=fresh_decision)
    else:
        keep_orders = True
    if keep_orders and "orders" in current_state:
        updates["orders"] = current_state["orders"]

    return update_state(updates, project_dir, replace=True)


def _update_handoff_state(phase: str, cycle_type: str, action: str,
                          round_num: int, updated_by: str,
                          project_dir: str = ".",
                          auto_escalate: bool = False) -> None:
    """Update handoff-state.json to match the current per-cycle status.

    Thin wrapper around _derive_top_level_state. Kept for call-site
    compatibility; action/round_num/auto_escalate are no longer needed
    for the derivation itself (the cycle status file already reflects
    the outcome) but remain for backward compatibility with callers.
    """
    _derive_top_level_state(phase, cycle_type, project_dir, updated_by)


def _read_status_from_file(path: Path) -> dict | None:
    """File-side status reader. Returns None on parse error or missing."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_rounds_from_file(path: Path) -> list[dict]:
    """File-side rounds reader. Returns [] on missing or parse error."""
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


# Phase 55: pass as `conn=` to `read_status` / `read_rounds` to skip the DB
# step entirely (files only) — a report with no readable database must never
# fall through to `db.connect`, which creates and migrates.
FILES_ONLY = object()


def _read_status_from_db(phase: str, cycle_type: str,
                        project_dir: str, conn=None) -> dict | None:
    """DB-side status reader. Returns the file-shape dict or None.

    Converts `db.get_cycle`'s schema to the historical file shape
    expected by callers. The conversion mirrors `db.export_to_files`
    so a status produced here matches what export would have written.
    """
    from tagteam import db
    if conn is FILES_ONLY:
        return None
    owned = conn is None
    try:
        if owned:
            conn = db.connect(project_dir=project_dir)
        cycle = db.get_cycle(conn, phase, cycle_type)
        if cycle is None:
            return None
        status: dict = {
            "state": cycle["state"],
            "round": cycle.get("round") or 0,
            "phase": phase,
            "type": cycle_type,
            "lead": cycle.get("lead"),
            "reviewer": cycle.get("reviewer"),
            "date": cycle.get("date"),
        }
        if cycle.get("ready_for_present"):
            status["ready_for"] = cycle.get("ready_for")
        baseline = cycle.get("baseline")
        if baseline is not None:
            status["baseline"] = baseline
        return status
    except Exception:
        return None
    finally:
        if owned and conn is not None:
            try: conn.close()
            except Exception: pass


def _read_rounds_from_db(phase: str, cycle_type: str,
                        project_dir: str, conn=None) -> list[dict]:
    """DB-side rounds reader. Returns the file-shape entries."""
    from tagteam import db
    if conn is FILES_ONLY:
        return []
    owned = conn is None
    try:
        if owned:
            conn = db.connect(project_dir=project_dir)
        rounds = db.get_rounds(conn, phase, cycle_type)
        out: list[dict] = []
        for r in rounds:
            entry = {
                "round": r["round"],
                "role": r["role"],
                "action": r["action"],
                "content": r.get("content") or "",
                "ts": r["ts"],
            }
            if r.get("updated_by") is not None:
                entry["updated_by"] = r["updated_by"]
            if r.get("summary") is not None:
                entry["summary"] = r["summary"]
            out.append(entry)
        return out
    except Exception:
        return []
    finally:
        if owned and conn is not None:
            try: conn.close()
            except Exception: pass


def read_status(phase: str, cycle_type: str, project_dir: str = ".", *,
                conn=None) -> dict | None:
    """Read status for a cycle. Returns None if not found.

    Phase 28 Stage 2 contract: DB-first when the DB is valid; falls
    back to legacy file source (docs/handoffs/ or .tagteam/legacy/)
    when the DB doesn't have the cycle. When `dualwrite.is_db_invalid`
    is set, file source is canonical — if no legacy file exists,
    raises `CycleReadError` with an operator recovery hint rather than
    silently returning DB content that may be stale.

    Phase 55: `conn` — an open connection to read the DB through (not
    closed here), or `FILES_ONLY` to skip the DB. Default opens `db.connect`.
    """
    from tagteam import dualwrite
    project_dir = _resolve(project_dir)

    if dualwrite.is_db_invalid(project_dir):
        legacy = _legacy_status_path(phase, cycle_type, project_dir)
        if legacy is not None:
            return _read_status_from_file(legacy)
        # Sentinel set + no file source → operator must repair.
        # For not-found cases (cycle never existed), prefer None so
        # callers like `cycle status` can print "no cycle found" rather
        # than crash. Only raise if other cycles exist on disk; that
        # signals "this project has data, just not for this cycle name."
        if _any_legacy_cycle_files(project_dir):
            return None
        raise CycleReadError(
            "DB_INVALID and no legacy cycle files found. "
            "Run `tagteam state repair-db` to recover."
        )

    db_status = _read_status_from_db(phase, cycle_type, project_dir, conn)
    if db_status is not None:
        return db_status
    legacy = _legacy_status_path(phase, cycle_type, project_dir)
    if legacy is not None:
        return _read_status_from_file(legacy)
    return None


def read_rounds(phase: str, cycle_type: str, project_dir: str = ".", *,
                conn=None) -> list[dict]:
    """Read all round entries for a cycle. Returns [] if not found.

    Same DB-first / file-fallback / db_invalid contract (and `conn`) as
    `read_status`.
    """
    from tagteam import dualwrite
    project_dir = _resolve(project_dir)

    if dualwrite.is_db_invalid(project_dir):
        legacy = _legacy_rounds_path(phase, cycle_type, project_dir)
        if legacy is not None:
            return _read_rounds_from_file(legacy)
        if _any_legacy_cycle_files(project_dir):
            return []
        raise CycleReadError(
            "DB_INVALID and no legacy cycle files found. "
            "Run `tagteam state repair-db` to recover."
        )

    db_rounds = _read_rounds_from_db(phase, cycle_type, project_dir, conn)
    if db_rounds:
        return db_rounds
    # DB returned empty — could be no cycle, or a cycle with no rounds.
    # Disambiguate by checking if status exists (in DB or legacy).
    if _read_status_from_db(phase, cycle_type, project_dir, conn) is not None:
        return []  # cycle exists in DB but has no rounds
    legacy = _legacy_rounds_path(phase, cycle_type, project_dir)
    if legacy is not None:
        return _read_rounds_from_file(legacy)
    return []


def _any_legacy_cycle_files(project_dir: str) -> bool:
    """True if any cycle file exists in docs/handoffs/ or .tagteam/legacy/."""
    pdir = Path(_resolve(project_dir))
    for d in (pdir / "docs" / "handoffs", pdir / ".tagteam" / "legacy"):
        if d.is_dir():
            for p in d.iterdir():
                name = p.name
                if name.endswith("_rounds.jsonl") or name.endswith("_status.json"):
                    return True
    return False


def render_cycle_from_files(phase: str, cycle_type: str,
                           project_dir: str = ".") -> str | None:
    """File-side renderer. Reads ONLY from legacy JSONL/JSON files
    (docs/handoffs/ or .tagteam/legacy/), never from the DB.

    Used by divergence and repair parity checks that compare file-side
    output to DB-side output. Keeping these comparisons file-vs-DB is
    the load-bearing parity contract from Phase 28 Step A.
    """
    project_dir = _resolve(project_dir)
    status_path = _legacy_status_path(phase, cycle_type, project_dir)
    if status_path is None:
        return None
    status = _read_status_from_file(status_path)
    if status is None:
        return None
    rounds_path = _legacy_rounds_path(phase, cycle_type, project_dir)
    entries = _read_rounds_from_file(rounds_path) if rounds_path else []
    return _format_cycle_md(phase, cycle_type, status, entries)


def render_cycle(phase: str, cycle_type: str, project_dir: str = ".") -> str | None:
    """Synthesize human-readable markdown for a cycle.

    DB-backed when the DB is valid (delegates to `db.render_cycle`,
    which produces byte-identical output to the file-side renderer per
    the Phase 28 parity contract). Falls back to `render_cycle_from_files`
    when DB_INVALID is set.

    Returns None if the cycle doesn't exist on either side.
    """
    from tagteam import db, dualwrite
    project_dir = _resolve(project_dir)

    if dualwrite.is_db_invalid(project_dir):
        return render_cycle_from_files(phase, cycle_type, project_dir)

    conn = None
    try:
        conn = db.connect(project_dir=project_dir)
        md = db.render_cycle(conn, phase, cycle_type)
        if md is not None:
            return md
    except Exception:
        pass
    finally:
        if conn is not None:
            try: conn.close()
            except Exception: pass

    return render_cycle_from_files(phase, cycle_type, project_dir)


def _format_cycle_md(phase: str, cycle_type: str, status: dict,
                     entries: list[dict]) -> str:
    """Format the cycle markdown from a status dict + round entries.
    Used by `render_cycle_from_files`. Same shape as the historical
    pre-Stage-2 `render_cycle` output."""

    step_label = "Plan" if cycle_type == "plan" else "Implementation"
    lines = [
        f"# {step_label} Review Cycle: {phase}",
        "",
        f"- **Phase:** {phase}",
        f"- **Type:** {cycle_type}",
        f"- **Date:** {status.get('date', '?')}",
        f"- **Lead:** {status.get('lead', '?')}",
        f"- **Reviewer:** {status.get('reviewer', '?')}",
        "",
    ]

    # Group entries by round
    rounds: dict[int, list[dict]] = {}
    for e in entries:
        r = e.get("round", 0)
        rounds.setdefault(r, []).append(e)

    for round_num in sorted(rounds.keys()):
        lines.append(f"## Round {round_num}")
        lines.append("")
        for e in rounds[round_num]:
            role_label = {"lead": "Lead", "gatekeeper": "Gatekeeper"}.get(e["role"], "Reviewer")
            lines.append(f"### {role_label}")
            lines.append("")
            lines.append(f"**Action:** {e.get('action', '?')}")
            lines.append("")
            lines.append(e.get("content", ""))
            lines.append("")

    # Status footer
    lines.append("---")
    lines.append("")
    lines.append("<!-- CYCLE_STATUS -->")
    lines.append(f"READY_FOR: {status.get('ready_for', '?')}")
    lines.append(f"ROUND: {status.get('round', '?')}")
    lines.append(f"STATE: {status.get('state', '?')}")

    return "\n".join(lines)


def list_cycles(project_dir: str = ".") -> list[dict]:
    """List all cycles, de-duplicating JSONL and legacy .md formats.

    JSONL takes precedence when both formats exist for the same cycle.
    Returns list of {id, format, phase, type} dicts.

    Scans both `docs/handoffs/` (active cycle source) AND
    `.tagteam/legacy/` (post-Step-B-activation source location).
    Without the legacy scan, migrated cycles disappear from web/TUI
    discovery once `migrate --to-step-b` moves their files.
    """
    project_dir = _resolve(project_dir)
    handoffs = _handoffs_dir(project_dir)
    legacy = Path(project_dir) / ".tagteam" / "legacy"

    cycles: dict[str, dict] = {}

    # Scan JSONL status.json files in both locations. docs/handoffs/
    # takes precedence over .tagteam/legacy/ (active source wins on
    # rerun-after-edit, mirrors `_step_b_source_files` priority).
    for d in (legacy, handoffs):
        if not d.is_dir():
            continue
        for f in d.iterdir():
            m = re.match(r"^(.+)_(plan|impl)_status\.json$", f.name)
            if m:
                phase, cycle_type = m.group(1), m.group(2)
                cycle_id = f"{phase}_{cycle_type}"
                cycles[cycle_id] = {
                    "id": cycle_id,
                    "format": "jsonl",
                    "phase": phase,
                    "type": cycle_type,
                }

    # Scan for legacy markdown cycles in docs/handoffs/ only — these
    # are pre-Phase-12 free-form _cycle.md files, never migrated.
    if handoffs.is_dir():
        for f in handoffs.iterdir():
            m = re.match(r"^(.+)_(plan|impl)_cycle\.md$", f.name)
            if m:
                phase, cycle_type = m.group(1), m.group(2)
                cycle_id = f"{phase}_{cycle_type}"
                if cycle_id not in cycles:  # JSONL takes precedence
                    cycles[cycle_id] = {
                        "id": cycle_id,
                        "format": "markdown",
                        "phase": phase,
                        "type": cycle_type,
                    }

    return sorted(cycles.values(), key=lambda c: c["id"])


# --- CLI ---

def cycle_command(args: list[str]) -> int:
    """Handle `python -m tagteam cycle <subcommand>`."""
    if not args:
        print("Usage: python -m tagteam cycle <init|add|status|rounds|render|scope-diff>")
        return 1

    from tagteam.state import _resolve_project_root
    print(f"[tagteam] project root: {_resolve_project_root()}", file=sys.stderr)

    subcmd = args[0]
    if subcmd == "init":
        return _cli_init(args[1:])
    elif subcmd == "add":
        return _cli_add(args[1:])
    elif subcmd == "status":
        return _cli_status(args[1:])
    elif subcmd == "rounds":
        return _cli_rounds(args[1:])
    elif subcmd == "render":
        return _cli_render(args[1:])
    elif subcmd == "scope-diff":
        return _cli_scope_diff(args[1:])
    else:
        print(f"Unknown cycle subcommand: {subcmd}")
        return 1


def _parse_args(args: list[str], allowed: set[str]) -> dict[str, str]:
    """Parse --key value pairs from args."""
    result = {}
    i = 0
    while i < len(args):
        key = args[i]
        if key.startswith("--") and key in allowed:
            if i + 1 >= len(args):
                print(f"Missing value for {key}")
                sys.exit(1)
            result[key] = args[i + 1]
            i += 2
        else:
            print(f"Unknown flag: {key}")
            sys.exit(1)
    return result


def _read_content(parsed: dict[str, str]) -> str:
    """Get content from --content flag or stdin."""
    if "--content" in parsed:
        return parsed["--content"]
    # Read from stdin
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    print("Error: --content required (or pipe via stdin)")
    sys.exit(1)


def _cli_init(args: list[str]) -> int:
    """Start a new cycle.

    Required:
      --phase     The phase slug (e.g. `feature-x`)
      --content   The lead's initial submission text (or pipe via
                  stdin)

    Optional (defaults shown):
      --type        plan       (or `impl`)
      --lead        from tagteam.yaml `agents.lead.name`
      --reviewer    from tagteam.yaml `agents.reviewer.name`
      --updated-by  same as --lead

    Skill callers (agents) typically pass everything explicitly;
    humans driving the CLI can pass just `--phase` and `--content`.
    """
    args, no_gate = _strip_flag(args, "--no-gate")
    allowed = {"--phase", "--type", "--lead", "--reviewer",
               "--content", "--updated-by"}
    parsed = _parse_args(args, allowed)

    phase = parsed.get("--phase")
    if not phase:
        print("Required: --phase")
        return 1

    # Type defaults to plan (the universally-first kind of cycle).
    cycle_type = parsed.get("--type", "plan")
    if cycle_type not in VALID_TYPES:
        print(f"Invalid type: {cycle_type}. Must be 'plan' or 'impl'.")
        return 1

    # Lead/reviewer default to tagteam.yaml values. Only fall through
    # to the "required" error if neither flag nor config provides a
    # name — which usually means an unconfigured project, where the
    # right action is `tagteam init` first.
    lead = parsed.get("--lead")
    reviewer = parsed.get("--reviewer")
    if not lead or not reviewer:
        from tagteam.config import read_config, get_agent_names
        from tagteam.state import _resolve_project_root
        cfg = read_config(Path(_resolve_project_root()) / "tagteam.yaml")
        if cfg is not None:
            cfg_lead, cfg_reviewer = get_agent_names(cfg)
            if not lead:
                lead = cfg_lead
            if not reviewer:
                reviewer = cfg_reviewer

    if not lead or not reviewer:
        missing = []
        if not lead:
            missing.append("--lead")
        if not reviewer:
            missing.append("--reviewer")
        print(
            f"Required: {', '.join(missing)} "
            "(no value found in tagteam.yaml — run `tagteam init` first, "
            "or pass the flag explicitly)."
        )
        return 1

    updated_by = parsed.get("--updated-by")
    # init_cycle defaults updated_by to lead when None, so we can leave
    # it unset here and let the underlying function handle it.

    content = _read_content(parsed)
    init_cycle(phase, cycle_type, lead, reviewer, content,
               updated_by=updated_by)
    print(f"Cycle created: {phase}_{cycle_type} (round 1, ready_for: reviewer)"
          " + state updated")
    _print_pause_notice(reviewer)
    _capture_snapshot(phase, cycle_type, 1, "SUBMIT_FOR_REVIEW")
    if not no_gate:
        _on_submit_gate(phase, cycle_type, reviewer)
    return 0


def _print_pause_notice(next_agent: str | None = None) -> None:
    """After a write that hands the turn over: if watcher dispatch is paused
    (`.tagteam/headless-paused.json`), say so — the watcher's own log is the
    only other place the pause is visible and the writer does not read it."""
    try:
        from tagteam.headless import handoff_pause_notice
        from tagteam.state import _resolve_project_root
        note = handoff_pause_notice(_resolve_project_root(), next_agent)
    except Exception:
        note = None
    if note:
        print(note)


def _dispatch_line() -> str:
    """`cycle status` / `state` field: is watcher dispatch held by a pause marker?"""
    try:
        from tagteam.headless import read_pause, describe_pause
        from tagteam.state import _resolve_project_root
        info = read_pause(_resolve_project_root())
    except Exception:
        return "unknown"
    if info is None:
        return "not paused"
    return describe_pause(info) + " — tagteam resume to release"


def _agent_name_for(role: str | None) -> str | None:
    if role not in ("lead", "reviewer"):
        return None
    try:
        from tagteam.config import read_config, get_agent_names
        from tagteam.state import _resolve_project_root
        cfg = read_config(Path(_resolve_project_root()) / "tagteam.yaml")
        if cfg is None:
            return role
        lead, reviewer = get_agent_names(cfg)
        return lead if role == "lead" else reviewer
    except Exception:
        return role


def _strip_flag(args: list[str], flag: str) -> tuple[list[str], bool]:
    """Remove a valueless flag from args; return (rest, present)."""
    if flag not in args:
        return args, False
    return [a for a in args if a != flag], True


def _capture_snapshot(phase: str, cycle_type: str, round_num: int, action: str) -> None:
    """Phase 56: pin the working tree of the lead entry just written
    (best effort; a failure prints one note and changes nothing else)."""
    try:
        from tagteam.snapshots import capture_after_write
        capture_after_write(phase, cycle_type, round_num, action)
    except Exception:
        pass


def _on_submit_gate(phase: str, cycle_type: str, reviewer: str | None = None) -> None:
    """Phase 41: run the on-submit gate for a lead submission just written
    (no-op unless `gatekeeper.on_submit` is on and the gate applies)."""
    from tagteam.gatekeeper import on_submit_gate
    from tagteam.state import _resolve_project_root
    root = _resolve_project_root()
    if reviewer is None:
        try:
            from tagteam.config import read_config, get_agent_names
            cfg = read_config(Path(root) / "tagteam.yaml")
            reviewer = get_agent_names(cfg)[1] if cfg else None
        except Exception:
            reviewer = None
    on_submit_gate(root, phase, cycle_type, reviewer=reviewer)


def _cli_add(args: list[str]) -> int:
    allowed = {"--phase", "--type", "--role", "--action", "--round", "--content", "--updated-by"}
    args, no_gate = _strip_flag(args, "--no-gate")
    parsed = _parse_args(args, allowed)

    phase = parsed.get("--phase")
    cycle_type = parsed.get("--type")
    role = parsed.get("--role")
    action = parsed.get("--action")
    round_str = parsed.get("--round")

    if not all([phase, cycle_type, role, action, round_str]):
        print("Required: --phase, --type, --role, --action, --round")
        return 1
    if cycle_type not in VALID_TYPES:
        print(f"Invalid type: {cycle_type}. Must be 'plan' or 'impl'.")
        return 1
    if role not in VALID_ROLES:
        print(f"Invalid role: {role}. Must be 'lead' or 'reviewer'.")
        return 1
    if action not in VALID_ACTIONS:
        print(f"Invalid action: {action}. Must be one of: {', '.join(sorted(VALID_ACTIONS))}")
        return 1
    try:
        round_num = int(round_str)
    except ValueError:
        print(f"Round must be an integer, got: {round_str}")
        return 1

    updated_by = parsed.get("--updated-by")
    content = _read_content(parsed)
    try:
        status = add_round(phase, cycle_type, role, action, round_num, content,
                           updated_by=updated_by)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Round added: {phase}_{cycle_type} round={status['round']} "
          f"state={status['state']} ready_for={status.get('ready_for')}"
          " + state updated")
    if status.get("ready_for"):
        _print_pause_notice(_agent_name_for(status.get("ready_for")))
    if role == "lead" and action in ("SUBMIT_FOR_REVIEW", "AMEND"):
        _capture_snapshot(phase, cycle_type, round_num, action)
    if role == "lead" and action == "SUBMIT_FOR_REVIEW" and not no_gate:
        _on_submit_gate(phase, cycle_type)
    return 0


def _cli_status(args: list[str]) -> int:
    allowed = {"--phase", "--type"}
    parsed = _parse_args(args, allowed)

    phase = parsed.get("--phase")
    cycle_type = parsed.get("--type")
    if not phase or not cycle_type:
        print("Required: --phase, --type")
        return 1

    status = read_status(phase, cycle_type)
    if status is not None:
        for k, v in status.items():
            print(f"{k}: {v}")
        print(f"dispatch: {_dispatch_line()}")
        return 0

    # Fall back to legacy markdown — extract status from CYCLE_STATUS block
    md_path = _handoffs_dir(".") / f"{phase}_{cycle_type}_cycle.md"
    if md_path.exists():
        import re as _re
        content = md_path.read_text(encoding="utf-8")
        state_m = _re.search(r"STATE:\s*(\S+)", content)
        ready_m = _re.search(r"READY_FOR:\s*(\S+)", content)
        round_m = _re.search(r"ROUND:\s*(\S+)", content)
        print(f"state: {state_m.group(1) if state_m else '?'}")
        print(f"ready_for: {ready_m.group(1) if ready_m else '?'}")
        print(f"round: {round_m.group(1) if round_m else '?'}")
        print(f"format: markdown (legacy)")
        print(f"dispatch: {_dispatch_line()}")
        return 0

    print(f"No cycle found: {phase}_{cycle_type}")
    return 1


def tail_rounds(phase: str, cycle_type: str, n: int | None = None,
                project_dir: str = ".") -> list[dict]:
    """Return the entries `cycle rounds` would print, optionally the last N.

    Same source order as the CLI: the merged per-round view from
    `tagteam.parser.read_cycle_rounds` when available, else the raw
    JSONL/DB entries from `read_rounds`. `n=None` returns everything;
    `n` larger than the list returns the whole list. Used by the headless
    orchestrator (Phase 31) to compose a bounded turn context.
    """
    if n is not None and n < 1:
        raise ValueError("--tail must be >= 1")
    from tagteam.parser import read_cycle_rounds
    entries = read_cycle_rounds(phase, cycle_type, project_dir=project_dir)
    if not entries:
        entries = read_rounds(phase, cycle_type, project_dir)
    if not entries:
        return []
    entries = list(entries)
    _attach_interjections(entries, phase, cycle_type, project_dir)
    return entries[-n:] if n is not None else entries


def _attach_interjections(entries: list[dict], phase: str, cycle_type: str,
                          project_dir: str) -> None:
    """Phase 32: add an additive `interjections` list to each round dict
    (arbiter notes written while that round was current). Best-effort —
    never raises; entries without a DB get empty lists."""
    for e in entries:
        e.setdefault("interjections", [])
        e.setdefault("entries", [])
        e.setdefault("rulings", [])
    try:
        from tagteam import db
        conn = db.connect(project_dir=_resolve(project_dir))
        try:
            rows = db.get_interjections(conn, phase=phase, cycle_type=cycle_type)
        finally:
            conn.close()
    except Exception:
        return
    if not rows:
        return
    by_round: dict[int, list[dict]] = {}
    for r in rows:
        by_round.setdefault(int(r.get("round") or 0), []).append({
            "id": r["id"], "ts": r["ts"], "by": r["by"], "note": r["note"],
            "target_role": r["target_role"], "delivered_role": r["delivered_role"],
            "delivered_round": r["delivered_round"], "delivered_stem": r["delivered_stem"],
            "retired_ts": r["retired_ts"],
        })
    for e in entries:
        try:
            rn = int(e.get("round") or 0)
        except (TypeError, ValueError):
            continue
        if rn in by_round:
            e["interjections"] = by_round[rn]


def _cli_rounds(args: list[str]) -> int:
    allowed = {"--phase", "--type", "--tail"}
    parsed = _parse_args(args, allowed)

    phase = parsed.get("--phase")
    cycle_type = parsed.get("--type")
    if not phase or not cycle_type:
        print("Required: --phase, --type")
        return 1

    tail_n: int | None = None
    if "--tail" in parsed:
        try:
            tail_n = int(parsed["--tail"])
        except ValueError:
            print(f"--tail must be an integer >= 1, got: {parsed['--tail']}")
            return 1
        if tail_n < 1:
            print(f"--tail must be an integer >= 1, got: {tail_n}")
            return 1

    entries = tail_rounds(phase, cycle_type, tail_n)
    _print_orders_block()
    if entries:
        for e in entries:
            print(json.dumps(e))
        return 0

    print(f"No rounds found for: {phase}_{cycle_type}")
    return 1


def _print_orders_block() -> None:
    """Phase 70: the STANDING ORDERS block on stderr, so an interactive agent
    reading `cycle rounds` sees it and stdout stays JSON lines. Silent
    without explicit orders; never fails the read."""
    try:
        from tagteam import orders as _orders
        from tagteam.state import read_state
        root = _resolve(".")
        block = _orders.render_block(_orders.effective(read_state(root) or {}, root))
    except Exception:
        return
    if block:
        sys.stderr.write(block)


def _with_interjections(md: str, phase: str, cycle_type: str,
                        project_dir: str = ".") -> str:
    """Phase 32: insert an 'Arbiter interjections' line under each
    `## Round N` heading that has notes. No-op when there are none (keeps
    the parity corpus byte-identical)."""
    try:
        from tagteam import db
        conn = db.connect(project_dir=_resolve(project_dir))
        try:
            rows = db.get_interjections(conn, phase=phase, cycle_type=cycle_type)
        finally:
            conn.close()
    except Exception:
        return md
    if not rows:
        return md
    by_round: dict[int, list[dict]] = {}
    for r in rows:
        by_round.setdefault(int(r.get("round") or 0), []).append(r)
    out_lines = []
    for line in md.splitlines():
        out_lines.append(line)
        if line.startswith("## Round "):
            try:
                rn = int(line[len("## Round "):].strip())
            except ValueError:
                continue
            for r in by_round.get(rn, []):
                status = ("retired" if r["retired_ts"] else
                          f"delivered → {r['delivered_role']} r{r['delivered_round']}"
                          if r["delivered_ts"] else "pending")
                out_lines.append("")
                out_lines.append(f"**Arbiter interjection #{r['id']}** ({r['by']}, "
                                 f"→ {r['target_role'] or 'next turn'}, {status}): {r['note']}")
    return "\n".join(out_lines)


def _cli_render(args: list[str]) -> int:
    allowed = {"--phase", "--type"}
    parsed = _parse_args(args, allowed)

    phase = parsed.get("--phase")
    cycle_type = parsed.get("--type")
    if not phase or not cycle_type:
        print("Required: --phase, --type")
        return 1

    # Try JSONL render first
    md = render_cycle(phase, cycle_type)
    if md is not None:
        print(_with_interjections(md, phase, cycle_type))
        return 0

    # Fall back to legacy markdown — just cat the file
    md_path = _handoffs_dir(".") / f"{phase}_{cycle_type}_cycle.md"
    if md_path.exists():
        print(md_path.read_text(encoding="utf-8"))
        return 0

    print(f"No cycle found: {phase}_{cycle_type}")
    return 1


class ScopeDiffError(Exception):
    """Raised by `compute_scope_diff` with the exact message the CLI prints."""


def compute_scope_diff(phase: str, cycle_type: str, project_dir: str = ".") -> dict:
    """Programmatic scope-diff (Phase 34): the paths attributable to this
    phase, filtering out pre-existing drift.

    Reads the cycle's `baseline` block (captured at plan-init, copied into
    impl on init), then computes:
      committed_since_baseline ∪ (current_dirty − baseline_dirty)

    Committed paths always surface, even if dirty at baseline (they are
    provably phase work). Uncommitted paths surface only if they were not
    already dirty at baseline.

    When baseline.sha is null (plan-init in a no-commit repo), the
    committed-side comparison is against git's empty-tree object.

    Returns {"paths": [...sorted], "baseline": {...}, "diff_base": sha,
    "head_resolves": bool, "committed": [...], "uncommitted": [...]}.
    Raises `ScopeDiffError(message)` for the same conditions the CLI
    reports (message text identical to `tagteam cycle scope-diff`).
    """
    project_dir = _resolve(project_dir)
    sp = _status_path(phase, cycle_type, project_dir)
    if not sp.exists():
        raise ScopeDiffError(f"No cycle found: {phase}_{cycle_type}")

    try:
        status = json.loads(sp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise ScopeDiffError(f"Failed to read cycle status: {e}")

    if "baseline" not in status or status["baseline"] is None:
        raise ScopeDiffError(
            "Cycle has no baseline (created before phase: "
            "cycle-baseline-snapshot). Cannot compute scope-diff."
        )

    baseline = status["baseline"]
    baseline_sha = baseline.get("sha")
    baseline_dirty_lines = baseline.get("dirty_paths") or []
    baseline_dirty = {_porcelain_path(line) for line in baseline_dirty_lines}

    # Determine if HEAD resolves now.
    head_rc, _ = _git(project_dir, "rev-parse", "--verify", "HEAD")
    head_resolves = (head_rc == 0)

    diff_base = baseline_sha if baseline_sha else _GIT_EMPTY_TREE
    committed: set[str] = set()
    if head_resolves:
        rc, out = _git(project_dir, "diff", "--name-only", diff_base, "HEAD")
        if rc == 0:
            committed = {p for p in out.splitlines() if p}

    rc, porc_out = _git(project_dir, "status", "--porcelain")
    current_dirty: set[str] = set()
    if rc == 0:
        current_dirty = {
            _porcelain_path(line) for line in porc_out.splitlines() if line
        }

    attributable_uncommitted = current_dirty - baseline_dirty
    attributable = (committed | attributable_uncommitted)
    # Strip tagteam's own bookkeeping artifacts; they are review-system
    # output, not phase work.
    paths = sorted(p for p in attributable if not _is_tagteam_artifact(p))
    return {
        "paths": paths,
        "baseline": baseline,
        "diff_base": diff_base,
        "head_resolves": head_resolves,
        "committed": sorted(p for p in committed if not _is_tagteam_artifact(p)),
        "uncommitted": sorted(p for p in attributable_uncommitted
                              if not _is_tagteam_artifact(p)),
    }


class ImplWorkUnavailable(Exception):
    """The implementation-work check cannot run (not git / no boundary) —
    a `skip`, never a `fail`."""


def compute_impl_work(phase: str, project_dir: str = ".") -> dict:
    """Phase 38: implementation work since the implementation boundary —
    one rule per path, whether now committed or dirty:
      (a) every path in boundary.dirty: current content (working tree if
          present, else HEAD blob; deletion = null) hashed and compared to
          the captured hash — only a changed hash counts, regardless of
          whether the path has since been committed;
      (b) every path committed after boundary.sha that is NOT in
          boundary.dirty counts;
      (c) every currently dirty/untracked path NOT in boundary.dirty counts;
    minus tagteam artifacts and this phase's plan artifacts
    (docs/roadmap.md, docs/phases/<phase>.md). No HEAD → (b) is empty and
    the snapshot still decides. Raises ImplWorkUnavailable when there is
    no boundary or no git repo."""
    project_dir = _resolve(project_dir)
    boundary = read_impl_boundary(phase, "impl", project_dir)
    if boundary is None:
        # impl not opened yet (gate check pre-flight) → the plan's boundary
        boundary = read_impl_boundary(phase, "plan", project_dir)
    if not isinstance(boundary, dict):
        raise ImplWorkUnavailable("no implementation boundary recorded (plan approved before 3.2.0, "
                                  "or a legacy cycle) — scope not enforced")
    rc, _ = _git(project_dir, "rev-parse", "--git-dir")
    if rc != 0:
        raise ImplWorkUnavailable("not a git repository — scope not enforced")
    plan_artifacts = {"docs/roadmap.md", f"docs/phases/{phase}.md"}

    def excluded(p: str) -> bool:
        return _is_tagteam_artifact(p) or p.startswith(".tagteam/") or p in plan_artifacts

    b_dirty: dict = boundary.get("dirty") or {}
    b_sha = boundary.get("sha")
    changed: set[str] = set()
    detail: dict[str, str] = {}
    # (a) boundary-dirty paths by content hash
    for path, old_hash in b_dirty.items():
        cur = _blob_sha256(project_dir, path)
        if cur is None:
            # not in the working tree: deleted, or committed and clean → HEAD blob
            rc, out = _git(project_dir, "show", f"HEAD:{path}")
            if rc == 0:
                import hashlib
                cur = hashlib.sha256(out.encode("utf-8", "surrogateescape")).hexdigest()
        if cur != old_hash:
            changed.add(path); detail[path] = "boundary-dirty content changed"
    # (b) committed after the boundary
    head_rc, _ = _git(project_dir, "rev-parse", "--verify", "HEAD")
    if head_rc == 0 and b_sha:
        rc, out = _git(project_dir, "diff", "--name-only", b_sha, "HEAD")
        if rc == 0:
            for path in out.splitlines():
                if path and path not in b_dirty:
                    changed.add(path); detail.setdefault(path, "committed since boundary")
    # (c) currently dirty/untracked, not in the boundary snapshot
    rc, porc = _git(project_dir, "status", "--porcelain", "--untracked-files=all")
    if rc == 0:
        for line in porc.splitlines():
            if not line:
                continue
            path = _porcelain_path(line)
            if path not in b_dirty:
                changed.add(path); detail.setdefault(path, "dirty since boundary")
    paths = sorted(p for p in changed if not excluded(p))
    return {"paths": paths, "detail": {p: detail[p] for p in paths}, "boundary": boundary,
            "excluded": sorted(p for p in changed if excluded(p))}


def ensure_gate_applied(phase: str, cycle_type: str, decision: dict, project_dir: str = ".") -> dict:
    """Phase 38: idempotently apply a gate decision across the stores.

    `decision` = {"action": GATE_PASS|GATE_BOUNCE, "content", "round",
    "gate_event", "gate_id", "gate_attempt", "submission_seq"}.
    Under the writer lock: (1) if a round entry with this `gate_event`
    exists → never append again; (2) else append the entry (rounds JSONL +
    shadow DB + export); (3) PASS → nothing else (no state derive, seq
    unchanged); BOUNCE → compare-and-apply on the pinned submission:
    original submission still reviewer-ready at exactly `submission_seq` →
    apply the REQUEST_CHANGES-shaped transition once (status → derive →
    seq+1) and return applied_seq; already applied → nothing; state
    advanced for any other reason → `superseded` (no replay, no bump).
    Returns {"entry_appended": bool, "applied": "pass"|"applied"|"already"|
    "superseded", "applied_seq": int|None, "seq": int}."""
    from tagteam import dualwrite
    from tagteam.state import read_state
    project_dir = _resolve(project_dir)
    action = decision["action"]
    if action not in GATE_ACTIONS:
        raise ValueError(f"not a gate action: {action!r}")
    event = decision["gate_event"]
    with dualwrite.writer_lock(project_dir):
        rp = _rounds_path(phase, cycle_type, project_dir)
        rounds = _read_rounds_from_file(rp) if rp.exists() else []
        existing = next((e for e in rounds if e.get("gate_event") == event), None)
        entry_appended = False
        entry = existing
        st = read_state(project_dir) or {}
        seq = int(st.get("seq") or 0)
        sub_seq = int(decision.get("submission_seq") if decision.get("submission_seq") is not None else -1)
        status = _read_status_from_file(_status_path(phase, cycle_type, project_dir)) or {}
        same_cycle = (st.get("phase") == phase and st.get("type") == cycle_type
                      and int(st.get("round") or 0) == int(decision["round"]))
        still_reviewer_ready = (same_cycle and seq == sub_seq
                                and status.get("state") == "in-progress"
                                and status.get("ready_for") == "reviewer")
        pre_entries = decision.get("pre_entries")
        log_moved = (pre_entries is not None and len(rounds) != int(pre_entries))
        if existing is None and (not still_reviewer_ready or log_moved):
            # A FRESH decision for a submission that has already moved on
            # (lead AMENDed — rounds-only, so the seq is unchanged but the
            # round log grew — or re-submitted, arbiter ruled, reviewer
            # acted): no cycle write at all — the newer submission gets its
            # own gate (a re-run on the same event key, or a new key).
            return {"entry_appended": False, "applied": "superseded", "applied_seq": None, "seq": seq}
        if existing is None:
            entry = {"round": int(decision["round"]), "role": ROLE_GATEKEEPER, "action": action,
                     "content": decision.get("content", ""), "ts": datetime.now(timezone.utc).isoformat(),
                     "updated_by": "Gatekeeper",
                     "gate_event": event, "gate_id": decision.get("gate_id"),
                     "gate_attempt": decision.get("gate_attempt")}
            with open(rp, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            entry_appended = True
            _shadow_db_after_amend(project_dir, phase, cycle_type, entry)   # rounds-only mirror
        if action == GATE_PASS:
            # A RECOVERED / existing PASS (crash right after the JSONL
            # append, before the mirror or the export) must still leave every
            # store consistent: re-mirror the canonical rounds (the shadow
            # write dedupes by round/role/action/ts) and re-export — never a
            # state derive, seq untouched.
            if existing is not None:
                _shadow_db_after_cycle_write(project_dir, phase, cycle_type)
            _auto_export_cycle_md(project_dir, phase, cycle_type)
            return {"entry_appended": entry_appended, "applied": "pass", "applied_seq": None, "seq": seq}
        # BOUNCE: compare-and-apply on (phase, type, round, submission_seq)
        applied_seq = decision.get("applied_seq")
        already = (same_cycle and status.get("state") == "in-progress"
                   and status.get("ready_for") == "lead"
                   and (applied_seq is not None and seq == int(applied_seq)))
        # Partial apply (crash between the cycle-status write and the top-
        # level derive): the entry exists, the cycle status already says
        # lead, the top-level state still says reviewer at exactly the
        # submission seq → finish the derive exactly once.
        partial = (existing is not None and same_cycle and seq == sub_seq
                   and st.get("turn") == "reviewer"
                   and status.get("state") == "in-progress" and status.get("ready_for") == "lead")
        if still_reviewer_ready or partial:
            if not partial:
                status["state"] = "in-progress"
                status["ready_for"] = "lead"
                sp = _status_path(phase, cycle_type, project_dir)
                sp.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
            _derive_top_level_state(phase, cycle_type, project_dir, updated_by="Gatekeeper")
            _shadow_db_after_cycle_write(project_dir, phase, cycle_type)
            _auto_export_cycle_md(project_dir, phase, cycle_type)
            new_seq = int((read_state(project_dir) or {}).get("seq") or 0)
            return {"entry_appended": entry_appended, "applied": "applied", "applied_seq": new_seq, "seq": new_seq}
        if already or (same_cycle and status.get("ready_for") == "lead" and applied_seq is None
                       and seq == sub_seq + 1):
            # fully applied earlier; still make sure the mirror + export
            # carry the entry (crash-after-append recovery)
            _shadow_db_after_cycle_write(project_dir, phase, cycle_type)
            _auto_export_cycle_md(project_dir, phase, cycle_type)
            return {"entry_appended": entry_appended, "applied": "already", "applied_seq": seq, "seq": seq}
        if existing is not None:
            _shadow_db_after_cycle_write(project_dir, phase, cycle_type)
        _auto_export_cycle_md(project_dir, phase, cycle_type)
        return {"entry_appended": entry_appended, "applied": "superseded", "applied_seq": None, "seq": seq}


def _cli_scope_diff(args: list[str]) -> int:
    """Print paths attributable to this phase, filtering out pre-existing drift.

    Thin CLI over `compute_scope_diff` — output is unchanged from before
    the extraction (one path per line; the same error messages).
    """
    allowed = {"--phase", "--type"}
    parsed = _parse_args(args, allowed)
    phase = parsed.get("--phase")
    cycle_type = parsed.get("--type")
    if not phase or not cycle_type:
        print("Required: --phase, --type")
        return 1

    try:
        result = compute_scope_diff(phase, cycle_type, ".")
    except ScopeDiffError as e:
        print(str(e))
        return 1

    for path in result["paths"]:
        print(path)
    return 0


# ----------------------------------------------------------------------

# --- Phase 28 Step B: auto-export hooks ---

def _emit_auto_export_diagnostic(
    project_dir: str,
    kind: str,
    phase: str,
    cycle_type: str,
    *,
    reason: str | None = None,
) -> None:
    """Append a best-effort auto-export diagnostic.

    This side-channel must never break cycle writes.
    """
    try:
        from tagteam.state import DIAGNOSTICS_LOG

        entry = {
            "kind": kind,
            "phase": phase,
            "type": cycle_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if reason:
            entry["reason"] = reason
        log_path = Path(project_dir) / DIAGNOSTICS_LOG
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _auto_export_cycle_md(project_dir: str, phase: str,
                          cycle_type: str) -> None:
    """Render the DB-backed markdown artifact for a cycle when Step B
    auto-export is active.

    Called inside the writer lock after the shadow DB helper returns.
    If the DB-invalid sentinel is set, skip rather than rendering stale
    content over the last-good markdown.
    """
    from tagteam import auto_export, db, dualwrite

    if not dualwrite.step_b_active():
        return

    if dualwrite.is_db_invalid(project_dir):
        _emit_auto_export_diagnostic(
            project_dir, "auto_export_skipped_db_invalid", phase, cycle_type
        )
        return

    conn = None
    try:
        conn = db.connect(project_dir=project_dir)
        ok = auto_export.render_cycle_to_file(
            conn, project_dir, phase, cycle_type
        )
        if not ok:
            _emit_auto_export_diagnostic(
                project_dir,
                "auto_export_failed",
                phase,
                cycle_type,
                reason="render_returned_false",
            )
    except Exception as e:
        _emit_auto_export_diagnostic(
            project_dir,
            "auto_export_failed",
            phase,
            cycle_type,
            reason=f"{type(e).__name__}: {e}",
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# --- Phase 28 Step A: shadow DB write helpers ---

def _shadow_db_after_cycle_write(project_dir: str, phase: str,
                                 cycle_type: str) -> None:
    """Mirror the just-written file state for a cycle to the shadow DB,
    then run a divergence check.

    Called from `init_cycle` and the non-AMEND path of `add_round`,
    inside the writer lock. Reads the canonical file state (status +
    rounds) and re-issues the equivalent DB writes.

    For `init_cycle`, this populates the cycle row + round 1 from
    scratch. For subsequent `add_round` calls, only the new round row
    is added (db.add_round always inserts; existing rounds are
    untouched), and the cycle status fields are refreshed via upsert.

    Retry-on-next-write: if `db_invalid` is set and backoff allows,
    attempt repair before the shadow write. Successful repair
    rebuilds the DB from files (which already include this write),
    so we early-return; the shadow write would otherwise be redundant.

    Failures mark `db_invalid` and are swallowed — files are
    canonical during Step A. The divergence check produces a
    diagnostic row in the DB if files and DB renders disagree.
    """
    from tagteam import db, divergence, dualwrite, repair

    if dualwrite.is_db_invalid(project_dir) and \
            repair.should_attempt_repair(project_dir):
        if repair.attempt_repair(project_dir)["success"]:
            return  # Repair rebuilt from files; nothing more to do.

    conn = None
    db_failed = False
    try:
        conn = db.connect(project_dir=project_dir)
        # Shadow write must read the FRESHLY-WRITTEN files, not the
        # stale DB state. The new Stage 2 read_status/read_rounds are
        # DB-first, so they'd return pre-write content here. Use the
        # file-side helpers directly.
        status = _read_status_from_file(
            _status_path(phase, cycle_type, project_dir)
        ) or {}
        cycle_id = db.upsert_cycle(
            conn, phase, cycle_type,
            lead=status.get("lead"),
            reviewer=status.get("reviewer"),
            state=status.get("state", "in-progress"),
            ready_for=status.get("ready_for"),
            ready_for_present=("ready_for" in status),
            round_=status.get("round", 0),
            date=status.get("date"),
            baseline=status.get("baseline"),
        )

        # Determine which rounds the DB does not yet have, and add
        # only those. Compare by (round, role, action, ts) — the
        # rounds table allows duplicates so we must avoid re-inserting.
        existing_keys = set(
            conn.execute(
                "SELECT round, role, action, ts FROM rounds WHERE cycle_id=?",
                (cycle_id,),
            ).fetchall()
        )
        for r in _read_rounds_from_file(
            _rounds_path(phase, cycle_type, project_dir)
        ):
            key = (r["round"], r["role"], r["action"], r["ts"])
            if key in existing_keys:
                continue
            db.add_round(
                conn, cycle_id,
                round_=r["round"],
                role=r["role"],
                action=r["action"],
                content=r.get("content", ""),
                ts=r["ts"],
                updated_by=r.get("updated_by"),
                summary=r.get("summary"),
            )
        conn.commit()
    except Exception as e:
        db_failed = True
        dualwrite.mark_db_invalid(
            project_dir, reason=f"cycle dual-write failed: {e}"
        )

    if conn is not None and not db_failed:
        try:
            divergence.log_divergence_if_needed(
                conn, project_dir, phase, cycle_type
            )
        except Exception:
            # Divergence logging must never break the caller. The DB
            # being broken is exactly the kind of thing this helper is
            # supposed to surface; masking errors here would defeat
            # the purpose, but the caller's file-side write has
            # already succeeded so we still don't raise.
            pass

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def _shadow_db_after_amend(project_dir: str, phase: str, cycle_type: str,
                           entry: dict) -> None:
    """Mirror an AMEND round to the shadow DB.

    AMEND only appends a round on the file side — no status change,
    no top-level state derive. The DB write follows the same shape:
    `db.add_round` only, no `upsert_cycle` for status fields.

    Retry-on-next-write: if `db_invalid` is set and backoff allows,
    attempt repair first. Successful repair makes the shadow write
    redundant (the rebuild includes our just-appended AMEND).

    Failures mark `db_invalid` and are swallowed.
    """
    from tagteam import db, divergence, dualwrite, repair

    if dualwrite.is_db_invalid(project_dir) and \
            repair.should_attempt_repair(project_dir):
        if repair.attempt_repair(project_dir)["success"]:
            return

    conn = None
    db_failed = False
    try:
        conn = db.connect(project_dir=project_dir)
        cur = conn.execute(
            "SELECT id FROM cycles WHERE phase=? AND type=?",
            (phase, cycle_type),
        )
        row = cur.fetchone()
        if row is None:
            # Cycle missing from DB — shouldn't happen if previous
            # dual-writes succeeded. Fall back to a status-driven
            # upsert so the AMEND has somewhere to attach. Read from
            # file directly (DB is stale by definition here).
            status = _read_status_from_file(
                _status_path(phase, cycle_type, project_dir)
            ) or {}
            cycle_id = db.upsert_cycle(
                conn, phase, cycle_type,
                lead=status.get("lead"),
                reviewer=status.get("reviewer"),
                state=status.get("state", "in-progress"),
                ready_for=status.get("ready_for"),
                ready_for_present=("ready_for" in status),
                round_=status.get("round", 0),
                date=status.get("date"),
                baseline=status.get("baseline"),
            )
        else:
            cycle_id = row[0]

        db.add_round(
            conn, cycle_id,
            round_=entry["round"],
            role=entry["role"],
            action=entry["action"],
            content=entry.get("content", ""),
            ts=entry["ts"],
            updated_by=entry.get("updated_by"),
            summary=entry.get("summary"),
        )
        conn.commit()
    except Exception as e:
        db_failed = True
        dualwrite.mark_db_invalid(
            project_dir, reason=f"amend dual-write failed: {e}"
        )

    if conn is not None and not db_failed:
        try:
            divergence.log_divergence_if_needed(
                conn, project_dir, phase, cycle_type
            )
        except Exception:
            pass

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
