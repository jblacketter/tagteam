"""Phase 56 — submission snapshots.

After every lead submission (`cycle init`, `cycle add … SUBMIT_FOR_REVIEW`)
and every `AMEND`, record the exact working tree at that moment as a commit
pinned under `refs/tagteam/snapshots/<phase>/<type>/r<N>/<entry-ts>`:

    GIT_INDEX_FILE=<tmp> git read-tree HEAD      (skipped without a HEAD)
    GIT_INDEX_FILE=<tmp> git add -A              tracked + untracked, honours .gitignore
    GIT_INDEX_FILE=<tmp> git write-tree
    git commit-tree <tree> [-p HEAD]
    git update-ref <ref> <commit>

The user's index, working tree, stash and branches are never touched. The
snapshot is the tree *at submission*, not proof of what a reviewer saw after
later edits. Identity is the lead entry's `ts`, so a round with a submit and
two AMENDs has three snapshots. Best effort: any failure returns None with a
reason and the caller's write proceeds unchanged. No model, no tokens.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_TIMEOUT_S = 30.0
REF_PREFIX = "refs/tagteam/snapshots"
SNAPSHOT_ACTIONS = ("SUBMIT_FOR_REVIEW", "AMEND")


class SnapshotError(Exception):
    pass


def _ref_part(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(text)).strip("-") or "x"


def snapshot_ref(phase: str, cycle_type: str, round_: int, entry_ts: str) -> str:
    return (f"{REF_PREFIX}/{_ref_part(phase)}/{_ref_part(cycle_type)}/r{int(round_)}/"
            f"{re.sub(r'[^0-9A-Za-z]', '', entry_ts)}")


def _run_git(cwd: str, args: list[str], deadline: float, env: dict | None = None) -> str:
    left = deadline - time.monotonic()
    if left <= 0:
        raise SnapshotError(f"timed out after {SNAPSHOT_TIMEOUT_S:.0f} s")
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                           timeout=left, env=env)
    except subprocess.TimeoutExpired:
        raise SnapshotError(f"timed out after {SNAPSHOT_TIMEOUT_S:.0f} s")
    except (FileNotFoundError, OSError) as e:
        raise SnapshotError(f"git unavailable: {e}")
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip().splitlines()
        raise SnapshotError(f"git {args[0]} failed: {msg[-1] if msg else r.returncode}")
    return r.stdout.strip()


def take_snapshot(project_root: str | Path, message: str, *,
                  timeout_s: float = SNAPSHOT_TIMEOUT_S) -> dict:
    """{commit_sha, tree_sha, head_sha} for the current working tree. Raises
    SnapshotError. Writes only objects (no ref, no index, no worktree change)."""
    cwd = str(project_root)
    deadline = time.monotonic() + timeout_s
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=cwd,
                            capture_output=True, text=True, timeout=timeout_s)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise SnapshotError("not a git repository")
    head = subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD^{commit}"], cwd=cwd,
                          capture_output=True, text=True, timeout=timeout_s)
    head_sha = head.stdout.strip() if head.returncode == 0 and head.stdout.strip() else None
    fd, tmp_index = tempfile.mkstemp(prefix="tagteam-snapshot-", suffix=".index")
    os.close(fd)
    os.unlink(tmp_index)          # git wants to create the index file itself
    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = tmp_index
    try:
        if head_sha:
            _run_git(cwd, ["read-tree", head_sha], deadline, env)
        _run_git(cwd, ["add", "-A"], deadline, env)
        tree_sha = _run_git(cwd, ["write-tree"], deadline, env)
    finally:
        for p in (tmp_index, tmp_index + ".lock"):
            try:
                os.unlink(p)
            except OSError:
                pass
    cenv = dict(os.environ)
    # commit-tree needs an identity; a snapshot must not fail on an unset one.
    for k, v in (("GIT_AUTHOR_NAME", "tagteam"), ("GIT_AUTHOR_EMAIL", "tagteam@localhost"),
                 ("GIT_COMMITTER_NAME", "tagteam"), ("GIT_COMMITTER_EMAIL", "tagteam@localhost")):
        cenv.setdefault(k, v)
    args = ["commit-tree", tree_sha, "-m", message]
    if head_sha:
        args[2:2] = ["-p", head_sha]
    commit_sha = _run_git(cwd, args, deadline, cenv)
    return {"commit_sha": commit_sha, "tree_sha": tree_sha, "head_sha": head_sha}


def _lead_entry_ts(phase: str, cycle_type: str, round_: int, action: str, root: str) -> str | None:
    """`ts` of the lead entry just written (the last lead `action` entry of the round)."""
    from tagteam import cycle as _cycle
    try:
        entries = _cycle.read_rounds(phase, cycle_type, root)
    except Exception:
        entries = []
    for e in reversed(entries or []):
        if (e.get("role") == "lead" and e.get("action") == action
                and int(e.get("round") or -1) == int(round_) and e.get("ts")):
            return e["ts"]
    return None


def _base_sha(phase: str, cycle_type: str, root: str) -> str | None:
    """The cycle baseline sha (what the gate's scope check diffs against)."""
    from tagteam import cycle as _cycle
    try:
        status = _cycle.read_status(phase, cycle_type, root) or {}
    except Exception:
        status = {}
    baseline = status.get("baseline")
    if not isinstance(baseline, dict):
        proot = _cycle._resolve(root)
        for p in (_cycle._status_path(phase, cycle_type, proot),
                  _cycle._legacy_status_path(phase, cycle_type, proot)):
            if p is not None and Path(p).exists():
                baseline = (_cycle._read_status_from_file(Path(p)) or {}).get("baseline")
                if isinstance(baseline, dict):
                    break
    return baseline.get("sha") if isinstance(baseline, dict) else None


def capture_submission_snapshot(project_root: str | Path, phase: str, cycle_type: str,
                                round_: int, action: str, *,
                                entry_ts: str | None = None) -> tuple[dict | None, str]:
    """(row, "ok") or (None, reason). Never raises."""
    root = str(project_root)
    try:
        if action not in SNAPSHOT_ACTIONS:
            return None, f"not a submission action: {action}"
        if entry_ts is None:
            entry_ts = _lead_entry_ts(phase, cycle_type, round_, action, root)
        if not entry_ts:
            return None, "submission entry has no timestamp"
        snap = take_snapshot(root, f"tagteam snapshot {phase}/{cycle_type}/r{round_} {entry_ts}")
        ref = snapshot_ref(phase, cycle_type, round_, entry_ts)
        _run_git(root, ["update-ref", ref, snap["commit_sha"]], time.monotonic() + SNAPSHOT_TIMEOUT_S)
        row = {"phase": phase, "type": cycle_type, "round": int(round_), "entry_ts": entry_ts,
               "action": action, "commit_sha": snap["commit_sha"], "tree_sha": snap["tree_sha"],
               "head_sha": snap["head_sha"],
               "base_sha": _base_sha(phase, cycle_type, root) or snap["head_sha"],
               "ref": ref, "captured_at": datetime.now(timezone.utc).isoformat()}
        from tagteam import db as _db
        conn = _db.connect(project_dir=root)
        try:
            _db.add_submission_snapshot(conn, **row)
        finally:
            conn.close()
        return row, "ok"
    except SnapshotError as e:
        return None, str(e)
    except Exception as e:  # best effort: a snapshot never breaks a submission
        return None, f"{type(e).__name__}: {e}"


def capture_after_write(phase: str, cycle_type: str, round_: int, action: str) -> dict | None:
    """CLI hook: capture for the entry just written; on failure print one note
    and record a diagnostic. Never raises."""
    import sys
    try:
        from tagteam.state import _resolve_project_root
        root = _resolve_project_root()
    except Exception as e:
        print(f"[tagteam] note: submission snapshot not captured ({e})", file=sys.stderr)
        return None
    started = time.monotonic()
    row, reason = capture_submission_snapshot(root, phase, cycle_type, round_, action)
    if row is not None:
        return row
    print(f"[tagteam] note: submission snapshot not captured ({reason})", file=sys.stderr)
    try:
        from tagteam import db as _db
        conn = _db.connect(project_dir=root)
        try:
            _db.add_diagnostic(conn, "snapshot_failed", {
                "phase": phase, "type": cycle_type, "round": round_, "action": action,
                "reason": reason, "elapsed_s": round(time.monotonic() - started, 3)},
                datetime.now(timezone.utc).isoformat())
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
    return None
