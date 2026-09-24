# Phase 72: Jobs: background tasks and ci-watch

## Status
- [x] Planning: approved round 3 (2026-09-23) at `30dfa1b`. r1: commit-bound workflow selection, PR rollup, desktop-only delivery; r2: OS-released locks instead of token files, at-most-once delivery.
- [x] Implementation: branch `phase-72-jobs-and-ci-watch`
- [x] Implementation Review: approved round 2 (2026-09-24) at `241b4df`; gate 2,554 passed, 5 skipped
- [x] Complete: PR #60 merged 2026-09-24 (rebase). Criterion 8 (the real 3.14.9 release watched by jobs) is done at release time and recorded here.

## Closeout
```
Phase report: jobs-background-tasks-and-ci-watch — plan approved r3 · impl approved r2
  plan   3 rounds · 2 change requests · 0 bounces
  impl   2 rounds · 1 change request · 0 bounces · gate 2 runs, 17m 16s
  time   start→approve 1h 00m · implementation before first submit 21m 48s
         lead 13m 47s (3 spans, 2 unknown) · reviewer 8m 05s (5 spans) · gate 17m 17s (2 spans)  (elapsed; includes relay wait)
  usage  no usage rows stored under this phase
  turns  matched 0 of 10 · no token data 0 · unmatched 8 · unknown 2
```
- **Plan r1:**
  - a "start minus 2 minutes" window can't tell the previous run from the new one, so the watch is bound to a commit;
  - `gh pr checks` errors before checks are registered, so the watch uses the `statusCheckRollup`;
  - there is no phone transport, so delivery is desktop-only.
- **Plan r2:** an `O_EXCL` token can exclude other writers but can't recover from a crash, so ownership uses two locks the OS releases. Delivery is at most once.
- **Impl r1:** each of these is fixed with regression tests:
  - a cancel accepted mid-poll lost to the poll's answer, and neither a slow `gh` call nor the wait honoured cancel or the deadline;
  - the cockpit's cancel went past the read-only allowlist;
  - the final poll's pin and attempt count were not saved.

## Implementation notes
**Driven in a real page:** a scratch project with a fake `gh` on PATH, detached runners started with `tagteam job start`, `tagteam serve`, and Playwright.
- A polling job and a failed job showed as chips. The failed job's detail showed the failing check, its link, the `--log-failed` tail, and the delivery line.
- **Cancel** went through the confirmation, which showed `tagteam job cancel <id>`. The chip turned `cancelled` over SSE.
- A runner killed with SIGKILL read `runner lost`. **Clear** then recorded `cancelled (runner lost)`.
- **Looking at the page also fixed the delivery line.** It had said "notify failed" when notifications were merely disabled (`TAGTEAM_NO_NOTIFY`); it now says they were skipped.
- **Not seen in a real page:** the `unknown` state and the empty strip. The real-Chromium tests in `tests/test_jobs.py` cover them.
- **Known limit:** a runner killed outright leaves no file trace, so its `lost` state appears on the page's next refresh, the 30 s safety net at the latest.

## Summary
**The rule: deterministic first, a cheap model second, the top model last.**
Today, waiting for CI or a release is done by the top model polling by hand.

- On 2026-09-20, the lead polled five release runs one `gh` call at a time.
- `gh run list --limit 1` right after a tag push returns the *previous* run.
  That was learned the hard way.
- PyPI's JSON endpoint lags about 20 s behind `/simple/`.

None of this needs a model. It needs a recorded background task that polls
until it knows the answer, then says it once.

Add **`tagteam job`**: a recorded, detached background task with a status, a
log and a short result. The first kind is **`ci-watch`**, and it involves no
model. The result reaches the arbiter as a best-effort desktop
notification and in the cockpit, and reaches the lead too if
asked (`--to-lead` delivers it as an interjection). The cockpit gets a
**Jobs strip**.

## Scope
**In**
- A new module, `tagteam/jobs.py`: the job record, the runner and the
  `ci-watch` kind.
- A CLI: `tagteam job start|list|status|log|cancel`.
- `GET /api/jobs` and `POST /api/jobs/cancel`.
- The Jobs strip in the cockpit.
- Tests, with a fake `gh` and a fake PyPI.

**Out**
- **A model-written failure summary.** The roadmap entry allows "a model
  only to summarise a failure". This phase ships the *deterministic* failure
  result: the failing check names, their URLs, and the tail of
  `gh run view --log-failed`. That is usually enough to act on. A cheap-model
  summary is a flag for a later phase, measured first (the rule above).
