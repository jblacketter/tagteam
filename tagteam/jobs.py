"""Jobs: recorded background tasks (Phase 72).

The rule: deterministic first, a cheap model second, the top model last.
Waiting for CI or a release needs no model — it needs a task that polls until
it knows the answer, then says it once. A job is that task:

  tagteam job start ci-watch (--pr N [--expect-checks N] | --run ID
                              | --workflow NAME (--ref REF | --sha SHA) | --pypi PKG==VER)
                   [--interval S] [--timeout M] [--to-lead] [--quiet]
  tagteam job list [--all] [--json] | status ID [--json] | log ID [-n N] | cancel ID
  tagteam job run ID          # internal: the detached runner

A job lives in files, never the database, so `list` / `status` / `log` and
`GET /api/jobs` create nothing and work read-only:

``.tagteam/jobs/<id>/job.json``     the record (atomic replace, guarded opens)
``.tagteam/jobs/<id>/log.txt``      one line per poll, bounded (+ one ``.1``)
``.tagteam/jobs/<id>/runner.lock``  held by the runner for its whole life
``.tagteam/jobs/<id>/job.lock``     held briefly around every write of job.json
``.tagteam/jobs/<id>/cancel``       the cancel marker
``.tagteam/jobs/<id>/runner.out``   the runner's stdout/stderr (tracebacks)

**Ownership is two locks the OS releases** (plan review r1/r2). Holding
``runner.lock`` IS the claim and the liveness signal: a reader that can take
it proves no runner holds the job (``lost``); one that cannot open it knows
nothing (``unknown``, never treated as dead). Every write of ``job.json``
re-reads under ``job.lock`` and a terminal record is never replaced — only
its committer may add ``delivery`` — so recovery never overwrites a CI
result. Lock order: the runner takes ``runner.lock`` once, before it ever
takes ``job.lock``; everyone else only probes ``runner.lock`` non-blocking.

Delivery is attempted at most once, best-effort, by the committer, after the
commit: a committer that dies in between misses it and nothing replays it.

No model is involved and no turn slot is taken — a job is not a turn.
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tagteam.safe_read import _dir_chain, read_bounded

JOBS_REL = ".tagteam/jobs"
KINDS = ("ci-watch",)
TERMINAL = ("succeeded", "failed", "timed-out", "cancelled", "error")
STATUSES = ("starting", "running") + TERMINAL

DEFAULT_INTERVAL_S = 20
MIN_INTERVAL_S = 5
DEFAULT_TIMEOUT_MIN = 60
START_DEADLINE_S = 5.0          # `start` waits this long for the runner to move starting → running
CLAIM_RETRY_S = 2.0             # the runner's non-blocking acquire retries this long (a reader's probe)
STARTING_GRACE_S = 30.0         # a `starting` job with a free runner lock reads `starting` this long
RETENTION_DAYS = 7
LIST_WINDOW_H = 24
LOG_MAX_BYTES = 256 * 1024
LOG_TAIL_LINES = 40             # of `gh run view --log-failed` in a red result
GH_TIMEOUT_S = 60
PYPI_SIMPLE_ENV = "TAGTEAM_PYPI_SIMPLE"      # test/mirror override of https://pypi.org/simple/
PYPI_SIMPLE = "https://pypi.org/simple/"

_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_PYPI_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.+!_-]*)$")
_RUN_URL_RE = re.compile(r"/actions/runs/(\d+)")
_RECORD_LIMIT = 256 * 1024

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _age_s(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return max(0.0, (_now() - datetime.fromisoformat(ts)).total_seconds())
    except (TypeError, ValueError):
        return None


def valid_id(jid: str) -> bool:
    return isinstance(jid, str) and bool(_ID_RE.match(jid))


def _job_rel(jid: str, name: str = "") -> str:
    return f"{JOBS_REL}/{jid}" + (f"/{name}" if name else "")


# ---------------------------------------------------------------------------
# Guarded files
# ---------------------------------------------------------------------------

def _open_guarded(root: Path, rel: str, flags: int) -> int | None:
    """An fd on ``rel`` or None: every parent a plain directory, an existing
    leaf a regular file by ``lstat`` and again by ``fstat`` (the watchlog
    rules — a symlink or FIFO at the path is refused, never followed or
    blocked on)."""
    parent = Path(rel).parent.as_posix()
    if _dir_chain(root, parent)[0] != "dir":
        return None
    path = root / rel
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        st = None
    except OSError:
        return None
    if st is not None and not stat.S_ISREG(st.st_mode):
        return None
    try:
        fd = os.open(path, flags | _NOFOLLOW | _NONBLOCK | _CLOEXEC, 0o644)
    except OSError:
        return None
    try:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            return fd
    except OSError:
        pass
    os.close(fd)
    return None


def _mkdirs(root: Path, rel: str) -> bool:
    """Create ``rel`` under ``root`` component by component, refusing a
    symlinked or non-directory component."""
    cur = root
    for part in Path(rel).parts:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            try:
                os.mkdir(cur, 0o755)
            except FileExistsError:
                pass
            except OSError:
                return False
            try:
                st = os.lstat(cur)
            except OSError:
                return False
        except OSError:
            return False
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            return False
    return True


def read_record(root: Path, jid: str) -> dict | None:
    if not valid_id(jid):
        return None
    r = read_bounded(Path(root), _job_rel(jid, "job.json"), _RECORD_LIMIT, truncate=False)
    if r.state != "file" or r.data is None:
        return None
    try:
        data = json.loads(r.data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_record(root: Path, jid: str, rec: dict) -> bool:
    """Atomic replace of job.json (temp + ``os.replace``): a crash leaves the
    old record or the new one, never half of one."""
    tmp_rel = _job_rel(jid, f"job.json.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = _open_guarded(root, tmp_rel, flags)
    if fd is None:
        try:
            os.unlink(root / tmp_rel)       # a leftover of a dead writer with our pid
        except OSError:
            return False
        fd = _open_guarded(root, tmp_rel, flags)
        if fd is None:
            return False
    try:
        os.write(fd, json.dumps(rec, indent=2, sort_keys=True).encode("utf-8"))
        os.fsync(fd)
    except OSError:
        os.close(fd)
        try:
            os.unlink(root / tmp_rel)
        except OSError:
            pass
        return False
    os.close(fd)
    try:
        os.replace(root / tmp_rel, root / _job_rel(jid, "job.json"))
        return True
    except OSError:
        return False


def _refuse_read_only(detail: str) -> None:
    """Phase 50's switch at the jobs' shared mutation boundary: direct callers
    (the cockpit action, tests, other modules) are refused like the CLI."""
    from tagteam.dualwrite import refuse_if_read_only
    refuse_if_read_only(detail)


def _append_log(root: Path, jid: str, msg: str) -> None:
    """One timestamped line; bounded (rotates once to ``log.txt.1``). Never raises;
    writes nothing under TAGTEAM_READ_ONLY."""
    from tagteam.dualwrite import read_only
    if read_only():
        return
    try:
        rel = _job_rel(jid, "log.txt")
        fd = _open_guarded(root, rel, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        if fd is None:
            return
        try:
            if os.fstat(fd).st_size > LOG_MAX_BYTES:
                os.close(fd)
                fd = None
                os.replace(root / rel, root / _job_rel(jid, "log.txt.1"))
                fd = _open_guarded(root, rel, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
                if fd is None:
                    return
            line = f"{_now().strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}".rstrip() + "\n"
            os.write(fd, line.encode("utf-8", "replace"))
        finally:
            if fd is not None:
                os.close(fd)
    except Exception:
        pass


def read_log(root: Path, jid: str, n: int = 20) -> list[str]:
    if not valid_id(jid):
        return []
    r = read_bounded(Path(root), _job_rel(jid, "log.txt"), LOG_MAX_BYTES + 64 * 1024, truncate=True)
    if r.state != "file" or not r.data:
        return []
    lines = r.data.decode("utf-8", "replace").splitlines()
    return lines[-n:] if n > 0 else lines


# ---------------------------------------------------------------------------
# Locks (portable; the OS releases a dead holder's lock)
# ---------------------------------------------------------------------------

_WIN_LOCK_OFFSET = 1 << 30      # the same far byte dualwrite and the watcher lock use

if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        """Genuinely non-blocking exclusive lock (``LOCK_NB``)."""
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


def _lock_blocking(fd: int) -> None:
    from tagteam.dualwrite import _os_lock
    _os_lock(fd)


class _JobLock:
    """``job.lock``, held briefly around a job.json read-modify-write."""

    def __init__(self, root: Path, jid: str):
        self.root, self.jid, self.fd = root, jid, None

    def __enter__(self):
        self.fd = _open_guarded(self.root, _job_rel(self.jid, "job.lock"), os.O_RDWR | os.O_CREAT)
        if self.fd is None:
            raise OSError(f"job {self.jid}: job.lock cannot be opened")
        _lock_blocking(self.fd)
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            _unlock(self.fd)
            os.close(self.fd)
            self.fd = None
        return False


def probe_runner(root: Path, jid: str) -> str:
    """``busy`` (a runner holds runner.lock) | ``free`` (nobody does — the OS
    would have kept it for a living holder) | ``unknown`` (it cannot be
    opened). Opens the existing file read-only; creates and writes nothing."""
    fd = _open_guarded(Path(root), _job_rel(jid, "runner.lock"), os.O_RDONLY)
    if fd is None:
        return "unknown"
    try:
        if _try_lock(fd):
            _unlock(fd)
            return "free"
        return "busy"
    finally:
        os.close(fd)


def _terminal_change_ok(old: dict, new: dict) -> bool:
    """A terminal record is immutable except that its committer may add
    ``delivery`` once."""
    if "delivery" in old:
        return False
    a = {k: v for k, v in old.items() if k != "delivery"}
    b = {k: v for k, v in new.items() if k != "delivery"}
    return a == b


def update_record(root: Path, jid: str, fn) -> tuple[dict | None, bool]:
    """Under ``job.lock``: re-read, ``new = fn(copy)``, write. Returns
    (record now on disk, whether this call wrote). ``fn`` returning None means
    no change. A change to a terminal record other than adding ``delivery``
    is refused (nothing written)."""
    root = Path(root)
    _refuse_read_only(f"job {jid}: write refused")
    with _JobLock(root, jid):
        rec = read_record(root, jid)
        if rec is None:
            return None, False
        new = fn(dict(rec))
        if new is None or new == rec:
            return rec, False
        if rec.get("status") in TERMINAL and not _terminal_change_ok(rec, new):
            return rec, False
        if not _write_record(root, jid, new):
            return rec, False
        return new, True


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def liveness(root: Path, rec: dict) -> str | None:
    """None for a terminal record; else running | starting | lost | unknown.
    Derived on read; the reader writes nothing."""
    status = rec.get("status")
    if status in TERMINAL:
        return None
    probe = probe_runner(root, rec.get("id", ""))
    if probe == "busy":
        return "running"
    if probe == "unknown":
        return "unknown"
    if status == "starting":
        age = _age_s(rec.get("created_at"))
        if age is not None and age < STARTING_GRACE_S:
            return "starting"
    return "lost"


def label(target: dict) -> str:
    t = target or {}
    if "pr" in t:
        return f"PR #{t['pr']}"
    if "run" in t:
        return f"run {t['run']}"
    if "workflow" in t:
        where = t.get("ref") or ""
        sha7 = str(t.get("sha") or "")[:7]
        return f"{t['workflow']} @ {where} ({sha7})" if where else f"{t['workflow']} @ {sha7}"
    if "pypi" in t:
        return f"PyPI {t['pypi']}=={t.get('version')}"
    return "?"


def view(root: Path, rec: dict, log_lines: int = 8) -> dict:
    live = liveness(root, rec)
    status = rec.get("status")
    shown = status if live in (None, "running", "starting") else live
    res = rec.get("result") or {}
    jid = rec.get("id", "")
    out = {
        "id": jid, "kind": rec.get("kind"), "label": label(rec.get("target") or {}),
        "status": status, "shown": shown, "liveness": live,
        "created_at": rec.get("created_at"), "started_at": rec.get("started_at"),
        "finished_at": rec.get("finished_at"),
        "age_s": int(_age_s(rec.get("created_at")) or 0),
        "summary": res.get("summary") or rec.get("note") or "",
        "result": res, "error": rec.get("error"), "delivery": rec.get("delivery"),
        "pinned_run": rec.get("pinned_run"), "attempts": rec.get("attempts", 0),
        "log": read_log(root, jid, log_lines) if log_lines else [],
        "cancel_cli": f"tagteam job cancel {jid}",
    }
    out["cancellable"] = status not in TERMINAL and live != "unknown"
    return out


def list_ids(root: Path) -> list[str]:
    root = Path(root)
    if _dir_chain(root, JOBS_REL)[0] != "dir":
        return []
    try:
        names = [n for n in os.listdir(root / JOBS_REL) if valid_id(n)]
    except OSError:
        return []
    return sorted(names, reverse=True)          # ids sort by creation time


def _recent(rec: dict, window_h: float) -> bool:
    if rec.get("status") not in TERMINAL:
        return True
    age = _age_s(rec.get("finished_at") or rec.get("created_at"))
    return age is not None and age <= window_h * 3600


def jobs_payload(project_dir: str | Path, include_all: bool = False) -> dict:
    """``GET /api/jobs``: running jobs and those finished in the last 24 h,
    newest first. File-only — never opens the database, creates nothing."""
    root = Path(project_dir)
    jobs = []
    for jid in list_ids(root):
        rec = read_record(root, jid)
        if rec is None:
            continue
        if include_all or _recent(rec, LIST_WINDOW_H):
            jobs.append(view(root, rec))
    return {"jobs": jobs, "running": sum(1 for j in jobs if j["status"] not in TERMINAL)}


def signature(project_dir: str | Path) -> list | None:
    """Cheap SSE change signal: [count, max job.json mtime]."""
    root = Path(project_dir)
    ids = list_ids(root)
    if not ids:
        return None
    mx = 0
    for jid in ids:
        try:
            mx = max(mx, os.lstat(root / _job_rel(jid, "job.json")).st_mtime_ns // 1_000_000)
        except OSError:
            pass
    return [len(ids), mx]


# ---------------------------------------------------------------------------
# ci-watch: each target asks the one right question
# ---------------------------------------------------------------------------

class WatchError(Exception):
    """The watch itself cannot work (gh missing / unauthenticated / target
    not found / bad data) — ``error``, never ``failed``, never retried."""


class Interrupted(Exception):
    """External work abandoned because a cancel arrived or the deadline
    passed (impl review r1): the loop re-decides from the marker and clock."""


# The runner's budget while it polls: (stop() -> bool, deadline on _clock()).
# External work (gh, the PyPI fetch) checks it every STOP_CHECK_S and is
# abandoned — gh is killed — as soon as either says stop.
_BUDGET: contextvars.ContextVar = contextvars.ContextVar("tagteam_job_budget", default=(None, None))
STOP_CHECK_S = 0.2
_clock = time.monotonic


def _should_stop() -> bool:
    stop, deadline = _BUDGET.get()
    return bool((stop is not None and stop()) or (deadline is not None and _clock() >= deadline))


def _gh(root: Path, args: list[str]) -> str:
    """One gh call: killed when the job is cancelled or its deadline passes
    (``Interrupted``), or after GH_TIMEOUT_S (``WatchError``)."""
    shown = " ".join(args[:2])
    if _should_stop():
        raise Interrupted(f"gh {shown} not started")
    try:
        p = subprocess.Popen(["gh", *args], cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL, text=True)
    except FileNotFoundError:
        raise WatchError("gh not found on PATH (install GitHub CLI and run `gh auth login`)")
    t0 = _clock()
    while True:
        try:
            out, err = p.communicate(timeout=STOP_CHECK_S)
            break
        except subprocess.TimeoutExpired:
            stopping = _should_stop()
            if stopping or _clock() - t0 >= GH_TIMEOUT_S:
                p.kill()
                p.communicate()
                if stopping:
                    raise Interrupted(f"gh {shown} abandoned")
                raise WatchError(f"gh {shown} timed out after {GH_TIMEOUT_S}s")
    if p.returncode != 0:
        first = next((ln.strip() for ln in (err or out or "").splitlines() if ln.strip()),
                     f"exit {p.returncode}")
        raise WatchError(f"gh {shown}: {first}")
    return out


def _interruptible(fn, what: str):
    """Run ``fn`` in a daemon thread, waiting in STOP_CHECK_S steps; abandon it
    (``Interrupted``) the moment the budget says stop. For a blocking call
    that can't be killed, like an HTTP fetch."""
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as e:              # re-raised in the caller's thread
            box["exc"] = e
    t = threading.Thread(target=target, daemon=True)
    t.start()
    while t.is_alive():
        t.join(STOP_CHECK_S)
        if t.is_alive() and _should_stop():
            raise Interrupted(f"{what} abandoned")
    if "exc" in box:
        raise box["exc"]
    return box.get("value")


