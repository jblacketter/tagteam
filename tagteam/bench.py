"""Phase 56 — review bench.

Replay a recorded reviewer round against reviewer *cells* (provider × model ×
effort) and compare each cell's verdict with the recorded one.

    tagteam bench select   benchable rounds: recorded verdict, reviewed version, provenance
    tagteam bench run      dry run by default; --yes spawns one reviewer turn per pair
    tagteam bench table    agreement / missed-RC / extra-RC / severity / tokens / seconds per cell

A pair runs in an **isolated replay repository** (a fresh `git init` in the
system temp directory whose only history is `base` → `submitted`, both built
from `git archive`), never in a worktree of the project, so no later commit
or `refs/tagteam/*` is reachable. The target cycle's outcome is scrubbed from
both trees before they are committed. The child runs with
`TAGTEAM_READ_ONLY=1` and writes one verdict file (the panel's contract,
`panel.verify_verdict`). Bench usage rows carry `kind="bench"` and no
phase/target, so `tagteam report` never counts them as phase work.

Agreement is with the recorded reviewer, not with ground truth.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tagteam import headless as h

VERDICTS = ("APPROVE", "REQUEST_CHANGES", "ESCALATE", "NEED_HUMAN")
LEAD_VERSION_ACTIONS = ("SUBMIT_FOR_REVIEW", "AMEND")
DEFAULT_MAX_TURNS = 12
TEMP_PREFIX = "tagteam-bench-"
OWNER_FILE = "owner.json"
CONTRACT_PATH = Path(__file__).parent / "data" / "bench" / "contract.md"
NAME_STATUS_MAX_LINES = 400
NOT_GROUND_TRUTH = "agreement is with the recorded reviewer, not with ground truth"

USAGE = """usage:
  tagteam bench select [--phase P] [--type plan|impl] [--verdict V] [--provenance snapshot|any]
                       [--limit N] [--json]
  tagteam bench run    --round P:T:N[@[BASE..]REV] ... --cell claude:<model>:<effort> ...
                       [--max-turns N] [--timeout-minutes M] [--keep] [--yes]
  tagteam bench table  [--run RUN_ID] [--json]