- Starting jobs from the cockpit. Jobs are started by the CLI, by the lead
  or the arbiter. The cockpit shows and cancels them.
- Job kinds other than `ci-watch`, and scheduling or recurrence.

## Technical approach

### The job record: files, not the database
A job lives in `.tagteam/jobs/<id>/`:
- `job.json`: `id`, `kind`, `target`, `status`, `created_at`, `started_at`,
  `finished_at`, `pid`, `ident`, `by`, `deliver`, `result`, `attempts`,
  `last_poll_at`, `error`;
- `log.txt`: one line per poll, bounded like the watcher log.

Files keep `/api/jobs` and the Jobs strip **database-free**, as Phase 68 made
the turn facts, so any project and `tagteam job list` under read-only work
and create nothing. Writes are atomic (temp file + replace) under the guarded
`O_NOFOLLOW` rules that `watchlog` uses.

**Who may write: two locks the OS releases** (plan review r1 and r2). A
token file shuts other writers out, but it can't recover from a crash: a
process that dies holding it leaves the job stuck for good. So ownership uses
the same portable exclusive lock the cycle writer already uses
(`dualwrite._os_lock`/`_os_unlock`: `fcntl.flock` on POSIX, `msvcrt.locking`
on Windows). A dead holder's lock is released by the OS. There are two lock
files, both created by `start` before it spawns the runner:
- **`runner.lock` is held by the runner for its whole life.** It works like
  the watcher's lifetime lock (`acquire_watcher_lock`). It is **ownership
  and liveness in one**:
  - `job run ID` acquires it non-blocking, retrying for up to 2 s, so a
    reader's momentary probe can't turn a real start into a duplicate. If it
    can't get the lock, another runner holds it, and `job run` exits
    non-zero having written nothing.
  - No "claim published but identity not written yet" window exists,
    because holding the lock *is* the claim. The pid/ident the runner
    records in `job.json` are for display only, never for liveness.
  - Python opens descriptors non-inheritable (PEP 446), so the `gh`
    children never hold the lock after the runner dies.
- **`job.lock` is held briefly around every write of `job.json`.** It is a
  blocking lock. Every writer (`start`'s timeout, the runner, `cancel`) does
  three things under it:
  - re-read `job.json`;
  - apply its change;
  - atomically replace the file (temp + `os.replace`, so a crash leaves the
    old or the new record, never half of one).

  **A terminal record is never replaced.** Its status and result are
  immutable. The one later write allowed is its committer adding the
  `delivery` field (see Delivery). A writer that finds the job already
  terminal writes nothing and reports what it found. Crash recovery
  therefore can't overwrite a committed CI result, and concurrent cancels
  serialise and re-check. **Lock order:** the runner takes `runner.lock`
  once, before it ever takes `job.lock`. Everyone else only *probes*
  `runner.lock` non-blocking while holding `job.lock`. So there is no
  deadlock.

**Startup.** `start` writes `job.json` (`status: starting`) and the two
lock files, then spawns the runner. It never writes `job.json` again,
except for one deadline case:
- The runner, holding `runner.lock`, takes `job.lock`, re-reads, and moves
  `starting → running`. If the record is already terminal, it exits.
- `start` waits up to 5 s for `running`. If the job is still `starting`,
  `start` takes `job.lock`, re-reads, and if it is still `starting`, writes
  `error: runner did not start within 5 s`.
- A runner that starts late then finds the record terminal and exits. The
  5 s deadline, applied under the lock, decides the race. No liveness
  guess is involved.

**Statuses:** `starting → running → succeeded | failed | timed-out |
cancelled | error`.
- `failed`: CI said red.
- `error`: the watch itself could not work, e.g. `gh` missing or
  unauthenticated, the target not found, or the runner did not start.

**Liveness is derived on read, and the reader writes nothing.** For a
non-terminal job, the reader opens the existing `runner.lock` read-only
(`O_NOFOLLOW`) and tries the lock non-blocking:
- **Busy:** a runner holds it, so the job is `running`.
- **Acquired:** it is released at once. No process holds the runner lock,
  and the OS would have kept it for a living holder, so this is **confirmed
  dead**: the job reads `lost`.
- **The probe can't be made** (the lock file is missing or can't be
  opened): the job reads **`unknown`**. It is never treated as dead.

This replaces r2's pid/identity rule. `pid_alive`/`identity` answer "is
*a* process with this pid alive", and the lock answers "does the owner
still hold the job", which is the actual question. The reader creates
nothing: the lock files exist from `start`, and a probe only opens them.