def _gh_json(root: Path, args: list[str]):
    out = _gh(root, args)
    try:
        return json.loads(out)
    except ValueError:
        raise WatchError(f"gh {' '.join(args[:2])}: output is not JSON")


def _log_tail(root: Path, run_id) -> list[str]:
    """Best-effort: the last lines of ``gh run view ID --log-failed``."""
    try:
        out = _gh(root, ["run", "view", str(run_id), "--log-failed"])
    except WatchError:
        return []
    return out.splitlines()[-LOG_TAIL_LINES:]


def _norm_check(c: dict) -> dict:
    """A statusCheckRollup item → {name, url, bucket: pending|pass|skip|fail}."""
    if c.get("__typename") == "StatusContext" or ("context" in c and "state" in c):
        st = str(c.get("state") or "").upper()
        bucket = {"SUCCESS": "pass", "FAILURE": "fail", "ERROR": "fail"}.get(st, "pending")
        return {"name": c.get("context") or "?", "url": c.get("targetUrl") or "", "bucket": bucket}
    status = str(c.get("status") or "").upper()
    concl = str(c.get("conclusion") or "").upper()
    if status != "COMPLETED":
        bucket = "pending"
    elif concl == "SUCCESS":
        bucket = "pass"
    elif concl in ("SKIPPED", "NEUTRAL"):
        bucket = "skip"
    else:
        bucket = "fail"
    return {"name": c.get("name") or "?", "url": c.get("detailsUrl") or "", "bucket": bucket}