run is a dry run unless --yes: it prints the round x cell grid and a proxy token
estimate and spawns nothing. Each pair costs about one reviewer turn of window."""


class BenchError(ValueError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# cells

@dataclass
class Cell:
    provider: str
    model: str
    effort: str

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}:{self.effort}"

    @property
    def user_args(self) -> list[str]:
        return ["--model", self.model, "--effort", self.effort]


def parse_cell(spec: str) -> Cell:
    parts = spec.split(":")
    if len(parts) != 3 or not all(p.strip() for p in parts):
        raise BenchError(f"cell {spec!r} must be provider:model:effort (e.g. claude:sonnet:high)")
    provider, model, effort = (p.strip() for p in parts)
    if provider == "codex":
        raise BenchError(f"cell {spec!r}: codex cells are not supported yet")
    if provider != "claude":
        raise BenchError(f"cell {spec!r}: unknown provider {provider!r} (supported: claude)")
    cell = Cell(provider, model, effort)
    try:
        h.validate_user_args(h.CLAUDE, cell.user_args)
    except h.HeadlessConfigError as e:
        raise BenchError(f"cell {spec!r}: {e}")
    return cell


# ---------------------------------------------------------------------------
# benchable rounds

@dataclass
class BenchRound:
    phase: str
    type: str
    round: int
    recorded_verdict: str
    verdict_index: int
    version_index: int
    version_ts: str | None
    amended: int
    entries: list = field(repr=False, default_factory=list)   # the whole cycle log

    @property
    def key(self) -> str:
        return f"{self.phase}:{self.type}:{self.round}"

    @property
    def pre_verdict(self) -> list[dict]:
        return self.entries[:self.verdict_index]


def _cycle_ids(root: str, conn=None) -> list[tuple[str, str]]:
    ids: set[tuple[str, str]] = set()
    for d in (Path(root) / "docs" / "handoffs", Path(root) / ".tagteam" / "legacy"):
        if d.is_dir():
            for p in d.glob("*_rounds.jsonl"):
                m = re.match(r"^(.+)_(plan|impl)_rounds\.jsonl$", p.name)
                if m:
                    ids.add((m.group(1), m.group(2)))
    if conn is not None:
        try:
            for phase, ctype in conn.execute("SELECT phase, type FROM cycles"):
                if ctype in ("plan", "impl"):
                    ids.add((phase, ctype))
        except Exception:
            pass
    return sorted(ids)


def _cycle_entries(root: str, phase: str, cycle_type: str, conn=None) -> list[dict]:
    from tagteam import cycle as _cycle
    try:
        entries = _cycle.read_rounds_file(phase, cycle_type, root)
    except Exception:
        entries = []
    if not entries:
        try:
            entries = _cycle.read_rounds(phase, cycle_type, root, conn=conn)
        except Exception:
            entries = []
    return [e for e in entries if isinstance(e, dict)]


def rounds_in_cycle(phase: str, cycle_type: str, entries: list[dict]) -> list[BenchRound]:
    """Every reviewed version in a cycle log: a reviewer verdict for round N
    preceded (since the previous verdict) by a lead SUBMIT_FOR_REVIEW of round
    N. The reviewed version is the last lead SUBMIT/AMEND of round N before
    the verdict."""
    out: list[BenchRound] = []
    for i, e in enumerate(entries):
        if e.get("role") != "reviewer" or e.get("action") not in VERDICTS:
            continue
        try:
            n = int(e.get("round"))
        except (TypeError, ValueError):
            continue
        version_index, submitted, amended = None, False, 0
        for j in range(i - 1, -1, -1):
            p = entries[j]
            if p.get("role") == "reviewer" and p.get("action") in VERDICTS:
                break
            if p.get("role") != "lead":
                continue
            try:
                pn = int(p.get("round"))
            except (TypeError, ValueError):
                continue
            if pn != n or p.get("action") not in LEAD_VERSION_ACTIONS:
                continue
            if version_index is None:
                version_index = j
            if p.get("action") == "AMEND":
                amended += 1
            else:
                submitted = True
                break
        if version_index is None or not submitted:
            continue
        out.append(BenchRound(phase, cycle_type, n, e["action"], i, version_index,
                              entries[version_index].get("ts"), amended, entries))
    return out


def benchable_rounds(root: str, conn=None, *, phase: str | None = None,
                     cycle_type: str | None = None) -> list[BenchRound]:
    out = []
    for p, t in _cycle_ids(root, conn):
        if (phase and p != phase) or (cycle_type and t != cycle_type):
            continue
        out.extend(rounds_in_cycle(p, t, _cycle_entries(root, p, t, conn)))
    return out


def find_round(rounds: list[BenchRound], phase: str, cycle_type: str, n: int) -> BenchRound | None:
    """The latest reviewed version of round N (a re-entered round has several)."""
    match = [r for r in rounds if r.phase == phase and r.type == cycle_type and r.round == n]
    return match[-1] if match else None


def snapshot_for(conn, r: BenchRound) -> dict | None:
    if conn is None or not r.version_ts:
        return None
    from tagteam import db as _db
    try:
        return _db.submission_snapshot_for(conn, r.phase, r.type, r.round, r.version_ts)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# select

def select_rows(root: str, conn=None, *, phase=None, cycle_type=None, verdict=None,
                provenance="any", limit=None) -> list[dict]:
    rows = []
    for r in benchable_rounds(root, conn, phase=phase, cycle_type=cycle_type):
        if verdict and r.recorded_verdict != verdict:
            continue
        snap = snapshot_for(conn, r)
        prov = "snapshot" if snap else "none"
        if provenance == "snapshot" and prov != "snapshot":
            continue
        rows.append({"round": r.key, "phase": r.phase, "type": r.type, "n": r.round,
                     "recorded": r.recorded_verdict, "version_ts": r.version_ts,
                     "amended": r.amended, "provenance": prov,
                     "commit_sha": snap.get("commit_sha") if snap else None,
                     "base_sha": snap.get("base_sha") if snap else None})
    if limit is not None:
        rows = rows[-int(limit):]
    return rows


# ---------------------------------------------------------------------------
# plan a run

@dataclass
class Pair:
    round: BenchRound
    cell: Cell
    provenance: str            # snapshot | asserted
    commit_sha: str
    base_sha: str | None
    done: bool = False

    @property
    def identity(self) -> tuple:
        r = self.round
        return (r.phase, r.type, r.round, r.version_ts, self.provenance, self.cell.label,
                self.commit_sha, self.base_sha)


def _aggregation_key(row: dict) -> tuple:
    """One table row per reviewed round version per provenance per cell: several
    asserted tree choices for the same version count once (the latest ok)."""
    return (row["phase"], row["type"], int(row["round"]), row.get("version_ts"), row["provenance"], row["cell"])


def _result_identity(row: dict) -> tuple:
    return (row["phase"], row["type"], int(row["round"]), row.get("version_ts"),
            row["provenance"], row["cell"], row["commit_sha"], row.get("base_sha"))


def parse_round_spec(spec: str) -> tuple[str, str, int, str | None, str | None]:
    """P:T:N[@[BASE..]REV] → (phase, type, n, base_rev, rev)."""
    head, at, trees = spec.partition("@")
    parts = head.rsplit(":", 2)
    if len(parts) != 3 or not parts[0]:
        raise BenchError(f"round {spec!r} must be phase:type:N[@[BASE..]REV]")
    phase, ctype, n = parts
    if ctype not in ("plan", "impl"):
        raise BenchError(f"round {spec!r}: type must be plan or impl")
    try:
        n_int = int(n)
    except ValueError:
        raise BenchError(f"round {spec!r}: round must be an integer")
    base = rev = None
    if at:
        if not trees:
            raise BenchError(f"round {spec!r}: empty revision after @")
        if ".." in trees:
            base, _, rev = trees.partition("..")
            if not base or not rev:
                raise BenchError(f"round {spec!r}: use @BASE..REV")
        else:
            rev = trees
        if ctype == "impl" and base is None:
            raise BenchError(f"round {spec!r}: an asserted impl round needs @BASE..REV "
                             "(the bench never infers a base)")
    return phase, ctype, n_int, base, rev


def _rev_parse(root: str, rev: str) -> str:
    r = subprocess.run(["git", "rev-parse", "--verify", "-q", f"{rev}^{{commit}}"], cwd=root,
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise BenchError(f"revision {rev!r} is not a commit in this repository")
    return r.stdout.strip()


def plan_pairs(root: str, conn, round_specs: list[str], cells: list[Cell]) -> list[Pair]:
    """Validate everything before any spawn. Raises BenchError listing every problem."""
    problems, pairs = [], []
    seen: set[tuple] = set()
    rounds = benchable_rounds(root, conn)
    done: set[tuple] = set()
    if conn is not None:
        from tagteam import db as _db
        done = {_result_identity(row) for row in _db.bench_results(conn) if row["outcome"] == "ok"}
    for spec in round_specs:
        try:
            phase, ctype, n, base_rev, rev = parse_round_spec(spec)
        except BenchError as e:
            problems.append(str(e)); continue
        r = find_round(rounds, phase, ctype, n)
        if r is None:
            problems.append(f"round {spec!r}: no reviewer verdict recorded for {phase}:{ctype}:{n}")
            continue
        if rev is not None:
            try:
                commit = _rev_parse(root, rev)
                base = _rev_parse(root, base_rev) if base_rev else None
            except BenchError as e:
                problems.append(f"round {spec!r}: {e}"); continue
            prov = "asserted"
        else:
            snap = snapshot_for(conn, r)
            if snap is None:
                problems.append(f"round {spec!r}: no snapshot for the reviewed version "
                                f"({r.version_ts}); name the trees with @BASE..REV")
                continue
            commit, base, prov = snap["commit_sha"], snap.get("base_sha"), "snapshot"
        for cell in cells:
            p = Pair(r, cell, prov, commit, base)
            if p.identity in seen:
                continue            # the same pair requested twice runs (and is budgeted) once
            seen.add(p.identity)
            p.done = p.identity in done
            pairs.append(p)
    if problems:
        raise BenchError("\n".join(problems))
    return pairs


def reviewer_token_estimate(conn) -> int | None:
    """Mean input-side tokens (input + cache read + cache write) of this
    project's recorded reviewer turns (not bench rows). A proxy."""
    if conn is None:
        return None
    from tagteam import db as _db
    vals = []
    for u in _db.get_usage(conn):
        if u.get("role") != "reviewer" or u.get("kind") == "bench":
            continue
        parts = [u.get("input_tokens"), u.get("cache_read_tokens"), u.get("cache_write_tokens")]
        if all(v is None for v in parts):
            continue
        vals.append(sum(v or 0 for v in parts))
    return int(sum(vals) / len(vals)) if vals else None