**Cancel** (it never kills a process):
- `tagteam job cancel ID` writes a `cancel` marker file, then takes
  `job.lock` and re-reads:
  - **already terminal:** it reports the status and writes nothing;
  - **runner lock busy (live runner):** it leaves the job to the runner,
    which sees the marker within one interval and commits `cancelled`
    under `job.lock`;
  - **runner lock free (`lost`: the runner died, or it was killed after
    deciding its answer but before committing it):** no one else will
    ever finish the job, so cancel commits `status: cancelled`,
    `error: "runner lost"` itself. It delivers nothing, because no CI
    answer is known. The job is now durably finished and is listed and
    retained like any other;
  - **`unknown`:** it leaves the marker only, and says it couldn't confirm
    the runner is gone.
- A cancel that dies part-way leaves either the old record or the
  committed one, and the lock is released. The next cancel finishes the
  job.
- An uncancelled `lost` job stays `lost` in `list`. Retention prunes it,
  like a finished one, 7 days after `created_at`.

### The runner
`tagteam job start ci-watch …` validates the target, writes `job.json`, and
spawns `python -m tagteam job run <id>`, detached (a new session, stdout to
`log.txt`). The runner (after the startup handshake above):
- polls every `--interval` seconds (default 20, minimum 5), up to
  `--timeout` (default 60 min);
- stops at the first definite answer, a cancel marker, or the timeout;
- commits the result under `job.lock`, then attempts delivery once.

No model is involved, and no turn slot is taken, because a job is not a turn
and must never block one.

### `ci-watch` targets: each with the one right question
| Target | How it is answered (all through `gh … --json`, parsed; no text scraping) |
|---|---|
| `--pr N` | `gh pr view N --json number,state,headRefOid,statusCheckRollup` (not `gh pr checks`, which exits non-zero when no checks are registered yet; see "PR checks" below). |
| `--run ID` | `gh run view ID --json status,conclusion,name,url,jobs` |
| `--workflow NAME (--ref REF \| --sha SHA)` | Bound to a **commit**, not a time window (see "Selecting the run" below). `gh run list --workflow NAME --commit SHA --json databaseId,headSha,event,createdAt,status,conclusion,url`, then `gh run view ID --json …` once a run is pinned. |
| `--pypi PKG==VER` | `GET https://pypi.org/simple/PKG/` (PEP 691 JSON). Done when VER is listed. The JSON API is deliberately not used, because it lags. No `gh`. |

**Selecting the run (`--workflow`)** (plan review r1). A time window can't
tell a previous run from the new one. A run 60 s old passes a "start minus 2
minutes" filter. So the job binds to the **commit** that was pushed:
- `--ref REF` is resolved **once, at `start`, synchronously**, with
  `git rev-parse --verify REF^{commit}` in the project. For a release, the tag
  is created locally before it is pushed, so this works whether the watch is
  armed before or after the push. `--sha SHA` gives the commit directly. If
  the ref doesn't resolve, `start` fails at once, and no job is written.
- The runner polls `gh run list --workflow NAME --commit SHA`:
  - **No run for that commit yet:** it logs "waiting for a run of <sha7>"
    and keeps polling until the timeout. It is never green.
  - **One run:** the job pins it.
  - **Several runs** (e.g. a re-dispatch): it pins the newest, the highest
    `databaseId`, and logs the others.
  - **Pinned is final:** the pinned run id is written to `job.json`
    (`pinned_run`), and every later poll is `gh run view <pinned>`.
- The run that existed before the push belongs to a different commit, so it
  can never match, however recent it is. A run for the same commit that
  finished before the watch started is the right answer, and it is reported.
- Tag pushes and branch pushes are the same case. A bare `--ref` for a branch
  means the local tip, which is what was just pushed.

**PR checks (`--pr`)** (plan review r1). `gh pr view --json
statusCheckRollup` separates the two cases by *structure*, not by stderr
text. An existing PR returns exit 0 with a rollup list, which may be
**empty**. A missing or inaccessible PR exits non-zero, which is an `error`.
The rollup holds `CheckRun` items (`status`, `conclusion`, `name`,
`detailsUrl`) and `StatusContext` items (`state`, `context`, `targetUrl`).
Both are normalised to `pending | pass | skip | fail`.
- **Empty rollup:** "no checks registered yet". The job keeps waiting, and
  never calls it green.
- **Any check pending:** the job waits.
- **All complete:** green if every check is pass or skip, red otherwise.
- **The head sha is recorded with each poll.** If it changes (a new push),
  the log says so and the judgement follows the new head. The result names
  the sha it judged.