def _wait(note: str) -> tuple:
    return ("wait", note, None)


def _done(status: str, summary: str, **extra) -> tuple:
    return ("done", status, dict(summary=summary, **extra))


def poll_pr(root: Path, target: dict, mem: dict) -> tuple:
    n = target["pr"]
    data = _gh_json(root, ["pr", "view", str(n), "--json", "number,state,headRefOid,statusCheckRollup"])
    if not isinstance(data, dict):
        raise WatchError(f"gh pr view {n}: unexpected output")
    head = data.get("headRefOid")
    if mem.get("head") and head and head != mem["head"]:
        mem["head_changed"] = f"head moved {mem['head'][:7]} → {head[:7]}; judging the new head"
    mem["head"] = head
    checks = [_norm_check(c) for c in (data.get("statusCheckRollup") or []) if isinstance(c, dict)]
    total = len(checks)
    if not total:
        return _wait("no checks registered yet")
    expect = target.get("expect_checks")
    if expect and total < expect:
        return _wait(f"{total}/{expect} checks registered")
    pending = [c for c in checks if c["bucket"] == "pending"]
    if pending:
        return _wait(f"{total - len(pending)}/{total} checks complete")
    fails = [c for c in checks if c["bucket"] == "fail"]
    sha7 = (head or "")[:7]
    if fails:
        tail: list[str] = []
        for c in fails:
            m = _RUN_URL_RE.search(c["url"] or "")
            if m:
                tail = _log_tail(root, m.group(1))
                break
        names = ", ".join(c["name"] for c in fails)
        return _done("failed", f"PR #{n} ({sha7}): {len(fails)}/{total} failed — {names}",
                     failing=[{"name": c["name"], "url": c["url"]} for c in fails], log_tail=tail, sha=head)
    passed = sum(1 for c in checks if c["bucket"] == "pass")
    skipped = total - passed
    extra = f", {skipped} skipped" if skipped else ""
    return _done("succeeded", f"PR #{n} ({sha7}): {passed}/{total} checks passed{extra}", sha=head)