def _fmt_tokens(n: int | float | None) -> str:
    if n is None:
        return "-"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}k" if n >= 10_000 else f"{n / 1000:.1f}k"
    return str(int(n))


# ---------------------------------------------------------------------------
# replay repository

def _git(cwd: str | Path, *args: str, env: dict | None = None, check: bool = True,
         text: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=text, env=env)
    if check and r.returncode != 0:
        err = r.stderr if text else r.stderr.decode("utf-8", "replace")
        raise BenchError(f"git {args[0]} failed: {(err or '').strip()}")
    return r


def _replay_env() -> dict:
    env = dict(os.environ)
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
              "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        env.pop(k, None)
    return env


# Attributes that would transform content between the object store and the
# working tree (eol conversion, clean/smudge filters, ident, encodings) are
# neutralised in the replay repo, so checkout writes the blobs byte for byte.
_RAW_ATTRIBUTES = "* -text -filter -ident -working-tree-encoding\n"
_REPLAY_BRANCH = "refs/heads/replay"


def outcome_paths(phase: str, cycle_type: str) -> list[str]:
    """Every tracked copy of a cycle's history/outcome the readers support."""
    stem = f"{phase}_{cycle_type}"
    return [f"docs/handoffs/{stem}_rounds.jsonl", f"docs/handoffs/{stem}_status.json",
            f"docs/handoffs/{stem}.md", f"docs/handoffs/{stem}_cycle.md",
            f".tagteam/legacy/{stem}_rounds.jsonl", f".tagteam/legacy/{stem}_status.json"]


def pre_verdict_jsonl(pre_verdict: list[dict]) -> bytes:
    clean = [{k: v for k, v in e.items() if k not in ("interjections", "entries")} for e in pre_verdict]
    return "".join(json.dumps(e) + "\n" for e in clean).encode("utf-8")


def _tree_entries(project_root: str, commit: str) -> list[tuple[str, str, str, str]]:
    """(mode, type, sha, path) of every entry of `commit`'s tree, recursively."""
    out = _git(project_root, "ls-tree", "-r", "-z", "--full-tree", commit, text=False).stdout
    entries = []
    for rec in out.split(b"\0"):
        if not rec:
            continue
        meta, _, path = rec.partition(b"\t")
        mode, otype, sha = meta.decode().split()
        entries.append((mode, otype, sha, path.decode("utf-8", "surrogateescape")))
    return entries