- **`--expect-checks N`** (optional): don't judge until at least N checks
  are registered. This covers a PR whose workflows register one at a time.
  Without it, the first complete-and-non-empty set is judged, and the plan
  names that as a known limit.

- On red, the result gets the failing check or job names, their URLs, and
  the last 40 lines of `gh run view <id> --log-failed`.
- `gh` exiting non-zero (not logged in, not found) is an `error` with its
  stderr's first line, and is never retried as if it were CI still running.

### Delivery: one best-effort attempt, at most once
**Delivery is attempted at most once, and it is best-effort**
(plan review r2). Only the process that *committed* the terminal record
attempts delivery. It does so after releasing `job.lock`. It then adds a
`delivery` field (`notify: sent|failed|skipped`,
`interjection: recorded|skipped|failed`) to the record under the lock. It
has two possible gaps, both documented:
- A committer that dies between the commit and delivery leaves a
  delivery that is **missed, and never replayed**.
- A committer that dies after sending, but before recording it, leaves a
  record with **no `delivery` field**, and the job reads "delivery not
  recorded".

Crash-proof exactly-once delivery is out of scope. A lost-job cleanup
(cancel) delivers nothing.

- **`job.json` + `log.txt`:** always.
- **A best-effort desktop notification** through `tagteam.notify`, which
  uses osascript, Windows toast or notify-send. For example: "CI green —
  PR #59: 2/2 checks passed" / "CI failed — PR #59: pytest (ubuntu) · <url>".
  It is on by default, with `--quiet` to turn it off. A notifier failure is
  logged and never changes the job's result. **There is no phone
  guarantee** (plan review r1): tagteam has no phone transport. A push to
  the arbiter's phone is Claude Code's Remote Control and is out of scope;
  the `--to-lead` interjection is how a live Claude session hears of it.
- **`--to-lead`:** the one-line result is recorded as an interjection for
  the lead (`tagteam interject --to lead`, with `--by job:<id>`), so the
  lead's next turn reads it instead of polling. If no cycle is active, the
  interjection is skipped, and `job.json` says so.

### CLI
```
tagteam job start ci-watch (--pr N [--expect-checks N] | --run ID
                             | --workflow NAME (--ref REF | --sha SHA) | --pypi PKG==VER)
                  [--interval S] [--timeout M] [--to-lead] [--quiet]
tagteam job run ID                     # internal: the runner (takes runner.lock or exits)
tagteam job list [--all] [--json]      # running + the last 24 h (--all: everything)
tagteam job status ID [--json]
tagteam job log ID [-n N]
tagteam job cancel ID
```
- `list`, `status` and `log` go into `READ_ONLY_COMMANDS`. `start` and
  `cancel` are refused under read-only.
- `start` prints the job id and a copy-paste `tagteam job status <id>`.
- Retention: finished jobs older than 7 days are pruned by the next `start`.

### Cockpit: the Jobs strip
- `GET /api/jobs` (file-only) returns running jobs and those finished in
  the last 24 h, newest first.
- A slim strip under the turn bar shows **only when there is a job**. Each
  chip reads, for example, "ci-watch · PR #59 · running 3m" or "✓ green" /
  "✗ failed". A chip expands to the result and the log tail. A running job
  has **Cancel**, confirmed with its CLI line (`POST /api/jobs/cancel`,
  through the existing action pattern).
- A `lost` job says so, and Cancel finalises it (`cancelled` / `runner lost`);
  an `unknown` one says so and Cancel only leaves the marker. Cancel never
  kills a process.
- The strip is refreshed by the existing SSE signature plus the live tick.
  The signature gains the max `jobs` mtime.

## Files
- `tagteam/jobs.py` (new): the record, the runner and `ci_watch`.
- `tagteam/cli.py`: dispatch, help and `READ_ONLY_COMMANDS`.
- `tagteam/cockpit_api.py`: `jobs_payload`, the cancel action, and the
  signature.
- `tagteam/server.py`: the routes.
- `cockpit.html` / `.js` / `.css`: a "Phase 72" Jobs-strip slice.
- Docs: README, how-tagteam-works, and the workflows Steering list.
- Tests: `tests/test_jobs.py`, with a fake `gh` script on PATH that replays
  canned JSON sequences, and a local HTTP stub for PyPI.

## Success criteria
1. **Each target reaches the right answer from canned `gh` sequences.**
   The fixtures pair each JSON body with the exit code the real command
   gives.
   - A PR goes from no checks (an empty rollup) to pending to green, and
     from pending to red with the failing names, URLs and log tail.
   - An empty rollup is never green.
   - `--expect-checks 2` doesn't judge one completed check.
   - A head-sha change mid-watch is followed.
   - A run succeeds or fails.
   - PyPI goes from absent to listed.