def _judge_run(root: Path, run_id) -> tuple:
    data = _gh_json(root, ["run", "view", str(run_id), "--json", "status,conclusion,name,url,jobs,headSha"])
    if not isinstance(data, dict):
        raise WatchError(f"gh run view {run_id}: unexpected output")
    name = data.get("name") or "run"
    jobs = [j for j in (data.get("jobs") or []) if isinstance(j, dict)]
    if str(data.get("status") or "").lower() != "completed":
        done = sum(1 for j in jobs if str(j.get("status") or "").lower() == "completed")
        return _wait(f"{name} {data.get('status') or 'pending'}" + (f" ({done}/{len(jobs)} jobs done)" if jobs else ""))
    concl = str(data.get("conclusion") or "").lower()
    if concl in ("success", "skipped", "neutral"):
        return _done("succeeded", f"{name} #{run_id}: {concl}", url=data.get("url"), sha=data.get("headSha"))
    fails = [j for j in jobs if str(j.get("conclusion") or "").lower() not in ("success", "skipped", "neutral")]
    names = ", ".join(j.get("name") or "?" for j in fails) or concl
    return _done("failed", f"{name} #{run_id}: {concl} — {names}", url=data.get("url"), sha=data.get("headSha"),
                 failing=[{"name": j.get("name") or "?", "url": j.get("url") or ""} for j in fails],
                 log_tail=_log_tail(root, run_id))


def poll_run(root: Path, target: dict, mem: dict) -> tuple:
    return _judge_run(root, target["run"])


def poll_workflow(root: Path, target: dict, mem: dict) -> tuple:
    """Bound to a commit, never a time window: a run of another commit can
    never match however recent it is; the first run found is pinned for good."""
    if not mem.get("pinned_run"):
        sha = target["sha"]
        runs = _gh_json(root, ["run", "list", "--workflow", target["workflow"], "--commit", sha,
                               "--json", "databaseId,headSha,event,createdAt,status,conclusion,url",
                               "--limit", "20"])
        if not isinstance(runs, list):
            raise WatchError("gh run list: unexpected output")
        mine = [r for r in runs if isinstance(r, dict) and r.get("databaseId")
                and str(r.get("headSha") or "").lower().startswith(sha)]
        if not mine:
            return _wait(f"waiting for a run of {sha[:7]}")
        mine.sort(key=lambda r: int(r["databaseId"]))
        mem["pinned_run"] = int(mine[-1]["databaseId"])
        if len(mine) > 1:
            mem["pin_note"] = (f"{len(mine)} runs for {sha[:7]}: pinned {mem['pinned_run']}, ignored "
                               + ", ".join(str(r["databaseId"]) for r in mine[:-1]))
    return _judge_run(root, mem["pinned_run"])