def _fi_path(path: str) -> bytes:
    raw = path.encode("utf-8", "surrogateescape")
    if raw.startswith(b'"') or b"\n" in raw:
        raw = b'"' + raw.replace(b"\\", b"\\\\").replace(b'"', b'\\"').replace(b"\n", b"\\n") + b'"'
    return raw


def _read_blob(batch: subprocess.Popen, sha: str) -> bytes:
    batch.stdin.write(sha.encode() + b"\n")
    batch.stdin.flush()
    header = batch.stdout.readline().split()
    if len(header) != 3 or header[1] != b"blob":
        raise BenchError(f"cannot read blob {sha}: {b' '.join(header).decode(errors='replace')}")
    data = batch.stdout.read(int(header[2]))
    batch.stdout.read(1)        # trailing newline
    return data


def _stream_commit(fi, batch, project_root: str, commit: str | None, message: str,
                   skip: set[str], extra: dict[str, bytes], omitted: list[str]) -> None:
    body = message.encode()
    fi.write(b"commit " + _REPLAY_BRANCH.encode() + b"\n")
    fi.write(b"committer tagteam-bench <bench@localhost> 0 +0000\n")
    fi.write(b"data %d\n%s\n" % (len(body), body))
    # No `from`: a second commit to the branch in the same stream takes its tip as parent.
    fi.write(b"deleteall\n")
    for mode, otype, sha, path in (_tree_entries(project_root, commit) if commit else []):
        if path in skip:
            continue
        if otype != "blob":             # submodule gitlinks: not materialised
            omitted.append(path)
            continue
        data = _read_blob(batch, sha)
        fi.write(b"M %s inline %s\ndata %d\n" % (mode.encode(), _fi_path(path), len(data)))
        fi.write(data + b"\n")
    for path, data in extra.items():
        fi.write(b"M 100644 inline %s\ndata %d\n" % (_fi_path(path), len(data)))
        fi.write(data + b"\n")