2. **The previous-run trap.** With `--workflow … --ref v1.2.3`:
   - a successful run for the same workflow and branch/ref name, created
     **60 s** before the job started but for a different commit, is
     ignored; the job waits;
   - the run for the resolved sha then appears and is watched;
   - with two runs for that sha, the higher id is pinned, and `pinned_run`
     stays fixed across later polls;
   - a ref that doesn't resolve fails `start` with no job written.
3. **`error` is not `failed`.** A missing `gh`, an unauthenticated `gh` and
   a not-found target each end as `error` with the first line of the reason,
   without polling until the timeout.
4. **The lifecycle:**
   - timeout → `timed-out`;
   - cancel → `cancelled` within one interval;
   - a killed runner → `lost` on read, and the reader writes nothing;
   - **fast completion:** a runner that finishes before `start` returns
     keeps its terminal result. `start`'s deadline write finds the record
     terminal and writes nothing;
   - **duplicate runner:** a second `job run ID`, while the first holds
     `runner.lock`, exits non-zero, writes nothing and delivers nothing;
   - **a probe doesn't block a real start:** a reader holding the
     `runner.lock` probe when the runner starts doesn't make the runner
     exit (the 2 s retry);
   - **interrupted finaliser:** a real child process takes `runner.lock`
     and `job.lock`, then `os._exit`s before the terminal write. The job
     reads `lost`, and `cancel` commits `cancelled` / `runner lost`
     durably, which then survives a re-read and a second cancel;
   - **a canceller dies after taking `job.lock`:** a child takes `job.lock`
     and exits without writing. The next cancel completes the job;
   - **concurrent cancellers** on a lost job: exactly one commits, and the
     other reports the terminal record;
   - **recovery never overwrites a result:** a committed `succeeded` job
     cancelled afterwards (and while `lost`) is unchanged;
   - **missing lock file** reads `unknown`, never `lost`, and cancel
     doesn't finalise it;
   - **the runner never starts:** after `start`'s 5 s deadline the job is
     `error: runner did not start`, and a late runner exits on the
     terminal record;
   - the lost job cancelled above is pruned by retention like a finished
     one;
   - `list`, `status` and `log` create nothing and work read-only.
5. **Delivery** (local calls only — this proves the notifier is called,
   not that anything reaches a phone; at most once, best-effort):
   - on the normal path, exactly one notification per finished job (a
     stubbed `tagteam.notify`), and a `delivery` field in the record;
   - a duplicate runner and a racing cancel add no notification;
   - a lost-job cancel sends none;
   - a committer killed between the commit and delivery leaves the result
     committed, no `delivery` field, and no later replay;
   - a notifier that raises doesn't change the result;
   - `--quiet` sends none;
   - `--to-lead` records one interjection for the lead with `by: job:<id>`,
     or records that it skipped it when no cycle is active.
6. **A job never touches the turn slot.** A job running beside a headless
   turn does not change `slot_status`, `launch_availability` or the
   headline.
7. **The cockpit:**
   - the strip is absent with no jobs;
   - a running job shows its chip and a cancel with its CLI line;
   - a finished job shows its result;
   - a lost job says so.

   Driven in a real page with a fake `gh`.
8. **Used on the real thing once:** after this phase merges, the 3.14.9
   release is watched with `tagteam job start ci-watch --workflow "Publish
   to PyPI" --ref v3.14.9` and `--pypi tagteam==3.14.9`, instead of by hand.
   The result is recorded in the closeout.

## Risks and open questions for the reviewer
- **Files, not the database**, for job records. This keeps `/api/jobs`
  database-free and each runner the single writer of its own record. The
  cost: no SQL queries over job history, which the strip does not need.
- **The model summary is deferred** (see Out). The deterministic failure
  result ships first; whether a cheap-model summary earns its tokens is
  measured later.
- **Liveness by lock probe, not pid.** A probe that briefly takes a free
  lock is the one moment a reader holds anything. It is released at once,
  and the runner's 2 s acquire retry absorbs it.
- **Desktop notification on by default** (accepted in r1). It is
  best-effort and desktop-only; the phone push is not tagteam's.
- **`--ref` resolves locally.** A watch on a ref that exists only on the
  remote needs `--sha`. That is deliberate: resolving through the API would
  bring back "not found yet or not found at all" ambiguity.