def _pep503(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def poll_pypi(root: Path, target: dict, mem: dict) -> tuple:
    """PyPI's /simple/ index (PEP 691 JSON) — the JSON API lags behind it."""
    import urllib.error
    import urllib.request
    pkg, ver = target["pypi"], target["version"]
    base = os.environ.get(PYPI_SIMPLE_ENV) or PYPI_SIMPLE
    url = base.rstrip("/") + "/" + _pep503(pkg) + "/"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.pypi.simple.v1+json"})

    def fetch():
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    try:
        data = _interruptible(fetch, f"PyPI {url}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return _wait(f"{pkg} not on the index yet")
        raise WatchError(f"PyPI {url}: HTTP {e.code}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise WatchError(f"PyPI {url}: {getattr(e, 'reason', e)}")
    versions = data.get("versions") if isinstance(data, dict) else None
    listed = isinstance(versions, list) and ver in versions
    if not listed and isinstance(data, dict):
        stem = _pep503(pkg).replace("-", "_")
        for f in data.get("files") or []:
            fn = str((f or {}).get("filename") or "")
            if fn.lower().startswith((f"{stem}-{ver}-".lower(), f"{stem}-{ver}.tar".lower(),
                                      f"{pkg}-{ver}.tar".lower())):
                listed = True
                break
    if listed:
        return _done("succeeded", f"PyPI {pkg}=={ver} is listed")
    return _wait(f"{pkg}=={ver} not listed yet")


def poll(root: Path, target: dict, mem: dict) -> tuple:
    if "pr" in target:
        return poll_pr(root, target, mem)
    if "run" in target:
        return poll_run(root, target, mem)
    if "workflow" in target:
        return poll_workflow(root, target, mem)
    if "pypi" in target:
        return poll_pypi(root, target, mem)
    raise WatchError(f"unknown target {target!r}")


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

def _sleep(s: float) -> None:
    time.sleep(s)


def _cancel_requested(root: Path, jid: str) -> bool:
    try:
        return os.lstat(root / _job_rel(jid, "cancel")) is not None
    except OSError:
        return False


def _hold_runner_lock(root: Path, jid: str) -> int | None:
    """The runner's lifetime lock: non-blocking, retried for CLAIM_RETRY_S so a
    reader's momentary probe cannot turn a real start into a duplicate. The fd
    is non-inheritable (PEP 446), so gh children never keep it."""
    fd = _open_guarded(root, _job_rel(jid, "runner.lock"), os.O_RDWR)
    if fd is None:
        return None
    deadline = time.monotonic() + CLAIM_RETRY_S
    while True:
        if _try_lock(fd):
            return fd
        if time.monotonic() >= deadline:
            os.close(fd)
            return None
        time.sleep(0.05)


def _commit(root: Path, jid: str, status: str, result: dict | None = None,
            error: str | None = None, meta: dict | None = None) -> tuple[dict | None, bool]:
    """The terminal write, with the final poll's metadata (attempts,
    last_poll_at, pinned_run) in the same record (impl review r1)."""
    def fn(r):
        if r.get("status") in TERMINAL:
            return None
        for k, v in (meta or {}).items():
            if v is not None:
                r[k] = v
        r.update(status=status, finished_at=_now_iso())
        if result is not None:
            r["result"] = result
        if error is not None:
            r["error"] = error
        return r
    return update_record(root, jid, fn)


def run(root: str | Path, jid: str, out=None) -> int:
    """``tagteam job run ID``: take runner.lock (or exit 3 — another runner
    holds it), move starting → running, poll until a definite answer, a
    cancel or the timeout, commit under job.lock, then deliver once."""
    out = out or sys.stdout
    root = Path(root)
    _refuse_read_only(f"`tagteam job run {jid}` refused")
    if read_record(root, jid) is None:
        print(f"No job {jid}.", file=out)
        return 1
    fd = _hold_runner_lock(root, jid)
    if fd is None:
        print(f"Job {jid}: another runner holds it.", file=out)
        return 3
    try:
        from tagteam import procs
        me, ident = os.getpid(), None
        try:
            ident = procs.identity(me)
        except Exception:
            pass

        def to_running(r):
            if r.get("status") != "starting":
                return None
            r.update(status="running", pid=me, ident=ident, started_at=_now_iso())
            return r
        rec, wrote = update_record(root, jid, to_running)
        if not wrote:
            print(f"Job {jid}: not starting (status {rec and rec.get('status')}); nothing to do.", file=out)
            return 3
        _append_log(root, jid, f"runner {me} started: {label(rec.get('target') or {})}")
        return _loop(root, jid, rec)
    finally:
        _unlock(fd)
        os.close(fd)


def _loop(root: Path, jid: str, rec: dict) -> int:
    """Poll until an answer, a cancel or the deadline. Cancel and the deadline
    are honoured DURING external work (gh is killed, a fetch abandoned) and
    during the wait (never past the deadline), and re-checked after every
    poll, before an answer or an error is committed (impl review r1)."""
    target = rec.get("target") or {}
    interval = max(MIN_INTERVAL_S, int(rec.get("interval_s") or DEFAULT_INTERVAL_S))
    timeout_s = rec.get("timeout_s")
    timeout_s = float(DEFAULT_TIMEOUT_MIN * 60 if timeout_s is None else timeout_s)
    deadline = _clock() + timeout_s

    def stop():
        return _cancel_requested(root, jid)
    token = _BUDGET.set((stop, deadline))
    mem: dict = {}
    st = {"attempts": 0, "last_poll_at": None, "note": None}

    def meta():
        return {"attempts": st["attempts"], "last_poll_at": st["last_poll_at"], "note": st["note"],
                "pinned_run": mem.get("pinned_run")}

    def flush_notes():
        for k in ("head_changed", "pin_note"):
            if mem.get(k):
                _append_log(root, jid, mem.pop(k))

    def stop_now():
        """(status, result) when a cancel or the deadline ends the job now."""
        if stop():
            _append_log(root, jid, "cancel requested")
            return "cancelled", {"summary": f"{label(target)}: cancelled"}
        if _clock() >= deadline:
            _append_log(root, jid, "timed out")
            mins = int(timeout_s) // 60
            span = f"{mins} min" if mins else f"{int(timeout_s)} s"
            return "timed-out", {"summary": f"{label(target)}: no answer in {span}"
                                            + (f" (last: {st['note']})" if st["note"] else "")}
        return None

    try:
        while True:
            ended = stop_now()
            if ended:
                return _finish(root, jid, *ended, meta=meta())
            st["attempts"] += 1
            st["last_poll_at"] = _now_iso()
            try:
                kind, a, b = poll(root, target, mem)
                err = None
            except Interrupted:
                flush_notes()
                continue                                    # the top of the loop decides which stop it was
            except WatchError as e:
                kind, a, b, err = "done", "error", {"summary": f"{label(target)}: {e}"}, str(e)
            flush_notes()
            ended = stop_now()                              # a cancel accepted mid-poll wins over its answer
            if ended:
                return _finish(root, jid, *ended, meta=meta())
            if kind == "done":
                _append_log(root, jid, (f"error: {err}" if err else f"{a}: {b.get('summary')}"))
                return _finish(root, jid, a, b, error=err, meta=meta())
            st["note"] = a
            _append_log(root, jid, f"poll {st['attempts']}: {a}")
            m = meta()

            def progress(r, _m=m):
                if r.get("status") != "running":
                    return None
                for k, v in _m.items():
                    if v is not None:
                        r[k] = v
                return r
            update_record(root, jid, progress)
            wake = min(_clock() + interval, deadline)       # never sleep past the deadline
            while _clock() < wake and not stop():
                _sleep(min(1.0, max(0.0, wake - _clock())))
    finally:
        _BUDGET.reset(token)


def _finish(root: Path, jid: str, status: str, result: dict, error: str | None = None,
            meta: dict | None = None) -> int:
    rec, wrote = _commit(root, jid, status, result, error, meta)
    if not wrote:
        return 0            # someone else committed first; the committer delivers, not us
    deliver(root, jid, rec)
    return 0 if status == "succeeded" else 4


# ---------------------------------------------------------------------------
# Delivery: at most once, best-effort, by the committer
# ---------------------------------------------------------------------------

_TITLES = {"succeeded": "CI green", "failed": "CI failed", "timed-out": "CI watch timed out",
           "error": "CI watch error", "cancelled": "CI watch cancelled"}


def _notify(title: str, message: str):
    """True (sent) | False (a backend was tried and failed) | a string saying
    why nothing was tried."""
    if os.environ.get("TAGTEAM_NO_NOTIFY"):
        return "skipped: TAGTEAM_NO_NOTIFY is set"
    from tagteam.notify import notify
    return notify(title, message)


def _interject_lead(root: Path, jid: str, note: str) -> str:
    import io
    from tagteam import controls
    _st, ident = controls._owed_identity(root)
    if ident.get("phase") is None:
        return "skipped: no active cycle"
    buf = io.StringIO()
    rc = controls.interject_command([note, "--by", f"job:{jid}", "--to", "lead"], project_root=root, out=buf)
    return "recorded" if rc == 0 else f"failed: {buf.getvalue().strip()[:200]}"


def deliver(root: Path, jid: str, rec: dict) -> dict:
    """Attempted once by the process that committed the terminal record.
    Never raises; records what happened in ``delivery``."""
    status = rec.get("status")
    summary = (rec.get("result") or {}).get("summary") or status
    failing = (rec.get("result") or {}).get("failing") or []
    msg = summary + (f" · {failing[0].get('url')}" if failing and failing[0].get("url") else "")
    d: dict = {"at": _now_iso()}
    if rec.get("quiet"):
        d["notify"] = "skipped"
    else:
        try:
            sent = _notify(_TITLES.get(status, "CI watch"), msg)
            d["notify"] = sent if isinstance(sent, str) else ("sent" if sent else "failed")
        except Exception as e:
            d["notify"] = f"failed: {type(e).__name__}"
    if rec.get("to_lead"):
        try:
            d["interjection"] = _interject_lead(root, jid, f"[job {jid}] {_TITLES.get(status, status)}: {msg}")
        except Exception as e:
            d["interjection"] = f"failed: {type(e).__name__}: {e}"[:200]
    else:
        d["interjection"] = "skipped"
    _append_log(root, jid, f"delivery: notify {d['notify']}, interjection {d['interjection']}")

    def add(r):
        if "delivery" in r:
            return None
        r["delivery"] = d
        return r
    try:
        update_record(root, jid, add)
    except Exception:
        pass
    return d


# ---------------------------------------------------------------------------
# start / cancel / prune
# ---------------------------------------------------------------------------

def _new_id() -> str:
    return _now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def resolve_ref(root: Path, ref: str) -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=str(root),
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    sha = r.stdout.strip()
    return sha if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def _spawn(root: Path, jid: str) -> int | None:
    """Start the detached runner; its stdout/stderr go to runner.out."""
    fd = _open_guarded(root, _job_rel(jid, "runner.out"), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    kw: dict = {}
    if sys.platform == "win32":  # pragma: no cover - Windows CI
        kw["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kw["start_new_session"] = True
    try:
        p = subprocess.Popen([sys.executable, "-m", "tagteam", "job", "run", jid], cwd=str(root),
                             stdin=subprocess.DEVNULL, stdout=fd if fd is not None else subprocess.DEVNULL,
                             stderr=subprocess.STDOUT if fd is not None else subprocess.DEVNULL, **kw)
        return p.pid
    except OSError:
        return None
    finally:
        if fd is not None:
            os.close(fd)


def create(root: Path, kind: str, target: dict, *, interval_s: int, timeout_s: int,
           to_lead: bool, quiet: bool, by: str) -> str | None:
    """Write the job's directory, its lock files and the initial record
    (``starting``). Returns the id, or None when .tagteam/jobs is unusable."""
    root = Path(root)
    _refuse_read_only("`tagteam job start` refused")
    jid = _new_id()
    while (root / _job_rel(jid)).exists():
        jid = _new_id()
    if not _mkdirs(root, _job_rel(jid)):
        return None
    for name in ("runner.lock", "job.lock"):
        fd = _open_guarded(root, _job_rel(jid, name), os.O_WRONLY | os.O_CREAT)
        if fd is None:
            return None
        os.close(fd)
    rec = {"id": jid, "kind": kind, "target": target, "status": "starting", "created_at": _now_iso(),
           "started_at": None, "finished_at": None, "pid": None, "ident": None, "by": by,
           "interval_s": interval_s, "timeout_s": timeout_s, "to_lead": bool(to_lead), "quiet": bool(quiet),
           "attempts": 0, "last_poll_at": None, "result": None, "error": None}
    if not _write_record(root, jid, rec):
        return None
    return jid


def start(root: Path, kind: str, target: dict, *, interval_s: int = DEFAULT_INTERVAL_S,
          timeout_s: int = DEFAULT_TIMEOUT_MIN * 60, to_lead: bool = False, quiet: bool = False,
          by: str = "arbiter", deadline_s: float | None = None) -> tuple[str | None, dict | None]:
    """Create + spawn; wait up to START_DEADLINE_S for ``running``; past it,
    under job.lock, a still-``starting`` job becomes ``error: runner did not
    start`` (a late runner then finds it terminal and exits)."""
    root = Path(root)
    _refuse_read_only("`tagteam job start` refused")
    prune(root)
    jid = create(root, kind, target, interval_s=interval_s, timeout_s=timeout_s,
                 to_lead=to_lead, quiet=quiet, by=by)
    if jid is None:
        return None, None
    _spawn(root, jid)
    limit = START_DEADLINE_S if deadline_s is None else deadline_s
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        rec = read_record(root, jid)
        if rec and rec.get("status") != "starting":
            return jid, rec
        time.sleep(0.05)

    def expire(r):
        if r.get("status") != "starting":
            return None
        r.update(status="error", finished_at=_now_iso(),
                 error=f"runner did not start within {limit:g} s",
                 result={"summary": f"{label(target)}: runner did not start"})
        return r
    rec, wrote = update_record(root, jid, expire)
    if wrote:
        deliver(root, jid, rec)
    return jid, rec


def cancel(root: Path, jid: str, by: str = "arbiter") -> tuple[str, dict | None]:
    """Write the marker, then under job.lock: terminal → report; a live
    runner → leave it (it commits within one interval); a free runner lock
    (lost) → commit ``cancelled`` / ``runner lost`` here (no delivery — no CI
    answer is known); unknown → marker only. Never kills a process.
    Returns (outcome, record): terminal | left-to-runner | finalised | unknown | missing."""
    root = Path(root)
    _refuse_read_only(f"`tagteam job cancel {jid}` refused")
    if read_record(root, jid) is None:
        return "missing", None
    fd = _open_guarded(root, _job_rel(jid, "cancel"), os.O_WRONLY | os.O_CREAT)
    if fd is not None:
        try:
            os.write(fd, json.dumps({"by": by, "ts": _now_iso()}).encode("utf-8"))
        except OSError:
            pass
        os.close(fd)
    outcome = {"v": "terminal"}

    def fn(r):
        if r.get("status") in TERMINAL:
            outcome["v"] = "terminal"
            return None
        probe = probe_runner(root, jid)
        if probe == "busy":
            outcome["v"] = "left-to-runner"
            return None
        if probe == "unknown":
            outcome["v"] = "unknown"
            return None
        outcome["v"] = "finalised"
        r.update(status="cancelled", finished_at=_now_iso(), error="runner lost",
                 result={"summary": f"{label(r.get('target') or {})}: cancelled (runner lost)"},
                 delivery={"at": _now_iso(), "notify": "skipped", "interjection": "skipped",
                           "note": "lost-job cleanup delivers nothing"})
        return r
    rec, _wrote = update_record(root, jid, fn)
    if outcome["v"] == "finalised":
        _append_log(root, jid, f"cancelled by {by}: runner lost")
    return outcome["v"], rec


def prune(root: Path, days: int = RETENTION_DAYS) -> list[str]:
    """Remove finished (and lost) jobs whose created_at is older than ``days``."""
    import shutil
    root = Path(root)
    _refuse_read_only("job pruning refused")
    gone = []
    for jid in list_ids(root):
        rec = read_record(root, jid)
        if rec is None:
            continue
        age = _age_s(rec.get("created_at"))
        if age is None or age < days * 86400:
            continue
        if rec.get("status") not in TERMINAL and liveness(root, rec) != "lost":
            continue
        d = root / _job_rel(jid)
        try:
            if not os.path.islink(d):
                shutil.rmtree(d)
                gone.append(jid)
        except OSError:
            pass
    return gone


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

USAGE = """Usage:
  tagteam job start ci-watch (--pr N [--expect-checks N] | --run ID
                              | --workflow NAME (--ref REF | --sha SHA) | --pypi PKG==VER)
                   [--interval S] [--timeout M] [--to-lead] [--quiet]
  tagteam job list [--all] [--json]      running + the last 24 h (--all: everything)
  tagteam job status ID [--json]
  tagteam job log ID [-n N]
  tagteam job cancel ID

A job is a recorded background task; no model, no turn slot. `ci-watch` polls
GitHub (through `gh … --json`) or PyPI's /simple/ index until it knows the
answer, then says it once: a desktop notification (unless --quiet) and, with
--to-lead, an interjection for the lead's next turn."""


def _fmt_age(s) -> str:
    try:
        s = int(s)
    except (TypeError, ValueError):
        return "?"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 86400}d"


def _mark(v: dict) -> str:
    return {"succeeded": "✓ green", "failed": "✗ failed", "timed-out": "timed out", "cancelled": "cancelled",
            "error": "error", "running": "running", "starting": "starting", "lost": "LOST (runner gone)",
            "unknown": "running? (runner unverifiable)"}.get(v["shown"], v["shown"])


def _parse_start(args: list[str]) -> dict:
    if not args or args[0] != "ci-watch":
        raise ValueError("the only job kind is ci-watch: tagteam job start ci-watch …")
    opts: dict = {}
    flags_v = {"--pr", "--run", "--workflow", "--ref", "--sha", "--pypi", "--interval", "--timeout",
               "--expect-checks", "--by"}
    flags = {"--to-lead", "--quiet"}
    i = 1
    while i < len(args):
        a = args[i]
        if a in flags_v:
            if i + 1 >= len(args):
                raise ValueError(f"{a} requires a value")
            opts[a] = args[i + 1]
            i += 2
        elif a in flags:
            opts[a] = True
            i += 1
        else:
            raise ValueError(f"Unknown argument: {a}")
    return opts


def _int(opts, key, lo=None):
    try:
        v = int(opts[key])
    except (TypeError, ValueError):
        raise ValueError(f"{key} needs an integer, got {opts[key]!r}")
    if lo is not None and v < lo:
        raise ValueError(f"{key} must be at least {lo}")
    return v


def _target_from(root: Path, opts: dict) -> dict:
    chosen = [k for k in ("--pr", "--run", "--workflow", "--pypi") if k in opts]
    if len(chosen) != 1:
        raise ValueError("give exactly one target: --pr N | --run ID | --workflow NAME (--ref REF | --sha SHA) | --pypi PKG==VER")
    k = chosen[0]
    if ("--ref" in opts or "--sha" in opts) and k != "--workflow":
        raise ValueError("--ref / --sha go with --workflow")
    if "--expect-checks" in opts and k != "--pr":
        raise ValueError("--expect-checks goes with --pr")
    if k == "--pr":
        t = {"pr": _int(opts, "--pr", 1)}
        if "--expect-checks" in opts:
            t["expect_checks"] = _int(opts, "--expect-checks", 1)
        return t
    if k == "--run":
        return {"run": _int(opts, "--run", 1)}
    if k == "--pypi":
        m = _PYPI_RE.match(opts["--pypi"].strip())
        if not m:
            raise ValueError(f"--pypi takes PKG==VERSION, got {opts['--pypi']!r}")
        return {"pypi": m.group(1), "version": m.group(2)}
    name = opts["--workflow"].strip()
    if not name:
        raise ValueError("--workflow needs a name")
    if ("--ref" in opts) == ("--sha" in opts):
        raise ValueError("--workflow needs exactly one of --ref REF or --sha SHA (the commit that was pushed)")
    if "--sha" in opts:
        sha = opts["--sha"].strip()
        if not _SHA_RE.match(sha):
            raise ValueError(f"--sha must be a hex commit id, got {sha!r}")
        return {"workflow": name, "sha": sha.lower()}
    ref = opts["--ref"].strip()
    sha = resolve_ref(root, ref)
    if not sha:
        raise ValueError(f"--ref {ref!r} does not resolve to a commit here (git rev-parse); "
                         "create the tag/branch first, or pass --sha")
    return {"workflow": name, "ref": ref, "sha": sha}


def _root(project_root) -> Path:
    if project_root is None:
        from tagteam.state import _resolve_project_root
        project_root = _resolve_project_root()
    return Path(project_root)


def job_command(args: list[str], project_root: str | Path | None = None, out=None) -> int:
    out = out or sys.stdout
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE, file=out)
        return 0 if args else 1
    sub, rest = args[0], args[1:]
    root = _root(project_root)
    if sub == "start":
        try:
            opts = _parse_start(rest)
            target = _target_from(root, opts)
            interval = _int(opts, "--interval", MIN_INTERVAL_S) if "--interval" in opts else DEFAULT_INTERVAL_S
            timeout_m = _int(opts, "--timeout", 1) if "--timeout" in opts else DEFAULT_TIMEOUT_MIN
        except ValueError as e:
            print(f"job start: {e}", file=out)
            return 1
        from tagteam.controls import _who
        jid, rec = start(root, "ci-watch", target, interval_s=interval, timeout_s=timeout_m * 60,
                         to_lead=bool(opts.get("--to-lead")), quiet=bool(opts.get("--quiet")),
                         by=_who(opts.get("--by")))
        if jid is None:
            print(f"job start: cannot create {JOBS_REL}/ here (not a plain directory?)", file=out)
            return 1
        v = view(root, rec or read_record(root, jid) or {"id": jid}, log_lines=0)
        print(f"Job {jid}: ci-watch · {label(target)} — {_mark(v)}"
              + (f" ({v['summary']})" if v["status"] in TERMINAL and v["summary"] else ""), file=out)
        print(f"  status: tagteam job status {jid}", file=out)
        print(f"  log:    tagteam job log {jid}", file=out)
        return 1 if v["status"] == "error" else 0
    if sub == "run":
        if len(rest) != 1 or not valid_id(rest[0]):
            print("Usage: tagteam job run ID", file=out)
            return 1
        return run(root, rest[0], out=out)
    if sub == "list":
        unknown = [a for a in rest if a not in ("--all", "--json")]
        if unknown:
            print(f"Unknown argument: {unknown[0]}", file=out)
            return 1
        p = jobs_payload(root, include_all="--all" in rest)
        if "--json" in rest:
            print(json.dumps(p, indent=2), file=out)
            return 0
        if not p["jobs"]:
            print("No jobs" + ("." if "--all" in rest else " running or finished in the last 24 h."), file=out)
            return 0
        for v in p["jobs"]:
            print(f"{v['id']}  {_mark(v):<14} {v['kind']} · {v['label']}  {_fmt_age(v['age_s'])} ago"
                  + (f"  — {v['summary']}" if v["summary"] else ""), file=out)
        return 0
    if sub in ("status", "log", "cancel"):
        ids, skip = [], False
        for a in rest:
            if skip:
                skip = False
            elif a == "-n":
                skip = True
            elif not a.startswith("-"):
                ids.append(a)
        if len(ids) != 1 or not valid_id(ids[0]):
            print(f"Usage: tagteam job {sub} ID", file=out)
            return 1
        jid = ids[0]
        rec = read_record(root, jid)
        if rec is None:
            print(f"No job {jid}.", file=out)
            return 1
        if sub == "status":
            v = view(root, rec, log_lines=5)
            if "--json" in rest:
                print(json.dumps(v, indent=2), file=out)
                return 0
            print(f"Job {jid}: {v['kind']} · {v['label']} — {_mark(v)}", file=out)
            print(f"  created {v['created_at']} by {rec.get('by')}; {v['attempts']} poll(s)", file=out)
            if v["summary"]:
                print(f"  {v['summary']}", file=out)
            for f in (v["result"] or {}).get("failing") or []:
                print(f"  ✗ {f.get('name')}  {f.get('url')}", file=out)
            if v["pinned_run"]:
                print(f"  pinned run: {v['pinned_run']}", file=out)
            if v["delivery"]:
                d = v["delivery"]
                print(f"  delivery: notify {d.get('notify')}, interjection {d.get('interjection')}", file=out)
            elif v["status"] in TERMINAL:
                print("  delivery: not recorded", file=out)
            if v["liveness"] == "lost":
                print(f"  the runner is gone; `tagteam job cancel {jid}` finalises it", file=out)
            elif v["liveness"] == "unknown":
                print("  the runner lock cannot be checked; the job is not treated as dead", file=out)
            return 0
        if sub == "log":
            n = 20
            if "-n" in rest:
                try:
                    n = int(rest[rest.index("-n") + 1])
                except (IndexError, ValueError):
                    print("-n needs an integer", file=out)
                    return 1
            for line in read_log(root, jid, n):
                print(line, file=out)
            return 0
        from tagteam.controls import _who
        outcome, rec = cancel(root, jid, by=_who(None))
        msgs = {
            "terminal": f"Job {jid} already finished: {rec and rec.get('status')}. Nothing to cancel.",
            "left-to-runner": f"Cancel requested: job {jid}'s runner stops within one poll interval.",
            "finalised": f"Job {jid}: the runner was gone; recorded as cancelled (runner lost).",
            "unknown": f"Cancel marker written for job {jid}, but its runner lock cannot be checked — "
                       "not finalised (it is not treated as dead).",
        }
        print(msgs.get(outcome, f"Job {jid}: {outcome}"), file=out)
        return 0 if outcome in ("terminal", "left-to-runner", "finalised") else 1
    print(f"Unknown job command: {sub}\n\n{USAGE}", file=out)
    return 1