def build_replay_repo(project_root: str, repo: Path, pair: Pair) -> dict:
    """Materialise base → submitted from raw tree objects (`ls-tree` +
    `cat-file --batch` into `fast-import`): no export-ignore/export-subst,
    no clean filters, no eol conversion. The target cycle's outcome copies
    are left out of both commits; the submitted commit gets the pre-verdict
    rounds file. Returns {"omitted": [gitlink paths not materialised]}."""
    r = pair.round
    env = _replay_env()
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", env=env)
    (repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text(_RAW_ATTRIBUTES, encoding="utf-8")
    skip = set(outcome_paths(r.phase, r.type))
    canonical = f"docs/handoffs/{r.phase}_{r.type}_rounds.jsonl"
    omitted: list[str] = []
    batch = subprocess.Popen(["git", "cat-file", "--batch"], cwd=project_root, env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    fi = subprocess.Popen(["git", "fast-import", "--quiet", "--done"], cwd=str(repo), env=env,
                          stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        try:
            if pair.base_sha:
                _stream_commit(fi.stdin, batch, project_root, pair.base_sha, "base", skip, {}, [])
                fi.stdin.write(b"reset refs/tags/base\nfrom " + _REPLAY_BRANCH.encode() + b"\n\n")
            _stream_commit(fi.stdin, batch, project_root, pair.commit_sha, "submitted", skip,
                           {canonical: pre_verdict_jsonl(r.pre_verdict)}, omitted)
            fi.stdin.write(b"done\n")
            fi.stdin.close()
        except BrokenPipeError:
            pass
        err = fi.stderr.read().decode("utf-8", "replace")
        if fi.wait() != 0:
            raise BenchError(f"git fast-import failed: {err.strip()}")
    finally:
        for p in (batch, fi):
            if p.poll() is None:
                p.kill()
        batch.stdin.close()
        batch.wait()
    _git(repo, "symbolic-ref", "HEAD", _REPLAY_BRANCH, env=env)
    _git(repo, "-c", "core.autocrlf=false", "-c", "core.hooksPath=/dev/null", "reset", "-q", "--hard",
         _REPLAY_BRANCH, env=env)
    return {"omitted": omitted}


# ---------------------------------------------------------------------------
# replay directories: ownership, liveness, retention

def _read_owner(d: Path) -> dict | None:
    try:
        data = json.loads((d / OWNER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_owner(d: Path, project_root: str, run_id: str, attempt: str, keep: bool) -> None:
    from tagteam import procs
    pid = os.getpid()
    (d / OWNER_FILE).write_text(json.dumps({
        "project": project_root, "run_id": run_id, "attempt": attempt, "keep": bool(keep),
        "pid": pid, "ident": procs.identity(pid)}), encoding="utf-8")


def replay_dir_state(d: Path, project_root: str) -> str:
    """other | kept | active | abandoned."""
    from tagteam import procs
    owner = _read_owner(d)
    if owner is None or owner.get("project") != project_root:
        return "other"
    if owner.get("keep"):
        return "kept"
    pid = owner.get("pid")
    if isinstance(pid, int) and procs.pid_alive(pid):
        ident = owner.get("ident")
        if ident is None or procs.identity(pid) in (None, ident):
            return "active"
    return "abandoned"


def prune_stale_replays(project_root: str) -> int:
    """Remove this project's replay directories whose owning bench process is
    gone. Kept (--keep) and live ones are never touched."""
    n = 0
    for d in Path(tempfile.gettempdir()).glob(TEMP_PREFIX + "*"):
        if d.is_dir() and replay_dir_state(d, project_root) == "abandoned":
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    return n

# ---------------------------------------------------------------------------
# prompt

def _name_status(repo: Path, has_base: bool) -> str:
    if not has_base:
        return ""
    out = _git(repo, "diff", "--name-status", "--find-renames", "HEAD~1", "HEAD").stdout
    lines = out.splitlines()
    if len(lines) > NAME_STATUS_MAX_LINES:
        lines = lines[:NAME_STATUS_MAX_LINES] + [f"… {len(lines) - NAME_STATUS_MAX_LINES} more"]
    return "\n".join(lines) or "(no file changes between base and submitted)"


def compose_bench_prompt(pair: Pair, repo: Path, verdict_path: Path, *,
                         omitted: list[str] | None = None) -> str:
    r = pair.round
    contract = CONTRACT_PATH.read_text(encoding="utf-8").format(
        phase=r.phase, type=r.type, round=r.round, verdict_path=str(verdict_path))
    parts = [contract.rstrip(), ""]
    parts.append("=== SUBMITTED CHANGE ===")
    if pair.base_sha:
        parts += ["History: base → submitted (HEAD). Review `git diff HEAD~1` (same as "
                  "`git diff base..HEAD`); plain `git diff` / `git status` are empty by construction.",
                  "Files changed (git diff --name-status --find-renames HEAD~1 HEAD):",
                  _name_status(repo, True)]
    else:
        parts.append("no base: review the plan document and the tree (history is the submitted "
                     "commit only).")
    if omitted:
        parts.append(f"Not materialised (submodule entries): {', '.join(omitted[:20])}"
                     + (f" … {len(omitted) - 20} more" if len(omitted) > 20 else ""))
    parts.append("")
    # The project's own checklist wins; otherwise the package's (Phase 61:
    # setup no longer installs docs/checklists/).
    name = "code_review.md" if r.type == "impl" else "plan_review.md"
    checklist = repo / "docs" / "checklists" / name
    label = f"docs/checklists/{name}"
    if not checklist.is_file():
        checklist = Path(__file__).parent / "data" / "checklists" / name
        label = f"tagteam package: checklists/{name}"
    if checklist.is_file():
        parts += [f"=== REVIEW CHECKLIST ({label}) ===",
                  checklist.read_text(encoding="utf-8", errors="replace").strip(), ""]
    pre = r.pre_verdict
    gate = next((e for e in reversed(pre) if e.get("role") == "gatekeeper"
                 and _int(e.get("round")) == r.round), None)
    if gate:
        parts += ["=== GATEKEEPER (already ran on this submission) ===",
                  (gate.get("content") or "").strip(), ""]
    low = r.round - h.DEFAULT_TAIL_ROUNDS + 1
    tail = [e for e in pre if (_int(e.get("round")) or 0) >= low]
    parts += [f"=== ROUND TAIL (entries of rounds {max(low, 1)}–{r.round} up to the reviewed version) ===",
              "\n".join(json.dumps({k: v for k, v in e.items() if k not in ("interjections", "entries")})
                        for e in tail) or "(no rounds)", ""]
    plan = repo / "docs" / "phases" / f"{r.phase}.md"
    parts += [f"=== PLAN (docs/phases/{r.phase}.md) ===",
              plan.read_text(encoding="utf-8", errors="replace").strip() if plan.is_file()
              else "(no plan document found)", "",
              "Now do the review. Read the code and the plan yourself; the round tail is a summary, "
              "not evidence. Write the verdict JSON to the path above and stop."]
    return "\n".join(parts)


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# run one pair

def _executable(root: str) -> str:
    from tagteam.config import read_config, get_headless_spec
    configured = None
    try:
        cfg = read_config(Path(root) / "tagteam.yaml") or {}
        for role in ("reviewer", "lead"):
            spec = get_headless_spec(cfg, role)
            if spec.get("provider") == "claude" and spec.get("executable"):
                configured = spec["executable"]
                break
    except Exception:
        configured = None
    return h.resolve_executable("claude", configured)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")


def run_pair(root: str, pair: Pair, *, run_id: str, executable: str, timeout_s: float,
             keep: bool, log) -> dict:
    from tagteam import db as _db
    from tagteam import dualwrite
    from tagteam.panel import verify_verdict
    r = pair.round
    attempt = uuid.uuid4().hex[:8]
    version = re.sub(r"[^0-9A-Za-z]", "", r.version_ts or "none")
    stem_dir = (Path(root) / ".tagteam" / "bench" / run_id /
                _slug(f"{r.phase}_{r.type}_r{r.round}_{version}_{pair.provenance}_{pair.cell.label}_"
                      f"{pair.commit_sha[:10]}_{(pair.base_sha or 'nobase')[:10]}_{attempt}"))
    stem_dir.mkdir(parents=True, exist_ok=False)     # a fresh directory per attempt: no stale verdict
    verdict_path = stem_dir / "verdict.json"
    events_path, log_path = stem_dir / "events.jsonl", stem_dir / "turn.log"
    tmp = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    write_owner(tmp, root, run_id, attempt, keep)
    repo = tmp / "repo"
    outcome, reason, ustatus, verdict = "failed", "", "spawn_failed", None
    out = h.RunOutput(exit_code=None, timed_out=False, duration_ms=0)
    try:
        try:
            built = build_replay_repo(root, repo, pair)
            prompt = compose_bench_prompt(pair, repo, verdict_path, omitted=built["omitted"])
        except (BenchError, OSError) as e:
            reason = f"replay repository: {e}"
            raise _Abort()
        (stem_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        argv = h.build_argv(h.CLAUDE, executable, pair.cell.user_args + ["--add-dir", str(stem_dir)], repo)
        env = dict(os.environ)
        for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
            env.pop(k, None)
        env[dualwrite.READ_ONLY_ENV] = "1"
        env["TAGTEAM_BENCH"] = "1"
        log(f"   bench: {r.key} [{pair.provenance}] × {pair.cell.label} — spawning (log: {log_path})")
        try:
            out = h.run_process(argv, prompt, repo, events_path=events_path, log_path=log_path,
                                provider="claude", timeout_s=timeout_s, env=env)
        except h.SpawnError as e:
            reason = f"could not start claude: {e}"
            raise _Abort()
        verdict, vreason = verify_verdict(verdict_path)
        if out.timed_out:
            reason, ustatus = f"timeout after {timeout_s / 60:.0f} min", "timeout"
        elif verdict is None:
            reason = vreason
            ustatus = "nonzero_exit" if out.exit_code not in (0, None) else "no_round"
        else:
            outcome, reason, ustatus = "ok", "ok", "ok"
    except _Abort:
        pass
    finally:
        if keep:
            log(f"   bench: kept replay repository {repo}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    try:
        lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    usage = h.parse_usage("claude", lines) or {}
    conn = _db.connect(project_dir=root)
    try:
        usage_row_id = None
        if ustatus != "spawn_failed" or lines:
            usage_row_id = _db.add_usage(
                conn, ts=_now_iso(), role="reviewer", agent=pair.cell.label, provider="claude",
                status=ustatus, exit_code=out.exit_code, duration_ms=out.duration_ms,
                log_path=str(log_path), kind="bench",
                **{k: usage.get(k) for k in ("model", "input_tokens", "output_tokens",
                                             "cache_read_tokens", "cache_write_tokens",
                                             "cost_usd", "num_turns", "session_id",
                                             "model_usage_json")})
        findings = (verdict or {}).get("findings") or []
        sev = lambda s: sum(1 for f in findings if f.get("severity") == s)  # noqa: E731
        row = {"run_id": run_id, "phase": r.phase, "type": r.type, "round": r.round,
               "version_ts": r.version_ts, "amended": 1 if r.amended else 0,
               "provenance": pair.provenance, "cell": pair.cell.label,
               "commit_sha": pair.commit_sha, "base_sha": pair.base_sha,
               "recorded_verdict": r.recorded_verdict,
               "verdict": verdict["verdict"] if verdict else None,
               "n_blocker": sev("blocker") if verdict else None,
               "n_major": sev("major") if verdict else None,
               "n_minor": sev("minor") if verdict else None,
               "findings_json": json.dumps(findings) if verdict else None,
               "outcome": outcome, "reason": reason, "usage_row_id": usage_row_id,
               "duration_ms": out.duration_ms, "ts": _now_iso()}
        _db.add_bench_result(conn, **row)
    finally:
        conn.close()
    log(f"   bench: {r.key} × {pair.cell.label} → {outcome}"
        + (f" {row['verdict']} (recorded {r.recorded_verdict})" if verdict else f" ({reason})"))
    return row


class _Abort(Exception):
    pass


# ---------------------------------------------------------------------------
# table

def table_data(conn, run_id: str | None = None) -> dict:
    from tagteam import db as _db
    rows = _db.bench_results(conn, run_id) if conn is not None else []
    usage = {u["id"]: u for u in _db.get_usage(conn)} if conn is not None and rows else {}
    blocks = []
    for prov in ("snapshot", "asserted"):
        prow = [x for x in rows if x["provenance"] == prov]
        if not prow:
            continue
        cells = []
        for cell in sorted({x["cell"] for x in prow}):
            crow = [x for x in prow if x["cell"] == cell]
            latest_ok: dict[tuple, dict] = {}
            for x in crow:
                if x["outcome"] == "ok":
                    latest_ok[_aggregation_key(x)] = x      # rows are oldest first: the last one wins
            ok = list(latest_ok.values())
            ok_ids = set(latest_ok)
            failed = [x for x in crow if x["outcome"] == "failed" and _aggregation_key(x) not in ok_ids]
            rec_rc = [x for x in ok if x["recorded_verdict"] == "REQUEST_CHANGES"]
            rec_ap = [x for x in ok if x["recorded_verdict"] == "APPROVE"]
            cell_rc = [x for x in ok if x["verdict"] == "REQUEST_CHANGES"]
            toks = [usage.get(x["usage_row_id"]) for x in ok]
            toks = [u for u in toks if u and any(u.get(k) is not None for k in
                                                 ("input_tokens", "cache_read_tokens", "output_tokens"))]
            mean = lambda xs: (sum(xs) / len(xs)) if xs else None  # noqa: E731
            cells.append({
                "cell": cell, "rounds": len(ok),
                "agree": sum(1 for x in ok if x["verdict"] == x["recorded_verdict"]),
                "recorded_rc": len(rec_rc),
                "missed_rc": sum(1 for x in rec_rc if x["verdict"] == "APPROVE"),
                "recorded_approve": len(rec_ap),
                "extra_rc": sum(1 for x in rec_ap if x["verdict"] == "REQUEST_CHANGES"),
                "blockers_per_rc": mean([x["n_blocker"] or 0 for x in cell_rc]),
                "majors_per_rc": mean([x["n_major"] or 0 for x in cell_rc]),
                "input_tokens": mean([sum((u.get(k) or 0) for k in ("input_tokens", "cache_read_tokens",
                                                                    "cache_write_tokens")) for u in toks]),
                "output_tokens": mean([u.get("output_tokens") or 0 for u in toks]),
                "rows_with_tokens": len(toks),
                "seconds": mean([(x["duration_ms"] or 0) / 1000 for x in ok]),
                "failed": len(failed),
                "failed_reasons": sorted({x["reason"] or "" for x in failed}),
            })
        blocks.append({"provenance": prov, "cells": cells})
    return {"note": NOT_GROUND_TRUTH, "run_id": run_id, "blocks": blocks}


def render_table(data: dict) -> str:
    if not data["blocks"]:
        return "no bench results"
    lines = []
    for b in data["blocks"]:
        lines += [f"provenance {b['provenance']}", data["note"],
                  f"{'cell':<28}{'rounds':>7}{'agree':>8}{'missed-RC':>11}{'extra-RC':>10}"
                  f"{'blk/maj per RC':>16}{'in-tok':>8}{'out-tok':>9}{'sec':>6}"]
        for c in b["cells"]:
            sec = "-" if c["seconds"] is None else f"{c['seconds']:.0f}"
            bm = ("-" if c["blockers_per_rc"] is None
                  else f"{c['blockers_per_rc']:.1f} / {c['majors_per_rc']:.1f}")
            lines.append(
                f"{c['cell']:<28}{c['rounds']:>7}{str(c['agree']) + '/' + str(c['rounds']):>8}"
                f"{str(c['missed_rc']) + '/' + str(c['recorded_rc']):>11}"
                f"{str(c['extra_rc']) + '/' + str(c['recorded_approve']):>10}{bm:>16}"
                f"{_fmt_tokens(c['input_tokens']):>8}{_fmt_tokens(c['output_tokens']):>9}"
                f"{sec:>6}")
        for c in b["cells"]:
            if c["failed"]:
                lines.append(f"failed: {c['cell']} {c['failed']} ({'; '.join(c['failed_reasons'])})")
        lines.append("")
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# commands

def _root(project_root) -> str:
    if project_root is None:
        from tagteam.state import _resolve_project_root
        project_root = _resolve_project_root()
    return str(Path(project_root).resolve())


def _take(args: list[str], i: int, flag: str) -> str:
    if i + 1 >= len(args):
        raise BenchError(f"{flag} needs a value")
    return args[i + 1]


def select_command(args: list[str], project_root=None, out=None) -> int:
    from tagteam import db as _db
    out = out or sys.stdout
    opts = {"phase": None, "type": None, "verdict": None, "provenance": "any", "limit": None}
    as_json, i = False, 0
    try:
        while i < len(args):
            a = args[i]
            if a in ("--phase", "--type", "--verdict", "--provenance", "--limit"):
                opts[a[2:]] = _take(args, i, a); i += 2
            elif a == "--json":
                as_json = True; i += 1
            else:
                raise BenchError(f"unknown argument: {a}")
        if opts["type"] not in (None, "plan", "impl"):
            raise BenchError("--type must be plan or impl")
        if opts["provenance"] not in ("any", "snapshot"):
            raise BenchError("--provenance must be snapshot or any")
        if opts["verdict"] not in (None, *VERDICTS):
            raise BenchError(f"--verdict must be one of {', '.join(VERDICTS)}")
        limit = int(opts["limit"]) if opts["limit"] is not None else None
    except (BenchError, ValueError) as e:
        print(f"bench select: {e}\n{USAGE}", file=out)
        return 1
    root = _root(project_root)
    conn, _note = _db.connect_for_read(root)
    try:
        rows = select_rows(root, conn, phase=opts["phase"], cycle_type=opts["type"],
                           verdict=opts["verdict"], provenance=opts["provenance"], limit=limit)
    finally:
        if conn is not None:
            conn.close()
    if as_json:
        print(json.dumps(rows, indent=2), file=out)
        return 0
    if not rows:
        print("no benchable rounds", file=out)
        return 0
    for x in rows:
        amended = f" (amended {x['amended']})" if x["amended"] else ""
        print(f"{x['round']:<40} recorded={x['recorded']:<16} version={x['version_ts']}{amended}"
              f"  provenance={x['provenance']}", file=out)
    return 0


def run_command(args: list[str], project_root=None, out=None) -> int:
    from tagteam import db as _db
    out = out or sys.stdout
    round_specs, cell_specs = [], []
    max_turns, timeout_min, keep, yes = DEFAULT_MAX_TURNS, float(h.DEFAULT_TURN_TIMEOUT_MINUTES), False, False
    i = 0
    try:
        while i < len(args):
            a = args[i]
            if a == "--round":
                round_specs.append(_take(args, i, a)); i += 2
            elif a == "--cell":
                cell_specs.append(_take(args, i, a)); i += 2
            elif a == "--max-turns":
                max_turns = int(_take(args, i, a)); i += 2
            elif a == "--timeout-minutes":
                timeout_min = float(_take(args, i, a)); i += 2
            elif a == "--keep":
                keep = True; i += 1
            elif a == "--yes":
                yes = True; i += 1
            else:
                raise BenchError(f"unknown argument: {a}")
        if not round_specs or not cell_specs:
            raise BenchError("at least one --round and one --cell are required")
        if max_turns < 1 or timeout_min <= 0:
            raise BenchError("--max-turns and --timeout-minutes must be positive")
        cells = [parse_cell(c) for c in cell_specs]
    except (BenchError, ValueError) as e:
        print(f"bench run: {e}\n{USAGE}", file=out)
        return 1
    root = _root(project_root)
    conn, _note = _db.connect_for_read(root)
    try:
        try:
            pairs = plan_pairs(root, conn, round_specs, cells)
        except BenchError as e:
            print(f"bench run: refused\n{e}", file=out)
            return 1
        estimate = reviewer_token_estimate(conn)
    finally:
        if conn is not None:
            conn.close()
    todo = [p for p in pairs if not p.done]
    print(f"bench run: {len(pairs)} pair(s), {len(pairs) - len(todo)} already done, {len(todo)} to run", file=out)
    for p in pairs:
        r = p.round
        base = p.base_sha[:12] if p.base_sha else "none"
        print(f"  {'done ' if p.done else 'run  '} {r.key:<36} {p.cell.label:<26} {p.provenance:<9} "
              f"recorded={r.recorded_verdict} version={r.version_ts} commit={p.commit_sha[:12]} base={base}",
              file=out)
    if estimate is None:
        print("  estimate unknown (no reviewer usage rows)", file=out)
    else:
        print(f"  estimate ≈ {len(todo)} × {_fmt_tokens(estimate)} input tokens "
              f"= {_fmt_tokens(len(todo) * estimate)} (proxy: past reviewer turns, not bench turns)", file=out)
    if len(todo) > max_turns:
        print(f"bench run: refused — {len(todo)} pairs to run exceeds --max-turns {max_turns} "
              f"(raise it with --max-turns {len(todo)})", file=out)
        return 1
    if not yes:
        print("dry run: nothing spawned; add --yes to run", file=out)
        return 0
    if not todo:
        return 0
    try:
        executable = _executable(root)
    except h.HeadlessConfigError as e:
        print(f"bench run: {e}", file=out)
        return 1
    pruned = prune_stale_replays(root)
    if pruned:
        print(f"bench run: removed {pruned} stale replay director{'y' if pruned == 1 else 'ies'}", file=out)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    log = lambda m: print(m, file=out, flush=True)  # noqa: E731
    failed = 0
    for p in todo:
        row = run_pair(root, p, run_id=run_id, executable=executable, timeout_s=timeout_min * 60.0,
                       keep=keep, log=log)
        failed += row["outcome"] != "ok"
    print(f"bench run {run_id}: {len(todo) - failed} ok, {failed} failed — `tagteam bench table --run {run_id}`",
          file=out)
    return 0 if failed == 0 else 1


def table_command(args: list[str], project_root=None, out=None) -> int:
    from tagteam import db as _db
    out = out or sys.stdout
    run_id, as_json, i = None, False, 0
    try:
        while i < len(args):
            a = args[i]
            if a == "--run":
                run_id = _take(args, i, a); i += 2
            elif a == "--json":
                as_json = True; i += 1
            else:
                raise BenchError(f"unknown argument: {a}")
    except BenchError as e:
        print(f"bench table: {e}\n{USAGE}", file=out)
        return 1
    root = _root(project_root)
    conn, _note = _db.connect_for_read(root)
    try:
        data = table_data(conn, run_id)
    finally:
        if conn is not None:
            conn.close()
    print(json.dumps(data, indent=2) if as_json else render_table(data), file=out)
    return 0


def bench_command(args: list[str], project_root=None, out=None) -> int:
    out = out or sys.stdout
    if not args or args[0] in ("-h", "--help"):
        print(USAGE, file=out)
        return 0 if args else 1
    sub, rest = args[0], args[1:]
    if sub == "select":
        return select_command(rest, project_root, out)
    if sub == "run":
        return run_command(rest, project_root, out)
    if sub == "table":
        return table_command(rest, project_root, out)
    print(f"bench: unknown subcommand {sub!r}\n{USAGE}", file=out)
    return 1
